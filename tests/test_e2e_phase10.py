"""End-to-End multi-agent orchestration tests (Phase 10 Step 8).

Six scenario classes validating the full multi-agent lifecycle:
1. TestMultiAgentWorkflow — 3+ agent collaboration via workflow engine
2. TestRoleBasedAccess — role-based create/checkout permissions
3. TestHandoffChain — genesis→worker→reviewer context propagation
4. TestConcurrentAccess — advisory locking on shared nodes
5. TestConflictResolution — conflict detect → review task creation
6. TestObservability — timeline + stats + bottleneck accuracy

Requires a running PostgreSQL instance (Docker).
"""

from __future__ import annotations

import uuid

import pytest
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.graph.health import HealthService
from kgn.graph.subgraph import SubgraphService
from kgn.models.enums import (
    NodeStatus,
    NodeType,
)
from kgn.models.node import NodeRecord
from kgn.orchestration.conflict_resolution import ConflictResolutionService
from kgn.orchestration.handoff import HandoffService
from kgn.orchestration.locking import NodeLockService
from kgn.orchestration.observability import ObservabilityService
from kgn.orchestration.roles import AgentRole, RoleGuard
from kgn.orchestration.templates import BUILTIN_TEMPLATES
from kgn.orchestration.workflow import WorkflowEngine
from kgn.task.service import TaskService

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    *,
    title: str = "E2E Node",
    node_type: NodeType = NodeType.SPEC,
    status: NodeStatus = NodeStatus.ACTIVE,
    body_md: str = "## Content\n\nE2E body.",
    created_by: uuid.UUID | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=status,
        title=title,
        body_md=body_md,
        content_hash=uuid.uuid4().hex,
        created_by=created_by,
    )


def _create_agent(
    repo: KgnRepository,
    project_id: uuid.UUID,
    key: str,
    role: str = "admin",
) -> uuid.UUID:
    agent_id = repo.get_or_create_agent(project_id, key)
    repo.set_agent_role(agent_id, role)
    return agent_id


def _build_task_service(
    repo: KgnRepository,
) -> TaskService:
    """Build a TaskService with all real dependencies (no embedding)."""
    subgraph = SubgraphService(repo)
    handoff = HandoffService(repo)
    return TaskService(
        repo=repo,
        subgraph_service=subgraph,
        handoff_service=handoff,
        embedding_client=None,
    )


# ══════════════════════════════════════════════════════════════════════
#  Scenario 1: Multi-Agent Workflow — 3+ agent collaboration
# ══════════════════════════════════════════════════════════════════════


class TestMultiAgentWorkflow:
    """3 agents (genesis, worker, reviewer) collaborate via workflow engine."""

    @pytest.fixture
    def env(self, db_conn: Connection, repo: KgnRepository, project_id: uuid.UUID):
        genesis = _create_agent(repo, project_id, "e2e-genesis", "genesis")
        worker = _create_agent(repo, project_id, "e2e-worker", "worker")
        reviewer = _create_agent(repo, project_id, "e2e-reviewer", "reviewer")

        task_svc = _build_task_service(repo)
        wf = WorkflowEngine(repo, task_svc)
        for t in BUILTIN_TEMPLATES:
            wf.register(t)

        return {
            "project_id": project_id,
            "genesis": genesis,
            "worker": worker,
            "reviewer": reviewer,
            "repo": repo,
            "task_svc": task_svc,
            "wf": wf,
        }

    def test_design_to_impl_creates_correct_nodes(self, env) -> None:
        """Workflow creates SPEC, ARCH, TASK(impl), TASK(review) from GOAL."""
        pid, genesis = env["project_id"], env["genesis"]
        repo, wf = env["repo"], env["wf"]

        goal = _make_node(
            pid,
            title="auth system setup",
            node_type=NodeType.GOAL,
            created_by=genesis,
        )
        repo.upsert_node(goal)

        result = wf.execute(goal.id, pid, genesis, "design-to-impl")

        assert len(result.created_nodes) == 4
        types = {n.node_type for n in result.created_nodes}
        assert NodeType.SPEC in types
        assert NodeType.ARCH in types
        assert NodeType.TASK in types  # impl + review

    def test_full_workflow_all_tasks_done(self, env) -> None:
        """Worker and reviewer complete all tasks → all DONE.

        design-to-impl creates:
          - SPEC, ARCH (non-TASK, not enqueued)
          - impl (TASK, role:worker, READY — depends on ARCH which is non-TASK)
          - review (TASK, role:reviewer, BLOCKED — depends on impl)
        """
        pid = env["project_id"]
        genesis, worker, reviewer = env["genesis"], env["worker"], env["reviewer"]
        repo, wf, task_svc = env["repo"], env["wf"], env["task_svc"]

        # 1. Genesis creates GOAL and runs workflow
        goal = _make_node(pid, title="Full Flow", node_type=NodeType.GOAL, created_by=genesis)
        repo.upsert_node(goal)
        result = wf.execute(goal.id, pid, genesis, "design-to-impl")
        assert len(result.created_nodes) == 4

        # 2. Worker handles impl (READY, role:worker)
        ctx = task_svc.checkout(pid, worker, role_filter="worker")
        assert ctx is not None
        task_svc.complete(ctx.task.id)

        # 3. Reviewer handles review (now unblocked, role:reviewer)
        ctx2 = task_svc.checkout(pid, reviewer, role_filter="reviewer")
        assert ctx2 is not None
        task_svc.complete(ctx2.task.id)

        # 4. No more tasks
        assert task_svc.checkout(pid, genesis) is None

    def test_workflow_graph_health(self, env) -> None:
        """After workflow, orphan rate ≤ 10%."""
        pid, genesis, repo, wf = env["project_id"], env["genesis"], env["repo"], env["wf"]

        goal = _make_node(pid, title="Health Test", node_type=NodeType.GOAL, created_by=genesis)
        repo.upsert_node(goal)
        wf.execute(goal.id, pid, genesis, "design-to-impl")

        health = HealthService(repo)
        report = health.compute(pid)
        # GOAL + 4 workflow nodes = 5. All connected via edges.
        assert report.orphan_rate <= 0.10


# ══════════════════════════════════════════════════════════════════════
#  Scenario 2: Role-Based Access — per-role allow/deny
# ══════════════════════════════════════════════════════════════════════


class TestRoleBasedAccess:
    """Role permissions are correctly enforced."""

    @pytest.fixture
    def env(self, repo: KgnRepository, project_id: uuid.UUID):
        genesis = _create_agent(repo, project_id, "rbac-genesis", "genesis")
        worker = _create_agent(repo, project_id, "rbac-worker", "worker")
        reviewer = _create_agent(repo, project_id, "rbac-reviewer", "reviewer")
        indexer = _create_agent(repo, project_id, "rbac-indexer", "indexer")

        task_svc = _build_task_service(repo)
        return {
            "project_id": project_id,
            "genesis": genesis,
            "worker": worker,
            "reviewer": reviewer,
            "indexer": indexer,
            "repo": repo,
            "task_svc": task_svc,
        }

    def test_genesis_can_create_goal(self, env) -> None:
        result = RoleGuard.can_create_node(AgentRole.GENESIS, NodeType.GOAL)
        assert result.allowed is True

    def test_worker_cannot_create_goal(self, env) -> None:
        result = RoleGuard.can_create_node(AgentRole.WORKER, NodeType.GOAL)
        assert result.allowed is False

    def test_reviewer_can_create_issue(self, env) -> None:
        result = RoleGuard.can_create_node(AgentRole.REVIEWER, NodeType.ISSUE)
        assert result.allowed is True

    def test_indexer_cannot_checkout(self, env) -> None:
        result = RoleGuard.can_checkout_task(AgentRole.INDEXER)
        assert result.allowed is False

    def test_worker_can_checkout(self, env) -> None:
        result = RoleGuard.can_checkout_task(AgentRole.WORKER)
        assert result.allowed is True

    def test_role_filter_restricts_checkout(self, env) -> None:
        """Worker cannot check out genesis-tagged tasks."""
        pid, genesis, worker = env["project_id"], env["genesis"], env["worker"]
        repo, task_svc = env["repo"], env["task_svc"]

        node = _make_node(pid, title="Genesis Only", node_type=NodeType.TASK, created_by=genesis)
        node = NodeRecord(
            id=node.id,
            project_id=pid,
            type=NodeType.TASK,
            status=NodeStatus.ACTIVE,
            title="Genesis Only",
            body_md="## Content\nBody.",
            content_hash=uuid.uuid4().hex,
            tags=["role:genesis"],
            created_by=genesis,
        )
        repo.upsert_node(node)
        repo.enqueue_task(pid, node.id)

        # Worker with role_filter="worker" → no matching tasks
        ctx = task_svc.checkout(pid, worker, role_filter="worker")
        assert ctx is None

        # Genesis with role_filter="genesis" → gets the task
        ctx2 = task_svc.checkout(pid, genesis, role_filter="genesis")
        assert ctx2 is not None


# ══════════════════════════════════════════════════════════════════════
#  Scenario 3: Handoff Chain — context propagation
# ══════════════════════════════════════════════════════════════════════


class TestHandoffChain:
    """Context flows from genesis → worker → reviewer via handoff."""

    @pytest.fixture
    def env(self, repo: KgnRepository, project_id: uuid.UUID):
        genesis = _create_agent(repo, project_id, "ho-genesis", "genesis")
        worker = _create_agent(repo, project_id, "ho-worker", "worker")
        reviewer = _create_agent(repo, project_id, "ho-reviewer", "reviewer")

        task_svc = _build_task_service(repo)
        wf = WorkflowEngine(repo, task_svc)
        for t in BUILTIN_TEMPLATES:
            wf.register(t)

        return {
            "project_id": project_id,
            "genesis": genesis,
            "worker": worker,
            "reviewer": reviewer,
            "repo": repo,
            "task_svc": task_svc,
            "wf": wf,
        }

    def test_handoff_context_injected(self, env) -> None:
        """Completed impl task injects context into dependent review task.

        design-to-impl: impl (TASK, READY) → review (TASK, BLOCKED).
        After impl completes, review is unblocked and receives handoff context.
        """
        pid, worker = env["project_id"], env["worker"]
        repo, wf, task_svc = env["repo"], env["wf"], env["task_svc"]
        genesis = env["genesis"]

        goal = _make_node(pid, title="Handoff Test", node_type=NodeType.GOAL, created_by=genesis)
        repo.upsert_node(goal)
        wf.execute(goal.id, pid, genesis, "design-to-impl")

        # Worker checks out impl (READY, role:worker)
        ctx = task_svc.checkout(pid, worker, role_filter="worker")
        assert ctx is not None
        task_svc.complete(ctx.task.id)

        # Review task should now have handoff context
        reviewer = env["reviewer"]
        ctx2 = task_svc.checkout(pid, reviewer, role_filter="reviewer")
        assert ctx2 is not None
        review_node = repo.get_node_by_id(ctx2.task.task_node_id)
        assert review_node is not None
        assert "Handoff Context" in review_node.body_md

    def test_full_chain_propagation(self, env) -> None:
        """Context propagates through worker→reviewer chain."""
        pid = env["project_id"]
        genesis, worker, reviewer = env["genesis"], env["worker"], env["reviewer"]
        repo, wf, task_svc = env["repo"], env["wf"], env["task_svc"]

        goal = _make_node(pid, title="Chain Prop", node_type=NodeType.GOAL, created_by=genesis)
        repo.upsert_node(goal)
        wf.execute(goal.id, pid, genesis, "design-to-impl")

        # impl (READY) → review (BLOCKED)
        # Worker completes impl → review unblocked → reviewer completes
        for role_f, agent in [
            ("worker", worker),
            ("reviewer", reviewer),
        ]:
            ctx = task_svc.checkout(pid, agent, role_filter=role_f)
            assert ctx is not None, f"No task for role_filter={role_f}"
            task_svc.complete(ctx.task.id)

        # Review task should have received handoff context from impl
        review_tasks = repo.list_tasks(pid, state="DONE")
        review_nodes = [
            repo.get_node_by_id(t.task_node_id)
            for t in review_tasks
            if "Review" in (repo.get_node_by_id(t.task_node_id) or _make_node(pid)).title
        ]
        assert any("Handoff Context" in (n.body_md or "") for n in review_nodes if n)


# ══════════════════════════════════════════════════════════════════════
#  Scenario 4: Concurrent Access — advisory locking
# ══════════════════════════════════════════════════════════════════════


class TestConcurrentAccess:
    """Advisory locks prevent concurrent node modification."""

    @pytest.fixture
    def env(self, repo: KgnRepository, project_id: uuid.UUID):
        agent_a = _create_agent(repo, project_id, "lock-a", "worker")
        agent_b = _create_agent(repo, project_id, "lock-b", "worker")
        locks = NodeLockService(repo)
        return {
            "project_id": project_id,
            "agent_a": agent_a,
            "agent_b": agent_b,
            "repo": repo,
            "locks": locks,
        }

    def test_lock_acquire_and_deny(self, env) -> None:
        """Agent A locks, Agent B denied."""
        pid, agent_a, agent_b = env["project_id"], env["agent_a"], env["agent_b"]
        repo, locks = env["repo"], env["locks"]

        node = _make_node(pid, title="Locked Node", created_by=agent_a)
        repo.upsert_node(node)

        result_a = locks.acquire(node.id, agent_a, duration_sec=300)
        assert result_a.acquired is True

        result_b = locks.acquire(node.id, agent_b, duration_sec=300)
        assert result_b.acquired is False

    def test_lock_release_then_acquire(self, env) -> None:
        """Agent A releases, Agent B can acquire."""
        pid, agent_a, agent_b = env["project_id"], env["agent_a"], env["agent_b"]
        repo, locks = env["repo"], env["locks"]

        node = _make_node(pid, title="Release Test", created_by=agent_a)
        repo.upsert_node(node)

        locks.acquire(node.id, agent_a, duration_sec=300)
        locks.release(node.id, agent_a)

        result_b = locks.acquire(node.id, agent_b, duration_sec=300)
        assert result_b.acquired is True

    def test_expired_lock_reacquirable(self, env) -> None:
        """Expired lock can be taken by another agent."""
        pid, agent_a, agent_b = env["project_id"], env["agent_a"], env["agent_b"]
        repo, locks = env["repo"], env["locks"]

        node = _make_node(pid, title="Expiry Test", created_by=agent_a)
        repo.upsert_node(node)

        # Lock, then manually expire it
        locks.acquire(node.id, agent_a, duration_sec=300)
        repo._conn.execute(
            "UPDATE nodes SET lock_expires_at = now() - interval '1 second' WHERE id = %s",
            (node.id,),
        )

        result_b = locks.acquire(node.id, agent_b, duration_sec=300)
        assert result_b.acquired is True

    def test_task_checkout_locks_node(self, env) -> None:
        """task_svc.checkout() acquires lock on the task node."""
        pid, agent_a = env["project_id"], env["agent_a"]
        repo, locks = env["repo"], env["locks"]

        task_svc = _build_task_service(repo)
        node = _make_node(pid, title="Auto Lock", node_type=NodeType.TASK, created_by=agent_a)
        repo.upsert_node(node)
        repo.enqueue_task(pid, node.id)

        ctx = task_svc.checkout(pid, agent_a)
        assert ctx is not None

        # Node should be locked by agent_a
        lock_info = locks.check(node.id)
        assert lock_info is not None
        assert lock_info.locked_by == agent_a


# ══════════════════════════════════════════════════════════════════════
#  Scenario 5: Conflict Resolution — detect → review → resolve
# ══════════════════════════════════════════════════════════════════════


class TestConflictResolution:
    """Conflict detection and review task creation E2E."""

    @pytest.fixture
    def env(self, repo: KgnRepository, project_id: uuid.UUID):
        agent_a = _create_agent(repo, project_id, "conflict-a", "worker")
        agent_b = _create_agent(repo, project_id, "conflict-b", "worker")
        crs = ConflictResolutionService(repo)
        return {
            "project_id": project_id,
            "agent_a": agent_a,
            "agent_b": agent_b,
            "repo": repo,
            "crs": crs,
        }

    def test_no_conflict_same_agent(self, env) -> None:
        """Same agent updating node → no conflict."""
        pid, agent_a = env["project_id"], env["agent_a"]
        repo, crs = env["repo"], env["crs"]

        node = _make_node(pid, title="No Conflict", created_by=agent_a)
        repo.upsert_node(node)
        repo._save_version(node)

        detection = crs.detect(node.id, agent_a)
        assert detection.detected is False

    def test_conflict_different_agents(self, env) -> None:
        """Different agent updating → conflict detected."""
        pid, agent_a, agent_b = env["project_id"], env["agent_a"], env["agent_b"]
        repo, crs = env["repo"], env["crs"]

        node = _make_node(pid, title="Conflict Target", created_by=agent_a)
        repo.upsert_node(node)
        repo._save_version(node)

        detection = crs.detect(node.id, agent_b)
        assert detection.detected is True
        assert detection.previous_agent == agent_a
        assert detection.current_agent == agent_b

    def test_conflict_creates_review_task(self, env) -> None:
        """Conflict creates ISSUE + TASK(review) and enqueues."""
        pid, agent_a, agent_b = env["project_id"], env["agent_a"], env["agent_b"]
        repo, crs = env["repo"], env["crs"]

        node = _make_node(pid, title="Review Needed", created_by=agent_a)
        repo.upsert_node(node)

        record = crs.create_review_task(pid, node.id, agent_a, agent_b)
        assert record.issue_node_id is not None
        assert record.review_task_node_id is not None

        # Review task should be enqueued
        tasks = repo.list_tasks(pid, state="READY")
        task_node_ids = {t.task_node_id for t in tasks}
        assert record.review_task_node_id in task_node_ids

    def test_review_task_tagged_reviewer(self, env) -> None:
        """Review task is tagged role:reviewer."""
        pid, agent_a, agent_b = env["project_id"], env["agent_a"], env["agent_b"]
        repo, crs = env["repo"], env["crs"]

        node = _make_node(pid, title="Tag Check", created_by=agent_a)
        repo.upsert_node(node)

        record = crs.create_review_task(pid, node.id, agent_a, agent_b)
        review_node = repo.get_node_by_id(record.review_task_node_id)
        assert review_node is not None
        assert "role:reviewer" in review_node.tags


# ══════════════════════════════════════════════════════════════════════
#  Scenario 6: Observability — stats, timeline, bottleneck
# ══════════════════════════════════════════════════════════════════════


class TestObservability:
    """Observability data accurately reflects agent activities."""

    @pytest.fixture
    def env(self, db_conn: Connection, repo: KgnRepository, project_id: uuid.UUID):
        agent_a = _create_agent(repo, project_id, "obs-a", "worker")
        agent_b = _create_agent(repo, project_id, "obs-b", "reviewer")
        task_svc = _build_task_service(repo)
        obs = ObservabilityService(repo)
        return {
            "project_id": project_id,
            "agent_a": agent_a,
            "agent_b": agent_b,
            "repo": repo,
            "task_svc": task_svc,
            "obs": obs,
            "db_conn": db_conn,
        }

    def test_checkout_logged_in_timeline(self, env) -> None:
        """task_svc.checkout logs TASK_CHECKOUT activity."""
        pid, agent_a = env["project_id"], env["agent_a"]
        repo, task_svc, obs = env["repo"], env["task_svc"], env["obs"]

        node = _make_node(pid, title="TL Test", node_type=NodeType.TASK, created_by=agent_a)
        repo.upsert_node(node)
        repo.enqueue_task(pid, node.id)

        ctx = task_svc.checkout(pid, agent_a)
        assert ctx is not None

        timeline = obs.get_agent_timeline(pid, agent_a)
        types = [e.activity_type for e in timeline]
        assert "TASK_CHECKOUT" in types

    def test_complete_logged_in_timeline(self, env) -> None:
        """task_svc.complete logs TASK_COMPLETED activity."""
        pid, agent_a = env["project_id"], env["agent_a"]
        repo, task_svc, obs = env["repo"], env["task_svc"], env["obs"]

        node = _make_node(pid, title="Complete TL", node_type=NodeType.TASK, created_by=agent_a)
        repo.upsert_node(node)
        repo.enqueue_task(pid, node.id)

        ctx = task_svc.checkout(pid, agent_a)
        task_svc.complete(ctx.task.id)

        timeline = obs.get_agent_timeline(pid, agent_a)
        types = [e.activity_type for e in timeline]
        assert "TASK_COMPLETED" in types

    def test_agent_stats_accuracy(self, env) -> None:
        """Agent stats accurately count done/failed tasks."""
        pid, agent_a = env["project_id"], env["agent_a"]
        repo, task_svc, obs = env["repo"], env["task_svc"], env["obs"]

        # Complete 2, fail 1
        for i in range(3):
            n = _make_node(pid, title=f"Stat {i}", node_type=NodeType.TASK, created_by=agent_a)
            repo.upsert_node(n)
            repo.enqueue_task(pid, n.id)
            ctx = task_svc.checkout(pid, agent_a)
            if i < 2:
                task_svc.complete(ctx.task.id)
            else:
                task_svc.fail(ctx.task.id, reason="test fail")

        stats = obs.get_agent_stats(pid)
        a_stat = [s for s in stats if s.agent_id == agent_a][0]
        assert a_stat.done_count == 2
        assert a_stat.failed_count == 1
        assert a_stat.total_tasks == 3

    def test_bottleneck_detection(self, env) -> None:
        """Slow task identified as bottleneck."""
        pid, agent_a = env["project_id"], env["agent_a"]
        repo, task_svc, obs = env["repo"], env["task_svc"], env["obs"]
        db_conn = env["db_conn"]

        # Create 4 fast tasks + 1 slow task
        for i in range(4):
            n = _make_node(pid, title=f"Fast {i}", node_type=NodeType.TASK, created_by=agent_a)
            repo.upsert_node(n)
            repo.enqueue_task(pid, n.id)
            ctx = task_svc.checkout(pid, agent_a)
            task_svc.complete(ctx.task.id)

        # Slow task — manipulate timestamps
        slow_node = _make_node(pid, title="Slow Task", node_type=NodeType.TASK, created_by=agent_a)
        repo.upsert_node(slow_node)
        tq_id = repo.enqueue_task(pid, slow_node.id)
        db_conn.execute(
            "UPDATE task_queue SET state = 'DONE', leased_by = %s, "
            "updated_at = created_at + interval '2 hours' WHERE id = %s",
            (agent_a, tq_id),
        )

        bottlenecks = obs.detect_bottlenecks(pid, percentile=0.8)
        slow = [b for b in bottlenecks if b.task_title == "Slow Task"]
        assert len(slow) == 1
        assert slow[0].duration_sec > 3600  # > 1 hour

    def test_report_aggregation(self, env) -> None:
        """get_report aggregates stats + activities correctly."""
        pid, agent_a = env["project_id"], env["agent_a"]
        repo, task_svc, obs = env["repo"], env["task_svc"], env["obs"]

        n = _make_node(pid, title="Report test", node_type=NodeType.TASK, created_by=agent_a)
        repo.upsert_node(n)
        repo.enqueue_task(pid, n.id)
        ctx = task_svc.checkout(pid, agent_a)
        task_svc.complete(ctx.task.id)

        report = obs.get_report(pid)
        assert report.total_agents >= 2  # agent_a + agent_b
        assert report.total_tasks_completed >= 1
        assert "TASK_CHECKOUT" in report.activity_summary
