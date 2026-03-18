"""Typed server state for KGN MCP server."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kgn.embedding.client import EmbeddingClient


@dataclass
class KgnServerState:
    """Typed state attached to a FastMCP server instance.

    Replaces the previous pattern of dynamic ``_kgn_*`` attributes
    with ``# type: ignore[attr-defined]`` annotations.
    """

    project_id: uuid.UUID
    project_name: str
    agent_role: str = "admin"
    conn_factory: Any = field(default=None)  # Callable[[], ContextManager[Connection]]
    embed_client: EmbeddingClient | None = None


def get_state(server: Any) -> KgnServerState:
    """Retrieve typed ``KgnServerState`` from a FastMCP server."""
    return server._kgn_state  # type: ignore[attr-defined]
