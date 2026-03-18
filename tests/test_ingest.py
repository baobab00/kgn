"""Integration tests for the ingest pipeline (Step 6).

Requires a running PostgreSQL instance (Docker).
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.ingest.service import IngestBatchResult, IngestService

# ── Fixtures ───────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"
INGEST_DIR = FIXTURES_DIR / "ingest"


@pytest.fixture
def svc(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> IngestService:
    """IngestService bound to the test repo/project/agent."""
    return IngestService(repo=repo, project_id=project_id, agent_id=agent_id)


# ── Helpers ────────────────────────────────────────────────────────────


def _assert_batch(
    batch: IngestBatchResult,
    *,
    success: int,
    skipped: int = 0,
    failed: int = 0,
) -> None:
    assert batch.success == success, f"success: {batch.success} != {success}"
    assert batch.skipped == skipped, f"skipped: {batch.skipped} != {skipped}"
    assert batch.failed == failed, f"failed: {batch.failed} != {failed}"


# ── Single .kgn file ──────────────────────────────────────────────────


class TestSingleKgnFile:
    """Ingest a single .kgn file."""

    def test_ingest_single_kgn_success(self, svc: IngestService) -> None:
        """Valid .kgn → SUCCESS, node is created."""
        batch = svc.ingest_path(INGEST_DIR / "node_a.kgn")
        _assert_batch(batch, success=1)
        assert batch.details[0].node_id is not None

    def test_ingest_duplicate_skipped(self, svc: IngestService) -> None:
        """Same file twice → second is SKIPPED (V8 content_hash)."""
        svc.ingest_path(INGEST_DIR / "node_a.kgn")

        # new IngestService to reset the new: mapping
        svc2 = IngestService(
            repo=svc._repo,
            project_id=svc._project_id,
            agent_id=svc._agent_id,
        )
        batch2 = svc2.ingest_path(INGEST_DIR / "node_a.kgn")
        _assert_batch(batch2, success=0, skipped=1)

    def test_ingest_invalid_kgn_failed(self, svc: IngestService) -> None:
        """Invalid file → FAILED."""
        batch = svc.ingest_path(FIXTURES_DIR / "invalid_type.kgn")
        _assert_batch(batch, success=0, failed=1)
        assert batch.details[0].error is not None

    def test_ingest_no_front_matter_failed(self, svc: IngestService) -> None:
        """File without front matter → FAILED."""
        batch = svc.ingest_path(FIXTURES_DIR / "no_front_matter.kgn")
        _assert_batch(batch, success=0, failed=1)

    def test_ingest_valid_spec_uuid(self, svc: IngestService) -> None:
        """File with a real UUID id → SUCCESS."""
        batch = svc.ingest_path(FIXTURES_DIR / "valid_spec.kgn")
        _assert_batch(batch, success=1)
        detail = batch.details[0]
        assert detail.node_id == uuid.UUID("550e8400-e29b-41d4-a716-446655440000")


# ── Single .kge file ──────────────────────────────────────────────────


class TestSingleKgeFile:
    """Ingest a single .kge file after its referenced nodes exist."""

    def test_ingest_kge_with_uuid_refs(self, svc: IngestService) -> None:
        """Edge file referencing UUID node not in DB → FAILED (FK)."""
        svc.ingest_path(FIXTURES_DIR / "valid_spec.kgn")

        # edges.kge references 450e... which doesn't exist → FK violation
        batch = svc.ingest_path(FIXTURES_DIR / "edges.kge")
        _assert_batch(batch, success=0, failed=1)
        assert "DB error" in (batch.details[0].error or "")


# ── new: ID resolution ────────────────────────────────────────────────


class TestNewIdResolution:
    """Test new: slug → UUID mapping within a batch."""

    def test_new_id_generates_uuid(self, svc: IngestService) -> None:
        """new: ID in .kgn → UUID is generated."""
        batch = svc.ingest_path(INGEST_DIR / "node_a.kgn")
        _assert_batch(batch, success=1)
        node_id = batch.details[0].node_id
        assert node_id is not None
        assert isinstance(node_id, uuid.UUID)

    def test_new_id_mapped_in_kge(self, svc: IngestService) -> None:
        """new: slugs defined in .kgn files are resolved in .kge edges."""
        batch = svc.ingest_path(INGEST_DIR)
        # node_a.kgn, node_b.kgn → SUCCESS; edges_ab.kge → SUCCESS
        # collision.kgn → FAILED (slug collision); edges_bad_ref.kge → FAILED
        successes = [d for d in batch.details if d.status == "SUCCESS"]
        assert len(successes) >= 3  # node_a + node_b + edges_ab

    def test_slug_collision_fails(self, svc: IngestService) -> None:
        """Same new: slug in two .kgn files → second file FAILED."""
        batch = svc.ingest_path(INGEST_DIR)
        # Both collision.kgn and node_a.kgn define "new:auth-spec".
        # Sorted alphabetically, collision.kgn is processed first (SUCCESS)
        # and node_a.kgn second (FAILED due to slug collision).
        slug_failed = [
            d for d in batch.details if d.status == "FAILED" and "new:auth-spec" in (d.error or "")
        ]
        assert len(slug_failed) == 1
        assert "already defined" in (slug_failed[0].error or "")

    def test_unresolved_slug_in_kge_fails(self, svc: IngestService) -> None:
        """Edge referencing undefined new: slug → FAILED."""
        # Only ingest node_a (which defines new:auth-spec)
        svc.ingest_path(INGEST_DIR / "node_a.kgn")

        # Now ingest edges_bad_ref.kge which references new:nonexistent-slug
        batch = svc.ingest_path(INGEST_DIR / "edges_bad_ref.kge")
        _assert_batch(batch, success=0, failed=1)
        assert "nonexistent-slug" in (batch.details[0].error or "")


# ── Directory ingest ──────────────────────────────────────────────────


class TestDirectoryIngest:
    """Ingest files from a directory."""

    def test_non_recursive_top_level_only(self, svc: IngestService) -> None:
        """Non-recursive ingest only picks top-level files."""
        batch = svc.ingest_path(INGEST_DIR, recursive=False)
        paths = {d.file_path for d in batch.details}
        # Should NOT include sub/nested.kgn
        assert not any("nested.kgn" in p for p in paths)
        assert batch.total > 0

    def test_recursive_includes_subdirs(self, svc: IngestService) -> None:
        """Recursive ingest picks subdirectory files too."""
        batch = svc.ingest_path(INGEST_DIR, recursive=True)
        paths = {d.file_path for d in batch.details}
        assert any("nested.kgn" in p for p in paths)

    def test_kgn_before_kge_ordering(self, svc: IngestService) -> None:
        """.kgn files are processed before .kge files."""
        batch = svc.ingest_path(INGEST_DIR)
        kgn_indices = [i for i, d in enumerate(batch.details) if d.file_path.endswith(".kgn")]
        kge_indices = [i for i, d in enumerate(batch.details) if d.file_path.endswith(".kge")]
        if kgn_indices and kge_indices:
            assert max(kgn_indices) < min(kge_indices)


# ── Error isolation ───────────────────────────────────────────────────


class TestErrorIsolation:
    """Individual file errors do not stop the batch."""

    def test_batch_continues_after_failure(self, svc: IngestService) -> None:
        """Batch with a mix of valid/invalid files continues processing."""
        batch = svc.ingest_path(INGEST_DIR)
        # Should have some successes AND some failures
        assert batch.success > 0
        assert batch.failed > 0
        assert batch.total == batch.success + batch.skipped + batch.failed


# ── Ingest log ────────────────────────────────────────────────────────


class TestIngestLog:
    """Verify kgn_ingest_log entries."""

    def test_log_written_for_each_file(
        self,
        svc: IngestService,
        db_conn: Connection,
        project_id: uuid.UUID,
    ) -> None:
        """Each processed file should have a kgn_ingest_log entry."""
        batch = svc.ingest_path(INGEST_DIR)
        log_count = db_conn.execute(
            "SELECT COUNT(*) FROM kgn_ingest_log WHERE project_id = %s",
            (project_id,),
        ).fetchone()
        assert log_count is not None
        assert log_count[0] == batch.total

    def test_failed_log_has_error_detail(
        self,
        svc: IngestService,
        db_conn: Connection,
    ) -> None:
        """FAILED entries should have error_detail populated."""
        svc.ingest_path(INGEST_DIR)
        failed_logs = db_conn.execute(
            "SELECT error_detail FROM kgn_ingest_log WHERE status = 'FAILED'",
        ).fetchall()
        assert len(failed_logs) > 0
        for row in failed_logs:
            assert row[0] is not None  # error_detail should be present

    def test_success_log_has_content_hash(
        self,
        svc: IngestService,
        db_conn: Connection,
        project_id: uuid.UUID,
    ) -> None:
        """SUCCESS entries for .kgn files should record actual content_hash (Phase 12 / Step 9)."""
        svc.ingest_path(INGEST_DIR)
        rows = db_conn.execute(
            "SELECT content_hash FROM kgn_ingest_log "
            "WHERE project_id = %s AND status = 'SUCCESS' AND file_path LIKE '%%.kgn'",
            (project_id,),
        ).fetchall()
        assert len(rows) > 0
        for row in rows:
            assert row[0] != "", "content_hash should not be empty on .kgn success"


# ── Edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases for the ingest pipeline."""

    def test_empty_directory(self, svc: IngestService, tmp_path: Path) -> None:
        """Empty directory → zero results."""
        batch = svc.ingest_path(tmp_path)
        _assert_batch(batch, success=0)
        assert batch.total == 0

    def test_non_kgn_files_ignored(self, svc: IngestService, tmp_path: Path) -> None:
        """Files with non-.kgn/.kge extensions are silently ignored."""
        (tmp_path / "readme.md").write_text("# Hello")
        (tmp_path / "data.json").write_text("{}")
        batch = svc.ingest_path(tmp_path)
        _assert_batch(batch, success=0)
        assert batch.total == 0

    def test_single_file_wrong_extension(self, svc: IngestService, tmp_path: Path) -> None:
        """Passing a .txt file directly → zero results (silently ignored)."""
        f = tmp_path / "notes.txt"
        f.write_text("hello")
        batch = svc.ingest_path(f)
        _assert_batch(batch, success=0)
        assert batch.total == 0

    def test_ingest_kgn_with_uuid_id(self, svc: IngestService) -> None:
        """Node with real UUID id (not new:) works correctly."""
        batch = svc.ingest_path(FIXTURES_DIR / "valid_spec.kgn")
        _assert_batch(batch, success=1)
        assert batch.details[0].node_id == uuid.UUID("550e8400-e29b-41d4-a716-446655440000")

    def test_ingest_path_recursive_with_copy(
        self,
        svc: IngestService,
        tmp_path: Path,
    ) -> None:
        """Recursive ingest with copied fixture tree."""
        src = INGEST_DIR / "node_a.kgn"
        sub = tmp_path / "level1" / "level2"
        sub.mkdir(parents=True)
        shutil.copy(src, sub / "deep_node.kgn")

        batch = svc.ingest_path(tmp_path, recursive=True)
        _assert_batch(batch, success=1)
        assert "deep_node.kgn" in batch.details[0].file_path
