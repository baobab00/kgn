"""Tests for MCP write tool error/edge paths — Step 8 coverage gaps.

Covers exception handlers & BLOCKED state in write.py
(lines 50-56, 121-127, 145-146, 184, 186, 205-214).
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

from kgn.errors import KgnError, KgnErrorCode
from kgn.mcp.server import create_server
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _call_tool(server, tool_name: str, **kwargs) -> str:
    import asyncio

    async def _run():
        return await server.call_tool(tool_name, kwargs)

    raw = asyncio.run(_run())
    content_list = raw[0] if isinstance(raw, tuple) else raw
    if content_list and hasattr(content_list[0], "text"):
        return content_list[0].text
    return str(content_list)


def _make_kgn_content(project_id: str = "test") -> str:
    return (
        "---\n"
        'kgn_version: "0.1"\n'
        'id: "new:test"\n'
        "type: SPEC\n"
        'title: "Test"\n'
        "status: ACTIVE\n"
        f'project_id: "{project_id}"\n'
        'agent_id: "mcp"\n'
        'created_at: "2026-03-01T00:00:00+09:00"\n'
        'tags: ["test"]\n'
        "confidence: 0.9\n"
        "---\n\n## Content\n\nBody.\n"
    )


def _make_kge_content(from_id: str, to_id: str, project_id: str = "test") -> str:
    return (
        "---\n"
        'kgn_version: "0.1"\n'
        f'project_id: "{project_id}"\n'
        'agent_id: "mcp"\n'
        'created_at: "2026-03-01T00:00:00+09:00"\n'
        "edges:\n"
        f'  - from: "{from_id}"\n'
        f'    to: "{to_id}"\n'
        "    type: DEPENDS_ON\n"
        '    note: "test"\n'
        "---\n"
    )


def _make_task_node(project_id, repo) -> NodeRecord:
    node = NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.TASK,
        status=NodeStatus.ACTIVE,
        title="Task test node",
        body_md="do it",
        content_hash=uuid.uuid4().hex,
    )
    repo.upsert_node(node)
    return node


# ── ingest_node exception handlers ────────────────────────────────────


class TestIngestNodeExceptions:
    """Cover except KgnError/Exception handlers in ingest_node (lines 50-56)."""

    def test_ingest_node_kgn_error_re_raised(self, db_conn, repo) -> None:
        """KgnError raised inside ingest_text → safe_tool_call catches it."""
        project_name = f"mcp-err-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        with patch(
            "kgn.ingest.service.IngestService.ingest_text",
            side_effect=KgnError(
                KgnErrorCode.VALIDATION_FAILED,
                "Mocked KgnError",
            ),
        ):
            result = _call_tool(server, "ingest_node", kgn_content=_make_kgn_content(project_name))

        data = json.loads(result)
        assert "error" in data
        assert data["code"] == KgnErrorCode.VALIDATION_FAILED.value

    def test_ingest_node_generic_exception(self, db_conn, repo) -> None:
        """Generic Exception → returns INVALID_KGN_FORMAT error JSON."""
        project_name = f"mcp-err-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        with patch(
            "kgn.ingest.service.IngestService.ingest_text",
            side_effect=RuntimeError("unexpected boom"),
        ):
            result = _call_tool(server, "ingest_node", kgn_content=_make_kgn_content(project_name))

        data = json.loads(result)
        assert "error" in data
        assert data["code"] == KgnErrorCode.INVALID_KGN_FORMAT.value
        assert "unexpected boom" in data["detail"]


# ── ingest_edge exception handlers ────────────────────────────────────


class TestIngestEdgeExceptions:
    """Cover except KgnError/Exception in ingest_edge (lines 121-127)
    and result.failed > 0 path (lines 145-146)."""

    def test_ingest_edge_kgn_error_re_raised(self, db_conn, repo) -> None:
        """KgnError raised inside ingest_text → safe_tool_call catches it."""
        project_name = f"mcp-err-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        with patch(
            "kgn.ingest.service.IngestService.ingest_text",
            side_effect=KgnError(
                KgnErrorCode.VALIDATION_FAILED,
                "edge validation error",
            ),
        ):
            result = _call_tool(
                server,
                "ingest_edge",
                kge_content=_make_kge_content(str(uuid.uuid4()), str(uuid.uuid4()), project_name),
            )

        data = json.loads(result)
        assert "error" in data
        assert data["code"] == KgnErrorCode.VALIDATION_FAILED.value

    def test_ingest_edge_generic_exception(self, db_conn, repo) -> None:
        """Generic Exception → returns INVALID_KGE_FORMAT error JSON."""
        project_name = f"mcp-err-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        with patch(
            "kgn.ingest.service.IngestService.ingest_text",
            side_effect=TypeError("edge type boom"),
        ):
            result = _call_tool(
                server,
                "ingest_edge",
                kge_content=_make_kge_content(str(uuid.uuid4()), str(uuid.uuid4()), project_name),
            )

        data = json.loads(result)
        assert "error" in data
        assert data["code"] == KgnErrorCode.INVALID_KGE_FORMAT.value
        assert "edge type boom" in data["detail"]

    def test_ingest_edge_failed_result(self, db_conn, repo) -> None:
        """ingest_text returns result with failed > 0 → VALIDATION_FAILED."""
        project_name = f"mcp-err-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        # Create nodes so the edge syntax is valid but use a method that
        # causes a failure in the ingest pipeline (missing node reference)
        n1 = NodeRecord(
            id=uuid.uuid4(),
            project_id=pid,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="N1",
            body_md="b",
            content_hash=uuid.uuid4().hex,
        )
        repo.upsert_node(n1)
        # Point to a nonexistent 'to' node
        missing_to = str(uuid.uuid4())

        content = _make_kge_content(str(n1.id), missing_to, project_name)
        result = _call_tool(server, "ingest_edge", kge_content=content)

        data = json.loads(result)
        assert "error" in data


# ── enqueue_task exception handlers ───────────────────────────────────


class TestEnqueueTaskExceptions:
    """Cover except KgnError/Exception in enqueue_task (lines 184, 186)
    and BLOCKED state response (lines 205-214)."""

    def test_enqueue_task_kgn_error(self, db_conn, repo) -> None:
        """KgnError raised during enqueue → safe_tool_call catches."""
        project_name = f"mcp-err-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        node = _make_task_node(pid, repo)

        with patch(
            "kgn.task.service.TaskService.enqueue",
            side_effect=KgnError(
                KgnErrorCode.TASK_NODE_INVALID,
                "mocked kgn error for enqueue",
            ),
        ):
            result = _call_tool(server, "enqueue_task", task_node_id=str(node.id))

        data = json.loads(result)
        assert "error" in data
        assert data["code"] == KgnErrorCode.TASK_NODE_INVALID.value

    def test_enqueue_task_generic_exception(self, db_conn, repo) -> None:
        """Generic Exception during enqueue → TASK_NODE_INVALID error."""
        project_name = f"mcp-err-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        node = _make_task_node(pid, repo)

        with patch(
            "kgn.task.service.TaskService.enqueue",
            side_effect=ValueError("unexpected enqueue failure"),
        ):
            result = _call_tool(server, "enqueue_task", task_node_id=str(node.id))

        data = json.loads(result)
        assert "error" in data
        assert data["code"] == KgnErrorCode.TASK_NODE_INVALID.value

    def test_enqueue_task_blocked_state(self, db_conn, repo) -> None:
        """Task with unsatisfied DEPENDS_ON → BLOCKED state with blocking_tasks."""
        project_name = f"mcp-blk-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        # Task A (dependency — NOT completed)
        task_a = _make_task_node(pid, repo)

        # Task B depends on Task A
        task_b = NodeRecord(
            id=uuid.uuid4(),
            project_id=pid,
            type=NodeType.TASK,
            status=NodeStatus.ACTIVE,
            title="Dependent Task B",
            body_md="depends on A",
            content_hash=uuid.uuid4().hex,
        )
        repo.upsert_node(task_b)

        # Create DEPENDS_ON edge: B → A
        from kgn.models.edge import EdgeRecord

        edge = EdgeRecord(
            from_node_id=task_b.id,
            to_node_id=task_a.id,
            type="DEPENDS_ON",
            project_id=pid,
            note="B depends on A",
        )
        repo.insert_edge(edge)

        result = _call_tool(server, "enqueue_task", task_node_id=str(task_b.id))
        data = json.loads(result)

        assert data["status"] == "BLOCKED"
        assert "blocking_tasks" in data
        assert len(data["blocking_tasks"]) >= 1
        assert "message" in data
        assert "BLOCKED" in data["message"]
