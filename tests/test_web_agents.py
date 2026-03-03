"""Tests for Web UI agents integration — Step 7 (Phase 10).

Covers:
- Template: Agents tab presence, CSS/JS includes
- Tasks API: agent_key + agent_role enrichment on kanban cards
- Agents API: list, timeline, stats, flow, bottlenecks (extended from Step 6)
- Static files: agents.css, agents.js served correctly
- Dashboard: agent chart integration

Target: 22+ tests
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.models.enums import (
    ActivityType,
    NodeStatus,
    NodeType,
)
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    *,
    title: str = "Web Agent Node",
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


@contextmanager
def _mock_connection(db_conn: Connection):
    yield db_conn


def _setup_task_with_agent(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    title: str = "Agent Task",
    complete: bool = True,
) -> uuid.UUID:
    """Create a TASK node, enqueue, assign to agent, optionally complete."""
    node = _make_node(project_id, title=title, created_by=agent_id)
    repo.upsert_node(node)
    tq_id = repo.enqueue_task(project_id, node.id)
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


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def web_client(db_conn: Connection, project_id: uuid.UUID):
    """TestClient with all routes patched."""
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from kgn.web.app import create_app

    app = create_app(project_name="test-agents-ui", project_id=project_id)
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


# ══════════════════════════════════════════════════════════════════════
# Template tests — Agents tab, CSS/JS includes
# ══════════════════════════════════════════════════════════════════════


class TestTemplateIntegration:
    """Verify index.html includes Agents tab, CSS, and JS."""

    def test_agents_tab_present(self, web_client) -> None:
        resp = web_client.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert 'data-tab="agents"' in html
        assert ">Agents<" in html

    def test_five_tabs_in_nav(self, web_client) -> None:
        resp = web_client.get("/")
        html = resp.text
        for tab in ("overview", "graph", "kanban", "agents", "dashboard"):
            assert f'data-tab="{tab}"' in html

    def test_agents_view_container(self, web_client) -> None:
        resp = web_client.get("/")
        html = resp.text
        assert 'data-view="agents"' in html
        assert 'id="agents-container"' in html

    def test_agents_css_linked(self, web_client) -> None:
        resp = web_client.get("/")
        assert "/static/css/agents.css" in resp.text

    def test_agents_js_linked(self, web_client) -> None:
        resp = web_client.get("/")
        assert "/static/js/agents.js" in resp.text

    def test_agents_init_function(self, web_client) -> None:
        resp = web_client.get("/")
        assert "initAgents" in resp.text


# ══════════════════════════════════════════════════════════════════════
# Static file tests — CSS/JS serving
# ══════════════════════════════════════════════════════════════════════


class TestStaticFiles:
    """Verify agents static files are served correctly."""

    def test_agents_css_served(self, web_client) -> None:
        resp = web_client.get("/static/css/agents.css")
        assert resp.status_code == 200
        assert ".agent-card" in resp.text
        assert ".agent-role-badge" in resp.text

    def test_agents_js_served(self, web_client) -> None:
        resp = web_client.get("/static/js/agents.js")
        assert resp.status_code == 200
        assert "AgentsView" in resp.text
        assert "renderAgentPerformanceChart" in resp.text


# ══════════════════════════════════════════════════════════════════════
# Tasks API agent enrichment tests
# ══════════════════════════════════════════════════════════════════════


class TestTasksAgentEnrichment:
    """Tasks API returns agent_key and agent_role for kanban cards."""

    def test_tasks_include_agent_key(
        self,
        web_client,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """IN_PROGRESS task should have agent_key and agent_role."""
        _setup_task_with_agent(
            repo,
            project_id,
            agent_id,
            title="K-Agent",
            complete=False,
        )

        resp = web_client.get("/api/v1/tasks")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        in_progress = [t for t in tasks if t["state"] == "IN_PROGRESS"]
        assert len(in_progress) >= 1
        task = in_progress[0]
        assert task["agent_key"] is not None
        assert task["agent_role"] is not None

    def test_tasks_ready_no_agent(
        self,
        web_client,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """READY task should have null agent info."""
        node = _make_node(project_id, title="Unassigned", created_by=agent_id)
        repo.upsert_node(node)
        repo.enqueue_task(project_id, node.id)

        resp = web_client.get("/api/v1/tasks")
        ready = [t for t in resp.json()["tasks"] if t["state"] == "READY"]
        assert len(ready) >= 1
        task = ready[0]
        assert task["agent_key"] is None
        assert task["agent_role"] is None

    def test_tasks_grouped_includes_agent(
        self,
        web_client,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Grouped kanban response should also have agent info."""
        _setup_task_with_agent(
            repo,
            project_id,
            agent_id,
            title="KG-Task",
            complete=True,
        )

        resp = web_client.get("/api/v1/tasks")
        grouped = resp.json()["grouped"]
        done = grouped["DONE"]
        assert len(done) >= 1
        assert "agent_key" in done[0]

    def test_tasks_done_has_agent_key(
        self,
        web_client,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _setup_task_with_agent(
            repo,
            project_id,
            agent_id,
            title="DoneTask",
            complete=True,
        )

        resp = web_client.get("/api/v1/tasks")
        done = [t for t in resp.json()["tasks"] if t["state"] == "DONE"]
        assert len(done) >= 1
        assert done[0]["agent_key"] is not None


# ══════════════════════════════════════════════════════════════════════
# Agents API tests (extended from test_observability.py)
# ══════════════════════════════════════════════════════════════════════


class TestAgentsAPIExtended:
    """Additional agents API tests for Step 7."""

    def test_agents_list_with_stats(
        self,
        web_client,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _setup_task_with_agent(repo, project_id, agent_id, title="S7-task")

        resp = web_client.get("/api/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        # Check stats fields are present
        agent = data["agents"][0]
        for field in (
            "agent_id",
            "agent_key",
            "role",
            "total_tasks",
            "done_count",
            "failed_count",
            "success_rate",
            "avg_duration_sec",
        ):
            assert field in agent, f"Missing field: {field}"

    def test_agents_list_success_rate(
        self,
        web_client,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _setup_task_with_agent(repo, project_id, agent_id, title="S7-done")

        resp = web_client.get("/api/v1/agents")
        agents = resp.json()["agents"]
        agent = [a for a in agents if a["agent_id"] == str(agent_id)][0]
        assert agent["success_rate"] >= 0
        assert agent["success_rate"] <= 100

    def test_workflow_flow_has_fields(
        self,
        web_client,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _setup_task_with_agent(repo, project_id, agent_id, title="FlowTask")

        resp = web_client.get("/api/v1/workflow/flow")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert len(tasks) >= 1
        t = tasks[0]
        for field in ("task_queue_id", "task_title", "state", "duration_sec", "leased_by_key"):
            assert field in t, f"Missing field: {field}"

    def test_bottleneck_total_field(
        self,
        web_client,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _setup_task_with_agent(repo, project_id, agent_id, title="BN-task")

        resp = web_client.get("/api/v1/workflow/bottlenecks")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "bottlenecks" in data

    def test_timeline_returns_entries_list(
        self,
        web_client,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        repo.log_activity(
            project_id,
            agent_id,
            ActivityType.NODE_CREATED,
            message="s7-tl",
        )

        resp = web_client.get(f"/api/v1/agents/{agent_id}/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert data["total"] >= 1
        entry = data["entries"][0]
        assert "activity_type" in entry
        assert "message" in entry


# ══════════════════════════════════════════════════════════════════════
# Dashboard integration tests
# ══════════════════════════════════════════════════════════════════════


class TestDashboardAgentIntegration:
    """Dashboard agent chart integration tests."""

    def test_dashboard_js_has_agent_chart(self, web_client) -> None:
        resp = web_client.get("/static/js/dashboard.js")
        assert resp.status_code == 200
        assert "dash-agent-chart" in resp.text
        assert "dash-flow-chart" in resp.text
        assert "renderAgentPerformanceChart" in resp.text

    def test_dashboard_fetches_agents(self, web_client) -> None:
        """Dashboard load() fetches /api/v1/agents."""
        resp = web_client.get("/static/js/dashboard.js")
        assert "fetch('/api/v1/agents')" in resp.text

    def test_agents_js_exports(self, web_client) -> None:
        resp = web_client.get("/static/js/agents.js")
        text = resp.text
        assert "window.AgentsView" in text
        assert "window.renderAgentPerformanceChart" in text
        assert "window.renderTaskFlowChart" in text
        assert "window.ROLE_COLORS" in text


# ══════════════════════════════════════════════════════════════════════
# Kanban card agent rendering tests
# ══════════════════════════════════════════════════════════════════════


class TestKanbanAgentDisplay:
    """Kanban JS renders agent info with role badge."""

    def test_kanban_js_has_agent_role_badge(self, web_client) -> None:
        resp = web_client.get("/static/js/kanban.js")
        assert resp.status_code == 200
        assert "card-agent-role" in resp.text
        assert "card-agent-info" in resp.text

    def test_kanban_css_agent_styles(self, web_client) -> None:
        resp = web_client.get("/static/css/kanban.css")
        assert resp.status_code == 200
        assert ".card-agent" in resp.text

    def test_agents_css_role_colors(self, web_client) -> None:
        resp = web_client.get("/static/css/agents.css")
        text = resp.text
        for role in ("admin", "worker", "reviewer", "indexer", "genesis"):
            assert f"role-{role}" in text
