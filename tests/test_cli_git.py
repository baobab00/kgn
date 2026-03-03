"""CLI tests for git, branch, and pr commands.

All Git/GitHub operations are mocked — no real repo needed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from kgn.cli import app

runner = CliRunner()


# ══════════════════════════════════════════════════════════════════════
# git init
# ══════════════════════════════════════════════════════════════════════


class TestGitInitCLI:
    def test_git_init_happy(self, tmp_path: Path) -> None:
        from kgn.git.service import GitResult

        with patch("kgn.git.service.GitService") as MockGit:
            MockGit.return_value.init.return_value = GitResult(success=True, message="Initialized")
            result = runner.invoke(app, ["git", "init", "--target", str(tmp_path)])
        assert result.exit_code == 0
        assert "initialized" in result.output.lower()

    def test_git_init_error(self, tmp_path: Path) -> None:
        with patch("kgn.git.service.GitService") as MockGit:
            MockGit.return_value.init.side_effect = Exception("git not found")
            result = runner.invoke(app, ["git", "init", "--target", str(tmp_path)])
        assert result.exit_code == 1
        assert "git not found" in result.output


# ══════════════════════════════════════════════════════════════════════
# git status
# ══════════════════════════════════════════════════════════════════════


class TestGitStatusCLI:
    def test_git_status_clean(self, tmp_path: Path) -> None:
        from kgn.git.service import GitStatusResult

        with patch("kgn.git.service.GitService") as MockGit:
            MockGit.return_value.status.return_value = GitStatusResult(
                modified=[], added=[], deleted=[], untracked=[]
            )
            result = runner.invoke(app, ["git", "status", "--target", str(tmp_path)])
        assert result.exit_code == 0
        assert "clean" in result.output.lower()

    def test_git_status_with_changes(self, tmp_path: Path) -> None:
        from kgn.git.service import GitStatusResult

        with patch("kgn.git.service.GitService") as MockGit:
            MockGit.return_value.status.return_value = GitStatusResult(
                modified=["file1.kgn"],
                added=["file2.kgn"],
                deleted=["file3.kgn"],
                untracked=["file4.kgn"],
            )
            result = runner.invoke(app, ["git", "status", "--target", str(tmp_path)])
        assert result.exit_code == 0
        assert "file1.kgn" in result.output
        assert "file2.kgn" in result.output

    def test_git_status_error(self, tmp_path: Path) -> None:
        with patch("kgn.git.service.GitService") as MockGit:
            MockGit.return_value.status.side_effect = Exception("not a repo")
            result = runner.invoke(app, ["git", "status", "--target", str(tmp_path)])
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# git diff
# ══════════════════════════════════════════════════════════════════════


class TestGitDiffCLI:
    def test_git_diff_empty(self, tmp_path: Path) -> None:
        with patch("kgn.git.service.GitService") as MockGit:
            MockGit.return_value.diff.return_value = ""
            result = runner.invoke(app, ["git", "diff", "--target", str(tmp_path)])
        assert result.exit_code == 0
        assert "No differences" in result.output

    def test_git_diff_with_output(self, tmp_path: Path) -> None:
        with patch("kgn.git.service.GitService") as MockGit:
            MockGit.return_value.diff.return_value = "+line added\n-line removed"
            result = runner.invoke(app, ["git", "diff", "--target", str(tmp_path)])
        assert result.exit_code == 0
        assert "+line added" in result.output

    def test_git_diff_cached(self, tmp_path: Path) -> None:
        with patch("kgn.git.service.GitService") as MockGit:
            MockGit.return_value.diff.return_value = "staged changes"
            result = runner.invoke(app, ["git", "diff", "--target", str(tmp_path), "--cached"])
        assert result.exit_code == 0

    def test_git_diff_error(self, tmp_path: Path) -> None:
        with patch("kgn.git.service.GitService") as MockGit:
            MockGit.return_value.diff.side_effect = Exception("not a repo")
            result = runner.invoke(app, ["git", "diff", "--target", str(tmp_path)])
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# git log
# ══════════════════════════════════════════════════════════════════════


class TestGitLogCLI:
    def test_git_log_empty(self, tmp_path: Path) -> None:
        with patch("kgn.git.service.GitService") as MockGit:
            MockGit.return_value.log.return_value = []
            result = runner.invoke(app, ["git", "log", "--target", str(tmp_path)])
        assert result.exit_code == 0
        assert "No commits" in result.output

    def test_git_log_with_entries(self, tmp_path: Path) -> None:
        from kgn.git.service import GitLogEntry

        entries = [
            GitLogEntry(
                hash="abc123def456",
                short_hash="abc123d",
                subject="feat: add node",
                author="Test",
                date="2026-01-01",
            ),
            GitLogEntry(
                hash="def456abc123",
                short_hash="def456a",
                subject="fix: bug",
                author="Test",
                date="2026-01-02",
            ),
        ]
        with patch("kgn.git.service.GitService") as MockGit:
            MockGit.return_value.log.return_value = entries
            result = runner.invoke(app, ["git", "log", "--target", str(tmp_path)])
        assert result.exit_code == 0
        assert "feat: add node" in result.output
        assert "fix: bug" in result.output

    def test_git_log_custom_count(self, tmp_path: Path) -> None:
        with patch("kgn.git.service.GitService") as MockGit:
            MockGit.return_value.log.return_value = []
            result = runner.invoke(app, ["git", "log", "--target", str(tmp_path), "--count", "5"])
        assert result.exit_code == 0

    def test_git_log_error(self, tmp_path: Path) -> None:
        with patch("kgn.git.service.GitService") as MockGit:
            MockGit.return_value.log.side_effect = Exception("not a repo")
            result = runner.invoke(app, ["git", "log", "--target", str(tmp_path)])
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# branch list
# ══════════════════════════════════════════════════════════════════════


class TestBranchListCLI:
    def test_branch_list_happy(self, tmp_path: Path) -> None:
        with (
            patch("kgn.git.service.GitService"),
            patch("kgn.git.branch.BranchService") as MockBranch,
        ):
            MockBranch.return_value.current_branch.return_value = "main"
            MockBranch.return_value.list_branches.return_value = ["main", "feature/x"]
            result = runner.invoke(app, ["git", "branch", "list", "--target", str(tmp_path)])
        assert result.exit_code == 0
        assert "main" in result.output
        assert "feature/x" in result.output

    def test_branch_list_error(self, tmp_path: Path) -> None:
        with (
            patch("kgn.git.service.GitService"),
            patch("kgn.git.branch.BranchService") as MockBranch,
        ):
            MockBranch.return_value.list_branches.side_effect = Exception("no repo")
            result = runner.invoke(app, ["git", "branch", "list", "--target", str(tmp_path)])
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# branch checkout
# ══════════════════════════════════════════════════════════════════════


class TestBranchCheckoutCLI:
    def test_checkout_happy(self, tmp_path: Path) -> None:
        from kgn.git.service import GitResult

        with (
            patch("kgn.git.service.GitService"),
            patch("kgn.git.branch.BranchService") as MockBranch,
        ):
            MockBranch.return_value.checkout.return_value = GitResult(success=True, message="")
            result = runner.invoke(
                app,
                ["git", "branch", "checkout", "feature/x", "--target", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert "feature/x" in result.output

    def test_checkout_error(self, tmp_path: Path) -> None:
        with (
            patch("kgn.git.service.GitService"),
            patch("kgn.git.branch.BranchService") as MockBranch,
        ):
            MockBranch.return_value.checkout.side_effect = Exception("branch not found")
            result = runner.invoke(
                app,
                ["git", "branch", "checkout", "no-branch", "--target", str(tmp_path)],
            )
        assert result.exit_code == 1
        assert "branch not found" in result.output


# ══════════════════════════════════════════════════════════════════════
# branch cleanup
# ══════════════════════════════════════════════════════════════════════


class TestBranchCleanupCLI:
    def test_cleanup_no_branches(self, tmp_path: Path) -> None:
        with (
            patch("kgn.git.service.GitService"),
            patch("kgn.git.branch.BranchService") as MockBranch,
        ):
            MockBranch.return_value.cleanup_merged_branches.return_value = []
            result = runner.invoke(app, ["git", "branch", "cleanup", "--target", str(tmp_path)])
        assert result.exit_code == 0
        assert "No merged" in result.output

    def test_cleanup_with_branches(self, tmp_path: Path) -> None:
        with (
            patch("kgn.git.service.GitService"),
            patch("kgn.git.branch.BranchService") as MockBranch,
        ):
            MockBranch.return_value.cleanup_merged_branches.return_value = [
                "agent/old-branch-1",
                "agent/old-branch-2",
            ]
            result = runner.invoke(app, ["git", "branch", "cleanup", "--target", str(tmp_path)])
        assert result.exit_code == 0
        assert "2 branch" in result.output

    def test_cleanup_error(self, tmp_path: Path) -> None:
        with (
            patch("kgn.git.service.GitService"),
            patch("kgn.git.branch.BranchService") as MockBranch,
        ):
            MockBranch.return_value.cleanup_merged_branches.side_effect = Exception("failed")
            result = runner.invoke(app, ["git", "branch", "cleanup", "--target", str(tmp_path)])
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# pr create
# ══════════════════════════════════════════════════════════════════════


class TestPRCreateCLI:
    def test_pr_create_happy(self, monkeypatch) -> None:
        monkeypatch.setenv("KGN_GITHUB_TOKEN", "test-token")
        monkeypatch.setenv("KGN_GITHUB_OWNER", "test-owner")
        monkeypatch.setenv("KGN_GITHUB_REPO", "test-repo")

        from kgn.github.pr_service import PRResult

        mock_result = PRResult(success=True, pr_number=42, html_url="https://github.com/pr/42")

        with (
            patch("kgn.github.client.GitHubClient") as MockClient,
            patch("kgn.github.pr_service.PullRequestService") as MockPR,
        ):
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockPR.return_value.create_task_pr.return_value = mock_result
            result = runner.invoke(
                app,
                [
                    "git",
                    "pr",
                    "create",
                    "--title",
                    "Test PR",
                    "--head",
                    "feature/x",
                    "--base",
                    "main",
                ],
            )
        assert result.exit_code == 0
        assert "#42" in result.output

    def test_pr_create_failure(self, monkeypatch) -> None:
        monkeypatch.setenv("KGN_GITHUB_TOKEN", "test-token")
        monkeypatch.setenv("KGN_GITHUB_OWNER", "test-owner")
        monkeypatch.setenv("KGN_GITHUB_REPO", "test-repo")

        from kgn.github.pr_service import PRResult

        mock_result = PRResult(success=False, message="Branch not found")

        with (
            patch("kgn.github.client.GitHubClient") as MockClient,
            patch("kgn.github.pr_service.PullRequestService") as MockPR,
        ):
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockPR.return_value.create_task_pr.return_value = mock_result
            result = runner.invoke(
                app,
                [
                    "git",
                    "pr",
                    "create",
                    "--title",
                    "Bad PR",
                    "--head",
                    "no-branch",
                ],
            )
        assert result.exit_code == 1

    def test_pr_create_no_token(self) -> None:
        """Without GitHub env vars, should fail."""
        result = runner.invoke(
            app,
            [
                "git",
                "pr",
                "create",
                "--title",
                "No Token PR",
                "--head",
                "feature/x",
            ],
        )
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# pr list
# ══════════════════════════════════════════════════════════════════════


class TestPRListCLI:
    def test_pr_list_happy(self, monkeypatch) -> None:
        monkeypatch.setenv("KGN_GITHUB_TOKEN", "test-token")
        monkeypatch.setenv("KGN_GITHUB_OWNER", "test-owner")
        monkeypatch.setenv("KGN_GITHUB_REPO", "test-repo")

        with (
            patch("kgn.github.client.GitHubClient") as MockClient,
            patch("kgn.github.pr_service.PullRequestService") as MockPR,
        ):
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockPR.return_value.list_prs.return_value = [
                {"number": 1, "title": "First PR", "state": "open"},
                {"number": 2, "title": "Second PR", "state": "open"},
            ]
            result = runner.invoke(app, ["git", "pr", "list"])
        assert result.exit_code == 0
        assert "#1" in result.output or "First PR" in result.output

    def test_pr_list_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("KGN_GITHUB_TOKEN", "test-token")
        monkeypatch.setenv("KGN_GITHUB_OWNER", "test-owner")
        monkeypatch.setenv("KGN_GITHUB_REPO", "test-repo")

        with (
            patch("kgn.github.client.GitHubClient") as MockClient,
            patch("kgn.github.pr_service.PullRequestService") as MockPR,
        ):
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockPR.return_value.list_prs.return_value = []
            result = runner.invoke(app, ["git", "pr", "list"])
        assert result.exit_code == 0
        assert "No" in result.output

    def test_pr_list_no_token(self) -> None:
        result = runner.invoke(app, ["git", "pr", "list"])
        assert result.exit_code == 1
