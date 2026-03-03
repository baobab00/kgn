"""Tests for kgn.lsp.subgraph_handler — Subgraph Preview data builder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from kgn.lsp.indexer import LocalGraph, NodeMeta
from kgn.lsp.subgraph_handler import (
    DEFAULT_DEPTH,
    DEFAULT_MAX_NODES,
    NODE_TYPE_COLOURS,
    _empty_response,
    build_response,
)
from kgn.models.edge import EdgeEntry
from kgn.models.enums import EdgeType, NodeStatus, NodeType

# ── Constants ────────────────────────────────────────────────────────

UUID_A = "550e8400-e29b-41d4-a716-446655440000"
UUID_B = "660e8400-e29b-41d4-a716-446655440111"
UUID_C = "770e8400-e29b-41d4-a716-446655440222"


def _meta(
    nid: str,
    ntype: NodeType = NodeType.SPEC,
    title: str = "Title",
    status: NodeStatus = NodeStatus.ACTIVE,
    slug: str = "slug",
) -> NodeMeta:
    """Create a NodeMeta fixture."""
    return NodeMeta(
        id=nid,
        slug=slug,
        type=ntype,
        title=title,
        status=status,
        confidence=0.9,
        path=Path(f"/ws/{slug}.kgn"),
    )


def _edge(from_n: str, to_n: str, etype: EdgeType = EdgeType.DEPENDS_ON) -> EdgeEntry:
    """Create an EdgeEntry fixture."""
    return EdgeEntry(**{"from": from_n, "to": to_n, "type": etype})


def _indexer_with(nodes: dict[str, NodeMeta], edges: list[EdgeEntry] | None = None) -> MagicMock:
    """Create a mock indexer returning given graph on build_local_subgraph."""
    graph = LocalGraph(nodes=nodes, edges=edges or [])
    idx = MagicMock()
    idx.build_local_subgraph.return_value = graph
    return idx


# ── TestBuildResponse ────────────────────────────────────────────────


class TestBuildResponse:
    """Tests for build_response()."""

    def test_basic_graph(self) -> None:
        """Single centre node, one neighbour, one edge."""
        nodes = {
            UUID_A: _meta(UUID_A, NodeType.GOAL, "Root Goal", slug="root"),
            UUID_B: _meta(UUID_B, NodeType.SPEC, "Child Spec", slug="child"),
        }
        edges = [_edge(UUID_A, UUID_B)]
        idx = _indexer_with(nodes, edges)

        result = build_response(UUID_A, idx)

        assert result["centre"] == UUID_A
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        assert result["truncated"] is False

        # Check node dict structure
        root = next(n for n in result["nodes"] if n["id"] == UUID_A)
        assert root["type"] == "GOAL"
        assert root["title"] == "Root Goal"
        assert root["colour"] == NODE_TYPE_COLOURS["GOAL"]
        assert root["slug"] == "root"

    def test_empty_node_id(self) -> None:
        """Empty node_id returns empty response without calling indexer."""
        idx = MagicMock()
        result = build_response("", idx)

        assert result["nodes"] == []
        assert result["edges"] == []
        assert result["truncated"] is False
        idx.build_local_subgraph.assert_not_called()

    def test_node_not_found(self) -> None:
        """When indexer returns empty graph, result is empty."""
        idx = _indexer_with({})
        result = build_response(UUID_A, idx)

        assert result["centre"] == UUID_A
        assert result["nodes"] == []
        assert result["edges"] == []
        assert result["truncated"] is False

    def test_depth_forwarded(self) -> None:
        """build_response passes depth parameter to indexer."""
        idx = _indexer_with({UUID_A: _meta(UUID_A)})
        build_response(UUID_A, idx, depth=5)
        idx.build_local_subgraph.assert_called_once_with(UUID_A, depth=5)

    def test_default_depth(self) -> None:
        """Default depth parameter is DEFAULT_DEPTH (2)."""
        idx = _indexer_with({UUID_A: _meta(UUID_A)})
        build_response(UUID_A, idx)
        idx.build_local_subgraph.assert_called_once_with(UUID_A, depth=DEFAULT_DEPTH)

    def test_max_nodes_truncation(self) -> None:
        """Graph with more nodes than max_nodes is truncated."""
        # Create 5 nodes, cap at 3
        nodes = {f"id-{i}": _meta(f"id-{i}", slug=f"s{i}") for i in range(5)}
        idx = _indexer_with(nodes)

        result = build_response("id-0", idx, max_nodes=3)

        assert len(result["nodes"]) == 3
        assert result["truncated"] is True

    def test_max_nodes_exact_boundary(self) -> None:
        """When node count equals max_nodes, truncated is False."""
        nodes = {f"id-{i}": _meta(f"id-{i}", slug=f"s{i}") for i in range(3)}
        idx = _indexer_with(nodes)

        result = build_response("id-0", idx, max_nodes=3)

        assert len(result["nodes"]) == 3
        assert result["truncated"] is False

    def test_edges_filtered_to_included_nodes(self) -> None:
        """Edges between excluded nodes are omitted."""
        nodes = {
            UUID_A: _meta(UUID_A, slug="a"),
            UUID_B: _meta(UUID_B, slug="b"),
            UUID_C: _meta(UUID_C, slug="c"),
        }
        edges = [
            _edge(UUID_A, UUID_B),  # both included (if max_nodes >= 2)
            _edge(UUID_B, UUID_C),  # UUID_C excluded when max_nodes=2
        ]
        idx = _indexer_with(nodes, edges)

        result = build_response(UUID_A, idx, max_nodes=2)

        # Only edge A→B should survive
        assert len(result["edges"]) <= 1
        edge_targets = {(e["from"], e["to"]) for e in result["edges"]}
        included_ids = {n["id"] for n in result["nodes"]}
        # All endpoints in edge list must be in included nodes
        for frm, to in edge_targets:
            assert frm in included_ids
            assert to in included_ids

    def test_edge_type_enum_serialised(self) -> None:
        """Edge type is serialised as string even if it's an Enum."""
        nodes = {
            UUID_A: _meta(UUID_A, slug="a"),
            UUID_B: _meta(UUID_B, slug="b"),
        }
        edges = [_edge(UUID_A, UUID_B, EdgeType.DEPENDS_ON)]
        idx = _indexer_with(nodes, edges)

        result = build_response(UUID_A, idx)

        assert result["edges"][0]["type"] == "DEPENDS_ON"
        assert isinstance(result["edges"][0]["type"], str)

    def test_unknown_type_gets_fallback_colour(self) -> None:
        """Node with type not in NODE_TYPE_COLOURS gets grey fallback."""
        _meta(UUID_A)
        # Patch the type name to something non-standard
        mock_type = MagicMock()
        mock_type.name = "UNKNOWN_CUSTOM"
        meta_patched = NodeMeta(
            id=UUID_A,
            slug="x",
            type=NodeType.SPEC,
            title="X",
            status=NodeStatus.ACTIVE,
            confidence=None,
            path=Path("/x.kgn"),
        )
        nodes = {UUID_A: meta_patched}
        idx = _indexer_with(nodes)
        # Override the type.name via a real mock
        mock_meta = MagicMock(spec=NodeMeta)
        mock_meta.type = mock_type
        mock_meta.title = "T"
        mock_meta.status = MagicMock()
        mock_meta.status.name = "ACTIVE"
        mock_meta.slug = "slug"
        mock_meta.path = Path("/x.kgn")
        idx.build_local_subgraph.return_value = LocalGraph(
            nodes={UUID_A: mock_meta},
            edges=[],
        )

        result = build_response(UUID_A, idx)

        assert result["nodes"][0]["colour"] == "#7F8C8D"

    def test_node_path_is_string(self) -> None:
        """Node path is serialised as a string, not Path object."""
        idx = _indexer_with({UUID_A: _meta(UUID_A)})
        result = build_response(UUID_A, idx)
        assert isinstance(result["nodes"][0]["path"], str)

    def test_new_slug_id(self) -> None:
        """new:slug style IDs work correctly."""
        nid = "new:auth-flow"
        idx = _indexer_with({nid: _meta(nid, slug="auth-flow")})
        result = build_response(nid, idx)

        assert result["centre"] == nid
        assert result["nodes"][0]["id"] == nid


# ── TestEmptyResponse ────────────────────────────────────────────────


class TestEmptyResponse:
    """Tests for _empty_response()."""

    def test_structure(self) -> None:
        result = _empty_response("abc")
        assert result == {
            "centre": "abc",
            "nodes": [],
            "edges": [],
            "truncated": False,
        }

    def test_empty_string_id(self) -> None:
        result = _empty_response("")
        assert result["centre"] == ""


# ── TestNodeTypeColours ──────────────────────────────────────────────


class TestNodeTypeColours:
    """Tests for NODE_TYPE_COLOURS completeness."""

    def test_all_node_types_covered(self) -> None:
        """Every NodeType enum member has a colour mapping."""
        for nt in NodeType:
            assert nt.name in NODE_TYPE_COLOURS, f"Missing colour for {nt.name}"

    def test_all_values_are_hex(self) -> None:
        """Every colour value is a valid hex colour string."""
        import re

        for name, colour in NODE_TYPE_COLOURS.items():
            assert re.match(r"^#[0-9A-Fa-f]{6}$", colour), (
                f"Invalid hex colour for {name}: {colour}"
            )

    def test_no_extra_entries(self) -> None:
        """No extra keys beyond NodeType members."""
        valid = {nt.name for nt in NodeType}
        extra = set(NODE_TYPE_COLOURS) - valid
        assert extra == set(), f"Extra colour keys: {extra}"


# ── TestConstants ────────────────────────────────────────────────────


class TestConstants:
    """Tests for module-level constants."""

    def test_default_depth(self) -> None:
        assert DEFAULT_DEPTH == 2

    def test_default_max_nodes(self) -> None:
        assert DEFAULT_MAX_NODES == 50


# ── TestMultiEdge ────────────────────────────────────────────────────


class TestMultiEdge:
    """Tests for graphs with multiple edges and edge types."""

    def test_multiple_edge_types(self) -> None:
        """Different edge types are preserved in response."""
        nodes = {
            UUID_A: _meta(UUID_A, slug="a"),
            UUID_B: _meta(UUID_B, slug="b"),
        }
        edges = [
            _edge(UUID_A, UUID_B, EdgeType.DEPENDS_ON),
            _edge(UUID_B, UUID_A, EdgeType.SUPERSEDES),
        ]
        idx = _indexer_with(nodes, edges)

        result = build_response(UUID_A, idx)

        assert len(result["edges"]) == 2
        types = {e["type"] for e in result["edges"]}
        assert "DEPENDS_ON" in types
        assert "SUPERSEDES" in types

    def test_self_loop_edge(self) -> None:
        """Self-referencing edge (A→A) is included if node is included."""
        nodes = {UUID_A: _meta(UUID_A, slug="a")}
        edges = [_edge(UUID_A, UUID_A)]
        idx = _indexer_with(nodes, edges)

        result = build_response(UUID_A, idx)

        assert len(result["edges"]) == 1
        assert result["edges"][0]["from"] == UUID_A
        assert result["edges"][0]["to"] == UUID_A

    def test_no_edges_graph(self) -> None:
        """Graph with nodes but no edges."""
        nodes = {
            UUID_A: _meta(UUID_A, slug="a"),
            UUID_B: _meta(UUID_B, slug="b"),
        }
        idx = _indexer_with(nodes, [])

        result = build_response(UUID_A, idx)

        assert len(result["nodes"]) == 2
        assert result["edges"] == []
