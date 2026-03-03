"""Tests for MCP read-only tools (Phase 4 Step 3).

get_node, query_nodes, get_subgraph, query_similar — 4 tool verification.
SAVEPOINT transaction connection injection for DB integration tests.
"""

from __future__ import annotations

import json
import uuid

from kgn.mcp.server import create_server
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    *,
    node_type: NodeType = NodeType.SPEC,
    status: NodeStatus = NodeStatus.ACTIVE,
    title: str = "Test Node",
    body_md: str = "body",
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=status,
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
    # FastMCP.call_tool returns (content_list, structured_result) tuple
    content_list = raw[0] if isinstance(raw, tuple) else raw
    if content_list and hasattr(content_list[0], "text"):
        return content_list[0].text
    return str(content_list)


# ── get_node ───────────────────────────────────────────────────────────


class TestGetNode:
    def test_returns_node_json(self, db_conn, repo, project_id) -> None:
        node = _make_node(project_id, title="My Spec Node")
        repo.upsert_node(node)
        project_name = f"mcp-read-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        # Re-create with actual project
        node2 = _make_node(
            repo.get_project_by_name(project_name),
            title="Lookup Node",
        )
        repo.upsert_node(node2)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "get_node", node_id=str(node2.id))
        data = json.loads(result)

        assert data["id"] == str(node2.id)
        assert data["title"] == "Lookup Node"
        assert data["type"] == "SPEC"

    def test_not_found_returns_error(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-read-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "get_node", node_id=str(uuid.uuid4()))
        data = json.loads(result)

        assert "error" in data
        assert "not found" in data["error"].lower()

    def test_invalid_uuid_returns_error(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-read-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "get_node", node_id="not-a-uuid")
        data = json.loads(result)

        assert "error" in data
        assert "Invalid UUID" in data["error"]


# ── query_nodes ────────────────────────────────────────────────────────


class TestQueryNodes:
    def test_returns_all_nodes(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-qn-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        repo.upsert_node(_make_node(pid, title="Node A"))
        repo.upsert_node(_make_node(pid, title="Node B"))

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "query_nodes", project=project_name)
        data = json.loads(result)

        assert isinstance(data, list)
        assert len(data) == 2
        titles = {d["title"] for d in data}
        assert titles == {"Node A", "Node B"}

    def test_filter_by_type(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-qn-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        repo.upsert_node(_make_node(pid, node_type=NodeType.SPEC, title="Spec"))
        repo.upsert_node(_make_node(pid, node_type=NodeType.GOAL, title="Goal"))

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "query_nodes", project=project_name, type="GOAL")
        data = json.loads(result)

        assert len(data) == 1
        assert data[0]["title"] == "Goal"

    def test_filter_by_status(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-qn-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        repo.upsert_node(_make_node(pid, status=NodeStatus.ACTIVE, title="Active"))
        repo.upsert_node(_make_node(pid, status=NodeStatus.DEPRECATED, title="Deprecated"))

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "query_nodes", project=project_name, status="DEPRECATED")
        data = json.loads(result)

        assert len(data) == 1
        assert data[0]["title"] == "Deprecated"

    def test_invalid_type_returns_error(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-qn-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "query_nodes", project=project_name, type="BOGUS")
        data = json.loads(result)

        assert "error" in data
        assert "Invalid node type" in data["error"]

    def test_nonexistent_project_returns_error(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-qn-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "query_nodes", project="no-such-project")
        data = json.loads(result)

        assert "error" in data
        assert "not found" in data["error"].lower()

    def test_empty_type_status_returns_all(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-qn-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        repo.upsert_node(_make_node(pid, title="Only"))

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "query_nodes", project=project_name, type="", status="")
        data = json.loads(result)

        assert len(data) == 1


# ── get_subgraph ───────────────────────────────────────────────────────


class TestGetSubgraph:
    def test_returns_subgraph_json(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-sg-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        node = _make_node(pid, title="Root Node")
        repo.upsert_node(node)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "get_subgraph", node_id=str(node.id))
        data = json.loads(result)

        assert data["root_id"] == str(node.id)
        assert data["depth"] == 2
        assert isinstance(data["nodes"], list)
        assert isinstance(data["edges"], list)

    def test_custom_depth(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-sg-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        node = _make_node(pid, title="Deep Root")
        repo.upsert_node(node)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "get_subgraph", node_id=str(node.id), depth=1)
        data = json.loads(result)

        assert data["depth"] == 1

    def test_not_found_returns_error(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-sg-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "get_subgraph", node_id=str(uuid.uuid4()))
        data = json.loads(result)

        assert "error" in data

    def test_isolated_node_returns_empty_edges(self, db_conn, repo, project_id) -> None:
        """Isolated node (no edges) is handled correctly."""
        project_name = f"mcp-sg-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        node = _make_node(pid, title="Lonely")
        repo.upsert_node(node)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "get_subgraph", node_id=str(node.id))
        data = json.loads(result)

        assert data["edges"] == []

    def test_invalid_uuid_returns_error(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-sg-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "get_subgraph", node_id="bad-id")
        data = json.loads(result)

        assert "error" in data


# ── query_similar ──────────────────────────────────────────────────────


class TestQuerySimilar:
    def test_no_embedding_returns_empty_list(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-qs-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        node = _make_node(pid, title="No Embed")
        repo.upsert_node(node)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "query_similar", node_id=str(node.id))
        data = json.loads(result)

        assert data == []

    def test_not_found_returns_error(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-qs-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "query_similar", node_id=str(uuid.uuid4()))
        data = json.loads(result)

        assert "error" in data

    def test_invalid_uuid_returns_error(self, db_conn, repo, project_id) -> None:
        project_name = f"mcp-qs-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "query_similar", node_id="xxx")
        data = json.loads(result)

        assert "error" in data

    def test_custom_top_k(self, db_conn, repo, project_id) -> None:
        """Verify top_k parameter is passed (empty list since no embeddings)."""
        project_name = f"mcp-qs-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        node = _make_node(pid, title="TopK Test")
        repo.upsert_node(node)

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "query_similar", node_id=str(node.id), top_k=3)
        data = json.loads(result)

        assert data == []
