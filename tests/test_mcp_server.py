"""Tests for kgn MCP server initialization (Phase 4 Step 2).

Server skeleton verification:
- create_server() factory function returns FastMCP instance
- Non-existent project raises SystemExit
- CLI `kgn mcp serve` command registration
- Transport validation
"""

from __future__ import annotations

import uuid

import pytest
from mcp.server.fastmcp import FastMCP

# ── create_server factory ──────────────────────────────────────────────


class TestCreateServer:
    """Tests for create_server() factory function."""

    def test_returns_fastmcp_instance(self, db_conn, repo, project_id) -> None:
        """Returns FastMCP instance with valid project name."""
        from kgn.mcp.server import create_server

        project_name = f"mcp-test-{uuid.uuid4().hex[:8]}"
        # Create project first
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn)

        assert isinstance(server, FastMCP)

    def test_server_name_includes_project(self, db_conn, repo, project_id) -> None:
        """Server name should include the project name."""
        from kgn.mcp.server import create_server

        project_name = f"mcp-test-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn)

        assert project_name in server.name

    def test_project_id_stored_on_server(self, db_conn, repo, project_id) -> None:
        """Server instance should store project_id."""
        from kgn.mcp.server import create_server

        project_name = f"mcp-test-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn)

        assert server._kgn_project_id == pid  # noqa: SLF001
        assert server._kgn_project_name == project_name  # noqa: SLF001

    def test_nonexistent_project_raises_system_exit(self) -> None:
        """Non-existent project name raises SystemExit."""
        from kgn.mcp.server import create_server

        with pytest.raises(SystemExit, match="not found in DB"):
            create_server(f"no-such-project-{uuid.uuid4().hex[:8]}")


# ── CLI registration ──────────────────────────────────────────────────


class TestMCPServeCLI:
    """Tests for CLI `kgn mcp serve` command."""

    def test_mcp_serve_registered(self) -> None:
        """kgn mcp serve command should be registered."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "serve" in result.output

    def test_mcp_serve_requires_project(self) -> None:
        """Error when running without --project option."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["mcp", "serve"])
        assert result.exit_code != 0

    def test_mcp_serve_invalid_transport(self, repo, project_id) -> None:
        """Unsupported transport raises error."""
        from typer.testing import CliRunner

        from kgn.cli import app

        project_name = f"mcp-cli-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["mcp", "serve", "--project", project_name, "--transport", "websocket"],
        )
        assert result.exit_code == 1
        assert "Unsupported transport" in result.output

    def test_mcp_serve_nonexistent_project(self) -> None:
        """Serve with non-existent project raises error."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["mcp", "serve", "--project", f"ghost-{uuid.uuid4().hex[:8]}"],
        )
        assert result.exit_code != 0

    def test_mcp_serve_default_transport_is_stdio(self) -> None:
        """Default transport should be stdio."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["mcp", "serve", "--help"])
        assert result.exit_code == 0
        assert "stdio" in result.output
