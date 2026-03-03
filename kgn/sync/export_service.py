"""Export service — DB → file system (.kgn/.kge files).

Reads all nodes and edges from the database for a given project,
serializes them into .kgn/.kge text, and writes to the file system.

Change detection uses content_hash computed from the *serialized* text
(not the original import text) to avoid false positives from formatting
differences (see R-012 in RISK_REGISTER.md).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import structlog

from kgn.db.repository import KgnRepository
from kgn.serializer import serialize_edges, serialize_node
from kgn.sync.layout import edge_path, find_kge_files, find_kgn_files, node_path

log = structlog.get_logger()


def _compute_hash(text: str) -> str:
    """SHA-256 hex digest of serialized text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ExportResult:
    """Export operation summary."""

    exported: int = 0
    skipped: int = 0
    deleted: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.exported + self.skipped

    @property
    def error_count(self) -> int:
        return len(self.errors)


class ExportService:
    """Export a project's nodes/edges from DB to .kgn/.kge files.

    Core behaviour:
        1. Query all nodes/edges from DB for the project.
        2. Serialize each node to ``<type>/<slug>.kgn`` path.
        3. Compare content_hash to avoid rewriting unchanged files.
        4. Remove orphan files (exist on disk but not in DB).
        5. Write ``.kgn-sync.json`` metadata.
    """

    def __init__(self, repo: KgnRepository) -> None:
        self._repo = repo

    def export_project(
        self,
        project_name: str,
        project_id: uuid.UUID,
        target_dir: Path,
        *,
        agent_id: str | None = None,
    ) -> ExportResult:
        """Export all nodes and edges for *project_id* to *target_dir*.

        Parameters:
            project_name: Human-readable project name (used for directory).
            project_id: UUID of the project in DB.
            target_dir: Root directory for sync (the ``sync_root``).
            agent_id: Agent identifier for serialized files.

        Returns:
            ExportResult with counts and any errors.
        """
        result = ExportResult()

        # 1. Query all nodes
        nodes = self._repo.search_nodes(project_id, exclude_archived=False)
        log.info("export.nodes_queried", project=project_name, count=len(nodes))

        # 2. Query all edges
        edges = self._repo.search_edges(project_id)
        log.info("export.edges_queried", project=project_name, count=len(edges))

        # 3. Export nodes
        written_node_paths: set[Path] = set()
        for node in nodes:
            try:
                path = node_path(target_dir, project_name, node)
                text = serialize_node(node, agent_id=agent_id)
                written = self._write_if_changed(path, text)
                written_node_paths.add(path)
                if written:
                    result.exported += 1
                else:
                    result.skipped += 1
            except Exception as exc:
                result.errors.append(f"Node {node.id}: {exc}")
                log.warning("export.node_error", node_id=str(node.id), error=str(exc))

        # 4. Export edges — group by (from, to, type) → one edge per .kge
        written_edge_paths: set[Path] = set()
        for edge in edges:
            try:
                path = edge_path(target_dir, project_name, edge)
                text = serialize_edges([edge], project_id=project_id, agent_id=agent_id)
                written = self._write_if_changed(path, text)
                written_edge_paths.add(path)
                if written:
                    result.exported += 1
                else:
                    result.skipped += 1
            except Exception as exc:
                result.errors.append(f"Edge {edge.from_node_id}→{edge.to_node_id}: {exc}")
                log.warning("export.edge_error", error=str(exc))

        # 5. Clean up orphan files
        result.deleted += self._cleanup_orphans(
            target_dir, project_name, written_node_paths, written_edge_paths
        )

        # 6. Write sync metadata
        self._write_sync_metadata(
            target_dir,
            project_name=project_name,
            project_id=project_id,
            node_count=len(nodes),
            edge_count=len(edges),
        )

        log.info(
            "export.complete",
            project=project_name,
            exported=result.exported,
            skipped=result.skipped,
            deleted=result.deleted,
            errors=result.error_count,
        )

        return result

    def _write_if_changed(self, path: Path, text: str) -> bool:
        """Write file only if content has changed. Returns True if written."""
        new_hash = _compute_hash(text)

        if path.exists():
            existing_text = path.read_text(encoding="utf-8")
            existing_hash = _compute_hash(existing_text)
            if existing_hash == new_hash:
                return False

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True

    def _cleanup_orphans(
        self,
        sync_root: Path,
        project_name: str,
        written_node_paths: set[Path],
        written_edge_paths: set[Path],
    ) -> int:
        """Remove files on disk that are no longer in DB."""
        deleted = 0
        project_path = sync_root / project_name

        # Orphan .kgn files
        for existing_path in find_kgn_files(project_path):
            if existing_path not in written_node_paths:
                existing_path.unlink()
                deleted += 1
                log.debug("export.orphan_deleted", path=str(existing_path))

        # Orphan .kge files
        for existing_path in find_kge_files(project_path):
            if existing_path not in written_edge_paths:
                existing_path.unlink()
                deleted += 1
                log.debug("export.orphan_deleted", path=str(existing_path))

        return deleted

    @staticmethod
    def _write_sync_metadata(
        sync_root: Path,
        *,
        project_name: str,
        project_id: uuid.UUID,
        node_count: int,
        edge_count: int,
    ) -> None:
        """Write (or update) the .kgn-sync.json metadata file."""
        meta_path = sync_root / ".kgn-sync.json"

        meta: dict = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}

        meta["version"] = "1.0"
        meta["last_export"] = datetime.now(UTC).isoformat()
        meta["project"] = project_name
        meta["project_id"] = str(project_id)
        meta["node_count"] = node_count
        meta["edge_count"] = edge_count

        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
