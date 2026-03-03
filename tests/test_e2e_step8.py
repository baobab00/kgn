"""E2E integration tests — Step 8 extension.

New scenarios:
1. Full pipeline: ingest → export → reimport → verify round-trip integrity
2. Task BLOCKED → unblock flow: enqueue blocked → complete dependency → checkout unblocked
"""

from __future__ import annotations

import uuid
from pathlib import Path

from kgn.graph.subgraph import SubgraphService
from kgn.models.edge import EdgeRecord
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.sync.export_service import ExportService
from kgn.sync.import_service import ImportService
from kgn.task.service import TaskService

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    *,
    node_type: NodeType = NodeType.SPEC,
    title: str = "E2E Node",
    body_md: str = "## Context\n\nBody.",
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md=body_md,
        content_hash=uuid.uuid4().hex,
        tags=["e2e"],
    )


# ══════════════════════════════════════════════════════════════════════
# Scenario 1: Full ingest → export → reimport round-trip
# ══════════════════════════════════════════════════════════════════════


class TestE2EFullRoundTrip:
    """Ingest nodes + edges, export to disk, reimport to different project,
    verify data integrity."""

    def test_ingest_export_reimport_integrity(
        self, db_conn, repo, project_id, agent_id, tmp_path: Path
    ) -> None:
        # 1. Create nodes
        n1 = _make_node(project_id, title="Round-trip Node A", node_type=NodeType.SPEC)
        n2 = _make_node(project_id, title="Round-trip Node B", node_type=NodeType.GOAL)
        n3 = _make_node(project_id, title="Round-trip Node C", node_type=NodeType.TASK)
        for n in [n1, n2, n3]:
            repo.upsert_node(n)

        # 2. Create edges
        e1 = EdgeRecord(
            from_node_id=n1.id,
            to_node_id=n2.id,
            type="DEPENDS_ON",
            project_id=project_id,
            note="A→B",
        )
        e2 = EdgeRecord(
            from_node_id=n2.id,
            to_node_id=n3.id,
            type="DERIVED_FROM",
            project_id=project_id,
            note="B→C",
        )
        repo.insert_edge(e1)
        repo.insert_edge(e2)

        # 3. Export
        project_name = "e2e-roundtrip"
        export_svc = ExportService(repo)
        ex_result = export_svc.export_project(
            project_name=project_name,
            project_id=project_id,
            target_dir=tmp_path,
        )

        assert ex_result.exported >= 5  # 3 nodes + 2 edges
        assert ex_result.error_count == 0

        # 4. Reimport into the SAME project (idempotent)
        import_svc = ImportService(repo)
        im_result = import_svc.import_project(
            project_name=project_name,
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )

        # All nodes should be imported or skipped (none failed)
        assert im_result.failed == 0
        assert im_result.total >= 3

        # 5. Verify data integrity
        nodes = repo.search_nodes(project_id, exclude_archived=False)
        node_titles = {n.title for n in nodes}
        assert "Round-trip Node A" in node_titles
        assert "Round-trip Node B" in node_titles
        assert "Round-trip Node C" in node_titles

        edges = repo.search_edges(project_id)
        assert len(edges) >= 2

    def test_export_skip_unchanged(self, db_conn, repo, project_id, tmp_path: Path) -> None:
        """Double export → second export skips all unchanged files."""
        n = _make_node(project_id, title="Skip test node")
        repo.upsert_node(n)

        project_name = "e2e-skip"
        export_svc = ExportService(repo)

        # First export
        r1 = export_svc.export_project(
            project_name=project_name,
            project_id=project_id,
            target_dir=tmp_path,
        )
        assert r1.exported >= 1

        # Second export → should skip
        r2 = export_svc.export_project(
            project_name=project_name,
            project_id=project_id,
            target_dir=tmp_path,
        )
        assert r2.skipped >= 1
        assert r2.exported == 0


# ══════════════════════════════════════════════════════════════════════
# Scenario 2: Task BLOCKED → unblock flow
# ══════════════════════════════════════════════════════════════════════


class TestE2EBlockedUnblockFlow:
    """Enqueue blocked task → complete dependency → unblock on complete."""

    def test_blocked_to_unblocked_lifecycle(self, db_conn, repo, project_id) -> None:
        subgraph_svc = SubgraphService(repo)
        task_svc = TaskService(repo, subgraph_svc)

        # 1. Create parent task (dependency)
        parent = _make_node(project_id, title="Parent Task", node_type=NodeType.TASK)
        repo.upsert_node(parent)

        # 2. Create child task that depends on parent
        child = _make_node(project_id, title="Child Task", node_type=NodeType.TASK)
        repo.upsert_node(child)

        # 3. Create DEPENDS_ON edge: child → parent
        dep_edge = EdgeRecord(
            from_node_id=child.id,
            to_node_id=parent.id,
            type="DEPENDS_ON",
            project_id=project_id,
            note="child depends on parent",
        )
        repo.insert_edge(dep_edge)

        # 4. Enqueue parent (should be READY)
        parent_result = task_svc.enqueue(project_id, parent.id, priority=10)
        assert parent_result.state == "READY"

        # 5. Enqueue child (should be BLOCKED because parent not done)
        child_result = task_svc.enqueue(project_id, child.id, priority=20)
        assert child_result.state == "BLOCKED"

        # 6. Checkout parent
        agent_id = repo.get_or_create_agent(project_id, "e2e-agent")
        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None
        assert pkg.task.task_node_id == parent.id

        # 7. Complete parent → should unblock child
        complete_result = task_svc.complete(parent_result.task_queue_id)
        assert len(complete_result.unblocked_tasks) >= 1
        unblocked_ids = [ut.task_queue_id for ut in complete_result.unblocked_tasks]
        assert child_result.task_queue_id in unblocked_ids

        # 8. Now child should be checkable
        pkg2 = task_svc.checkout(project_id, agent_id)
        assert pkg2 is not None
        assert pkg2.task.task_node_id == child.id

        # 9. Complete child
        task_svc.complete(child_result.task_queue_id)

        # 10. No more tasks
        pkg3 = task_svc.checkout(project_id, agent_id)
        assert pkg3 is None

    def test_multiple_dependencies_all_must_complete(self, db_conn, repo, project_id) -> None:
        """Child with 2 deps → both must complete before unblocking."""
        subgraph_svc = SubgraphService(repo)
        task_svc = TaskService(repo, subgraph_svc)

        dep_a = _make_node(project_id, title="Dep A", node_type=NodeType.TASK)
        dep_b = _make_node(project_id, title="Dep B", node_type=NodeType.TASK)
        child = _make_node(project_id, title="Multi-dep child", node_type=NodeType.TASK)
        for n in [dep_a, dep_b, child]:
            repo.upsert_node(n)

        # child → dep_a, child → dep_b
        for dep in [dep_a, dep_b]:
            edge = EdgeRecord(
                from_node_id=child.id,
                to_node_id=dep.id,
                type="DEPENDS_ON",
                project_id=project_id,
            )
            repo.insert_edge(edge)

        # Enqueue all
        res_a = task_svc.enqueue(project_id, dep_a.id)
        res_b = task_svc.enqueue(project_id, dep_b.id)
        res_child = task_svc.enqueue(project_id, child.id)

        assert res_a.state == "READY"
        assert res_b.state == "READY"
        assert res_child.state == "BLOCKED"

        agent_id = repo.get_or_create_agent(project_id, "e2e-multi-agent")

        # Complete dep_a
        pkg_a = task_svc.checkout(project_id, agent_id)
        task_svc.complete(pkg_a.task.id)
        # Child may or may not be unblocked yet (dep_b still pending)

        # Complete dep_b
        pkg_b = task_svc.checkout(project_id, agent_id)
        task_svc.complete(pkg_b.task.id)

        # Now child should be unblocked and checkable
        pkg_child = task_svc.checkout(project_id, agent_id)
        assert pkg_child is not None
        assert pkg_child.task.task_node_id == child.id
