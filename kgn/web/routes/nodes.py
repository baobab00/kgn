"""KGN Web — Nodes API routes.

GET /api/v1/nodes      — List nodes with optional type/status filters.
GET /api/v1/nodes/{id} — Get a single node by UUID.

All business logic is delegated to KgnRepository (R3/R12).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from kgn.db.connection import get_connection
from kgn.db.repository import KgnRepository
from kgn.models.enums import NodeStatus, NodeType

router = APIRouter(tags=["nodes"])


def _node_to_dict(node: Any) -> dict:
    """Convert a NodeRecord to a JSON-serializable dict."""
    return {
        "id": str(node.id),
        "project_id": str(node.project_id),
        "type": node.type.value if hasattr(node.type, "value") else str(node.type),
        "status": node.status.value if hasattr(node.status, "value") else str(node.status),
        "title": node.title,
        "body_md": node.body_md,
        "file_path": node.file_path,
        "content_hash": node.content_hash,
        "tags": node.tags or [],
        "confidence": node.confidence,
        "created_by": str(node.created_by) if node.created_by else None,
        "created_at": node.created_at.isoformat() if node.created_at else None,
        "updated_at": node.updated_at.isoformat() if node.updated_at else None,
    }


@router.get("/nodes")
async def list_nodes(
    request: Request,
    node_type_filter: str | None = Query(
        None, alias="type", description="Node type filter (e.g. SPEC, GOAL)"
    ),
    status: str | None = Query(None, description="Node status filter (e.g. ACTIVE)"),
    tags: str | None = Query(None, description="Comma-separated tag filter (OR)"),
    q: str | None = Query(None, description="Text search in title"),
    limit: int = Query(200, ge=1, le=1000, description="Max nodes to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
) -> dict:
    """List nodes in the project with optional type/status/tags/text filters."""
    project_id: uuid.UUID = request.app.state.project_id

    node_type: NodeType | None = None
    node_status: NodeStatus | None = None

    if node_type_filter is not None:
        try:
            node_type = NodeType(node_type_filter.upper())
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid node type: {node_type_filter}. "
                f"Valid: {', '.join(t.value for t in NodeType)}",
            ) from exc

    if status is not None:
        try:
            node_status = NodeStatus(status.upper())
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: {status}. Valid: {', '.join(s.value for s in NodeStatus)}",
            ) from exc

    tag_list: list[str] | None = None
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    with get_connection() as conn:
        repo = KgnRepository(conn)
        nodes = repo.search_nodes(
            project_id,
            node_type=node_type,
            status=node_status,
            exclude_archived=node_status is None,
        )

    # Post-filter: tags (OR match) and text search
    result = nodes
    if tag_list:
        result = [n for n in result if n.tags and any(t in n.tags for t in tag_list)]
    if q:
        q_lower = q.lower()
        result = [n for n in result if q_lower in n.title.lower()]

    total = len(result)
    page = result[offset : offset + limit]

    return {
        "nodes": [_node_to_dict(n) for n in page],
        "total": total,
        "project": str(project_id),
    }


@router.get("/nodes/{node_id}")
async def get_node(request: Request, node_id: str) -> dict:
    """Get a single node by its UUID."""
    try:
        nid = uuid.UUID(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {node_id}") from exc

    with get_connection() as conn:
        repo = KgnRepository(conn)
        node = repo.get_node_by_id(nid)

    if node is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

    return _node_to_dict(node)
