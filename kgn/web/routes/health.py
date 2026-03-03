"""KGN Web — Health API route.

GET /api/v1/health — Project-level graph health metrics.

Delegates to HealthService for computation (R3/R12).
"""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter, Request

from kgn.db.connection import get_connection
from kgn.db.repository import KgnRepository
from kgn.graph.health import HealthService

router = APIRouter(tags=["health"])


@router.get("/health")
async def get_health(request: Request) -> dict:
    """Return graph health metrics for the current project."""
    project_id = request.app.state.project_id

    with get_connection() as conn:
        repo = KgnRepository(conn)
        svc = HealthService(repo)
        report = svc.compute(project_id)

    result = dataclasses.asdict(report)
    # Add computed properties that aren't in asdict output.
    result["orphan_rate"] = round(report.orphan_rate, 4)
    result["orphan_rate_ok"] = report.orphan_rate_ok
    result["conflict_ok"] = report.conflict_ok
    result["superseded_stale_ok"] = report.superseded_stale_ok
    result["dup_spec_rate"] = round(report.dup_spec_rate, 4)
    result["dup_spec_rate_ok"] = report.dup_spec_rate_ok
    result["project"] = str(project_id)

    return result
