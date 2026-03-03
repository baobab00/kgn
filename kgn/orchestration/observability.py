"""ObservabilityService — agent activity tracking and bottleneck detection.

Provides tools for monitoring multi-agent workflows:
- Agent activity timelines
- Per-agent task statistics
- Bottleneck detection (top-20% slowest tasks)
- Task flow overview (DAG with time axis)

Rule compliance:
- R1  — all SQL resides in repository layer
- R12 — service-layer orchestration only
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import structlog

from kgn.db.repository import KgnRepository

log = structlog.get_logger()


# ── Result types ───────────────────────────────────────────────────────


@dataclass
class AgentStats:
    """Per-agent task statistics."""

    agent_id: uuid.UUID
    agent_key: str
    role: str
    total_tasks: int
    done_count: int
    failed_count: int
    avg_duration_sec: float

    @property
    def success_rate(self) -> float:
        """Percentage of DONE tasks out of total (0.0—100.0)."""
        if self.total_tasks == 0:
            return 0.0
        return (self.done_count / self.total_tasks) * 100.0


@dataclass
class TimelineEntry:
    """Single entry in an agent activity timeline."""

    id: int
    agent_id: uuid.UUID
    agent_key: str
    agent_role: str
    activity_type: str
    target_node_id: uuid.UUID | None
    message: str
    task_queue_id: uuid.UUID | None
    created_at: datetime


@dataclass
class Bottleneck:
    """A task identified as a bottleneck (above duration threshold)."""

    task_queue_id: uuid.UUID
    task_node_id: uuid.UUID
    task_title: str
    state: str
    duration_sec: float
    leased_by_key: str | None
    priority: int


@dataclass
class TaskFlowEntry:
    """An entry in the task flow overview."""

    task_queue_id: uuid.UUID
    task_node_id: uuid.UUID
    task_title: str
    state: str
    priority: int
    leased_by_key: str | None
    created_at: datetime
    updated_at: datetime
    duration_sec: float | None


@dataclass
class ObservabilityReport:
    """Aggregated observability report for a project."""

    agent_stats: list[AgentStats]
    bottlenecks: list[Bottleneck]
    activity_summary: dict[str, int]
    total_agents: int
    total_tasks_completed: int
    total_tasks_failed: int


# ── Service ────────────────────────────────────────────────────────────


class ObservabilityService:
    """Agent workflow tracking and bottleneck detection.

    Usage::

        svc = ObservabilityService(repo)
        stats = svc.get_agent_stats(project_id)
        timeline = svc.get_agent_timeline(project_id, agent_id)
        bottlenecks = svc.detect_bottlenecks(project_id)
    """

    def __init__(self, repo: KgnRepository) -> None:
        self._repo = repo

    # ── Agent statistics ───────────────────────────────────────────

    def get_agent_stats(self, project_id: uuid.UUID) -> list[AgentStats]:
        """Return per-agent task statistics.

        Returns:
            List of AgentStats, one per agent in the project.
        """
        rows = self._repo.get_agent_task_stats(project_id)
        return [
            AgentStats(
                agent_id=r["agent_id"],
                agent_key=r["agent_key"],
                role=r["role"],
                total_tasks=r["total_tasks"],
                done_count=r["done_count"],
                failed_count=r["failed_count"],
                avg_duration_sec=float(r["avg_duration_sec"]),
            )
            for r in rows
        ]

    # ── Timeline ───────────────────────────────────────────────────

    def get_agent_timeline(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        *,
        limit: int = 50,
    ) -> list[TimelineEntry]:
        """Return activity timeline for one or all agents.

        Args:
            project_id: Project to query.
            agent_id: Optional — filter to a single agent.
            limit: Maximum entries to return (default 50).

        Returns:
            List of TimelineEntry, most recent first.
        """
        rows = self._repo.get_agent_timeline(project_id, agent_id, limit=limit)
        return [
            TimelineEntry(
                id=r["id"],
                agent_id=r["agent_id"],
                agent_key=r["agent_key"],
                agent_role=r["agent_role"],
                activity_type=r["activity_type"],
                target_node_id=r.get("target_node_id"),
                message=r["message"],
                task_queue_id=r.get("task_queue_id"),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ── Task flow ──────────────────────────────────────────────────

    def get_task_flow(self, project_id: uuid.UUID) -> list[TaskFlowEntry]:
        """Return task flow data (completed/failed tasks with durations).

        Returns:
            List of TaskFlowEntry ordered by duration descending.
        """
        rows = self._repo.get_task_durations(project_id)
        return [
            TaskFlowEntry(
                task_queue_id=r["task_queue_id"],
                task_node_id=r["task_node_id"],
                task_title=r["task_title"],
                state=r["state"],
                priority=r["priority"],
                leased_by_key=r.get("leased_by_key"),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                duration_sec=float(r["duration_sec"]) if r["duration_sec"] is not None else None,
            )
            for r in rows
        ]

    # ── Bottleneck detection ───────────────────────────────────────

    def detect_bottlenecks(
        self,
        project_id: uuid.UUID,
        *,
        percentile: float = 0.8,
    ) -> list[Bottleneck]:
        """Identify bottleneck tasks (top percentile by duration).

        A task is a bottleneck if its duration is in the top
        ``(1 - percentile)`` portion of all completed tasks.
        Default: top 20% (percentile=0.8).

        Args:
            project_id: Project to analyze.
            percentile: Threshold (0.0–1.0). Tasks above this
                percentile of duration are bottlenecks.

        Returns:
            List of Bottleneck entries, sorted by duration descending.
        """
        rows = self._repo.get_task_durations(project_id)
        if not rows:
            return []

        durations = [float(r["duration_sec"]) for r in rows if r["duration_sec"] is not None]
        if not durations:
            return []

        durations_sorted = sorted(durations)
        idx = int(len(durations_sorted) * percentile)
        threshold = durations_sorted[min(idx, len(durations_sorted) - 1)]

        bottlenecks = []
        for r in rows:
            dur = float(r["duration_sec"]) if r["duration_sec"] is not None else 0.0
            if dur >= threshold:
                bottlenecks.append(
                    Bottleneck(
                        task_queue_id=r["task_queue_id"],
                        task_node_id=r["task_node_id"],
                        task_title=r["task_title"],
                        state=r["state"],
                        duration_sec=dur,
                        leased_by_key=r.get("leased_by_key"),
                        priority=r["priority"],
                    )
                )

        log.debug(
            "bottlenecks_detected",
            project_id=str(project_id),
            threshold_sec=threshold,
            count=len(bottlenecks),
        )
        return bottlenecks

    # ── Aggregated report ──────────────────────────────────────────

    def get_report(self, project_id: uuid.UUID) -> ObservabilityReport:
        """Generate a full observability report for a project.

        Combines agent stats, bottleneck detection, and activity summary.
        """
        stats = self.get_agent_stats(project_id)
        bottlenecks = self.detect_bottlenecks(project_id)
        summary_rows = self._repo.get_project_activity_summary(project_id)

        activity_summary = {r["activity_type"]: r["count"] for r in summary_rows}

        total_done = sum(s.done_count for s in stats)
        total_failed = sum(s.failed_count for s in stats)

        return ObservabilityReport(
            agent_stats=stats,
            bottlenecks=bottlenecks,
            activity_summary=activity_summary,
            total_agents=len(stats),
            total_tasks_completed=total_done,
            total_tasks_failed=total_failed,
        )
