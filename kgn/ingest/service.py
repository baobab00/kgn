"""Ingest service — core pipeline for .kgn and .kge files.

Collects files from a path (single file or directory), parses and validates
them, resolves ``new:`` temporary IDs, and upserts nodes / edges via the
repository layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from psycopg.errors import DatabaseError

from kgn.db.repository import KgnRepository
from kgn.models.edge import EdgeRecord
from kgn.models.node import NodeRecord
from kgn.parser.kge_parser import KgeParseError, parse_kge, parse_kge_text
from kgn.parser.kgn_parser import KgnParseError, parse_kgn, parse_kgn_text
from kgn.parser.validator import validate_kgn

log = structlog.get_logger()

# ── Result data classes ────────────────────────────────────────────────


@dataclass
class IngestFileResult:
    """Outcome of ingesting a single file."""

    file_path: str
    status: str  # "SUCCESS" | "SKIPPED" | "FAILED"
    node_id: uuid.UUID | None = None
    error: str | None = None


@dataclass
class IngestBatchResult:
    """Aggregated outcome of a batch ingest."""

    success: int = 0
    skipped: int = 0
    failed: int = 0
    details: list[IngestFileResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.success + self.skipped + self.failed

    @property
    def mutated_node_ids(self) -> list[uuid.UUID]:
        """Return node IDs of successfully created or updated nodes."""
        return [d.node_id for d in self.details if d.status == "SUCCESS" and d.node_id is not None]

    def add(self, result: IngestFileResult) -> None:
        self.details.append(result)
        if result.status == "SUCCESS":
            self.success += 1
        elif result.status == "SKIPPED":
            self.skipped += 1
        else:
            self.failed += 1


# ── Service ────────────────────────────────────────────────────────────


class IngestService:
    """Stateful ingest pipeline for a single batch run.

    Each instance maintains a ``new:`` slug → UUID mapping that lives
    for the duration of one :meth:`ingest_path` call.
    """

    def __init__(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        *,
        enforce_project: bool = False,
    ) -> None:
        self._repo = repo
        self._project_id = project_id
        self._agent_id = agent_id
        self._enforce_project = enforce_project

        # new:slug → generated UUID
        self._new_id_map: dict[str, uuid.UUID] = {}
        # new:slug → source file path (for collision detection)
        self._new_id_sources: dict[str, str] = {}

    # ── Public API ─────────────────────────────────────────────────

    def ingest_path(
        self,
        path: Path,
        *,
        recursive: bool = False,
    ) -> IngestBatchResult:
        """Run the full ingest pipeline on *path*.

        Parameters:
            path: A single ``.kgn`` / ``.kge`` file **or** a directory.
            recursive: When *path* is a directory, search subdirectories.

        Returns:
            IngestBatchResult with per-file details.
        """
        result = IngestBatchResult()
        kgn_files, kge_files = self._collect_files(path, recursive=recursive)

        # Phase 1: .kgn files (nodes must exist before edges)
        for fp in kgn_files:
            file_result = self._ingest_kgn(fp)
            result.add(file_result)
            self._log_ingest(file_result)

        # Phase 2: .kge files (edges — may reference new: IDs)
        for fp in kge_files:
            file_result = self._ingest_kge(fp)
            result.add(file_result)
            self._log_ingest(file_result)

        return result

    def ingest_text(
        self,
        content: str,
        ext: str,
    ) -> IngestBatchResult:
        """Ingest a single node/edge from raw text without touching the filesystem.

        Parameters:
            content: Raw ``.kgn`` or ``.kge`` file content as a string.
            ext: File extension — ``".kgn"`` or ``".kge"``.

        Returns:
            IngestBatchResult with a single entry.
        """
        result = IngestBatchResult()
        if ext == ".kgn":
            file_result = self._ingest_kgn_text(content)
        elif ext == ".kge":
            file_result = self._ingest_kge_text(content)
        else:
            file_result = IngestFileResult(
                file_path="<text>",
                status="FAILED",
                error=f"Unsupported extension: {ext}",
            )
        result.add(file_result)
        self._log_ingest(file_result)
        return result

    # ── File collection ────────────────────────────────────────────

    @staticmethod
    def _collect_files(
        path: Path,
        *,
        recursive: bool = False,
    ) -> tuple[list[Path], list[Path]]:
        """Classify files into (.kgn list, .kge list).

        When *path* is a single file it is placed into the appropriate list.
        When it is a directory, files are globbed.  Results are sorted by
        name for deterministic ordering.
        """
        kgn_files: list[Path] = []
        kge_files: list[Path] = []

        if path.is_file():
            if path.suffix == ".kgn":
                kgn_files.append(path)
            elif path.suffix == ".kge":
                kge_files.append(path)
            return kgn_files, kge_files

        pattern = "**/*" if recursive else "*"
        for fp in sorted(path.glob(pattern)):
            if not fp.is_file():
                continue
            if fp.suffix == ".kgn":
                kgn_files.append(fp)
            elif fp.suffix == ".kge":
                kge_files.append(fp)

        return kgn_files, kge_files

    # ── .kgn processing ───────────────────────────────────────────

    def _ingest_kgn(self, path: Path) -> IngestFileResult:
        """Parse → validate → resolve new: → upsert a single .kgn file."""
        file_str = str(path)

        # 1. Parse
        try:
            parsed = parse_kgn(path)
        except KgnParseError as exc:
            return IngestFileResult(file_path=file_str, status="FAILED", error=str(exc))

        # 2. Validate (V1-V6, V9, V10)
        vr = validate_kgn(parsed)
        if not vr.is_valid:
            return IngestFileResult(
                file_path=file_str,
                status="FAILED",
                error="; ".join(vr.errors),
            )

        # 3. Resolve node ID (new: → UUID)
        fm = parsed.front_matter
        try:
            node_id = self._resolve_node_id(fm.id, file_str)
        except _SlugCollisionError as exc:
            return IngestFileResult(file_path=file_str, status="FAILED", error=str(exc))

        # 4. Resolve project / agent
        project_uuid = self._repo.get_or_create_project(fm.project_id)
        agent_uuid = self._repo.get_or_create_agent(
            project_id=project_uuid,
            agent_key=fm.agent_id,
        )

        # 5. Build NodeRecord
        node = NodeRecord(
            id=node_id,
            project_id=project_uuid,
            type=fm.type,
            status=fm.status,
            title=fm.title,
            body_md=parsed.body,
            file_path=file_str,
            content_hash=parsed.content_hash,
            tags=fm.tags,
            confidence=fm.confidence,
            created_by=agent_uuid,
            created_at=fm.created_at,
        )

        # 6. Upsert
        upsert = self._repo.upsert_node(node)

        # 7. Conflict detection on UPDATE
        if upsert.status == "UPDATED":
            self._check_conflict_on_update(
                node_id=upsert.node_id,
                project_id=project_uuid,
                agent_id=agent_uuid,
            )

        status = "SUCCESS" if upsert.status == "CREATED" else upsert.status
        if upsert.status == "UPDATED":
            status = "SUCCESS"

        return IngestFileResult(
            file_path=file_str,
            status=status,
            node_id=upsert.node_id,
        )

    # ── .kge processing ───────────────────────────────────────────

    def _ingest_kge(self, path: Path) -> IngestFileResult:
        """Parse → resolve IDs → insert edges from a single .kge file."""
        file_str = str(path)

        # 1. Parse
        try:
            edge_fm = parse_kge(path)
        except KgeParseError as exc:
            return IngestFileResult(file_path=file_str, status="FAILED", error=str(exc))

        # 2. Resolve project / agent
        project_uuid = self._repo.get_or_create_project(edge_fm.project_id)
        agent_uuid = self._repo.get_or_create_agent(
            project_id=project_uuid,
            agent_key=edge_fm.agent_id,
        )

        # 3. Insert each edge
        for entry in edge_fm.edges:
            try:
                from_id = self._resolve_edge_ref(entry.from_node, file_str)
                to_id = self._resolve_edge_ref(entry.to, file_str)
            except _IdResolutionError as exc:
                return IngestFileResult(file_path=file_str, status="FAILED", error=str(exc))

            edge = EdgeRecord(
                project_id=project_uuid,
                from_node_id=from_id,
                to_node_id=to_id,
                type=entry.type,
                note=entry.note,
                created_by=agent_uuid,
            )
            try:
                self._repo.savepoint("edge_insert")
                self._repo.insert_edge(edge)
                self._repo.release_savepoint("edge_insert")
            except DatabaseError as exc:
                self._repo.rollback_to_savepoint("edge_insert")
                return IngestFileResult(
                    file_path=file_str,
                    status="FAILED",
                    error=f"DB error inserting edge: {exc}",
                )

        return IngestFileResult(
            file_path=file_str,
            status="SUCCESS",
        )

    # ── Text-based processing (no filesystem) ─────────────────────

    def _ingest_kgn_text(self, content: str) -> IngestFileResult:
        """Parse → validate → resolve new: → upsert from raw .kgn text."""
        source = "<text>"

        # 1. Parse
        try:
            parsed = parse_kgn_text(content, source_path=source)
        except KgnParseError as exc:
            return IngestFileResult(file_path=source, status="FAILED", error=str(exc))

        # 2. Validate
        vr = validate_kgn(parsed)
        if not vr.is_valid:
            return IngestFileResult(
                file_path=source,
                status="FAILED",
                error="; ".join(vr.errors),
            )

        # 3. Resolve node ID
        fm = parsed.front_matter
        try:
            node_id = self._resolve_node_id(fm.id, source)
        except _SlugCollisionError as exc:
            return IngestFileResult(file_path=source, status="FAILED", error=str(exc))

        # 4. Resolve project / agent
        if self._enforce_project:
            project_uuid = self._project_id
        else:
            project_uuid = self._repo.get_or_create_project(fm.project_id)
        agent_uuid = self._repo.get_or_create_agent(
            project_id=project_uuid,
            agent_key=fm.agent_id,
        )

        # 5. Build NodeRecord
        node = NodeRecord(
            id=node_id,
            project_id=project_uuid,
            type=fm.type,
            status=fm.status,
            title=fm.title,
            body_md=parsed.body,
            file_path=source,
            content_hash=parsed.content_hash,
            tags=fm.tags,
            confidence=fm.confidence,
            created_by=agent_uuid,
            created_at=fm.created_at,
        )

        # 6. Upsert
        upsert = self._repo.upsert_node(node)

        # 7. Conflict detection on UPDATE
        if upsert.status == "UPDATED":
            self._check_conflict_on_update(
                node_id=upsert.node_id,
                project_id=project_uuid,
                agent_id=agent_uuid,
            )

        status = "SUCCESS" if upsert.status == "CREATED" else upsert.status
        if upsert.status == "UPDATED":
            status = "SUCCESS"

        return IngestFileResult(
            file_path=source,
            status=status,
            node_id=upsert.node_id,
        )

    def _ingest_kge_text(self, content: str) -> IngestFileResult:
        """Parse → resolve IDs → insert edges from raw .kge text."""
        source = "<text>"

        # 1. Parse
        try:
            edge_fm = parse_kge_text(content)
        except KgeParseError as exc:
            return IngestFileResult(file_path=source, status="FAILED", error=str(exc))

        # 2. Resolve project / agent
        if self._enforce_project:
            project_uuid = self._project_id
        else:
            project_uuid = self._repo.get_or_create_project(edge_fm.project_id)
        agent_uuid = self._repo.get_or_create_agent(
            project_id=project_uuid,
            agent_key=edge_fm.agent_id,
        )

        # 3. Insert each edge
        for entry in edge_fm.edges:
            try:
                from_id = self._resolve_edge_ref(entry.from_node, source)
                to_id = self._resolve_edge_ref(entry.to, source)
            except _IdResolutionError as exc:
                return IngestFileResult(file_path=source, status="FAILED", error=str(exc))

            edge = EdgeRecord(
                project_id=project_uuid,
                from_node_id=from_id,
                to_node_id=to_id,
                type=entry.type,
                note=entry.note,
                created_by=agent_uuid,
            )
            try:
                self._repo.savepoint("edge_insert")
                self._repo.insert_edge(edge)
                self._repo.release_savepoint("edge_insert")
            except DatabaseError as exc:
                self._repo.rollback_to_savepoint("edge_insert")
                return IngestFileResult(
                    file_path=source,
                    status="FAILED",
                    error=f"DB error inserting edge: {exc}",
                )

        return IngestFileResult(
            file_path=source,
            status="SUCCESS",
        )

    # ── ID resolution helpers ──────────────────────────────────────

    def _resolve_node_id(self, raw_id: str, file_path: str) -> uuid.UUID:
        """Convert a raw ``id`` field to UUID.

        - UUID string → parse directly
        - ``new:slug`` → generate UUID, store in mapping, detect collisions
        """
        if raw_id.startswith("new:"):
            slug = raw_id  # keep full "new:slug" as key
            if slug in self._new_id_map:
                existing_source = self._new_id_sources[slug]
                raise _SlugCollisionError(f'"{slug}" already defined in {existing_source}')
            generated = uuid.uuid4()
            self._new_id_map[slug] = generated
            self._new_id_sources[slug] = file_path
            return generated

        return uuid.UUID(raw_id)

    def _resolve_edge_ref(self, ref: str, file_path: str) -> uuid.UUID:
        """Resolve an edge from/to reference.

        - ``new:slug`` → look up in mapping
        - UUID string → parse directly
        """
        if ref.startswith("new:"):
            resolved = self._new_id_map.get(ref)
            if resolved is None:
                raise _IdResolutionError(
                    f'"{ref}" referenced in {file_path} was not defined '
                    f"in any .kgn file in this batch"
                )
            return resolved

        return uuid.UUID(ref)

    # ── Logging helper ─────────────────────────────────────────────

    def _log_ingest(self, result: IngestFileResult) -> None:
        """Write to ``kgn_ingest_log``."""
        error_detail = {"error": result.error} if result.error else None
        self._repo.log_ingest(
            project_id=self._project_id,
            file_path=result.file_path,
            content_hash="",  # hash may not be available on failure
            status=result.status,
            error_detail=error_detail,
            ingested_by=self._agent_id,
        )

    # ── Conflict detection hook ────────────────────────────────────

    def _check_conflict_on_update(
        self,
        node_id: uuid.UUID,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Post-upsert hook: detect concurrent edit and auto-create review task.

        Called after a successful UPDATE (not INSERT). If the previous
        version was written by a different agent, a ConflictResolutionService
        review task is created for a reviewer agent.
        """
        from kgn.orchestration.conflict_resolution import ConflictResolutionService

        try:
            svc = ConflictResolutionService(self._repo)
            det = svc.detect(node_id, agent_id)
            if det.detected and det.previous_agent is not None:
                svc.create_review_task(
                    project_id,
                    node_id,
                    det.previous_agent,
                    agent_id,
                )
        except Exception:  # noqa: BLE001
            # Conflict detection is advisory — never block ingest
            log.debug(
                "conflict_check_failed",
                node_id=str(node_id),
                exc_info=True,
            )


# ── Internal exceptions ───────────────────────────────────────────────


class _SlugCollisionError(Exception):
    """Raised when a ``new:`` slug is used in more than one .kgn file."""


class _IdResolutionError(Exception):
    """Raised when a ``new:`` reference cannot be resolved."""
