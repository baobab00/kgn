"""Tests for ConflictResolutionService — concurrent edit mediation (Phase 10, Step 5).

Covers:
- Repository layer: get_latest_node_version, get_node_version, get_node_version_count, update_node_status
- ConflictResolutionService.detect(): same agent no conflict, different agent conflict, no versions
- ConflictResolutionService.create_review_task(): ISSUE + TASK + edge + queue creation
- ConflictResolutionService.resolve(): accept_a, accept_b, merge, invalid resolution
- IngestService hook: concurrent edit auto-creates review task
- Integration: full conflict lifecycle (detect → review task → resolve)

Target: 25+ tests
"""

from __future__ import annotations

import uuid

import pytest
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.errors import KgnError, KgnErrorCode
from kgn.ingest.service import IngestService
from kgn.models.enums import (
    ActivityType,
    EdgeType,
    NodeStatus,
    NodeType,
)
from kgn.models.node import NodeRecord
from kgn.orchestration.conflict_resolution import (
    CONFLICT_TASK_PRIORITY,
    ConflictDetection,
    ConflictRecord,
    ConflictResolutionService,
    ResolutionResult,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    *,
    title: str = "Conflict Test Node",
    body: str = "## Content\n\nOriginal body.",
    node_type: NodeType = NodeType.SPEC,
    created_by: uuid.UUID | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md=body,
        content_hash=uuid.uuid4().hex,
        created_by=created_by,
    )


def _make_second_agent(repo: KgnRepository, project_id: uuid.UUID) -> uuid.UUID:
    return repo.get_or_create_agent(project_id, f"agent-b-{uuid.uuid4().hex[:8]}")


def _update_node_as_agent(
    repo: KgnRepository,
    node: NodeRecord,
    agent_id: uuid.UUID,
    *,
    new_body: str = "## Content\n\nUpdated body.",
    new_title: str | None = None,
) -> None:
    """Simulate a node update by a specific agent (triggers version save)."""
    updated = NodeRecord(
        id=node.id,
        project_id=node.project_id,
        type=node.type,
        status=node.status,
        title=new_title or node.title,
        body_md=new_body,
        content_hash=uuid.uuid4().hex,
        created_by=agent_id,
    )
    repo.upsert_node(updated)


def _make_kgn_content(
    *,
    node_id: str = "new:conflict-test",
    project_name: str = "test-project",
    agent_name: str = "mcp",
    title: str = "Conflict Test",
    body: str = "## Content\n\nTest body.",
) -> str:
    return (
        "---\n"
        'kgn_version: "0.1"\n'
        f'id: "{node_id}"\n'
        "type: SPEC\n"
        f'title: "{title}"\n'
        "status: ACTIVE\n"
        f'project_id: "{project_name}"\n'
        f'agent_id: "{agent_name}"\n'
        'created_at: "2026-03-01T00:00:00+09:00"\n'
        'tags: ["test"]\n'
        "confidence: 0.9\n"
        "---\n"
        f"\n{body}\n"
    )


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def conflict_svc(repo: KgnRepository) -> ConflictResolutionService:
    return ConflictResolutionService(repo)


# ══════════════════════════════════════════════════════════════════════
# Repository helper tests
# ══════════════════════════════════════════════════════════════════════


class TestRepositoryVersionHelpers:
    """Test new repository methods for version management."""

    def test_get_latest_version_none(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        assert repo.get_latest_node_version(node.id) is None

    def test_get_latest_version_after_update(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id, created_by=agent_id)
        repo.upsert_node(node)
        # Update to create a version
        _update_node_as_agent(repo, node, agent_id, new_body="Updated v1")

        ver = repo.get_latest_node_version(node.id)
        assert ver is not None
        assert ver["version"] == 1
        assert ver["updated_by"] == agent_id

    def test_get_node_version_specific(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id, created_by=agent_id)
        repo.upsert_node(node)
        _update_node_as_agent(repo, node, agent_id, new_body="V1")

        ver = repo.get_node_version(node.id, 1)
        assert ver is not None
        assert ver["version"] == 1

        assert repo.get_node_version(node.id, 999) is None

    def test_get_node_version_count(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id, created_by=agent_id)
        repo.upsert_node(node)
        assert repo.get_node_version_count(node.id) == 0

        _update_node_as_agent(repo, node, agent_id, new_body="V1")
        assert repo.get_node_version_count(node.id) == 1

        _update_node_as_agent(repo, node, agent_id, new_body="V2")
        assert repo.get_node_version_count(node.id) == 2

    def test_update_node_status(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)

        result = repo.update_node_status(node.id, "DEPRECATED")
        assert result is True

        updated = repo.get_node_by_id(node.id)
        assert updated is not None
        assert updated.status == NodeStatus.DEPRECATED

    def test_update_node_status_nonexistent(
        self,
        repo: KgnRepository,
    ) -> None:
        assert repo.update_node_status(uuid.uuid4(), "DEPRECATED") is False


# ══════════════════════════════════════════════════════════════════════
# ConflictResolutionService.detect() tests
# ══════════════════════════════════════════════════════════════════════


class TestConflictDetection:
    """Conflict detection logic tests."""

    def test_no_versions_no_conflict(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Brand new node — no versions, no conflict."""
        node = _make_node(project_id)
        repo.upsert_node(node)

        det = conflict_svc.detect(node.id, agent_id)
        assert det.detected is False
        assert det.node_id == node.id

    def test_same_agent_no_conflict(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Same agent updating — no conflict."""
        node = _make_node(project_id, created_by=agent_id)
        repo.upsert_node(node)
        _update_node_as_agent(repo, node, agent_id, new_body="Updated")

        det = conflict_svc.detect(node.id, agent_id)
        assert det.detected is False
        assert det.previous_agent == agent_id

    def test_different_agent_conflict(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Different agent updating — conflict detected."""
        node = _make_node(project_id, created_by=agent_id)
        repo.upsert_node(node)
        _update_node_as_agent(repo, node, agent_id, new_body="V1 by A")

        agent_b = _make_second_agent(repo, project_id)
        det = conflict_svc.detect(node.id, agent_b)
        assert det.detected is True
        assert det.previous_agent == agent_id
        assert det.current_agent == agent_b
        assert det.previous_version == 1

    def test_null_updated_by_no_conflict(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        """Version with NULL updated_by — no conflict."""
        node = _make_node(project_id, created_by=agent_id)
        repo.upsert_node(node)
        _update_node_as_agent(repo, node, agent_id)
        # Force updated_by to NULL
        db_conn.execute(
            "UPDATE node_versions SET updated_by = NULL WHERE node_id = %s",
            (node.id,),
        )

        det = conflict_svc.detect(node.id, agent_id)
        assert det.detected is False

    def test_detection_dataclass(self) -> None:
        det = ConflictDetection(
            detected=True,
            node_id=uuid.uuid4(),
            current_agent=uuid.uuid4(),
            previous_agent=uuid.uuid4(),
            previous_version=3,
        )
        assert det.detected is True
        assert det.previous_version == 3


# ══════════════════════════════════════════════════════════════════════
# ConflictResolutionService.create_review_task() tests
# ══════════════════════════════════════════════════════════════════════


class TestCreateReviewTask:
    """Review task creation tests."""

    def test_creates_issue_and_task(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id, created_by=agent_id)
        repo.upsert_node(node)
        agent_b = _make_second_agent(repo, project_id)

        record = conflict_svc.create_review_task(
            project_id,
            node.id,
            agent_id,
            agent_b,
        )

        assert isinstance(record, ConflictRecord)
        assert record.node_id == node.id
        assert record.agent_a == agent_id
        assert record.agent_b == agent_b

        # Verify ISSUE node exists
        issue = repo.get_node_by_id(record.issue_node_id)
        assert issue is not None
        assert issue.type == NodeType.ISSUE
        assert "conflict" in issue.tags

        # Verify TASK node exists
        task = repo.get_node_by_id(record.review_task_node_id)
        assert task is not None
        assert task.type == NodeType.TASK
        assert "role:reviewer" in task.tags

    def test_creates_contradicts_edge(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id, created_by=agent_id)
        repo.upsert_node(node)
        agent_b = _make_second_agent(repo, project_id)

        record = conflict_svc.create_review_task(
            project_id,
            node.id,
            agent_id,
            agent_b,
        )

        # Verify CONTRADICTS edge
        edge = repo.find_contradicts_edge(
            project_id,
            record.issue_node_id,
            node.id,
        )
        assert edge is not None
        assert edge["status"] == "PENDING"

    def test_enqueues_task_with_priority(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id, created_by=agent_id)
        repo.upsert_node(node)
        agent_b = _make_second_agent(repo, project_id)

        record = conflict_svc.create_review_task(
            project_id,
            node.id,
            agent_id,
            agent_b,
        )

        task_item = repo.get_task_status(record.task_queue_id)
        assert task_item is not None
        assert task_item.state == "READY"
        assert task_item.priority == CONFLICT_TASK_PRIORITY

    def test_creates_resolves_edge(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id, created_by=agent_id)
        repo.upsert_node(node)
        agent_b = _make_second_agent(repo, project_id)

        record = conflict_svc.create_review_task(
            project_id,
            node.id,
            agent_id,
            agent_b,
        )

        # There should be a RESOLVES edge from task to issue
        edges = repo.get_edges_from(record.review_task_node_id)
        resolves = [e for e in edges if e.type == EdgeType.RESOLVES]
        assert len(resolves) == 1
        assert resolves[0].to_node_id == record.issue_node_id

    def test_logs_conflict_activity(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        node = _make_node(project_id, created_by=agent_id)
        repo.upsert_node(node)
        agent_b = _make_second_agent(repo, project_id)

        conflict_svc.create_review_task(
            project_id,
            node.id,
            agent_id,
            agent_b,
        )

        rows = db_conn.execute(
            "SELECT activity_type FROM agent_activities "
            "WHERE project_id = %s AND activity_type = %s",
            (project_id, ActivityType.CONFLICT_DETECTED.value),
        ).fetchall()
        assert len(rows) >= 1


# ══════════════════════════════════════════════════════════════════════
# ConflictResolutionService.resolve() tests
# ══════════════════════════════════════════════════════════════════════


class TestResolveConflict:
    """Conflict resolution tests."""

    def _setup_conflict(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_a: uuid.UUID,
    ) -> tuple[NodeRecord, uuid.UUID, ConflictRecord]:
        """Create a node, update with agent_b, detect conflict, create review."""
        node = _make_node(project_id, title="Conflicted", created_by=agent_a)
        repo.upsert_node(node)
        _update_node_as_agent(repo, node, agent_a, new_body="Agent A version")

        agent_b = _make_second_agent(repo, project_id)
        _update_node_as_agent(repo, node, agent_b, new_body="Agent B version")

        record = conflict_svc.create_review_task(
            project_id,
            node.id,
            agent_a,
            agent_b,
        )
        return node, agent_b, record

    def test_accept_a_reverts(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node, agent_b, record = self._setup_conflict(
            conflict_svc,
            repo,
            project_id,
            agent_id,
        )

        result = conflict_svc.resolve(
            project_id,
            node.id,
            "accept_a",
            agent_id=agent_id,
        )

        assert result.resolution == "accept_a"
        assert result.node_id == node.id

        # Node body should be reverted to Agent A's version
        reverted = repo.get_node_by_id(node.id)
        assert reverted is not None
        assert "Agent A version" in reverted.body_md

    def test_accept_b_keeps_current(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node, agent_b, record = self._setup_conflict(
            conflict_svc,
            repo,
            project_id,
            agent_id,
        )

        result = conflict_svc.resolve(
            project_id,
            node.id,
            "accept_b",
            agent_id=agent_id,
        )

        assert result.resolution == "accept_b"

        # Node body should still be Agent B's version
        current = repo.get_node_by_id(node.id)
        assert current is not None
        assert "Agent B version" in current.body_md

    def test_merge_updates_body(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node, agent_b, record = self._setup_conflict(
            conflict_svc,
            repo,
            project_id,
            agent_id,
        )

        merge_body = "## Content\n\nMerged: A + B combined."
        result = conflict_svc.resolve(
            project_id,
            node.id,
            "merge",
            agent_id=agent_id,
            merge_body=merge_body,
        )

        assert result.resolution == "merge"

        merged = repo.get_node_by_id(node.id)
        assert merged is not None
        assert "Merged: A + B combined" in merged.body_md

    def test_resolve_approves_contradicts_edge(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node, agent_b, record = self._setup_conflict(
            conflict_svc,
            repo,
            project_id,
            agent_id,
        )

        conflict_svc.resolve(
            project_id,
            node.id,
            "accept_b",
            agent_id=agent_id,
        )

        # CONTRADICTS edge should now be APPROVED
        edge = repo.find_contradicts_edge(
            project_id,
            record.issue_node_id,
            node.id,
        )
        assert edge is not None
        assert edge["status"] == "APPROVED"

    def test_resolve_invalid_resolution_raises(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)

        with pytest.raises(KgnError) as exc_info:
            conflict_svc.resolve(
                project_id,
                node.id,
                "invalid",
            )
        assert exc_info.value.code == KgnErrorCode.CONFLICT_RESOLUTION_FAILED

    def test_resolve_merge_without_body_raises(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)

        with pytest.raises(KgnError) as exc_info:
            conflict_svc.resolve(
                project_id,
                node.id,
                "merge",
            )
        assert exc_info.value.code == KgnErrorCode.CONFLICT_RESOLUTION_FAILED

    def test_resolve_nonexistent_node_raises(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        with pytest.raises(KgnError) as exc_info:
            conflict_svc.resolve(
                project_id,
                uuid.uuid4(),
                "accept_a",
            )
        assert exc_info.value.code == KgnErrorCode.NODE_NOT_FOUND

    def test_resolve_accept_a_no_version_raises(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """accept_a with no previous version should fail."""
        node = _make_node(project_id)
        repo.upsert_node(node)

        with pytest.raises(KgnError) as exc_info:
            conflict_svc.resolve(
                project_id,
                node.id,
                "accept_a",
            )
        assert exc_info.value.code == KgnErrorCode.CONFLICT_RESOLUTION_FAILED

    def test_resolve_logs_activity(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        node, agent_b, record = self._setup_conflict(
            conflict_svc,
            repo,
            project_id,
            agent_id,
        )

        conflict_svc.resolve(
            project_id,
            node.id,
            "accept_b",
            agent_id=agent_id,
        )

        rows = db_conn.execute(
            "SELECT activity_type FROM agent_activities "
            "WHERE project_id = %s AND activity_type = %s",
            (project_id, ActivityType.CONFLICT_RESOLVED.value),
        ).fetchall()
        assert len(rows) >= 1


# ══════════════════════════════════════════════════════════════════════
# IngestService integration tests
# ══════════════════════════════════════════════════════════════════════


class TestIngestConflictHook:
    """Test that IngestService auto-detects conflicts on UPDATE."""

    def test_ingest_update_by_different_agent_creates_issue(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """When agent B updates a node last modified by agent A, an ISSUE is created."""
        # Agent A creates and updates a node
        node_id = uuid.uuid4()
        node = NodeRecord(
            id=node_id,
            project_id=project_id,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="Ingest Conflict Test",
            body_md="## Content\n\nOriginal.",
            content_hash=uuid.uuid4().hex,
            created_by=agent_id,
        )
        repo.upsert_node(node)
        # Create a version (simulating first update by agent_a)
        _update_node_as_agent(repo, node, agent_id, new_body="V1 by A")

        # Agent B updates via IngestService
        agent_b = _make_second_agent(repo, project_id)
        f"conflict-hook-{uuid.uuid4().hex[:8]}"
        # We need to use the same project_id
        svc = IngestService(repo, project_id, agent_b, enforce_project=True)

        content = _make_kgn_content(
            node_id=str(node_id),
            title="Ingest Conflict Test",
            body="## Content\n\nV2 by B.",
        )
        result = svc.ingest_text(content, ".kgn")
        assert result.success == 1

        # An ISSUE node should have been created for the conflict
        issues = repo.search_nodes(project_id, node_type=NodeType.ISSUE)
        conflict_issues = [n for n in issues if "conflict" in (n.tags or [])]
        assert len(conflict_issues) >= 1

    def test_ingest_update_by_same_agent_no_issue(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """Same agent updating — no conflict issue should be created."""
        # Create agent with known key so ingest resolves to the same UUID
        agent_key = f"same-agent-{uuid.uuid4().hex[:8]}"
        agent_a = repo.get_or_create_agent(project_id, agent_key)

        node_id = uuid.uuid4()
        node = NodeRecord(
            id=node_id,
            project_id=project_id,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="Same Agent Test",
            body_md="## Content\n\nOriginal.",
            content_hash=uuid.uuid4().hex,
            created_by=agent_a,
        )
        repo.upsert_node(node)
        _update_node_as_agent(repo, node, agent_a, new_body="V1 by A")

        # Same agent updates again via IngestService (same agent_key)
        svc = IngestService(repo, project_id, agent_a, enforce_project=True)
        content = _make_kgn_content(
            node_id=str(node_id),
            agent_name=agent_key,
            title="Same Agent Test",
            body="## Content\n\nV2 by A again.",
        )
        result = svc.ingest_text(content, ".kgn")
        assert result.success == 1

        # No conflict issues should exist
        issues = repo.search_nodes(project_id, node_type=NodeType.ISSUE)
        conflict_issues = [n for n in issues if "conflict" in (n.tags or [])]
        assert len(conflict_issues) == 0

    def test_ingest_create_no_conflict(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Creating a new node via ingest — no conflict check."""
        svc = IngestService(repo, project_id, agent_id, enforce_project=True)
        content = _make_kgn_content(
            node_id="new:no-conflict",
            title="New Node",
            body="## Content\n\nBrand new.",
        )
        result = svc.ingest_text(content, ".kgn")
        assert result.success == 1

        issues = repo.search_nodes(project_id, node_type=NodeType.ISSUE)
        conflict_issues = [n for n in issues if "conflict" in (n.tags or [])]
        assert len(conflict_issues) == 0


# ══════════════════════════════════════════════════════════════════════
# Full lifecycle integration test
# ══════════════════════════════════════════════════════════════════════


class TestConflictLifecycle:
    """End-to-end conflict lifecycle: detect → create review → resolve."""

    def test_full_lifecycle(
        self,
        conflict_svc: ConflictResolutionService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        # 1. Agent A creates and updates a node
        node = _make_node(project_id, title="Lifecycle Test", created_by=agent_id)
        repo.upsert_node(node)
        _update_node_as_agent(repo, node, agent_id, new_body="Agent A content")

        # 2. Agent B modifies
        agent_b = _make_second_agent(repo, project_id)
        _update_node_as_agent(repo, node, agent_b, new_body="Agent B content")

        # 3. Detect conflict
        det = conflict_svc.detect(node.id, agent_b)
        assert det.detected is True
        assert det.previous_agent == agent_id

        # 4. Create review task
        record = conflict_svc.create_review_task(
            project_id,
            node.id,
            agent_id,
            agent_b,
        )
        assert record.issue_node_id is not None
        assert record.review_task_node_id is not None

        # 5. Resolve with accept_b (keep B's version)
        result = conflict_svc.resolve(
            project_id,
            node.id,
            "accept_b",
            agent_id=agent_id,
        )
        assert result.resolution == "accept_b"

        # 6. Verify CONTRADICTS edge is APPROVED
        edge = repo.find_contradicts_edge(
            project_id,
            record.issue_node_id,
            node.id,
        )
        assert edge is not None
        assert edge["status"] == "APPROVED"

        # 7. Node content is unchanged (accept_b)
        final = repo.get_node_by_id(node.id)
        assert final is not None
        assert "Agent B content" in final.body_md

    def test_dataclasses(self) -> None:
        """Verify dataclass constructors."""
        record = ConflictRecord(
            issue_node_id=uuid.uuid4(),
            review_task_node_id=uuid.uuid4(),
            task_queue_id=uuid.uuid4(),
            contradicts_edge_id=1,
            node_id=uuid.uuid4(),
            agent_a=uuid.uuid4(),
            agent_b=uuid.uuid4(),
        )
        assert record.contradicts_edge_id == 1

        result = ResolutionResult(
            resolution="merge",
            node_id=uuid.uuid4(),
        )
        assert result.resolution == "merge"
        assert result.deprecated_node_id is None
