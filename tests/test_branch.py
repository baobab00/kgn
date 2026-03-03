"""Tests for BranchService — agent-scoped branch management.

Uses real git commands in tmp_path (no mocking).
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from kgn.git.branch import BranchService, task_branch_name
from kgn.git.service import GitService


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
#  Naming convention
# ══════════════════════════════════════════════════════════════════════


class TestBranchNaming:
    def test_task_branch_name_format(self) -> None:
        """Branch name follows agent/<key>-task-<uuid8> pattern."""
        tid = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
        name = task_branch_name("claude", tid)
        assert name == "agent/claude-task-550e8400"

    def test_task_branch_name_normalizes(self) -> None:
        """Agent key is lowered and spaces replaced."""
        tid = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        name = task_branch_name("GPT 4o", tid)
        assert name == "agent/gpt-4o-task-a1b2c3d4"


# ══════════════════════════════════════════════════════════════════════
#  Branch creation
# ══════════════════════════════════════════════════════════════════════


class TestBranchCreation:
    def test_create_task_branch(self, tmp_path: Path) -> None:
        """create_task_branch creates and switches to new branch."""
        repo_dir = tmp_path / "repo"
        _, branch_svc = _init_repo(repo_dir)

        tid = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
        branch = branch_svc.create_task_branch("claude", tid)

        assert branch == "agent/claude-task-550e8400"
        assert branch_svc.current_branch() == branch

    def test_create_task_branch_idempotent(self, tmp_path: Path) -> None:
        """Calling again with same args checks out existing branch."""
        repo_dir = tmp_path / "repo-idem"
        _, branch_svc = _init_repo(repo_dir)

        tid = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
        branch1 = branch_svc.create_task_branch("claude", tid)
        # Switch away first
        branch_svc.checkout(branch_svc._detect_main_branch())
        # Re-create (should checkout existing)
        branch2 = branch_svc.create_task_branch("claude", tid)

        assert branch1 == branch2
        assert branch_svc.current_branch() == branch1

    def test_list_branches_includes_new(self, tmp_path: Path) -> None:
        """New branch appears in list_branches."""
        repo_dir = tmp_path / "repo-list"
        _, branch_svc = _init_repo(repo_dir)

        tid = uuid.UUID("aaaa1111-bbbb-2222-cccc-333344445555")
        branch_svc.create_task_branch("agent1", tid)

        branches = branch_svc.list_branches()
        assert "agent/agent1-task-aaaa1111" in branches


# ══════════════════════════════════════════════════════════════════════
#  Checkout
# ══════════════════════════════════════════════════════════════════════


class TestBranchCheckout:
    def test_checkout_switches_branch(self, tmp_path: Path) -> None:
        """checkout() switches to the specified branch."""
        repo_dir = tmp_path / "repo-co"
        _, branch_svc = _init_repo(repo_dir)

        tid = uuid.UUID("bbbb2222-cccc-3333-dddd-444455556666")
        branch = branch_svc.create_task_branch("worker", tid)
        main = branch_svc._detect_main_branch()

        branch_svc.checkout(main)
        assert branch_svc.current_branch() == main

        branch_svc.checkout(branch)
        assert branch_svc.current_branch() == branch


# ══════════════════════════════════════════════════════════════════════
#  Merge
# ══════════════════════════════════════════════════════════════════════


class TestBranchMerge:
    def test_merge_to_main(self, tmp_path: Path) -> None:
        """merge_to_main merges branch and deletes it."""
        repo_dir = tmp_path / "repo-merge"
        git_svc, branch_svc = _init_repo(repo_dir)

        tid = uuid.UUID("cccc3333-dddd-4444-eeee-555566667777")
        branch = branch_svc.create_task_branch("claude", tid)

        # Make a commit on the branch
        (repo_dir / "task-output.kgn").write_text("result", encoding="utf-8")
        git_svc.commit("complete task")

        result = branch_svc.merge_to_main(branch, delete_after=True)
        assert result.success is True

        # Branch should be deleted
        assert branch not in branch_svc.list_branches()

        # File should be visible on main
        assert (repo_dir / "task-output.kgn").exists()

    def test_merge_no_delete(self, tmp_path: Path) -> None:
        """merge_to_main with delete_after=False keeps the branch."""
        repo_dir = tmp_path / "repo-nodelete"
        git_svc, branch_svc = _init_repo(repo_dir)

        tid = uuid.UUID("dddd4444-eeee-5555-ffff-666677778888")
        branch = branch_svc.create_task_branch("gpt4", tid)

        (repo_dir / "output.kgn").write_text("data", encoding="utf-8")
        git_svc.commit("work done")

        branch_svc.merge_to_main(branch, delete_after=False)

        # Branch should still exist
        assert branch in branch_svc.list_branches()


# ══════════════════════════════════════════════════════════════════════
#  Cleanup
# ══════════════════════════════════════════════════════════════════════


class TestBranchCleanup:
    def test_cleanup_merged_branches(self, tmp_path: Path) -> None:
        """cleanup removes merged agent/* branches."""
        repo_dir = tmp_path / "repo-cleanup"
        git_svc, branch_svc = _init_repo(repo_dir)

        # Create and merge a branch
        tid = uuid.UUID("eeee5555-ffff-6666-7777-888899990000")
        branch = branch_svc.create_task_branch("claude", tid)
        (repo_dir / "node.kgn").write_text("result", encoding="utf-8")
        git_svc.commit("task done")
        branch_svc.merge_to_main(branch, delete_after=False)

        # Cleanup should delete it
        deleted = branch_svc.cleanup_merged_branches()
        assert branch in deleted
        assert branch not in branch_svc.list_branches()

    def test_cleanup_no_branches(self, tmp_path: Path) -> None:
        """cleanup returns empty list when nothing to clean."""
        repo_dir = tmp_path / "repo-nocleanup"
        _, branch_svc = _init_repo(repo_dir)

        deleted = branch_svc.cleanup_merged_branches()
        assert deleted == []
