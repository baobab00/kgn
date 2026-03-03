"""Git integration package."""

from kgn.git.branch import BranchService, task_branch_name
from kgn.git.service import GitLogEntry, GitResult, GitService, GitStatusResult

__all__ = [
    "BranchService",
    "GitLogEntry",
    "GitResult",
    "GitService",
    "GitStatusResult",
    "task_branch_name",
]
