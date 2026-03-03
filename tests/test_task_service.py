"""Integration tests for TaskService.

Requires a running PostgreSQL instance (Docker on port 5433).
Each test runs inside a SAVEPOINT that is rolled back at teardown.
"""

from __future__ import annotations

import random
import uuid

import pytest
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.graph.subgraph import SubgraphService
from kgn.models.edge import EdgeRecord
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.task.service import ContextPackage, TaskService
from tests.helpers import EMBEDDING_DIMS, MockEmbeddingClient

# ── Constants ──────────────────────────────────────────────────────────

DIM = EMBEDDING_DIMS

# ── Helpers ────────────────────────────────────────────────────────────


def _dummy_vector(seed: float = 0.5) -> list[float]:
    """Create a reproducible dummy embedding vector."""
    rng = random.Random(seed)
    vec = [rng.gauss(0, 0.1) for _ in range(DIM)]
    norm = sum(x * x for x in vec) ** 0.5
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def _make_task_node(
    project_id: uuid.UUID,
    *,
    title: str = "Implement feature",
    body: str = "## Context\n\nTask body",
    created_by: uuid.UUID | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.TASK,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md=body,
        content_hash=uuid.uuid4().hex,
        created_by=created_by,
    )


def _make_spec_node(
    project_id: uuid.UUID,
    *,
    title: str = "Spec node",
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.SPEC,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md="## Context\n\nSpec body",
        content_hash=uuid.uuid4().hex,
    )


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def subgraph_svc(repo: KgnRepository) -> SubgraphService:
    return SubgraphService(repo)


@pytest.fixture
def task_svc(repo: KgnRepository, subgraph_svc: SubgraphService) -> TaskService:
    """TaskService without embedding client."""
    return TaskService(repo, subgraph_svc)


@pytest.fixture
def task_svc_with_embed(
    repo: KgnRepository,
    subgraph_svc: SubgraphService,
) -> TaskService:
    """TaskService with a mock embedding client."""
    return TaskService(repo, subgraph_svc, embedding_client=MockEmbeddingClient())


# ── Enqueue ────────────────────────────────────────────────────────────


class TestTaskServiceEnqueue:
    def test_enqueue_returns_uuid(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        result = task_svc.enqueue(project_id, node.id)
        assert isinstance(result.task_queue_id, uuid.UUID)
        assert result.state == "READY"

    def test_enqueue_rejects_non_task(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_spec_node(project_id)
        repo.upsert_node(node)
        with pytest.raises(ValueError, match="expected TASK"):
            task_svc.enqueue(project_id, node.id)

    def test_enqueue_custom_priority(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        result = task_svc.enqueue(project_id, node.id, priority=10)
        item = repo.get_task_status(result.task_queue_id)
        assert item is not None
        assert item.priority == 10


# ── Checkout ───────────────────────────────────────────────────────────


class TestTaskServiceCheckout:
    def test_checkout_returns_context_package(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)

        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None
        assert isinstance(pkg, ContextPackage)
        assert pkg.task.state == "IN_PROGRESS"
        assert pkg.node.id == node.id
        assert pkg.node.type == NodeType.TASK
        assert pkg.subgraph.root_id == str(node.id)
        assert pkg.similar_nodes == []  # no embedding client

    def test_checkout_none_when_empty(
        self,
        task_svc: TaskService,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is None

    def test_checkout_includes_subgraph(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Checkout assembles subgraph with connected nodes."""
        task_node = _make_task_node(project_id, title="Main task")
        spec_node = _make_spec_node(project_id, title="Related spec")
        repo.upsert_node(task_node)
        repo.upsert_node(spec_node)
        repo.insert_edge(
            EdgeRecord(
                project_id=project_id,
                from_node_id=task_node.id,
                to_node_id=spec_node.id,
                type=EdgeType.DEPENDS_ON,
            )
        )
        task_svc.enqueue(project_id, task_node.id)

        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None
        # Subgraph should include both nodes
        subgraph_ids = {n.id for n in pkg.subgraph.nodes}
        assert task_node.id in subgraph_ids
        assert spec_node.id in subgraph_ids
        assert len(pkg.subgraph.edges) >= 1

    def test_checkout_with_embedding_returns_similar(
        self,
        task_svc_with_embed: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """When embedding client is present and node has embedding, similar_nodes is populated."""
        task_node = _make_task_node(project_id, title="Task with embedding")
        other_node = _make_spec_node(project_id, title="Similar spec")
        repo.upsert_node(task_node)
        repo.upsert_node(other_node)

        # Create embeddings for both
        vec_task = _dummy_vector(0.3)
        vec_other = _dummy_vector(0.31)  # close vector
        repo.upsert_embedding(task_node.id, project_id, vec_task, "text-embedding-3-small")
        repo.upsert_embedding(other_node.id, project_id, vec_other, "text-embedding-3-small")

        task_svc_with_embed.enqueue(project_id, task_node.id)
        pkg = task_svc_with_embed.checkout(project_id, agent_id)
        assert pkg is not None
        assert len(pkg.similar_nodes) >= 1
        # The task node itself should be excluded
        similar_ids = {s.id for s in pkg.similar_nodes}
        assert task_node.id not in similar_ids

    def test_checkout_no_embedding_still_works(
        self,
        task_svc_with_embed: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Even with embedding client, if node has no embedding, similar_nodes is empty."""
        task_node = _make_task_node(project_id, title="No embedding")
        repo.upsert_node(task_node)
        task_svc_with_embed.enqueue(project_id, task_node.id)

        pkg = task_svc_with_embed.checkout(project_id, agent_id)
        assert pkg is not None
        assert pkg.similar_nodes == []


# ── Complete / Fail ────────────────────────────────────────────────────


class TestTaskServiceComplete:
    def test_complete(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)
        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None

        task_svc.complete(pkg.task.id)
        status = repo.get_task_status(pkg.task.id)
        assert status is not None
        assert status.state == "DONE"

    def test_complete_non_in_progress_raises(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        result = task_svc.enqueue(project_id, node.id)
        with pytest.raises(ValueError, match="not IN_PROGRESS"):
            task_svc.complete(result.task_queue_id)


class TestTaskServiceFail:
    def test_fail(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)
        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None

        task_svc.fail(pkg.task.id, reason="something broke")
        status = repo.get_task_status(pkg.task.id)
        assert status is not None
        assert status.state == "FAILED"

    def test_fail_non_in_progress_raises(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        result = task_svc.enqueue(project_id, node.id)
        with pytest.raises(ValueError, match="not IN_PROGRESS"):
            task_svc.fail(result.task_queue_id)


# ── Requeue Expired ────────────────────────────────────────────────────


class TestTaskServiceRequeue:
    def test_requeue_expired(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)
        pkg = task_svc.checkout(project_id, agent_id, lease_duration_sec=1)
        assert pkg is not None

        # Force lease expiry
        db_conn.execute(
            "UPDATE task_queue SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
            (pkg.task.id,),
        )

        count = task_svc.requeue_expired(project_id)
        assert count == 1

        status = repo.get_task_status(pkg.task.id)
        assert status is not None
        assert status.state == "READY"

    def test_requeue_returns_zero_when_none(
        self,
        task_svc: TaskService,
        project_id: uuid.UUID,
    ) -> None:
        count = task_svc.requeue_expired(project_id)
        assert count == 0


# ── Activity Logging Tests ─────────────────────────────────────────────


class TestTaskActivityLogging:
    """Verify that TaskService automatically records agent_activities."""

    def test_checkout_records_activity(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id, title="Act-checkout")
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)

        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None

        activities = repo.get_task_activities(pkg.task.id)
        assert len(activities) == 1
        assert activities[0]["activity_type"] == "TASK_CHECKOUT"
        assert "Checked out" in activities[0]["message"]

    def test_complete_records_activity(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id, title="Act-complete")
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)

        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None
        task_svc.complete(pkg.task.id)

        activities = repo.get_task_activities(pkg.task.id)
        assert len(activities) == 2
        types = [a["activity_type"] for a in activities]
        assert types == ["TASK_CHECKOUT", "TASK_COMPLETED"]

    def test_fail_records_activity(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id, title="Act-fail")
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)

        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None
        task_svc.fail(pkg.task.id, reason="test failure reason")

        activities = repo.get_task_activities(pkg.task.id)
        assert len(activities) == 2
        types = [a["activity_type"] for a in activities]
        assert types == ["TASK_CHECKOUT", "TASK_FAILED"]
        assert activities[1]["message"] == "test failure reason"

    def test_fail_without_reason_uses_default_message(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id, title="Act-fail-default")
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)

        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None
        task_svc.fail(pkg.task.id)

        activities = repo.get_task_activities(pkg.task.id)
        fail_act = activities[-1]
        assert "failed" in fail_act["message"].lower()

    def test_activities_ordered_by_created_at(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id, title="Act-order")
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)

        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None
        task_svc.complete(pkg.task.id)

        activities = repo.get_task_activities(pkg.task.id)
        timestamps = [a["created_at"] for a in activities]
        assert timestamps == sorted(timestamps)

    def test_get_task_activities_empty(
        self,
        repo: KgnRepository,
    ) -> None:
        """No activities for a random task_queue_id."""
        activities = repo.get_task_activities(uuid.uuid4())
        assert activities == []

    def test_activity_includes_agent_key(
        self,
        task_svc: TaskService,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id, title="Act-agent")
        repo.upsert_node(node)
        task_svc.enqueue(project_id, node.id)

        pkg = task_svc.checkout(project_id, agent_id)
        assert pkg is not None

        activities = repo.get_task_activities(pkg.task.id)
        assert activities[0]["agent_key"] is not None
        assert len(activities[0]["agent_key"]) > 0
