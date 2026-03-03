"""End-to-End integration tests for Phase 5 features.

Scenarios:
1. Full MCP cycle with auto-embedding:
   ingest 2 nodes (MockEmbeddingClient) → ingest edge → query_similar → get_subgraph
2. Task cycle with context package:
   ingest TASK node → enqueue → checkout (verify context package) →
   ingest result node → ingest IMPLEMENTS edge → task_complete → query_similar
3. Error codes in responses:
   get_node(nonexistent) → KGN-300, task_complete(nonexistent) → KGN-401,
   ingest_node(invalid) → KGN-2xx — all have {error, code, detail, recoverable}
4. Graceful embedding skip:
   server with embedding_client=None → ingest succeeds with embedding="skipped"
   → query_similar returns empty list

Requires a running PostgreSQL instance (Docker).
"""

from __future__ import annotations

import json
import uuid

import pytest
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.errors import KgnErrorCode
from kgn.mcp.server import create_server
from tests.helpers import (
    MockEmbeddingClient,
)
from tests.helpers import (
    assert_error_shape as _assert_error_shape,
)
from tests.helpers import (
    call_tool as _call_tool,
)
from tests.helpers import (
    make_kge as _make_kge,
)
from tests.helpers import (
    make_kgn as _make_kgn,
)

# ══════════════════════════════════════════════════════════════════════
#  Scenario 1: Full MCP cycle with auto-embedding
# ══════════════════════════════════════════════════════════════════════


class TestFullMCPCycleWithEmbedding:
    """ingest 2 nodes (auto-embed) → ingest edge → query_similar → get_subgraph."""

    @pytest.fixture
    def env(self, db_conn: Connection, repo: KgnRepository):
        """Set up project + server with MockEmbeddingClient."""
        project_name = f"e2e-p5-embed-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        mock_client = MockEmbeddingClient()
        server = create_server(
            project_name,
            conn=db_conn,
            embedding_client=mock_client,
        )
        return server, project_name, mock_client

    def test_ingest_produces_embedding_success(self, env) -> None:
        """ingest_node with embedding client → embedding: success."""
        server, project_name, mock_client = env

        kgn = _make_kgn(
            node_id=str(uuid.uuid4()),
            node_type="SPEC",
            title="Embedded Spec",
            project_id=project_name,
            body="## Content\n\nThis node should be auto-embedded.",
        )
        result = json.loads(_call_tool(server, "ingest_node", kgn_content=kgn))

        assert result["status"] == "ok"
        assert result["embedding"] == "success"
        assert mock_client.call_count >= 1

    def test_full_cycle_ingest_edge_query_subgraph(self, env) -> None:
        """
        Complete cycle:
        1. Ingest node A (GOAL) with auto-embed
        2. Ingest node B (SPEC) with auto-embed
        3. Ingest edge A → B (IMPLEMENTS)
        4. query_similar from A → finds B
        5. get_subgraph from A → includes both nodes + edge
        """
        server, project_name, mock_client = env

        # 1. Ingest node A (GOAL)
        node_a_id = str(uuid.uuid4())
        kgn_a = _make_kgn(
            node_id=node_a_id,
            node_type="GOAL",
            title="Architecture Goal",
            project_id=project_name,
            body="## Content\n\nDefine the system architecture.",
        )
        res_a = json.loads(_call_tool(server, "ingest_node", kgn_content=kgn_a))
        assert res_a["status"] == "ok"
        assert res_a["embedding"] == "success"

        # 2. Ingest node B (SPEC)
        node_b_id = str(uuid.uuid4())
        kgn_b = _make_kgn(
            node_id=node_b_id,
            node_type="SPEC",
            title="Architecture Spec",
            project_id=project_name,
            body="## Content\n\nImplement the architecture specification.",
        )
        res_b = json.loads(_call_tool(server, "ingest_node", kgn_content=kgn_b))
        assert res_b["status"] == "ok"
        assert res_b["embedding"] == "success"
        assert mock_client.call_count == 2

        # 3. Ingest edge A → B (IMPLEMENTS)
        kge = _make_kge(
            from_id=node_a_id,
            to_id=node_b_id,
            edge_type="IMPLEMENTS",
            project_id=project_name,
        )
        edge_res = json.loads(_call_tool(server, "ingest_edge", kge_content=kge))
        assert edge_res["status"] == "ok"
        assert edge_res["edge_count"] == 1

        # 4. query_similar from A → finds B
        similar_res = json.loads(_call_tool(server, "query_similar", node_id=node_a_id, top_k=5))
        assert isinstance(similar_res, list)
        assert len(similar_res) >= 1
        similar_ids = {s["id"] for s in similar_res}
        assert node_b_id in similar_ids
        # Both nodes have MockEmbeddingClient vectors — check similarity score exists
        assert all("similarity" in s for s in similar_res)

        # 5. get_subgraph from A → includes both nodes + edge
        sg_res = json.loads(_call_tool(server, "get_subgraph", node_id=node_a_id, depth=2))
        assert sg_res["root_id"] == node_a_id
        sg_node_ids = {n["id"] for n in sg_res["nodes"]}
        assert node_a_id in sg_node_ids
        assert node_b_id in sg_node_ids
        assert len(sg_res["edges"]) >= 1

    def test_query_similar_returns_similarity_scores(self, env) -> None:
        """query_similar results include 'similarity' float field."""
        server, project_name, _ = env

        # Ingest two nodes
        id_a, id_b = str(uuid.uuid4()), str(uuid.uuid4())
        for nid, title in [(id_a, "Score Node A"), (id_b, "Score Node B")]:
            kgn = _make_kgn(
                node_id=nid,
                node_type="SPEC",
                title=title,
                project_id=project_name,
                body=f"## Content\n\n{title} body.",
            )
            res = json.loads(_call_tool(server, "ingest_node", kgn_content=kgn))
            assert res["embedding"] == "success"

        similar = json.loads(_call_tool(server, "query_similar", node_id=id_a, top_k=5))
        assert len(similar) >= 1
        for s in similar:
            assert isinstance(s["similarity"], float)
            # cosine similarity can be slightly negative with hash-based mock vectors
            assert -1.0 <= s["similarity"] <= 1.0


# ══════════════════════════════════════════════════════════════════════
#  Scenario 2: Task cycle with context package
# ══════════════════════════════════════════════════════════════════════


class TestTaskCycleWithContextPackage:
    """ingest TASK → enqueue → checkout (context pkg) → result node → edge → complete → similar."""

    def test_full_task_cycle_with_embedding_and_edges(
        self, db_conn: Connection, repo: KgnRepository
    ) -> None:
        """
        End-to-end task lifecycle:
        1. Ingest TASK node (auto-embed)
        2. Enqueue task
        3. Checkout → verify context package structure
        4. Ingest result SPEC node (auto-embed)
        5. Ingest IMPLEMENTS edge (result → task)
        6. task_complete
        7. query_similar from task → finds result node
        """
        project_name = f"e2e-p5-task-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        mock_client = MockEmbeddingClient()
        server = create_server(project_name, conn=db_conn, embedding_client=mock_client)

        # 1. Ingest TASK node
        task_node_id = str(uuid.uuid4())
        task_kgn = _make_kgn(
            node_id=task_node_id,
            node_type="TASK",
            title="Implement Caching Layer",
            project_id=project_name,
            body="## Content\n\nAdd Redis caching for hot queries.",
        )
        ingest_res = json.loads(_call_tool(server, "ingest_node", kgn_content=task_kgn))
        assert ingest_res["status"] == "ok"
        assert ingest_res["embedding"] == "success"

        # 2. Enqueue
        enq_res = json.loads(
            _call_tool(server, "enqueue_task", task_node_id=task_node_id, priority=10)
        )
        assert enq_res["status"] == "READY"
        assert "task_queue_id" in enq_res

        # 3. Checkout — verify context package structure
        checkout_res = json.loads(
            _call_tool(server, "task_checkout", project=project_name, agent="e2e-agent")
        )
        assert "task" in checkout_res, f"Expected 'task' in checkout: {checkout_res}"
        assert "node" in checkout_res, f"Expected 'node' in checkout: {checkout_res}"
        assert checkout_res["task"]["node_id"] == task_node_id
        assert checkout_res["node"]["title"] == "Implement Caching Layer"
        task_queue_id = checkout_res["task"]["id"]

        # 4. Ingest result SPEC node (auto-embed)
        result_node_id = str(uuid.uuid4())
        result_kgn = _make_kgn(
            node_id=result_node_id,
            node_type="SPEC",
            title="Caching Layer Spec",
            project_id=project_name,
            body="## Content\n\nRedis caching implementation specification.",
        )
        result_res = json.loads(_call_tool(server, "ingest_node", kgn_content=result_kgn))
        assert result_res["status"] == "ok"
        assert result_res["embedding"] == "success"

        # 5. Ingest IMPLEMENTS edge (result → task)
        kge = _make_kge(
            from_id=result_node_id,
            to_id=task_node_id,
            edge_type="IMPLEMENTS",
            project_id=project_name,
        )
        edge_res = json.loads(_call_tool(server, "ingest_edge", kge_content=kge))
        assert edge_res["status"] == "ok"
        assert edge_res["edge_count"] == 1

        # 6. task_complete
        complete_res = json.loads(_call_tool(server, "task_complete", task_id=task_queue_id))
        assert complete_res["status"] == "ok"

        # 7. query_similar from task → finds result node
        similar = json.loads(_call_tool(server, "query_similar", node_id=task_node_id, top_k=5))
        assert isinstance(similar, list)
        assert len(similar) >= 1
        similar_ids = {s["id"] for s in similar}
        assert result_node_id in similar_ids

    def test_context_package_has_subgraph(self, db_conn: Connection, repo: KgnRepository) -> None:
        """Context package from checkout includes subgraph data."""
        project_name = f"e2e-p5-ctx-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn, embedding_client=None)

        # Create TASK node + a SPEC node linked to it
        task_id = str(uuid.uuid4())
        spec_id = str(uuid.uuid4())

        _call_tool(
            server,
            "ingest_node",
            kgn_content=_make_kgn(
                node_id=task_id,
                node_type="TASK",
                title="Context Task",
                project_id=project_name,
            ),
        )
        _call_tool(
            server,
            "ingest_node",
            kgn_content=_make_kgn(
                node_id=spec_id,
                node_type="SPEC",
                title="Related Spec",
                project_id=project_name,
            ),
        )
        _call_tool(
            server,
            "ingest_edge",
            kge_content=_make_kge(
                from_id=task_id,
                to_id=spec_id,
                edge_type="DEPENDS_ON",
                project_id=project_name,
            ),
        )

        # Enqueue + checkout
        _call_tool(server, "enqueue_task", task_node_id=task_id)
        checkout = json.loads(
            _call_tool(server, "task_checkout", project=project_name, agent="ctx-agent")
        )

        assert "task" in checkout
        assert "node" in checkout
        assert "subgraph" in checkout
        # Subgraph should contain at least the task node
        sg_nodes = checkout["subgraph"]["nodes"]
        sg_node_ids = {n["id"] for n in sg_nodes}
        assert task_id in sg_node_ids

    def test_checkout_empty_queue_returns_empty(
        self, db_conn: Connection, repo: KgnRepository
    ) -> None:
        """task_checkout on empty queue returns status=empty."""
        project_name = f"e2e-p5-empty-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn, embedding_client=None)

        result = json.loads(
            _call_tool(server, "task_checkout", project=project_name, agent="lonely-agent")
        )
        assert result["status"] == "empty"


# ══════════════════════════════════════════════════════════════════════
#  Scenario 3: Error codes in responses
# ══════════════════════════════════════════════════════════════════════


class TestErrorCodesInResponses:
    """Verify structured error codes in MCP tool responses."""

    @pytest.fixture
    def server(self, db_conn: Connection, repo: KgnRepository):
        project_name = f"e2e-p5-err-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        return create_server(project_name, conn=db_conn, embedding_client=None), project_name

    def test_get_node_not_found_returns_kgn_300(self, server) -> None:
        """get_node with nonexistent UUID → KGN-300."""
        srv, _ = server
        fake_id = str(uuid.uuid4())
        result = json.loads(_call_tool(srv, "get_node", node_id=fake_id))

        _assert_error_shape(result)
        assert result["code"] == KgnErrorCode.NODE_NOT_FOUND  # KGN-300
        assert result["recoverable"] is False

    def test_get_node_invalid_uuid_returns_kgn_210(self, server) -> None:
        """get_node with malformed UUID → KGN-210."""
        srv, _ = server
        result = json.loads(_call_tool(srv, "get_node", node_id="not-a-uuid"))

        _assert_error_shape(result)
        assert result["code"] == KgnErrorCode.INVALID_UUID  # KGN-210
        assert result["recoverable"] is False

    def test_task_complete_nonexistent_returns_kgn_401(self, server) -> None:
        """task_complete with non-existent task → KGN-401."""
        srv, _ = server
        fake_id = str(uuid.uuid4())
        result = json.loads(_call_tool(srv, "task_complete", task_id=fake_id))

        _assert_error_shape(result)
        assert result["code"] == KgnErrorCode.TASK_NOT_IN_PROGRESS  # KGN-401
        assert result["recoverable"] is False

    def test_task_fail_nonexistent_returns_kgn_401(self, server) -> None:
        """task_fail with non-existent task → KGN-401."""
        srv, _ = server
        fake_id = str(uuid.uuid4())
        result = json.loads(_call_tool(srv, "task_fail", task_id=fake_id, reason="test"))

        _assert_error_shape(result)
        assert result["code"] == KgnErrorCode.TASK_NOT_IN_PROGRESS  # KGN-401

    def test_ingest_invalid_kgn_returns_kgn_2xx(self, server) -> None:
        """ingest_node with invalid content → KGN-2xx error code."""
        srv, _ = server
        result = json.loads(_call_tool(srv, "ingest_node", kgn_content="not valid kgn"))

        _assert_error_shape(result)
        # Should be one of the 2xx ingest error codes
        assert result["code"].startswith("KGN-2"), f"Expected KGN-2xx, got {result['code']}"
        assert result["recoverable"] is False

    def test_query_nodes_invalid_type_returns_kgn_310(self, server) -> None:
        """query_nodes with invalid type filter → KGN-310."""
        srv, project_name = server
        result = json.loads(
            _call_tool(srv, "query_nodes", project=project_name, type="NONEXISTENT")
        )

        _assert_error_shape(result)
        assert result["code"] == KgnErrorCode.INVALID_NODE_TYPE  # KGN-310

    def test_query_nodes_invalid_status_returns_kgn_311(self, server) -> None:
        """query_nodes with invalid status filter → KGN-311."""
        srv, project_name = server
        result = json.loads(_call_tool(srv, "query_nodes", project=project_name, status="INVALID"))

        _assert_error_shape(result)
        assert result["code"] == KgnErrorCode.INVALID_NODE_STATUS  # KGN-311

    def test_query_nodes_unknown_project_returns_kgn_301(self, server) -> None:
        """query_nodes with unknown project → KGN-301."""
        srv, _ = server
        result = json.loads(_call_tool(srv, "query_nodes", project="nonexistent-project-xyz"))

        _assert_error_shape(result)
        assert result["code"] == KgnErrorCode.PROJECT_NOT_FOUND  # KGN-301

    def test_error_response_has_all_four_fields(self, server) -> None:
        """Every error response contains exactly {error, code, detail, recoverable}."""
        srv, _ = server
        fake_id = str(uuid.uuid4())

        # Collect multiple error responses
        errors = [
            json.loads(_call_tool(srv, "get_node", node_id=fake_id)),
            json.loads(_call_tool(srv, "get_node", node_id="bad")),
            json.loads(_call_tool(srv, "ingest_node", kgn_content="broken")),
        ]
        for err in errors:
            _assert_error_shape(err)


# ══════════════════════════════════════════════════════════════════════
#  Scenario 4: Graceful embedding skip
# ══════════════════════════════════════════════════════════════════════


class TestGracefulEmbeddingSkip:
    """Server with embedding_client=None → ingest works, embedding skipped."""

    @pytest.fixture
    def env(self, db_conn: Connection, repo: KgnRepository):
        """Server with no embedding client."""
        project_name = f"e2e-p5-noembed-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(
            project_name,
            conn=db_conn,
            embedding_client=None,
        )
        return server, project_name

    def test_ingest_without_embedding_client_returns_skipped(self, env) -> None:
        """ingest_node with no embedding client → embedding: skipped."""
        server, project_name = env

        kgn = _make_kgn(
            node_id=str(uuid.uuid4()),
            node_type="SPEC",
            title="No Embed Node",
            project_id=project_name,
        )
        result = json.loads(_call_tool(server, "ingest_node", kgn_content=kgn))

        assert result["status"] == "ok"
        assert result["embedding"] == "skipped"

    def test_query_similar_without_embedding_returns_empty(self, env) -> None:
        """query_similar on un-embedded node returns empty list."""
        server, project_name = env

        node_id = str(uuid.uuid4())
        kgn = _make_kgn(
            node_id=node_id,
            node_type="GOAL",
            title="No Embed Goal",
            project_id=project_name,
        )
        ingest = json.loads(_call_tool(server, "ingest_node", kgn_content=kgn))
        assert ingest["status"] == "ok"
        assert ingest["embedding"] == "skipped"

        similar = json.loads(_call_tool(server, "query_similar", node_id=node_id, top_k=5))
        assert similar == []

    def test_get_node_still_works_without_embedding(self, env) -> None:
        """get_node returns full node data regardless of embedding status."""
        server, project_name = env

        node_id = str(uuid.uuid4())
        kgn = _make_kgn(
            node_id=node_id,
            node_type="DECISION",
            title="Decision Without Embed",
            project_id=project_name,
            body="## Content\n\nDecided to skip embedding.",
        )
        json.loads(_call_tool(server, "ingest_node", kgn_content=kgn))

        result = json.loads(_call_tool(server, "get_node", node_id=node_id))
        assert result["id"] == node_id
        assert result["title"] == "Decision Without Embed"
        assert result["type"] == "DECISION"

    def test_subgraph_works_without_embedding(self, env) -> None:
        """get_subgraph works correctly even without any embeddings."""
        server, project_name = env

        id_a, id_b = str(uuid.uuid4()), str(uuid.uuid4())
        for nid, title, ntype in [
            (id_a, "SG Node A", "GOAL"),
            (id_b, "SG Node B", "SPEC"),
        ]:
            _call_tool(
                server,
                "ingest_node",
                kgn_content=_make_kgn(
                    node_id=nid,
                    node_type=ntype,
                    title=title,
                    project_id=project_name,
                ),
            )

        _call_tool(
            server,
            "ingest_edge",
            kge_content=_make_kge(
                from_id=id_a,
                to_id=id_b,
                edge_type="DEPENDS_ON",
                project_id=project_name,
            ),
        )

        sg = json.loads(_call_tool(server, "get_subgraph", node_id=id_a, depth=2))
        assert sg["root_id"] == id_a
        sg_ids = {n["id"] for n in sg["nodes"]}
        assert id_a in sg_ids
        assert id_b in sg_ids
        assert len(sg["edges"]) >= 1
