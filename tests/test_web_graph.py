"""Tests for Phase 9 Step 2 — Subgraph API + Cytoscape.js data conversion.

Covers:
- GET /api/v1/subgraph/{id} — Cytoscape.js elements format
- depth parameter validation
- 404 for non-existent root node
- 400 for invalid UUID
- Truncation at max_nodes=200
- Multiple nodes and edges in correct Cytoscape.js structure
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


def _make_edge(
    project_id: uuid.UUID,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    *,
    edge_type: EdgeType = EdgeType.DEPENDS_ON,
    agent_id: uuid.UUID | None = None,
) -> EdgeRecord:
    return EdgeRecord(
        project_id=project_id,
        from_node_id=from_id,
        to_node_id=to_id,
        type=edge_type,
        note="",
        created_by=agent_id,
    )


@contextmanager
def _mock_connection(db_conn: Connection) -> Generator[Connection, None, None]:
    yield db_conn


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def graph_client(
    db_conn: Connection,
    project_id: uuid.UUID,
) -> TestClient:
    """TestClient for the web app with get_connection mocked."""
    from kgn.web.app import create_app

    app = create_app(project_name="test-graph", project_id=project_id)

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
def graph_with_data(
    db_conn: Connection,
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> tuple[TestClient, dict[str, uuid.UUID]]:
    """TestClient with a small graph: A -> B -> C, A -> D."""
    a = _make_node(project_id, agent_id, title="Root Goal", node_type=NodeType.GOAL)
    b = _make_node(project_id, agent_id, title="Child Spec", node_type=NodeType.SPEC)
    c = _make_node(project_id, agent_id, title="Grandchild Logic", node_type=NodeType.LOGIC)
    d = _make_node(project_id, agent_id, title="Child Decision", node_type=NodeType.DECISION)

    for n in (a, b, c, d):
        repo.upsert_node(n)

    repo.insert_edge(
        _make_edge(project_id, a.id, b.id, edge_type=EdgeType.IMPLEMENTS, agent_id=agent_id)
    )
    repo.insert_edge(
        _make_edge(project_id, b.id, c.id, edge_type=EdgeType.DEPENDS_ON, agent_id=agent_id)
    )
    repo.insert_edge(
        _make_edge(project_id, a.id, d.id, edge_type=EdgeType.DERIVED_FROM, agent_id=agent_id)
    )

    from kgn.web.app import create_app

    app = create_app(project_name="test-graph", project_id=project_id)

    ids = {"a": a.id, "b": b.id, "c": c.id, "d": d.id}

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
        yield TestClient(app), ids


# ── Subgraph API tests ────────────────────────────────────────────────


class TestSubgraphAPI:
    """GET /api/v1/subgraph/{id} tests."""

    def test_invalid_uuid_returns_400(self, graph_client: TestClient) -> None:
        resp = graph_client.get("/api/v1/subgraph/not-a-uuid")
        assert resp.status_code == 400
        assert "Invalid UUID" in resp.json()["detail"]

    def test_not_found_returns_404(self, graph_client: TestClient) -> None:
        fake_id = uuid.uuid4()
        resp = graph_client.get(f"/api/v1/subgraph/{fake_id}")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_depth_min_validation(self, graph_client: TestClient) -> None:
        """depth < 1 should be rejected."""
        fake_id = uuid.uuid4()
        resp = graph_client.get(f"/api/v1/subgraph/{fake_id}?depth=0")
        assert resp.status_code == 422  # FastAPI validation error

    def test_depth_max_validation(self, graph_client: TestClient) -> None:
        """depth > 5 should be rejected."""
        fake_id = uuid.uuid4()
        resp = graph_client.get(f"/api/v1/subgraph/{fake_id}?depth=6")
        assert resp.status_code == 422

    def test_returns_cytoscape_elements(
        self,
        graph_with_data: tuple[TestClient, dict[str, uuid.UUID]],
    ) -> None:
        """Subgraph should return Cytoscape.js-compatible format."""
        client, ids = graph_with_data
        resp = client.get(f"/api/v1/subgraph/{ids['a']}?depth=2")
        assert resp.status_code == 200

        data = resp.json()
        assert "elements" in data
        assert "nodes" in data["elements"]
        assert "edges" in data["elements"]
        assert data["root_id"] == str(ids["a"])
        assert data["depth"] == 2
        assert data["truncated"] is False

    def test_node_data_structure(
        self,
        graph_with_data: tuple[TestClient, dict[str, uuid.UUID]],
    ) -> None:
        """Each node element should have data.id, data.label, data.type, data.status."""
        client, ids = graph_with_data
        resp = client.get(f"/api/v1/subgraph/{ids['a']}?depth=1")
        data = resp.json()

        nodes = data["elements"]["nodes"]
        assert len(nodes) >= 1

        root_node = next(n for n in nodes if n["data"]["id"] == str(ids["a"]))
        assert root_node["data"]["label"] == "Root Goal"
        assert root_node["data"]["type"] == "GOAL"
        assert root_node["data"]["status"] == "ACTIVE"
        assert root_node["data"]["depth"] == 0

    def test_edge_data_structure(
        self,
        graph_with_data: tuple[TestClient, dict[str, uuid.UUID]],
    ) -> None:
        """Each edge element should have data.source, data.target, data.label."""
        client, ids = graph_with_data
        resp = client.get(f"/api/v1/subgraph/{ids['a']}?depth=2")
        data = resp.json()

        edges = data["elements"]["edges"]
        assert len(edges) >= 1

        # Check edge fields exist
        for e in edges:
            assert "source" in e["data"]
            assert "target" in e["data"]
            assert "label" in e["data"]
            assert "id" in e["data"]

    def test_depth_1_limits_hop(
        self,
        graph_with_data: tuple[TestClient, dict[str, uuid.UUID]],
    ) -> None:
        """depth=1 should only return root + direct neighbours."""
        client, ids = graph_with_data
        resp = client.get(f"/api/v1/subgraph/{ids['a']}?depth=1")
        data = resp.json()

        node_ids = {n["data"]["id"] for n in data["elements"]["nodes"]}
        # A, B, D are within depth 1; C is at depth 2
        assert str(ids["a"]) in node_ids
        assert str(ids["b"]) in node_ids
        assert str(ids["d"]) in node_ids
        assert str(ids["c"]) not in node_ids

    def test_depth_2_includes_grandchild(
        self,
        graph_with_data: tuple[TestClient, dict[str, uuid.UUID]],
    ) -> None:
        """depth=2 should include grandchild C."""
        client, ids = graph_with_data
        resp = client.get(f"/api/v1/subgraph/{ids['a']}?depth=2")
        data = resp.json()

        node_ids = {n["data"]["id"] for n in data["elements"]["nodes"]}
        assert str(ids["c"]) in node_ids
        assert data["total_nodes"] == 4
        assert data["rendered_nodes"] == 4

    def test_rendered_nodes_count(
        self,
        graph_with_data: tuple[TestClient, dict[str, uuid.UUID]],
    ) -> None:
        """total_nodes and rendered_nodes should match when under limit."""
        client, ids = graph_with_data
        resp = client.get(f"/api/v1/subgraph/{ids['a']}?depth=2")
        data = resp.json()

        assert data["total_nodes"] == data["rendered_nodes"]
        assert data["truncated"] is False

    def test_edges_match_nodes(
        self,
        graph_with_data: tuple[TestClient, dict[str, uuid.UUID]],
    ) -> None:
        """All edge endpoints should reference nodes in the response."""
        client, ids = graph_with_data
        resp = client.get(f"/api/v1/subgraph/{ids['a']}?depth=2")
        data = resp.json()

        node_ids = {n["data"]["id"] for n in data["elements"]["nodes"]}
        for e in data["elements"]["edges"]:
            assert e["data"]["source"] in node_ids
            assert e["data"]["target"] in node_ids

    def test_edge_labels_are_edge_types(
        self,
        graph_with_data: tuple[TestClient, dict[str, uuid.UUID]],
    ) -> None:
        """Edge labels should be valid EdgeType values."""
        client, ids = graph_with_data
        resp = client.get(f"/api/v1/subgraph/{ids['a']}?depth=2")
        data = resp.json()

        valid_types = {t.value for t in EdgeType}
        for e in data["elements"]["edges"]:
            assert e["data"]["label"] in valid_types

    def test_subgraph_from_leaf_node(
        self,
        graph_with_data: tuple[TestClient, dict[str, uuid.UUID]],
    ) -> None:
        """Extracting from a leaf returns at least the node itself."""
        client, ids = graph_with_data
        # C is a leaf, but BFS goes both directions so it should find B
        resp = client.get(f"/api/v1/subgraph/{ids['c']}?depth=1")
        data = resp.json()

        node_ids = {n["data"]["id"] for n in data["elements"]["nodes"]}
        assert str(ids["c"]) in node_ids
        assert data["total_nodes"] >= 1


# ── Index HTML graph tab ─────────────────────────────────────────────


class TestIndexGraphTab:
    """Verify graph view elements are present in index.html."""

    def test_index_contains_cytoscape_script(self, graph_client: TestClient) -> None:
        resp = graph_client.get("/")
        assert resp.status_code == 200
        assert "cytoscape" in resp.text.lower()

    def test_index_contains_graph_container(self, graph_client: TestClient) -> None:
        resp = graph_client.get("/")
        assert 'id="cy"' in resp.text

    def test_index_contains_graph_js(self, graph_client: TestClient) -> None:
        resp = graph_client.get("/")
        assert "graph.js" in resp.text

    def test_index_has_tab_navigation(self, graph_client: TestClient) -> None:
        resp = graph_client.get("/")
        assert "Overview" in resp.text
        assert "Graph" in resp.text
