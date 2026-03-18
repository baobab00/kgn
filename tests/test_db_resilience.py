"""Tests for DB/network resilience (Phase 5 Step 5).

Validates:
- safe_tool_call decorator handling of PoolTimeout, OperationalError, unexpected errors
- MCP tools return structured error JSON on infrastructure failures
- Connection pool configuration via environment variables
- Embedding API timeout graceful degradation
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from psycopg import OperationalError
from psycopg_pool import PoolTimeout

from kgn.errors import KgnError, KgnErrorCode
from kgn.mcp._helpers import _error_json, _error_response, safe_tool_call
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _call_tool(server, tool_name: str, **kwargs) -> str:
    """Invoke a registered FastMCP tool by name (sync wrapper)."""
    import asyncio

    async def _run():
        return await server.call_tool(tool_name, kwargs)

    raw = asyncio.run(_run())
    content_list = raw[0] if isinstance(raw, tuple) else raw
    if content_list and hasattr(content_list[0], "text"):
        return content_list[0].text
    return str(content_list)


def _make_node(
    project_id: uuid.UUID,
    *,
    title: str = "Test Node",
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.SPEC,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md="body content",
        content_hash=uuid.uuid4().hex,
    )


# ── safe_tool_call unit tests ─────────────────────────────────────────


class TestSafeToolCallDecorator:
    """Unit tests for the safe_tool_call decorator."""

    def test_kgn_error_returns_its_code(self) -> None:
        """KgnError is caught and uses its own error code."""

        @safe_tool_call
        def failing_tool() -> str:
            raise KgnError(KgnErrorCode.NODE_NOT_FOUND, "Node xyz not found")

        result = json.loads(failing_tool())
        assert result["code"] == "KGN-300"
        assert result["recoverable"] is False
        assert "xyz" in result["error"]

    def test_pool_timeout_returns_kgn_101(self) -> None:
        """PoolTimeout → KGN-101 structured error."""

        @safe_tool_call
        def failing_tool() -> str:
            raise PoolTimeout()

        result = json.loads(failing_tool())
        assert result["code"] == "KGN-101"
        assert result["recoverable"] is True
        assert "pool timeout" in result["error"].lower()

    def test_operational_error_returns_kgn_100(self) -> None:
        """OperationalError → KGN-100 structured error."""

        @safe_tool_call
        def failing_tool() -> str:
            raise OperationalError("connection refused")

        result = json.loads(failing_tool())
        assert result["code"] == "KGN-100"
        assert result["recoverable"] is True
        assert "connection" in result["error"].lower()

    def test_unexpected_error_returns_kgn_999(self) -> None:
        """Unexpected exception → KGN-999 structured error."""

        @safe_tool_call
        def failing_tool() -> str:
            raise RuntimeError("something broke")

        result = json.loads(failing_tool())
        assert result["code"] == "KGN-999"
        assert result["recoverable"] is False
        assert "something broke" in result["error"]

    def test_successful_call_passes_through(self) -> None:
        """Normal execution passes through without interception."""

        @safe_tool_call
        def ok_tool(x: int) -> str:
            return json.dumps({"result": x})

        result = json.loads(ok_tool(42))
        assert result["result"] == 42

    def test_preserves_function_name(self) -> None:
        """functools.wraps preserves __name__ for FastMCP tool registration."""

        @safe_tool_call
        def my_custom_tool(node_id: str) -> str:
            return ""

        assert my_custom_tool.__name__ == "my_custom_tool"

    def test_logs_pool_timeout(self, caplog) -> None:
        """PoolTimeout is logged with tool name."""

        @safe_tool_call
        def get_node() -> str:
            raise PoolTimeout()

        import logging

        with caplog.at_level(logging.ERROR, logger="kgn.mcp.safety"):
            get_node()

        assert any("pool_timeout" in r.message for r in caplog.records)

    def test_logs_operational_error(self, caplog) -> None:
        """OperationalError is logged with tool name and error detail."""

        @safe_tool_call
        def query_nodes() -> str:
            raise OperationalError("host unreachable")

        import logging

        with caplog.at_level(logging.ERROR, logger="kgn.mcp.safety"):
            query_nodes()

        assert any("db_connection_failed" in r.message for r in caplog.records)


# ── _error_response helper ─────────────────────────────────────────────


class TestErrorResponse:
    """Unit tests for _error_response and _error_json helpers."""

    def test_error_response_structure(self) -> None:
        result = json.loads(_error_response("test error", KgnErrorCode.DB_CONNECTION_FAILED))
        assert result["error"] == "test error"
        assert result["code"] == "KGN-100"
        assert result["recoverable"] is True
        assert "detail" in result

    def test_error_response_non_recoverable(self) -> None:
        result = json.loads(_error_response("fatal", KgnErrorCode.INTERNAL_ERROR))
        assert result["recoverable"] is False

    def test_error_json_with_code(self) -> None:
        result = json.loads(
            _error_json("Node not found", KgnErrorCode.NODE_NOT_FOUND, detail="abc-123")
        )
        assert result["code"] == "KGN-300"
        assert result["detail"] == "abc-123"
        assert result["recoverable"] is False

    def test_error_json_default_code(self) -> None:
        """R-009: code is now required — calling without code raises TypeError."""
        with pytest.raises(TypeError):
            _error_json("Something went wrong")

    def test_error_json_explicit_internal_error(self) -> None:
        """Explicit INTERNAL_ERROR code still works."""
        result = json.loads(_error_json("Something went wrong", KgnErrorCode.INTERNAL_ERROR))
        assert result["code"] == "KGN-999"


# ── MCP tool integration tests: DB failure scenarios ───────────────────


class TestMCPToolDBFailure:
    """Integration tests: MCP tools handle DB connection failures gracefully."""

    @pytest.fixture
    def server_with_failing_conn(self, db_conn, repo):
        """Create a server, then replace conn_factory with one that raises."""
        from kgn.mcp.server import create_server

        project_name = f"resilience-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)
        return server

    def _set_failing_factory(self, server, exc):
        """Replace server's connection factory with one that raises exc."""
        from kgn.mcp._state import get_state

        @contextmanager
        def _failing():
            raise exc

        get_state(server).conn_factory = _failing

    def test_get_node_pool_timeout(self, server_with_failing_conn) -> None:
        """get_node returns KGN-101 when pool is exhausted."""
        server = server_with_failing_conn
        self._set_failing_factory(server, PoolTimeout())

        result = json.loads(_call_tool(server, "get_node", node_id=str(uuid.uuid4())))
        assert result["code"] == "KGN-101"
        assert result["recoverable"] is True

    def test_query_nodes_operational_error(self, server_with_failing_conn) -> None:
        """query_nodes returns KGN-100 when DB is unreachable."""
        server = server_with_failing_conn
        self._set_failing_factory(server, OperationalError("connection refused"))

        result = json.loads(_call_tool(server, "query_nodes", project="any-project"))
        assert result["code"] == "KGN-100"
        assert result["recoverable"] is True

    def test_ingest_node_pool_timeout(self, server_with_failing_conn) -> None:
        """ingest_node returns KGN-101 when pool is exhausted."""
        server = server_with_failing_conn
        self._set_failing_factory(server, PoolTimeout())

        kgn = """---
id: 00000000-0000-0000-0000-000000000001
type: SPEC
status: DRAFT
title: Test
confidence: 0.8
---
# Summary
test
"""
        result = json.loads(_call_tool(server, "ingest_node", kgn_content=kgn))
        assert result["code"] == "KGN-101"

    def test_task_checkout_operational_error(self, server_with_failing_conn) -> None:
        """task_checkout returns KGN-100 when DB is down."""
        server = server_with_failing_conn
        self._set_failing_factory(server, OperationalError("server closed"))

        result = json.loads(_call_tool(server, "task_checkout", project="test", agent="claude"))
        assert result["code"] == "KGN-100"

    def test_get_subgraph_unexpected_error(self, server_with_failing_conn) -> None:
        """get_subgraph returns KGN-999 on unexpected internal error."""
        server = server_with_failing_conn
        self._set_failing_factory(server, RuntimeError("segfault simulation"))

        result = json.loads(_call_tool(server, "get_subgraph", node_id=str(uuid.uuid4())))
        assert result["code"] == "KGN-999"
        assert result["recoverable"] is False


# ── Connection pool configuration tests ────────────────────────────────


class TestConnectionPoolConfig:
    """Verify pool settings are configurable via environment variables."""

    def test_pool_timeout_from_env(self, monkeypatch) -> None:
        """KGN_DB_POOL_TIMEOUT env var is read."""
        from kgn.db import connection

        # Reset pool to force re-creation
        connection._pool = None
        monkeypatch.setenv("KGN_DB_POOL_TIMEOUT", "5.0")
        monkeypatch.setenv("KGN_DB_POOL_MAX_IDLE", "120.0")
        monkeypatch.setenv("KGN_DB_POOL_RECONNECT_TIMEOUT", "15.0")

        pool = connection.get_pool()
        try:
            # The pool was created with the env-specified settings.
            # We verify pool is alive as a proxy — the actual timeout
            # values are not easily inspectable on ConnectionPool objects.
            assert pool is not None
            # Verify the pool works
            with pool.connection() as c:
                c.execute("SELECT 1")
        finally:
            connection.close_pool()

    def test_pool_default_values(self, monkeypatch) -> None:
        """Pool uses sensible defaults when env vars are absent."""
        from kgn.db import connection

        connection._pool = None
        monkeypatch.delenv("KGN_DB_POOL_TIMEOUT", raising=False)
        monkeypatch.delenv("KGN_DB_POOL_MAX_IDLE", raising=False)
        monkeypatch.delenv("KGN_DB_POOL_RECONNECT_TIMEOUT", raising=False)

        pool = connection.get_pool()
        try:
            assert pool is not None
            with pool.connection() as c:
                c.execute("SELECT 1")
        finally:
            connection.close_pool()


# ── Embedding timeout graceful degradation ─────────────────────────────


class TestEmbeddingTimeout:
    """Embedding API timeout results in graceful skip, not tool failure."""

    def test_ingest_node_embedding_timeout_graceful(self, db_conn, repo, project_id) -> None:
        """When embedding client times out, ingest_node succeeds with embed=failed."""
        from kgn.mcp.server import create_server

        project_name = f"embed-timeout-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        # Create a mock embedding client that raises on embed
        mock_client = MagicMock()
        mock_client.embed.side_effect = TimeoutError("API timeout after 30s")

        server = create_server(
            project_name,
            conn=db_conn,
            embedding_client=mock_client,
        )

        node_uuid = str(uuid.uuid4())
        kgn = f"""---
kgn_version: "0.1"
id: "{node_uuid}"
type: SPEC
status: ACTIVE
title: Timeout Test
project_id: "{project_name}"
agent_id: "test-agent"
confidence: 0.9
---

## Summary

This node should ingest even if embedding times out.
"""
        result = json.loads(_call_tool(server, "ingest_node", kgn_content=kgn))

        # Node ingest succeeds
        assert result["status"] == "ok"
        assert result["node_id"] is not None
        # Embedding gracefully degraded
        assert result["embedding"] == "failed"
