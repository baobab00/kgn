"""Tests for Phase 9 Step 5 — Dashboard Stats API + SSE + EventBus.

Covers:
- GET /api/v1/stats — aggregated statistics + Health Index
- GET /api/v1/events — SSE stream connection
- EventBus publish/subscribe
- Dashboard HTML structure
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Generator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.web.events import EventBus

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    title: str = "Test Node",
    node_type: NodeType = NodeType.SPEC,
    status: NodeStatus = NodeStatus.ACTIVE,
    node_id: uuid.UUID | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=node_id or uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=status,
        title=title,
        body_md=f"Body of {title}",
        content_hash=uuid.uuid4().hex,
        created_by=agent_id,
        created_at=datetime.now(tz=UTC),
    )


@contextmanager
def _mock_connection(db_conn: Connection) -> Generator[Connection, None, None]:
    yield db_conn


def _all_patches(db_conn: Connection):
    """Return patch contexts for all web route modules."""
    mock = lambda: _mock_connection(db_conn)  # noqa: E731
    return (
        patch("kgn.web.routes.nodes.get_connection", mock),
        patch("kgn.web.routes.health.get_connection", mock),
        patch("kgn.web.routes.subgraph.get_connection", mock),
        patch("kgn.web.routes.edges.get_connection", mock),
        patch("kgn.web.routes.tasks.get_connection", mock),
        patch("kgn.web.routes.stats.get_connection", mock),
        patch("kgn.web.routes.search.get_connection", mock),
    )


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def stats_client(
    db_conn: Connection,
    project_id: uuid.UUID,
) -> TestClient:
    """TestClient with no extra data."""
    from kgn.web.app import create_app

    app = create_app(project_name="test-stats", project_id=project_id)

    patches = _all_patches(db_conn)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        yield TestClient(app)


@pytest.fixture
def stats_with_data(
    db_conn: Connection,
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> tuple[TestClient, dict]:
    """TestClient with some nodes and tasks for stats testing."""
    from kgn.web.app import create_app

    # Create various node types
    nodes = [
        _make_node(project_id, agent_id, title="Goal A", node_type=NodeType.GOAL),
        _make_node(project_id, agent_id, title="Spec A", node_type=NodeType.SPEC),
        _make_node(project_id, agent_id, title="Spec B", node_type=NodeType.SPEC),
        _make_node(project_id, agent_id, title="Task A", node_type=NodeType.TASK),
        _make_node(project_id, agent_id, title="Task B", node_type=NodeType.TASK),
        _make_node(project_id, agent_id, title="Task C", node_type=NodeType.TASK),
        _make_node(
            project_id,
            agent_id,
            title="Archived Node",
            node_type=NodeType.ARCH,
            status=NodeStatus.ARCHIVED,
        ),
    ]
    for n in nodes:
        repo.upsert_node(n)

    # Enqueue some tasks
    task_nodes = [n for n in nodes if n.type == NodeType.TASK]
    for tn in task_nodes:
        repo.enqueue_task(project_id, tn.id, priority=5)

    # Checkout one task to get IN_PROGRESS
    repo.checkout_task(project_id, agent_id, lease_duration_sec=600)

    info = {
        "node_ids": [n.id for n in nodes],
        "task_node_ids": [n.id for n in task_nodes],
    }

    app = create_app(project_name="test-stats-data", project_id=project_id)
    patches = _all_patches(db_conn)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        yield TestClient(app), info


# ── Stats API Tests ────────────────────────────────────────────────────


class TestStatsAPI:
    """Tests for GET /api/v1/stats."""

    def test_stats_empty(self, stats_client: TestClient) -> None:
        """Empty project returns zero counts and health_index 1.0."""
        r = stats_client.get("/api/v1/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total_nodes"] == 0
        assert data["total_edges"] == 0
        assert data["active_nodes"] == 0
        assert data["health_index"] == 1.0
        assert data["node_types"] == {}
        assert data["task_pipeline"] == {}

    def test_stats_with_data(
        self,
        stats_with_data: tuple[TestClient, dict],
    ) -> None:
        """Populated project returns correct counts."""
        client, info = stats_with_data
        r = client.get("/api/v1/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total_nodes"] == 7
        assert data["total_edges"] == 0  # no edges created
        assert data["active_nodes"] == 6  # 7 minus 1 archived

    def test_stats_node_types_breakdown(
        self,
        stats_with_data: tuple[TestClient, dict],
    ) -> None:
        """node_types dict shows per-type counts."""
        client, _ = stats_with_data
        data = client.get("/api/v1/stats").json()
        nt = data["node_types"]
        assert nt.get("GOAL", 0) == 1
        assert nt.get("SPEC", 0) == 2
        assert nt.get("TASK", 0) == 3
        assert nt.get("ARCH", 0) == 1

    def test_stats_task_pipeline(
        self,
        stats_with_data: tuple[TestClient, dict],
    ) -> None:
        """task_pipeline shows state counts."""
        client, _ = stats_with_data
        data = client.get("/api/v1/stats").json()
        pipeline = data["task_pipeline"]
        assert pipeline.get("READY", 0) == 2
        assert pipeline.get("IN_PROGRESS", 0) == 1

    def test_stats_health_index_range(
        self,
        stats_with_data: tuple[TestClient, dict],
    ) -> None:
        """health_index is between 0.0 and 1.0."""
        client, _ = stats_with_data
        data = client.get("/api/v1/stats").json()
        assert 0.0 <= data["health_index"] <= 1.0

    def test_stats_health_metrics(
        self,
        stats_with_data: tuple[TestClient, dict],
    ) -> None:
        """health_metrics sub-object contains expected keys."""
        client, _ = stats_with_data
        data = client.get("/api/v1/stats").json()
        m = data["health_metrics"]
        expected_keys = {
            "orphan_rate",
            "orphan_count",
            "conflict_count",
            "wip_tasks",
            "superseded_stale",
            "dup_spec_rate",
            "open_assumptions",
        }
        assert expected_keys.issubset(m.keys())

    def test_stats_health_metrics_wip_count(
        self,
        stats_with_data: tuple[TestClient, dict],
    ) -> None:
        """wip_tasks reflects checked-out tasks."""
        client, _ = stats_with_data
        data = client.get("/api/v1/stats").json()
        assert data["health_metrics"]["wip_tasks"] == 1

    def test_stats_project_id(
        self,
        stats_with_data: tuple[TestClient, dict],
    ) -> None:
        """Response includes project UUID."""
        client, _ = stats_with_data
        data = client.get("/api/v1/stats").json()
        assert "project" in data
        # Should be a valid UUID string
        uuid.UUID(data["project"])

    def test_stats_health_index_empty_is_perfect(
        self,
        stats_client: TestClient,
    ) -> None:
        """Empty graph has perfect health index (1.0)."""
        data = stats_client.get("/api/v1/stats").json()
        assert data["health_index"] == 1.0


# ── SSE Endpoint Tests ─────────────────────────────────────────────────


class TestSSEEndpoint:
    """Tests for GET /api/v1/events (SSE stream)."""

    def test_sse_content_type(self, stats_client: TestClient) -> None:
        """SSE endpoint returns text/event-stream content type."""

        # Patch the event_bus to yield one event then stop
        async def _one_event():
            yield {"type": "test", "data": {"msg": "hello"}}

        with patch("kgn.web.routes.stats.event_bus") as mock_bus:
            mock_bus.subscribe = _one_event
            r = stats_client.get("/api/v1/events")
            assert r.status_code == 200
            assert "text/event-stream" in r.headers.get("content-type", "")

    def test_sse_event_format(self, stats_client: TestClient) -> None:
        """SSE response contains properly formatted event data."""

        async def _one_event():
            yield {"type": "task_update", "data": {"id": "123"}}

        with patch("kgn.web.routes.stats.event_bus") as mock_bus:
            mock_bus.subscribe = _one_event
            r = stats_client.get("/api/v1/events")
            assert "event: task_update" in r.text
            assert '"id": "123"' in r.text


# ── EventBus Unit Tests ───────────────────────────────────────────────


class TestEventBus:
    """Tests for the EventBus in-memory pub/sub."""

    def test_initial_state(self) -> None:
        """New EventBus has no subscribers and empty history."""
        bus = EventBus()
        assert bus.subscriber_count == 0
        assert bus.history == []

    def test_publish_stores_history(self) -> None:
        """Published events are stored in history."""

        async def _run():
            bus = EventBus(maxlen=10)
            await bus.publish("test_event", {"key": "value"})
            assert len(bus.history) == 1
            assert bus.history[0]["type"] == "test_event"
            assert bus.history[0]["data"] == {"key": "value"}
            assert "timestamp" in bus.history[0]

        asyncio.run(_run())

    def test_history_maxlen(self) -> None:
        """History respects maxlen limit."""

        async def _run():
            bus = EventBus(maxlen=3)
            for i in range(5):
                await bus.publish("evt", {"i": i})
            assert len(bus.history) == 3
            # Oldest events dropped
            assert bus.history[0]["data"]["i"] == 2

        asyncio.run(_run())

    def test_subscribe_receives_events(self) -> None:
        """Subscriber receives published events."""

        async def _run():
            bus = EventBus()
            received = []

            async def _consumer():
                async for event in bus.subscribe():
                    received.append(event)
                    if len(received) >= 2:
                        break

            task = asyncio.create_task(_consumer())
            # Give subscriber time to register
            await asyncio.sleep(0.01)

            assert bus.subscriber_count == 1
            await bus.publish("a", {"n": 1})
            await bus.publish("b", {"n": 2})

            await asyncio.wait_for(task, timeout=2.0)
            assert len(received) == 2
            assert received[0]["type"] == "a"
            assert received[1]["type"] == "b"

        asyncio.run(_run())

    def test_subscriber_cleanup(self) -> None:
        """Subscriber is removed after generator is closed."""

        async def _run():
            bus = EventBus()
            gen = bus.subscribe()

            # Start the generator — subscriber registered
            task = asyncio.ensure_future(gen.__anext__())
            await asyncio.sleep(0.01)
            assert bus.subscriber_count == 1

            # Cancel the consumer task — triggers GeneratorExit via finally
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            # Explicitly close to trigger finally cleanup
            await gen.aclose()
            assert bus.subscriber_count == 0

        asyncio.run(_run())

    def test_multiple_subscribers(self) -> None:
        """Multiple subscribers each receive events."""

        async def _run():
            bus = EventBus()
            results_a = []
            results_b = []

            async def _sub(results):
                async for event in bus.subscribe():
                    results.append(event)
                    if len(results) >= 1:
                        break

            t1 = asyncio.create_task(_sub(results_a))
            t2 = asyncio.create_task(_sub(results_b))
            await asyncio.sleep(0.01)

            assert bus.subscriber_count == 2
            await bus.publish("shared", {"x": 1})

            await asyncio.wait_for(asyncio.gather(t1, t2), timeout=2.0)
            assert len(results_a) == 1
            assert len(results_b) == 1
            assert results_a[0]["data"] == {"x": 1}

        asyncio.run(_run())


# ── Health Index Computation Tests ─────────────────────────────────────


class TestHealthIndexComputation:
    """Tests for _compute_health_index function."""

    def test_empty_graph(self) -> None:
        """Empty graph → 1.0."""
        from kgn.web.routes.stats import _compute_health_index

        assert _compute_health_index(0, 0, 0, 0) == 1.0

    def test_no_problems(self) -> None:
        """No orphans/conflicts → 1.0."""
        from kgn.web.routes.stats import _compute_health_index

        assert _compute_health_index(0, 0, 100, 200) == 1.0

    def test_some_problems(self) -> None:
        """Some orphans reduce index."""
        from kgn.web.routes.stats import _compute_health_index

        # 10 orphans out of 100 total → 1 - 10/100 = 0.9
        result = _compute_health_index(10, 0, 100, 0)
        assert result == 0.9

    def test_floor_at_zero(self) -> None:
        """Index cannot go below 0.0."""
        from kgn.web.routes.stats import _compute_health_index

        # More problems than elements
        result = _compute_health_index(200, 100, 10, 10)
        assert result == 0.0


# ── Dashboard HTML Tests ──────────────────────────────────────────────


class TestDashboardHTML:
    """Tests for dashboard HTML structure in index.html."""

    def test_dashboard_css_linked(self, stats_client: TestClient) -> None:
        """index.html includes dashboard.css."""
        r = stats_client.get("/")
        assert "dashboard.css" in r.text

    def test_dashboard_js_linked(self, stats_client: TestClient) -> None:
        """index.html includes dashboard.js."""
        r = stats_client.get("/")
        assert "dashboard.js" in r.text

    def test_chartjs_cdn_linked(self, stats_client: TestClient) -> None:
        """index.html includes Chart.js CDN."""
        r = stats_client.get("/")
        assert "chart.js" in r.text or "chart.umd" in r.text

    def test_dashboard_container(self, stats_client: TestClient) -> None:
        """index.html has dashboard-container div."""
        r = stats_client.get("/")
        assert 'id="dashboard-container"' in r.text

    def test_dashboard_tab(self, stats_client: TestClient) -> None:
        """index.html has Dashboard tab button."""
        r = stats_client.get("/")
        assert 'data-tab="dashboard"' in r.text
        assert "Dashboard" in r.text
