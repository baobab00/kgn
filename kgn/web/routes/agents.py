"""KGN Web — Agents API routes.

GET /api/v1/agents                   — Agent list with roles + stats.
GET /api/v1/agents/{agent_id}/timeline — Activity timeline for an agent.
GET /api/v1/agents/{agent_id}/stats  — Individual agent statistics.
GET /api/v1/workflow/flow            — Task flow data (DAG + durations).
GET /api/v1/workflow/bottlenecks     — Bottleneck tasks.

All business logic is delegated to ObservabilityService (R3/R12).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from kgn.db.connection import get_connection
from kgn.db.repository import KgnRepository
from kgn.orchestration.observability import ObservabilityService

router = APIRouter(tags=["agents"])


# ── Helpers ────────────────────────────────────────────────────────────


def _agent_stats_to_dict(s: Any) -> dict[str, Any]:
    return {
        "agent_id": str(s.agent_id),
        "agent_key": s.agent_key,
        "role": s.role,
        "total_tasks": s.total_tasks,
        "done_count": s.done_count,
        "failed_count": s.failed_count,
        "avg_duration_sec": round(s.avg_duration_sec, 2),
        "success_rate": round(s.success_rate, 2),
    }


def _timeline_to_dict(e: Any) -> dict[str, Any]:
    return {
        "id": e.id,
        "agent_id": str(e.agent_id),
        "agent_key": e.agent_key,
        "agent_role": e.agent_role,
        "activity_type": e.activity_type,
        "target_node_id": str(e.target_node_id) if e.target_node_id else None,
        "message": e.message,
        "task_queue_id": str(e.task_queue_id) if e.task_queue_id else None,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def _flow_to_dict(f: Any) -> dict[str, Any]:
    return {
        "task_queue_id": str(f.task_queue_id),
        "task_node_id": str(f.task_node_id),
        "task_title": f.task_title,
        "state": f.state,
        "priority": f.priority,
        "leased_by_key": f.leased_by_key,
        "created_at": f.created_at.isoformat() if f.created_at else None,
        "updated_at": f.updated_at.isoformat() if f.updated_at else None,
        "duration_sec": round(f.duration_sec, 2) if f.duration_sec is not None else None,
    }


def _bottleneck_to_dict(b: Any) -> dict[str, Any]:
    return {
        "task_queue_id": str(b.task_queue_id),
        "task_node_id": str(b.task_node_id),
        "task_title": b.task_title,
        "state": b.state,
        "duration_sec": round(b.duration_sec, 2),
        "leased_by_key": b.leased_by_key,
        "priority": b.priority,
    }


# ── Routes ─────────────────────────────────────────────────────────────


@router.get("/agents")
async def list_agents(request: Request) -> dict[str, Any]:
    """List all agents with their roles and task statistics."""
    project_id: uuid.UUID = request.app.state.project_id

    with get_connection() as conn:
        repo = KgnRepository(conn)
        svc = ObservabilityService(repo)
        stats = svc.get_agent_stats(project_id)

    return {
        "agents": [_agent_stats_to_dict(s) for s in stats],
        "total": len(stats),
    }


@router.get("/agents/{agent_id}/timeline")
async def agent_timeline(
    request: Request,
    agent_id: str,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Get activity timeline for a specific agent."""
    project_id: uuid.UUID = request.app.state.project_id

    try:
        aid = uuid.UUID(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid agent UUID: {agent_id}") from exc

    with get_connection() as conn:
        repo = KgnRepository(conn)
        svc = ObservabilityService(repo)
        entries = svc.get_agent_timeline(project_id, aid, limit=limit)

    return {
        "agent_id": agent_id,
        "entries": [_timeline_to_dict(e) for e in entries],
        "total": len(entries),
    }


@router.get("/agents/{agent_id}/stats")
async def agent_stats(request: Request, agent_id: str) -> dict[str, Any]:
    """Get task statistics for a specific agent."""
    project_id: uuid.UUID = request.app.state.project_id

    try:
        aid = uuid.UUID(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid agent UUID: {agent_id}") from exc

    with get_connection() as conn:
        repo = KgnRepository(conn)
        svc = ObservabilityService(repo)
        all_stats = svc.get_agent_stats(project_id)

    matching = [s for s in all_stats if s.agent_id == aid]
    if not matching:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    return _agent_stats_to_dict(matching[0])


@router.get("/workflow/flow")
async def workflow_flow(request: Request) -> dict[str, Any]:
    """Get task flow data (completed/failed tasks with durations)."""
    project_id: uuid.UUID = request.app.state.project_id

    with get_connection() as conn:
        repo = KgnRepository(conn)
        svc = ObservabilityService(repo)
        entries = svc.get_task_flow(project_id)

    return {
        "tasks": [_flow_to_dict(f) for f in entries],
        "total": len(entries),
    }


@router.get("/workflow/bottlenecks")
async def workflow_bottlenecks(
    request: Request,
    percentile: float = Query(default=0.8, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """Identify bottleneck tasks above the given duration percentile."""
    project_id: uuid.UUID = request.app.state.project_id

    with get_connection() as conn:
        repo = KgnRepository(conn)
        svc = ObservabilityService(repo)
        bottlenecks = svc.detect_bottlenecks(project_id, percentile=percentile)

    return {
        "bottlenecks": [_bottleneck_to_dict(b) for b in bottlenecks],
        "total": len(bottlenecks),
        "percentile": percentile,
    }
