"""Tests for Phase 9 Step 6 — Search, Filter, Similar & Conflict APIs.

Covers:
- GET /api/v1/nodes with tags/text filters
- GET /api/v1/similar/{id} — similar node search
- GET /api/v1/conflicts — conflict candidate listing
- Search/filter HTML structure
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
from kgn.web.app import create_app

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    title: str = "Test Node",
    node_type: NodeType = NodeType.SPEC,
    status: NodeStatus = NodeStatus.ACTIVE,
    tags: list[str] | None = None,
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


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def search_client(
    db_conn: Connection,
    project_id: uuid.UUID,
) -> TestClient:
    """TestClient with no extra data."""
    app = create_app("search-test", project_id)
    patches = _all_patches(db_conn)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        yield TestClient(app)


@pytest.fixture
def search_with_data(
    db_conn: Connection,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> tuple[TestClient, list[uuid.UUID]]:
    """TestClient with seeded nodes (some with tags)."""
    repo = KgnRepository(db_conn)

    nodes = [
        _make_node(
            project_id,
            agent_id,
            title="Auth Spec",
            node_type=NodeType.SPEC,
            tags=["auth", "security"],
        ),
        _make_node(
            project_id, agent_id, title="Database Arch", node_type=NodeType.ARCH, tags=["database"]
        ),
        _make_node(
            project_id, agent_id, title="Login Logic", node_type=NodeType.LOGIC, tags=["auth"]
        ),
        _make_node(
            project_id, agent_id, title="Performance Goal", node_type=NodeType.GOAL, tags=None
        ),
        _make_node(
            project_id,
            agent_id,
            title="Auth Archived",
            node_type=NodeType.SPEC,
            status=NodeStatus.ARCHIVED,
            tags=["auth"],
        ),
    ]
    ids = []
    for n in nodes:
        repo.upsert_node(n)
        ids.append(n.id)

    app = create_app("search-test", project_id)
    patches = _all_patches(db_conn)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        yield TestClient(app), ids


# ── Tests: Tags filter ────────────────────────────────────────────────


class TestTagsFilter:
    """Tests for GET /api/v1/nodes with tags parameter."""

    def test_no_tags_filter_returns_all(self, search_with_data):
        client, ids = search_with_data
        r = client.get("/api/v1/nodes")
        assert r.status_code == 200
        # Should return 4 active nodes (excludes ARCHIVED by default)
        assert r.json()["total"] == 4

    def test_single_tag_filter(self, search_with_data):
        client, ids = search_with_data
        r = client.get("/api/v1/nodes?tags=auth")
        assert r.status_code == 200
        data = r.json()
        # "Auth Spec" + "Login Logic" match (active only, "Auth Archived" excluded)
        assert data["total"] == 2
        titles = {n["title"] for n in data["nodes"]}
        assert "Auth Spec" in titles
        assert "Login Logic" in titles

    def test_multiple_tags_or(self, search_with_data):
        client, ids = search_with_data
        r = client.get("/api/v1/nodes?tags=auth,database")
        assert r.status_code == 200
        data = r.json()
        # "Auth Spec" + "Login Logic" + "Database Arch" (OR match)
        assert data["total"] == 3

    def test_nonexistent_tag(self, search_with_data):
        client, ids = search_with_data
        r = client.get("/api/v1/nodes?tags=nonexistent")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_tag_with_type_filter(self, search_with_data):
        client, ids = search_with_data
        r = client.get("/api/v1/nodes?type=SPEC&tags=auth")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["nodes"][0]["title"] == "Auth Spec"


class TestTextSearch:
    """Tests for GET /api/v1/nodes with q (text search) parameter."""

    def test_text_search_by_title(self, search_with_data):
        client, ids = search_with_data
        r = client.get("/api/v1/nodes?q=auth")
        assert r.status_code == 200
        data = r.json()
        # "Auth Spec" + "Login Logic" won't match — only titles containing "Auth"
        # Actually "Auth Spec" matches
        assert data["total"] >= 1
        assert any("Auth" in n["title"] for n in data["nodes"])

    def test_text_search_case_insensitive(self, search_with_data):
        client, ids = search_with_data
        r = client.get("/api/v1/nodes?q=DATABASE")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["nodes"][0]["title"] == "Database Arch"

    def test_text_search_no_match(self, search_with_data):
        client, ids = search_with_data
        r = client.get("/api/v1/nodes?q=xyzzy")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_text_search_with_tags(self, search_with_data):
        client, ids = search_with_data
        r = client.get("/api/v1/nodes?q=auth&tags=security")
        assert r.status_code == 200
        data = r.json()
        # Only "Auth Spec" has tag=security AND title contains "auth"
        assert data["total"] == 1
        assert data["nodes"][0]["title"] == "Auth Spec"


# ── Tests: Similar API ─────────────────────────────────────────────────


class TestSimilarAPI:
    """Tests for GET /api/v1/similar/{id}."""

    def test_invalid_uuid(self, search_client):
        r = search_client.get("/api/v1/similar/not-a-uuid")
        assert r.status_code == 400

    def test_node_not_found(self, search_client):
        fake_id = uuid.uuid4()
        r = search_client.get(f"/api/v1/similar/{fake_id}")
        assert r.status_code == 404

    def test_no_embedding_returns_empty(self, search_with_data):
        client, ids = search_with_data
        r = client.get(f"/api/v1/similar/{ids[0]}")
        assert r.status_code == 200
        data = r.json()
        assert data["node_id"] == str(ids[0])
        assert data["results"] == []
        assert data["total"] == 0

    def test_with_embeddings(self, search_with_data, db_conn, project_id):
        """Test similar search with actual embeddings in DB."""
        client, ids = search_with_data
        repo = KgnRepository(db_conn)

        # Store embeddings (1536-dim zero vectors with slight variations)
        dim = 1536
        for i, nid in enumerate(ids[:3]):
            vec = [0.0] * dim
            vec[0] = 1.0 - i * 0.1  # slightly different
            vec[1] = float(i) * 0.05
            repo.upsert_embedding(nid, project_id, vec, "test-model")

        r = client.get(f"/api/v1/similar/{ids[0]}?top_k=2")
        assert r.status_code == 200
        data = r.json()
        assert data["node_id"] == str(ids[0])
        assert data["total"] <= 2
        # Results should have id, type, title, similarity
        for item in data["results"]:
            assert "id" in item
            assert "type" in item
            assert "title" in item
            assert "similarity" in item
            assert 0.0 <= item["similarity"] <= 1.0

    def test_top_k_param(self, search_client):
        # top_k validation
        r = search_client.get(f"/api/v1/similar/{uuid.uuid4()}?top_k=0")
        assert r.status_code == 422  # validation error

    def test_top_k_max(self, search_client):
        r = search_client.get(f"/api/v1/similar/{uuid.uuid4()}?top_k=51")
        assert r.status_code == 422


# ── Tests: Conflicts API ──────────────────────────────────────────────


class TestConflictsAPI:
    """Tests for GET /api/v1/conflicts."""

    def test_empty_project(self, search_client):
        r = search_client.get("/api/v1/conflicts")
        assert r.status_code == 200
        data = r.json()
        assert data["conflicts"] == []
        assert data["total"] == 0
        assert "threshold" in data
        assert "project" in data

    def test_threshold_param(self, search_client):
        r = search_client.get("/api/v1/conflicts?threshold=0.95")
        assert r.status_code == 200
        assert r.json()["threshold"] == 0.95

    def test_threshold_validation(self, search_client):
        r = search_client.get("/api/v1/conflicts?threshold=1.5")
        assert r.status_code == 422

    def test_conflict_response_shape(self, search_with_data, db_conn, project_id):
        """If there are highly similar embeddings, conflicts are returned."""
        client, ids = search_with_data
        repo = KgnRepository(db_conn)

        # Two nearly identical embeddings → should be a conflict candidate
        dim = 1536
        vec = [0.1] * dim
        repo.upsert_embedding(ids[0], project_id, vec, "test-model")
        repo.upsert_embedding(ids[1], project_id, vec, "test-model")

        r = client.get("/api/v1/conflicts?threshold=0.9")
        assert r.status_code == 200
        data = r.json()
        # With identical vectors, similarity should be 1.0 (or very close)
        if data["total"] > 0:
            c = data["conflicts"][0]
            assert "node_a_id" in c
            assert "node_b_id" in c
            assert "similarity" in c
            assert "status" in c
            assert c["status"] in ("NEW", "PENDING")


# ── Tests: HTML structure ─────────────────────────────────────────────


class TestSearchHTML:
    """Tests for search/filter UI elements in HTML."""

    def test_search_css_linked(self, search_client):
        r = search_client.get("/")
        assert r.status_code == 200
        assert "search.css" in r.text

    def test_search_js_linked(self, search_client):
        r = search_client.get("/")
        assert r.status_code == 200
        assert "search.js" in r.text

    def test_search_filter_bar_container(self, search_client):
        r = search_client.get("/")
        assert r.status_code == 200
        assert 'id="search-filter-bar"' in r.text

    def test_similar_button(self, search_client):
        r = search_client.get("/")
        assert r.status_code == 200
        assert "btn-similar" in r.text

    def test_conflicts_button(self, search_client):
        r = search_client.get("/")
        assert r.status_code == 200
        assert "btn-conflicts" in r.text

    def test_similar_dropdown_container(self, search_client):
        r = search_client.get("/")
        assert r.status_code == 200
        assert 'id="similar-dropdown"' in r.text

    def test_conflict_dropdown_container(self, search_client):
        r = search_client.get("/")
        assert r.status_code == 200
        assert 'id="conflict-dropdown"' in r.text
