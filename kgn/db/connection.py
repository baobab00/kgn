"""Database connection pool management."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from psycopg import Connection
from psycopg_pool import ConnectionPool

_PKG_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


def _find_env_file() -> Path | None:
    """Find .env file: CWD first, then package source root."""
    cwd_env = Path.cwd() / ".env"
    if cwd_env.is_file():
        return cwd_env
    if _PKG_ENV_FILE.is_file():
        return _PKG_ENV_FILE
    return None


# Keep backward-compatible alias for existing imports (e.g. tests)
_ENV_FILE = _PKG_ENV_FILE


def _load_env() -> None:
    """Load .env file if it exists. Uses override=False so existing
    environment variables are never silently overwritten (R-001)."""
    env_file = _find_env_file()
    if env_file is not None:
        load_dotenv(env_file, override=False)


def _load_db_config() -> dict[str, str | int]:
    """Load database configuration from environment variables."""
    _load_env()
    return {
        "host": os.environ.get("KGN_DB_HOST", "localhost"),
        "port": int(os.environ.get("KGN_DB_PORT", "5432")),
        "dbname": os.environ.get("KGN_DB_NAME", "kgn"),
        "user": os.environ.get("KGN_DB_USER", "kgn"),
        "password": os.environ.get("KGN_DB_PASSWORD", ""),
    }


def _build_conninfo(config: dict[str, str | int]) -> str:
    """Build psycopg connection string from config dict."""
    return (
        f"host={config['host']} "
        f"port={config['port']} "
        f"dbname={config['dbname']} "
        f"user={config['user']} "
        f"password={config['password']}"
    )


_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Get or create the global connection pool.

    Commit policy:
        ``pool.connection()`` wraps each checkout in a transaction.
        On normal exit the connection is **auto-committed**; on exception
        it is **rolled back**.  Therefore MCP tools that obtain a
        connection via ``get_connection()`` do **not** need to call
        ``conn.commit()`` — the pool context manager handles it.

        CLI commands that use the same ``get_connection()`` helper call
        ``conn.commit()`` explicitly for clarity, which is harmless
        (double-commit on an already-committed transaction is a no-op
        in psycopg 3's default transaction mode).
    """
    global _pool  # noqa: PLW0603
    if _pool is None:
        _load_env()
        config = _load_db_config()
        conninfo = _build_conninfo(config)
        min_size = int(os.environ.get("KGN_DB_POOL_MIN", "1"))
        max_size = int(os.environ.get("KGN_DB_POOL_MAX", "5"))
        _pool = ConnectionPool(
            conninfo=conninfo,
            min_size=min_size,
            max_size=max_size,
            # Phase 5: resilience settings
            timeout=float(os.environ.get("KGN_DB_POOL_TIMEOUT", "10.0")),
            max_idle=float(os.environ.get("KGN_DB_POOL_MAX_IDLE", "300.0")),
            reconnect_timeout=float(os.environ.get("KGN_DB_POOL_RECONNECT_TIMEOUT", "30.0")),
            open=True,
        )
    return _pool


def close_pool() -> None:
    """Close the global connection pool."""
    global _pool  # noqa: PLW0603
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def get_connection() -> Generator[Connection, None, None]:
    """Get a connection from the pool as a context manager.

    The returned connection is wrapped by ``pool.connection()`` which
    auto-commits on successful exit and rolls back on exception.
    """
    pool = get_pool()
    with pool.connection() as conn:
        yield conn
