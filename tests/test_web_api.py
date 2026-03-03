"""Tests for Phase 9 Step 1 — Web API endpoints.

Covers:
- FastAPI create_app factory
- GET / (index HTML page)
- GET /api/v1/nodes (list with filters)
- GET /api/v1/nodes/{id} (detail / 404)
- GET /api/v1/health
- CLI kgn web serve ImportError guard
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord

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
    """Yield the test connection so routes use the test transaction."""
    yield db_conn


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def web_client(
    db_conn: Connection,
    project_id: uuid.UUID,
) -> TestClient:
    """Create a TestClient for the web app with get_connection mocked."""
    from kgn.web.app import create_app

    app = create_app(project_name="test-web", project_id=project_id)

    # Patch get_connection at both route module import locations
    mock = lambda: _mock_connection(db_conn)  # noqa: E731
    with (
        patch("kgn.web.routes.nodes.get_connection", mock),
        patch("kgn.web.routes.health.get_connection", mock),
        patch("kgn.web.routes.subgraph.get_connection", mock),
        patch("kgn.web.routes.edges.get_connection", mock),
        patch("kgn.web.routes.tasks.get_connection", mock),
        patch("kgn.web.routes.stats.get_connection", mock),
        patch("kgn.web.routes.search.get_connection", mock),
    ):
        yield TestClient(app)


@pytest.fixture
def seeded_client(
    db_conn: Connection,
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> TestClient:
    """TestClient with pre-seeded node data."""
    # Seed some nodes
    n1 = _make_node(
        project_id,
        agent_id,
        title="Auth Spec",
        node_type=NodeType.SPEC,
        status=NodeStatus.ACTIVE,
    )
    n2 = _make_node(
        project_id,
        agent_id,
        title="DB Goal",
        node_type=NodeType.GOAL,
        status=NodeStatus.ACTIVE,
    )
    n3 = _make_node(
        project_id,
        agent_id,
        title="Old Decision",
        node_type=NodeType.DECISION,
        status=NodeStatus.ARCHIVED,
    )
    repo.upsert_node(n1)
    repo.upsert_node(n2)
    repo.upsert_node(n3)

    from kgn.web.app import create_app

    app = create_app(project_name="test-web", project_id=project_id)

    mock = lambda: _mock_connection(db_conn)  # noqa: E731
    with (
        patch("kgn.web.routes.nodes.get_connection", mock),
        patch("kgn.web.routes.health.get_connection", mock),
        patch("kgn.web.routes.subgraph.get_connection", mock),
        patch("kgn.web.routes.edges.get_connection", mock),
        patch("kgn.web.routes.tasks.get_connection", mock),
        patch("kgn.web.routes.stats.get_connection", mock),
        patch("kgn.web.routes.search.get_connection", mock),
    ):
        yield TestClient(app)


# ── create_app factory ────────────────────────────────────────────────


class TestCreateApp:
    """Verify the FastAPI application factory."""

    def test_returns_fastapi_instance(self, project_id: uuid.UUID) -> None:
        from kgn.web.app import create_app

        app = create_app(project_name="test", project_id=project_id)

        from fastapi import FastAPI

        assert isinstance(app, FastAPI)

    def test_stores_project_in_state(self, project_id: uuid.UUID) -> None:
        from kgn.web.app import create_app

        app = create_app(project_name="my-proj", project_id=project_id)

        assert app.state.project_name == "my-proj"
        assert app.state.project_id == project_id


# ── GET / (index page) ───────────────────────────────────────────────


class TestIndexPage:
    """GET / should return the dashboard HTML."""

    def test_returns_html(self, web_client: TestClient) -> None:
        resp = web_client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_contains_project_name(self, web_client: TestClient) -> None:
        resp = web_client.get("/")
        assert "test-web" in resp.text

    def test_contains_version(self, web_client: TestClient) -> None:
        from kgn import __version__

        resp = web_client.get("/")
        assert __version__ in resp.text


# ── GET /api/v1/nodes ────────────────────────────────────────────────


class TestListNodes:
    """GET /api/v1/nodes list and filter tests."""

    def test_empty_project_returns_empty(self, web_client: TestClient) -> None:
        resp = web_client.get("/api/v1/nodes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"] == []
        assert data["total"] == 0

    def test_returns_seeded_nodes(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/v1/nodes")
        assert resp.status_code == 200
        data = resp.json()
        # 3 nodes seeded, but archived excluded by default
        assert data["total"] == 2
        titles = {n["title"] for n in data["nodes"]}
        assert "Auth Spec" in titles
        assert "DB Goal" in titles
        assert "Old Decision" not in titles

    def test_filter_by_type(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/v1/nodes?type=SPEC")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["nodes"][0]["type"] == "SPEC"

    def test_filter_by_status_archived(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/v1/nodes?status=ARCHIVED")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["nodes"][0]["title"] == "Old Decision"

    def test_invalid_type_returns_400(self, web_client: TestClient) -> None:
        resp = web_client.get("/api/v1/nodes?type=INVALID")
        assert resp.status_code == 400
        assert "Invalid node type" in resp.json()["detail"]

    def test_invalid_status_returns_400(self, web_client: TestClient) -> None:
        resp = web_client.get("/api/v1/nodes?status=INVALID")
        assert resp.status_code == 400
        assert "Invalid status" in resp.json()["detail"]


# ── GET /api/v1/nodes/{id} ───────────────────────────────────────────


class TestGetNode:
    """GET /api/v1/nodes/{id} detail tests."""

    def test_returns_node_detail(
        self,
        db_conn: Connection,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id, agent_id, title="Detail Node")
        repo.upsert_node(node)

        from kgn.web.app import create_app

        app = create_app(project_name="test-web", project_id=project_id)
        mock = lambda: _mock_connection(db_conn)  # noqa: E731
        with (
            patch("kgn.web.routes.nodes.get_connection", mock),
            patch("kgn.web.routes.health.get_connection", mock),
            patch("kgn.web.routes.subgraph.get_connection", mock),
            patch("kgn.web.routes.edges.get_connection", mock),
            patch("kgn.web.routes.tasks.get_connection", mock),
            patch("kgn.web.routes.stats.get_connection", mock),
            patch("kgn.web.routes.search.get_connection", mock),
        ):
            client = TestClient(app)
            resp = client.get(f"/api/v1/nodes/{node.id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Detail Node"
        assert data["id"] == str(node.id)
        assert data["type"] == "SPEC"

    def test_not_found_returns_404(self, web_client: TestClient) -> None:
        fake_id = uuid.uuid4()
        resp = web_client.get(f"/api/v1/nodes/{fake_id}")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_invalid_uuid_returns_400(self, web_client: TestClient) -> None:
        resp = web_client.get("/api/v1/nodes/not-a-uuid")
        assert resp.status_code == 400
        assert "Invalid UUID" in resp.json()["detail"]


# ── GET /api/v1/health ───────────────────────────────────────────────


class TestHealth:
    """GET /api/v1/health endpoint tests."""

    def test_returns_health_metrics(self, web_client: TestClient) -> None:
        resp = web_client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_nodes" in data
        assert "total_edges" in data
        assert "orphan_rate" in data
        assert "orphan_rate_ok" in data
        assert "conflict_ok" in data
        assert "project" in data

    def test_health_with_seeded_data(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        # 3 nodes seeded (2 active + 1 archived)
        assert data["total_nodes"] >= 2


# ── CLI kgn web serve ────────────────────────────────────────────────


class TestWebServeCLI:
    """CLI command registration and ImportError guard."""

    def test_web_serve_registered(self) -> None:
        """kgn web serve should be a registered command."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["web", "--help"])
        assert result.exit_code == 0
        assert "serve" in result.output

    def test_web_serve_missing_import(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should exit gracefully when fastapi is not installed."""
        import builtins

        from typer.testing import CliRunner

        from kgn.cli import app

        original_import = builtins.__import__

        def mock_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "uvicorn":
                raise ImportError("No module named 'uvicorn'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            runner = CliRunner()
            result = runner.invoke(app, ["web", "serve", "--project", "x"])
        assert result.exit_code != 0
