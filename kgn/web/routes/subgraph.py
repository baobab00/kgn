"""KGN Web — Subgraph API route.

GET /api/v1/subgraph/{node_id} — Extract k-hop subgraph as Cytoscape.js elements.

Delegates to SubgraphService for BFS traversal (R3/R12).
Returns Cytoscape.js-compatible ``{ nodes: [...], edges: [...] }`` format
for direct consumption by the graph.js frontend.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request

from kgn.db.connection import get_connection
from kgn.db.repository import KgnRepository
from kgn.graph.subgraph import SubgraphService

router = APIRouter(tags=["subgraph"])

# R15: max rendered nodes to prevent browser freeze
_MAX_NODES = 200


@router.get("/subgraph/{node_id}")
async def get_subgraph(
    request: Request,
    node_id: str,
    depth: int = Query(2, ge=1, le=5, description="BFS hop depth (1-5)"),
) -> dict:
    """Extract a k-hop subgraph centred on *node_id*.

    Returns Cytoscape.js-compatible elements with node/edge styling hints.
    """
    try:
        root_id = uuid.UUID(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {node_id}") from exc

    project_id: uuid.UUID = request.app.state.project_id

    with get_connection() as conn:
        repo = KgnRepository(conn)

        # Verify root node exists
        root = repo.get_node_by_id(root_id)
        if root is None:
            raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

        svc = SubgraphService(repo)
        result = svc.extract(
            root_id=root_id,
            project_id=project_id,
            depth=depth,
            include_archived=False,
        )

    truncated = len(result.nodes) > _MAX_NODES
    nodes_to_render = result.nodes[:_MAX_NODES] if truncated else result.nodes

    # Build set of rendered node IDs for edge filtering
    rendered_ids = {str(n.id) for n in nodes_to_render}

    cy_nodes = [
        {
            "data": {
                "id": str(n.id),
                "label": n.title,
                "type": n.type,
                "status": n.status,
                "depth": n.depth,
                "tags": n.tags,
            },
        }
        for n in nodes_to_render
    ]

    cy_edges = [
        {
            "data": {
                "id": f"{e.from_id}-{e.type}-{e.to_id}",
                "source": e.from_id,
                "target": e.to_id,
                "label": e.type,
                "note": e.note,
            },
        }
        for e in result.edges
        if e.from_id in rendered_ids and e.to_id in rendered_ids
    ]

    return {
        "root_id": str(root_id),
        "depth": depth,
        "total_nodes": len(result.nodes),
        "rendered_nodes": len(cy_nodes),
        "truncated": truncated,
        "elements": {
            "nodes": cy_nodes,
            "edges": cy_edges,
        },
    }
