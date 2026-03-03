"""KGN Web — Search & similarity API routes.

GET /api/v1/similar/{id}  — Cosine-similar nodes (requires embeddings).
GET /api/v1/conflicts     — Conflict candidates via ConflictService.

All business logic is delegated to existing services (R3/R12).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from kgn.conflict.service import ConflictService
from kgn.db.connection import get_connection
from kgn.db.repository import KgnRepository

router = APIRouter(tags=["search"])


@router.get("/similar/{node_id}")
async def similar_nodes(
    request: Request,
    node_id: str,
    top_k: int = Query(5, ge=1, le=50, description="Number of similar nodes"),
) -> dict:
    """Return top-K similar nodes by cosine similarity.

    Requires the target node to have an embedding vector stored
    (via ``kgn embed``).  Returns an empty list if no embedding exists.
    """
    try:
        nid = uuid.UUID(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {node_id}") from exc

    with get_connection() as conn:
        repo = KgnRepository(conn)

        node = repo.get_node_by_id(nid)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

        embedding = repo.get_node_embedding(nid)
        if embedding is None:
            return {"node_id": str(nid), "results": [], "total": 0}

        similars = repo.search_similar_nodes(
            query_embedding=embedding,
            project_id=node.project_id,
            top_k=top_k,
            exclude_ids={nid},
        )

    results: list[dict[str, Any]] = [
        {
            "id": str(s.id),
            "type": s.type,
            "title": s.title,
            "similarity": round(s.similarity, 4),
        }
        for s in similars
    ]

    return {"node_id": str(nid), "results": results, "total": len(results)}


@router.get("/conflicts")
async def list_conflicts(
    request: Request,
    threshold: float = Query(0.85, ge=0.0, le=1.0, description="Similarity threshold"),
) -> dict:
    """Return conflict candidate pairs from ConflictService.scan()."""
    project_id: uuid.UUID = request.app.state.project_id

    with get_connection() as conn:
        repo = KgnRepository(conn)
        service = ConflictService(repo)
        candidates = service.scan(project_id, threshold=threshold)

    results: list[dict[str, Any]] = [
        {
            "node_a_id": str(c.node_a_id),
            "node_a_title": c.node_a_title,
            "node_b_id": str(c.node_b_id),
            "node_b_title": c.node_b_title,
            "similarity": round(c.similarity, 4),
            "status": c.status,
        }
        for c in candidates
    ]

    return {
        "conflicts": results,
        "total": len(results),
        "threshold": threshold,
        "project": str(project_id),
    }
