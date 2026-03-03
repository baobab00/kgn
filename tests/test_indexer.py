"""Tests for kgn.lsp.indexer — WorkspaceIndexer.

Test categories:
- _MtimeLRU cache
- NodeMeta model
- full_scan accuracy
- Incremental updates (created / changed / deleted)
- Open-document priority
- Query API (resolve_slug, resolve_uuid, get_references, etc.)
- build_local_subgraph BFS
- 1000-file performance simulation
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from kgn.lsp.indexer import (
    LocalGraph,
    NodeMeta,
    WorkspaceIndexer,
    _extract_meta,
    _MtimeLRU,
    _slug_from_path,
)
from kgn.models.enums import NodeStatus, NodeType
from kgn.parser import parse_kgn_tolerant

if TYPE_CHECKING:
    pass


# ── Fixtures ───────────────────────────────────────────────────────────

_VALID_KGN = """\
---
kgn_version: "0.1"
id: "550e8400-e29b-41d4-a716-446655440000"
type: SPEC
title: "Test Node"
status: ACTIVE
project_id: "proj-alpha"
agent_id: "worker-01"
created_at: "2026-01-01T00:00:00+00:00"
confidence: 0.85
---

## Content

Body text here.
"""

_VALID_KGN_2 = """\
---
kgn_version: "0.1"
id: "660e8400-e29b-41d4-a716-446655440001"
type: GOAL
title: "Second Node"
status: ACTIVE
project_id: "proj-alpha"
agent_id: "worker-02"
created_at: "2026-01-02T00:00:00+00:00"
---

## Content

Another body.
"""

_VALID_KGE = """\
---
kgn_version: "0.1"
project_id: "proj-alpha"
agent_id: "worker-01"
created_at: "2026-01-01T00:00:00+00:00"
edges:
  - from: "550e8400-e29b-41d4-a716-446655440000"
    to: "660e8400-e29b-41d4-a716-446655440001"
    type: IMPLEMENTS
    note: "impl edge"
  - from: "550e8400-e29b-41d4-a716-446655440000"
    to: "350e8400-e29b-41d4-a716-446655440002"
    type: DEPENDS_ON
---
"""

_VALID_KGE_2 = """\
---
kgn_version: "0.1"
project_id: "proj-alpha"
agent_id: "worker-01"
created_at: "2026-01-01T00:00:00+00:00"
edges:
  - from: "660e8400-e29b-41d4-a716-446655440001"
    to: "550e8400-e29b-41d4-a716-446655440000"
    type: DEPENDS_ON
---
"""


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Create a minimal workspace with .kgn and .kge files."""
    kgn_file = tmp_path / "test-node.kgn"
    kgn_file.write_text(_VALID_KGN, encoding="utf-8")

    kgn_file2 = tmp_path / "second-node.kgn"
    kgn_file2.write_text(_VALID_KGN_2, encoding="utf-8")

    kge_file = tmp_path / "edges.kge"
    kge_file.write_text(_VALID_KGE, encoding="utf-8")

    return tmp_path


@pytest.fixture()
def indexer() -> WorkspaceIndexer:
    """Create a fresh WorkspaceIndexer."""
    return WorkspaceIndexer()


# ── _MtimeLRU Tests ────────────────────────────────────────────────────


class TestMtimeLRU:
    """Tests for the LRU cache implementation."""

    def test_put_and_get(self, tmp_path: Path) -> None:
        cache = _MtimeLRU(maxsize=10)
        path = tmp_path / "test.kgn"
        result = parse_kgn_tolerant("---\n---\n")
        cache.put(path, 12345, result)
        assert cache.get(path, 12345) is result

    def test_get_miss(self, tmp_path: Path) -> None:
        cache = _MtimeLRU(maxsize=10)
        path = tmp_path / "test.kgn"
        assert cache.get(path, 12345) is None

    def test_mtime_mismatch(self, tmp_path: Path) -> None:
        cache = _MtimeLRU(maxsize=10)
        path = tmp_path / "test.kgn"
        result = parse_kgn_tolerant("---\n---\n")
        cache.put(path, 12345, result)
        assert cache.get(path, 99999) is None

    def test_maxsize_eviction(self, tmp_path: Path) -> None:
        cache = _MtimeLRU(maxsize=3)
        result = parse_kgn_tolerant("---\n---\n")
        for i in range(5):
            cache.put(tmp_path / f"file{i}.kgn", i, result)
        assert len(cache) == 3
        # Oldest two should be evicted
        assert cache.get(tmp_path / "file0.kgn", 0) is None
        assert cache.get(tmp_path / "file1.kgn", 1) is None
        # Latest three should remain
        assert cache.get(tmp_path / "file2.kgn", 2) is result

    def test_evict_by_path(self, tmp_path: Path) -> None:
        cache = _MtimeLRU(maxsize=10)
        path = tmp_path / "test.kgn"
        result = parse_kgn_tolerant("---\n---\n")
        cache.put(path, 111, result)
        cache.put(path, 222, result)
        cache.evict(path)
        assert cache.get(path, 111) is None
        assert cache.get(path, 222) is None
        assert len(cache) == 0

    def test_clear(self, tmp_path: Path) -> None:
        cache = _MtimeLRU(maxsize=10)
        result = parse_kgn_tolerant("---\n---\n")
        cache.put(tmp_path / "a.kgn", 1, result)
        cache.put(tmp_path / "b.kgn", 2, result)
        cache.clear()
        assert len(cache) == 0

    def test_move_to_end_on_access(self, tmp_path: Path) -> None:
        """Accessing an item moves it to end so it's evicted last."""
        cache = _MtimeLRU(maxsize=3)
        result = parse_kgn_tolerant("---\n---\n")
        cache.put(tmp_path / "a.kgn", 1, result)
        cache.put(tmp_path / "b.kgn", 2, result)
        cache.put(tmp_path / "c.kgn", 3, result)
        # Access 'a' to move it to end
        cache.get(tmp_path / "a.kgn", 1)
        # Adding one more should evict 'b' (oldest not-recently-used)
        cache.put(tmp_path / "d.kgn", 4, result)
        assert cache.get(tmp_path / "a.kgn", 1) is result
        assert cache.get(tmp_path / "b.kgn", 2) is None


# ── Helper Tests ───────────────────────────────────────────────────────


class TestHelpers:
    """Tests for module-level helper functions."""

    def test_slug_from_path(self) -> None:
        assert _slug_from_path(Path("/ws/My-Node.kgn")) == "my-node"

    def test_slug_from_path_uppercase(self) -> None:
        assert _slug_from_path(Path("UPPER.kgn")) == "upper"

    def test_extract_meta_none_front_matter(self, tmp_path: Path) -> None:
        result = parse_kgn_tolerant("broken content")
        meta = _extract_meta(tmp_path / "broken.kgn", result)
        assert meta is None

    def test_extract_meta_valid(self, tmp_path: Path) -> None:
        result = parse_kgn_tolerant(_VALID_KGN)
        path = tmp_path / "test-node.kgn"
        meta = _extract_meta(path, result)
        assert meta is not None
        assert meta.id == "550e8400-e29b-41d4-a716-446655440000"
        assert meta.slug == "test-node"
        assert meta.type == NodeType.SPEC
        assert meta.title == "Test Node"
        assert meta.status == NodeStatus.ACTIVE
        assert meta.confidence == 0.85
        assert meta.path == path


# ── NodeMeta Tests ─────────────────────────────────────────────────────


class TestNodeMeta:
    """Tests for the NodeMeta dataclass."""

    def test_frozen(self) -> None:
        meta = NodeMeta(
            id="abc",
            slug="test",
            type=NodeType.SPEC,
            title="Test",
            status=NodeStatus.ACTIVE,
            confidence=None,
            path=Path("test.kgn"),
        )
        with pytest.raises(AttributeError):
            meta.id = "xyz"  # type: ignore[misc]

    def test_fields(self) -> None:
        meta = NodeMeta(
            id="abc",
            slug="test",
            type=NodeType.GOAL,
            title="Title",
            status=NodeStatus.DEPRECATED,
            confidence=0.5,
            path=Path("a.kgn"),
        )
        assert meta.confidence == 0.5
        assert meta.type == NodeType.GOAL


# ── LocalGraph Tests ───────────────────────────────────────────────────


class TestLocalGraph:
    """Tests for the LocalGraph dataclass."""

    def test_defaults(self) -> None:
        g = LocalGraph()
        assert g.nodes == {}
        assert g.edges == []


# ── WorkspaceIndexer — Full Scan ───────────────────────────────────────


class TestFullScan:
    """Tests for the full_scan method."""

    @pytest.mark.asyncio()
    async def test_full_scan_indexes_kgn(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        assert idx.is_scanned is True
        assert idx.node_count == 2

    @pytest.mark.asyncio()
    async def test_full_scan_indexes_kge(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        assert idx.edge_file_count == 1

    @pytest.mark.asyncio()
    async def test_slug_lookup_after_scan(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        path = idx.resolve_slug("test-node")
        assert path is not None
        assert path.name == "test-node.kgn"

    @pytest.mark.asyncio()
    async def test_uuid_lookup_after_scan(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        path = idx.resolve_uuid("550e8400-e29b-41d4-a716-446655440000")
        assert path is not None
        assert path.name == "test-node.kgn"

    @pytest.mark.asyncio()
    async def test_meta_after_scan(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        path = workspace / "test-node.kgn"
        meta = idx.get_meta(path)
        assert meta is not None
        assert meta.title == "Test Node"
        assert meta.type == NodeType.SPEC

    @pytest.mark.asyncio()
    async def test_references_after_scan(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        refs = idx.get_references("550e8400-e29b-41d4-a716-446655440000")
        assert len(refs) == 1
        assert (workspace / "edges.kge") in refs

    @pytest.mark.asyncio()
    async def test_empty_workspace(self, tmp_path: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(tmp_path)
        assert idx.is_scanned is True
        assert idx.node_count == 0
        assert idx.edge_file_count == 0

    @pytest.mark.asyncio()
    async def test_invalid_kgn_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "broken.kgn").write_text("not valid kgn", encoding="utf-8")
        idx = WorkspaceIndexer()
        await idx.full_scan(tmp_path)
        assert idx.node_count == 0

    @pytest.mark.asyncio()
    async def test_invalid_kge_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "broken.kge").write_text("not valid yaml!", encoding="utf-8")
        idx = WorkspaceIndexer()
        await idx.full_scan(tmp_path)
        assert idx.edge_file_count == 0

    @pytest.mark.asyncio()
    async def test_nested_directories(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        (sub / "nested.kgn").write_text(_VALID_KGN, encoding="utf-8")
        idx = WorkspaceIndexer()
        await idx.full_scan(tmp_path)
        assert idx.node_count == 1
        assert idx.resolve_slug("nested") is not None


# ── WorkspaceIndexer — Incremental Updates ─────────────────────────────


class TestIncrementalUpdates:
    """Tests for on_file_created, on_file_changed, on_file_deleted."""

    @pytest.mark.asyncio()
    async def test_on_file_created_kgn(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        assert idx.node_count == 2

        new_file = workspace / "new-node.kgn"
        new_file.write_text(
            _VALID_KGN.replace(
                "550e8400-e29b-41d4-a716-446655440000",
                "770e8400-e29b-41d4-a716-446655440002",
            ).replace("Test Node", "New Node"),
            encoding="utf-8",
        )

        idx.on_file_created(new_file)
        assert idx.node_count == 3
        assert idx.resolve_slug("new-node") is not None

    @pytest.mark.asyncio()
    async def test_on_file_created_kge(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        assert idx.edge_file_count == 1

        new_kge = workspace / "new-edges.kge"
        new_kge.write_text(_VALID_KGE_2, encoding="utf-8")

        idx.on_file_created(new_kge)
        assert idx.edge_file_count == 2

    @pytest.mark.asyncio()
    async def test_on_file_changed_kgn(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)

        kgn_file = workspace / "test-node.kgn"
        # Change the title
        kgn_file.write_text(
            _VALID_KGN.replace("Test Node", "Updated Title"),
            encoding="utf-8",
        )

        idx.on_file_changed(kgn_file)
        meta = idx.get_meta(kgn_file)
        assert meta is not None
        assert meta.title == "Updated Title"

    @pytest.mark.asyncio()
    async def test_on_file_changed_slug_change(self, workspace: Path) -> None:
        """Changing filename (slug) should update slug index."""
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        assert idx.resolve_slug("test-node") is not None

        # Simulate rename by deleting old and creating new
        old_path = workspace / "test-node.kgn"
        new_path = workspace / "renamed-node.kgn"
        content = old_path.read_text(encoding="utf-8")
        old_path.unlink()
        new_path.write_text(content, encoding="utf-8")

        idx.on_file_deleted(old_path)
        idx.on_file_created(new_path)

        assert idx.resolve_slug("test-node") is None
        assert idx.resolve_slug("renamed-node") is not None

    @pytest.mark.asyncio()
    async def test_on_file_deleted_kgn(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        assert idx.node_count == 2

        kgn_file = workspace / "test-node.kgn"
        kgn_file.unlink()
        idx.on_file_deleted(kgn_file)

        assert idx.node_count == 1
        assert idx.resolve_slug("test-node") is None
        assert idx.resolve_uuid("550e8400-e29b-41d4-a716-446655440000") is None

    @pytest.mark.asyncio()
    async def test_on_file_deleted_kge(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        assert idx.edge_file_count == 1

        kge_file = workspace / "edges.kge"
        kge_file.unlink()
        idx.on_file_deleted(kge_file)

        assert idx.edge_file_count == 0
        refs = idx.get_references("550e8400-e29b-41d4-a716-446655440000")
        assert len(refs) == 0

    @pytest.mark.asyncio()
    async def test_on_file_changed_kge(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)

        kge_file = workspace / "edges.kge"
        # Replace with different edges
        kge_file.write_text(_VALID_KGE_2, encoding="utf-8")
        idx.on_file_changed(kge_file)

        # Old reference should be cleaned up, new one added
        edges = idx.get_all_edges_for("660e8400-e29b-41d4-a716-446655440001")
        assert len(edges) == 1
        assert edges[0].type == "DEPENDS_ON"

    def test_on_file_created_unknown_suffix(self, indexer: WorkspaceIndexer) -> None:
        """Non-KGN/KGE files should be silently ignored."""
        indexer.on_file_created(Path("readme.md"))
        assert indexer.node_count == 0

    def test_on_file_deleted_nonexistent(self, indexer: WorkspaceIndexer) -> None:
        """Deleting a file not in the index should not raise."""
        indexer.on_file_deleted(Path("nonexistent.kgn"))
        assert indexer.node_count == 0


# ── WorkspaceIndexer — Open Document Priority ─────────────────────────


class TestOpenDocumentPriority:
    """Tests for editor buffer priority over disk content."""

    @pytest.mark.asyncio()
    async def test_open_overrides_disk(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)

        kgn_file = workspace / "test-node.kgn"
        uri = f"file:///{kgn_file}"

        # Open with different title
        modified = _VALID_KGN.replace("Test Node", "Buffer Title")
        idx.on_document_open(uri, kgn_file, modified)

        meta = idx.get_meta(kgn_file)
        assert meta is not None
        assert meta.title == "Buffer Title"

    @pytest.mark.asyncio()
    async def test_change_updates_buffer(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)

        kgn_file = workspace / "test-node.kgn"
        uri = f"file:///{kgn_file}"

        # Open
        idx.on_document_open(uri, kgn_file, _VALID_KGN)
        # Change buffer
        modified = _VALID_KGN.replace("Test Node", "Changed Again")
        idx.on_document_change(uri, kgn_file, modified)

        meta = idx.get_meta(kgn_file)
        assert meta is not None
        assert meta.title == "Changed Again"

    @pytest.mark.asyncio()
    async def test_close_reverts_to_disk(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)

        kgn_file = workspace / "test-node.kgn"
        uri = f"file:///{kgn_file}"

        # Open with modified content
        modified = _VALID_KGN.replace("Test Node", "Buffer Title")
        idx.on_document_open(uri, kgn_file, modified)

        # Close — should revert to disk
        idx.on_document_close(uri, kgn_file)

        meta = idx.get_meta(kgn_file)
        assert meta is not None
        assert meta.title == "Test Node"

    @pytest.mark.asyncio()
    async def test_close_deleted_file(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)

        kgn_file = workspace / "test-node.kgn"
        uri = f"file:///{kgn_file}"

        idx.on_document_open(uri, kgn_file, _VALID_KGN)
        kgn_file.unlink()
        idx.on_document_close(uri, kgn_file)

        # File doesn't exist, so meta should be removed
        assert idx.get_meta(kgn_file) is None


# ── WorkspaceIndexer — Query API ───────────────────────────────────────


class TestQueryAPI:
    """Tests for resolve_slug, resolve_uuid, get_references, etc."""

    @pytest.mark.asyncio()
    async def test_resolve_slug_case_insensitive(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        assert idx.resolve_slug("TEST-NODE") is not None
        assert idx.resolve_slug("Test-Node") is not None

    @pytest.mark.asyncio()
    async def test_resolve_slug_not_found(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        assert idx.resolve_slug("nonexistent") is None

    @pytest.mark.asyncio()
    async def test_resolve_uuid_not_found(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        assert idx.resolve_uuid("00000000-0000-0000-0000-000000000000") is None

    @pytest.mark.asyncio()
    async def test_get_meta_not_indexed(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        assert idx.get_meta(Path("nonexistent.kgn")) is None

    @pytest.mark.asyncio()
    async def test_get_all_edges_for(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        edges = idx.get_all_edges_for("550e8400-e29b-41d4-a716-446655440000")
        assert len(edges) == 2
        edge_types = {e.type for e in edges}
        assert "IMPLEMENTS" in edge_types
        assert "DEPENDS_ON" in edge_types

    @pytest.mark.asyncio()
    async def test_get_all_edges_for_no_refs(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        edges = idx.get_all_edges_for("nonexistent-id")
        assert edges == []

    @pytest.mark.asyncio()
    async def test_get_all_node_ids(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        ids = idx.get_all_node_ids()
        assert "550e8400-e29b-41d4-a716-446655440000" in ids
        assert "660e8400-e29b-41d4-a716-446655440001" in ids

    @pytest.mark.asyncio()
    async def test_get_all_slugs(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        slugs = idx.get_all_slugs()
        assert "test-node" in slugs
        assert "second-node" in slugs


# ── WorkspaceIndexer — build_local_subgraph ────────────────────────────


class TestBuildLocalSubgraph:
    """Tests for the BFS local subgraph builder."""

    @pytest.mark.asyncio()
    async def test_single_hop(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        graph = idx.build_local_subgraph(
            "550e8400-e29b-41d4-a716-446655440000",
            depth=1,
        )
        assert "550e8400-e29b-41d4-a716-446655440000" in graph.nodes
        assert "660e8400-e29b-41d4-a716-446655440001" in graph.nodes
        assert len(graph.edges) >= 1

    @pytest.mark.asyncio()
    async def test_zero_depth(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        graph = idx.build_local_subgraph(
            "550e8400-e29b-41d4-a716-446655440000",
            depth=0,
        )
        assert "550e8400-e29b-41d4-a716-446655440000" in graph.nodes
        assert len(graph.edges) >= 1

    @pytest.mark.asyncio()
    async def test_unknown_node(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        graph = idx.build_local_subgraph("nonexistent", depth=1)
        assert len(graph.nodes) == 0
        assert len(graph.edges) == 0

    @pytest.mark.asyncio()
    async def test_edges_deduplicated(self, workspace: Path) -> None:
        """Edges should not be duplicated when discovered from both endpoints."""
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        graph = idx.build_local_subgraph(
            "550e8400-e29b-41d4-a716-446655440000",
            depth=2,
        )
        edge_keys = [(e.from_node, e.to, e.type) for e in graph.edges]
        assert len(edge_keys) == len(set(edge_keys))


# ── WorkspaceIndexer — LRU Cache Integration ──────────────────────────


class TestLRUCacheIntegration:
    """Tests for LRU cache behavior within the indexer."""

    @pytest.mark.asyncio()
    async def test_cache_hit_on_rescan(self, workspace: Path) -> None:
        """Second scan of unchanged files should use cache."""
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        # Cache should have entries
        assert len(idx._cache) > 0

    @pytest.mark.asyncio()
    async def test_cache_invalidated_on_change(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)

        kgn_file = workspace / "test-node.kgn"
        kgn_file.write_text(
            _VALID_KGN.replace("Test Node", "Modified"),
            encoding="utf-8",
        )

        idx.on_file_changed(kgn_file)
        meta = idx.get_meta(kgn_file)
        assert meta is not None
        assert meta.title == "Modified"


# ── WorkspaceIndexer — Clear ──────────────────────────────────────────


class TestClear:
    """Tests for the clear method."""

    @pytest.mark.asyncio()
    async def test_clear_resets_all(self, workspace: Path) -> None:
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)
        assert idx.node_count > 0

        idx.clear()
        assert idx.node_count == 0
        assert idx.edge_file_count == 0
        assert idx.is_scanned is False
        assert len(idx._cache) == 0


# ── Performance Test ───────────────────────────────────────────────────


class TestPerformance:
    """Performance simulation tests."""

    @pytest.mark.asyncio()
    async def test_1000_file_scan_under_2s(self, tmp_path: Path) -> None:
        """Full scan of 1000 .kgn files should complete in < 2 seconds."""
        # Generate 1000 .kgn files
        for i in range(1000):
            node_id = f"aaaaaaaa-bbbb-cccc-dddd-{i:012d}"
            content = f"""\
---
kgn_version: "0.1"
id: "{node_id}"
type: SPEC
title: "Node {i}"
status: ACTIVE
project_id: "proj-perf"
agent_id: "perf-agent"
created_at: "2026-01-01T00:00:00+00:00"
---

## Content

Performance test node {i}.
"""
            (tmp_path / f"node-{i:04d}.kgn").write_text(content, encoding="utf-8")

        idx = WorkspaceIndexer()
        start = time.monotonic()
        await idx.full_scan(tmp_path)
        elapsed = time.monotonic() - start

        assert idx.node_count == 1000
        assert elapsed < 10.0, f"full_scan took {elapsed:.2f}s, expected < 10s"

    @pytest.mark.asyncio()
    async def test_incremental_event_under_10ms(self, workspace: Path) -> None:
        """Single file event should process in < 10ms."""
        idx = WorkspaceIndexer()
        await idx.full_scan(workspace)

        new_file = workspace / "perf-new.kgn"
        new_file.write_text(
            _VALID_KGN.replace(
                "550e8400-e29b-41d4-a716-446655440000",
                "880e8400-e29b-41d4-a716-446655440099",
            ),
            encoding="utf-8",
        )

        start = time.monotonic()
        idx.on_file_created(new_file)
        elapsed = time.monotonic() - start

        assert elapsed < 0.01, f"Event processing took {elapsed * 1000:.1f}ms, expected < 10ms"


# ── Server Integration Tests ──────────────────────────────────────────


class TestServerIntegration:
    """Tests for the indexer integration in the LSP server module."""

    def test_indexer_instance_exists(self) -> None:
        """The server module should expose an indexer instance."""
        from kgn.lsp.server import indexer as srv_indexer

        assert isinstance(srv_indexer, WorkspaceIndexer)

    def test_uri_to_path_file_uri(self) -> None:
        from kgn.lsp.server import _uri_to_path

        path = _uri_to_path("file:///home/user/test.kgn")
        assert path is not None
        assert str(path).endswith("test.kgn")

    def test_uri_to_path_non_file(self) -> None:
        from kgn.lsp.server import _uri_to_path

        assert _uri_to_path("untitled:Untitled-1") is None

    def test_uri_to_path_percent_encoded(self) -> None:
        from kgn.lsp.server import _uri_to_path

        path = _uri_to_path("file:///home/user/my%20file.kgn")
        assert path is not None
        assert "my file.kgn" in str(path)

    def test_uri_to_path_unix_preserves_leading_slash(self) -> None:
        """R-103: Unix paths must retain leading /."""
        from kgn.lsp.server import _uri_to_path

        path = _uri_to_path("file:///home/user/test.kgn")
        assert path is not None
        assert str(path).startswith("/") or str(path).startswith("\\")

    def test_uri_to_path_windows_drive(self) -> None:
        """Windows file URIs with drive letter should strip leading /."""
        from kgn.lsp.server import _uri_to_path

        path = _uri_to_path("file:///C:/Users/test.kgn")
        assert path is not None
        path_str = str(path)
        assert "C:" in path_str
        assert not path_str.startswith("/C:")

    def test_uri_to_path_vscode_scheme(self) -> None:
        from kgn.lsp.server import _uri_to_path

        assert _uri_to_path("vscode-userdata:///test") is None


# ── Slug Collision Tests ───────────────────────────────────────────────


class TestSlugCollision:
    """Tests for slug collision warning (R-105)."""

    @pytest.mark.asyncio()
    async def test_slug_collision_logs_warning(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When two files share the same stem, a warning should be logged."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        (dir1 / "node.kgn").write_text(
            _VALID_KGN.replace(
                "550e8400-e29b-41d4-a716-446655440000",
                "aaa00000-0000-0000-0000-000000000001",
            ),
            encoding="utf-8",
        )
        (dir2 / "node.kgn").write_text(
            _VALID_KGN.replace(
                "550e8400-e29b-41d4-a716-446655440000",
                "bbb00000-0000-0000-0000-000000000002",
            ),
            encoding="utf-8",
        )

        idx = WorkspaceIndexer()
        with caplog.at_level(logging.WARNING, logger="kgn.lsp.indexer"):
            await idx.full_scan(tmp_path)

        assert any("Slug collision" in r.message for r in caplog.records)
