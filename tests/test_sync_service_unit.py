"""Unit tests for SyncService push/pull pipelines (R-035).

All external dependencies (ExportService, ImportService, GitService,
GitHubClient) are mocked so tests run without DB or git.
Targets the uncovered lines in ``github/sync_service.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kgn.errors import KgnError, KgnErrorCode
from kgn.github.client import GitHubConfig
from kgn.github.sync_service import (
    ConflictDetector,
    ConflictStrategy,
    SyncService,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_sync(
    *,
    strategy: ConflictStrategy = ConflictStrategy.DB_WINS,
    github_client: MagicMock | None = None,
) -> tuple[SyncService, MagicMock]:
    """Return (SyncService, mock_git_service)."""
    git_mock = MagicMock()
    svc = SyncService(
        git_service=git_mock,
        github_client=github_client,
        conflict_strategy=strategy,
    )
    return svc, git_mock


def _ids() -> tuple[str, uuid.UUID, uuid.UUID]:
    return "test-proj", uuid.uuid4(), uuid.uuid4()


@dataclass
class _FakeExportResult:
    exported: int = 3
    skipped: int = 0
    deleted: int = 0
    errors: list[str] | None = None


@dataclass
class _FakeImportResult:
    imported: int = 2
    skipped: int = 1
    errors: list[str] | None = None


@dataclass
class _FakeGitResult:
    success: bool = True
    message: str = "ok"


# ══════════════════════════════════════════════════════════════════════
#  push() pipeline
# ══════════════════════════════════════════════════════════════════════


class TestPushExportFailed:
    """push() returns failure when ExportService raises."""

    def test_export_exception_returns_failure(self, tmp_path: Path) -> None:
        svc, git = _make_sync()
        name, pid, aid = _ids()

        with patch("kgn.sync.export_service.ExportService") as MockExport:
            MockExport.return_value.export_project.side_effect = RuntimeError("disk full")
            result = svc.push(
                project_name=name,
                project_id=pid,
                sync_dir=tmp_path,
                repo=MagicMock(),
            )

        assert result.success is False
        assert "Export failed" in result.message


class TestPushCommitFailed:
    """push() returns failure when git commit raises a non-KgnError."""

    def test_commit_exception_returns_failure(self, tmp_path: Path) -> None:
        svc, git = _make_sync()
        name, pid, aid = _ids()

        with patch("kgn.sync.export_service.ExportService") as MockExport:
            MockExport.return_value.export_project.return_value = _FakeExportResult()
            git.commit.side_effect = RuntimeError("lock file exists")

            result = svc.push(
                project_name=name,
                project_id=pid,
                sync_dir=tmp_path,
                repo=MagicMock(),
            )

        assert result.success is False
        assert "Commit failed" in result.message

    def test_commit_kgnerror_propagates(self, tmp_path: Path) -> None:
        svc, git = _make_sync()
        name, pid, aid = _ids()

        with patch("kgn.sync.export_service.ExportService") as MockExport:
            MockExport.return_value.export_project.return_value = _FakeExportResult()
            git.commit.side_effect = KgnError(KgnErrorCode.GIT_COMMAND_FAILED, "bad")

            with pytest.raises(KgnError):
                svc.push(
                    project_name=name,
                    project_id=pid,
                    sync_dir=tmp_path,
                    repo=MagicMock(),
                )


class TestPushRemoteSuccess:
    """push() completes on remote push success."""

    def test_push_complete(self, tmp_path: Path) -> None:
        svc, git = _make_sync()
        name, pid, aid = _ids()

        with patch("kgn.sync.export_service.ExportService") as MockExport:
            MockExport.return_value.export_project.return_value = _FakeExportResult()
            git.commit.return_value = _FakeGitResult(message="[main abc123]")
            git.push.return_value = None  # success

            result = svc.push(
                project_name=name,
                project_id=pid,
                sync_dir=tmp_path,
                repo=MagicMock(),
            )

        assert result.success is True
        assert result.pushed is True
        assert result.message == "Push complete"
        assert result.exported == 3


class TestPushRemoteFailed:
    """push() handles push KgnError — remote vs non-remote."""

    def test_push_no_remote_still_succeeds(self, tmp_path: Path) -> None:
        svc, git = _make_sync()
        name, pid, aid = _ids()

        with patch("kgn.sync.export_service.ExportService") as MockExport:
            MockExport.return_value.export_project.return_value = _FakeExportResult()
            git.commit.return_value = _FakeGitResult(message="[main abc123]")
            git.push.side_effect = KgnError(
                KgnErrorCode.GIT_COMMAND_FAILED,
                "No configured push destination",
            )

            result = svc.push(
                project_name=name,
                project_id=pid,
                sync_dir=tmp_path,
                repo=MagicMock(),
            )

        assert result.success is True
        assert result.pushed is False
        assert "no remote" in result.message.lower()

    def test_push_real_error_fails(self, tmp_path: Path) -> None:
        svc, git = _make_sync()
        name, pid, aid = _ids()

        with patch("kgn.sync.export_service.ExportService") as MockExport:
            MockExport.return_value.export_project.return_value = _FakeExportResult()
            git.commit.return_value = _FakeGitResult(message="[main abc123]")
            git.push.side_effect = KgnError(
                KgnErrorCode.GIT_COMMAND_FAILED,
                "Authentication failed for repo",
            )

            result = svc.push(
                project_name=name,
                project_id=pid,
                sync_dir=tmp_path,
                repo=MagicMock(),
            )

        assert result.success is False
        assert "Push failed" in result.message


class TestPushCustomMessage:
    """push() uses custom commit message when provided."""

    def test_custom_message(self, tmp_path: Path) -> None:
        svc, git = _make_sync()
        name, pid, aid = _ids()

        with patch("kgn.sync.export_service.ExportService") as MockExport:
            MockExport.return_value.export_project.return_value = _FakeExportResult()
            git.commit.return_value = _FakeGitResult(message="[main abc]")
            git.push.return_value = None

            svc.push(
                project_name=name,
                project_id=pid,
                sync_dir=tmp_path,
                repo=MagicMock(),
                message="custom msg",
            )

        git.commit.assert_called_once_with("custom msg")


# ══════════════════════════════════════════════════════════════════════
#  pull() pipeline
# ══════════════════════════════════════════════════════════════════════


class TestPullMergeConflictManual:
    """pull() raises KgnError when conflicts found & strategy is manual."""

    def test_manual_conflicts_raise(self, tmp_path: Path) -> None:
        svc, git = _make_sync(strategy=ConflictStrategy.MANUAL)
        name, pid, aid = _ids()

        git.pull.return_value = _FakeGitResult(
            success=False,
            message="CONFLICT (content): Merge conflict in nodes/SPEC/a.kgn",
        )

        with pytest.raises(KgnError) as exc_info:
            svc.pull(
                project_name=name,
                project_id=pid,
                agent_id=aid,
                sync_dir=tmp_path,
                repo=MagicMock(),
            )

        assert exc_info.value.code == KgnErrorCode.SYNC_CONFLICT_UNRESOLVED


class TestPullMergeConflictAutoResolve:
    """pull() auto-resolves conflicts for db-wins strategy."""

    def test_db_wins_aborts_merge_then_imports(self, tmp_path: Path) -> None:
        svc, git = _make_sync(strategy=ConflictStrategy.DB_WINS)
        name, pid, aid = _ids()

        git.pull.return_value = _FakeGitResult(
            success=False,
            message="CONFLICT (content): Merge conflict in nodes/SPEC/a.kgn",
        )

        with (
            patch("kgn.sync.import_service.ImportService") as MockImport,
            patch("kgn.sync.export_service.ExportService") as MockExport,
        ):
            MockImport.return_value.import_project.return_value = _FakeImportResult()
            MockExport.return_value.export_project.return_value = _FakeExportResult()
            git.commit.return_value = _FakeGitResult(message="resolved")

            result = svc.pull(
                project_name=name,
                project_id=pid,
                agent_id=aid,
                sync_dir=tmp_path,
                repo=MagicMock(),
            )

        assert result.success is True
        assert result.has_conflicts is True
        # merge --abort should have been called (R-023: now followed by status check)
        git._run.assert_any_call("merge", "--abort", check=False)
        git._run.assert_any_call("status", "--porcelain", check=False)
        # Post-conflict re-export called
        MockExport.return_value.export_project.assert_called_once()


class TestPullConflictResolveFailed:
    """pull() logs warning when post-conflict resolution export fails."""

    def test_resolve_exception_does_not_crash(self, tmp_path: Path) -> None:
        svc, git = _make_sync(strategy=ConflictStrategy.DB_WINS)
        name, pid, aid = _ids()

        git.pull.return_value = _FakeGitResult(
            success=False,
            message="CONFLICT (content): Merge conflict in nodes/SPEC/a.kgn",
        )

        with (
            patch("kgn.sync.import_service.ImportService") as MockImport,
            patch("kgn.sync.export_service.ExportService") as MockExport,
        ):
            MockImport.return_value.import_project.return_value = _FakeImportResult()
            # Post-conflict re-export fails
            MockExport.return_value.export_project.side_effect = RuntimeError("boom")

            result = svc.pull(
                project_name=name,
                project_id=pid,
                agent_id=aid,
                sync_dir=tmp_path,
                repo=MagicMock(),
            )

        # Should still succeed (import worked)
        assert result.success is True


class TestPullImportFailed:
    """pull() returns failure when ImportService raises."""

    def test_import_exception_returns_failure(self, tmp_path: Path) -> None:
        svc, git = _make_sync()
        name, pid, aid = _ids()

        git.pull.return_value = _FakeGitResult(success=True, message="up to date")

        with patch("kgn.sync.import_service.ImportService") as MockImport:
            MockImport.return_value.import_project.side_effect = RuntimeError("bad data")

            result = svc.pull(
                project_name=name,
                project_id=pid,
                agent_id=aid,
                sync_dir=tmp_path,
                repo=MagicMock(),
            )

        assert result.success is False
        assert "Import failed" in result.message


class TestPullNoRemote:
    """pull() handles 'no remote' exception gracefully."""

    def test_no_remote_then_imports(self, tmp_path: Path) -> None:
        svc, git = _make_sync()
        name, pid, aid = _ids()

        git.pull.side_effect = Exception("No configured pull destination")

        with patch("kgn.sync.import_service.ImportService") as MockImport:
            MockImport.return_value.import_project.return_value = _FakeImportResult()

            result = svc.pull(
                project_name=name,
                project_id=pid,
                agent_id=aid,
                sync_dir=tmp_path,
                repo=MagicMock(),
            )

        assert result.success is True
        assert result.pulled is False

    def test_unknown_pull_error_fails(self, tmp_path: Path) -> None:
        svc, git = _make_sync()
        name, pid, aid = _ids()

        git.pull.side_effect = Exception("totally unexpected")

        result = svc.pull(
            project_name=name,
            project_id=pid,
            agent_id=aid,
            sync_dir=tmp_path,
            repo=MagicMock(),
        )

        assert result.success is False
        assert "Pull failed" in result.message


class TestPullHappyPath:
    """pull() success with clean pull."""

    def test_clean_pull_and_import(self, tmp_path: Path) -> None:
        svc, git = _make_sync()
        name, pid, aid = _ids()

        git.pull.return_value = _FakeGitResult(success=True, message="up to date")

        with patch("kgn.sync.import_service.ImportService") as MockImport:
            MockImport.return_value.import_project.return_value = _FakeImportResult()

            result = svc.pull(
                project_name=name,
                project_id=pid,
                agent_id=aid,
                sync_dir=tmp_path,
                repo=MagicMock(),
            )

        assert result.success is True
        assert result.imported == 2
        assert "Pull complete" in result.message


# ══════════════════════════════════════════════════════════════════════
#  ensure_remote()
# ══════════════════════════════════════════════════════════════════════


class TestEnsureRemote:
    def test_adds_origin_from_config(self) -> None:
        cfg = GitHubConfig(token="tok", owner="own", repo="rep")
        svc, git = _make_sync()
        git.remote_list.return_value = []

        svc.ensure_remote(config=cfg)

        git.remote_add.assert_called_once_with("origin", cfg.repo_url)

    def test_skips_if_origin_exists(self) -> None:
        cfg = GitHubConfig(token="tok", owner="own", repo="rep")
        svc, git = _make_sync()
        git.remote_list.return_value = [("origin", "https://...")]

        svc.ensure_remote(config=cfg)

        git.remote_add.assert_not_called()

    def test_uses_github_client_config(self) -> None:
        mock_gh = MagicMock()
        cfg = GitHubConfig(token="tok", owner="own", repo="rep")
        mock_gh.config = cfg
        svc, git = _make_sync(github_client=mock_gh)
        git.remote_list.return_value = []

        svc.ensure_remote()  # no explicit config -> uses github_client.config

        git.remote_add.assert_called_once_with("origin", cfg.repo_url)

    def test_no_config_raises(self) -> None:
        svc, git = _make_sync()
        with pytest.raises(KgnError, match="GitHub configuration required"):
            svc.ensure_remote()


# ══════════════════════════════════════════════════════════════════════
#  ConflictDetector extra coverage
# ══════════════════════════════════════════════════════════════════════


class TestConflictDetectorExtra:
    def test_strategy_file_wins_auto_resolve(self) -> None:
        detector = ConflictDetector(ConflictStrategy.FILE_WINS)
        assert detector.should_auto_resolve() is True
        assert detector.strategy == ConflictStrategy.FILE_WINS

    def test_conflict_without_in_keyword(self) -> None:
        """CONFLICT line without ' in ' uses the whole line."""
        output = "CONFLICT (modify/delete): some-file was deleted"
        detector = ConflictDetector()
        conflicts = detector.detect_merge_conflicts(output)
        assert len(conflicts) == 1
        # Without " in ", uses the full line as file_path
        assert "CONFLICT" in conflicts[0].file_path
