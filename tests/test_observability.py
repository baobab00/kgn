"""Tests for ObservabilityService — agent tracking and bottleneck detection (Phase 10, Step 6).

Covers:
- Repository layer: get_agent_timeline, get_agent_task_stats, get_task_durations, get_project_activity_summary
- ObservabilityService: get_agent_stats, get_agent_timeline, get_task_flow, detect_bottlenecks, get_report
- Web API: /agents, /agents/{id}/timeline, /agents/{id}/stats, /workflow/flow, /workflow/bottlenecks
- CLI: agent stats, agent timeline
- Dataclass properties: AgentStats.success_rate

Target: 30+ tests
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

import pytest
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.models.enums import (
    ActivityType,
    NodeStatus,
    NodeType,
)
from kgn.models.node import NodeRecord
from kgn.orchestration.observability import (
    AgentStats,
    Bottleneck,
    ObservabilityReport,
    ObservabilityService,
    TaskFlowEntry,
    TimelineEntry,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    *,
    title: str = "Obs Test Node",
    node_type: NodeType = NodeType.TASK,
    created_by: uuid.UUID | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md="## Content\n\nTest.",
        content_hash=uuid.uuid4().hex,
        created_by=created_by,
    )


def _create_agent(repo: KgnRepository, project_id: uuid.UUID, key: str) -> uuid.UUID:
    return repo.get_or_create_agent(project_id, key)


def _setup_task_lifecycle(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    node_title: str = "Task A",
    complete: bool = True,
) -> uuid.UUID:
    """Create a node, enqueue, checkout (mark IN_PROGRESS), and optionally complete.

    Returns the task_queue_id.
    """
    node = _make_node(project_id, title=node_title, created_by=agent_id)
    repo.upsert_node(node)
    tq_id = repo.enqueue_task(project_id, node.id)

    # Simulate checkout: mark IN_PROGRESS + lease_by
    repo._conn.execute(
        "UPDATE task_queue SET state = 'IN_PROGRESS', leased_by = %s, "
        "updated_at = now() WHERE id = %s",
        (agent_id, tq_id),
    )

    if complete:
        repo._conn.execute(
            "UPDATE task_queue SET state = 'DONE', updated_at = now() WHERE id = %s",
            (tq_id,),
        )

    return tq_id


def _setup_failed_task(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    node_title: str = "Failed Task",
) -> uuid.UUID:
    """Create and fail a task."""
    node = _make_node(project_id, title=node_title, created_by=agent_id)
    repo.upsert_node(node)
    tq_id = repo.enqueue_task(project_id, node.id)
    repo._conn.execute(
        "UPDATE task_queue SET state = 'FAILED', leased_by = %s, updated_at = now() WHERE id = %s",
        (agent_id, tq_id),
    )
    return tq_id


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def obs_svc(repo: KgnRepository) -> ObservabilityService:
    return ObservabilityService(repo)


# ══════════════════════════════════════════════════════════════════════
# Repository helper tests
# ══════════════════════════════════════════════════════════════════════


class TestRepositoryObservability:
    """Tests for new repository query methods."""

    def test_get_agent_timeline_empty(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        result = repo.get_agent_timeline(project_id)
        assert result == []

    def test_get_agent_timeline_returns_entries(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        repo.log_activity(project_id, agent_id, ActivityType.NODE_CREATED, message="n1")
        repo.log_activity(project_id, agent_id, ActivityType.NODE_UPDATED, message="n2")

        result = repo.get_agent_timeline(project_id)
        assert len(result) == 2
        # Most recent first
        assert result[0]["message"] == "n2"
        assert result[1]["message"] == "n1"

    def test_get_agent_timeline_filtered(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        agent_b = _create_agent(repo, project_id, "obs-agent-b")
        repo.log_activity(project_id, agent_id, ActivityType.NODE_CREATED, message="a1")
        repo.log_activity(project_id, agent_b, ActivityType.NODE_CREATED, message="b1")

        result = repo.get_agent_timeline(project_id, agent_id)
        assert len(result) == 1
        assert result[0]["message"] == "a1"

    def test_get_agent_timeline_limit(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        for i in range(5):
            repo.log_activity(project_id, agent_id, ActivityType.NODE_CREATED, message=f"m{i}")

        result = repo.get_agent_timeline(project_id, limit=3)
        assert len(result) == 3

    def test_get_agent_task_stats_empty(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        stats = repo.get_agent_task_stats(project_id)
        assert len(stats) >= 1
        agent_stat = [s for s in stats if s["agent_id"] == agent_id][0]
        assert agent_stat["total_tasks"] == 0
        assert agent_stat["done_count"] == 0

    def test_get_agent_task_stats_with_tasks(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _setup_task_lifecycle(repo, project_id, agent_id, node_title="T1")
        _setup_task_lifecycle(repo, project_id, agent_id, node_title="T2")
        _setup_failed_task(repo, project_id, agent_id, node_title="F1")

        stats = repo.get_agent_task_stats(project_id)
        agent_stat = [s for s in stats if s["agent_id"] == agent_id][0]
        assert agent_stat["done_count"] == 2
        assert agent_stat["failed_count"] == 1
        assert agent_stat["total_tasks"] == 3

    def test_get_task_durations(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _setup_task_lifecycle(repo, project_id, agent_id)

        durations = repo.get_task_durations(project_id)
        assert len(durations) >= 1
        assert durations[0]["duration_sec"] is not None

    def test_get_task_durations_empty(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        durations = repo.get_task_durations(project_id)
        assert durations == []

    def test_get_project_activity_summary(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        repo.log_activity(project_id, agent_id, ActivityType.NODE_CREATED)
        repo.log_activity(project_id, agent_id, ActivityType.NODE_CREATED)
        repo.log_activity(project_id, agent_id, ActivityType.NODE_UPDATED)

        summary = repo.get_project_activity_summary(project_id)
        summary_dict = {r["activity_type"]: r["count"] for r in summary}
        assert summary_dict["NODE_CREATED"] == 2
        assert summary_dict["NODE_UPDATED"] == 1


# ══════════════════════════════════════════════════════════════════════
# ObservabilityService tests
# ══════════════════════════════════════════════════════════════════════


class TestObservabilityService:
    """Core service tests."""

    def test_get_agent_stats(
        self,
        obs_svc: ObservabilityService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _setup_task_lifecycle(repo, project_id, agent_id, node_title="ST1")

        stats = obs_svc.get_agent_stats(project_id)
        assert len(stats) >= 1
        agent_stat = [s for s in stats if s.agent_id == agent_id][0]
        assert isinstance(agent_stat, AgentStats)
        assert agent_stat.done_count == 1

    def test_agent_stats_success_rate(self) -> None:
        s = AgentStats(
            agent_id=uuid.uuid4(),
            agent_key="a",
            role="worker",
            total_tasks=10,
            done_count=8,
            failed_count=2,
            avg_duration_sec=5.0,
        )
        assert s.success_rate == 80.0

    def test_agent_stats_success_rate_zero(self) -> None:
        s = AgentStats(
            agent_id=uuid.uuid4(),
            agent_key="a",
            role="worker",
            total_tasks=0,
            done_count=0,
            failed_count=0,
            avg_duration_sec=0.0,
        )
        assert s.success_rate == 0.0

    def test_get_agent_timeline(
        self,
        obs_svc: ObservabilityService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        repo.log_activity(project_id, agent_id, ActivityType.TASK_CHECKOUT, message="tl1")

        entries = obs_svc.get_agent_timeline(project_id, agent_id)
        assert len(entries) >= 1
        assert isinstance(entries[0], TimelineEntry)
        assert entries[0].activity_type == "TASK_CHECKOUT"

    def test_get_agent_timeline_all(
        self,
        obs_svc: ObservabilityService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        agent_b = _create_agent(repo, project_id, "obs-tl-b")
        repo.log_activity(project_id, agent_id, ActivityType.NODE_CREATED, message="a")
        repo.log_activity(project_id, agent_b, ActivityType.NODE_CREATED, message="b")

        entries = obs_svc.get_agent_timeline(project_id)
        assert len(entries) == 2

    def test_get_task_flow(
        self,
        obs_svc: ObservabilityService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _setup_task_lifecycle(repo, project_id, agent_id, node_title="flow1")

        flow = obs_svc.get_task_flow(project_id)
        assert len(flow) >= 1
        assert isinstance(flow[0], TaskFlowEntry)
        assert flow[0].state == "DONE"

    def test_detect_bottlenecks_empty(
        self,
        obs_svc: ObservabilityService,
        project_id: uuid.UUID,
    ) -> None:
        bottlenecks = obs_svc.detect_bottlenecks(project_id)
        assert bottlenecks == []

    def test_detect_bottlenecks_finds_slow_tasks(
        self,
        obs_svc: ObservabilityService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        # Create 5 tasks: 4 fast, 1 slow
        for i in range(4):
            _setup_task_lifecycle(repo, project_id, agent_id, node_title=f"Fast{i}")

        # Create a slow task
        slow_node = _make_node(project_id, title="Slow Task", created_by=agent_id)
        repo.upsert_node(slow_node)
        tq_id = repo.enqueue_task(project_id, slow_node.id)
        db_conn.execute(
            "UPDATE task_queue SET state = 'DONE', leased_by = %s, "
            "updated_at = created_at + interval '1 hour' WHERE id = %s",
            (agent_id, tq_id),
        )

        bottlenecks = obs_svc.detect_bottlenecks(project_id, percentile=0.8)
        assert len(bottlenecks) >= 1
        slow = [b for b in bottlenecks if b.task_title == "Slow Task"]
        assert len(slow) == 1
        assert isinstance(slow[0], Bottleneck)
        assert slow[0].duration_sec > 0

    def test_get_report(
        self,
        obs_svc: ObservabilityService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _setup_task_lifecycle(repo, project_id, agent_id, node_title="R1")
        repo.log_activity(project_id, agent_id, ActivityType.NODE_CREATED)

        report = obs_svc.get_report(project_id)
        assert isinstance(report, ObservabilityReport)
        assert report.total_agents >= 1
        assert report.total_tasks_completed >= 1
        assert "NODE_CREATED" in report.activity_summary

    def test_dataclasses(self) -> None:
        tl = TimelineEntry(
            id=1,
            agent_id=uuid.uuid4(),
            agent_key="a",
            agent_role="worker",
            activity_type="NODE_CREATED",
            target_node_id=None,
            message="test",
            task_queue_id=None,
            created_at=datetime.now(),
        )
        assert tl.activity_type == "NODE_CREATED"

        b = Bottleneck(
            task_queue_id=uuid.uuid4(),
            task_node_id=uuid.uuid4(),
            task_title="t",
            state="DONE",
            duration_sec=10.0,
            leased_by_key="a",
            priority=100,
        )
        assert b.duration_sec == 10.0

        f = TaskFlowEntry(
            task_queue_id=uuid.uuid4(),
            task_node_id=uuid.uuid4(),
            task_title="t",
            state="DONE",
            priority=100,
            leased_by_key="a",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            duration_sec=5.0,
        )
        assert f.duration_sec == 5.0


# ══════════════════════════════════════════════════════════════════════
# Web API tests
# ══════════════════════════════════════════════════════════════════════


@contextmanager
def _mock_connection(db_conn: Connection):
    yield db_conn


class TestWebAgentsAPI:
    """Web API route tests for agents/workflow endpoints."""

    @pytest.fixture
    def client(
        self,
        db_conn: Connection,
        project_id: uuid.UUID,
    ):
        from fastapi.testclient import TestClient

        from kgn.web.app import create_app

        app = create_app(project_name="test-obs", project_id=project_id)
        mock = lambda: _mock_connection(db_conn)  # noqa: E731
        with (
            patch("kgn.web.routes.agents.get_connection", mock),
            patch("kgn.web.routes.nodes.get_connection", mock),
            patch("kgn.web.routes.health.get_connection", mock),
            patch("kgn.web.routes.subgraph.get_connection", mock),
            patch("kgn.web.routes.edges.get_connection", mock),
            patch("kgn.web.routes.tasks.get_connection", mock),
            patch("kgn.web.routes.stats.get_connection", mock),
            patch("kgn.web.routes.search.get_connection", mock),
        ):
            yield TestClient(app)

    def test_list_agents(
        self,
        client,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        _create_agent(repo, project_id, "web-agent-1")

        resp = client.get("/api/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert data["total"] >= 1

    def test_agent_timeline(
        self,
        client,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        aid = _create_agent(repo, project_id, "web-tl-agent")
        repo.log_activity(project_id, aid, ActivityType.NODE_CREATED, message="web-test")

        resp = client.get(f"/api/v1/agents/{aid}/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert data["entries"][0]["message"] == "web-test"

    def test_agent_timeline_invalid_uuid(self, client) -> None:
        resp = client.get("/api/v1/agents/not-a-uuid/timeline")
        assert resp.status_code == 400

    def test_agent_stats_endpoint(
        self,
        client,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        resp = client.get(f"/api/v1/agents/{agent_id}/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "agent_key" in data
        assert "success_rate" in data

    def test_agent_stats_not_found(self, client) -> None:
        fake = uuid.uuid4()
        resp = client.get(f"/api/v1/agents/{fake}/stats")
        assert resp.status_code == 404

    def test_workflow_flow(
        self,
        client,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _setup_task_lifecycle(repo, project_id, agent_id, node_title="web-flow")

        resp = client.get("/api/v1/workflow/flow")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert data["total"] >= 1

    def test_workflow_bottlenecks(
        self,
        client,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _setup_task_lifecycle(repo, project_id, agent_id, node_title="bn-task")

        resp = client.get("/api/v1/workflow/bottlenecks")
        assert resp.status_code == 200
        data = resp.json()
        assert "bottlenecks" in data
        assert "percentile" in data

    def test_workflow_bottlenecks_custom_percentile(
        self,
        client,
    ) -> None:
        resp = client.get("/api/v1/workflow/bottlenecks?percentile=0.5")
        assert resp.status_code == 200
        assert resp.json()["percentile"] == 0.5


# ══════════════════════════════════════════════════════════════════════
# CLI tests
# ══════════════════════════════════════════════════════════════════════


class TestCLIAgentCommands:
    """CLI agent stats/timeline command tests."""

    def test_agent_stats_cli(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app

        # Get the project name
        project_name = repo._conn.execute(
            "SELECT name FROM projects WHERE id = %s",
            (project_id,),
        ).fetchone()[0]

        _setup_task_lifecycle(repo, project_id, agent_id, node_title="CLI-stat")

        runner = CliRunner()
        with (
            patch(
                "kgn.db.connection.get_connection",
                lambda: _mock_connection(repo._conn),
            ),
            patch("kgn.db.connection.close_pool"),
        ):
            result = runner.invoke(app, ["agent", "stats", "--project", project_name])

        assert result.exit_code == 0
        assert "Agent Stats" in result.output or "Done" in result.output

    def test_agent_timeline_cli(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app

        project_name = repo._conn.execute(
            "SELECT name FROM projects WHERE id = %s",
            (project_id,),
        ).fetchone()[0]

        repo.log_activity(project_id, agent_id, ActivityType.NODE_CREATED, message="cli-tl")

        runner = CliRunner()
        with (
            patch(
                "kgn.db.connection.get_connection",
                lambda: _mock_connection(repo._conn),
            ),
            patch("kgn.db.connection.close_pool"),
        ):
            result = runner.invoke(app, ["agent", "timeline", "--project", project_name])

        assert result.exit_code == 0
        assert "Timeline" in result.output or "NODE_CREATED" in result.output

    def test_agent_timeline_cli_not_found(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app

        project_name = repo._conn.execute(
            "SELECT name FROM projects WHERE id = %s",
            (project_id,),
        ).fetchone()[0]

        runner = CliRunner()
        with (
            patch(
                "kgn.db.connection.get_connection",
                lambda: _mock_connection(repo._conn),
            ),
            patch("kgn.db.connection.close_pool"),
        ):
            result = runner.invoke(
                app,
                ["agent", "timeline", "--project", project_name, "--agent", "nonexistent-agent"],
            )

        assert result.exit_code == 1
