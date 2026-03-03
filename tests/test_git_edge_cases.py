"""Tests for Step 6 — Git/GitHub edge case completion (R-022, R-023).

R-022: detect_default_branch() — remote refs, local fallback, main/master.
R-023: pull conflict abort → clean working tree guarantee.
Edge cases: duplicate branch creation, delete non-existent branch, push arg fix.
"""

from __future__ import annotations

import contextlib
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from kgn.git.branch import BranchService
from kgn.git.service import GitResult, GitService
from kgn.github.sync_service import (
    ConflictStrategy,
    SyncService,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _git_installed() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not _git_installed(), reason="git is not installed")


def _init_repo(path: Path) -> tuple[GitService, BranchService]:
    """Create a repo with initial commit and return services."""
    git_svc = GitService(path)
    git_svc.init()
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    git_svc.commit("initial commit")
    return git_svc, BranchService(git_svc)


# ══════════════════════════════════════════════════════════════════════
#  R-022: detect_default_branch()
# ══════════════════════════════════════════════════════════════════════


class TestDetectDefaultBranch:
    """GitService.detect_default_branch() resolves main vs master."""

    def test_local_main_detected(self, tmp_path: Path) -> None:
        """Local repo with 'main' branch → returns 'main'."""
        git_svc, _ = _init_repo(tmp_path)
        # Ensure current branch is main (git init default)
        result = git_svc.detect_default_branch()
        # Should detect 'main' or 'master' from local branches
        assert result in ("main", "master")

    def test_local_master_detected(self, tmp_path: Path) -> None:
        """Local repo with 'master' branch → returns 'master'."""
        git_svc, _ = _init_repo(tmp_path)
        current = git_svc.current_branch()
        # Rename current branch to 'master'
        git_svc._run("branch", "-m", current, "master")
        result = git_svc.detect_default_branch()
        assert result == "master"

    def test_fallback_to_main_when_empty(self, tmp_path: Path) -> None:
        """When no recognized branches exist, fallback to 'main'."""
        git_svc = GitService(tmp_path)
        git_svc.init()
        # No commits yet → no branches listed
        result = git_svc.detect_default_branch()
        assert result == "main"

    def test_prefers_main_over_master(self, tmp_path: Path) -> None:
        """When both main and master exist, prefers 'main'."""
        git_svc, _ = _init_repo(tmp_path)
        current = git_svc.current_branch()
        # Create another branch called master (if current is main)
        if current == "main":
            git_svc._run("branch", "master")
        else:
            # current is master, create main
            git_svc._run("branch", "main")
        result = git_svc.detect_default_branch()
        assert result == "main"

    def test_symbolic_ref_parsing(self) -> None:
        """Mocked: symbolic-ref returns refs/remotes/origin/main."""
        git_svc = MagicMock(spec=GitService)
        git_svc.detect_default_branch = GitService.detect_default_branch.__get__(
            git_svc, GitService
        )
        git_svc._run.return_value = GitResult(
            success=True,
            message="refs/remotes/origin/main",
            returncode=0,
        )
        result = git_svc.detect_default_branch()
        assert result == "main"

    def test_symbolic_ref_master(self) -> None:
        """Mocked: symbolic-ref returns refs/remotes/origin/master."""
        git_svc = MagicMock(spec=GitService)
        git_svc.detect_default_branch = GitService.detect_default_branch.__get__(
            git_svc, GitService
        )
        git_svc._run.return_value = GitResult(
            success=True,
            message="refs/remotes/origin/master",
            returncode=0,
        )
        result = git_svc.detect_default_branch()
        assert result == "master"

    def test_symbolic_ref_fails_remote_show_succeeds(self) -> None:
        """Mocked: symbolic-ref fails, 'git remote show origin' succeeds."""
        git_svc = MagicMock(spec=GitService)
        git_svc.detect_default_branch = GitService.detect_default_branch.__get__(
            git_svc, GitService
        )

        def _side_effect(*args, **kwargs):
            if args[0] == "symbolic-ref":
                return GitResult(success=False, message="", returncode=1)
            if args[0] == "remote" and args[1] == "show":
                return GitResult(
                    success=True,
                    message="  HEAD branch: develop\n  Remote branches:",
                    returncode=0,
                )
            return GitResult(success=True, message="", returncode=0)

        git_svc._run.side_effect = _side_effect
        result = git_svc.detect_default_branch()
        assert result == "develop"

    def test_all_methods_fail_fallback_main(self) -> None:
        """Mocked: all detection methods fail → returns 'main'."""
        git_svc = MagicMock(spec=GitService)
        git_svc.detect_default_branch = GitService.detect_default_branch.__get__(
            git_svc, GitService
        )
        git_svc._run.return_value = GitResult(
            success=False,
            message="",
            returncode=1,
        )
        result = git_svc.detect_default_branch()
        assert result == "main"


class TestBranchServiceDetectMainBranch:
    """BranchService._detect_main_branch() delegates to GitService (R-022)."""

    def test_delegates_to_git_service(self, tmp_path: Path) -> None:
        """BranchService._detect_main_branch() uses detect_default_branch()."""
        git_svc, branch_svc = _init_repo(tmp_path)
        result = branch_svc._detect_main_branch()
        expected = git_svc.detect_default_branch()
        assert result == expected


# ══════════════════════════════════════════════════════════════════════
#  R-023: Pull conflict clean state guarantee
# ══════════════════════════════════════════════════════════════════════


@dataclass
class _FakeGitResult:
    success: bool = True
    message: str = ""
    returncode: int = 0


@dataclass
class _FakeImportResult:
    imported: int = 2
    skipped: int = 0


@dataclass
class _FakeExportResult:
    exported: int = 3
    skipped: int = 0
    deleted: int = 0
    errors: list[str] | None = None


class TestPullConflictCleanState:
    """After merge --abort, working tree is guaranteed clean (R-023)."""

    def test_abort_with_clean_tree(self, tmp_path: Path) -> None:
        """merge --abort leaves clean tree → no extra cleanup."""
        git = MagicMock()
        svc = SyncService(
            git_service=git,
            conflict_strategy=ConflictStrategy.DB_WINS,
        )

        git.pull.return_value = _FakeGitResult(
            success=False,
            message="CONFLICT (content): Merge conflict in file.kgn",
        )

        # After merge --abort, status --porcelain returns empty (clean)
        def _run_side_effect(*args, **kwargs):
            if args[0] == "merge" and args[1] == "--abort":
                return _FakeGitResult()
            if args[0] == "status" and args[1] == "--porcelain":
                return _FakeGitResult(success=True, message="")
            return _FakeGitResult()

        git._run.side_effect = _run_side_effect

        with (
            patch("kgn.sync.import_service.ImportService") as MockImport,
            patch("kgn.sync.export_service.ExportService") as MockExport,
        ):
            MockImport.return_value.import_project.return_value = _FakeImportResult()
            MockExport.return_value.export_project.return_value = _FakeExportResult()
            git.commit.return_value = _FakeGitResult(message="resolved")

            result = svc.pull(
                project_name="test",
                project_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                sync_dir=tmp_path,
                repo=MagicMock(),
            )

        assert result.success is True
        # merge --abort called
        assert call("merge", "--abort", check=False) in git._run.call_args_list
        # status --porcelain called
        assert call("status", "--porcelain", check=False) in git._run.call_args_list
        # No checkout/clean needed because tree was clean
        assert call("checkout", ".", check=False) not in git._run.call_args_list

    def test_abort_with_dirty_tree_force_cleans(self, tmp_path: Path) -> None:
        """merge --abort leaves dirty tree → checkout . + clean -fd called (R-023)."""
        git = MagicMock()
        svc = SyncService(
            git_service=git,
            conflict_strategy=ConflictStrategy.DB_WINS,
        )

        git.pull.return_value = _FakeGitResult(
            success=False,
            message="CONFLICT (content): Merge conflict in file.kgn",
        )

        # After merge --abort, status shows residual files
        def _run_side_effect(*args, **kwargs):
            if args[0] == "merge" and args[1] == "--abort":
                return _FakeGitResult()
            if args[0] == "status" and args[1] == "--porcelain":
                return _FakeGitResult(success=True, message="M  file.kgn\n?? temp.txt")
            if args[0] == "checkout" and args[1] == ".":
                return _FakeGitResult()
            if args[0] == "clean" and args[1] == "-fd":
                return _FakeGitResult()
            return _FakeGitResult()

        git._run.side_effect = _run_side_effect

        with (
            patch("kgn.sync.import_service.ImportService") as MockImport,
            patch("kgn.sync.export_service.ExportService") as MockExport,
        ):
            MockImport.return_value.import_project.return_value = _FakeImportResult()
            MockExport.return_value.export_project.return_value = _FakeExportResult()
            git.commit.return_value = _FakeGitResult(message="resolved")

            result = svc.pull(
                project_name="test",
                project_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                sync_dir=tmp_path,
                repo=MagicMock(),
            )

        assert result.success is True
        # Force cleanup called
        assert call("checkout", ".", check=False) in git._run.call_args_list
        assert call("clean", "-fd", check=False) in git._run.call_args_list


# ══════════════════════════════════════════════════════════════════════
#  Branch edge cases
# ══════════════════════════════════════════════════════════════════════


class TestBranchEdgeCases:
    """Edge cases: duplicate creation, non-existent delete, push fix."""

    def test_create_existing_branch_idempotent(self, tmp_path: Path) -> None:
        """Creating a branch that already exists checks it out instead."""
        git_svc, branch_svc = _init_repo(tmp_path)
        tid = uuid.uuid4()

        name1 = branch_svc.create_task_branch("claude", tid)
        # Switch back to main
        git_svc._run("checkout", git_svc.detect_default_branch())
        # Create again — should just checkout
        name2 = branch_svc.create_task_branch("claude", tid)

        assert name1 == name2
        assert branch_svc.current_branch() == name1

    def test_delete_nonexistent_branch_graceful(self, tmp_path: Path) -> None:
        """Deleting a non-existent branch does not crash (check=False)."""
        git_svc, _ = _init_repo(tmp_path)
        result = git_svc._run("branch", "-d", "non-existent-branch", check=False)
        assert result.success is False

    def test_checkout_nonexistent_branch_raises(self, tmp_path: Path) -> None:
        """Checking out a non-existent branch raises KgnError."""
        from kgn.errors import KgnError

        git_svc, branch_svc = _init_repo(tmp_path)
        with pytest.raises(KgnError):
            branch_svc.checkout("non-existent-branch")

    def test_push_args_no_duplicate_remote(self, tmp_path: Path) -> None:
        """push() without branch doesn't duplicate 'origin' in args."""
        git_svc, _ = _init_repo(tmp_path)

        # Mock _run to capture args
        original_run = git_svc._run
        captured_calls: list[tuple] = []

        def capture_run(*args, **kwargs):
            captured_calls.append(args)
            # Let it fail gracefully (no remote configured)
            from kgn.errors import KgnError

            raise KgnError(
                KgnErrorCode="KGN-502",
                message="no remote",
            )

        # Use mock to track calls without actual push
        with patch.object(git_svc, "_run") as mock_run:
            mock_run.side_effect = lambda *a, **kw: (
                (_ for _ in ()).throw(Exception("no remote"))
                if a[0] == "push"
                else original_run(*a, **kw)
            )
            with contextlib.suppress(Exception):
                git_svc.push()

            # Find the push call
            push_calls = [c for c in mock_run.call_args_list if c[0][0] == "push"]
            if push_calls:
                push_args = push_calls[0][0]
                # Count occurrences of "origin" — should be exactly 1
                origin_count = push_args.count("origin")
                assert origin_count == 1, f"'origin' appears {origin_count} times: {push_args}"

    def test_push_with_explicit_branch(self, tmp_path: Path) -> None:
        """push(branch='feature') uses correct arg order."""
        git_svc, _ = _init_repo(tmp_path)

        with patch.object(git_svc, "_run") as mock_run:
            mock_run.side_effect = Exception("no remote")
            with contextlib.suppress(Exception):
                git_svc.push(branch="feature")

            push_calls = [c for c in mock_run.call_args_list if c[0][0] == "push"]
            if push_calls:
                push_args = push_calls[0][0]
                assert push_args == ("push", "origin", "feature")

    def test_merge_to_main_uses_detect_default_branch(self, tmp_path: Path) -> None:
        """merge_to_main without explicit main_branch uses detect_default_branch."""
        git_svc, branch_svc = _init_repo(tmp_path)
        tid = uuid.uuid4()

        # Create task branch with a commit
        branch_name = branch_svc.create_task_branch("agent1", tid)
        (tmp_path / "new_file.txt").write_text("content", encoding="utf-8")
        git_svc.commit("task work")

        # Merge back
        result = branch_svc.merge_to_main(branch_name)
        assert result.success

        # Should be back on main/master
        current = git_svc.current_branch()
        assert current == git_svc.detect_default_branch()
