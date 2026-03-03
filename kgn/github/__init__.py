"""GitHub integration package — REST API client, sync, and PR automation."""

from kgn.github.client import GitHubClient
from kgn.github.pr_service import PRContext, PRResult, PullRequestService
from kgn.github.sync_service import ConflictDetector, ConflictStrategy, SyncResult, SyncService

__all__ = [
    "ConflictDetector",
    "ConflictStrategy",
    "GitHubClient",
    "PRContext",
    "PRResult",
    "PullRequestService",
    "SyncResult",
    "SyncService",
]
