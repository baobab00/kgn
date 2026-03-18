"""Tests for db/migrations.py — migration runner edge cases.

Covers:
- _get_migration_files() when MIGRATIONS_DIR doesn't exist
- _is_applied() returning True for already-applied migrations
- run_migrations() skipping already-applied, printing correctly
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from kgn.db.migrations import _get_migration_files, _is_applied, run_migrations


class TestGetMigrationFiles:
    def test_returns_empty_for_nonexistent_dir(self, tmp_path: Path) -> None:
        """Returns [] when MIGRATIONS_DIR doesn't exist."""
        fake_dir = tmp_path / "no-such-dir"
        with patch("kgn.db.migrations.MIGRATIONS_DIR", fake_dir):
            result = _get_migration_files()
        assert result == []

    def test_returns_sorted_files(self, tmp_path: Path) -> None:
        """Returns SQL files sorted by name."""
        (tmp_path / "002_b.sql").write_text("SELECT 1;")
        (tmp_path / "001_a.sql").write_text("SELECT 1;")
        (tmp_path / "not_sql.txt").write_text("ignore")

        with patch("kgn.db.migrations.MIGRATIONS_DIR", tmp_path):
            result = _get_migration_files()

        assert len(result) == 2
        assert result[0].name == "001_a.sql"
        assert result[1].name == "002_b.sql"


class TestIsApplied:
    def test_already_applied(self, db_conn) -> None:
        """Returns True for a migration that's recorded."""
        # All standard migrations should already be applied
        result = _is_applied(db_conn, "001_init_enums.sql")
        assert result is True

    def test_not_applied(self, db_conn) -> None:
        """Returns False for unknown migration."""
        result = _is_applied(db_conn, "999_does_not_exist.sql")
        assert result is False


class TestRunMigrations:
    def test_skips_already_applied(self, db_conn) -> None:
        """Re-running migrations skips all already-applied ones."""
        applied = run_migrations(db_conn)
        # All migrations should be skipped (0 newly applied)
        assert applied == []


class TestMigration010NodeVersionsFullSnapshot:
    """Verify migration 010 adds the 5 new columns to node_versions."""

    def test_new_columns_exist(self, db_conn) -> None:
        """After migration, node_versions has type/status/file_path/tags/confidence."""
        row = db_conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'node_versions' "
            "AND column_name IN ('type', 'status', 'file_path', 'tags', 'confidence') "
            "ORDER BY column_name",
        ).fetchall()
        col_names = sorted(r[0] for r in row)
        assert col_names == ["confidence", "file_path", "status", "tags", "type"]

    def test_existing_rows_have_null_new_columns(self, db_conn) -> None:
        """Pre-existing node_versions rows have NULL for new columns."""
        row = db_conn.execute(
            "SELECT type, status, file_path, confidence FROM node_versions LIMIT 1",
        ).fetchone()
        # If there are rows, they should have NULLs (backfill not done)
        if row is not None:
            assert row[0] is None  # type
            assert row[1] is None  # status
            assert row[2] is None  # file_path
            assert row[3] is None  # confidence

    def test_migration_is_recorded(self, db_conn) -> None:
        """010_node_versions_full_snapshot.sql is in migration history."""
        assert _is_applied(db_conn, "010_node_versions_full_snapshot.sql")
