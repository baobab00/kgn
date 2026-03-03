"""End-to-End integration tests for Phase 6 — Git/GitHub Hybrid Architecture.

Scenarios:
1. Export → Delete DB → Import → verify round-trip fidelity
2. BLOCKED dependency chain: enqueue → BLOCKED → complete prereq → auto-READY
3. Git local workflow: init → export+commit → modify → commit → log
4. Mermaid visualisation: multi-type nodes → flowchart + task board + README
5. Sync conflict detection: export → DB modify → file modify → detect conflict

Requires a running PostgreSQL instance (Docker on port 5433).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from kgn.db.repository import KgnRepository
from kgn.graph.mermaid import MermaidGenerator
from kgn.graph.subgraph import SubgraphService
from kgn.models.edge import EdgeRecord
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.sync.export_service import ExportService
from kgn.sync.import_service import ImportService
from kgn.task.service import TaskService

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    *,
    node_type: NodeType = NodeType.TASK,
    title: str = "E2E Node",
    body: str = "## Context\n\nE2E body",
    agent_id: uuid.UUID | None = None,
    status: NodeStatus = NodeStatus.ACTIVE,
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=status,
        title=title,
        body_md=body,
        content_hash=uuid.uuid4().hex,
        created_by=agent_id,
    )


def _insert_node(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    node_type: NodeType = NodeType.TASK,
    title: str = "E2E Node",
    body: str = "## Context\n\nE2E body",
) -> uuid.UUID:
    node = _make_node(
        project_id,
        node_type=node_type,
        title=title,
        body=body,
        agent_id=agent_id,
    )
    repo.upsert_node(node)
    return node.id


def _insert_edge(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    edge_type: EdgeType = EdgeType.DEPENDS_ON,
) -> None:
    edge = EdgeRecord(
        project_id=project_id,
        from_node_id=from_id,
        to_node_id=to_id,
        type=edge_type,
        note="e2e test edge",
        created_by=agent_id,
    )
    repo.insert_edge(edge)


# ══════════════════════════════════════════════════════════════════════
#  Scenario 1 — Export/Import Round-trip
# ══════════════════════════════════════════════════════════════════════


class TestExportImportRoundtrip:
    """DB → export → clear DB → import → verify identical data."""

    def test_roundtrip_nodes_and_edges(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        # 1. Create a named project
        project_name = f"e2e-rt-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        aid = repo.get_or_create_agent(pid, "rt-agent")

        # 2. Create nodes of different types
        goal_id = _insert_node(
            repo,
            pid,
            aid,
            node_type=NodeType.GOAL,
            title="Auth System",
            body="## Context\n\nBuild auth",
        )
        spec_id = _insert_node(
            repo,
            pid,
            aid,
            node_type=NodeType.SPEC,
            title="Auth Spec",
            body="## Decisions\n\nUse JWT",
        )
        task_id = _insert_node(
            repo,
            pid,
            aid,
            node_type=NodeType.TASK,
            title="Implement JWT",
            body="## Context\n\nJWT implementation",
        )

        # 3. Create edges
        _insert_edge(repo, pid, aid, goal_id, spec_id, EdgeType.IMPLEMENTS)
        _insert_edge(repo, pid, aid, spec_id, task_id, EdgeType.DEPENDS_ON)

        # Record originals
        orig_node_ids = {goal_id, spec_id, task_id}

        # 4. Export
        export_svc = ExportService(repo)
        export_result = export_svc.export_project(
            project_name=project_name,
            project_id=pid,
            target_dir=tmp_path,
            agent_id=str(aid),
        )
        assert export_result.exported > 0
        assert not export_result.errors

        # 5. Delete nodes and edges from DB (simulate fresh import)
        #    Temporarily disable append-only trigger on agent_activities
        repo._conn.execute("ALTER TABLE agent_activities DISABLE TRIGGER trg_activities_immutable")
        try:
            for e in repo.search_edges(pid):
                repo._conn.execute("DELETE FROM edges WHERE id = %s", (e.id,))
            for n in repo.search_nodes(pid, exclude_archived=False):
                repo._conn.execute(
                    "DELETE FROM agent_activities WHERE target_node_id = %s", (n.id,)
                )
                repo._conn.execute("DELETE FROM node_embeddings WHERE node_id = %s", (n.id,))
                repo._conn.execute("DELETE FROM node_versions WHERE node_id = %s", (n.id,))
                repo._conn.execute("DELETE FROM nodes WHERE id = %s", (n.id,))
        finally:
            repo._conn.execute(
                "ALTER TABLE agent_activities ENABLE TRIGGER trg_activities_immutable"
            )

        # Verify deletion
        assert len(repo.search_nodes(pid)) == 0
        assert len(repo.search_edges(pid)) == 0

        # 6. Import back
        import_svc = ImportService(repo)
        import_result = import_svc.import_project(
            project_name=project_name,
            project_id=pid,
            agent_id=aid,
            source_dir=tmp_path,
        )

        assert import_result.imported > 0
        assert not import_result.errors

        # 7. Verify round-trip fidelity
        restored_nodes = repo.search_nodes(pid, exclude_archived=False)
        restored_edges = repo.search_edges(pid)

        assert len(restored_nodes) == 3
        assert len(restored_edges) == 2

        restored_ids = {n.id for n in restored_nodes}
        assert restored_ids == orig_node_ids

        restored_titles = {n.title for n in restored_nodes}
        assert "Auth System" in restored_titles
        assert "Auth Spec" in restored_titles
        assert "Implement JWT" in restored_titles

    def test_export_skip_unchanged(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        """Second export should skip all files (content_hash unchanged)."""
        project_name = f"e2e-skip-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        _insert_node(repo, pid, agent_id, title="Skip Test")

        svc = ExportService(repo)

        r1 = svc.export_project(project_name, pid, tmp_path, agent_id=str(agent_id))
        assert r1.exported > 0

        r2 = svc.export_project(project_name, pid, tmp_path, agent_id=str(agent_id))
        assert r2.skipped > 0
        assert r2.exported == 0


# ══════════════════════════════════════════════════════════════════════
#  Scenario 2 — BLOCKED Dependency Chain
# ══════════════════════════════════════════════════════════════════════


class TestBlockedDependencyChain:
    """A→DEPENDS_ON→B: enqueue both → B READY, A BLOCKED → complete B → A auto-READY."""

    def test_dependency_blocks_and_unblocks(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        sg_svc = SubgraphService(repo)
        task_svc = TaskService(repo, sg_svc)

        # Create prerequisite task B
        prereq_id = _insert_node(
            repo,
            project_id,
            agent_id,
            node_type=NodeType.TASK,
            title="Prerequisite B",
        )

        # Create dependent task A (DEPENDS_ON B)
        dependent_id = _insert_node(
            repo,
            project_id,
            agent_id,
            node_type=NodeType.TASK,
            title="Dependent A",
        )
        _insert_edge(repo, project_id, agent_id, dependent_id, prereq_id, EdgeType.DEPENDS_ON)

        # 1. Enqueue B first → should be READY
        result_b = task_svc.enqueue(project_id, prereq_id)
        assert result_b.state == "READY"

        # 2. Enqueue A → should be BLOCKED (B not DONE yet)
        result_a = task_svc.enqueue(project_id, dependent_id)
        assert result_a.state == "BLOCKED"

        # 3. Checkout B → IN_PROGRESS
        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None
        assert pkg.task.task_node_id == prereq_id

        # 4. Complete B → DONE, A should auto-unblock to READY
        complete_result = task_svc.complete(pkg.task.id)
        assert len(complete_result.unblocked_tasks) == 1
        assert complete_result.unblocked_tasks[0].node_title == "Dependent A"

        # 5. Verify A is now READY
        task_a = repo.get_task_by_node_id(dependent_id, project_id)
        assert task_a is not None
        assert task_a.state == "READY"

        # 6. Checkout A → should succeed
        pkg_a = task_svc.checkout(project_id, agent_id)
        assert pkg_a is not None
        assert pkg_a.task.task_node_id == dependent_id

    def test_cycle_detection(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Cyclic dependency → enqueue raises KGN-404."""
        from kgn.errors import KgnError

        sg_svc = SubgraphService(repo)
        task_svc = TaskService(repo, sg_svc)

        a_id = _insert_node(repo, project_id, agent_id, title="Cycle A", node_type=NodeType.TASK)
        b_id = _insert_node(repo, project_id, agent_id, title="Cycle B", node_type=NodeType.TASK)

        _insert_edge(repo, project_id, agent_id, a_id, b_id, EdgeType.DEPENDS_ON)
        _insert_edge(repo, project_id, agent_id, b_id, a_id, EdgeType.DEPENDS_ON)

        with pytest.raises(KgnError, match="cycle"):
            task_svc.enqueue(project_id, a_id)

    def test_multi_level_chain(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """C→B→A chain: complete A → B unblocks, complete B → C unblocks."""
        sg_svc = SubgraphService(repo)
        task_svc = TaskService(repo, sg_svc)

        a_id = _insert_node(repo, project_id, agent_id, title="Chain A", node_type=NodeType.TASK)
        b_id = _insert_node(repo, project_id, agent_id, title="Chain B", node_type=NodeType.TASK)
        c_id = _insert_node(repo, project_id, agent_id, title="Chain C", node_type=NodeType.TASK)

        _insert_edge(repo, project_id, agent_id, b_id, a_id, EdgeType.DEPENDS_ON)
        _insert_edge(repo, project_id, agent_id, c_id, b_id, EdgeType.DEPENDS_ON)

        # Enqueue all: A=READY, B=BLOCKED, C=BLOCKED
        ra = task_svc.enqueue(project_id, a_id)
        rb = task_svc.enqueue(project_id, b_id)
        rc = task_svc.enqueue(project_id, c_id)
        assert ra.state == "READY"
        assert rb.state == "BLOCKED"
        assert rc.state == "BLOCKED"

        # Complete A → B unblocks
        pkg_a = task_svc.checkout(project_id, agent_id)
        assert pkg_a is not None
        r1 = task_svc.complete(pkg_a.task.id)
        assert len(r1.unblocked_tasks) == 1
        assert r1.unblocked_tasks[0].node_title == "Chain B"

        # C still BLOCKED (depends on B, not A directly)
        task_c = repo.get_task_by_node_id(c_id, project_id)
        assert task_c is not None
        assert task_c.state == "BLOCKED"

        # Complete B → C unblocks
        pkg_b = task_svc.checkout(project_id, agent_id)
        assert pkg_b is not None
        r2 = task_svc.complete(pkg_b.task.id)
        assert len(r2.unblocked_tasks) == 1
        assert r2.unblocked_tasks[0].node_title == "Chain C"


# ══════════════════════════════════════════════════════════════════════
#  Scenario 3 — Git Local Workflow
# ══════════════════════════════════════════════════════════════════════


class TestGitWorkflow:
    """init → export+commit → modify → commit → log."""

    def test_git_init_export_commit_log(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        from kgn.git.service import GitService

        git_dir = tmp_path / "git-e2e"
        git_svc = GitService(git_dir)

        # 1. git init
        init_result = git_svc.init()
        assert init_result.success
        assert git_svc.is_initialized()

        # Configure git user for commit (test environment)
        git_svc._run("config", "user.email", "test@e2e.com")
        git_svc._run("config", "user.name", "E2E Test")

        # 2. Create a project and export
        project_name = f"e2e-git-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        aid = repo.get_or_create_agent(pid, "git-agent")
        _insert_node(repo, pid, aid, title="Git Node 1", node_type=NodeType.GOAL)

        export_svc = ExportService(repo)
        export_svc.export_project(project_name, pid, git_dir, agent_id=str(aid))

        # 3. First commit
        commit1 = git_svc.commit(f"kgn: export {project_name}")
        assert commit1.success

        # 4. Add another node and re-export
        _insert_node(repo, pid, aid, title="Git Node 2", node_type=NodeType.SPEC)
        export_svc.export_project(project_name, pid, git_dir, agent_id=str(aid))

        # 5. Second commit
        commit2 = git_svc.commit("kgn: add second node")
        assert commit2.success

        # 6. Verify log has 2 commits
        log_entries = git_svc.log(n=10)
        assert len(log_entries) == 2

        # 7. Verify clean status
        status = git_svc.status()
        assert status.is_clean

    def test_git_status_tracks_changes(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        from kgn.git.service import GitService

        git_dir = tmp_path / "git-status"
        git_svc = GitService(git_dir)
        git_svc.init()
        git_svc._run("config", "user.email", "test@e2e.com")
        git_svc._run("config", "user.name", "E2E Test")

        # Create initial commit
        (git_dir / "init.txt").write_text("init", encoding="utf-8")
        git_svc.commit("init commit")

        # Add a new file → untracked
        (git_dir / "new-file.kgn").write_text("new content", encoding="utf-8")
        status = git_svc.status()
        assert not status.is_clean
        assert len(status.untracked) > 0


# ══════════════════════════════════════════════════════════════════════
#  Scenario 4 — Mermaid Visualisation
# ══════════════════════════════════════════════════════════════════════


class TestMermaidVisualization:
    """Multi-type nodes → flowchart + task board + README."""

    def test_full_graph_mermaid(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        # Create diverse node types
        goal_id = _insert_node(
            repo, project_id, agent_id, title="Main Goal", node_type=NodeType.GOAL
        )
        spec_id = _insert_node(
            repo, project_id, agent_id, title="Design Spec", node_type=NodeType.SPEC
        )
        task_id = _insert_node(
            repo, project_id, agent_id, title="JWT Impl", node_type=NodeType.TASK
        )
        dec_id = _insert_node(
            repo, project_id, agent_id, title="Use OAuth", node_type=NodeType.DECISION
        )
        issue_id = _insert_node(
            repo, project_id, agent_id, title="Auth Bug", node_type=NodeType.ISSUE
        )

        _insert_edge(repo, project_id, agent_id, goal_id, spec_id, EdgeType.IMPLEMENTS)
        _insert_edge(repo, project_id, agent_id, spec_id, task_id, EdgeType.DEPENDS_ON)
        _insert_edge(repo, project_id, agent_id, dec_id, spec_id, EdgeType.DERIVED_FROM)
        _insert_edge(repo, project_id, agent_id, issue_id, task_id, EdgeType.RESOLVES)

        gen = MermaidGenerator(repo)
        result = gen.generate_graph(project_id)

        # Verify structure
        assert result.node_count == 5
        assert result.edge_count == 4
        assert "flowchart TD" in result.diagram

        # Verify all node types present with correct class assignments
        assert ":::goal" in result.diagram
        assert ":::spec" in result.diagram
        assert ":::task" in result.diagram
        assert ":::decision" in result.diagram
        assert ":::issue" in result.diagram

        # Verify edge labels
        assert "|IMPLEMENTS|" in result.diagram
        assert "|DEPENDS_ON|" in result.diagram
        assert "|DERIVED_FROM|" in result.diagram
        assert "|RESOLVES|" in result.diagram

        # Verify classDefs
        assert "classDef goal" in result.diagram
        assert "classDef task" in result.diagram

    def test_task_board_with_dependencies(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        sg_svc = SubgraphService(repo)
        task_svc = TaskService(repo, sg_svc)

        # Create 3 tasks with dependency
        t1_id = _insert_node(repo, project_id, agent_id, title="Setup DB", node_type=NodeType.TASK)
        t2_id = _insert_node(repo, project_id, agent_id, title="Build API", node_type=NodeType.TASK)
        _insert_edge(repo, project_id, agent_id, t2_id, t1_id, EdgeType.DEPENDS_ON)

        task_svc.enqueue(project_id, t1_id)
        task_svc.enqueue(project_id, t2_id)

        gen = MermaidGenerator(repo)
        result = gen.generate_task_board(project_id)

        assert "flowchart LR" in result.diagram
        assert "subgraph READY" in result.diagram
        assert "subgraph BLOCKED" in result.diagram
        assert "Setup DB" in result.diagram
        assert "Build API" in result.diagram
        assert result.node_count == 2

    def test_readme_generation_complete(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        _insert_node(repo, project_id, agent_id, title="G1", node_type=NodeType.GOAL)
        _insert_node(repo, project_id, agent_id, title="S1", node_type=NodeType.SPEC)
        _insert_node(repo, project_id, agent_id, title="T1", node_type=NodeType.TASK)

        gen = MermaidGenerator(repo)
        readme_path = gen.generate_readme(project_id, "e2e-mermaid", tmp_path)

        assert readme_path.exists()
        content = readme_path.read_text(encoding="utf-8")

        # README includes project title
        assert "# e2e-mermaid" in content
        # README includes mermaid block
        assert "```mermaid" in content
        assert "flowchart TD" in content
        # README includes stats table
        assert "Node Statistics" in content
        assert "| GOAL |" in content
        assert "| SPEC |" in content
        assert "| TASK |" in content
        # Auto-generation marker
        assert "Auto-generated by KGN" in content


# ══════════════════════════════════════════════════════════════════════
#  Scenario 5 — Sync Conflict Detection
# ══════════════════════════════════════════════════════════════════════


class TestSyncConflictDetection:
    """Export → modify DB → modify file → detect conflict via hash mismatch."""

    def test_detect_hash_mismatch(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        from kgn.github.sync_service import ConflictDetector

        # 1. Create and export
        project_name = f"e2e-conflict-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        aid = repo.get_or_create_agent(pid, "conflict-agent")
        _insert_node(repo, pid, aid, title="Conflict Node", node_type=NodeType.SPEC)

        export_svc = ExportService(repo)
        export_svc.export_project(project_name, pid, tmp_path, agent_id=str(aid))

        # 2. Record "last sync" hashes from exported files
        import hashlib

        last_sync_hash: dict[str, str] = {}
        proj_dir = tmp_path / project_name
        for f in proj_dir.rglob("*.kgn"):
            rel = str(f.relative_to(tmp_path))
            content = f.read_text(encoding="utf-8")
            last_sync_hash[rel] = hashlib.sha256(content.encode()).hexdigest()

        # 3. Modify a file on disk (simulate external edit)
        kgn_files = list(proj_dir.rglob("*.kgn"))
        assert len(kgn_files) >= 1
        target_file = kgn_files[0]
        original = target_file.read_text(encoding="utf-8")
        target_file.write_text(original + "\n<!-- modified by external tool -->", encoding="utf-8")

        # 4. Compute current file hashes
        current_files: dict[str, str] = {}
        for f in proj_dir.rglob("*.kgn"):
            rel = str(f.relative_to(tmp_path))
            content = f.read_text(encoding="utf-8")
            current_files[rel] = hashlib.sha256(content.encode()).hexdigest()

        # 5. Detect sync conflicts
        detector = ConflictDetector()
        conflicts = detector.detect_sync_conflicts(
            sync_dir=tmp_path,
            last_sync_hash=last_sync_hash,
            current_files=current_files,
        )

        assert len(conflicts) >= 1
        assert conflicts[0].reason == "both_modified"

    def test_merge_conflict_parsing(self) -> None:
        """ConflictDetector parses git pull output for CONFLICT lines."""
        from kgn.github.sync_service import ConflictDetector

        pull_output = (
            "Auto-merging nodes/SPEC/auth.kgn\n"
            "CONFLICT (content): Merge conflict in nodes/SPEC/auth.kgn\n"
            "Auto-merging nodes/TASK/jwt.kgn\n"
            "CONFLICT (content): Merge conflict in nodes/TASK/jwt.kgn\n"
            "Automatic merge failed; fix conflicts and then commit.\n"
        )

        detector = ConflictDetector()
        conflicts = detector.detect_merge_conflicts(pull_output)

        assert len(conflicts) == 2
        assert "auth.kgn" in conflicts[0].file_path
        assert "jwt.kgn" in conflicts[1].file_path
        assert all(c.reason == "merge_conflict" for c in conflicts)

    def test_no_conflict_when_unchanged(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        """When files are not modified, no conflicts should be detected."""
        from kgn.github.sync_service import ConflictDetector

        project_name = f"e2e-noconflict-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        aid = repo.get_or_create_agent(pid, "nc-agent")
        _insert_node(repo, pid, aid, title="Unchanged", node_type=NodeType.GOAL)

        export_svc = ExportService(repo)
        export_svc.export_project(project_name, pid, tmp_path, agent_id=str(aid))

        # Same hashes → no conflict
        import hashlib

        hashes: dict[str, str] = {}
        proj_dir = tmp_path / project_name
        for f in proj_dir.rglob("*.kgn"):
            rel = str(f.relative_to(tmp_path))
            content = f.read_text(encoding="utf-8")
            hashes[rel] = hashlib.sha256(content.encode()).hexdigest()

        detector = ConflictDetector()
        conflicts = detector.detect_sync_conflicts(
            sync_dir=tmp_path,
            last_sync_hash=hashes,
            current_files=hashes,
        )
        assert len(conflicts) == 0


# ══════════════════════════════════════════════════════════════════════
#  Scenario 6 — Cross-cutting: Export + Mermaid README
# ══════════════════════════════════════════════════════════════════════


class TestExportWithMermaidReadme:
    """Export generates README.md with Mermaid diagrams alongside .kgn files."""

    def test_export_and_readme_coexist(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        project_name = f"e2e-readme-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        aid = repo.get_or_create_agent(pid, "readme-agent")

        g = _insert_node(repo, pid, aid, title="Project Goal", node_type=NodeType.GOAL)
        s = _insert_node(repo, pid, aid, title="API Spec", node_type=NodeType.SPEC)
        _insert_edge(repo, pid, aid, g, s, EdgeType.IMPLEMENTS)

        # Export
        export_svc = ExportService(repo)
        export_svc.export_project(project_name, pid, tmp_path, agent_id=str(aid))

        # Generate README
        gen = MermaidGenerator(repo)
        readme_path = gen.generate_readme(pid, project_name, tmp_path)

        # .kgn files exist
        proj_dir = tmp_path / project_name
        kgn_files = list(proj_dir.rglob("*.kgn"))
        assert len(kgn_files) == 2

        # README exists at target root
        assert readme_path.exists()
        content = readme_path.read_text(encoding="utf-8")
        assert "```mermaid" in content
        assert "|IMPLEMENTS|" in content
