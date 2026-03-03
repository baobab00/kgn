"""MCP read-only tools — get_node, query_nodes, get_subgraph, query_similar."""

from __future__ import annotations

import json
import time
from dataclasses import asdict

import structlog
from mcp.server.fastmcp import FastMCP

from kgn.db.repository import KgnRepository
from kgn.errors import KgnErrorCode
from kgn.graph.subgraph import SubgraphService
from kgn.mcp._helpers import (
    _error_json,
    _node_to_dict,
    _parse_node_status,
    _parse_node_type,
    _parse_uuid,
    _subgraph_node_to_dict,
    safe_tool_call,
)

log = structlog.get_logger("kgn.mcp.read")


def register_read_tools(server: FastMCP) -> None:
    """Register read-only MCP tools on the server."""

    @server.tool(
        name="get_node",
        description="Get a node by ID. Returns error JSON if not found.",
    )
    @safe_tool_call
    def get_node(node_id: str) -> str:
        t0 = time.monotonic()
        log.info("tool_called", tool="get_node", node_id=node_id)

        nid = _parse_uuid(node_id)
        if nid is None:
            return _error_json(f"Invalid UUID: {node_id}", KgnErrorCode.INVALID_UUID)

        with server._kgn_conn_factory() as c:  # type: ignore[attr-defined]
            repo = KgnRepository(c)
            node = repo.get_node_by_id(nid)

        if node is None:
            return _error_json(f"Node not found: {node_id}", KgnErrorCode.NODE_NOT_FOUND)

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        log.info("tool_completed", tool="get_node", node_id=node_id, duration_ms=elapsed_ms)
        return json.dumps(_node_to_dict(node), ensure_ascii=False, indent=2)

    @server.tool(
        name="query_nodes",
        description="Search nodes in a project. Optional type/status filters.",
    )
    @safe_tool_call
    def query_nodes(project: str, type: str = "", status: str = "") -> str:  # noqa: A002
        t0 = time.monotonic()
        log.info("tool_called", tool="query_nodes", project=project)

        with server._kgn_conn_factory() as c:  # type: ignore[attr-defined]
            repo = KgnRepository(c)
            pid = repo.get_project_by_name(project)
            if pid is None:
                return _error_json(f"Project not found: {project}", KgnErrorCode.PROJECT_NOT_FOUND)

            node_type = _parse_node_type(type) if type else None
            node_status = _parse_node_status(status) if status else None

            if type and node_type is None:
                return _error_json(f"Invalid node type: {type}", KgnErrorCode.INVALID_NODE_TYPE)
            if status and node_status is None:
                return _error_json(
                    f"Invalid node status: {status}", KgnErrorCode.INVALID_NODE_STATUS
                )

            nodes = repo.search_nodes(pid, node_type=node_type, status=node_status)

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        log.info(
            "tool_completed",
            tool="query_nodes",
            count=len(nodes),
            duration_ms=elapsed_ms,
        )
        return json.dumps(
            [_node_to_dict(n) for n in nodes],
            ensure_ascii=False,
            indent=2,
        )

    @server.tool(
        name="get_subgraph",
        description="Extract subgraph from a node via BFS. Adjustable depth.",
    )
    @safe_tool_call
    def get_subgraph(node_id: str, depth: int = 2) -> str:
        t0 = time.monotonic()
        log.info("tool_called", tool="get_subgraph", node_id=node_id, depth=depth)

        nid = _parse_uuid(node_id)
        if nid is None:
            return _error_json(f"Invalid UUID: {node_id}", KgnErrorCode.INVALID_UUID)

        with server._kgn_conn_factory() as c:  # type: ignore[attr-defined]
            repo = KgnRepository(c)
            node = repo.get_node_by_id(nid)
            if node is None:
                return _error_json(f"Node not found: {node_id}", KgnErrorCode.NODE_NOT_FOUND)

            svc = SubgraphService(repo)
            result = svc.extract(
                root_id=nid,
                project_id=node.project_id,
                depth=depth,
            )

        data = {
            "root_id": result.root_id,
            "depth": result.depth,
            "nodes": [_subgraph_node_to_dict(n) for n in result.nodes],
            "edges": [asdict(e) for e in result.edges],
        }
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        log.info(
            "tool_completed",
            tool="get_subgraph",
            node_count=len(result.nodes),
            edge_count=len(result.edges),
            duration_ms=elapsed_ms,
        )
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)

    @server.tool(
        name="query_similar",
        description=(
            "Search for similar nodes by vector similarity. Returns empty list if no embeddings."
        ),
    )
    @safe_tool_call
    def query_similar(node_id: str, top_k: int = 5) -> str:
        t0 = time.monotonic()
        log.info("tool_called", tool="query_similar", node_id=node_id, top_k=top_k)

        nid = _parse_uuid(node_id)
        if nid is None:
            return _error_json(f"Invalid UUID: {node_id}", KgnErrorCode.INVALID_UUID)

        with server._kgn_conn_factory() as c:  # type: ignore[attr-defined]
            repo = KgnRepository(c)
            node = repo.get_node_by_id(nid)
            if node is None:
                return _error_json(f"Node not found: {node_id}", KgnErrorCode.NODE_NOT_FOUND)

            embedding = repo.get_node_embedding(nid)
            if embedding is None:
                return json.dumps([], ensure_ascii=False)

            similars = repo.search_similar_nodes(
                query_embedding=embedding,
                project_id=node.project_id,
                top_k=top_k,
                exclude_ids={nid},
            )

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        log.info(
            "tool_completed",
            tool="query_similar",
            count=len(similars),
            duration_ms=elapsed_ms,
        )
        return json.dumps(
            [
                {
                    "id": str(s.id),
                    "type": s.type,
                    "title": s.title,
                    "similarity": round(s.similarity, 4),
                }
                for s in similars
            ],
            ensure_ascii=False,
            indent=2,
        )
