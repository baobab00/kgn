"""Tests for MCP write tools (Phase 4 Step 5).

ingest_node, ingest_edge, enqueue_task — 3 tool verification.
SAVEPOINT transaction connection injection for DB integration tests.
"""

from __future__ import annotations

import json
import uuid

from kgn.mcp.server import create_server
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


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


def _make_kgn_content(
    *,
    node_id: str = "new:mcp-test",
    node_type: str = "SPEC",
    title: str = "MCP Test Node",
    project_id: str = "test-project",
    agent_id: str = "mcp",
    body: str = "## Content\n\nTest body.",
) -> str:
    front = (
        "---\n"
        f'kgn_version: "0.1"\n'
        f'id: "{node_id}"\n'
        f"type: {node_type}\n"
        f'title: "{title}"\n'
        "status: ACTIVE\n"
        f'project_id: "{project_id}"\n'
        f'agent_id: "{agent_id}"\n'
        'created_at: "2026-03-01T00:00:00+09:00"\n'
        'tags: ["test"]\n'
        "confidence: 0.9\n"
        "---\n"
    )
    return front + "\n" + body + "\n"


def _make_kge_content(
    *,
    from_id: str,
    to_id: str,
    edge_type: str = "DEPENDS_ON",
    project_id: str = "test-project",
    agent_id: str = "mcp",
) -> str:
    return (
        "---\n"
        'kgn_version: "0.1"\n'
        f'project_id: "{project_id}"\n'
        f'agent_id: "{agent_id}"\n'
        'created_at: "2026-03-01T00:00:00+09:00"\n'
        "edges:\n"
        f'  - from: "{from_id}"\n'
        f'    to: "{to_id}"\n'
        f"    type: {edge_type}\n"
        '    note: "test edge"\n'
        "---\n"
    )


def _make_task_node(project_id: uuid.UUID, repo) -> NodeRecord:
    """Create and upsert a TASK node, return the record."""
    node = NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.TASK,
        status=NodeStatus.ACTIVE,
        title="Task for enqueue test",
        body_md="do something",
        content_hash=uuid.uuid4().hex,
    )
    repo.upsert_node(node)
    return node


# ── ingest_node ────────────────────────────────────────────────────────


class TestIngestNode:
    def test_ingest_new_node(self, db_conn, repo) -> None:
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        content = _make_kgn_content(project_id=project_name)
        result = _call_tool(server, "ingest_node", kgn_content=content)
        data = json.loads(result)

        assert data["status"] == "ok"
        assert "node_id" in data
        # node_id should be a valid UUID
        uuid.UUID(data["node_id"])

    def test_ingest_with_explicit_uuid(self, db_conn, repo) -> None:
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        node_id = str(uuid.uuid4())
        content = _make_kgn_content(
            node_id=node_id,
            project_id=project_name,
        )
        result = _call_tool(server, "ingest_node", kgn_content=content)
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["node_id"] == node_id

    def test_ingest_invalid_kgn(self, db_conn, repo) -> None:
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        result = _call_tool(server, "ingest_node", kgn_content="not valid kgn")
        data = json.loads(result)

        assert "error" in data

    def test_ingest_node_upsert(self, db_conn, repo) -> None:
        """Ingesting same ID twice → upsert (success)."""
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        node_id = str(uuid.uuid4())
        content = _make_kgn_content(
            node_id=node_id,
            title="Version 1",
            project_id=project_name,
        )
        r1 = json.loads(_call_tool(server, "ingest_node", kgn_content=content))
        assert r1["status"] == "ok"

        content2 = _make_kgn_content(
            node_id=node_id,
            title="Version 2",
            project_id=project_name,
            body="## Content\n\nUpdated body.",
        )
        r2 = json.loads(_call_tool(server, "ingest_node", kgn_content=content2))
        assert r2["status"] == "ok"
        assert r2["node_id"] == node_id

    def test_ingest_node_enforce_project(self, db_conn, repo) -> None:
        """MCP ingest_node must record to server-bound project even if .kgn has a different project_id."""
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        correct_pid = repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        # Intentionally put wrong project_id in .kgn text
        wrong_project = "wrong-project-name"
        content = _make_kgn_content(project_id=wrong_project)
        result = _call_tool(server, "ingest_node", kgn_content=content)
        data = json.loads(result)

        assert data["status"] == "ok"
        node_id = uuid.UUID(data["node_id"])

        # Verify node was recorded in the correct project
        node = repo.get_node_by_id(node_id)
        assert node is not None
        assert node.project_id == correct_pid

        # Verify wrong project was not created
        wrong_pid = repo.get_project_by_name(wrong_project)
        assert wrong_pid is None


# ── ingest_edge ────────────────────────────────────────────────────────


class TestIngestEdge:
    def test_ingest_edge_success(self, db_conn, repo) -> None:
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        # Create two nodes for edge endpoints
        n1 = NodeRecord(
            id=uuid.uuid4(),
            project_id=pid,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="N1",
            body_md="b",
            content_hash=uuid.uuid4().hex,
        )
        n2 = NodeRecord(
            id=uuid.uuid4(),
            project_id=pid,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="N2",
            body_md="b",
            content_hash=uuid.uuid4().hex,
        )
        repo.upsert_node(n1)
        repo.upsert_node(n2)

        content = _make_kge_content(
            from_id=str(n1.id),
            to_id=str(n2.id),
            project_id=project_name,
        )
        result = _call_tool(server, "ingest_edge", kge_content=content)
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["edge_count"] == 1

    def test_ingest_edge_invalid_kge(self, db_conn, repo) -> None:
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        result = _call_tool(server, "ingest_edge", kge_content="bad content")
        data = json.loads(result)

        assert "error" in data

    def test_ingest_edge_bad_ref(self, db_conn, repo) -> None:
        """Non-existent node reference → error."""
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        content = _make_kge_content(
            from_id=str(uuid.uuid4()),
            to_id=str(uuid.uuid4()),
            project_id=project_name,
        )
        result = _call_tool(server, "ingest_edge", kge_content=content)
        data = json.loads(result)

        assert "error" in data

    def test_ingest_edge_enforce_project(self, db_conn, repo) -> None:
        """MCP ingest_edge must record to server-bound project even if .kge has a different project_id."""
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        # Create two nodes
        n1 = NodeRecord(
            id=uuid.uuid4(),
            project_id=pid,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="N1",
            body_md="b",
            content_hash=uuid.uuid4().hex,
        )
        n2 = NodeRecord(
            id=uuid.uuid4(),
            project_id=pid,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="N2",
            body_md="b",
            content_hash=uuid.uuid4().hex,
        )
        repo.upsert_node(n1)
        repo.upsert_node(n2)

        # Intentionally put wrong project_id in .kge text
        wrong_project = "wrong-edge-project"
        content = _make_kge_content(
            from_id=str(n1.id),
            to_id=str(n2.id),
            project_id=wrong_project,
        )
        result = _call_tool(server, "ingest_edge", kge_content=content)
        data = json.loads(result)

        assert data["status"] == "ok"

        # Verify wrong project was not created
        wrong_pid = repo.get_project_by_name(wrong_project)
        assert wrong_pid is None


# ── enqueue_task ───────────────────────────────────────────────────────


class TestEnqueueTask:
    def test_enqueue_success(self, db_conn, repo) -> None:
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        node = _make_task_node(pid, repo)
        result = _call_tool(server, "enqueue_task", task_node_id=str(node.id))
        data = json.loads(result)

        assert data["status"] == "READY"
        assert "task_queue_id" in data
        uuid.UUID(data["task_queue_id"])

    def test_enqueue_with_priority(self, db_conn, repo) -> None:
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        node = _make_task_node(pid, repo)
        result = _call_tool(server, "enqueue_task", task_node_id=str(node.id), priority=50)
        data = json.loads(result)

        assert data["status"] == "READY"

    def test_enqueue_invalid_uuid(self, db_conn, repo) -> None:
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        result = _call_tool(server, "enqueue_task", task_node_id="not-a-uuid")
        data = json.loads(result)

        assert "error" in data
        assert "Invalid UUID" in data["error"]

    def test_enqueue_nonexistent_node(self, db_conn, repo) -> None:
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        result = _call_tool(server, "enqueue_task", task_node_id=str(uuid.uuid4()))
        data = json.loads(result)

        assert "error" in data

    def test_enqueue_non_task_node(self, db_conn, repo) -> None:
        """Enqueuing a SPEC type node → error."""
        project_name = f"mcp-write-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        spec_node = NodeRecord(
            id=uuid.uuid4(),
            project_id=pid,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="Not a task",
            body_md="body",
            content_hash=uuid.uuid4().hex,
        )
        repo.upsert_node(spec_node)

        result = _call_tool(server, "enqueue_task", task_node_id=str(spec_node.id))
        data = json.loads(result)

        assert "error" in data


# ── Role Fallback ──────────────────────────────────────────────────────


class TestRoleFallbackToIndexer:
    """Unrecognized role should fall back to INDEXER, not ADMIN (Phase 12 / Step 5)."""

    def test_unknown_role_denied_node_create(self, db_conn, repo, monkeypatch) -> None:
        """An agent with an unrecognized role should be denied SPEC node creation."""
        from kgn.db.repository import KgnRepository

        project_name = f"mcp-role-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        # Monkeypatch get_agent_role to return a value not in AgentRole enum
        _original = KgnRepository.get_agent_role
        monkeypatch.setattr(
            KgnRepository,
            "get_agent_role",
            lambda self, aid: "future_role_xyz",
        )

        # Try to ingest a SPEC node — unknown role falls back to INDEXER,
        # which cannot create SPEC nodes → expect KGN-550
        content = _make_kgn_content(
            node_type="SPEC",
            project_id=project_name,
        )
        result = _call_tool(server, "ingest_node", kgn_content=content)
        data = json.loads(result)

        assert "error" in data
        assert data.get("code") == "KGN-550"
