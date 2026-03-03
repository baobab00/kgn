"""KGN Web — Stats & SSE routes.

GET /api/v1/stats  — Aggregated project statistics + Health Index.
GET /api/v1/events — Server-Sent Events stream (infrastructure for Phase 10).

All business logic delegates to existing services (R3/R12).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from kgn.db.connection import get_connection
from kgn.db.repository import KgnRepository
from kgn.graph.health import HealthService
from kgn.web.events import event_bus

router = APIRouter(tags=["stats"])


def _compute_health_index(
    orphan_count: int,
    conflict_count: int,
    total_nodes: int,
    total_edges: int,
) -> float:
    """Health Index = 1 - (orphans + conflicts) / total_elements.

    Range: 0.0 (very bad) to 1.0 (perfect).
    Empty graph returns 1.0.
    """
    total = total_nodes + total_edges
    if total == 0:
        return 1.0
    problems = orphan_count + conflict_count
    return round(max(0.0, 1.0 - problems / total), 4)


@router.get("/stats")
async def get_stats(request: Request) -> dict[str, Any]:
    """Return aggregated project statistics with Health Index."""
    project_id = request.app.state.project_id

    with get_connection() as conn:
        repo = KgnRepository(conn)

        # Node/edge breakdowns
        node_counts = repo.count_nodes(project_id)
        edge_counts = repo.count_edges(project_id)

        total_nodes = sum(node_counts.values())
        total_edges = sum(edge_counts.values())

        # Health metrics via HealthService
        svc = HealthService(repo)
        report = svc.compute(project_id)

        # Task pipeline counts
        tasks = repo.list_tasks(project_id)
        task_pipeline: dict[str, int] = {}
        for task in tasks:
            task_pipeline[task.state] = task_pipeline.get(task.state, 0) + 1

    health_index = _compute_health_index(
        orphan_count=report.orphan_active,
        conflict_count=report.conflict_count,
        total_nodes=total_nodes,
        total_edges=total_edges,
    )

    return {
        "project": str(project_id),
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "active_nodes": report.active_nodes,
        "node_types": node_counts,
        "edge_types": edge_counts,
        "health_index": health_index,
        "health_metrics": {
            "orphan_rate": round(report.orphan_rate, 4),
            "orphan_count": report.orphan_active,
            "conflict_count": report.conflict_count,
            "wip_tasks": report.wip_tasks,
            "superseded_stale": report.superseded_stale,
            "dup_spec_rate": round(report.dup_spec_rate, 4),
            "open_assumptions": report.open_assumptions,
        },
        "task_pipeline": task_pipeline,
    }


@router.get("/events")
async def sse_events(request: Request) -> StreamingResponse:
    """Server-Sent Events stream.

    Phase 9: infrastructure only — events are published in Phase 10.
    Clients can connect and will receive events as they are published
    to the global EventBus.
    """

    async def _generate():
        async for event in event_bus.subscribe():
            data = json.dumps(event["data"])
            yield f"event: {event['type']}\ndata: {data}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
