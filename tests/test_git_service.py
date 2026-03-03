"""Tests for GitService — local git repository management.

Uses real git commands in temporary directories (no mocking).
Requires ``git`` to be installed on the system.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kgn.errors import KgnError, KgnErrorCode
from kgn.git.service import GitService

# ── Helpers ────────────────────────────────────────────────────────────


def _git_installed() -> bool:
    """Check if git is available."""
    try:
        subprocess.run(
            ["git", "--version"],
            capture_output=True,
            check=True,
            timeout=10,
        )
        return True
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(
    not _git_installed(),
    reason="git is not installed",
)


# ══════════════════════════════════════════════════════════════════════
#  Init
# ══════════════════════════════════════════════════════════════════════


class TestGitInit:
    def test_init_creates_repo(self, tmp_path: Path) -> None:
        """git init creates .git directory and .gitignore."""
        repo_dir = tmp_path / "my-repo"
        svc = GitService(repo_dir)
        result = svc.init()

        assert result.success is True
        assert (repo_dir / ".git").is_dir()
        assert (repo_dir / ".gitignore").is_file()

    def test_init_gitignore_content(self, tmp_path: Path) -> None:
        """.gitignore contains expected patterns."""
        repo_dir = tmp_path / "gi-repo"
        svc = GitService(repo_dir)
        svc.init()

        content = (repo_dir / ".gitignore").read_text(encoding="utf-8")
        assert ".kgn-sync.json" in content
        assert "__pycache__/" in content
        assert "*.pyc" in content

    def test_init_idempotent(self, tmp_path: Path) -> None:
        """Running init twice is safe."""
        repo_dir = tmp_path / "idem-repo"
        svc = GitService(repo_dir)
        svc.init()
        result = svc.init()
        assert result.success is True
        assert (repo_dir / ".git").is_dir()

    def test_is_initialized(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "check-repo"
        svc = GitService(repo_dir)

        assert svc.is_initialized() is False
        svc.init()
        assert svc.is_initialized() is True


# ══════════════════════════════════════════════════════════════════════
#  Status
# ══════════════════════════════════════════════════════════════════════


class TestGitStatus:
    def test_clean_status(self, tmp_path: Path) -> None:
        """Empty repo after init + commit has clean status."""
        repo_dir = tmp_path / "clean-repo"
        svc = GitService(repo_dir)
        svc.init()
        svc.commit("initial commit")

        status = svc.status()
        assert status.is_clean is True
        assert status.total_changes == 0

    def test_untracked_files(self, tmp_path: Path) -> None:
        """New files show as untracked."""
        repo_dir = tmp_path / "untracked-repo"
        svc = GitService(repo_dir)
        svc.init()
        svc.commit("initial commit")

        # Create a new file
        (repo_dir / "hello.txt").write_text("hello", encoding="utf-8")

        status = svc.status()
        assert status.is_clean is False
        assert "hello.txt" in status.untracked

    def test_modified_files(self, tmp_path: Path) -> None:
        """Modified tracked files show as modified."""
        repo_dir = tmp_path / "mod-repo"
        svc = GitService(repo_dir)
        svc.init()

        # Create, add, commit
        (repo_dir / "data.kgn").write_text("v1", encoding="utf-8")
        svc.commit("add data.kgn")

        # Modify
        (repo_dir / "data.kgn").write_text("v2", encoding="utf-8")

        status = svc.status()
        assert "data.kgn" in status.modified

    def test_deleted_files(self, tmp_path: Path) -> None:
        """Deleted tracked files show as deleted."""
        repo_dir = tmp_path / "del-repo"
        svc = GitService(repo_dir)
        svc.init()

        (repo_dir / "temp.kgn").write_text("tmp", encoding="utf-8")
        svc.commit("add temp")

        (repo_dir / "temp.kgn").unlink()

        status = svc.status()
        assert "temp.kgn" in status.deleted

    def test_not_initialized_raises(self, tmp_path: Path) -> None:
        """Status on non-git directory raises KGN-501."""
        repo_dir = tmp_path / "no-git"
        repo_dir.mkdir()
        svc = GitService(repo_dir)

        with pytest.raises(KgnError) as exc_info:
            svc.status()
        assert exc_info.value.code == KgnErrorCode.GIT_NOT_INITIALIZED


# ══════════════════════════════════════════════════════════════════════
#  Commit
# ══════════════════════════════════════════════════════════════════════


class TestGitCommit:
    def test_commit_creates_history(self, tmp_path: Path) -> None:
        """commit() creates a tracked commit."""
        repo_dir = tmp_path / "commit-repo"
        svc = GitService(repo_dir)
        svc.init()

        (repo_dir / "node.kgn").write_text("content", encoding="utf-8")
        result = svc.commit("add node")

        assert result.success is True
        entries = svc.log(n=5)
        assert len(entries) >= 1
        assert entries[0].subject == "add node"

    def test_commit_nothing_to_commit(self, tmp_path: Path) -> None:
        """commit() with no changes returns success with appropriate message."""
        repo_dir = tmp_path / "empty-commit-repo"
        svc = GitService(repo_dir)
        svc.init()
        svc.commit("initial")

        result = svc.commit("should be empty")
        assert result.success is True
        assert "Nothing to commit" in result.message

    def test_commit_auto_stages(self, tmp_path: Path) -> None:
        """commit() automatically stages all changes."""
        repo_dir = tmp_path / "autostage-repo"
        svc = GitService(repo_dir)
        svc.init()
        svc.commit("initial")

        (repo_dir / "file1.kgn").write_text("a", encoding="utf-8")
        (repo_dir / "file2.kgn").write_text("b", encoding="utf-8")
        svc.commit("add two files")

        # Both files should be tracked
        status = svc.status()
        assert status.is_clean is True


# ══════════════════════════════════════════════════════════════════════
#  Diff
# ══════════════════════════════════════════════════════════════════════


class TestGitDiff:
    def test_diff_shows_changes(self, tmp_path: Path) -> None:
        """diff() shows content changes."""
        repo_dir = tmp_path / "diff-repo"
        svc = GitService(repo_dir)
        svc.init()

        (repo_dir / "spec.kgn").write_text("original\n", encoding="utf-8")
        svc.commit("initial")

        (repo_dir / "spec.kgn").write_text("modified\n", encoding="utf-8")
        diff_output = svc.diff()

        assert "original" in diff_output
        assert "modified" in diff_output

    def test_diff_empty_when_clean(self, tmp_path: Path) -> None:
        """diff() returns empty string when working tree is clean."""
        repo_dir = tmp_path / "clean-diff-repo"
        svc = GitService(repo_dir)
        svc.init()

        (repo_dir / "data.kgn").write_text("data\n", encoding="utf-8")
        svc.commit("initial")

        diff_output = svc.diff()
        assert diff_output == ""

    def test_diff_cached(self, tmp_path: Path) -> None:
        """diff(cached=True) shows staged changes."""
        repo_dir = tmp_path / "cached-diff-repo"
        svc = GitService(repo_dir)
        svc.init()

        (repo_dir / "spec.kgn").write_text("original\n", encoding="utf-8")
        svc.commit("initial")

        (repo_dir / "spec.kgn").write_text("staged\n", encoding="utf-8")
        svc.add_all()
        diff_output = svc.diff(cached=True)

        assert "staged" in diff_output


# ══════════════════════════════════════════════════════════════════════
#  Log
# ══════════════════════════════════════════════════════════════════════


class TestGitLog:
    def test_log_multiple_commits(self, tmp_path: Path) -> None:
        """log() returns multiple commit entries in reverse-chronological order."""
        repo_dir = tmp_path / "log-repo"
        svc = GitService(repo_dir)
        svc.init()

        (repo_dir / "a.kgn").write_text("a", encoding="utf-8")
        svc.commit("first commit")

        (repo_dir / "b.kgn").write_text("b", encoding="utf-8")
        svc.commit("second commit")

        entries = svc.log(n=10)
        assert len(entries) == 2
        # Most recent first
        assert entries[0].subject == "second commit"
        assert entries[1].subject == "first commit"
        # Each entry has required fields
        assert len(entries[0].hash) == 40
        assert len(entries[0].short_hash) >= 7
        assert entries[0].author != ""
        assert entries[0].date != ""

    def test_log_empty_repo(self, tmp_path: Path) -> None:
        """log() returns empty list for repo with no commits."""
        repo_dir = tmp_path / "empty-log-repo"
        svc = GitService(repo_dir)
        svc.init()

        entries = svc.log()
        assert entries == []

    def test_log_limit(self, tmp_path: Path) -> None:
        """log(n=1) limits results."""
        repo_dir = tmp_path / "limit-log-repo"
        svc = GitService(repo_dir)
        svc.init()

        for i in range(3):
            (repo_dir / f"file-{i}.kgn").write_text(f"content {i}", encoding="utf-8")
            svc.commit(f"commit #{i}")

        entries = svc.log(n=1)
        assert len(entries) == 1
        assert entries[0].subject == "commit #2"


# ══════════════════════════════════════════════════════════════════════
#  Add All
# ══════════════════════════════════════════════════════════════════════


class TestGitAddAll:
    def test_add_all_stages_files(self, tmp_path: Path) -> None:
        """add_all() stages all untracked and modified files."""
        repo_dir = tmp_path / "add-repo"
        svc = GitService(repo_dir)
        svc.init()
        svc.commit("initial")

        (repo_dir / "new.kgn").write_text("new content", encoding="utf-8")
        svc.add_all()

        # After add, file should be staged (shown by diff --cached)
        diff_output = svc.diff(cached=True)
        assert "new content" in diff_output


# ══════════════════════════════════════════════════════════════════════
#  Status parsing
# ══════════════════════════════════════════════════════════════════════


class TestStatusParsing:
    """Unit tests for _parse_status (no git needed)."""

    def test_parse_empty(self) -> None:
        result = GitService._parse_status("")
        assert result.is_clean is True

    def test_parse_untracked(self) -> None:
        result = GitService._parse_status("?? new-file.kgn\n")
        assert result.untracked == ["new-file.kgn"]

    def test_parse_modified(self) -> None:
        result = GitService._parse_status(" M modified.kgn\n")
        assert result.modified == ["modified.kgn"]

    def test_parse_added(self) -> None:
        result = GitService._parse_status("A  staged.kgn\n")
        assert result.added == ["staged.kgn"]

    def test_parse_deleted(self) -> None:
        result = GitService._parse_status(" D removed.kgn\n")
        assert result.deleted == ["removed.kgn"]

    def test_parse_mixed(self) -> None:
        porcelain = " M modified.kgn\nA  added.kgn\n D deleted.kgn\n?? untracked.kgn\n"
        result = GitService._parse_status(porcelain)
        assert result.modified == ["modified.kgn"]
        assert result.added == ["added.kgn"]
        assert result.deleted == ["deleted.kgn"]
        assert result.untracked == ["untracked.kgn"]
        assert result.total_changes == 4


# ══════════════════════════════════════════════════════════════════════
#  Log parsing
# ══════════════════════════════════════════════════════════════════════


class TestLogParsing:
    """Unit tests for _parse_log (no git needed)."""

    def test_parse_empty(self) -> None:
        entries = GitService._parse_log("")
        assert entries == []

    def test_parse_single_entry(self) -> None:
        log_output = (
            "abc123def456abc123def456abc123def456abc12345\n"
            "abc123d\n"
            "Initial commit\n"
            "Test Author\n"
            "2025-01-01 12:00:00 +0900\n"
        )
        entries = GitService._parse_log(log_output)
        assert len(entries) == 1
        assert entries[0].hash == "abc123def456abc123def456abc123def456abc12345"
        assert entries[0].short_hash == "abc123d"
        assert entries[0].subject == "Initial commit"
        assert entries[0].author == "Test Author"

    def test_parse_multiple_entries(self) -> None:
        hash_a = "a" * 40
        hash_b = "b" * 40
        log_output = (
            f"{hash_a}\nabcdefg\nFirst\nAuthor1\n2025-01-01 12:00:00 +0900\n"
            f"{hash_b}\nbcdefgh\nSecond\nAuthor2\n2025-01-02 12:00:00 +0900\n"
        )
        entries = GitService._parse_log(log_output)
        assert len(entries) == 2
        assert entries[0].subject == "First"
        assert entries[1].subject == "Second"
