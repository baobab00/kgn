"""Workspace Indexer — event-based incremental indexing for KGN/KGE files.

Provides O(1) slug→file, UUID→file, and reverse-reference lookups.
After an initial full scan, subsequent changes are handled by incremental
updates via FileSystemWatcher events (``workspace/didChangeWatchedFiles``).

Architecture
------------
* **Five index structures** — slug→path, UUID→path, path→meta, reverse-refs,
  edge-index — all maintained incrementally.
* **LRU cache** — file parse results cached by ``(path, mtime_ns)`` key;
  changed mtime automatically invalidates stale entries.
* **Open-document priority** — editor buffer content overrides on-disk state
  for any file that is currently open in the editor.
* **R23 compliance** — ``full_scan`` dispatches blocking I/O via
  ``asyncio.to_thread()``.

Design rules respected
~~~~~~~~~~~~~~~~~~~~~~
R23  All blocking work → ``asyncio.to_thread``.
R24  ``parse_kgn_tolerant`` never throws.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from kgn.models.edge import EdgeEntry, EdgeFrontMatter
from kgn.models.enums import NodeStatus, NodeType
from kgn.parser import parse_kgn_tolerant
from kgn.parser.kge_parser import parse_kge_text

if TYPE_CHECKING:
    from kgn.parser.models import PartialParseResult

logger = logging.getLogger(__name__)


# ── Models ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NodeMeta:
    """Lightweight metadata extracted from a .kgn front matter.

    Used as the value in the ``_path_to_meta`` index.  Intentionally
    avoids storing the full body to keep memory footprint small.
    """

    id: str
    slug: str
    type: NodeType
    title: str
    status: NodeStatus
    confidence: float | None
    path: Path


@dataclass
class LocalGraph:
    """Result of a local subgraph BFS traversal.

    Attributes:
        nodes: Node metadata keyed by node id.
        edges: All edges connecting the included nodes.
    """

    nodes: dict[str, NodeMeta] = field(default_factory=dict)
    edges: list[EdgeEntry] = field(default_factory=list)


# ── LRU Cache ──────────────────────────────────────────────────────────


class _MtimeLRU:
    """Simple LRU cache keyed by ``(path, mtime_ns)``.

    ``functools.lru_cache`` is unsuitable here because we need to key by
    mutable ``(path, mtime_ns)`` pairs and manually evict stale entries.
    """

    def __init__(self, maxsize: int = 1024) -> None:
        self._maxsize = maxsize
        self._cache: OrderedDict[tuple[Path, int], PartialParseResult] = OrderedDict()

    def get(self, path: Path, mtime_ns: int) -> PartialParseResult | None:
        """Return cached result if key matches, else ``None``."""
        key = (path, mtime_ns)
        result = self._cache.get(key)
        if result is not None:
            self._cache.move_to_end(key)
        return result

    def put(self, path: Path, mtime_ns: int, value: PartialParseResult) -> None:
        """Store a result, evicting excess entries."""
        key = (path, mtime_ns)
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def evict(self, path: Path) -> None:
        """Remove all cached entries for *path* (any mtime)."""
        to_remove = [k for k in self._cache if k[0] == path]
        for k in to_remove:
            del self._cache[k]

    def clear(self) -> None:
        """Remove all entries."""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


# ── Helpers ────────────────────────────────────────────────────────────


def _slug_from_path(path: Path) -> str:
    """Derive a slug from a file path (stem, lowercased)."""
    return path.stem.lower()


def _parse_kgn_file(path: Path) -> PartialParseResult:
    """Read and parse a single .kgn file (blocking)."""
    text = path.read_text(encoding="utf-8")
    return parse_kgn_tolerant(text, source_path=str(path))


def _parse_kge_file(path: Path) -> EdgeFrontMatter | None:
    """Read and parse a single .kge file (blocking).  Returns None on error."""
    try:
        text = path.read_text(encoding="utf-8")
        return parse_kge_text(text)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to parse .kge file: %s", path)
        return None


def _extract_meta(path: Path, result: PartialParseResult) -> NodeMeta | None:
    """Extract NodeMeta from a PartialParseResult.  Returns None if no front-matter."""
    fm = result.front_matter
    if fm is None:
        return None
    return NodeMeta(
        id=fm.id,
        slug=_slug_from_path(path),
        type=fm.type,
        title=fm.title,
        status=fm.status,
        confidence=fm.confidence,
        path=path,
    )


# ── WorkspaceIndexer ──────────────────────────────────────────────────


class WorkspaceIndexer:
    """Event-based incremental workspace index.

    Maintains five data structures for O(1) lookups:

    * ``_slug_to_path``  — slug → file mapping
    * ``_uuid_to_path``  — UUID (node id) → file mapping
    * ``_path_to_meta``  — file → NodeMeta cache
    * ``_reverse_refs``  — node_id → set of .kge files referring to it
    * ``_edge_index``    — .kge file → parsed edge list
    """

    def __init__(self, *, lru_maxsize: int = 1024) -> None:
        # Primary indexes
        self._slug_to_path: dict[str, Path] = {}
        self._uuid_to_path: dict[str, Path] = {}
        self._path_to_meta: dict[Path, NodeMeta] = {}

        # Edge indexes
        self._reverse_refs: dict[str, set[Path]] = {}
        self._edge_index: dict[Path, list[EdgeEntry]] = {}

        # Cache
        self._cache = _MtimeLRU(maxsize=lru_maxsize)

        # Open-document buffers — URI → (path, text)
        self._open_docs: dict[str, tuple[Path, str]] = {}

        # Scan state
        self._scanned = False

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def is_scanned(self) -> bool:
        """Whether an initial full scan has completed."""
        return self._scanned

    @property
    def node_count(self) -> int:
        """Number of indexed .kgn nodes."""
        return len(self._path_to_meta)

    @property
    def edge_file_count(self) -> int:
        """Number of indexed .kge files."""
        return len(self._edge_index)

    # ── Full scan ──────────────────────────────────────────────────────

    async def full_scan(self, root: Path) -> None:
        """Perform initial full workspace scan in a background thread (R23).

        Recursively globs ``*.kgn`` and ``*.kge`` files under *root*,
        parses each, and populates all five indexes.
        """
        await asyncio.to_thread(self._full_scan_sync, root)
        self._scanned = True

    def _full_scan_sync(self, root: Path) -> None:
        """Synchronous implementation of full scan."""
        # Collect files
        kgn_files = list(root.rglob("*.kgn"))
        kge_files = list(root.rglob("*.kge"))

        logger.info(
            "Full scan: %d .kgn + %d .kge files in %s",
            len(kgn_files),
            len(kge_files),
            root,
        )

        for path in kgn_files:
            self._index_kgn_file(path)

        for path in kge_files:
            self._index_kge_file(path)

    # ── Incremental updates ────────────────────────────────────────────

    def on_file_created(self, path: Path) -> None:
        """Index a newly created file."""
        suffix = path.suffix.lower()
        if suffix == ".kgn":
            self._index_kgn_file(path)
        elif suffix == ".kge":
            self._index_kge_file(path)

    def on_file_changed(self, path: Path) -> None:
        """Re-index a changed file — remove old entries, then re-parse."""
        suffix = path.suffix.lower()
        if suffix == ".kgn":
            self._remove_kgn_file(path)
            self._cache.evict(path)
            self._index_kgn_file(path)
        elif suffix == ".kge":
            self._remove_kge_file(path)
            self._index_kge_file(path)

    def on_file_deleted(self, path: Path) -> None:
        """Remove a deleted file from all indexes."""
        suffix = path.suffix.lower()
        if suffix == ".kgn":
            self._remove_kgn_file(path)
            self._cache.evict(path)
        elif suffix == ".kge":
            self._remove_kge_file(path)

    # ── Open document priority ─────────────────────────────────────────

    def on_document_open(self, uri: str, path: Path, text: str) -> None:
        """Register an open editor buffer and re-index from buffer content."""
        self._open_docs[uri] = (path, text)
        if path.suffix.lower() == ".kgn":
            self._remove_kgn_file(path)
            self._index_kgn_from_text(path, text)

    def on_document_change(self, uri: str, path: Path, text: str) -> None:
        """Update index from changed editor buffer content."""
        self._open_docs[uri] = (path, text)
        if path.suffix.lower() == ".kgn":
            self._remove_kgn_file(path)
            self._index_kgn_from_text(path, text)

    def on_document_close(self, uri: str, path: Path) -> None:
        """Revert to disk-based indexing when a document is closed."""
        self._open_docs.pop(uri, None)
        suffix = path.suffix.lower()
        if suffix == ".kgn":
            self._remove_kgn_file(path)
            if path.exists():
                self._index_kgn_file(path)
        elif suffix == ".kge":
            self._remove_kge_file(path)
            if path.exists():
                self._index_kge_file(path)

    # ── Query API ──────────────────────────────────────────────────────

    def resolve_slug(self, slug: str) -> Path | None:
        """Return the file path for a slug, or ``None``.  O(1)."""
        return self._slug_to_path.get(slug.lower())

    def resolve_uuid(self, uuid: str) -> Path | None:
        """Return the file path for a node UUID, or ``None``.  O(1)."""
        return self._uuid_to_path.get(uuid)

    def get_references(self, node_id: str) -> set[Path]:
        """Return all .kge files that reference *node_id*.  O(1)."""
        return self._reverse_refs.get(node_id, set())

    def get_meta(self, path: Path) -> NodeMeta | None:
        """Return cached NodeMeta for *path*, or ``None``.  O(1)."""
        return self._path_to_meta.get(path)

    def get_all_edges_for(self, node_id: str) -> list[EdgeEntry]:
        """Return all edges where *node_id* appears as ``from`` or ``to``."""
        result: list[EdgeEntry] = []
        ref_paths = self._reverse_refs.get(node_id, set())
        for edge_path in ref_paths:
            edges = self._edge_index.get(edge_path, [])
            for edge in edges:
                if edge.from_node == node_id or edge.to == node_id:
                    result.append(edge)
        return result

    def build_local_subgraph(self, node_id: str, depth: int = 1) -> LocalGraph:
        """BFS traversal to build a local subgraph around *node_id*.

        Collects all nodes reachable within *depth* hops, plus the
        connecting edges.

        Parameters:
            node_id: Starting node identifier.
            depth: Maximum BFS depth (default 1).

        Returns:
            LocalGraph with nodes and edges populated.
        """
        graph = LocalGraph()
        visited: set[str] = set()
        frontier: set[str] = {node_id}

        for _ in range(depth + 1):
            next_frontier: set[str] = set()
            for nid in frontier:
                if nid in visited:
                    continue
                visited.add(nid)

                # Add node meta if available
                node_path = self._uuid_to_path.get(nid)
                if node_path is not None:
                    meta = self._path_to_meta.get(node_path)
                    if meta is not None:
                        graph.nodes[nid] = meta

                # Find connected edges
                edges = self.get_all_edges_for(nid)
                for edge in edges:
                    graph.edges.append(edge)
                    # Discover neighbors
                    neighbor = edge.to if edge.from_node == nid else edge.from_node
                    if neighbor not in visited:
                        next_frontier.add(neighbor)

            frontier = next_frontier

        # De-duplicate edges
        seen_edges: set[tuple[str, str, str]] = set()
        unique_edges: list[EdgeEntry] = []
        for edge in graph.edges:
            key = (edge.from_node, edge.to, edge.type)
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(edge)
        graph.edges = unique_edges

        return graph

    def get_all_node_ids(self) -> list[str]:
        """Return all indexed node UUIDs."""
        return list(self._uuid_to_path.keys())

    def get_all_slugs(self) -> list[str]:
        """Return all indexed slugs."""
        return list(self._slug_to_path.keys())

    # ── Internal indexing ──────────────────────────────────────────────

    def _index_kgn_file(self, path: Path) -> None:
        """Parse a .kgn file from disk and add to indexes.

        Uses LRU cache if mtime matches.
        """
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            logger.warning("Cannot stat .kgn file: %s", path)
            return

        cached = self._cache.get(path, mtime_ns)
        if cached is not None:
            result = cached
        else:
            result = _parse_kgn_file(path)
            self._cache.put(path, mtime_ns, result)

        meta = _extract_meta(path, result)
        if meta is not None:
            self._add_kgn_meta(meta)

    def _index_kgn_from_text(self, path: Path, text: str) -> None:
        """Parse a .kgn document from text (editor buffer) and add to indexes."""
        result = parse_kgn_tolerant(text, source_path=str(path))
        meta = _extract_meta(path, result)
        if meta is not None:
            self._add_kgn_meta(meta)

    def _add_kgn_meta(self, meta: NodeMeta) -> None:
        """Add a NodeMeta to all relevant indexes."""
        # Warn on slug collision (R-105)
        existing = self._slug_to_path.get(meta.slug)
        if existing is not None and existing != meta.path:
            logger.warning(
                "Slug collision: '%s' maps to both %s and %s (latter wins)",
                meta.slug,
                existing,
                meta.path,
            )
        self._path_to_meta[meta.path] = meta
        self._slug_to_path[meta.slug] = meta.path
        self._uuid_to_path[meta.id] = meta.path

    def _remove_kgn_file(self, path: Path) -> None:
        """Remove a .kgn file from all indexes."""
        meta = self._path_to_meta.pop(path, None)
        if meta is not None:
            # Only remove slug/uuid mapping if it still points to this path
            if self._slug_to_path.get(meta.slug) == path:
                del self._slug_to_path[meta.slug]
            if self._uuid_to_path.get(meta.id) == path:
                del self._uuid_to_path[meta.id]

    def _index_kge_file(self, path: Path) -> None:
        """Parse a .kge file and add to edge indexes."""
        fm = _parse_kge_file(path)
        if fm is None:
            return

        self._edge_index[path] = list(fm.edges)

        # Build reverse references
        for edge in fm.edges:
            self._reverse_refs.setdefault(edge.from_node, set()).add(path)
            self._reverse_refs.setdefault(edge.to, set()).add(path)

    def _remove_kge_file(self, path: Path) -> None:
        """Remove a .kge file from edge indexes including reverse-refs."""
        edges = self._edge_index.pop(path, None)
        if edges is not None:
            for edge in edges:
                refs = self._reverse_refs.get(edge.from_node)
                if refs is not None:
                    refs.discard(path)
                    if not refs:
                        del self._reverse_refs[edge.from_node]
                refs = self._reverse_refs.get(edge.to)
                if refs is not None:
                    refs.discard(path)
                    if not refs:
                        del self._reverse_refs[edge.to]

    def clear(self) -> None:
        """Reset all indexes and caches."""
        self._slug_to_path.clear()
        self._uuid_to_path.clear()
        self._path_to_meta.clear()
        self._reverse_refs.clear()
        self._edge_index.clear()
        self._cache.clear()
        self._open_docs.clear()
        self._scanned = False
