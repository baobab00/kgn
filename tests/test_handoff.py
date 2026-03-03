"""Tests for Agent Handoff Protocol — HandoffService & MatchingService.

Covers:
- HandoffService.propagate_context(): context injection into dependent body_md
- Idempotency: duplicate injection prevention via markers
- HandoffService with missing nodes, no dependents
- MatchingService.find_candidates(): role-based agent matching
- MatchingService edge cases: no role tag, no agents, admin fallback
- TaskService.complete() integration with handoff propagation
- Role-filtered checkout: agent gets only role-matched tasks
- End-to-end: multi-stage handoff chain (genesis → worker → reviewer)
- MCP task_checkout role filtering

Target: 25+ tests
"""

from __future__ import annotations

import uuid

import pytest

from kgn.db.repository import KgnRepository
from kgn.graph.subgraph import SubgraphService
from kgn.models.enums import (
    EdgeType,
    NodeStatus,
    NodeType,
)
from kgn.models.node import NodeRecord
from kgn.orchestration.handoff import (
    HANDOFF_SECTION_HEADER,
    HandoffEntry,
    HandoffResult,
    HandoffService,
)
from kgn.orchestration.matching import (
    AgentCandidate,
    MatchingService,
    MatchResult,
)
from kgn.task.service import TaskService

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def subgraph_service(repo: KgnRepository) -> SubgraphService:
    return SubgraphService(repo)


@pytest.fixture
def task_service(repo: KgnRepository, subgraph_service: SubgraphService) -> TaskService:
    return TaskService(repo, subgraph_service)


@pytest.fixture
def handoff_service(repo: KgnRepository) -> HandoffService:
    return HandoffService(repo)


@pytest.fixture
def matching_service(repo: KgnRepository) -> MatchingService:
    return MatchingService(repo)


def _make_task_node(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    title: str = "Test Task",
    body_md: str = "## Content\n\nTask body.",
    tags: list[str] | None = None,
) -> NodeRecord:
    """Create a TASK node and return its record."""
    node = NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.TASK,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md=body_md,
        tags=tags or [],
        created_by=agent_id,
    )
    repo.upsert_node(node)
    return node


def _enqueue_task(
    repo: KgnRepository,
    task_service: TaskService,
    project_id: uuid.UUID,
    task_node: NodeRecord,
    *,
    priority: int = 100,
) -> uuid.UUID:
    """Enqueue a TASK node and return the queue ID."""
    result = task_service.enqueue(project_id, task_node.id, priority=priority)
    return result.task_queue_id


# ── HandoffService Tests ──────────────────────────────────────────────


class TestHandoffServiceBasic:
    """Basic HandoffService functionality."""

    def test_propagate_context_no_dependents(
        self,
        repo: KgnRepository,
        handoff_service: HandoffService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """When no dependents exist, propagate returns empty result."""
        node = _make_task_node(repo, project_id, agent_id, title="Standalone Task")
        result = handoff_service.propagate_context(node.id, project_id)
        assert result.count == 0
        assert result.completed_task_node_id == node.id

    def test_propagate_context_missing_node(
        self,
        handoff_service: HandoffService,
        project_id: uuid.UUID,
    ):
        """When the completed node doesn't exist, return empty."""
        fake_id = uuid.uuid4()
        result = handoff_service.propagate_context(fake_id, project_id)
        assert result.count == 0

    def test_propagate_injects_context_into_dependent(
        self,
        repo: KgnRepository,
        handoff_service: HandoffService,
        task_service: TaskService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Context from completed task is injected into dependent body."""
        # Create upstream task
        upstream = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Upstream Task",
            body_md="## Result\n\nUpstream completed work.",
        )

        # Create downstream task that depends on upstream
        downstream = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Downstream Task",
            body_md="## Content\n\nWaiting for upstream.",
        )

        # Create DEPENDS_ON edge: downstream → upstream
        from kgn.models.edge import EdgeRecord

        edge = EdgeRecord(
            project_id=project_id,
            from_node_id=downstream.id,
            to_node_id=upstream.id,
            type=EdgeType.DEPENDS_ON,
            created_by=agent_id,
        )
        repo.insert_edge(edge)

        # Enqueue both tasks — downstream will be BLOCKED
        _enqueue_task(repo, task_service, project_id, upstream)
        _enqueue_task(repo, task_service, project_id, downstream)

        # propagate_context should inject into downstream
        result = handoff_service.propagate_context(upstream.id, project_id)
        assert result.count == 1

        # Verify body_md was updated
        updated = repo.get_node_by_id(downstream.id)
        assert updated is not None
        assert HANDOFF_SECTION_HEADER in updated.body_md
        assert "Upstream Task" in updated.body_md
        assert f"<!-- handoff:{upstream.id} -->" in updated.body_md

    def test_propagate_idempotent(
        self,
        repo: KgnRepository,
        handoff_service: HandoffService,
        task_service: TaskService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Calling propagate twice does not duplicate handoff context."""
        upstream = _make_task_node(repo, project_id, agent_id, title="UP")
        downstream = _make_task_node(repo, project_id, agent_id, title="DOWN")

        from kgn.models.edge import EdgeRecord

        edge = EdgeRecord(
            project_id=project_id,
            from_node_id=downstream.id,
            to_node_id=upstream.id,
            type=EdgeType.DEPENDS_ON,
            created_by=agent_id,
        )
        repo.insert_edge(edge)
        _enqueue_task(repo, task_service, project_id, upstream)
        _enqueue_task(repo, task_service, project_id, downstream)

        # First propagation
        result1 = handoff_service.propagate_context(upstream.id, project_id)
        assert result1.count == 1

        # Second propagation — idempotent, no new entries
        result2 = handoff_service.propagate_context(upstream.id, project_id)
        assert result2.count == 0

        # Body should contain marker only once
        updated = repo.get_node_by_id(downstream.id)
        assert updated.body_md.count(f"<!-- handoff:{upstream.id} -->") == 1

    def test_propagate_multiple_dependents(
        self,
        repo: KgnRepository,
        handoff_service: HandoffService,
        task_service: TaskService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Context is propagated to all dependents."""
        upstream = _make_task_node(repo, project_id, agent_id, title="Upstream")
        down1 = _make_task_node(repo, project_id, agent_id, title="Down1")
        down2 = _make_task_node(repo, project_id, agent_id, title="Down2")

        from kgn.models.edge import EdgeRecord

        for down in [down1, down2]:
            edge = EdgeRecord(
                project_id=project_id,
                from_node_id=down.id,
                to_node_id=upstream.id,
                type=EdgeType.DEPENDS_ON,
                created_by=agent_id,
            )
            repo.insert_edge(edge)
            _enqueue_task(repo, task_service, project_id, down)

        _enqueue_task(repo, task_service, project_id, upstream)

        result = handoff_service.propagate_context(upstream.id, project_id)
        assert result.count == 2

    def test_propagate_body_truncation(
        self,
        repo: KgnRepository,
        handoff_service: HandoffService,
        task_service: TaskService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Long body_md from upstream is truncated in handoff context."""
        long_body = "x" * 3000
        upstream = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="LongBody",
            body_md=long_body,
        )
        downstream = _make_task_node(repo, project_id, agent_id, title="Down")

        from kgn.models.edge import EdgeRecord

        edge = EdgeRecord(
            project_id=project_id,
            from_node_id=downstream.id,
            to_node_id=upstream.id,
            type=EdgeType.DEPENDS_ON,
            created_by=agent_id,
        )
        repo.insert_edge(edge)
        _enqueue_task(repo, task_service, project_id, upstream)
        _enqueue_task(repo, task_service, project_id, downstream)

        handoff_service.propagate_context(upstream.id, project_id)

        updated = repo.get_node_by_id(downstream.id)
        assert "… (truncated)" in updated.body_md


class TestHandoffContextBlock:
    """Test _build_context_block and _append_context."""

    def test_build_context_block_structure(
        self,
        repo: KgnRepository,
        handoff_service: HandoffService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Context block includes title, type, status, node_id, body."""
        node = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="My Task",
            body_md="## Work\n\nDone.",
        )
        block = handoff_service._build_context_block(node)
        assert "### From: My Task" in block
        assert "**Type**: TASK" in block
        assert "**Status**: ACTIVE" in block
        assert str(node.id) in block
        assert "Done." in block

    def test_append_context_creates_section(self):
        """When no handoff section exists, it creates one."""
        existing = "## Content\n\nOriginal body."
        block = "### From: Upstream\n- info"
        marker = "<!-- handoff:test -->"
        result = HandoffService._append_context(existing, block, marker)
        assert HANDOFF_SECTION_HEADER in result
        assert "---" in result  # separator
        assert marker in result
        assert block in result
        assert existing in result

    def test_append_context_appends_to_existing_section(self):
        """When handoff section already exists, appends under it."""
        existing = (
            "## Content\n\nOriginal body.\n\n---\n\n"
            f"{HANDOFF_SECTION_HEADER}\n\n"
            "<!-- handoff:first -->\n### From: First\n- info"
        )
        block = "### From: Second\n- more info"
        marker = "<!-- handoff:second -->"
        result = HandoffService._append_context(existing, block, marker)
        assert result.count(HANDOFF_SECTION_HEADER) == 1  # not duplicated
        assert "<!-- handoff:first -->" in result
        assert "<!-- handoff:second -->" in result

    def test_append_context_empty_body(self):
        """Appending to empty body creates section without separator."""
        result = HandoffService._append_context("", "### info", "<!-- m -->")
        assert HANDOFF_SECTION_HEADER in result
        assert "---" not in result  # no separator for empty body


# ── MatchingService Tests ─────────────────────────────────────────────


class TestMatchingServiceBasic:
    """Basic MatchingService functionality."""

    def test_find_candidates_with_matching_role(
        self,
        repo: KgnRepository,
        matching_service: MatchingService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Agents with matching role are returned as candidates."""
        # Set agent role to worker
        repo.set_agent_role(agent_id, "worker")

        # Create task node with role:worker tag
        node = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Worker Task",
            tags=["workflow", "role:worker"],
        )

        result = matching_service.find_candidates(node.id, project_id)
        assert result.required_role == "worker"
        assert result.has_candidates
        # Find our worker agent
        worker_candidates = [c for c in result.candidates if c.role == "worker"]
        assert len(worker_candidates) >= 1

    def test_find_candidates_no_role_tag(
        self,
        repo: KgnRepository,
        matching_service: MatchingService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Tasks without role tag return empty result."""
        node = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Untagged Task",
            tags=["workflow"],
        )

        result = matching_service.find_candidates(node.id, project_id)
        assert result.required_role is None
        assert not result.has_candidates

    def test_find_candidates_missing_node(
        self,
        matching_service: MatchingService,
        project_id: uuid.UUID,
    ):
        """Non-existent node returns empty result."""
        fake_id = uuid.uuid4()
        result = matching_service.find_candidates(fake_id, project_id)
        assert result.required_role is None
        assert not result.has_candidates

    def test_admin_agents_are_always_candidates(
        self,
        repo: KgnRepository,
        matching_service: MatchingService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Admin agents appear as candidates for any role."""
        # Default role is admin
        repo.set_agent_role(agent_id, "admin")

        node = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Worker Task",
            tags=["role:worker"],
        )

        result = matching_service.find_candidates(node.id, project_id)
        assert result.required_role == "worker"
        admin_candidates = [c for c in result.candidates if c.role == "admin"]
        assert len(admin_candidates) >= 1

    def test_single_candidate_returns_single(
        self,
        repo: KgnRepository,
        matching_service: MatchingService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """When exactly one candidate, single_candidate property returns it."""
        # Create a fresh agent with indexer role — unique in project
        indexer_id = repo.get_or_create_agent(project_id, f"indexer-{uuid.uuid4().hex[:6]}")
        repo.set_agent_role(indexer_id, "indexer")

        # Set original agent to a different non-admin role
        repo.set_agent_role(agent_id, "worker")

        node = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Indexer Task",
            tags=["role:indexer"],
        )

        result = matching_service.find_candidates(node.id, project_id)
        assert result.required_role == "indexer"
        assert result.single_candidate is not None
        assert result.single_candidate.role == "indexer"

    def test_multiple_candidates_single_is_none(
        self,
        repo: KgnRepository,
        matching_service: MatchingService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """When multiple candidates, single_candidate returns None."""
        # Two worker agents
        repo.set_agent_role(agent_id, "worker")
        worker2_id = repo.get_or_create_agent(project_id, f"worker2-{uuid.uuid4().hex[:6]}")
        repo.set_agent_role(worker2_id, "worker")

        node = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Worker Task",
            tags=["role:worker"],
        )

        result = matching_service.find_candidates(node.id, project_id)
        assert result.required_role == "worker"
        assert len(result.candidates) >= 2
        assert result.single_candidate is None


class TestMatchingServiceRoleExtraction:
    """Test role extraction from tags."""

    def test_extract_role_basic(self):
        assert MatchingService.extract_role_from_node(["role:worker"]) == "worker"

    def test_extract_role_with_other_tags(self):
        assert (
            MatchingService.extract_role_from_node(["workflow", "role:reviewer", "v1"])
            == "reviewer"
        )

    def test_extract_role_no_role(self):
        assert MatchingService.extract_role_from_node(["workflow", "v1"]) is None

    def test_extract_role_empty_tags(self):
        assert MatchingService.extract_role_from_node([]) is None

    def test_extract_role_first_role_wins(self):
        """When multiple role: tags, the first one wins."""
        assert MatchingService.extract_role_from_node(["role:worker", "role:reviewer"]) == "worker"


# ── TaskService Integration Tests ────────────────────────────────────


class TestTaskServiceHandoffIntegration:
    """Test that TaskService.complete() propagates handoff context."""

    def test_complete_propagates_handoff(
        self,
        repo: KgnRepository,
        task_service: TaskService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """TaskService.complete() injects handoff context into dependents."""
        # Create upstream task → enqueue → checkout → complete
        upstream = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Design Task",
            body_md="## Result\n\nDesign completed.",
        )
        downstream = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Impl Task",
            body_md="## Content\n\nImplement the design.",
        )

        from kgn.models.edge import EdgeRecord

        edge = EdgeRecord(
            project_id=project_id,
            from_node_id=downstream.id,
            to_node_id=upstream.id,
            type=EdgeType.DEPENDS_ON,
            created_by=agent_id,
        )
        repo.insert_edge(edge)

        _enqueue_task(repo, task_service, project_id, upstream)
        _enqueue_task(repo, task_service, project_id, downstream)

        # Checkout and complete upstream
        pkg = task_service.checkout(project_id, agent_id)
        assert pkg is not None
        task_service.complete(pkg.task.id)

        # Verify downstream body_md has handoff context
        updated_downstream = repo.get_node_by_id(downstream.id)
        assert HANDOFF_SECTION_HEADER in updated_downstream.body_md
        assert "Design Task" in updated_downstream.body_md

    def test_complete_without_dependents_no_error(
        self,
        repo: KgnRepository,
        task_service: TaskService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Complete works fine even when there are no dependents."""
        node = _make_task_node(repo, project_id, agent_id, title="Standalone")
        _enqueue_task(repo, task_service, project_id, node)

        pkg = task_service.checkout(project_id, agent_id)
        assert pkg is not None
        result = task_service.complete(pkg.task.id)
        assert result.unblocked_tasks == []


# ── Role-Filtered Checkout Tests ──────────────────────────────────────


class TestRoleFilteredCheckout:
    """Test role-based filtering in task checkout."""

    def test_checkout_with_role_filter(
        self,
        repo: KgnRepository,
        task_service: TaskService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Role filter returns only matching tasks."""
        # Create worker task and reviewer task
        worker_task = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Worker Task",
            tags=["workflow", "role:worker"],
        )
        reviewer_task = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Reviewer Task",
            tags=["workflow", "role:reviewer"],
        )

        _enqueue_task(repo, task_service, project_id, worker_task)
        _enqueue_task(repo, task_service, project_id, reviewer_task)

        # Checkout with worker filter should get worker task
        pkg = task_service.checkout(project_id, agent_id, role_filter="worker")
        assert pkg is not None
        assert pkg.node.title == "Worker Task"

    def test_checkout_role_filter_no_match(
        self,
        repo: KgnRepository,
        task_service: TaskService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Role filter with no matching tasks returns None."""
        worker_task = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Worker Task",
            tags=["workflow", "role:worker"],
        )
        _enqueue_task(repo, task_service, project_id, worker_task)

        # No indexer tasks
        pkg = task_service.checkout(project_id, agent_id, role_filter="indexer")
        assert pkg is None

    def test_checkout_no_role_filter_gets_any(
        self,
        repo: KgnRepository,
        task_service: TaskService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Without role filter, any READY task is returned."""
        task = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Any Task",
            tags=["role:reviewer"],
        )
        _enqueue_task(repo, task_service, project_id, task)

        pkg = task_service.checkout(project_id, agent_id, role_filter=None)
        assert pkg is not None
        assert pkg.node.title == "Any Task"

    def test_checkout_admin_no_filter(
        self,
        repo: KgnRepository,
        task_service: TaskService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Admin agents can checkout any task (no filter applied)."""
        task = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Worker Task",
            tags=["role:worker"],
        )
        _enqueue_task(repo, task_service, project_id, task)

        # No role filter = admin gets any task
        pkg = task_service.checkout(project_id, agent_id, role_filter=None)
        assert pkg is not None


# ── End-to-End Handoff Chain Tests ───────────────────────────────────


class TestHandoffChain:
    """End-to-end tests for multi-stage handoff chains."""

    def test_three_stage_handoff_chain(
        self,
        repo: KgnRepository,
        task_service: TaskService,
        handoff_service: HandoffService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """3-stage chain: A → B → C. Completing A injects into B.
        Completing B injects A+B context into C."""
        task_a = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Stage A",
            body_md="## Stage A\n\nFirst stage work.",
        )
        task_b = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Stage B",
            body_md="## Stage B\n\nSecond stage work.",
        )
        task_c = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Stage C",
            body_md="## Stage C\n\nThird stage work.",
        )

        from kgn.models.edge import EdgeRecord

        # B depends on A
        repo.insert_edge(
            EdgeRecord(
                project_id=project_id,
                from_node_id=task_b.id,
                to_node_id=task_a.id,
                type=EdgeType.DEPENDS_ON,
                created_by=agent_id,
            )
        )
        # C depends on B
        repo.insert_edge(
            EdgeRecord(
                project_id=project_id,
                from_node_id=task_c.id,
                to_node_id=task_b.id,
                type=EdgeType.DEPENDS_ON,
                created_by=agent_id,
            )
        )

        # Enqueue all
        _enqueue_task(repo, task_service, project_id, task_a)
        _enqueue_task(repo, task_service, project_id, task_b)
        _enqueue_task(repo, task_service, project_id, task_c)

        # Complete A → should inject context into B
        pkg_a = task_service.checkout(project_id, agent_id)
        assert pkg_a is not None
        task_service.complete(pkg_a.task.id)

        # Verify B has A's handoff context
        updated_b = repo.get_node_by_id(task_b.id)
        assert "Stage A" in updated_b.body_md
        assert HANDOFF_SECTION_HEADER in updated_b.body_md

        # Complete B → should inject context into C
        pkg_b = task_service.checkout(project_id, agent_id)
        assert pkg_b is not None
        assert pkg_b.node.id == task_b.id
        task_service.complete(pkg_b.task.id)

        # Verify C has B's handoff context
        updated_c = repo.get_node_by_id(task_c.id)
        assert "Stage B" in updated_c.body_md
        assert HANDOFF_SECTION_HEADER in updated_c.body_md

    def test_handoff_with_role_tagged_tasks(
        self,
        repo: KgnRepository,
        task_service: TaskService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Role-tagged tasks get proper handoff and role-filtered checkout."""
        genesis_task = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Genesis Design",
            body_md="## Design\n\nArchitecture plan.",
            tags=["workflow", "role:genesis"],
        )
        worker_task = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="Worker Impl",
            body_md="## Impl\n\nWaiting for design.",
            tags=["workflow", "role:worker"],
        )

        from kgn.models.edge import EdgeRecord

        repo.insert_edge(
            EdgeRecord(
                project_id=project_id,
                from_node_id=worker_task.id,
                to_node_id=genesis_task.id,
                type=EdgeType.DEPENDS_ON,
                created_by=agent_id,
            )
        )

        _enqueue_task(repo, task_service, project_id, genesis_task)
        _enqueue_task(repo, task_service, project_id, worker_task)

        # Genesis checkout
        pkg = task_service.checkout(project_id, agent_id, role_filter="genesis")
        assert pkg is not None
        assert pkg.node.title == "Genesis Design"
        task_service.complete(pkg.task.id)

        # Worker checkout (should get worker task)
        pkg2 = task_service.checkout(project_id, agent_id, role_filter="worker")
        assert pkg2 is not None
        assert pkg2.node.title == "Worker Impl"

        # Worker task should have genesis context
        assert "Genesis Design" in pkg2.node.body_md


# ── Data class / Result type tests ───────────────────────────────────


class TestDataClasses:
    """Test HandoffResult and MatchResult data classes."""

    def test_handoff_result_count(self):
        result = HandoffResult(completed_task_node_id=uuid.uuid4())
        assert result.count == 0
        result.entries.append(
            HandoffEntry(
                dependent_task_node_id=uuid.uuid4(),
                dependent_title="dep",
                from_task_node_id=uuid.uuid4(),
                from_title="from",
            )
        )
        assert result.count == 1

    def test_match_result_properties(self):
        result = MatchResult(task_node_id=uuid.uuid4(), required_role="worker")
        assert not result.has_candidates
        assert result.single_candidate is None

        c1 = AgentCandidate(agent_id=uuid.uuid4(), agent_key="agent1", role="worker")
        result.candidates.append(c1)
        assert result.has_candidates
        assert result.single_candidate is c1

        c2 = AgentCandidate(agent_id=uuid.uuid4(), agent_key="agent2", role="worker")
        result.candidates.append(c2)
        assert result.single_candidate is None  # multiple


# ── MCP task_checkout role-filtering test ────────────────────────────


class TestMCPCheckoutRoleFiltering:
    """Test MCP-level role filtering in task_checkout."""

    def test_mcp_checkout_worker_gets_filtered(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Role filtering via TaskService (same path MCP uses)."""
        # Create worker and reviewer tasks
        worker_task = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="MCP Worker Task",
            tags=["workflow", "role:worker"],
        )
        reviewer_task = _make_task_node(
            repo,
            project_id,
            agent_id,
            title="MCP Reviewer Task",
            tags=["workflow", "role:reviewer"],
        )

        svc = TaskService(repo, SubgraphService(repo))
        _enqueue_task(repo, svc, project_id, worker_task)
        _enqueue_task(repo, svc, project_id, reviewer_task)

        # MCP passes role_filter for non-admin agents
        pkg = svc.checkout(project_id, agent_id, role_filter="worker")
        assert pkg is not None
        assert pkg.node.title == "MCP Worker Task"

        # Reviewer task still available
        pkg2 = svc.checkout(project_id, agent_id, role_filter="reviewer")
        assert pkg2 is not None
        assert pkg2.node.title == "MCP Reviewer Task"


class TestRepositoryFindReadyDependents:
    """Test the public find_ready_dependents repository method."""

    def test_find_ready_dependents_returns_ready_tasks(
        self,
        repo: KgnRepository,
        task_service: TaskService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """find_ready_dependents finds READY tasks that depend on a node."""
        upstream = _make_task_node(repo, project_id, agent_id, title="UP")
        downstream = _make_task_node(repo, project_id, agent_id, title="DOWN")

        from kgn.models.edge import EdgeRecord

        repo.insert_edge(
            EdgeRecord(
                project_id=project_id,
                from_node_id=downstream.id,
                to_node_id=upstream.id,
                type=EdgeType.DEPENDS_ON,
                created_by=agent_id,
            )
        )

        # Enqueue downstream as READY (bypassing dependency check for test)
        repo.enqueue_task(project_id, downstream.id, priority=100, state="READY")
        repo.enqueue_task(project_id, upstream.id, priority=100, state="READY")

        dependents = repo.find_ready_dependents(upstream.id, project_id)
        assert len(dependents) == 1
        assert dependents[0].task_node_id == downstream.id

    def test_find_ready_dependents_excludes_blocked(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """BLOCKED tasks are not in find_ready_dependents results."""
        upstream = _make_task_node(repo, project_id, agent_id, title="UP")
        downstream = _make_task_node(repo, project_id, agent_id, title="DOWN")

        from kgn.models.edge import EdgeRecord

        repo.insert_edge(
            EdgeRecord(
                project_id=project_id,
                from_node_id=downstream.id,
                to_node_id=upstream.id,
                type=EdgeType.DEPENDS_ON,
                created_by=agent_id,
            )
        )

        # Enqueue downstream as BLOCKED
        repo.enqueue_task(project_id, downstream.id, priority=100, state="BLOCKED")

        dependents = repo.find_ready_dependents(upstream.id, project_id)
        assert len(dependents) == 0
