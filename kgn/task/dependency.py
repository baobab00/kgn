"""DependencyService — DEPENDS_ON edge-based task dependency resolution.

Provides dependency checking on enqueue and automatic BLOCKED→READY
transitions on task completion.

Rule compliance:
- R1  — no SQL outside repository; all queries via KgnRepository
- R10 — task_queue state transitions through repository methods
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import structlog

from kgn.db.repository import KgnRepository
from kgn.errors import KgnError, KgnErrorCode
from kgn.models.enums import NodeType

log = structlog.get_logger("kgn.task.dependency")

# ── Result types ───────────────────────────────────────────────────────


@dataclass
class BlockingTask:
    """A prerequisite task that is not yet DONE."""

    node_id: uuid.UUID
    title: str
    state: str


@dataclass
class DependencyCheckResult:
    """Result of dependency satisfaction check."""

    all_satisfied: bool
    blocking_tasks: list[BlockingTask] = field(default_factory=list)
    has_cycle: bool = False


@dataclass
class UnblockedTask:
    """A task that was transitioned from BLOCKED → READY."""

    task_queue_id: uuid.UUID
    node_title: str


# ── Service ────────────────────────────────────────────────────────────


class DependencyService:
    """DEPENDS_ON edge-based task dependency resolution.

    Edge direction convention:
        A --DEPENDS_ON--> B  means "A depends on B" (B must complete first).

    Key behaviours:
    1. ``check_dependencies()`` — on enqueue, determines if the task
       should start as READY or BLOCKED.
    2. ``unblock_dependents()`` — on task_complete, transitions eligible
       BLOCKED dependents to READY.
    3. Cycle detection via DFS prevents infinite dependency chains.
    """

    def __init__(self, repo: KgnRepository) -> None:
        self._repo = repo

    # ── Public API ─────────────────────────────────────────────────

    def check_dependencies(
        self,
        task_node_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> DependencyCheckResult:
        """Check whether all prerequisite tasks are DONE.

        Steps:
            1. Find outgoing DEPENDS_ON edges from *task_node_id*.
            2. For each dependency target, check if it has a DONE task entry.
            3. Detect cycles via DFS before returning.

        Returns:
            DependencyCheckResult with satisfaction status and blocking list.

        Raises:
            KgnError(KGN-404): If a dependency cycle is detected.
        """
        # 1. Cycle detection (R-015: uses bulk-loaded edges)
        if self._has_cycle(task_node_id, project_id):
            log.warning(
                "dependency_cycle_detected",
                task_node_id=str(task_node_id),
                project_id=str(project_id),
            )
            raise KgnError(
                KgnErrorCode.TASK_DEPENDENCY_CYCLE,
                f"Dependency cycle detected involving node {task_node_id}",
            )

        # 2. Get outgoing DEPENDS_ON edges
        dep_edges = self._repo.get_dependency_edges(task_node_id, project_id)
        if not dep_edges:
            return DependencyCheckResult(all_satisfied=True)

        # 3. Batch-check dependency targets (R-014: single queries)
        dep_node_ids = {edge.to_node_id for edge in dep_edges}
        node_map = self._repo.get_nodes_by_ids(dep_node_ids)
        task_node_id_set = {nid for nid, node in node_map.items() if node.type == NodeType.TASK}
        task_map = self._repo.get_tasks_by_node_ids(task_node_id_set, project_id)

        blocking: list[BlockingTask] = []
        for edge in dep_edges:
            dep_node_id = edge.to_node_id
            dep_node = node_map.get(dep_node_id)

            # Only consider TASK-type targets for dependency checking.
            # DEPENDS_ON edges to non-TASK nodes (SPEC, DECISION, etc.)
            # are semantic relationships, not task prerequisites.
            if dep_node is None or dep_node.type != NodeType.TASK:
                continue

            dep_task = task_map.get(dep_node_id)
            if dep_task is None:
                # TASK node exists but not enqueued → treat as blocking
                blocking.append(
                    BlockingTask(
                        node_id=dep_node_id,
                        title=dep_node.title,
                        state="NOT_ENQUEUED",
                    )
                )
                continue

            if dep_task.state != "DONE":
                blocking.append(
                    BlockingTask(
                        node_id=dep_node_id,
                        title=dep_node.title,
                        state=dep_task.state,
                    )
                )

        return DependencyCheckResult(
            all_satisfied=len(blocking) == 0,
            blocking_tasks=blocking,
        )

    def unblock_dependents(
        self,
        completed_task_node_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> list[UnblockedTask]:
        """Transition BLOCKED dependents of a completed task to READY.

        For each BLOCKED dependent:
            1. Re-check ALL its dependencies (not just the completed one).
            2. Only unblock if ALL dependencies are now satisfied.

        Returns list of tasks that were transitioned to READY.
        """
        # Find BLOCKED tasks that depend on the completed node
        blocked_dependents = self._repo.find_blocked_dependents(completed_task_node_id, project_id)

        unblocked: list[UnblockedTask] = []

        for dep_task in blocked_dependents:
            # Re-check ALL dependencies for this blocked task
            dep_edges = self._repo.get_dependency_edges(dep_task.task_node_id, project_id)
            all_done = True
            for edge in dep_edges:
                prereq = self._repo.get_task_by_node_id(edge.to_node_id, project_id)
                if prereq is None or prereq.state != "DONE":
                    all_done = False
                    break

            if all_done:
                transitioned = self._repo.unblock_task(dep_task.id)
                if transitioned:
                    node = self._repo.get_node_by_id(dep_task.task_node_id)
                    node_title = node.title if node else str(dep_task.task_node_id)
                    unblocked.append(
                        UnblockedTask(
                            task_queue_id=dep_task.id,
                            node_title=node_title,
                        )
                    )
                    log.info(
                        "task_unblocked",
                        task_queue_id=str(dep_task.id),
                        node_title=node_title,
                    )

        return unblocked

    # ── Private helpers ────────────────────────────────────────────

    def _has_cycle(
        self,
        start_node_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> bool:
        """Detect cycles in DEPENDS_ON graph via iterative DFS.

        Returns True if *start_node_id* can reach itself through
        DEPENDS_ON edges.

        Loads all DEPENDS_ON edges for the project in a single query
        and traverses the adjacency list in memory (R-015).
        """
        all_edges = self._repo.get_all_dependency_edges(project_id)

        # Build adjacency list: from_node → [to_node, …]
        adjacency: dict[uuid.UUID, list[uuid.UUID]] = {}
        for edge in all_edges:
            adjacency.setdefault(edge.from_node_id, []).append(edge.to_node_id)

        visited: set[uuid.UUID] = set()
        stack: list[uuid.UUID] = [start_node_id]

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)

            for target in adjacency.get(current, []):
                if target == start_node_id:
                    return True  # Cycle found
                if target not in visited:
                    stack.append(target)

        return False
