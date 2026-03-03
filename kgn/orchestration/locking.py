"""NodeLockService — concurrent access guard for nodes.

Prevents multiple agents from simultaneously editing the same node
by providing advisory locking with automatic expiration (lease pattern).

Rule compliance:
- R1  — all SQL resides in repository layer
- R12 — service-layer orchestration only
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import structlog

from kgn.db.repository import KgnRepository
from kgn.errors import KgnError, KgnErrorCode

log = structlog.get_logger()

# Default lock duration: 5 minutes
DEFAULT_LOCK_DURATION_SEC = 300


# ── Result types ───────────────────────────────────────────────────────


@dataclass
class LockInfo:
    """Information about a node's current lock state."""

    node_id: uuid.UUID
    locked_by: uuid.UUID
    lock_expires_at: datetime
    is_expired: bool


@dataclass
class LockResult:
    """Result of a lock acquisition attempt."""

    acquired: bool
    node_id: uuid.UUID
    locked_by: uuid.UUID | None = None
    lock_expires_at: datetime | None = None


# ── Service ────────────────────────────────────────────────────────────


class NodeLockService:
    """Advisory node locking with automatic lease expiration.

    Provides acquire/release/check operations on node locks.
    Expired locks are automatically treated as released.

    Usage::

        svc = NodeLockService(repo)
        result = svc.acquire(node_id, agent_id, duration_sec=300)
        if result.acquired:
            # ... do work ...
            svc.release(node_id, agent_id)
    """

    def __init__(self, repo: KgnRepository) -> None:
        self._repo = repo

    def acquire(
        self,
        node_id: uuid.UUID,
        agent_id: uuid.UUID,
        *,
        duration_sec: int = DEFAULT_LOCK_DURATION_SEC,
    ) -> LockResult:
        """Acquire an advisory lock on a node.

        - If the node is unlocked or the existing lock has expired,
          acquires the lock for the given agent.
        - If the agent already holds the lock, refreshes the expiry.
        - If another agent holds a non-expired lock, returns
          ``acquired=False``.

        Args:
            node_id: The node to lock.
            agent_id: The agent requesting the lock.
            duration_sec: Lock duration in seconds (default: 300).

        Returns:
            LockResult with acquisition status.
        """
        row = self._repo.acquire_node_lock(node_id, agent_id, duration_sec)
        if row is None:
            # Lock held by another agent
            info = self.check(node_id)
            log.info(
                "lock_denied",
                node_id=str(node_id),
                agent_id=str(agent_id),
                held_by=str(info.locked_by) if info else "unknown",
            )
            return LockResult(
                acquired=False,
                node_id=node_id,
                locked_by=info.locked_by if info else None,
                lock_expires_at=info.lock_expires_at if info else None,
            )

        log.debug(
            "lock_acquired",
            node_id=str(node_id),
            agent_id=str(agent_id),
            expires_at=str(row["lock_expires_at"]),
        )
        return LockResult(
            acquired=True,
            node_id=node_id,
            locked_by=agent_id,
            lock_expires_at=row["lock_expires_at"],
        )

    def release(
        self,
        node_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> bool:
        """Release a lock held by the given agent.

        Returns True if the lock was released, False if the agent
        does not hold the lock.
        """
        released = self._repo.release_node_lock(node_id, agent_id)
        if released:
            log.debug(
                "lock_released",
                node_id=str(node_id),
                agent_id=str(agent_id),
            )
        else:
            log.debug(
                "lock_release_no_op",
                node_id=str(node_id),
                agent_id=str(agent_id),
            )
        return released

    def release_all(self, agent_id: uuid.UUID) -> int:
        """Release all locks held by an agent.

        Useful for cleanup when a task completes or an agent disconnects.

        Returns the number of locks released.
        """
        count = self._repo.release_all_node_locks(agent_id)
        if count > 0:
            log.debug(
                "locks_released_all",
                agent_id=str(agent_id),
                count=count,
            )
        return count

    def check(self, node_id: uuid.UUID) -> LockInfo | None:
        """Check the current lock state of a node.

        Returns LockInfo if locked (even if expired), or None if unlocked.
        """
        row = self._repo.get_node_lock(node_id)
        if row is None:
            return None

        return LockInfo(
            node_id=node_id,
            locked_by=row["locked_by"],
            lock_expires_at=row["lock_expires_at"],
            is_expired=row["is_expired"],
        )

    def check_write_permission(
        self,
        node_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Raise KgnError if the node is locked by another agent.

        Used as a guard before write operations. Does NOT acquire a lock.
        Expired locks are treated as released (no error raised).

        Raises:
            KgnError(KGN-560): Node is locked by another agent.
        """
        info = self.check(node_id)
        if info is None or info.is_expired:
            return  # unlocked or expired
        if info.locked_by == agent_id:
            return  # same agent holds the lock
        raise KgnError(
            code=KgnErrorCode.NODE_LOCKED,
            message=(
                f"Node {node_id} is locked by agent {info.locked_by} until {info.lock_expires_at}"
            ),
        )

    def cleanup_expired(self) -> int:
        """Release all expired locks across all nodes.

        Returns the number of locks cleaned up.
        """
        count = self._repo.cleanup_expired_locks()
        if count > 0:
            log.info("locks_expired_cleanup", count=count)
        return count
