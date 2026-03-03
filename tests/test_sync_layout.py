"""Tests for kgn.sync.layout — file system path conventions."""

from __future__ import annotations

import uuid
from pathlib import Path

from kgn.models.edge import EdgeRecord
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.sync.layout import (
    edge_path,
    edge_slug,
    edges_dir,
    find_kge_files,
    find_kgn_files,
    node_path,
    node_slug,
    nodes_dir,
    project_dir,
)

# ── Fixtures ──────────────────────────────────────────────────────────

PROJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
NODE_ID = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
NODE_ID_2 = uuid.UUID("661f9511-f39a-4a0f-b2c7-557788990011")
AGENT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _make_node(
    **overrides,
) -> NodeRecord:
    defaults = {
        "id": NODE_ID,
        "project_id": PROJECT_ID,
        "type": NodeType.SPEC,
        "status": NodeStatus.ACTIVE,
        "title": "auth module design",
        "body_md": "## Context",
    }
    defaults.update(overrides)
    return NodeRecord(**defaults)


def _make_edge(**overrides) -> EdgeRecord:
    defaults = {
        "project_id": PROJECT_ID,
        "from_node_id": NODE_ID,
        "to_node_id": NODE_ID_2,
        "type": EdgeType.IMPLEMENTS,
    }
    defaults.update(overrides)
    return EdgeRecord(**defaults)


# ── node_slug tests ───────────────────────────────────────────────────


class TestNodeSlug:
    def test_basic_korean_title(self):
        node = _make_node(title="인증 모듈 설계")
        slug = node_slug(node)
        assert slug.startswith("인증-모듈-설계-")
        assert slug.endswith("550e8400")

    def test_english_title(self):
        node = _make_node(title="OAuth 2.0 / PKCE")
        slug = node_slug(node)
        assert "oauth" in slug
        assert slug.endswith("550e8400")

    def test_special_characters_replaced(self):
        node = _make_node(title="Hello, World! @#$%")
        slug = node_slug(node)
        # No special characters remain except hyphens
        assert all(c.isalnum() or c in "-_" for c in slug)

    def test_empty_title(self):
        node = _make_node(title="   ")
        slug = node_slug(node)
        assert slug.startswith("untitled-")

    def test_consecutive_hyphens_collapsed(self):
        node = _make_node(title="a -- b --- c")
        slug = node_slug(node)
        assert "---" not in slug
        assert "--" not in slug

    def test_uuid_prefix_appended(self):
        node = _make_node()
        slug = node_slug(node)
        # UUID first 8 chars: 550e8400
        assert slug.endswith("-550e8400")


# ── edge_slug tests ──────────────────────────────────────────────────


class TestEdgeSlug:
    def test_basic_slug(self):
        edge = _make_edge()
        slug = edge_slug(edge)
        assert slug == "550e8400--IMPLEMENTS--661f9511"

    def test_different_edge_type(self):
        edge = _make_edge(type=EdgeType.DEPENDS_ON)
        slug = edge_slug(edge)
        assert "DEPENDS_ON" in slug


# ── path helpers ─────────────────────────────────────────────────────


class TestPathHelpers:
    def test_node_path(self):
        node = _make_node()
        path = node_path(Path("/sync"), "my-project", node)
        assert path == Path("/sync/my-project/nodes/SPEC/auth-module-design-550e8400.kgn")

    def test_edge_path(self):
        edge = _make_edge()
        path = edge_path(Path("/sync"), "my-project", edge)
        assert path == Path("/sync/my-project/edges/550e8400--IMPLEMENTS--661f9511.kge")

    def test_project_dir(self):
        assert project_dir(Path("/sync"), "proj") == Path("/sync/proj")

    def test_nodes_dir(self):
        assert nodes_dir(Path("/sync"), "proj") == Path("/sync/proj/nodes")

    def test_edges_dir(self):
        assert edges_dir(Path("/sync"), "proj") == Path("/sync/proj/edges")


# ── file discovery ───────────────────────────────────────────────────


class TestFileDiscovery:
    def test_find_kgn_files(self, tmp_path: Path):
        nodes = tmp_path / "nodes" / "SPEC"
        nodes.mkdir(parents=True)
        (nodes / "a.kgn").write_text("content", encoding="utf-8")
        (nodes / "b.kgn").write_text("content", encoding="utf-8")
        (nodes / "c.txt").write_text("nope", encoding="utf-8")

        result = find_kgn_files(tmp_path)
        assert len(result) == 2
        assert all(p.suffix == ".kgn" for p in result)

    def test_find_kge_files(self, tmp_path: Path):
        edges = tmp_path / "edges"
        edges.mkdir(parents=True)
        (edges / "a.kge").write_text("content", encoding="utf-8")

        result = find_kge_files(tmp_path)
        assert len(result) == 1

    def test_find_in_nonexistent_dir(self):
        result = find_kgn_files(Path("/nonexistent"))
        assert result == []

    def test_files_sorted(self, tmp_path: Path):
        (tmp_path / "c.kgn").write_text("c", encoding="utf-8")
        (tmp_path / "a.kgn").write_text("a", encoding="utf-8")
        (tmp_path / "b.kgn").write_text("b", encoding="utf-8")

        result = find_kgn_files(tmp_path)
        names = [p.name for p in result]
        assert names == ["a.kgn", "b.kgn", "c.kgn"]
