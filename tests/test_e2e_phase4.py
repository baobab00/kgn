"""End-to-End integration tests for MCP tools (Phase 4 Step 8).

Scenarios:
1. MCP read tool full flow:
   ingest(nodes+edges) → get_node → query_nodes → get_subgraph → query_similar(Mock)
2. MCP task flow:
   ingest TASK node → enqueue_task → task_checkout → task_complete
3. MCP write tool:
   ingest_node → ingest_edge → verify with get_node
4. requeue_expired automation:
   enqueue → checkout(lease 0s) → task_checkout(new agent) → auto requeue + re-checkout

Requires a running PostgreSQL instance (Docker).
"""

from __future__ import annotations

import json
import uuid

import pytest
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.mcp.server import create_server
from kgn.models.edge import EdgeRecord
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord
from tests.helpers import call_tool as _call_tool
from tests.helpers import make_kge as _make_kge
from tests.helpers import make_kgn as _make_kgn

# ── Constants ──────────────────────────────────────────────────────────

EMBED_DIMS = 1536


# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    *,
    node_type: NodeType = NodeType.SPEC,
    status: NodeStatus = NodeStatus.ACTIVE,
    title: str = "E2E Node",
    body_md: str = "## Content\n\nE2E body.",
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=status,
        title=title,
        body_md=body_md,
        content_hash=uuid.uuid4().hex,
    )


# ══════════════════════════════════════════════════════════════════════
#  Scenario 1: MCP read tool full flow
# ══════════════════════════════════════════════════════════════════════


class TestE2EReadFlow:
    """ingest(nodes+edges) → get_node → query_nodes → get_subgraph → query_similar."""

    @pytest.fixture
    def setup(self, db_conn: Connection, repo: KgnRepository):
        """Create project, ingest 2 nodes + 1 edge via service, return context."""
        project_name = f"e2e-read-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)

        node_a = _make_node(pid, title="Node Alpha", node_type=NodeType.GOAL)
        node_b = _make_node(pid, title="Node Beta", node_type=NodeType.SPEC)
        repo.upsert_node(node_a)
        repo.upsert_node(node_b)

        agent_id = repo.get_or_create_agent(pid, "e2e-agent")
        edge = EdgeRecord(
            project_id=pid,
            from_node_id=node_a.id,
            to_node_id=node_b.id,
            type=EdgeType.IMPLEMENTS,
            note="e2e test edge",
            created_by=agent_id,
        )
        repo.insert_edge(edge)

        server = create_server(project_name, conn=db_conn)
        return server, project_name, pid, node_a, node_b

    def test_get_node_returns_ingested_node(self, setup) -> None:
        server, _, _, node_a, _ = setup
        result = _call_tool(server, "get_node", node_id=str(node_a.id))
        data = json.loads(result)

        assert data["id"] == str(node_a.id)
        assert data["title"] == "Node Alpha"
        assert data["type"] == "GOAL"

    def test_query_nodes_by_type(self, setup) -> None:
        server, project_name, _, _, _ = setup
        result = _call_tool(server, "query_nodes", project=project_name, type="SPEC")
        data = json.loads(result)

        assert isinstance(data, list)
        assert len(data) >= 1
        assert all(n["type"] == "SPEC" for n in data)

    def test_query_nodes_all(self, setup) -> None:
        server, project_name, _, _, _ = setup
        result = _call_tool(server, "query_nodes", project=project_name)
        data = json.loads(result)

        assert len(data) >= 2
        titles = {n["title"] for n in data}
        assert "Node Alpha" in titles
        assert "Node Beta" in titles

    def test_get_subgraph_from_goal(self, setup) -> None:
        server, _, _, node_a, node_b = setup
        result = _call_tool(server, "get_subgraph", node_id=str(node_a.id), depth=2)
        data = json.loads(result)

        assert data["root_id"] == str(node_a.id)
        assert data["depth"] == 2
        node_ids = {n["id"] for n in data["nodes"]}
        assert str(node_a.id) in node_ids
        assert str(node_b.id) in node_ids
        assert len(data["edges"]) >= 1

    def test_query_similar_with_mock_embedding(
        self, db_conn: Connection, repo: KgnRepository
    ) -> None:
        """query_similar with pre-stored embeddings returns results."""
        project_name = f"e2e-sim-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)

        # Create two nodes
        node_a = _make_node(pid, title="Similarity Source")
        node_b = _make_node(pid, title="Similar Target")
        repo.upsert_node(node_a)
        repo.upsert_node(node_b)

        # Store mock embeddings (close vectors → high similarity)
        vec_a = [0.5] * EMBED_DIMS
        vec_b = [0.5 + 0.001] * EMBED_DIMS
        repo.upsert_embedding(node_a.id, pid, vec_a, "mock-model")
        repo.upsert_embedding(node_b.id, pid, vec_b, "mock-model")

        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "query_similar", node_id=str(node_a.id), top_k=5)
        data = json.loads(result)

        assert isinstance(data, list)
        assert len(data) >= 1
        # node_b should appear as similar
        similar_ids = {s["id"] for s in data}
        assert str(node_b.id) in similar_ids
        assert data[0]["similarity"] > 0.9

    def test_query_similar_no_embedding(self, setup) -> None:
        """query_similar without embeddings returns empty list."""
        server, _, _, node_a, _ = setup
        result = _call_tool(server, "query_similar", node_id=str(node_a.id), top_k=5)
        data = json.loads(result)
        assert data == []


# ══════════════════════════════════════════════════════════════════════
#  Scenario 2: MCP task flow
# ══════════════════════════════════════════════════════════════════════


class TestE2ETaskFlow:
    """ingest TASK node → enqueue_task → task_checkout → task_complete."""

    def test_full_task_lifecycle(self, db_conn: Connection, repo: KgnRepository) -> None:
        """TASK ingest → enqueue → checkout → complete full cycle."""
        project_name = f"e2e-task-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        # 1. ingest TASK node via MCP tool
        task_kgn = _make_kgn(
            node_id="new:e2e-task-001",
            node_type="TASK",
            title="Fix Critical Bug",
            project_id=project_name,
            body="## Content\n\nFix the critical bug in module X.",
        )
        ingest_result = _call_tool(server, "ingest_node", kgn_content=task_kgn)
        ingest_data = json.loads(ingest_result)
        assert ingest_data["status"] == "ok"
        task_node_id = ingest_data["node_id"]

        # 2. enqueue_task via MCP tool
        enqueue_result = _call_tool(server, "enqueue_task", task_node_id=task_node_id, priority=50)
        enqueue_data = json.loads(enqueue_result)
        assert enqueue_data["status"] == "READY"
        assert "task_queue_id" in enqueue_data

        # 3. task_checkout via MCP tool
        checkout_result = _call_tool(
            server, "task_checkout", project=project_name, agent="e2e-agent"
        )
        checkout_data = json.loads(checkout_result)
        assert "task" in checkout_data
        assert checkout_data["task"]["node_id"] == task_node_id
        assert checkout_data["node"]["title"] == "Fix Critical Bug"
        task_queue_id = checkout_data["task"]["id"]

        # 4. task_complete via MCP tool
        complete_result = _call_tool(server, "task_complete", task_id=task_queue_id)
        complete_data = json.loads(complete_result)
        assert complete_data["status"] == "ok"
        assert "completed" in complete_data["message"]

        # 5. Verify no more tasks in queue
        empty_result = _call_tool(server, "task_checkout", project=project_name, agent="e2e-agent")
        empty_data = json.loads(empty_result)
        assert empty_data["status"] == "empty"

    def test_task_fail_lifecycle(self, db_conn: Connection, repo: KgnRepository) -> None:
        """TASK ingest → enqueue → checkout → fail cycle."""
        project_name = f"e2e-task-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        # Ingest + enqueue
        task_kgn = _make_kgn(
            node_id="new:e2e-task-fail",
            node_type="TASK",
            title="Risky Migration",
            project_id=project_name,
            body="## Content\n\nRun the risky DB migration.",
        )
        ingest_data = json.loads(_call_tool(server, "ingest_node", kgn_content=task_kgn))
        task_node_id = ingest_data["node_id"]
        _call_tool(server, "enqueue_task", task_node_id=task_node_id)

        # Checkout
        checkout_data = json.loads(
            _call_tool(server, "task_checkout", project=project_name, agent="e2e-agent")
        )
        task_queue_id = checkout_data["task"]["id"]

        # Fail
        fail_result = _call_tool(
            server, "task_fail", task_id=task_queue_id, reason="Migration error"
        )
        fail_data = json.loads(fail_result)
        assert fail_data["status"] == "ok"
        assert fail_data["reason"] == "Migration error"


# ══════════════════════════════════════════════════════════════════════
#  Scenario 3: MCP write tool
# ══════════════════════════════════════════════════════════════════════


class TestE2EWriteFlow:
    """ingest_node → ingest_edge → verify with get_node."""

    def test_ingest_node_then_edge_then_verify(
        self, db_conn: Connection, repo: KgnRepository
    ) -> None:
        """Ingest 2 nodes → ingest edge → verify with get_node + get_subgraph."""
        project_name = f"e2e-write-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        # 1. Ingest node A
        node_a_id = str(uuid.uuid4())
        kgn_a = _make_kgn(
            node_id=node_a_id,
            node_type="GOAL",
            title="Write Flow Goal",
            project_id=project_name,
            body="## Content\n\nGoal for write flow test.",
        )
        result_a = json.loads(_call_tool(server, "ingest_node", kgn_content=kgn_a))
        assert result_a["status"] == "ok"
        assert result_a["node_id"] == node_a_id

        # 2. Ingest node B
        node_b_id = str(uuid.uuid4())
        kgn_b = _make_kgn(
            node_id=node_b_id,
            node_type="SPEC",
            title="Write Flow Spec",
            project_id=project_name,
            body="## Content\n\nSpec implementing the goal.",
        )
        result_b = json.loads(_call_tool(server, "ingest_node", kgn_content=kgn_b))
        assert result_b["status"] == "ok"

        # 3. Ingest edge A → B
        kge = _make_kge(
            from_id=node_a_id,
            to_id=node_b_id,
            edge_type="IMPLEMENTS",
            project_id=project_name,
        )
        edge_result = json.loads(_call_tool(server, "ingest_edge", kge_content=kge))
        assert edge_result["status"] == "ok"
        assert edge_result["edge_count"] == 1

        # 4. Verify nodes with get_node
        verify_a = json.loads(_call_tool(server, "get_node", node_id=node_a_id))
        assert verify_a["title"] == "Write Flow Goal"
        assert verify_a["type"] == "GOAL"

        verify_b = json.loads(_call_tool(server, "get_node", node_id=node_b_id))
        assert verify_b["title"] == "Write Flow Spec"
        assert verify_b["type"] == "SPEC"

        # 5. Verify subgraph shows edge
        sg = json.loads(_call_tool(server, "get_subgraph", node_id=node_a_id, depth=1))
        sg_node_ids = {n["id"] for n in sg["nodes"]}
        assert node_a_id in sg_node_ids
        assert node_b_id in sg_node_ids
        assert len(sg["edges"]) >= 1

    def test_ingest_invalid_kgn_returns_error(
        self, db_conn: Connection, repo: KgnRepository
    ) -> None:
        """Invalid .kgn content ingest returns error."""
        project_name = f"e2e-write-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        result = json.loads(_call_tool(server, "ingest_node", kgn_content="not valid kgn"))
        assert "error" in result


# ══════════════════════════════════════════════════════════════════════
#  Scenario 4: requeue_expired automation
# ══════════════════════════════════════════════════════════════════════


class TestE2ERequeueExpired:
    """enqueue → checkout(lease 0s) → task_checkout(new agent) → requeue + re-checkout."""

    def test_expired_task_auto_requeued_on_checkout(
        self, db_conn: Connection, repo: KgnRepository
    ) -> None:
        """
        1. Create TASK node + enqueue
        2. First agent checkout (direct repo)
        3. Force-expire the lease
        4. MCP task_checkout call → requeue_expired auto-runs → re-checkout succeeds
        """
        project_name = f"e2e-requeue-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)

        # 1. Create TASK node + enqueue
        task_node = _make_node(pid, title="Expiring Task", node_type=NodeType.TASK)
        repo.upsert_node(task_node)
        repo.enqueue_task(pid, task_node.id)

        # 2. First agent checks out (directly via repo to control lease)
        first_agent_id = repo.get_or_create_agent(pid, "agent-alpha")
        task = repo.checkout_task(pid, first_agent_id)
        assert task is not None

        # 3. Force-expire the lease
        db_conn.execute(
            "UPDATE task_queue SET lease_expires_at = now() - interval '1 hour' WHERE id = %s",
            (task.id,),
        )

        # 4. MCP task_checkout with a different agent → auto requeue + checkout
        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "task_checkout", project=project_name, agent="agent-beta")
        data = json.loads(result)

        assert "task" in data, f"Expected task in response, got: {data}"
        assert data["task"]["node_id"] == str(task_node.id)
        assert data["node"]["title"] == "Expiring Task"

    def test_non_expired_task_not_requeued(self, db_conn: Connection, repo: KgnRepository) -> None:
        """
        Tasks with active lease are not requeued.
        Returns empty when no other tasks in queue.
        """
        project_name = f"e2e-requeue-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)

        # Create + enqueue + checkout (lease is still valid)
        task_node = _make_node(pid, title="Active Task", node_type=NodeType.TASK)
        repo.upsert_node(task_node)
        repo.enqueue_task(pid, task_node.id)
        agent_id = repo.get_or_create_agent(pid, "agent-holder")
        task = repo.checkout_task(pid, agent_id)
        assert task is not None

        # MCP task_checkout → should get empty (task is still held)
        server = create_server(project_name, conn=db_conn)
        result = _call_tool(server, "task_checkout", project=project_name, agent="agent-new")
        data = json.loads(result)
        assert data["status"] == "empty"
