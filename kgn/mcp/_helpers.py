"""Shared helper functions for MCP tool modules."""

from __future__ import annotations

import functools
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import structlog
from psycopg import OperationalError
from psycopg_pool import PoolTimeout

from kgn.errors import _RECOVERABLE_CODES, KgnError, KgnErrorCode
from kgn.models.enums import NodeStatus, NodeType

_log = structlog.get_logger("kgn.mcp.safety")


def _node_to_dict(node) -> dict:
    """Convert a NodeRecord to a JSON-serialisable dict."""
    return {
        "id": str(node.id),
        "project_id": str(node.project_id),
        "type": node.type.value if hasattr(node.type, "value") else str(node.type),
        "status": node.status.value if hasattr(node.status, "value") else str(node.status),
        "title": node.title,
        "body_md": node.body_md,
        "tags": node.tags,
        "confidence": node.confidence,
    }


def _error_json(
    message: str,
    code: KgnErrorCode,
    *,
    detail: str = "",
) -> str:
    """Return a structured JSON error string.

    ``code`` is a required positional argument so that callers are forced
    to specify an explicit error code — no accidental ``INTERNAL_ERROR``
    fallback (R-009).

    All MCP error responses go through this function so that every error
    carries ``code`` and ``recoverable`` fields for agent consumption.
    """
    return json.dumps(
        {
            "error": message,
            "code": code.value,
            "detail": detail or message,
            "recoverable": code in _RECOVERABLE_CODES,
        },
        ensure_ascii=False,
    )


def _parse_uuid(value: str) -> uuid.UUID | None:
    """Parse a UUID string, returning None on failure."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def _parse_node_type(value: str) -> NodeType | None:
    """Parse a node type string, returning None on failure."""
    try:
        return NodeType(value.upper())
    except ValueError:
        return None


def _parse_node_status(value: str) -> NodeStatus | None:
    """Parse a node status string, returning None on failure."""
    try:
        return NodeStatus(value.upper())
    except ValueError:
        return None


def _subgraph_node_to_dict(node) -> dict:
    """Convert a SubgraphNode dataclass to a JSON-serialisable dict."""
    return {
        "id": str(node.id),
        "type": node.type,
        "status": node.status,
        "title": node.title,
        "body_md": node.body_md,
        "depth": node.depth,
    }


# ── Infrastructure Error Handling ──────────────────────────────────────


def _error_response(
    message: str,
    code: KgnErrorCode,
) -> str:
    """Build structured error JSON for infrastructure failures.

    Delegates to ``_error_json`` so all error responses share the same
    shape: ``{error, code, detail, recoverable}``.
    """
    return _error_json(message, code)


def safe_tool_call(func: Callable[..., str]) -> Callable[..., str]:
    """Decorator: catch DB/infrastructure errors and return structured JSON.

    Wraps MCP tool functions to handle:
    - ``KgnError`` → use its ``code`` directly
    - ``PoolTimeout`` → ``KGN-101`` (pool exhausted / connection wait timeout)
    - ``OperationalError`` → ``KGN-100`` (DB down / connection refused)
    - Unexpected ``Exception`` → ``KGN-999`` (internal error)

    Business-logic errors (invalid UUID, not found, etc.) are handled
    inside each tool and are NOT intercepted by this wrapper.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        t0 = time.monotonic()
        try:
            return func(*args, **kwargs)
        except KgnError as exc:
            elapsed = round((time.monotonic() - t0) * 1000)
            _log.error(
                "kgn_error",
                tool=func.__name__,
                code=exc.code.value,
                error=str(exc),
                duration_ms=elapsed,
            )
            return _error_response(str(exc), exc.code)
        except PoolTimeout:
            elapsed = round((time.monotonic() - t0) * 1000)
            _log.error(
                "pool_timeout",
                tool=func.__name__,
                duration_ms=elapsed,
            )
            return _error_response(
                "Connection pool timeout — all connections busy",
                KgnErrorCode.DB_TIMEOUT,
            )
        except OperationalError as exc:
            elapsed = round((time.monotonic() - t0) * 1000)
            _log.error(
                "db_connection_failed",
                tool=func.__name__,
                error=str(exc),
                duration_ms=elapsed,
            )
            return _error_response(
                f"DB connection failed: {exc}",
                KgnErrorCode.DB_CONNECTION_FAILED,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = round((time.monotonic() - t0) * 1000)
            _log.error(
                "unexpected_error",
                tool=func.__name__,
                error=str(exc),
                duration_ms=elapsed,
            )
            return _error_response(
                f"Internal error: {exc}",
                KgnErrorCode.INTERNAL_ERROR,
            )

    return wrapper
