"""Subgraph handler for the ``kgn/subgraph`` custom LSP request.

Builds a JSON-serialisable subgraph centred on a given node ID,
using the ``WorkspaceIndexer`` as the primary data source (R22).
Enforces a ``max_nodes`` cap (default 50) to protect the editor
from excessively large graph renders (R-106).

Design rules
~~~~~~~~~~~~
R22  DB-free operation — ``WorkspaceIndexer`` only.
R24  Never throw — the ``build_response`` function is defensive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kgn.lsp.indexer import WorkspaceIndexer

# ── Constants ──────────────────────────────────────────────────────────

DEFAULT_DEPTH: int = 2
"""Default BFS traversal depth."""

DEFAULT_MAX_NODES: int = 50
"""Maximum number of nodes returned in a single response."""


# ── Node colour mapping ───────────────────────────────────────────────

NODE_TYPE_COLOURS: dict[str, str] = {
    "GOAL": "#4A90D9",
    "SPEC": "#27AE60",
    "ARCH": "#8E44AD",
    "LOGIC": "#E67E22",
    "DECISION": "#E74C3C",
    "ISSUE": "#F39C12",
    "TASK": "#3498DB",
    "CONSTRAINT": "#95A5A6",
    "ASSUMPTION": "#1ABC9C",
    "SUMMARY": "#34495E",
}
"""Colour hex codes for each NodeType, used by the webview."""


# ── Public API ─────────────────────────────────────────────────────────


def build_response(
    node_id: str,
    indexer: WorkspaceIndexer,
    *,
    depth: int = DEFAULT_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict[str, Any]:
    """Build a JSON-serialisable subgraph centred on *node_id*.

    Parameters:
        node_id: Centre node identifier (UUID or ``new:slug``).
        indexer: Workspace indexer instance.
        depth: BFS traversal depth (default 2).
        max_nodes: Maximum nodes to include (default 50).

    Returns:
        Dictionary with ``nodes``, ``edges``, and ``centre`` keys.
        ``nodes`` is a list of dicts, ``edges`` is a list of dicts.
        Returns an empty graph if *node_id* is not found.
    """
    if not node_id:
        return _empty_response(node_id)

    graph = indexer.build_local_subgraph(node_id, depth=depth)

    if not graph.nodes:
        return _empty_response(node_id)

    # ── Build node list (respect max_nodes) ───────────────────────
    node_list: list[dict[str, Any]] = []
    for nid, meta in graph.nodes.items():
        if len(node_list) >= max_nodes:
            break
        node_list.append(
            {
                "id": nid,
                "type": meta.type.name,
                "title": meta.title,
                "status": meta.status.name,
                "slug": meta.slug,
                "colour": NODE_TYPE_COLOURS.get(meta.type.name, "#7F8C8D"),
                "path": str(meta.path),
            },
        )

    # Collect the set of included node IDs for edge filtering
    included_ids = {n["id"] for n in node_list}

    # ── Build edge list (only edges between included nodes) ───────
    edge_list: list[dict[str, str]] = []
    for edge in graph.edges:
        if edge.from_node in included_ids and edge.to in included_ids:
            edge_list.append(
                {
                    "from": edge.from_node,
                    "to": edge.to,
                    "type": edge.type if isinstance(edge.type, str) else edge.type.name,
                },
            )

    return {
        "centre": node_id,
        "nodes": node_list,
        "edges": edge_list,
        "truncated": len(graph.nodes) > max_nodes,
    }


def _empty_response(node_id: str) -> dict[str, Any]:
    """Return a minimal empty-graph response."""
    return {
        "centre": node_id,
        "nodes": [],
        "edges": [],
        "truncated": False,
    }
