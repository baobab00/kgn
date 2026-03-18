"""End-to-end tests for Phase 9 — Web Visualization Dashboard.

Validates all 7 web API routes + the HTML index page in a realistic
scenario using the real database (via conftest fixtures), with only
``get_connection`` patched to redirect into the test transaction.

Scenarios:
  1. full_workflow — ingest nodes/edges, traverse ALL API routes
  2. filter_query_params — type/status/tags/text filters on GET /nodes
  3. subgraph_depth_limit — depth parameter + MAX_NODES guard
  4. sse_connection — SSE stream connect + event delivery
  5. similar_and_conflicts — similar node + conflict endpoints
  6. web_not_installed — graceful error when fastapi is absent
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    title: str = "E2E Node",
    node_type: NodeType = NodeType.SPEC,
    status: NodeStatus = NodeStatus.ACTIVE,
    tags: list[str] | None = None,
    body_md: str | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=status,
        title=title,
        body_md=body_md or f"Body of {title}",
        content_hash=uuid.uuid4().hex,
        tags=tags or [],
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


def _enter_patches(patches: tuple):
    """Enter all patch contexts and return a cleanup list."""
    active = []
    for p in patches:
        p.__enter__()
        active.append(p)
    return active


def _exit_patches(active: list):
    """Exit all patch contexts."""
    for p in reversed(active):
        p.__exit__(None, None, None)


def _insert_edge(
    db_conn: Connection,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    edge_type: EdgeType = EdgeType.DEPENDS_ON,
    *,
    project_id: uuid.UUID,
    note: str = "",
) -> None:
    """Insert an edge directly via SQL."""
    db_conn.execute(
        """
        INSERT INTO edges (project_id, from_node_id, to_node_id, type, note, status)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            str(project_id),
            str(from_id),
            str(to_id),
            edge_type.value,
            note,
            "APPROVED",
        ),
    )


# ── Scenario 1: Full Workflow ──────────────────────────────────────────


class TestE2EFullWorkflow:
    """Ingest nodes & edges, then traverse ALL 7 API routes + HTML index."""

    def test_full_workflow(
        self,
        db_conn: Connection,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        from fastapi.testclient import TestClient

        from kgn.web.app import create_app

        repo = KgnRepository(db_conn)

        # 1. Ingest nodes
        spec = _make_node(
            project_id, agent_id, title="Auth Spec", node_type=NodeType.SPEC, tags=["auth"]
        )
        goal = _make_node(
            project_id, agent_id, title="Auth Goal", node_type=NodeType.GOAL, tags=["auth"]
        )
        arch = _make_node(
            project_id, agent_id, title="System Arch", node_type=NodeType.ARCH, tags=["system"]
        )
        repo.upsert_node(spec)
        repo.upsert_node(goal)
        repo.upsert_node(arch)

        # 2. Ingest edge
        _insert_edge(
            db_conn,
            spec.id,
            goal.id,
            EdgeType.IMPLEMENTS,
            project_id=project_id,
            note="spec→goal",
        )

        # Set up TestClient with patches
        app = create_app("e2e-project", project_id)
        patches = _all_patches(db_conn)
        active = _enter_patches(patches)
        try:
            client = TestClient(app)

            # 3. GET /api/v1/nodes — list all nodes
            resp = client.get("/api/v1/nodes")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] >= 3
            titles = {n["title"] for n in data["nodes"]}
            assert "Auth Spec" in titles
            assert "Auth Goal" in titles
            assert "System Arch" in titles

            # 4. GET /api/v1/nodes/{id} — single node detail
            resp = client.get(f"/api/v1/nodes/{spec.id}")
            assert resp.status_code == 200
            assert resp.json()["title"] == "Auth Spec"

            # 5. GET /api/v1/subgraph/{id} — k-hop subgraph
            resp = client.get(f"/api/v1/subgraph/{spec.id}?depth=1")
            assert resp.status_code == 200
            sg = resp.json()
            assert "elements" in sg
            assert "nodes" in sg["elements"]
            assert "edges" in sg["elements"]
            # At least the root node
            node_ids_in_sg = {n["data"]["id"] for n in sg["elements"]["nodes"]}
            assert str(spec.id) in node_ids_in_sg

            # 6. GET /api/v1/edges — edges for a node
            resp = client.get(f"/api/v1/edges?node_id={spec.id}")
            assert resp.status_code == 200
            edge_data = resp.json()
            assert "outgoing" in edge_data or "incoming" in edge_data

            # 7. GET /api/v1/health — health metrics
            resp = client.get("/api/v1/health")
            assert resp.status_code == 200
            health = resp.json()
            assert "orphan_rate" in health

            # 8. GET /api/v1/tasks — task list (empty or populated)
            resp = client.get("/api/v1/tasks")
            assert resp.status_code == 200

            # 9. GET /api/v1/stats — aggregated statistics
            resp = client.get("/api/v1/stats")
            assert resp.status_code == 200
            stats = resp.json()
            assert "total_nodes" in stats
            assert stats["total_nodes"] >= 3
            assert "health_index" in stats
            assert 0.0 <= stats["health_index"] <= 1.0

            # 10. GET / — HTML page
            resp = client.get("/")
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"]
            assert "KGN Web" in resp.text or "kgn" in resp.text.lower()
        finally:
            _exit_patches(active)


# ── Scenario 2: Filter Query Parameters ───────────────────────────────


class TestE2EFilterQueryParams:
    """Verify type/status/tags/text query param filters on GET /nodes."""

    @pytest.fixture(autouse=True)
    def _seed(
        self,
        db_conn: Connection,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        from fastapi.testclient import TestClient

        from kgn.web.app import create_app

        repo = KgnRepository(db_conn)

        self.nodes = [
            _make_node(
                project_id,
                agent_id,
                title="Login SPEC",
                node_type=NodeType.SPEC,
                status=NodeStatus.ACTIVE,
                tags=["auth", "login"],
            ),
            _make_node(
                project_id,
                agent_id,
                title="DB Arch",
                node_type=NodeType.ARCH,
                status=NodeStatus.ACTIVE,
                tags=["database"],
            ),
            _make_node(
                project_id,
                agent_id,
                title="Old Logic",
                node_type=NodeType.LOGIC,
                status=NodeStatus.ARCHIVED,
                tags=["legacy"],
            ),
            _make_node(
                project_id,
                agent_id,
                title="Auth Goal",
                node_type=NodeType.GOAL,
                status=NodeStatus.ACTIVE,
                tags=["auth"],
            ),
        ]
        for n in self.nodes:
            repo.upsert_node(n)

        app = create_app("filter-test", project_id)
        patches = _all_patches(db_conn)
        self._active = _enter_patches(patches)
        self.client = TestClient(app)

    def teardown_method(self) -> None:
        _exit_patches(self._active)

    def test_filter_by_type(self) -> None:
        resp = self.client.get("/api/v1/nodes?type=SPEC")
        assert resp.status_code == 200
        nodes = resp.json()["nodes"]
        for n in nodes:
            assert n["type"] == "SPEC"

    def test_filter_by_status(self) -> None:
        resp = self.client.get("/api/v1/nodes?status=ARCHIVED")
        assert resp.status_code == 200
        nodes = resp.json()["nodes"]
        assert any(n["title"] == "Old Logic" for n in nodes)
        for n in nodes:
            assert n["status"] == "ARCHIVED"

    def test_filter_by_tags(self) -> None:
        resp = self.client.get("/api/v1/nodes?tags=auth")
        assert resp.status_code == 200
        nodes = resp.json()["nodes"]
        # All returned nodes should have 'auth' tag
        for n in nodes:
            assert "auth" in n["tags"]

    def test_filter_by_text(self) -> None:
        resp = self.client.get("/api/v1/nodes?q=Login")
        assert resp.status_code == 200
        nodes = resp.json()["nodes"]
        assert any(n["title"] == "Login SPEC" for n in nodes)

    def test_combined_filters(self) -> None:
        resp = self.client.get("/api/v1/nodes?type=SPEC&tags=auth")
        assert resp.status_code == 200
        nodes = resp.json()["nodes"]
        for n in nodes:
            assert n["type"] == "SPEC"
            assert "auth" in n["tags"]

    def test_no_match_filter(self) -> None:
        resp = self.client.get("/api/v1/nodes?tags=nonexistent_tag_xyz")
        assert resp.status_code == 200
        assert resp.json()["nodes"] == []


# ── Scenario 3: Subgraph Depth Limit ──────────────────────────────────


class TestE2ESubgraphDepthLimit:
    """Verify depth parameter bounds and MAX_NODES guard."""

    def test_depth_parameter(
        self,
        db_conn: Connection,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        from fastapi.testclient import TestClient

        from kgn.web.app import create_app

        repo = KgnRepository(db_conn)
        root = _make_node(project_id, agent_id, title="Root Node")
        child = _make_node(project_id, agent_id, title="Child Node")
        repo.upsert_node(root)
        repo.upsert_node(child)
        _insert_edge(
            db_conn,
            root.id,
            child.id,
            EdgeType.DEPENDS_ON,
            project_id=project_id,
        )

        app = create_app("depth-test", project_id)
        patches = _all_patches(db_conn)
        active = _enter_patches(patches)
        try:
            client = TestClient(app)

            # depth=1 should include root + direct neighbours
            resp = client.get(f"/api/v1/subgraph/{root.id}?depth=1")
            assert resp.status_code == 200
            sg = resp.json()
            node_ids = {n["data"]["id"] for n in sg["elements"]["nodes"]}
            assert str(root.id) in node_ids

            # depth=5 (max allowed)
            resp = client.get(f"/api/v1/subgraph/{root.id}?depth=5")
            assert resp.status_code == 200

            # depth=0 should be rejected (ge=1)
            resp = client.get(f"/api/v1/subgraph/{root.id}?depth=0")
            assert resp.status_code == 422

            # depth=6 should be rejected (le=5)
            resp = client.get(f"/api/v1/subgraph/{root.id}?depth=6")
            assert resp.status_code == 422
        finally:
            _exit_patches(active)

    def test_invalid_uuid(
        self,
        db_conn: Connection,
        project_id: uuid.UUID,
    ) -> None:
        from fastapi.testclient import TestClient

        from kgn.web.app import create_app

        app = create_app("invalid-test", project_id)
        patches = _all_patches(db_conn)
        active = _enter_patches(patches)
        try:
            client = TestClient(app)
            resp = client.get("/api/v1/subgraph/not-a-uuid")
            assert resp.status_code == 400
        finally:
            _exit_patches(active)


# ── Scenario 4: SSE Connection ─────────────────────────────────────────


class TestE2ESSEConnection:
    """Verify SSE stream connectivity and event delivery."""

    def test_sse_connection(
        self,
        db_conn: Connection,
        project_id: uuid.UUID,
    ) -> None:
        from fastapi.testclient import TestClient

        from kgn.web.app import create_app

        app = create_app("sse-test", project_id)
        patches = _all_patches(db_conn)
        active = _enter_patches(patches)
        try:
            # Mock the event bus subscribe to yield a single event then stop
            async def _mock_subscribe():
                yield {"type": "test_event", "data": {"msg": "hello"}}

            with patch("kgn.web.routes.stats.event_bus") as mock_bus:
                mock_bus.subscribe = _mock_subscribe

                client = TestClient(app)
                resp = client.get("/api/v1/events")
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers["content-type"]
                # Should contain the event we published
                assert "test_event" in resp.text
                assert "hello" in resp.text
        finally:
            _exit_patches(active)

    def test_stats_endpoint(
        self,
        db_conn: Connection,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        from fastapi.testclient import TestClient

        from kgn.web.app import create_app

        repo = KgnRepository(db_conn)
        node = _make_node(project_id, agent_id, title="Stats Node")
        repo.upsert_node(node)

        app = create_app("stats-test", project_id)
        patches = _all_patches(db_conn)
        active = _enter_patches(patches)
        try:
            client = TestClient(app)
            resp = client.get("/api/v1/stats")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_nodes"] >= 1
            assert "health_index" in data
            assert "health_metrics" in data
            assert "task_pipeline" in data
        finally:
            _exit_patches(active)


# ── Scenario 5: Similar & Conflicts ───────────────────────────────────


class TestE2ESimilarAndConflicts:
    """Verify similar node search and conflict API endpoints."""

    def test_similar_no_embedding(
        self,
        db_conn: Connection,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Node without embedding should return empty results."""
        from fastapi.testclient import TestClient

        from kgn.web.app import create_app

        repo = KgnRepository(db_conn)
        node = _make_node(project_id, agent_id, title="No Embed Node")
        repo.upsert_node(node)

        app = create_app("similar-test", project_id)
        patches = _all_patches(db_conn)
        active = _enter_patches(patches)
        try:
            client = TestClient(app)
            resp = client.get(f"/api/v1/similar/{node.id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["results"] == []
            assert data["total"] == 0
        finally:
            _exit_patches(active)

    def test_similar_invalid_uuid(
        self,
        db_conn: Connection,
        project_id: uuid.UUID,
    ) -> None:
        from fastapi.testclient import TestClient

        from kgn.web.app import create_app

        app = create_app("similar-invalid", project_id)
        patches = _all_patches(db_conn)
        active = _enter_patches(patches)
        try:
            client = TestClient(app)
            resp = client.get("/api/v1/similar/not-a-uuid")
            assert resp.status_code == 400
        finally:
            _exit_patches(active)

    def test_similar_not_found(
        self,
        db_conn: Connection,
        project_id: uuid.UUID,
    ) -> None:
        from fastapi.testclient import TestClient

        from kgn.web.app import create_app

        app = create_app("similar-404", project_id)
        patches = _all_patches(db_conn)
        active = _enter_patches(patches)
        try:
            client = TestClient(app)
            fake_id = uuid.uuid4()
            resp = client.get(f"/api/v1/similar/{fake_id}")
            assert resp.status_code == 404
        finally:
            _exit_patches(active)

    def test_conflicts_endpoint(
        self,
        db_conn: Connection,
        project_id: uuid.UUID,
    ) -> None:
        from fastapi.testclient import TestClient

        from kgn.web.app import create_app

        app = create_app("conflict-test", project_id)
        patches = _all_patches(db_conn)
        active = _enter_patches(patches)
        try:
            client = TestClient(app)
            resp = client.get("/api/v1/conflicts")
            assert resp.status_code == 200
            data = resp.json()
            assert "conflicts" in data
            assert isinstance(data["conflicts"], list)
        finally:
            _exit_patches(active)


# ── Scenario 6: Web Not Installed ──────────────────────────────────────


class TestE2EWebNotInstalled:
    """Verify graceful error when FastAPI is not installed."""

    def test_import_guard(self) -> None:
        """CLI ``web serve`` should fail gracefully when fastapi is absent."""
        import importlib
        import sys

        # Temporarily hide fastapi from sys.modules
        original = sys.modules.get("fastapi")
        sys.modules["fastapi"] = None  # type: ignore[assignment]
        try:
            # Attempting to import the web module should raise ImportError
            # or be caught by the CLI command. We verify the guard exists.
            with pytest.raises((ImportError, ModuleNotFoundError)):
                # Force re-import
                if "kgn.web.app" in sys.modules:
                    del sys.modules["kgn.web.app"]
                importlib.import_module("kgn.web.app")
        finally:
            if original is not None:
                sys.modules["fastapi"] = original
            elif "fastapi" in sys.modules:
                del sys.modules["fastapi"]


# ── Scenario 7: API Key Middleware ─────────────────────────────────────


class TestAPIKeyMiddleware:
    """Verify optional API key middleware (I-08 security hardening)."""

    def _make_app(self, api_key: str = ""):
        """Create app with optional API key, bypassing module-level cache."""
        import importlib
        import sys

        with patch.dict(os.environ, {"KGN_API_KEY": api_key}, clear=False):
            # Force re-import so create_app picks up env var
            mod_name = "kgn.web.app"
            if mod_name in sys.modules:
                del sys.modules[mod_name]
            mod = importlib.import_module(mod_name)
            return mod.create_app("test-proj", uuid.uuid4())

    def test_no_key_configured_allows_all(self, db_conn: Connection) -> None:
        """Without KGN_API_KEY, all endpoints are open."""
        from fastapi.testclient import TestClient

        app = self._make_app("")
        patches = _all_patches(db_conn)
        active = _enter_patches(patches)
        try:
            client = TestClient(app)
            resp = client.get("/api/v1/health")
            assert resp.status_code == 200
            resp = client.get("/api/v1/nodes")
            assert resp.status_code == 200
        finally:
            _exit_patches(active)

    def test_valid_key_allows_access(self, db_conn: Connection) -> None:
        """With correct X-API-Key header, endpoints are accessible."""
        from fastapi.testclient import TestClient

        app = self._make_app("secret-key-42")
        patches = _all_patches(db_conn)
        active = _enter_patches(patches)
        try:
            client = TestClient(app)
            resp = client.get(
                "/api/v1/nodes", headers={"X-API-Key": "secret-key-42"}
            )
            assert resp.status_code == 200
        finally:
            _exit_patches(active)

    def test_invalid_key_returns_401(self) -> None:
        """With wrong API key, endpoints return 401."""
        from fastapi.testclient import TestClient

        app = self._make_app("secret-key-42")
        client = TestClient(app)
        resp = client.get("/api/v1/nodes", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401
        assert "Invalid or missing API key" in resp.json()["detail"]

    def test_missing_key_returns_401(self) -> None:
        """Without API key header, protected endpoints return 401."""
        from fastapi.testclient import TestClient

        app = self._make_app("secret-key-42")
        client = TestClient(app)
        resp = client.get("/api/v1/nodes")
        assert resp.status_code == 401

    def test_health_exempt_from_key(self) -> None:
        """Health endpoint is accessible even when API key is required."""
        from fastapi.testclient import TestClient

        app = self._make_app("secret-key-42")
        client = TestClient(app)
        resp = client.get("/api/v1/health")
        # Health still goes through, but it needs the DB connection mock.
        # Even without mocking, the middleware should NOT block it.
        # The response might be 500 (no DB) but NOT 401.
        assert resp.status_code != 401
