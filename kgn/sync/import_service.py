"""Import service — file system (.kgn/.kge files) → DB.

Reads .kgn/.kge files from the sync directory and ingests them into PostgreSQL
using the existing IngestService pipeline.  content_hash-based skip detection
(V8 rule in upsert_node) ensures idempotent imports.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import structlog

from kgn.db.repository import KgnRepository
from kgn.ingest.service import IngestBatchResult, IngestService
from kgn.sync.layout import find_kge_files, find_kgn_files, project_dir

log = structlog.get_logger()


@dataclass
class ImportResult:
    """Import operation summary."""

    imported: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.imported + self.skipped + self.failed


@dataclass
class SyncStatus:
    """Comparison of DB and file system state."""

    db_node_count: int = 0
    db_edge_count: int = 0
    file_node_count: int = 0
    file_edge_count: int = 0
    last_export: str | None = None
    last_import: str | None = None

    @property
    def node_diff(self) -> int:
        """Positive = more in DB, negative = more on file system."""
        return self.db_node_count - self.file_node_count

    @property
    def edge_diff(self) -> int:
        return self.db_edge_count - self.file_edge_count


class ImportService:
    """Import .kgn/.kge files from file system into DB.

    Uses IngestService for actual parsing, validation, and upsert.
    Adds directory-aware scanning and sync metadata tracking.
    """

    def __init__(self, repo: KgnRepository) -> None:
        self._repo = repo

    def import_project(
        self,
        project_name: str,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        source_dir: Path,
    ) -> ImportResult:
        """Import all .kgn/.kge files under ``<source_dir>/<project_name>/``.

        Parameters:
            project_name: Name of the project (maps to directory name).
            project_id: UUID of the project in DB.
            agent_id: UUID of the agent performing the import.
            source_dir: Root sync directory containing project subdirectory.

        Returns:
            ImportResult with counts and errors.
        """
        result = ImportResult()
        proj_dir = project_dir(source_dir, project_name)

        if not proj_dir.exists():
            result.errors.append(f"Project directory does not exist: {proj_dir}")
            return result

        # Collect files
        kgn_files = find_kgn_files(proj_dir)
        kge_files = find_kge_files(proj_dir)

        log.info(
            "import.files_found",
            project=project_name,
            kgn_count=len(kgn_files),
            kge_count=len(kge_files),
        )

        if not kgn_files and not kge_files:
            return result

        # Create ingest service with enforce_project to bind to this project
        ingest = IngestService(
            repo=self._repo,
            project_id=project_id,
            agent_id=agent_id,
            enforce_project=True,
        )

        # Phase 1: Import .kgn files (nodes first)
        for kgn_path in kgn_files:
            try:
                text = kgn_path.read_text(encoding="utf-8")
                batch = ingest.ingest_text(text, ".kgn")
                self._aggregate_batch(result, batch)
            except Exception as exc:
                result.failed += 1
                result.errors.append(f"{kgn_path.name}: {exc}")
                log.warning("import.kgn_error", path=str(kgn_path), error=str(exc))

        # Phase 2: Import .kge files (edges after nodes)
        for kge_path in kge_files:
            try:
                text = kge_path.read_text(encoding="utf-8")
                batch = ingest.ingest_text(text, ".kge")
                self._aggregate_batch(result, batch)
            except Exception as exc:
                result.failed += 1
                result.errors.append(f"{kge_path.name}: {exc}")
                log.warning("import.kge_error", path=str(kge_path), error=str(exc))

        # Update sync metadata
        self._update_sync_metadata(source_dir)

        log.info(
            "import.complete",
            project=project_name,
            imported=result.imported,
            skipped=result.skipped,
            failed=result.failed,
        )

        return result

    @staticmethod
    def _aggregate_batch(result: ImportResult, batch: IngestBatchResult) -> None:
        """Merge IngestBatchResult into ImportResult."""
        result.imported += batch.success
        result.skipped += batch.skipped
        result.failed += batch.failed
        for detail in batch.details:
            if detail.error:
                result.errors.append(f"{detail.file_path}: {detail.error}")

    @staticmethod
    def _update_sync_metadata(sync_root: Path) -> None:
        """Update last_import timestamp in .kgn-sync.json."""
        meta_path = sync_root / ".kgn-sync.json"

        meta: dict = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}

        meta["last_import"] = datetime.now(UTC).isoformat()

        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def get_sync_status(
    repo: KgnRepository,
    project_name: str,
    project_id: uuid.UUID,
    sync_root: Path,
) -> SyncStatus:
    """Compare DB and file system state for a project.

    Returns:
        SyncStatus with counts and diffs.
    """
    status = SyncStatus()

    # DB counts
    db_nodes = repo.search_nodes(project_id, exclude_archived=False)
    db_edges = repo.search_edges(project_id)
    status.db_node_count = len(db_nodes)
    status.db_edge_count = len(db_edges)

    # File system counts
    proj_dir = project_dir(sync_root, project_name)
    status.file_node_count = len(find_kgn_files(proj_dir))
    status.file_edge_count = len(find_kge_files(proj_dir))

    # Metadata
    meta_path = sync_root / ".kgn-sync.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            status.last_export = meta.get("last_export")
            status.last_import = meta.get("last_import")
        except (json.JSONDecodeError, OSError):
            pass

    return status
