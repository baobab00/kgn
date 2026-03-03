"""KGN Web — Tasks API routes.

GET /api/v1/tasks              — List tasks (optionally filtered by state).
GET /api/v1/tasks/{task_id}    — Get a single task by queue UUID.
GET /api/v1/tasks/{task_id}/activities — Activity log for a task.

All business logic is delegated to KgnRepository (R3/R12).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from kgn.db.connection import get_connection
from kgn.db.repository import KgnRepository, TaskQueueItem

router = APIRouter(tags=["tasks"])

_VALID_STATES = {"READY", "IN_PROGRESS", "BLOCKED", "DONE", "FAILED"}


def _task_to_dict(task: TaskQueueItem) -> dict[str, Any]:
    """Convert a TaskQueueItem to a JSON-serializable dict."""
    return {
        "id": str(task.id),
        "project_id": str(task.project_id),
        "task_node_id": str(task.task_node_id),
        "priority": task.priority,
        "state": task.state,
        "leased_by": str(task.leased_by) if task.leased_by else None,
        "lease_expires_at": (task.lease_expires_at.isoformat() if task.lease_expires_at else None),
        "attempts": task.attempts,
        "max_attempts": task.max_attempts,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def _activity_to_dict(activity: dict) -> dict[str, Any]:
    """Convert an activity log dict to a JSON-serializable dict."""
    return {
        "activity_type": activity["activity_type"],
        "message": activity["message"],
        "agent_key": activity.get("agent_key"),
        "created_at": (activity["created_at"].isoformat() if activity.get("created_at") else None),
    }


@router.get("/tasks")
async def list_tasks(
    request: Request,
    state: str | None = Query(
        None,
        description="Filter by task state (READY, IN_PROGRESS, DONE, FAILED)",
    ),
) -> dict:
    """List tasks for the project, grouped by state for kanban rendering."""
    project_id: uuid.UUID = request.app.state.project_id

    filter_state: str | None = None
    if state is not None:
        upper = state.upper()
        if upper not in _VALID_STATES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid state: {state}. Valid: {', '.join(sorted(_VALID_STATES))}",
            )
        filter_state = upper

    with get_connection() as conn:
        repo = KgnRepository(conn)
        tasks = repo.list_tasks(project_id, state=filter_state)

        # Resolve task node titles for display
        node_ids = {t.task_node_id for t in tasks}
        titles: dict[uuid.UUID, str] = {}
        for nid in node_ids:
            node = repo.get_node_by_id(nid)
            if node is not None:
                titles[nid] = node.title

        # Resolve agent info for leased tasks
        agent_ids = {t.leased_by for t in tasks if t.leased_by}
        agent_info: dict[uuid.UUID, dict] = {}
        if agent_ids:
            agents = repo.list_agents(project_id)
            for a in agents:
                if a["id"] in agent_ids:
                    agent_info[a["id"]] = {
                        "agent_key": a["agent_key"],
                        "role": a.get("role", "worker"),
                    }

    task_dicts = []
    for t in tasks:
        d = _task_to_dict(t)
        d["title"] = titles.get(t.task_node_id, str(t.task_node_id)[:12] + "…")
        # Add agent details if leased
        if t.leased_by and t.leased_by in agent_info:
            d["agent_key"] = agent_info[t.leased_by]["agent_key"]
            d["agent_role"] = agent_info[t.leased_by]["role"]
        else:
            d["agent_key"] = None
            d["agent_role"] = None
        task_dicts.append(d)

    # Group by state for kanban
    grouped: dict[str, list[dict]] = {
        "READY": [],
        "IN_PROGRESS": [],
        "BLOCKED": [],
        "DONE": [],
        "FAILED": [],
    }
    for t in task_dicts:
        key = t["state"]
        if key in grouped:
            grouped[key].append(t)

    return {
        "tasks": task_dicts,
        "grouped": grouped,
        "total": len(task_dicts),
        "project": str(project_id),
    }


@router.get("/tasks/{task_id}")
async def get_task(request: Request, task_id: str) -> dict:
    """Get a single task by its queue UUID."""
    try:
        tid = uuid.UUID(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {task_id}") from exc

    with get_connection() as conn:
        repo = KgnRepository(conn)
        task = repo.get_task_status(tid)

        if task is None:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

        d = _task_to_dict(task)

        # Resolve title
        node = repo.get_node_by_id(task.task_node_id)
        d["title"] = node.title if node else str(task.task_node_id)[:12] + "…"

    return d


@router.get("/tasks/{task_id}/activities")
async def get_task_activities(request: Request, task_id: str) -> dict:
    """Get the activity log for a task."""
    try:
        tid = uuid.UUID(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {task_id}") from exc

    with get_connection() as conn:
        repo = KgnRepository(conn)

        # Verify task exists
        task = repo.get_task_status(tid)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

        activities = repo.get_task_activities(tid)

    return {
        "task_id": str(tid),
        "activities": [_activity_to_dict(a) for a in activities],
        "total": len(activities),
    }
