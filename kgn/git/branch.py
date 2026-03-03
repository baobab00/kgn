"""BranchService — agent-scoped branch management for KGN sync repos.

Provides branch creation, checkout, merge, and cleanup operations
following the naming convention ``agent/<agent_key>-task-<uuid_short>``.

Design decisions:
- Uses GitService internally for all git operations.
- Branch names are deterministic from (agent_key, task_id).
- create_task_branch is idempotent — re-calling with the same args
  checks out the existing branch.
"""

from __future__ import annotations

import uuid

import structlog

from kgn.git.service import GitResult, GitService

log = structlog.get_logger("kgn.git.branch")

# ── Naming convention ──────────────────────────────────────────────────


def task_branch_name(agent_key: str, task_id: uuid.UUID) -> str:
    """Generate a deterministic branch name for a task.

    Format: ``agent/<agent_key>-task-<first 8 chars of UUID>``

    Examples:
        ``agent/claude-task-550e8400``
        ``agent/gpt4-task-a1b2c3d4``
    """
    uuid_short = str(task_id).split("-")[0]
    safe_key = agent_key.lower().replace(" ", "-")
    return f"agent/{safe_key}-task-{uuid_short}"


# ── Service ────────────────────────────────────────────────────────────


class BranchService:
    """Agent-scoped branch management.

    Workflow:
    1. ``task_checkout`` → ``create_task_branch()`` — auto-create + switch
    2. (work: ingest_node, export, etc.)
    3. ``task_complete`` → commit on branch, optionally create PR
    """

    def __init__(self, git_service: GitService) -> None:
        self._git = git_service

    def create_task_branch(
        self,
        agent_key: str,
        task_id: uuid.UUID,
    ) -> str:
        """Create and checkout a task-scoped branch.

        If the branch already exists, simply checks it out.

        Args:
            agent_key: Agent identifier (e.g. ``"claude"``).
            task_id: UUID of the task queue item.

        Returns:
            The branch name that was created/checked out.
        """
        branch = task_branch_name(agent_key, task_id)

        # Check if branch already exists
        existing = self.list_branches()
        if branch in existing:
            self._git._run("checkout", branch)
            log.info("branch.checkout_existing", branch=branch)
        else:
            self._git._run("checkout", "-b", branch)
            log.info("branch.created", branch=branch)

        return branch

    def checkout(self, branch_name: str) -> GitResult:
        """Switch to an existing branch.

        Args:
            branch_name: Full branch name to switch to.

        Returns:
            GitResult from the checkout operation.
        """
        return self._git._run("checkout", branch_name)

    def merge_to_main(
        self,
        branch_name: str,
        *,
        main_branch: str | None = None,
        delete_after: bool = True,
    ) -> GitResult:
        """Merge a branch into the main branch.

        Steps:
        1. Switch to main branch
        2. Merge the specified branch
        3. Optionally delete the merged branch

        Args:
            branch_name: Branch to merge.
            main_branch: Target branch (auto-detected if None).
            delete_after: Delete the branch after merge (default True).

        Returns:
            GitResult from the merge operation.
        """
        target = main_branch or self._detect_main_branch()

        self._git._run("checkout", target)
        result = self._git._run("merge", branch_name)

        if delete_after and result.success:
            self._git._run("branch", "-d", branch_name, check=False)
            log.info("branch.deleted_after_merge", branch=branch_name)

        return result

    def cleanup_merged_branches(self) -> list[str]:
        """Remove branches that have been merged into main.

        Only considers branches matching ``agent/*`` pattern.

        Returns:
            List of deleted branch names.
        """
        main = self._detect_main_branch()
        self._git._run("checkout", main, check=False)

        # Get merged branches
        result = self._git._run("branch", "--merged", main)
        merged = [
            line.strip().lstrip("* ")
            for line in result.message.splitlines()
            if line.strip() and line.strip().lstrip("* ") != main
        ]

        deleted: list[str] = []
        for branch in merged:
            if branch.startswith("agent/"):
                self._git._run("branch", "-d", branch, check=False)
                deleted.append(branch)
                log.info("branch.cleanup", branch=branch)

        return deleted

    def list_branches(self) -> list[str]:
        """List all local branches.

        Returns:
            List of branch names (without leading ``*`` or whitespace).
        """
        result = self._git._run("branch", check=False)
        return [line.strip().lstrip("* ") for line in result.message.splitlines() if line.strip()]

    def current_branch(self) -> str:
        """Return the currently active branch name."""
        return self._git.current_branch()

    def _detect_main_branch(self) -> str:
        """Detect whether the repo uses ``main`` or ``master``.

        Delegates to ``GitService.detect_default_branch()`` which checks
        remote refs, ``git remote show``, and local branches (R-022).
        """
        return self._git.detect_default_branch()
