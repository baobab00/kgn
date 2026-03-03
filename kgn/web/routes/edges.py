"""KGN Web — Edges API route.

GET /api/v1/edges?node_id={id} — List incoming/outgoing edges for a node.

Delegates to KgnRepository for edge queries (R3/R12).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from kgn.db.connection import get_connection
from kgn.db.repository import KgnRepository
from kgn.models.edge import EdgeRecord

router = APIRouter(tags=["edges"])


def _edge_to_dict(edge: EdgeRecord, peer_title: str | None = None) -> dict[str, Any]:
    """Convert an EdgeRecord to a JSON-serializable dict."""
    d: dict[str, Any] = {
        "id": edge.id,
        "from_node_id": str(edge.from_node_id),
        "to_node_id": str(edge.to_node_id),
        "type": edge.type.value if hasattr(edge.type, "value") else str(edge.type),
        "note": edge.note or "",
        "created_at": edge.created_at.isoformat() if edge.created_at else None,
    }
    if peer_title is not None:
        d["peer_title"] = peer_title
    return d


@router.get("/edges")
async def list_edges(
    request: Request,
    node_id: str = Query(..., description="UUID of the node to get edges for"),
) -> dict:
    """List incoming and outgoing edges for a given node.

    Returns ``{ incoming: [...], outgoing: [...] }`` with peer node titles
    for convenient display in the detail panel.
    """
    try:
        nid = uuid.UUID(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {node_id}") from exc

    with get_connection() as conn:
        repo = KgnRepository(conn)

        # Verify node exists
        node = repo.get_node_by_id(nid)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

        incoming = repo.get_edges_to(nid)
        outgoing = repo.get_edges_from(nid)

        # Resolve peer titles for incoming (peer = from_node) and outgoing (peer = to_node)
        peer_ids = {e.from_node_id for e in incoming} | {e.to_node_id for e in outgoing}
        peer_titles: dict[uuid.UUID, str] = {}
        for pid in peer_ids:
            peer = repo.get_node_by_id(pid)
            if peer is not None:
                peer_titles[pid] = peer.title

    incoming_dicts = [
        _edge_to_dict(e, peer_title=peer_titles.get(e.from_node_id)) for e in incoming
    ]
    outgoing_dicts = [_edge_to_dict(e, peer_title=peer_titles.get(e.to_node_id)) for e in outgoing]

    return {
        "node_id": str(nid),
        "incoming": incoming_dicts,
        "outgoing": outgoing_dicts,
        "total": len(incoming_dicts) + len(outgoing_dicts),
    }
