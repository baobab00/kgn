"""Integration tests for DependencyService + TaskService dependency integration.

Requires a running PostgreSQL instance (Docker on port 5433).
Each test runs inside a SAVEPOINT that is rolled back at teardown.

Tests cover:
- Simple dependency chain (A→B)
- Multiple dependencies (A→B, A→C)
- Diamond dependency (A→B, A→C, B→D, C→D)
- Partial unblock (only unblock when ALL deps satisfied)
- Cycle detection
- Enqueue with BLOCKED state
- task_complete auto-unblock
- Independent tasks (no deps) remain READY
"""

from __future__ import annotations

import uuid

import pytest

from kgn.db.repository import KgnRepository
from kgn.errors import KgnError, KgnErrorCode
from kgn.graph.subgraph import SubgraphService
from kgn.models.edge import EdgeRecord
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.task.dependency import DependencyService
from kgn.task.service import TaskService

# ── Helpers ────────────────────────────────────────────────────────────


def _make_task_node(
    project_id: uuid.UUID,
    *,
    title: str = "Task",
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.TASK,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md="## Context\n\nTask body",
        content_hash=uuid.uuid4().hex,
    )


def _create_dependency_edge(
    repo: KgnRepository,
    project_id: uuid.UUID,
    from_node_id: uuid.UUID,
    to_node_id: uuid.UUID,
) -> None:
    """Create a DEPENDS_ON edge: from_node depends on to_node."""
    repo.insert_edge(
        EdgeRecord(
            project_id=project_id,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            type=EdgeType.DEPENDS_ON,
        )
    )


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def dep_svc(repo: KgnRepository) -> DependencyService:
    return DependencyService(repo)


@pytest.fixture
def task_svc(repo: KgnRepository) -> TaskService:
    return TaskService(repo, SubgraphService(repo))


# ══════════════════════════════════════════════════════════════════════
#  DependencyService.check_dependencies
# ══════════════════════════════════════════════════════════════════════


class TestCheckDependencies:
    def test_no_dependencies_all_satisfied(
        self,
        dep_svc: DependencyService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """Task with no DEPENDS_ON edges → all_satisfied=True."""
        node = _make_task_node(project_id, title="Independent")
        repo.upsert_node(node)

        result = dep_svc.check_dependencies(node.id, project_id)
        assert result.all_satisfied is True
        assert result.blocking_tasks == []
        assert result.has_cycle is False

    def test_dependency_not_done_blocks(
        self,
        dep_svc: DependencyService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """A → B (dep), B not DONE → blocking."""
        node_b = _make_task_node(project_id, title="Prerequisite B")
        node_a = _make_task_node(project_id, title="Dependent A")
        repo.upsert_node(node_b)
        repo.upsert_node(node_a)
        _create_dependency_edge(repo, project_id, node_a.id, node_b.id)

        # B is not enqueued yet → blocking
        result = dep_svc.check_dependencies(node_a.id, project_id)
        assert result.all_satisfied is False
        assert len(result.blocking_tasks) == 1
        assert result.blocking_tasks[0].node_id == node_b.id
        assert result.blocking_tasks[0].title == "Prerequisite B"

    def test_dependency_done_satisfied(
        self,
        dep_svc: DependencyService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """A → B (dep), B is DONE → all_satisfied."""
        node_b = _make_task_node(project_id, title="Done B")
        node_a = _make_task_node(project_id, title="Dependent A")
        repo.upsert_node(node_b)
        repo.upsert_node(node_a)
        _create_dependency_edge(repo, project_id, node_a.id, node_b.id)

        # Enqueue B, checkout, complete
        repo.enqueue_task(project_id, node_b.id)
        task_b = repo.checkout_task(project_id, agent_id)
        assert task_b is not None
        repo.complete_task(task_b.id)

        result = dep_svc.check_dependencies(node_a.id, project_id)
        assert result.all_satisfied is True
        assert result.blocking_tasks == []

    def test_multiple_deps_partial_block(
        self,
        dep_svc: DependencyService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """A → B, A → C. B done, C not done → blocked by C."""
        node_b = _make_task_node(project_id, title="B-done")
        node_c = _make_task_node(project_id, title="C-pending")
        node_a = _make_task_node(project_id, title="A-multi-dep")
        for n in (node_b, node_c, node_a):
            repo.upsert_node(n)

        _create_dependency_edge(repo, project_id, node_a.id, node_b.id)
        _create_dependency_edge(repo, project_id, node_a.id, node_c.id)

        # Complete B only
        repo.enqueue_task(project_id, node_b.id)
        task_b = repo.checkout_task(project_id, agent_id)
        assert task_b is not None
        repo.complete_task(task_b.id)

        result = dep_svc.check_dependencies(node_a.id, project_id)
        assert result.all_satisfied is False
        assert len(result.blocking_tasks) == 1
        assert result.blocking_tasks[0].node_id == node_c.id

    def test_cycle_detection_simple(
        self,
        dep_svc: DependencyService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """A → B → A → cycle detected."""
        node_a = _make_task_node(project_id, title="Cycle-A")
        node_b = _make_task_node(project_id, title="Cycle-B")
        repo.upsert_node(node_a)
        repo.upsert_node(node_b)

        _create_dependency_edge(repo, project_id, node_a.id, node_b.id)
        _create_dependency_edge(repo, project_id, node_b.id, node_a.id)

        with pytest.raises(KgnError) as exc_info:
            dep_svc.check_dependencies(node_a.id, project_id)
        assert exc_info.value.code == KgnErrorCode.TASK_DEPENDENCY_CYCLE

    def test_cycle_detection_three_nodes(
        self,
        dep_svc: DependencyService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """A → B → C → A → cycle detected."""
        node_a = _make_task_node(project_id, title="Tri-A")
        node_b = _make_task_node(project_id, title="Tri-B")
        node_c = _make_task_node(project_id, title="Tri-C")
        for n in (node_a, node_b, node_c):
            repo.upsert_node(n)

        _create_dependency_edge(repo, project_id, node_a.id, node_b.id)
        _create_dependency_edge(repo, project_id, node_b.id, node_c.id)
        _create_dependency_edge(repo, project_id, node_c.id, node_a.id)

        with pytest.raises(KgnError) as exc_info:
            dep_svc.check_dependencies(node_a.id, project_id)
        assert exc_info.value.code == KgnErrorCode.TASK_DEPENDENCY_CYCLE


# ══════════════════════════════════════════════════════════════════════
#  DependencyService.unblock_dependents
# ══════════════════════════════════════════════════════════════════════


class TestUnblockDependents:
    def test_unblock_single_dependent(
        self,
        dep_svc: DependencyService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """B depends on A. A completes → B unblocked."""
        node_a = _make_task_node(project_id, title="Prereq-A")
        node_b = _make_task_node(project_id, title="Blocked-B")
        repo.upsert_node(node_a)
        repo.upsert_node(node_b)
        _create_dependency_edge(repo, project_id, node_b.id, node_a.id)

        # Enqueue B as BLOCKED
        repo.enqueue_task(project_id, node_b.id, state="BLOCKED")
        # Enqueue A as READY (completed later)
        repo.enqueue_task(project_id, node_a.id)

        # Mark A as DONE (simulate checkout + complete)
        task_a = repo.checkout_task(project_id, repo.get_or_create_agent(project_id, "bot"))
        assert task_a is not None
        repo.complete_task(task_a.id)

        # Unblock dependents of A
        unblocked = dep_svc.unblock_dependents(node_a.id, project_id)
        assert len(unblocked) == 1
        assert unblocked[0].node_title == "Blocked-B"

        # Verify B is now READY
        task_b = repo.get_task_by_node_id(node_b.id, project_id)
        assert task_b is not None
        assert task_b.state == "READY"

    def test_no_blocked_dependents(
        self,
        dep_svc: DependencyService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """No BLOCKED dependents → empty list."""
        node_a = _make_task_node(project_id, title="Solo-A")
        repo.upsert_node(node_a)
        repo.enqueue_task(project_id, node_a.id)

        unblocked = dep_svc.unblock_dependents(node_a.id, project_id)
        assert unblocked == []

    def test_partial_unblock_multiple_deps(
        self,
        dep_svc: DependencyService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """C depends on A and B. Only A completes → C stays BLOCKED."""
        node_a = _make_task_node(project_id, title="Dep-A")
        node_b = _make_task_node(project_id, title="Dep-B")
        node_c = _make_task_node(project_id, title="Blocked-C")
        for n in (node_a, node_b, node_c):
            repo.upsert_node(n)

        _create_dependency_edge(repo, project_id, node_c.id, node_a.id)
        _create_dependency_edge(repo, project_id, node_c.id, node_b.id)

        # Enqueue C as BLOCKED, A and B as READY
        repo.enqueue_task(project_id, node_c.id, state="BLOCKED")
        repo.enqueue_task(project_id, node_a.id)
        repo.enqueue_task(project_id, node_b.id)

        # Complete only A
        agent = repo.get_or_create_agent(project_id, "bot")
        task_a = repo.checkout_task(project_id, agent)
        assert task_a is not None
        repo.complete_task(task_a.id)

        # Unblock → C should NOT be unblocked (B still pending)
        unblocked = dep_svc.unblock_dependents(node_a.id, project_id)
        assert unblocked == []

        task_c = repo.get_task_by_node_id(node_c.id, project_id)
        assert task_c is not None
        assert task_c.state == "BLOCKED"

    def test_full_unblock_multiple_deps(
        self,
        dep_svc: DependencyService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """C depends on A and B. Both complete → C unblocked."""
        node_a = _make_task_node(project_id, title="FullDep-A")
        node_b = _make_task_node(project_id, title="FullDep-B")
        node_c = _make_task_node(project_id, title="FullBlocked-C")
        for n in (node_a, node_b, node_c):
            repo.upsert_node(n)

        _create_dependency_edge(repo, project_id, node_c.id, node_a.id)
        _create_dependency_edge(repo, project_id, node_c.id, node_b.id)

        # Enqueue
        repo.enqueue_task(project_id, node_c.id, state="BLOCKED")
        repo.enqueue_task(project_id, node_a.id)
        repo.enqueue_task(project_id, node_b.id)

        agent = repo.get_or_create_agent(project_id, "bot")

        # Complete A → C still blocked
        task_a = repo.checkout_task(project_id, agent)
        assert task_a is not None
        repo.complete_task(task_a.id)
        unblocked_a = dep_svc.unblock_dependents(node_a.id, project_id)
        assert unblocked_a == []

        # Complete B → C should now be unblocked
        task_b = repo.checkout_task(project_id, agent)
        assert task_b is not None
        repo.complete_task(task_b.id)
        unblocked_b = dep_svc.unblock_dependents(node_b.id, project_id)
        assert len(unblocked_b) == 1
        assert unblocked_b[0].node_title == "FullBlocked-C"

        task_c = repo.get_task_by_node_id(node_c.id, project_id)
        assert task_c is not None
        assert task_c.state == "READY"


# ══════════════════════════════════════════════════════════════════════
#  TaskService integration (enqueue + complete with deps)
# ══════════════════════════════════════════════════════════════════════


class TestTaskServiceDependencyIntegration:
    def test_enqueue_with_unmet_deps_blocked(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """Enqueue a task with unmet DEPENDS_ON → BLOCKED state."""
        node_a = _make_task_node(project_id, title="Prereq-A")
        node_b = _make_task_node(project_id, title="Dependent-B")
        repo.upsert_node(node_a)
        repo.upsert_node(node_b)
        _create_dependency_edge(repo, project_id, node_b.id, node_a.id)

        result = task_svc.enqueue(project_id, node_b.id)
        assert result.state == "BLOCKED"
        assert result.dependency_check.all_satisfied is False
        assert len(result.dependency_check.blocking_tasks) == 1

        # Verify in DB
        item = repo.get_task_status(result.task_queue_id)
        assert item is not None
        assert item.state == "BLOCKED"

    def test_enqueue_with_met_deps_ready(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Enqueue a task with all deps DONE → READY state."""
        node_a = _make_task_node(project_id, title="Done-A")
        node_b = _make_task_node(project_id, title="ReadyDep-B")
        repo.upsert_node(node_a)
        repo.upsert_node(node_b)
        _create_dependency_edge(repo, project_id, node_b.id, node_a.id)

        # Complete A first
        repo.enqueue_task(project_id, node_a.id)
        task_a = repo.checkout_task(project_id, agent_id)
        assert task_a is not None
        repo.complete_task(task_a.id)

        # Now enqueue B → should be READY
        result = task_svc.enqueue(project_id, node_b.id)
        assert result.state == "READY"
        assert result.dependency_check.all_satisfied is True

    def test_enqueue_no_deps_ready(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """Enqueue a task with no deps → READY."""
        node = _make_task_node(project_id, title="Independent")
        repo.upsert_node(node)

        result = task_svc.enqueue(project_id, node.id)
        assert result.state == "READY"
        assert result.dependency_check.all_satisfied is True

    def test_enqueue_cycle_raises(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """Enqueue with cycle → KGN-404."""
        node_a = _make_task_node(project_id, title="CycleEnq-A")
        node_b = _make_task_node(project_id, title="CycleEnq-B")
        repo.upsert_node(node_a)
        repo.upsert_node(node_b)
        _create_dependency_edge(repo, project_id, node_a.id, node_b.id)
        _create_dependency_edge(repo, project_id, node_b.id, node_a.id)

        with pytest.raises(KgnError) as exc_info:
            task_svc.enqueue(project_id, node_a.id)
        assert exc_info.value.code == KgnErrorCode.TASK_DEPENDENCY_CYCLE

    def test_complete_unblocks_dependent(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Complete A → auto-unblock B (B depends on A)."""
        node_a = _make_task_node(project_id, title="CompleteA")
        node_b = _make_task_node(project_id, title="BlockedB")
        repo.upsert_node(node_a)
        repo.upsert_node(node_b)
        _create_dependency_edge(repo, project_id, node_b.id, node_a.id)

        # Enqueue both
        task_svc.enqueue(project_id, node_a.id)
        result_b = task_svc.enqueue(project_id, node_b.id)
        assert result_b.state == "BLOCKED"

        # Checkout + complete A
        pkg_a = task_svc.checkout(project_id, agent_id)
        assert pkg_a is not None
        complete_result = task_svc.complete(pkg_a.task.id)

        # Should have unblocked B
        assert len(complete_result.unblocked_tasks) == 1
        assert complete_result.unblocked_tasks[0].node_title == "BlockedB"

        # B should now be READY
        item_b = repo.get_task_status(result_b.task_queue_id)
        assert item_b is not None
        assert item_b.state == "READY"

    def test_complete_no_dependents(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Complete with no dependents → empty unblocked list."""
        node = _make_task_node(project_id, title="Solo")
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)

        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None
        result = task_svc.complete(pkg.task.id)
        assert result.unblocked_tasks == []

    def test_diamond_dependency(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Diamond: D depends on B,C. B,C depend on A.

            A
           / \\
          B   C
           \\ /
            D

        Complete A → B,C unblocked.
        Complete B → D still blocked (C pending).
        Complete C → D unblocked.
        """
        node_a = _make_task_node(project_id, title="Diamond-A")
        node_b = _make_task_node(project_id, title="Diamond-B")
        node_c = _make_task_node(project_id, title="Diamond-C")
        node_d = _make_task_node(project_id, title="Diamond-D")
        for n in (node_a, node_b, node_c, node_d):
            repo.upsert_node(n)

        # B,C depend on A; D depends on B,C
        _create_dependency_edge(repo, project_id, node_b.id, node_a.id)
        _create_dependency_edge(repo, project_id, node_c.id, node_a.id)
        _create_dependency_edge(repo, project_id, node_d.id, node_b.id)
        _create_dependency_edge(repo, project_id, node_d.id, node_c.id)

        # Enqueue all
        task_svc.enqueue(project_id, node_a.id)  # READY (no deps)
        result_b = task_svc.enqueue(project_id, node_b.id)  # BLOCKED
        result_c = task_svc.enqueue(project_id, node_c.id)  # BLOCKED
        result_d = task_svc.enqueue(project_id, node_d.id)  # BLOCKED

        assert result_b.state == "BLOCKED"
        assert result_c.state == "BLOCKED"
        assert result_d.state == "BLOCKED"

        # Complete A → B,C should unblock
        pkg_a = task_svc.checkout(project_id, agent_id)
        assert pkg_a is not None
        cr_a = task_svc.complete(pkg_a.task.id)
        unblocked_titles = {ut.node_title for ut in cr_a.unblocked_tasks}
        assert "Diamond-B" in unblocked_titles
        assert "Diamond-C" in unblocked_titles
        assert "Diamond-D" not in unblocked_titles

        # Complete B → D should NOT unblock (C still pending)
        pkg_b = task_svc.checkout(project_id, agent_id)
        assert pkg_b is not None
        cr_b = task_svc.complete(pkg_b.task.id)
        assert len(cr_b.unblocked_tasks) == 0

        # Complete C → D should unblock
        pkg_c = task_svc.checkout(project_id, agent_id)
        assert pkg_c is not None
        cr_c = task_svc.complete(pkg_c.task.id)
        assert len(cr_c.unblocked_tasks) == 1
        assert cr_c.unblocked_tasks[0].node_title == "Diamond-D"

        # D should be READY now
        item_d = repo.get_task_status(result_d.task_queue_id)
        assert item_d is not None
        assert item_d.state == "READY"


# ══════════════════════════════════════════════════════════════════════
#  Repository helper methods
# ══════════════════════════════════════════════════════════════════════


class TestRepositoryDependencyMethods:
    def test_enqueue_with_blocked_state(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """enqueue_task(state='BLOCKED') creates a BLOCKED entry."""
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        qid = repo.enqueue_task(project_id, node.id, state="BLOCKED")
        item = repo.get_task_status(qid)
        assert item is not None
        assert item.state == "BLOCKED"

    def test_unblock_task(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """BLOCKED → READY transition."""
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        qid = repo.enqueue_task(project_id, node.id, state="BLOCKED")

        result = repo.unblock_task(qid)
        assert result is True

        item = repo.get_task_status(qid)
        assert item is not None
        assert item.state == "READY"

    def test_unblock_non_blocked_returns_false(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """unblock_task on READY task → False."""
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        qid = repo.enqueue_task(project_id, node.id)

        result = repo.unblock_task(qid)
        assert result is False

    def test_get_task_by_node_id(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """Find task queue entry by node ID."""
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        qid = repo.enqueue_task(project_id, node.id)

        found = repo.get_task_by_node_id(node.id, project_id)
        assert found is not None
        assert found.id == qid

    def test_get_task_by_node_id_not_found(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """No task for given node → None."""
        found = repo.get_task_by_node_id(uuid.uuid4(), project_id)
        assert found is None

    def test_find_blocked_dependents(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """Find BLOCKED tasks that depend on a given node."""
        node_a = _make_task_node(project_id, title="A")
        node_b = _make_task_node(project_id, title="B")
        repo.upsert_node(node_a)
        repo.upsert_node(node_b)
        _create_dependency_edge(repo, project_id, node_b.id, node_a.id)

        repo.enqueue_task(project_id, node_b.id, state="BLOCKED")

        deps = repo.find_blocked_dependents(node_a.id, project_id)
        assert len(deps) == 1
        assert deps[0].task_node_id == node_b.id

    def test_get_dependency_edges(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        """Get outgoing DEPENDS_ON edges."""
        node_a = _make_task_node(project_id, title="DepEdge-A")
        node_b = _make_task_node(project_id, title="DepEdge-B")
        repo.upsert_node(node_a)
        repo.upsert_node(node_b)
        _create_dependency_edge(repo, project_id, node_a.id, node_b.id)

        edges = repo.get_dependency_edges(node_a.id, project_id)
        assert len(edges) == 1
        assert edges[0].to_node_id == node_b.id
        assert edges[0].type == EdgeType.DEPENDS_ON
