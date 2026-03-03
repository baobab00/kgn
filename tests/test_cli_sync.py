"""CLI tests for sync commands: export, import, status, push, pull.

Uses real DB but mocks service-layer calls where needed.
Requires a running PostgreSQL instance (Docker on port 5433).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from kgn.cli import app

runner = CliRunner()


# ── Helpers ────────────────────────────────────────────────────────


def _init_project(name: str) -> None:
    runner.invoke(app, ["init", "--project", name])


# ══════════════════════════════════════════════════════════════════════
# sync export
# ══════════════════════════════════════════════════════════════════════


class TestSyncExportCLI:
    def test_export_project_not_found(self) -> None:
        result = runner.invoke(app, ["sync", "export", "--project", "nonexistent-xyz-999"])
        assert result.exit_code == 1

    def test_export_happy_path(self, tmp_path: Path) -> None:
        proj = f"cli-exp-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.sync.export_service import ExportResult

        mock_result = ExportResult(exported=3, skipped=1, deleted=0)

        with (
            patch("kgn.sync.export_service.ExportService") as MockExport,
            patch("kgn.graph.mermaid.MermaidGenerator") as MockMermaid,
        ):
            MockExport.return_value.export_project.return_value = mock_result
            MockMermaid.return_value.generate_readme.return_value = tmp_path / "README.md"

            result = runner.invoke(
                app,
                ["sync", "export", "--project", proj, "--target", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert "Export complete" in result.output

    def test_export_with_errors(self, tmp_path: Path) -> None:
        proj = f"cli-exp-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.sync.export_service import ExportResult

        mock_result = ExportResult(exported=1, skipped=0, deleted=0, errors=["bad file"])

        with (
            patch("kgn.sync.export_service.ExportService") as MockExport,
            patch("kgn.graph.mermaid.MermaidGenerator") as MockMermaid,
        ):
            MockExport.return_value.export_project.return_value = mock_result
            MockMermaid.return_value.generate_readme.return_value = tmp_path / "README.md"

            result = runner.invoke(
                app,
                ["sync", "export", "--project", proj, "--target", str(tmp_path)],
            )
        assert result.exit_code == 1
        assert "bad file" in result.output

    def test_export_with_commit(self, tmp_path: Path) -> None:
        proj = f"cli-exp-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.sync.export_service import ExportResult

        mock_result = ExportResult(exported=2, skipped=0, deleted=0)

        with (
            patch("kgn.sync.export_service.ExportService") as MockExport,
            patch("kgn.graph.mermaid.MermaidGenerator") as MockMermaid,
            patch("kgn.git.service.GitService") as MockGit,
        ):
            MockExport.return_value.export_project.return_value = mock_result
            mock_result_obj = MagicMock()
            mock_result_obj.total = 3
            MockExport.return_value.export_project.return_value = mock_result
            MockMermaid.return_value.generate_readme.return_value = tmp_path / "README.md"

            from kgn.git.service import GitResult

            MockGit.return_value.commit.return_value = GitResult(success=True, message="committed")

            result = runner.invoke(
                app,
                [
                    "sync",
                    "export",
                    "--project",
                    proj,
                    "--target",
                    str(tmp_path),
                    "--commit",
                ],
            )
        assert result.exit_code == 0


# ══════════════════════════════════════════════════════════════════════
# sync import
# ══════════════════════════════════════════════════════════════════════


class TestSyncImportCLI:
    def test_import_happy_path(self, tmp_path: Path) -> None:
        proj = f"cli-imp-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.sync.import_service import ImportResult

        mock_result = ImportResult(imported=5, skipped=2, failed=0)

        with patch("kgn.sync.import_service.ImportService") as MockImport:
            MockImport.return_value.import_project.return_value = mock_result
            result = runner.invoke(
                app,
                ["sync", "import", "--project", proj, "--source", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert "Import complete" in result.output

    def test_import_with_errors(self, tmp_path: Path) -> None:
        proj = f"cli-imp-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.sync.import_service import ImportResult

        mock_result = ImportResult(imported=0, skipped=0, failed=1, errors=["parse error"])

        with patch("kgn.sync.import_service.ImportService") as MockImport:
            MockImport.return_value.import_project.return_value = mock_result
            result = runner.invoke(
                app,
                ["sync", "import", "--project", proj, "--source", str(tmp_path)],
            )
        assert result.exit_code == 1
        assert "parse error" in result.output


# ══════════════════════════════════════════════════════════════════════
# sync status
# ══════════════════════════════════════════════════════════════════════


class TestSyncStatusCLI:
    def test_status_project_not_found(self) -> None:
        result = runner.invoke(app, ["sync", "status", "--project", "nonexistent-xyz-999"])
        assert result.exit_code == 1

    def test_status_happy_path(self, tmp_path: Path) -> None:
        proj = f"cli-ss-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.sync.import_service import SyncStatus

        mock_status = SyncStatus(
            db_node_count=10,
            db_edge_count=5,
            file_node_count=8,
            file_edge_count=5,
            last_export="2026-01-01 12:00:00",
            last_import=None,
        )

        with patch("kgn.sync.import_service.get_sync_status", return_value=mock_status):
            result = runner.invoke(
                app,
                ["sync", "status", "--project", proj, "--target", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert "Nodes" in result.output

    def test_status_in_sync(self, tmp_path: Path) -> None:
        proj = f"cli-ss-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.sync.import_service import SyncStatus

        mock_status = SyncStatus(
            db_node_count=5,
            db_edge_count=3,
            file_node_count=5,
            file_edge_count=3,
        )

        with patch("kgn.sync.import_service.get_sync_status", return_value=mock_status):
            result = runner.invoke(
                app,
                ["sync", "status", "--project", proj, "--target", str(tmp_path)],
            )
        assert result.exit_code == 0


# ══════════════════════════════════════════════════════════════════════
# sync push
# ══════════════════════════════════════════════════════════════════════


class TestSyncPushCLI:
    def test_push_project_not_found(self) -> None:
        result = runner.invoke(app, ["sync", "push", "--project", "nonexistent-xyz-999"])
        assert result.exit_code == 1

    def test_push_happy_path(self, tmp_path: Path) -> None:
        proj = f"cli-push-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.github.sync_service import SyncResult

        mock_result = SyncResult(
            success=True,
            action="push",
            message="Push complete",
            exported=3,
            committed=True,
            pushed=True,
        )

        with (
            patch("kgn.git.service.GitService"),
            patch("kgn.github.sync_service.SyncService") as MockSync,
        ):
            MockSync.return_value.push.return_value = mock_result
            result = runner.invoke(
                app,
                [
                    "sync",
                    "push",
                    "--project",
                    proj,
                    "--target",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0
        assert "Push" in result.output

    def test_push_failure(self, tmp_path: Path) -> None:
        proj = f"cli-push-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.github.sync_service import SyncResult

        mock_result = SyncResult(
            success=False,
            action="push",
            message="Push failed: no remote",
        )

        with (
            patch("kgn.git.service.GitService"),
            patch("kgn.github.sync_service.SyncService") as MockSync,
        ):
            MockSync.return_value.push.return_value = mock_result
            result = runner.invoke(
                app,
                [
                    "sync",
                    "push",
                    "--project",
                    proj,
                    "--target",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# sync pull
# ══════════════════════════════════════════════════════════════════════


class TestSyncPullCLI:
    def test_pull_invalid_strategy(self) -> None:
        result = runner.invoke(
            app,
            ["sync", "pull", "--project", "any", "--strategy", "bad-strategy"],
        )
        assert result.exit_code == 1
        assert "Invalid strategy" in result.output

    def test_pull_happy_path(self, tmp_path: Path) -> None:
        proj = f"cli-pull-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.github.sync_service import SyncResult

        mock_result = SyncResult(
            success=True,
            action="pull",
            message="Pull complete",
            imported=5,
            pulled=True,
        )

        with (
            patch("kgn.git.service.GitService"),
            patch("kgn.github.sync_service.SyncService") as MockSync,
        ):
            MockSync.return_value.pull.return_value = mock_result
            result = runner.invoke(
                app,
                [
                    "sync",
                    "pull",
                    "--project",
                    proj,
                    "--target",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0

    def test_pull_with_conflicts(self, tmp_path: Path) -> None:
        proj = f"cli-pull-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.github.sync_service import ConflictInfo, SyncResult

        mock_result = SyncResult(
            success=False,
            action="pull",
            message="Pull had conflicts",
            imported=2,
            pulled=True,
            conflicts=[
                ConflictInfo(file_path="nodes/test.kgn", reason="hash mismatch"),
            ],
        )

        with (
            patch("kgn.git.service.GitService"),
            patch("kgn.github.sync_service.SyncService") as MockSync,
        ):
            MockSync.return_value.pull.return_value = mock_result
            result = runner.invoke(
                app,
                [
                    "sync",
                    "pull",
                    "--project",
                    proj,
                    "--target",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 1
        assert "Conflicts" in result.output
