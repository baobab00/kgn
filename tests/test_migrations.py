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
