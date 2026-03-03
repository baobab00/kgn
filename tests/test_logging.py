"""Tests for structured logging configuration (Phase 5 Step 4).

Validates:
- configure_logging() sets up structlog → stdlib bridge
- JSON and console renderers
- stderr mode for MCP stdio
- MCP tool handlers emit structured log events
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest

from tests.helpers import MockEmbeddingClient

# ── configure_logging ──────────────────────────────────────────────────


class TestConfigureLogging:
    """Tests for kgn.logging.config.configure_logging()."""

    def test_json_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        """JSON format should produce parseable JSON output."""
        from kgn.logging.config import configure_logging

        configure_logging(level="DEBUG", fmt="json")

        logger = logging.getLogger("kgn.test.json")
        logger.info("hello_json")

        captured = capsys.readouterr()
        # JSON renderer writes to stdout by default
        line = captured.out.strip().split("\n")[-1]
        data = json.loads(line)
        assert data["event"] == "hello_json"

    def test_json_format_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        """stderr=True should send output to stderr."""
        from kgn.logging.config import configure_logging

        configure_logging(level="DEBUG", fmt="json", stderr=True)

        logger = logging.getLogger("kgn.test.stderr")
        logger.info("hello_stderr")

        captured = capsys.readouterr()
        assert captured.out == ""  # nothing on stdout
        assert "hello_stderr" in captured.err

    def test_console_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Console format should produce human-readable output."""
        from kgn.logging.config import configure_logging

        configure_logging(level="DEBUG", fmt="console")

        logger = logging.getLogger("kgn.test.console")
        logger.warning("hello_console")

        captured = capsys.readouterr()
        assert "hello_console" in captured.out

    def test_log_level_filtering(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Log level should filter lower-priority messages."""
        from kgn.logging.config import configure_logging

        configure_logging(level="WARNING", fmt="json")

        logger = logging.getLogger("kgn.test.level")
        logger.debug("should_not_appear")
        logger.info("should_not_appear_either")
        logger.warning("should_appear")

        captured = capsys.readouterr()
        assert "should_not_appear" not in captured.out.split("should_appear")[0]
        assert "should_appear" in captured.out

    def test_structlog_logger_works(self, capsys: pytest.CaptureFixture[str]) -> None:
        """structlog.get_logger() should produce output via stdlib."""
        import structlog

        from kgn.logging.config import configure_logging

        configure_logging(level="DEBUG", fmt="json")

        log = structlog.get_logger("kgn.test.structlog")
        log.info("structured_event", key="value", count=42)

        captured = capsys.readouterr()
        line = captured.out.strip().split("\n")[-1]
        data = json.loads(line)
        assert data["event"] == "structured_event"
        assert data["key"] == "value"
        assert data["count"] == 42


# ── MCP Tool Logging Events ───────────────────────────────────────────


class TestMCPToolLogging:
    """Verify that MCP tool handlers emit structured log events."""

    def _make_kgn_content(self, project_id: str = "test-project") -> str:
        return (
            "---\n"
            'kgn_version: "0.1"\n'
            'id: "new:logging-test"\n'
            "type: SPEC\n"
            'title: "Logging Test Node"\n'
            "status: ACTIVE\n"
            f'project_id: "{project_id}"\n'
            'agent_id: "mcp"\n'
            "---\n"
            "## Content\n\nTest body.\n"
        )

    def test_ingest_node_logs_tool_called_and_completed(
        self,
        db_conn,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """ingest_node should emit tool_called and tool_completed events."""
        import asyncio

        from kgn.db.repository import KgnRepository
        from kgn.mcp.server import create_server

        repo = KgnRepository(db_conn)
        project_name = f"log-test-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn, embedding_client=None)
        kgn_content = self._make_kgn_content(project_id=project_name)

        with caplog.at_level(logging.INFO, logger="kgn.mcp.write"):
            asyncio.run(server.call_tool("ingest_node", {"kgn_content": kgn_content}))

        messages = " ".join(caplog.messages)
        assert "tool_called" in messages
        assert "tool_completed" in messages
        assert "ingest_node" in messages

    def test_get_node_logs_events(
        self,
        db_conn,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """get_node should emit tool_called and tool_completed events."""
        import asyncio

        from kgn.db.repository import KgnRepository
        from kgn.mcp.server import create_server

        repo = KgnRepository(db_conn)
        project_name = f"log-test-{uuid.uuid4().hex[:8]}"
        project_id = repo.get_or_create_project(project_name)
        agent_id = repo.get_or_create_agent(project_id, "test")

        # Create a node to retrieve
        from kgn.ingest.service import IngestService

        svc = IngestService(repo, project_id, agent_id)
        kgn_content = self._make_kgn_content(project_id=project_name)
        result = svc.ingest_text(kgn_content, ".kgn")
        node_id = str(result.details[0].node_id)

        server = create_server(project_name, conn=db_conn, embedding_client=None)

        with caplog.at_level(logging.INFO, logger="kgn.mcp.read"):
            asyncio.run(server.call_tool("get_node", {"node_id": node_id}))

        messages = " ".join(caplog.messages)
        assert "tool_called" in messages
        assert "tool_completed" in messages
        assert "get_node" in messages

    def test_ingest_node_with_embedding_logs_status(
        self,
        db_conn,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """ingest_node with embedding client should log embedding status."""
        import asyncio

        from kgn.db.repository import KgnRepository
        from kgn.mcp.server import create_server

        repo = KgnRepository(db_conn)
        project_name = f"log-test-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        mock_client = MockEmbeddingClient()
        server = create_server(
            project_name,
            conn=db_conn,
            embedding_client=mock_client,
        )
        kgn_content = self._make_kgn_content(project_id=project_name)

        with caplog.at_level(logging.INFO, logger="kgn.mcp.write"):
            asyncio.run(server.call_tool("ingest_node", {"kgn_content": kgn_content}))

        messages = " ".join(caplog.messages)
        assert "tool_completed" in messages
        # Should contain embedding status
        assert "'embedding': 'success'" in messages or "success" in messages

    def test_task_checkout_logs_empty(
        self,
        db_conn,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """task_checkout with no tasks should log 'empty' result."""
        import asyncio

        from kgn.db.repository import KgnRepository
        from kgn.mcp.server import create_server

        repo = KgnRepository(db_conn)
        project_name = f"log-test-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn, embedding_client=None)

        with caplog.at_level(logging.INFO, logger="kgn.mcp.task"):
            asyncio.run(
                server.call_tool(
                    "task_checkout",
                    {"project": project_name, "agent": "test"},
                )
            )

        messages = " ".join(caplog.messages)
        assert "tool_called" in messages
        assert "tool_completed" in messages
        assert "empty" in messages
