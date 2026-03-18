"""KGN MCP server — FastMCP-based server factory.

R12: No direct SQL/business logic in MCP tool handlers — reuse existing service layers only.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from kgn.db.connection import get_connection
from kgn.db.repository import KgnRepository
from kgn.embedding.factory import create_embedding_client
from kgn.mcp._state import KgnServerState
from kgn.mcp.tools.read import register_read_tools
from kgn.mcp.tools.task import register_task_tools
from kgn.mcp.tools.workflow import register_workflow_tools
from kgn.mcp.tools.write import register_write_tools

if TYPE_CHECKING:
    from collections.abc import Generator

    from psycopg import Connection

    from kgn.embedding.client import EmbeddingClient

# Sentinel to distinguish "not provided" from explicit None
_SENTINEL = object()


# ── Connection helpers ─────────────────────────────────────────────────


@contextmanager
def _default_connection() -> Generator[Connection, None, None]:
    """Default connection provider using the global pool.

    Commit policy: ``get_connection()`` → ``pool.connection()`` context
    manager auto-commits on success and rolls back on exception.
    Individual ``conn.commit()`` calls are intentionally omitted in
    MCP tool handlers.
    """
    with get_connection() as conn:
        yield conn


@contextmanager
def _fixed_connection(conn: Connection) -> Generator[Connection, None, None]:
    """Wrap an existing connection as a context manager (for tests)."""
    yield conn


# ── Server factory ─────────────────────────────────────────────────────


def create_server(
    project_name: str,
    *,
    conn: Connection | None = None,
    embedding_client: EmbeddingClient | None = _SENTINEL,  # type: ignore[assignment]
    role: str = "admin",
) -> FastMCP:
    """Create and configure a FastMCP server instance for the given project.

    Parameters
    ----------
    project_name:
        Project name. Must exist in DB; validated at server start.
    conn:
        Optional DB connection. If None, acquired from global pool.
        Used for injecting transactional connections in tests.
    embedding_client:
        Embedding client. By default, auto-created via ``create_embedding_client()``
        factory. Pass ``None`` explicitly to disable embeddings.
    role:
        Default agent role for this MCP session. Agents created via this
        server will use this role. Defaults to ``"admin"``.

    Returns
    -------
    FastMCP
        Configured MCP server instance.

    Raises
    ------
    SystemExit
        When the project does not exist in DB.
    """
    # ── Verify project exists ─────────────────────────────────────
    project_id = _resolve_project(project_name, conn=conn)

    server = FastMCP(
        name=f"kgn-{project_name}",
    )

    # Build typed state
    conn_factory = (lambda: _fixed_connection(conn)) if conn is not None else _default_connection  # noqa: E731
    embed_client = create_embedding_client() if embedding_client is _SENTINEL else embedding_client

    state = KgnServerState(
        project_id=project_id,
        project_name=project_name,
        agent_role=role,
        conn_factory=conn_factory,
        embed_client=embed_client,
    )
    server._kgn_state = state  # type: ignore[attr-defined]

    # ── Register tools ────────────────────────────────────────────
    register_read_tools(server)
    register_task_tools(server)
    register_workflow_tools(server)
    register_write_tools(server)

    return server


# ── Helpers ────────────────────────────────────────────────────────────


def _resolve_project(
    project_name: str,
    *,
    conn: Connection | None = None,
) -> uuid.UUID:
    """Resolve project name to UUID, raising SystemExit if not found."""
    if conn is not None:
        repo = KgnRepository(conn)
        project_id = repo.get_project_by_name(project_name)
    else:
        with get_connection() as pool_conn:
            repo = KgnRepository(pool_conn)
            project_id = repo.get_project_by_name(project_name)
    if project_id is None:
        raise SystemExit(
            f"Project '{project_name}' not found in DB. "
            f"Run 'kgn init --project {project_name}' first."
        )
    return project_id
