"""Tests for Phase 9 Step 3 — Node Detail Panel + Edges API.

Covers:
- GET /api/v1/edges?node_id={id} — incoming/outgoing edge lists
- Edge data includes peer_title for convenient display
- 400 for invalid UUID / missing param
- 404 for non-existent node
- Body Markdown present in GET /api/v1/nodes/{id}
- HTML references: detail-panel.js, detail.css, marked.js CDN, #detail-edges
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
from kgn.models.edge import EdgeRecord
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    title: str = "Test Node",
    node_type: NodeType = NodeType.SPEC,
    status: NodeStatus = NodeStatus.ACTIVE,
    body_md: str = "",
    tags: list[str] | None = None,
    confidence: float | None = None,
    node_id: uuid.UUID | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=node_id or uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=status,
        title=title,
        body_md=body_md or f"Body of {title}",
        content_hash=uuid.uuid4().hex,
        tags=tags or [],
        confidence=confidence,
        created_by=agent_id,
        created_at=datetime.now(tz=UTC),
    )


def _make_edge(
    project_id: uuid.UUID,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    *,
    edge_type: EdgeType = EdgeType.DEPENDS_ON,
    note: str = "",
    agent_id: uuid.UUID | None = None,
) -> EdgeRecord:
    return EdgeRecord(
        project_id=project_id,
        from_node_id=from_id,
        to_node_id=to_id,
        type=edge_type,
        note=note,
        created_by=agent_id,
    )


@contextmanager
def _mock_connection(db_conn: Connection) -> Generator[Connection, None, None]:
    yield db_conn


def _all_patches(db_conn: Connection):
    """Return a combined context manager that patches get_connection for all routes."""
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
def detail_client(
    db_conn: Connection,
    project_id: uuid.UUID,
) -> TestClient:
    """TestClient for the web app with get_connection mocked (no data)."""
    from kgn.web.app import create_app

    app = create_app(project_name="test-detail", project_id=project_id)

    patches = _all_patches(db_conn)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        yield TestClient(app)


@pytest.fixture
def detail_with_data(
    db_conn: Connection,
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> tuple[TestClient, dict[str, uuid.UUID]]:
    """TestClient with a graph: A -> B -> C, D -> A.

    A (GOAL): root with markdown body, incoming from D, outgoing to B
    B (SPEC): child of A, outgoing to C
    C (LOGIC): leaf grandchild of A
    D (DECISION): points to A
    """
    a = _make_node(
        project_id,
        agent_id,
        title="Auth System",
        node_type=NodeType.GOAL,
        body_md="## Context\nUser authentication.\n\n## Content\n- OAuth2 support\n- JWT tokens",
        tags=["auth", "security"],
        confidence=0.92,
    )
    b = _make_node(
        project_id,
        agent_id,
        title="Auth Spec",
        node_type=NodeType.SPEC,
        body_md="Specification for auth.",
    )
    c = _make_node(
        project_id,
        agent_id,
        title="Token Logic",
        node_type=NodeType.LOGIC,
        body_md="JWT validation logic.",
    )
    d = _make_node(
        project_id,
        agent_id,
        title="Use OAuth Decision",
        node_type=NodeType.DECISION,
        body_md="Decision to use OAuth2.",
    )

    for n in (a, b, c, d):
        repo.upsert_node(n)

    repo.insert_edge(
        _make_edge(
            project_id,
            a.id,
            b.id,
            edge_type=EdgeType.IMPLEMENTS,
            note="spec impl",
            agent_id=agent_id,
        )
    )
    repo.insert_edge(
        _make_edge(project_id, b.id, c.id, edge_type=EdgeType.DEPENDS_ON, agent_id=agent_id)
    )
    repo.insert_edge(
        _make_edge(
            project_id,
            d.id,
            a.id,
            edge_type=EdgeType.DERIVED_FROM,
            note="derived",
            agent_id=agent_id,
        )
    )

    from kgn.web.app import create_app

    app = create_app(project_name="test-detail", project_id=project_id)

    ids = {"a": a.id, "b": b.id, "c": c.id, "d": d.id}

    patches = _all_patches(db_conn)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        yield TestClient(app), ids


# ── Edges API tests ───────────────────────────────────────────────────


class TestEdgesAPI:
    """GET /api/v1/edges?node_id={id} tests."""

    def test_missing_node_id_returns_422(self, detail_client: TestClient) -> None:
        """node_id is required query param."""
        resp = detail_client.get("/api/v1/edges")
        assert resp.status_code == 422

    def test_invalid_uuid_returns_400(self, detail_client: TestClient) -> None:
        resp = detail_client.get("/api/v1/edges?node_id=not-a-uuid")
        assert resp.status_code == 400
        assert "Invalid UUID" in resp.json()["detail"]

    def test_not_found_returns_404(self, detail_client: TestClient) -> None:
        fake_id = uuid.uuid4()
        resp = detail_client.get(f"/api/v1/edges?node_id={fake_id}")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_returns_incoming_and_outgoing(
        self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Node A should have 1 incoming (from D) and 1 outgoing (to B)."""
        client, ids = detail_with_data
        resp = client.get(f"/api/v1/edges?node_id={ids['a']}")
        assert resp.status_code == 200

        data = resp.json()
        assert data["node_id"] == str(ids["a"])
        assert len(data["incoming"]) == 1
        assert len(data["outgoing"]) == 1
        assert data["total"] == 2

    def test_incoming_edge_structure(
        self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Incoming edge has from_node_id, to_node_id, type, peer_title."""
        client, ids = detail_with_data
        resp = client.get(f"/api/v1/edges?node_id={ids['a']}")
        edge = resp.json()["incoming"][0]

        assert edge["from_node_id"] == str(ids["d"])
        assert edge["to_node_id"] == str(ids["a"])
        assert edge["type"] == "DERIVED_FROM"
        assert "peer_title" in edge
        assert edge["peer_title"] == "Use OAuth Decision"

    def test_outgoing_edge_structure(
        self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Outgoing edge has peer_title pointing to the to_node."""
        client, ids = detail_with_data
        resp = client.get(f"/api/v1/edges?node_id={ids['a']}")
        edge = resp.json()["outgoing"][0]

        assert edge["from_node_id"] == str(ids["a"])
        assert edge["to_node_id"] == str(ids["b"])
        assert edge["type"] == "IMPLEMENTS"
        assert edge["peer_title"] == "Auth Spec"

    def test_edge_note_included(
        self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Edge notes should be present in the response."""
        client, ids = detail_with_data
        resp = client.get(f"/api/v1/edges?node_id={ids['a']}")
        outgoing = resp.json()["outgoing"][0]
        assert outgoing["note"] == "spec impl"

        incoming = resp.json()["incoming"][0]
        assert incoming["note"] == "derived"

    def test_leaf_node_no_outgoing(
        self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Leaf node C should have 1 incoming, 0 outgoing."""
        client, ids = detail_with_data
        resp = client.get(f"/api/v1/edges?node_id={ids['c']}")
        data = resp.json()

        assert len(data["incoming"]) == 1
        assert len(data["outgoing"]) == 0
        assert data["total"] == 1

    def test_node_b_has_both_directions(
        self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Node B: incoming from A, outgoing to C."""
        client, ids = detail_with_data
        resp = client.get(f"/api/v1/edges?node_id={ids['b']}")
        data = resp.json()

        assert len(data["incoming"]) == 1
        assert len(data["outgoing"]) == 1
        assert data["incoming"][0]["from_node_id"] == str(ids["a"])
        assert data["outgoing"][0]["to_node_id"] == str(ids["c"])

    def test_edge_has_created_at(
        self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Edges should contain created_at timestamp."""
        client, ids = detail_with_data
        resp = client.get(f"/api/v1/edges?node_id={ids['a']}")
        edge = resp.json()["incoming"][0]
        assert "created_at" in edge

    def test_edge_has_id(self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]) -> None:
        """Edge records should have integer id."""
        client, ids = detail_with_data
        resp = client.get(f"/api/v1/edges?node_id={ids['a']}")
        edge = resp.json()["incoming"][0]
        assert isinstance(edge["id"], int)


# ── Node detail body tests ────────────────────────────────────────────


class TestNodeDetailBody:
    """GET /api/v1/nodes/{id} body_md tests."""

    def test_body_md_included(
        self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Node detail should include body_md field."""
        client, ids = detail_with_data
        resp = client.get(f"/api/v1/nodes/{ids['a']}")
        assert resp.status_code == 200

        node = resp.json()
        assert "body_md" in node
        assert "## Context" in node["body_md"]
        assert "OAuth2 support" in node["body_md"]

    def test_tags_and_confidence(
        self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Node detail should have tags and confidence for display."""
        client, ids = detail_with_data
        resp = client.get(f"/api/v1/nodes/{ids['a']}")
        node = resp.json()

        assert node["tags"] == ["auth", "security"]
        assert node["confidence"] == 0.92


# ── Index HTML detail panel tests ─────────────────────────────────────


class TestIndexDetailPanel:
    """HTML structure tests for the detail panel."""

    def test_detail_css_linked(
        self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Index page should link detail.css."""
        client, _ = detail_with_data
        resp = client.get("/")
        assert "detail.css" in resp.text

    def test_marked_js_cdn(self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]) -> None:
        """Index page should include marked.js CDN for Markdown rendering."""
        client, _ = detail_with_data
        resp = client.get("/")
        assert "marked" in resp.text.lower()

    def test_detail_panel_js(
        self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Index page should include detail-panel.js script."""
        client, _ = detail_with_data
        resp = client.get("/")
        assert "detail-panel.js" in resp.text

    def test_detail_edges_section(
        self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Detail panel should have #detail-edges container."""
        client, _ = detail_with_data
        resp = client.get("/")
        assert 'id="detail-edges"' in resp.text

    def test_detail_body_section(
        self, detail_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Detail panel should have #detail-body container."""
        client, _ = detail_with_data
        resp = client.get("/")
        assert 'id="detail-body"' in resp.text
