"""TaskService — task lifecycle orchestration.

Combines ``KgnRepository`` task-queue methods with subgraph extraction,
optional vector search, and dependency resolution to produce a
``ContextPackage`` on checkout.

Rule compliance:
- R1  — no SQL outside repository
- R5  — agent_activities INSERT only (via repo.log_activity)
- R10 — task_queue state transitions only via TaskService / Repository
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from kgn.db.repository import KgnRepository, SimilarNode, TaskQueueItem
from kgn.embedding.client import EmbeddingClient
from kgn.errors import KgnError, KgnErrorCode
from kgn.graph.subgraph import SubgraphResult, SubgraphService
from kgn.models.enums import ActivityType
from kgn.models.node import NodeRecord
from kgn.orchestration.handoff import HandoffService
from kgn.orchestration.locking import NodeLockService
from kgn.task.dependency import DependencyCheckResult, DependencyService, UnblockedTask

# ── Result types ───────────────────────────────────────────────────────


@dataclass
class ContextPackage:
    """Context package required for task execution."""

    task: TaskQueueItem
    node: NodeRecord
    subgraph: SubgraphResult
    similar_nodes: list[SimilarNode]


@dataclass
class EnqueueResult:
    """Return value of :meth:`TaskService.enqueue`."""

    task_queue_id: uuid.UUID
    state: str  # "READY" or "BLOCKED"
    dependency_check: DependencyCheckResult


@dataclass
class CompleteResult:
    """Return value of :meth:`TaskService.complete`."""

    unblocked_tasks: list[UnblockedTask]


# ── Service ────────────────────────────────────────────────────────────


class TaskService:
    """High-level task lifecycle management.

    Orchestrates repository CRUD, subgraph extraction, and optional
    vector-similarity search into a single service layer.
    """

    def __init__(
        self,
        repo: KgnRepository,
        subgraph_service: SubgraphService,
        embedding_client: EmbeddingClient | None = None,
        *,
        handoff_service: HandoffService | None = None,
    ) -> None:
        self._repo = repo
        self._subgraph = subgraph_service
        self._embedding_client = embedding_client
        self._deps = DependencyService(repo)
        self._handoff = handoff_service or HandoffService(repo)
        self._locks = NodeLockService(repo)

    # ── Public API ─────────────────────────────────────────────────

    def enqueue(
        self,
        project_id: uuid.UUID,
        task_node_id: uuid.UUID,
        *,
        priority: int = 100,
    ) -> EnqueueResult:
        """Register a TASK node into the task queue.

        Checks DEPENDS_ON edges before enqueuing:
        - All dependencies satisfied → state = READY
        - Any dependency unmet → state = BLOCKED
        - Cycle detected → raises KgnError(KGN-404)

        Node-existence and TASK-type validation is performed by the
        underlying ``repo.enqueue_task()`` call.

        Returns:
            EnqueueResult with queue ID, initial state, and dep check info.
        """
        dep_check = self._deps.check_dependencies(task_node_id, project_id)

        initial_state = "READY" if dep_check.all_satisfied else "BLOCKED"

        queue_id = self._repo.enqueue_task(
            project_id,
            task_node_id,
            priority=priority,
            state=initial_state,
        )

        return EnqueueResult(
            task_queue_id=queue_id,
            state=initial_state,
            dependency_check=dep_check,
        )

    def checkout(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        *,
        lease_duration_sec: int = 600,
        role_filter: str | None = None,
    ) -> ContextPackage | None:
        """Consume a READY task and build a context package.

        Steps:
        1. ``repo.checkout_task()`` — SKIP LOCKED consumption
        2. Fetch the full TASK ``NodeRecord``
        3. Extract depth-2 subgraph via ``SubgraphService``
        4. Top-3 similar nodes (only when embedding is available)
        5. Return assembled ``ContextPackage``

        Args:
            project_id: Project scope.
            agent_id: The consuming agent.
            lease_duration_sec: Lease duration in seconds.
            role_filter: If provided, only checkout tasks tagged
                         ``role:<role_filter>``.

        Returns ``None`` when no READY task exists.
        """
        task = self._repo.checkout_task(
            project_id,
            agent_id,
            lease_duration_sec=lease_duration_sec,
            role_filter=role_filter,
        )
        if task is None:
            return None

        # 1b. Log checkout activity
        self._log_checkout_activity(task, agent_id)

        # 1c. Auto-lock the task node for this agent
        self._locks.acquire(
            task.task_node_id,
            agent_id,
            duration_sec=lease_duration_sec,
        )

        # 2. Full TASK node record
        node = self._repo.get_node_by_id(task.task_node_id)
        if node is None:
            raise KgnError(
                code=KgnErrorCode.TASK_NODE_INVALID,
                message=f"TASK node {task.task_node_id} disappeared",
            )

        # 3. Subgraph (depth=2)
        subgraph = self._subgraph.extract(
            root_id=task.task_node_id,
            project_id=project_id,
            depth=2,
        )

        # 4. Similar nodes (Top-3) — only when embedding is available
        similar_nodes: list[SimilarNode] = []
        if self._embedding_client is not None:
            embedding = self._repo.get_node_embedding(task.task_node_id)
            if embedding is not None:
                similar_nodes = self._repo.search_similar_nodes(
                    query_embedding=embedding,
                    project_id=project_id,
                    top_k=3,
                    exclude_ids={task.task_node_id},
                )

        return ContextPackage(
            task=task,
            node=node,
            subgraph=subgraph,
            similar_nodes=similar_nodes,
        )

    def _log_checkout_activity(
        self,
        task: TaskQueueItem,
        agent_id: uuid.UUID,
    ) -> None:
        """Record a TASK_CHECKOUT activity."""
        self._repo.log_activity(
            project_id=task.project_id,
            agent_id=agent_id,
            activity_type=ActivityType.TASK_CHECKOUT,
            target_node_id=task.task_node_id,
            task_queue_id=task.id,
            message=f"Checked out task {task.id}",
        )

    def complete(self, task_id: uuid.UUID) -> CompleteResult:
        """Mark a task as DONE and unblock dependents.

        State transition: IN_PROGRESS → DONE, then re-evaluate
        BLOCKED dependents of the completed task.

        Returns:
            CompleteResult with list of newly unblocked tasks.
        """
        # Get task info before state transition for activity logging
        task = self._repo.get_task_status(task_id)
        self._repo.complete_task(task_id)

        # Unblock eligible dependents
        unblocked: list[UnblockedTask] = []
        if task is not None:
            # Release node lock before unblocking
            if task.leased_by is not None:
                self._locks.release(task.task_node_id, task.leased_by)

            unblocked = self._deps.unblock_dependents(task.task_node_id, task.project_id)

            # Propagate handoff context to downstream tasks
            self._handoff.propagate_context(
                task.task_node_id,
                task.project_id,
            )

        # Log activity (skip if leased_by is unknown)
        if task is not None and task.leased_by is not None:
            self._repo.log_activity(
                project_id=task.project_id,
                agent_id=task.leased_by,
                activity_type=ActivityType.TASK_COMPLETED,
                target_node_id=task.task_node_id,
                task_queue_id=task.id,
                message=f"Task {task_id} completed",
            )

        return CompleteResult(unblocked_tasks=unblocked)

    def fail(self, task_id: uuid.UUID, *, reason: str = "") -> None:
        """Mark a task as FAILED (IN_PROGRESS → FAILED)."""
        # Get task info before state transition for activity logging
        task = self._repo.get_task_status(task_id)
        self._repo.fail_task(task_id, reason=reason)

        # Release node lock on failure
        if task is not None and task.leased_by is not None:
            self._locks.release(task.task_node_id, task.leased_by)
        # Log activity (skip if leased_by is unknown)
        if task is not None and task.leased_by is not None:
            self._repo.log_activity(
                project_id=task.project_id,
                agent_id=task.leased_by,
                activity_type=ActivityType.TASK_FAILED,
                target_node_id=task.task_node_id,
                task_queue_id=task.id,
                message=reason if reason else f"Task {task_id} failed",
            )

    def requeue_expired(self, project_id: uuid.UUID) -> int:
        """Requeue lease-expired IN_PROGRESS tasks back to READY.

        Returns the number of recovered tasks.
        """
        return self._repo.requeue_expired(project_id)
