"""Tests for kgn mcp init command."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from kgn.cli import app

runner = CliRunner()


class TestMcpInit:
    """Tests for `kgn mcp init`."""

    def test_claude_code_creates_mcp_json(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "mcp",
                "init",
                "--project",
                "my-proj",
                "--target",
                "claude-code",
                "--output",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        cfg = json.loads((tmp_path / ".mcp.json").read_text("utf-8"))
        assert "kgn" in cfg["mcpServers"]
        entry = cfg["mcpServers"]["kgn"]
        assert "--project" in entry["args"]
        idx = entry["args"].index("--project")
        assert entry["args"][idx + 1] == "my-proj"
        # default role=admin should NOT appear in args
        assert "--role" not in entry["args"]

    def test_claude_code_with_role(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "mcp",
                "init",
                "--project",
                "my-proj",
                "--target",
                "claude-code",
                "--role",
                "worker",
                "--output",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        cfg = json.loads((tmp_path / ".mcp.json").read_text("utf-8"))
        entry = cfg["mcpServers"]["kgn"]
        idx = entry["args"].index("--role")
        assert entry["args"][idx + 1] == "worker"

    def test_claude_code_merges_existing(self, tmp_path: Path) -> None:
        existing = {"mcpServers": {"other": {"command": "x", "args": []}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(existing), encoding="utf-8")
        result = runner.invoke(
            app,
            [
                "mcp",
                "init",
                "--project",
                "test",
                "--target",
                "claude-code",
                "--output",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        cfg = json.loads((tmp_path / ".mcp.json").read_text("utf-8"))
        assert "other" in cfg["mcpServers"]
        assert "kgn" in cfg["mcpServers"]

    def test_claude_desktop_creates_config(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "mcp",
                "init",
                "--project",
                "desk-proj",
                "--target",
                "claude-desktop",
                "--output",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        cfg_path = tmp_path / "claude_desktop_config.json"
        assert cfg_path.is_file()
        cfg = json.loads(cfg_path.read_text("utf-8"))
        assert "kgn" in cfg["mcpServers"]
        entry = cfg["mcpServers"]["kgn"]
        idx = entry["args"].index("--project")
        assert entry["args"][idx + 1] == "desk-proj"

    def test_claude_desktop_merges_existing(self, tmp_path: Path) -> None:
        existing = {"mcpServers": {"excalidraw": {"command": "y"}}}
        (tmp_path / "claude_desktop_config.json").write_text(json.dumps(existing), encoding="utf-8")
        result = runner.invoke(
            app,
            [
                "mcp",
                "init",
                "--project",
                "test",
                "--target",
                "claude-desktop",
                "--output",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        cfg = json.loads((tmp_path / "claude_desktop_config.json").read_text("utf-8"))
        assert "excalidraw" in cfg["mcpServers"]
        assert "kgn" in cfg["mcpServers"]

    def test_invalid_target(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "mcp",
                "init",
                "--project",
                "x",
                "--target",
                "invalid",
                "--output",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1

    def test_invalid_role(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "mcp",
                "init",
                "--project",
                "x",
                "--role",
                "bad",
                "--output",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1

    def test_default_target_is_claude_code(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "mcp",
                "init",
                "--project",
                "def-proj",
                "--output",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert (tmp_path / ".mcp.json").is_file()

    def test_args_contain_directory(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "mcp",
                "init",
                "--project",
                "test",
                "--output",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        cfg = json.loads((tmp_path / ".mcp.json").read_text("utf-8"))
        entry = cfg["mcpServers"]["kgn"]
        assert "--directory" in entry["args"]
