"""Database migration runner."""

from __future__ import annotations

from pathlib import Path

from psycopg import Connection
from rich.console import Console

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

console = Console()


def _get_migration_files() -> list[Path]:
    """Get sorted list of SQL migration files."""
    if not MIGRATIONS_DIR.is_dir():
        return []
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return files


def _ensure_migration_table(conn: Connection) -> None:
    """Create migration tracking table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migration_history (
            id          serial PRIMARY KEY,
            filename    text NOT NULL UNIQUE,
            applied_at  timestamptz NOT NULL DEFAULT now()
        )
    """)


def _is_applied(conn: Connection, filename: str) -> bool:
    """Check if a migration has already been applied."""
    row = conn.execute(
        "SELECT 1 FROM _migration_history WHERE filename = %s",
        (filename,),
    ).fetchone()
    return row is not None


def _record_migration(conn: Connection, filename: str) -> None:
    """Record that a migration has been applied."""
    conn.execute(
        "INSERT INTO _migration_history (filename) VALUES (%s)",
        (filename,),
    )


def run_migrations(conn: Connection) -> list[str]:
    """Run all pending migrations in order.

    Returns list of applied migration filenames.
    """
    _ensure_migration_table(conn)

    migration_files = _get_migration_files()
    applied: list[str] = []

    for filepath in migration_files:
        filename = filepath.name
        if _is_applied(conn, filename):
            console.print(f"  [dim]⏭  {filename} (already applied)[/dim]")
            continue

        sql = filepath.read_text(encoding="utf-8")
        conn.execute(sql)
        _record_migration(conn, filename)
        applied.append(filename)
        console.print(f"  [green]✅ {filename}[/green]")

    return applied
