"""Tests for MCP task tools (Phase 4 Step 4).

task_checkout, task_complete, task_fail — 3 tool verification.
SAVEPOINT transaction connection injection for DB integration tests.
"""

from __future__ import annotations

import json
import uuid

from kgn.mcp.server import create_server
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _make_task_node(
    project_id: uuid.UUID,
    *,
    title: str = "Fix bug",
    body_md: str = "details",
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.TASK,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md=body_md,
        content_hash=uuid.uuid4().hex,
    )


def _call_tool(server, tool_name: str, **kwargs) -> str:
    """Invoke a registered FastMCP tool by name (sync wrapper)."""
    import asyncio

    async def _run():
        return await server.call_tool(tool_name, kwargs)

    raw = asyncio.run(_run())
    content_list = raw[0] if isinstance(raw, tuple) else raw
    if content_list and hasattr(content_list[0], "text"):
        return content_list[0].text
    return str(content_list)


def _setup_project_with_task(repo, *, project_name: str | None = None):
    """Create project + TASK node + enqueue → return (project_name, task_node)."""
    name = project_name or f"mcp-task-{uuid.uuid4().hex[:8]}"
    pid = repo.get_or_create_project(name)
    node = _make_task_node(pid, title="Implement feature X")
    repo.upsert_node(node)
    repo.enqueue_task(pid, node.id)
    return name, pid, node


# ── task_checkout ──────────────────────────────────────────────────────


class TestTaskCheckout:
    def test_checkout_returns_context_package(self, db_conn, repo, project_id, agent_id) -> None:
        name, pid, node = _setup_project_with_task(repo)
        agent_key = f"agent-{uuid.uuid4().hex[:8]}"
        server = create_server(name, conn=db_conn)

        result = _call_tool(server, "task_checkout", project=name, agent=agent_key)
        data = json.loads(result)

        assert "task" in data
        assert data["task"]["node_id"] == str(node.id)
        assert "node" in data
        assert data["node"]["title"] == "Implement feature X"

    def test_checkout_empty_queue(self, db_conn, repo) -> None:
        name = f"mcp-task-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(name)
        server = create_server(name, conn=db_conn)

        result = _call_tool(server, "task_checkout", project=name, agent="idle-agent")
        data = json.loads(result)

        assert data["status"] == "empty"
        assert "No tasks available" in data["message"]

    def test_checkout_unknown_project(self, db_conn, repo) -> None:
        name = f"mcp-task-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(name)
        server = create_server(name, conn=db_conn)

        result = _call_tool(
            server,
            "task_checkout",
            project="nonexistent-project-xyz",
            agent="a",
        )
        data = json.loads(result)

        assert "error" in data
        assert "not found" in data["error"].lower()

    def test_checkout_requeues_expired_task(self, db_conn, repo) -> None:
        """Expired lease task is auto-requeued and available for checkout."""
        name, pid, node = _setup_project_with_task(repo)
        agent_key = f"agent-{uuid.uuid4().hex[:8]}"
        agent_id = repo.get_or_create_agent(pid, agent_key)

        # 1. checkout (transition to IN_PROGRESS)
        task = repo.checkout_task(pid, agent_id)
        assert task is not None

        # 2. Force-expire the lease
        db_conn.execute(
            "UPDATE task_queue SET lease_expires_at = now() - interval '1 hour' WHERE id = %s",
            (task.id,),
        )

        # 3. task_checkout call → requeue_expired runs first, restoring to READY, then checkout
        server = create_server(name, conn=db_conn)
        result = _call_tool(
            server,
            "task_checkout",
            project=name,
            agent=agent_key,
        )
        data = json.loads(result)

        assert "task" in data
        assert data["task"]["node_id"] == str(node.id)


# ── task_complete ──────────────────────────────────────────────────────


class TestTaskComplete:
    def test_complete_success(self, db_conn, repo) -> None:
        name, pid, node = _setup_project_with_task(repo)
        agent_key = f"agent-{uuid.uuid4().hex[:8]}"
        agent_id = repo.get_or_create_agent(pid, agent_key)
        # checkout to move IN_PROGRESS
        task = repo.checkout_task(pid, agent_id)
        assert task is not None

        server = create_server(name, conn=db_conn)
        result = _call_tool(server, "task_complete", task_id=str(task.id))
        data = json.loads(result)

        assert data["status"] == "ok"
        assert "completed" in data["message"]

    def test_complete_invalid_uuid(self, db_conn, repo) -> None:
        name = f"mcp-task-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(name)
        server = create_server(name, conn=db_conn)

        result = _call_tool(server, "task_complete", task_id="not-a-uuid")
        data = json.loads(result)

        assert "error" in data
        assert "Invalid UUID" in data["error"]

    def test_complete_not_in_progress(self, db_conn, repo) -> None:
        """Completing a task not IN_PROGRESS → error."""
        name, pid, node = _setup_project_with_task(repo)
        # enqueue only, no checkout → READY state
        task_id = repo.enqueue_task(pid, node.id)

        server = create_server(name, conn=db_conn)
        result = _call_tool(server, "task_complete", task_id=str(task_id))
        data = json.loads(result)

        assert "error" in data

    def test_complete_nonexistent_task(self, db_conn, repo) -> None:
        name = f"mcp-task-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(name)
        server = create_server(name, conn=db_conn)

        fake_id = str(uuid.uuid4())
        result = _call_tool(server, "task_complete", task_id=fake_id)
        data = json.loads(result)

        assert "error" in data


# ── task_fail ──────────────────────────────────────────────────────────


class TestTaskFail:
    def test_fail_success(self, db_conn, repo) -> None:
        name, pid, node = _setup_project_with_task(repo)
        agent_key = f"agent-{uuid.uuid4().hex[:8]}"
        agent_id = repo.get_or_create_agent(pid, agent_key)
        task = repo.checkout_task(pid, agent_id)
        assert task is not None

        server = create_server(name, conn=db_conn)
        result = _call_tool(server, "task_fail", task_id=str(task.id), reason="OOM")
        data = json.loads(result)

        assert data["status"] == "ok"
        assert "failed" in data["message"]
        assert data["reason"] == "OOM"

    def test_fail_without_reason(self, db_conn, repo) -> None:
        name, pid, node = _setup_project_with_task(repo)
        agent_key = f"agent-{uuid.uuid4().hex[:8]}"
        agent_id = repo.get_or_create_agent(pid, agent_key)
        task = repo.checkout_task(pid, agent_id)
        assert task is not None

        server = create_server(name, conn=db_conn)
        result = _call_tool(server, "task_fail", task_id=str(task.id))
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["reason"] == ""

    def test_fail_invalid_uuid(self, db_conn, repo) -> None:
        name = f"mcp-task-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(name)
        server = create_server(name, conn=db_conn)

        result = _call_tool(server, "task_fail", task_id="bad", reason="x")
        data = json.loads(result)

        assert "error" in data

    def test_fail_not_in_progress(self, db_conn, repo) -> None:
        """Failing a READY task → error."""
        name, pid, node = _setup_project_with_task(repo)
        task_id = repo.enqueue_task(pid, node.id)

        server = create_server(name, conn=db_conn)
        result = _call_tool(server, "task_fail", task_id=str(task_id), reason="test")
        data = json.loads(result)

        assert "error" in data
