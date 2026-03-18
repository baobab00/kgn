"""ConflictResolutionService — concurrent edit detection and mediation.

Detects when two agents edit the same node, creates an ISSUE + review TASK
for a reviewer agent, and provides resolution actions (accept_a, accept_b,
merge).

Rule compliance:
- R1  — all SQL resides in repository layer
- R12 — service-layer orchestration only
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog

from kgn.db.repository import KgnRepository
from kgn.errors import KgnError, KgnErrorCode
from kgn.models.edge import EdgeRecord
from kgn.models.enums import (
    ActivityType,
    EdgeType,
    NodeStatus,
    NodeType,
)
from kgn.models.node import NodeRecord
from kgn.task.service import TaskService

log = structlog.get_logger()

# Priority boost for conflict review tasks (higher = sooner in queue)
CONFLICT_TASK_PRIORITY = 50


# ── Result types ───────────────────────────────────────────────────────


@dataclass
class ConflictDetection:
    """Result of conflict detection on a node update."""

    detected: bool
    node_id: uuid.UUID
    current_agent: uuid.UUID
    previous_agent: uuid.UUID | None = None
    previous_version: int | None = None


@dataclass
class ConflictRecord:
    """Result of creating a conflict review task."""

    issue_node_id: uuid.UUID
    review_task_node_id: uuid.UUID
    task_queue_id: uuid.UUID
    contradicts_edge_id: int
    node_id: uuid.UUID
    agent_a: uuid.UUID
    agent_b: uuid.UUID


@dataclass
class ResolutionResult:
    """Result of resolving a conflict."""

    resolution: str  # "accept_a" | "accept_b" | "merge"
    node_id: uuid.UUID
    accepted_version: int | None = None
    deprecated_node_id: uuid.UUID | None = None


# ── Service ────────────────────────────────────────────────────────────


class ConflictResolutionService:
    """Detects concurrent edits and orchestrates reviewer mediation.

    Workflow:
    1. On node UPDATE, ``detect()`` checks if the previous version was
       by a different agent → concurrent edit.
    2. ``create_review_task()`` creates an ISSUE node describing the
       conflict and a TASK node tagged ``role:reviewer`` for resolution.
    3. ``resolve()`` applies the chosen resolution (accept one version
       or merge) and marks the conflict as handled.

    Usage::

        svc = ConflictResolutionService(repo)
        det = svc.detect(node_id, current_agent_id)
        if det.detected:
            record = svc.create_review_task(
                project_id, node_id, det.previous_agent, det.current_agent,
            )
    """

    def __init__(
        self,
        repo: KgnRepository,
        task_svc: TaskService | None = None,
    ) -> None:
        self._repo = repo
        self._task_svc = task_svc

    # ── Detection ──────────────────────────────────────────────────

    def detect(
        self,
        node_id: uuid.UUID,
        current_agent_id: uuid.UUID,
    ) -> ConflictDetection:
        """Detect if a node update constitutes a concurrent edit conflict.

        A conflict is detected when:
        - The node has at least one version in ``node_versions``
        - The latest version's ``updated_by`` differs from *current_agent_id*

        This is a lightweight heuristic: it doesn't require the lock to
        have been violated (advisory locks are non-blocking). It catches
        the case where Agent A and Agent B both legitimately update the
        same node in sequence.

        Returns:
            ConflictDetection with ``detected=True`` if agents differ.
        """
        latest = self._repo.get_latest_node_version(node_id)
        if latest is None:
            # First version — no conflict possible
            return ConflictDetection(
                detected=False,
                node_id=node_id,
                current_agent=current_agent_id,
            )

        previous_agent = latest["updated_by"]
        if previous_agent is None:
            return ConflictDetection(
                detected=False,
                node_id=node_id,
                current_agent=current_agent_id,
            )

        if previous_agent == current_agent_id:
            # Same agent updating again — no conflict
            return ConflictDetection(
                detected=False,
                node_id=node_id,
                current_agent=current_agent_id,
                previous_agent=previous_agent,
                previous_version=latest["version"],
            )

        log.info(
            "conflict_detected",
            node_id=str(node_id),
            previous_agent=str(previous_agent),
            current_agent=str(current_agent_id),
            version=latest["version"],
        )
        return ConflictDetection(
            detected=True,
            node_id=node_id,
            current_agent=current_agent_id,
            previous_agent=previous_agent,
            previous_version=latest["version"],
        )

    # ── Review task creation ───────────────────────────────────────

    def create_review_task(
        self,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
        agent_a: uuid.UUID,
        agent_b: uuid.UUID,
    ) -> ConflictRecord:
        """Create ISSUE + review TASK nodes for a concurrent edit conflict.

        Creates:
        1. An ISSUE node describing the conflict
        2. A TASK node tagged ``role:reviewer`` for mediation
        3. A CONTRADICTS edge between the ISSUE and the conflicted node
        4. Enqueues the TASK in the task queue with elevated priority

        Args:
            project_id: Project containing the conflicted node.
            node_id: The node that was concurrently edited.
            agent_a: The agent who made the previous edit.
            agent_b: The agent who made the current (conflicting) edit.

        Returns:
            ConflictRecord with created node/edge IDs.
        """
        # Fetch the conflicted node for title/context
        target_node = self._repo.get_node_by_id(node_id)
        node_title = target_node.title if target_node else str(node_id)

        # 1. Create ISSUE node
        issue_id = uuid.uuid4()
        issue_body = (
            f"## Conflict Description\n\n"
            f"Node **{node_title}** (`{node_id}`) was concurrently edited "
            f"by two different agents.\n\n"
            f"- **Agent A** (previous): `{agent_a}`\n"
            f"- **Agent B** (current): `{agent_b}`\n\n"
            f"## Resolution Required\n\n"
            f"A reviewer agent should examine both versions and choose:\n"
            f"- `accept_a` — revert to Agent A's version\n"
            f"- `accept_b` — keep Agent B's version (current)\n"
            f"- `merge` — manually combine both versions\n"
        )
        issue_node = NodeRecord(
            id=issue_id,
            project_id=project_id,
            type=NodeType.ISSUE,
            status=NodeStatus.ACTIVE,
            title=f"Concurrent edit conflict: {node_title}",
            body_md=issue_body,
            tags=["conflict", f"node:{node_id}"],
            confidence=1.0,
            created_by=agent_b,
        )
        self._repo.upsert_node(issue_node)

        # 2. Create TASK node (tagged for reviewer)
        task_id = uuid.uuid4()
        task_body = (
            f"## Context\n\n"
            f"Resolve concurrent edit conflict on node `{node_id}` "
            f"(**{node_title}**).\n\n"
            f"### Previous Version (Agent A: `{agent_a}`)\n\n"
            f"Check node_versions for the previous state.\n\n"
            f"### Current Version (Agent B: `{agent_b}`)\n\n"
            f"The current node content reflects Agent B's edit.\n\n"
            f"## Instructions\n\n"
            f"1. Compare both versions\n"
            f"2. Call `conflict_resolve` with resolution: "
            f"`accept_a`, `accept_b`, or `merge`\n"
        )
        task_node = NodeRecord(
            id=task_id,
            project_id=project_id,
            type=NodeType.TASK,
            status=NodeStatus.ACTIVE,
            title=f"Resolve conflict: {node_title}",
            body_md=task_body,
            tags=["conflict", "role:reviewer", f"node:{node_id}"],
            confidence=1.0,
            created_by=agent_b,
        )
        self._repo.upsert_node(task_node)

        # 3. Create CONTRADICTS edge: ISSUE ↔ conflicted node
        edge_id = self._repo.insert_contradicts_edge(
            project_id,
            issue_id,
            node_id,
            "PENDING",
            note=f"Concurrent edit by agents {agent_a} and {agent_b}",
            created_by=agent_b,
        )

        # 4. Create DEPENDS_ON edge: TASK → ISSUE
        dep_edge = EdgeRecord(
            project_id=project_id,
            from_node_id=task_id,
            to_node_id=issue_id,
            type=EdgeType.RESOLVES,
            note="Review task resolves this conflict issue",
            created_by=agent_b,
        )
        self._repo.insert_edge(dep_edge)

        # 5. Enqueue the review TASK with elevated priority (R10)
        if self._task_svc is not None:
            eq_result = self._task_svc.enqueue(
                project_id,
                task_id,
                priority=CONFLICT_TASK_PRIORITY,
            )
            queue_id = eq_result.task_queue_id
        else:
            queue_id = self._repo.enqueue_task(
                project_id,
                task_id,
                priority=CONFLICT_TASK_PRIORITY,
            )

        # 6. Log activity
        self._repo.log_activity(
            project_id=project_id,
            agent_id=agent_b,
            activity_type=ActivityType.CONFLICT_DETECTED,
            target_node_id=node_id,
            message=(
                f"Concurrent edit conflict detected on '{node_title}' "
                f"between agents {agent_a} and {agent_b}"
            ),
        )

        log.info(
            "conflict_review_created",
            node_id=str(node_id),
            issue_id=str(issue_id),
            task_id=str(task_id),
            queue_id=str(queue_id),
        )

        return ConflictRecord(
            issue_node_id=issue_id,
            review_task_node_id=task_id,
            task_queue_id=queue_id,
            contradicts_edge_id=edge_id,
            node_id=node_id,
            agent_a=agent_a,
            agent_b=agent_b,
        )

    # ── Resolution ─────────────────────────────────────────────────

    def resolve(
        self,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
        resolution: str,
        *,
        agent_id: uuid.UUID | None = None,
        merge_body: str | None = None,
    ) -> ResolutionResult:
        """Resolve a concurrent edit conflict.

        Args:
            project_id: Project containing the node.
            node_id: The conflicted node.
            resolution: One of ``"accept_a"``, ``"accept_b"``, ``"merge"``.
            agent_id: The agent performing the resolution (for logging).
            merge_body: Required when resolution is ``"merge"`` — the
                merged content to write.

        Actions per resolution:
        - ``accept_a``: Revert node to the previous version (from
          node_versions), current version becomes a version entry.
        - ``accept_b``: Keep current content as-is (no DB change needed).
        - ``merge``: Update node body with *merge_body*.

        In all cases:
        - Any PENDING CONTRADICTS edge involving this node is APPROVED.
        - Activity is logged.

        Raises:
            KgnError(KGN-571): Invalid resolution or missing merge_body.
            KgnError(KGN-300): Node not found.
        """
        valid_resolutions = ("accept_a", "accept_b", "merge")
        if resolution not in valid_resolutions:
            raise KgnError(
                code=KgnErrorCode.CONFLICT_RESOLUTION_FAILED,
                message=f"Invalid resolution '{resolution}'. "
                f"Must be one of: {', '.join(valid_resolutions)}",
            )

        if resolution == "merge" and not merge_body:
            raise KgnError(
                code=KgnErrorCode.CONFLICT_RESOLUTION_FAILED,
                message="merge_body is required for 'merge' resolution",
            )

        node = self._repo.get_node_by_id(node_id)
        if node is None:
            raise KgnError(
                code=KgnErrorCode.NODE_NOT_FOUND,
                message=f"Node {node_id} not found",
            )

        result: ResolutionResult

        if resolution == "accept_a":
            # Revert to previous version
            latest_ver = self._repo.get_latest_node_version(node_id)
            if latest_ver is None:
                raise KgnError(
                    code=KgnErrorCode.CONFLICT_RESOLUTION_FAILED,
                    message=f"No previous version found for node {node_id}",
                )
            # Revert node to pre-conflict version.
            # Phase 12 / Step 7: restore ALL mutable fields from the
            # version snapshot.  Fields that are NULL in legacy versions
            # (pre-migration-010) gracefully fall back to the current
            # node value so old data is not lost.
            reverted = NodeRecord(
                id=node.id,
                project_id=node.project_id,
                type=latest_ver.get("type") or node.type,
                status=latest_ver.get("status") or node.status,
                title=latest_ver["title"],
                body_md=latest_ver["body_md"],
                file_path=latest_ver.get("file_path") or node.file_path,
                content_hash=latest_ver.get("content_hash"),
                tags=latest_ver.get("tags") or node.tags,
                confidence=latest_ver.get("confidence") if latest_ver.get("confidence") is not None else node.confidence,
                created_by=agent_id or node.created_by,
            )
            self._repo.upsert_node(reverted)
            result = ResolutionResult(
                resolution=resolution,
                node_id=node_id,
                accepted_version=latest_ver["version"],
            )

        elif resolution == "accept_b":
            # Keep current content — no DB change needed
            version_count = self._repo.get_node_version_count(node_id)
            result = ResolutionResult(
                resolution=resolution,
                node_id=node_id,
                accepted_version=version_count + 1,
            )

        else:  # merge
            if merge_body is None:
                raise KgnError(
                    code=KgnErrorCode.CONFLICT_RESOLUTION_FAILED,
                    message="merge_body is required for merge resolution",
                )
            # upsert_node() saves current state to node_versions,
            # then updates — no private method access needed.
            merged = NodeRecord(
                id=node.id,
                project_id=node.project_id,
                type=node.type,
                status=node.status,
                title=node.title,
                body_md=merge_body,
                content_hash=None,  # merged content has new hash
                tags=node.tags,
                confidence=node.confidence,
                created_by=agent_id or node.created_by,
            )
            self._repo.upsert_node(merged)
            result = ResolutionResult(
                resolution=resolution,
                node_id=node_id,
            )

        # Approve any PENDING CONTRADICTS edges involving this node
        self._approve_pending_contradicts(project_id, node_id)

        # Log resolution activity
        if agent_id:
            self._repo.log_activity(
                project_id=project_id,
                agent_id=agent_id,
                activity_type=ActivityType.CONFLICT_RESOLVED,
                target_node_id=node_id,
                message=f"Conflict resolved on '{node.title}' via {resolution}",
            )

        log.info(
            "conflict_resolved",
            node_id=str(node_id),
            resolution=resolution,
        )

        return result

    # ── Helpers ─────────────────────────────────────────────────────

    def _approve_pending_contradicts(
        self,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
    ) -> int:
        """Approve all PENDING CONTRADICTS edges involving *node_id*.

        Returns the number of edges approved.
        """
        # Find CONTRADICTS edges where node_id is on either side
        edges = self._repo.get_contradicts_edges(project_id, status_filter="PENDING")
        approved = 0
        for edge in edges:
            if edge["from_node_id"] == node_id or edge["to_node_id"] == node_id:
                self._repo.update_edge_status(edge["id"], "APPROVED")
                approved += 1
        return approved
