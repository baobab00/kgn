"""CLI tests for graph mermaid and graph readme commands.

Uses real DB for project lookup, mocks MermaidGenerator.
Requires a running PostgreSQL instance (Docker on port 5433).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from kgn.cli import app

runner = CliRunner()


def _init_project(name: str) -> None:
    runner.invoke(app, ["init", "--project", name])


# ══════════════════════════════════════════════════════════════════════
# graph mermaid
# ══════════════════════════════════════════════════════════════════════


class TestGraphMermaidCLI:
    def test_mermaid_project_not_found(self) -> None:
        result = runner.invoke(app, ["graph", "mermaid", "--project", "nonexistent-xyz-999"])
        assert result.exit_code == 1

    def test_mermaid_graph_happy(self) -> None:
        proj = f"cli-gm-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.graph.mermaid import MermaidResult

        mock_result = MermaidResult(
            diagram="flowchart TD\n  A --> B",
            node_count=2,
            edge_count=1,
        )

        with patch("kgn.graph.mermaid.MermaidGenerator") as MockGen:
            MockGen.return_value.generate_graph.return_value = mock_result
            result = runner.invoke(app, ["graph", "mermaid", "--project", proj])
        assert result.exit_code == 0
        assert "mermaid" in result.output
        assert "flowchart" in result.output

    def test_mermaid_task_board(self) -> None:
        proj = f"cli-gm-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.graph.mermaid import MermaidResult

        mock_result = MermaidResult(
            diagram="flowchart LR\n  READY --> IN_PROGRESS",
            node_count=3,
            edge_count=2,
        )

        with patch("kgn.graph.mermaid.MermaidGenerator") as MockGen:
            MockGen.return_value.generate_task_board.return_value = mock_result
            result = runner.invoke(app, ["graph", "mermaid", "--project", proj, "--task-board"])
        assert result.exit_code == 0
        assert "mermaid" in result.output

    def test_mermaid_with_root(self) -> None:
        proj = f"cli-gm-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.graph.mermaid import MermaidResult

        root_id = str(uuid.uuid4())
        mock_result = MermaidResult(diagram="flowchart TD\n  A", node_count=1, edge_count=0)

        with patch("kgn.graph.mermaid.MermaidGenerator") as MockGen:
            MockGen.return_value.generate_graph.return_value = mock_result
            result = runner.invoke(
                app,
                [
                    "graph",
                    "mermaid",
                    "--project",
                    proj,
                    "--root",
                    root_id,
                    "--depth",
                    "2",
                ],
            )
        assert result.exit_code == 0

    def test_mermaid_no_status(self) -> None:
        proj = f"cli-gm-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.graph.mermaid import MermaidResult

        mock_result = MermaidResult(diagram="flowchart TD\n  X", node_count=1, edge_count=0)

        with patch("kgn.graph.mermaid.MermaidGenerator") as MockGen:
            MockGen.return_value.generate_graph.return_value = mock_result
            result = runner.invoke(
                app,
                ["graph", "mermaid", "--project", proj, "--no-status"],
            )
        assert result.exit_code == 0


# ══════════════════════════════════════════════════════════════════════
# graph readme
# ══════════════════════════════════════════════════════════════════════


class TestGraphReadmeCLI:
    def test_readme_project_not_found(self) -> None:
        result = runner.invoke(app, ["graph", "readme", "--project", "nonexistent-xyz-999"])
        assert result.exit_code == 1

    def test_readme_happy(self, tmp_path: Path) -> None:
        proj = f"cli-gr-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        readme_path = tmp_path / "README.md"

        with patch("kgn.graph.mermaid.MermaidGenerator") as MockGen:
            MockGen.return_value.generate_readme.return_value = readme_path
            result = runner.invoke(
                app,
                [
                    "graph",
                    "readme",
                    "--project",
                    proj,
                    "--target",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0
        assert "README generated" in result.output

    def test_readme_error(self, tmp_path: Path) -> None:
        proj = f"cli-gr-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        with patch("kgn.graph.mermaid.MermaidGenerator") as MockGen:
            MockGen.return_value.generate_readme.side_effect = Exception("disk full")
            result = runner.invoke(
                app,
                [
                    "graph",
                    "readme",
                    "--project",
                    proj,
                    "--target",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 1
        assert "disk full" in result.output
