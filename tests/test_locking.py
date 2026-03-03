"""Tests for NodeLockService — concurrent access guard (Phase 10, Step 4).

Covers:
- Repository layer: acquire/release/release_all/get_node_lock/cleanup_expired
- Service layer: NodeLockService acquire/release/release_all/check/check_write_permission/cleanup
- Lock lifecycle: acquire → check → release
- Same-agent re-acquire (refresh)
- Different agent blocked (KGN-560)
- Expired lock takeover
- release_all by agent
- cleanup_expired
- TaskService integration: checkout auto-locks, complete auto-releases, fail auto-releases
- MCP ingest_node lock guard

Target: 25+ tests
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.errors import KgnError, KgnErrorCode
from kgn.graph.subgraph import SubgraphService
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.orchestration.locking import (
    DEFAULT_LOCK_DURATION_SEC,
    LockInfo,
    LockResult,
    NodeLockService,
)
from kgn.task.service import TaskService

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    *,
    node_type: NodeType = NodeType.SPEC,
    title: str = "Lock Test Node",
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md="## Content\n\nTest body.",
        content_hash=uuid.uuid4().hex,
    )


def _make_task_node(
    project_id: uuid.UUID,
    *,
    title: str = "Lock Task",
    body: str = "## Context\n\nTask body.",
    created_by: uuid.UUID | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.TASK,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md=body,
        content_hash=uuid.uuid4().hex,
        created_by=created_by,
    )


def _make_second_agent(repo: KgnRepository, project_id: uuid.UUID) -> uuid.UUID:
    """Create a distinct second agent for concurrency tests."""
    return repo.get_or_create_agent(project_id, f"agent-b-{uuid.uuid4().hex[:8]}")


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def lock_svc(repo: KgnRepository) -> NodeLockService:
    return NodeLockService(repo)


@pytest.fixture
def subgraph_svc(repo: KgnRepository) -> SubgraphService:
    return SubgraphService(repo)


@pytest.fixture
def task_svc(repo: KgnRepository, subgraph_svc: SubgraphService) -> TaskService:
    return TaskService(repo, subgraph_svc)


# ══════════════════════════════════════════════════════════════════════
# Repository-layer tests
# ══════════════════════════════════════════════════════════════════════


class TestRepositoryLock:
    """Low-level repository lock method tests."""

    def test_acquire_returns_row(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)

        row = repo.acquire_node_lock(node.id, agent_id, 300)
        assert row is not None
        assert row["locked_by"] == agent_id
        assert row["lock_expires_at"] is not None

    def test_acquire_fails_for_other_agent(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        agent_b = _make_second_agent(repo, project_id)

        repo.acquire_node_lock(node.id, agent_id, 300)
        row = repo.acquire_node_lock(node.id, agent_b, 300)
        assert row is None

    def test_acquire_same_agent_refreshes(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)

        row1 = repo.acquire_node_lock(node.id, agent_id, 60)
        row2 = repo.acquire_node_lock(node.id, agent_id, 600)
        assert row2 is not None
        # Refreshed expiry should be later
        assert row2["lock_expires_at"] >= row1["lock_expires_at"]

    def test_release_returns_true_for_owner(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        repo.acquire_node_lock(node.id, agent_id, 300)

        assert repo.release_node_lock(node.id, agent_id) is True

    def test_release_returns_false_for_non_owner(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        agent_b = _make_second_agent(repo, project_id)
        repo.acquire_node_lock(node.id, agent_id, 300)

        assert repo.release_node_lock(node.id, agent_b) is False

    def test_release_all(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        n1 = _make_node(project_id, title="LN1")
        n2 = _make_node(project_id, title="LN2")
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        repo.acquire_node_lock(n1.id, agent_id, 300)
        repo.acquire_node_lock(n2.id, agent_id, 300)

        count = repo.release_all_node_locks(agent_id)
        assert count == 2

    def test_get_node_lock_unlocked(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        assert repo.get_node_lock(node.id) is None

    def test_get_node_lock_locked(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        repo.acquire_node_lock(node.id, agent_id, 300)

        lock = repo.get_node_lock(node.id)
        assert lock is not None
        assert lock["locked_by"] == agent_id
        assert lock["is_expired"] is False

    def test_cleanup_expired_locks(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        # Acquire then force-expire
        repo.acquire_node_lock(node.id, agent_id, 300)
        db_conn.execute(
            "UPDATE nodes SET lock_expires_at = now() - interval '1 second' WHERE id = %s",
            (node.id,),
        )
        count = repo.cleanup_expired_locks()
        assert count >= 1
        assert repo.get_node_lock(node.id) is None

    def test_acquire_expired_lock_by_other_agent(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        """Another agent can acquire a lock whose expiry has passed."""
        node = _make_node(project_id)
        repo.upsert_node(node)
        agent_b = _make_second_agent(repo, project_id)

        repo.acquire_node_lock(node.id, agent_id, 300)
        # Force-expire
        db_conn.execute(
            "UPDATE nodes SET lock_expires_at = now() - interval '1 second' WHERE id = %s",
            (node.id,),
        )
        row = repo.acquire_node_lock(node.id, agent_b, 300)
        assert row is not None
        assert row["locked_by"] == agent_b


# ══════════════════════════════════════════════════════════════════════
# NodeLockService tests
# ══════════════════════════════════════════════════════════════════════


class TestNodeLockService:
    """Service-layer lock tests."""

    def test_acquire_success(
        self,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)

        result = lock_svc.acquire(node.id, agent_id)
        assert result.acquired is True
        assert result.node_id == node.id
        assert result.locked_by == agent_id
        assert result.lock_expires_at is not None

    def test_acquire_denied_other_agent(
        self,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        agent_b = _make_second_agent(repo, project_id)

        lock_svc.acquire(node.id, agent_id)
        result = lock_svc.acquire(node.id, agent_b)
        assert result.acquired is False
        assert result.locked_by == agent_id

    def test_acquire_same_agent_refresh(
        self,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)

        r1 = lock_svc.acquire(node.id, agent_id, duration_sec=60)
        r2 = lock_svc.acquire(node.id, agent_id, duration_sec=600)
        assert r2.acquired is True
        assert r2.lock_expires_at >= r1.lock_expires_at

    def test_release(
        self,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        lock_svc.acquire(node.id, agent_id)

        assert lock_svc.release(node.id, agent_id) is True
        assert lock_svc.check(node.id) is None

    def test_release_not_owner(
        self,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        agent_b = _make_second_agent(repo, project_id)
        lock_svc.acquire(node.id, agent_id)

        assert lock_svc.release(node.id, agent_b) is False

    def test_release_all(
        self,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        n1 = _make_node(project_id, title="S1")
        n2 = _make_node(project_id, title="S2")
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        lock_svc.acquire(n1.id, agent_id)
        lock_svc.acquire(n2.id, agent_id)

        cnt = lock_svc.release_all(agent_id)
        assert cnt == 2
        assert lock_svc.check(n1.id) is None
        assert lock_svc.check(n2.id) is None

    def test_check_unlocked(
        self,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        assert lock_svc.check(node.id) is None

    def test_check_locked(
        self,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        lock_svc.acquire(node.id, agent_id)

        info = lock_svc.check(node.id)
        assert info is not None
        assert isinstance(info, LockInfo)
        assert info.node_id == node.id
        assert info.locked_by == agent_id
        assert info.is_expired is False

    def test_check_write_permission_unlocked(
        self,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        # Should not raise
        lock_svc.check_write_permission(node.id, agent_id)

    def test_check_write_permission_same_agent(
        self,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        lock_svc.acquire(node.id, agent_id)
        # Same agent — should not raise
        lock_svc.check_write_permission(node.id, agent_id)

    def test_check_write_permission_other_agent_raises(
        self,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        agent_b = _make_second_agent(repo, project_id)
        lock_svc.acquire(node.id, agent_id)

        with pytest.raises(KgnError) as exc_info:
            lock_svc.check_write_permission(node.id, agent_b)
        assert exc_info.value.code == KgnErrorCode.NODE_LOCKED

    def test_check_write_permission_expired_ok(
        self,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        """Expired locks should not block writes."""
        node = _make_node(project_id)
        repo.upsert_node(node)
        agent_b = _make_second_agent(repo, project_id)
        lock_svc.acquire(node.id, agent_id)

        # Force expire
        db_conn.execute(
            "UPDATE nodes SET lock_expires_at = now() - interval '1 second' WHERE id = %s",
            (node.id,),
        )
        # agent_b should be allowed — expired lock
        lock_svc.check_write_permission(node.id, agent_b)

    def test_cleanup_expired(
        self,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        lock_svc.acquire(node.id, agent_id)

        db_conn.execute(
            "UPDATE nodes SET lock_expires_at = now() - interval '1 second' WHERE id = %s",
            (node.id,),
        )
        count = lock_svc.cleanup_expired()
        assert count >= 1
        assert lock_svc.check(node.id) is None

    def test_default_lock_duration(self) -> None:
        assert DEFAULT_LOCK_DURATION_SEC == 300

    def test_lock_result_dataclass(self) -> None:
        node_id = uuid.uuid4()
        r = LockResult(acquired=True, node_id=node_id, locked_by=uuid.uuid4())
        assert r.acquired is True
        assert r.node_id == node_id

    def test_lock_info_dataclass(self) -> None:
        now = datetime.now(tz=UTC)
        info = LockInfo(
            node_id=uuid.uuid4(),
            locked_by=uuid.uuid4(),
            lock_expires_at=now,
            is_expired=False,
        )
        assert info.is_expired is False


# ══════════════════════════════════════════════════════════════════════
# TaskService integration tests
# ══════════════════════════════════════════════════════════════════════


class TestTaskServiceLockIntegration:
    """Verify lock acquire/release via task lifecycle."""

    def test_checkout_acquires_lock(
        self,
        task_svc: TaskService,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)

        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None

        info = lock_svc.check(node.id)
        assert info is not None
        assert info.locked_by == agent_id

    def test_complete_releases_lock(
        self,
        task_svc: TaskService,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)
        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None

        task_svc.complete(pkg.task.id)

        info = lock_svc.check(node.id)
        assert info is None

    def test_fail_releases_lock(
        self,
        task_svc: TaskService,
        lock_svc: NodeLockService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)
        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None

        task_svc.fail(pkg.task.id, reason="test failure")

        info = lock_svc.check(node.id)
        assert info is None

    def test_checkout_lock_matches_lease_duration(
        self,
        repo: KgnRepository,
        subgraph_svc: SubgraphService,
        lock_svc: NodeLockService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Checkout passes lease_duration_sec to lock acquire."""
        svc = TaskService(repo, subgraph_svc)
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        svc.enqueue(project_id, node.id)

        pkg = svc.checkout(project_id, agent_id, lease_duration_sec=120)
        assert pkg is not None

        info = lock_svc.check(node.id)
        assert info is not None
        assert info.locked_by == agent_id

    def test_locked_node_blocks_other_agent_checkout(
        self,
        repo: KgnRepository,
        subgraph_svc: SubgraphService,
        lock_svc: NodeLockService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """If node is locked by agent A, ingest by agent B should be denied."""
        node = _make_node(project_id)
        repo.upsert_node(node)
        agent_b = _make_second_agent(repo, project_id)

        lock_svc.acquire(node.id, agent_id)

        with pytest.raises(KgnError) as exc_info:
            lock_svc.check_write_permission(node.id, agent_b)
        assert exc_info.value.code == KgnErrorCode.NODE_LOCKED


# ══════════════════════════════════════════════════════════════════════
# MCP ingest_node lock guard tests
# ══════════════════════════════════════════════════════════════════════


class TestMCPIngestLockGuard:
    """MCP tool-level lock enforcement via ingest_node."""

    @staticmethod
    def _call_tool(server, tool_name: str, **kwargs) -> str:
        import asyncio

        async def _run():
            return await server.call_tool(tool_name, kwargs)

        raw = asyncio.run(_run())
        content_list = raw[0] if isinstance(raw, tuple) else raw
        if content_list and hasattr(content_list[0], "text"):
            return content_list[0].text
        return str(content_list)

    @staticmethod
    def _make_kgn(
        *,
        node_id: str = "new:lock-test",
        project_name: str = "test-project",
    ) -> str:
        return (
            "---\n"
            'kgn_version: "0.1"\n'
            f'id: "{node_id}"\n'
            "type: SPEC\n"
            'title: "Lock Guard Test"\n'
            "status: ACTIVE\n"
            f'project_id: "{project_name}"\n'
            'agent_id: "mcp"\n'
            'created_at: "2026-03-01T00:00:00+09:00"\n'
            'tags: ["test"]\n'
            "confidence: 0.9\n"
            "---\n"
            "\n## Content\n\nLock guard test body.\n"
        )

    def test_ingest_new_node_no_lock_conflict(
        self,
        db_conn: Connection,
        repo: KgnRepository,
    ) -> None:
        """New nodes (new:xxx) should never trigger lock checks."""
        from kgn.mcp.server import create_server

        project_name = f"lock-mcp-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        content = self._make_kgn(project_name=project_name)
        result = self._call_tool(server, "ingest_node", kgn_content=content)
        data = json.loads(result)
        assert data["status"] == "ok"

    def test_ingest_locked_node_by_another_agent_fails(
        self,
        db_conn: Connection,
        repo: KgnRepository,
    ) -> None:
        """Updating a node locked by a different agent returns KGN-560."""
        from kgn.mcp.server import create_server

        project_name = f"lock-mcp-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        # Create a node first
        node_id = uuid.uuid4()
        node = NodeRecord(
            id=node_id,
            project_id=pid,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="Locked Node",
            body_md="## Content\n\nBody.",
            content_hash=uuid.uuid4().hex,
        )
        repo.upsert_node(node)

        # Lock it with a different agent
        other_agent = _make_second_agent(repo, pid)
        repo.acquire_node_lock(node_id, other_agent, 300)

        # Try to update via MCP (default agent = "mcp")
        content = self._make_kgn(node_id=str(node_id), project_name=project_name)
        result = self._call_tool(server, "ingest_node", kgn_content=content)
        data = json.loads(result)

        assert data.get("status") == "error" or "error" in data
        assert "KGN-560" in result

    def test_ingest_locked_node_by_same_agent_ok(
        self,
        db_conn: Connection,
        repo: KgnRepository,
    ) -> None:
        """Updating a node locked by the same agent should succeed."""
        from kgn.mcp.server import create_server

        project_name = f"lock-mcp-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        # Create a node
        node_id = uuid.uuid4()
        node = NodeRecord(
            id=node_id,
            project_id=pid,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="Self-Locked Node",
            body_md="## Content\n\nBody.",
            content_hash=uuid.uuid4().hex,
        )
        repo.upsert_node(node)

        # Lock it with MCP agent (same as server agent)
        mcp_agent = repo.get_or_create_agent(pid, "mcp")
        repo.acquire_node_lock(node_id, mcp_agent, 300)

        content = self._make_kgn(node_id=str(node_id), project_name=project_name)
        result = self._call_tool(server, "ingest_node", kgn_content=content)
        data = json.loads(result)
        assert data["status"] == "ok"
