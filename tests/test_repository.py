"""Integration tests for KgnRepository.

Requires a running PostgreSQL instance (Docker on port 5433).
Each test runs inside a SAVEPOINT that is rolled back at teardown.
"""

from __future__ import annotations

import uuid

import pytest
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.models.edge import EdgeRecord
from kgn.models.enums import ActivityType, EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    *,
    node_id: uuid.UUID | None = None,
    node_type: NodeType = NodeType.SPEC,
    title: str = "Test Node",
    body: str = "## Context\n\ntest",
    content_hash: str | None = None,
    created_by: uuid.UUID | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=node_id or uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md=body,
        content_hash=content_hash or uuid.uuid4().hex,
        created_by=created_by,
    )


def _make_edge(
    project_id: uuid.UUID,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    edge_type: EdgeType = EdgeType.DEPENDS_ON,
    *,
    created_by: uuid.UUID | None = None,
) -> EdgeRecord:
    return EdgeRecord(
        project_id=project_id,
        from_node_id=from_id,
        to_node_id=to_id,
        type=edge_type,
        created_by=created_by,
    )


# ── Project ────────────────────────────────────────────────────────────


class TestProject:
    def test_create_project(self, repo: KgnRepository) -> None:
        pid = repo.get_or_create_project("repo-test-proj")
        assert isinstance(pid, uuid.UUID)

    def test_idempotent(self, repo: KgnRepository) -> None:
        p1 = repo.get_or_create_project("same-name")
        p2 = repo.get_or_create_project("same-name")
        assert p1 == p2


# ── Agent ──────────────────────────────────────────────────────────────


class TestAgent:
    def test_create_agent(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        aid = repo.get_or_create_agent(project_id, "a-1")
        assert isinstance(aid, uuid.UUID)

    def test_idempotent(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        a1 = repo.get_or_create_agent(project_id, "a-2")
        a2 = repo.get_or_create_agent(project_id, "a-2")
        assert a1 == a2


# ── Node upsert ────────────────────────────────────────────────────────


class TestUpsertNode:
    def test_insert_new(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id, created_by=agent_id)
        result = repo.upsert_node(node)
        assert result.status == "CREATED"
        assert result.node_id == node.id

    def test_v8_duplicate_hash_skipped(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        same_hash = "deadbeef" * 8
        n1 = _make_node(project_id, content_hash=same_hash)
        n2 = _make_node(project_id, content_hash=same_hash)

        r1 = repo.upsert_node(n1)
        r2 = repo.upsert_node(n2)

        assert r1.status == "CREATED"
        assert r2.status == "SKIPPED"
        assert r2.node_id == n1.id

    def test_update_existing(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        nid = uuid.uuid4()
        n1 = _make_node(
            project_id,
            node_id=nid,
            title="V1",
            content_hash="hash1",
            created_by=agent_id,
        )
        n2 = _make_node(
            project_id,
            node_id=nid,
            title="V2",
            content_hash="hash2",
            created_by=agent_id,
        )

        repo.upsert_node(n1)
        result = repo.upsert_node(n2)

        assert result.status == "UPDATED"
        updated = repo.get_node_by_id(nid)
        assert updated is not None
        assert updated.title == "V2"

    def test_version_saved_on_update(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        nid = uuid.uuid4()
        n1 = _make_node(project_id, node_id=nid, title="Original", content_hash="h1")
        n2 = _make_node(project_id, node_id=nid, title="Updated", content_hash="h2")

        repo.upsert_node(n1)
        repo.upsert_node(n2)

        row = db_conn.execute(
            "SELECT version, title FROM node_versions WHERE node_id = %s",
            (nid,),
        ).fetchone()
        assert row is not None
        assert row[0] == 1  # version 1
        assert row[1] == "Original"


# ── Node queries ───────────────────────────────────────────────────────


class TestNodeQueries:
    def test_get_node_by_id(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)

        fetched = repo.get_node_by_id(node.id)
        assert fetched is not None
        assert fetched.title == node.title

    def test_get_nonexistent(self, repo: KgnRepository) -> None:
        assert repo.get_node_by_id(uuid.uuid4()) is None

    def test_check_node_exists(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        assert repo.check_node_exists(node.id) is True
        assert repo.check_node_exists(uuid.uuid4()) is False

    def test_search_by_type(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        repo.upsert_node(_make_node(project_id, node_type=NodeType.SPEC, content_hash="s1"))
        repo.upsert_node(_make_node(project_id, node_type=NodeType.GOAL, content_hash="s2"))

        specs = repo.search_nodes(project_id, node_type=NodeType.SPEC)
        assert len(specs) == 1
        assert specs[0].type == NodeType.SPEC

    def test_search_by_status(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        node = _make_node(project_id, content_hash="ss1")
        node.status = NodeStatus.DEPRECATED
        repo.upsert_node(node)

        deps = repo.search_nodes(project_id, status=NodeStatus.DEPRECATED)
        assert len(deps) == 1

    def test_find_by_content_hash(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        node = _make_node(project_id, content_hash="unique-hash-42")
        repo.upsert_node(node)

        found = repo.find_node_by_content_hash(project_id, "unique-hash-42")
        assert found is not None
        assert found.id == node.id

        not_found = repo.find_node_by_content_hash(project_id, "no-such-hash")
        assert not_found is None


# ── Edge ───────────────────────────────────────────────────────────────


class TestEdge:
    def test_insert_edge(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        n1 = _make_node(project_id, content_hash="e1")
        n2 = _make_node(project_id, content_hash="e2")
        repo.upsert_node(n1)
        repo.upsert_node(n2)

        edge = _make_edge(project_id, n1.id, n2.id, created_by=agent_id)
        eid = repo.insert_edge(edge)
        assert isinstance(eid, int)

    def test_duplicate_edge_idempotent(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        n1 = _make_node(project_id, content_hash="de1")
        n2 = _make_node(project_id, content_hash="de2")
        repo.upsert_node(n1)
        repo.upsert_node(n2)

        edge = _make_edge(project_id, n1.id, n2.id)
        e1 = repo.insert_edge(edge)
        e2 = repo.insert_edge(edge)
        assert e1 == e2

    def test_get_edges_from(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        n1 = _make_node(project_id, content_hash="gf1")
        n2 = _make_node(project_id, content_hash="gf2")
        n3 = _make_node(project_id, content_hash="gf3")
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        repo.upsert_node(n3)

        repo.insert_edge(_make_edge(project_id, n1.id, n2.id, EdgeType.DEPENDS_ON))
        repo.insert_edge(_make_edge(project_id, n1.id, n3.id, EdgeType.IMPLEMENTS))

        edges = repo.get_edges_from(n1.id)
        assert len(edges) == 2

    def test_get_edges_to(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        n1 = _make_node(project_id, content_hash="gt1")
        n2 = _make_node(project_id, content_hash="gt2")
        repo.upsert_node(n1)
        repo.upsert_node(n2)

        repo.insert_edge(_make_edge(project_id, n1.id, n2.id))
        edges = repo.get_edges_to(n2.id)
        assert len(edges) == 1
        assert edges[0].from_node_id == n1.id


# ── Subgraph ───────────────────────────────────────────────────────────


class TestSubgraph:
    def test_extract_depth1(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        n1 = _make_node(project_id, title="root", content_hash="sg1")
        n2 = _make_node(project_id, title="child", content_hash="sg2")
        n3 = _make_node(project_id, title="grandchild", content_hash="sg3")
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        repo.upsert_node(n3)
        repo.insert_edge(_make_edge(project_id, n1.id, n2.id))
        repo.insert_edge(_make_edge(project_id, n2.id, n3.id))

        sg = repo.extract_subgraph(n1.id, project_id, depth=1)
        ids = {n.id for n in sg}
        assert n1.id in ids
        assert n2.id in ids
        assert n3.id not in ids  # depth=1 stops at child

    def test_extract_depth2(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        n1 = _make_node(project_id, title="root", content_hash="sg2a")
        n2 = _make_node(project_id, title="child", content_hash="sg2b")
        n3 = _make_node(project_id, title="grandchild", content_hash="sg2c")
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        repo.upsert_node(n3)
        repo.insert_edge(_make_edge(project_id, n1.id, n2.id))
        repo.insert_edge(_make_edge(project_id, n2.id, n3.id))

        sg = repo.extract_subgraph(n1.id, project_id, depth=2)
        ids = {n.id for n in sg}
        assert n3.id in ids  # depth=2 reaches grandchild

    def test_filter_by_edge_type(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        n1 = _make_node(project_id, content_hash="ft1")
        n2 = _make_node(project_id, content_hash="ft2")
        n3 = _make_node(project_id, content_hash="ft3")
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        repo.upsert_node(n3)
        repo.insert_edge(_make_edge(project_id, n1.id, n2.id, EdgeType.DEPENDS_ON))
        repo.insert_edge(_make_edge(project_id, n1.id, n3.id, EdgeType.IMPLEMENTS))

        sg = repo.extract_subgraph(
            n1.id,
            project_id,
            depth=1,
            edge_types=[EdgeType.DEPENDS_ON],
        )
        ids = {n.id for n in sg}
        assert n2.id in ids
        assert n3.id not in ids


# ── Ingest log ─────────────────────────────────────────────────────────


class TestIngestLog:
    def test_log_success(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        repo.log_ingest(project_id, "test.kgn", "abc123", "SUCCESS")

        row = db_conn.execute(
            "SELECT file_path, status FROM kgn_ingest_log WHERE project_id = %s",
            (project_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "test.kgn"
        assert row[1] == "SUCCESS"

    def test_log_failed_with_detail(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        repo.log_ingest(
            project_id,
            "bad.kgn",
            "def456",
            "FAILED",
            error_detail={"rule": "V4", "msg": "invalid type"},
        )
        row = db_conn.execute(
            "SELECT status, error_detail FROM kgn_ingest_log WHERE file_path = 'bad.kgn'",
        ).fetchone()
        assert row is not None
        assert row[0] == "FAILED"
        assert row[1]["rule"] == "V4"


# ── Activity log ───────────────────────────────────────────────────────


class TestActivityLog:
    def test_log_activity(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        repo.log_activity(
            project_id,
            agent_id,
            ActivityType.NODE_CREATED,
            message="created node",
        )
        row = db_conn.execute(
            "SELECT activity_type, message FROM agent_activities "
            "WHERE project_id = %s AND agent_id = %s",
            (project_id, agent_id),
        ).fetchone()
        assert row is not None
        assert row[0] == "NODE_CREATED"

    def test_append_only_no_update(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        """agent_activities trigger should prevent UPDATE."""
        repo.log_activity(project_id, agent_id, ActivityType.NODE_CREATED, message="x")

        with pytest.raises(Exception, match="append-only"):
            db_conn.execute(
                "UPDATE agent_activities SET message = 'hacked' "
                "WHERE project_id = %s AND agent_id = %s",
                (project_id, agent_id),
            )

    def test_append_only_no_delete(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        """agent_activities trigger should prevent DELETE."""
        repo.log_activity(project_id, agent_id, ActivityType.NODE_CREATED, message="y")

        with pytest.raises(Exception, match="append-only"):
            db_conn.execute(
                "DELETE FROM agent_activities WHERE project_id = %s AND agent_id = %s",
                (project_id, agent_id),
            )


# ── Statistics ─────────────────────────────────────────────────────────


class TestStatistics:
    def test_count_nodes(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        repo.upsert_node(_make_node(project_id, node_type=NodeType.SPEC, content_hash="cn1"))
        repo.upsert_node(_make_node(project_id, node_type=NodeType.SPEC, content_hash="cn2"))
        repo.upsert_node(_make_node(project_id, node_type=NodeType.GOAL, content_hash="cn3"))

        counts = repo.count_nodes(project_id)
        assert counts["SPEC"] == 2
        assert counts["GOAL"] == 1

    def test_count_edges(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        n1 = _make_node(project_id, content_hash="ce1")
        n2 = _make_node(project_id, content_hash="ce2")
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        repo.insert_edge(_make_edge(project_id, n1.id, n2.id, EdgeType.DEPENDS_ON))
        repo.insert_edge(_make_edge(project_id, n1.id, n2.id, EdgeType.IMPLEMENTS))

        counts = repo.count_edges(project_id)
        assert counts["DEPENDS_ON"] == 1
        assert counts["IMPLEMENTS"] == 1

    def test_count_orphan_nodes(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        n1 = _make_node(project_id, content_hash="co1")
        n2 = _make_node(project_id, content_hash="co2")
        n3 = _make_node(project_id, content_hash="co3")  # orphan
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        repo.upsert_node(n3)
        repo.insert_edge(_make_edge(project_id, n1.id, n2.id))

        orphans = repo.count_orphan_nodes(project_id)
        assert orphans == 1


# ── Vector search ──────────────────────────────────────────────────────

DIMS = 1536


def _dummy_vector(seed: float = 0.1) -> list[float]:
    """Return a 1536-dim dummy vector."""
    return [seed] * DIMS


def _similar_vector(base: float, offset: float) -> list[float]:
    """Return a vector close to but different from the base."""
    return [base + offset] * DIMS


class TestSearchSimilarNodes:
    """Integration tests for search_similar_nodes()."""

    def _setup_nodes_with_embeddings(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        count: int = 3,
    ) -> list[uuid.UUID]:
        """Create nodes and embed them with distinguishable vectors."""
        node_ids = []
        for i in range(count):
            node = _make_node(
                project_id,
                title=f"Node {i}",
                content_hash=f"sim-{uuid.uuid4().hex[:8]}",
                node_type=NodeType.SPEC,
            )
            repo.upsert_node(node)
            # Each node gets a vector offset so they have different similarities
            repo.upsert_embedding(
                node.id,
                project_id,
                _dummy_vector(0.1 * (i + 1)),
                "test-model",
            )
            node_ids.append(node.id)
        return node_ids

    def test_basic_top_k(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        self._setup_nodes_with_embeddings(repo, project_id, count=5)

        # Query with vector close to first node
        results = repo.search_similar_nodes(
            _dummy_vector(0.1),
            project_id,
            top_k=3,
        )
        assert len(results) == 3
        # All results should have similarity scores
        assert all(r.similarity > 0 for r in results)
        # Results should be sorted by descending similarity
        sims = [r.similarity for r in results]
        assert sims == sorted(sims, reverse=True)

    def test_top_k_limits_results(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        self._setup_nodes_with_embeddings(repo, project_id, count=5)

        results = repo.search_similar_nodes(
            _dummy_vector(0.1),
            project_id,
            top_k=2,
        )
        assert len(results) == 2

    def test_top_k_when_fewer_nodes(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        self._setup_nodes_with_embeddings(repo, project_id, count=2)

        results = repo.search_similar_nodes(
            _dummy_vector(0.1),
            project_id,
            top_k=10,
        )
        assert len(results) == 2

    def test_node_type_filter(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        # Create SPEC node
        spec = _make_node(
            project_id,
            title="Spec Node",
            content_hash="type-spec",
            node_type=NodeType.SPEC,
        )
        repo.upsert_node(spec)
        repo.upsert_embedding(spec.id, project_id, _dummy_vector(0.1), "m")

        # Create DECISION node
        dec = _make_node(
            project_id,
            title="Decision Node",
            content_hash="type-dec",
            node_type=NodeType.DECISION,
        )
        repo.upsert_node(dec)
        repo.upsert_embedding(dec.id, project_id, _dummy_vector(0.2), "m")

        # Filter by SPEC only
        results = repo.search_similar_nodes(
            _dummy_vector(0.15),
            project_id,
            node_type=NodeType.SPEC,
        )
        assert len(results) == 1
        assert results[0].type == "SPEC"

    def test_exclude_archived(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        # Active node
        active = _make_node(project_id, title="Active", content_hash="ea-act")
        repo.upsert_node(active)
        repo.upsert_embedding(active.id, project_id, _dummy_vector(0.1), "m")

        # Archived node
        archived_node = NodeRecord(
            id=uuid.uuid4(),
            project_id=project_id,
            type=NodeType.SPEC,
            status=NodeStatus.ARCHIVED,
            title="Archived",
            body_md="test",
            content_hash="ea-arch",
        )
        repo.upsert_node(archived_node)
        repo.upsert_embedding(archived_node.id, project_id, _dummy_vector(0.1), "m")

        # Default: exclude archived
        results = repo.search_similar_nodes(_dummy_vector(0.1), project_id)
        assert len(results) == 1
        assert results[0].id == active.id

    def test_include_archived(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        active = _make_node(project_id, title="Active", content_hash="ia-act")
        repo.upsert_node(active)
        repo.upsert_embedding(active.id, project_id, _dummy_vector(0.1), "m")

        archived_node = NodeRecord(
            id=uuid.uuid4(),
            project_id=project_id,
            type=NodeType.SPEC,
            status=NodeStatus.ARCHIVED,
            title="Archived",
            body_md="test",
            content_hash="ia-arch",
        )
        repo.upsert_node(archived_node)
        repo.upsert_embedding(archived_node.id, project_id, _dummy_vector(0.1), "m")

        results = repo.search_similar_nodes(_dummy_vector(0.1), project_id, exclude_archived=False)
        assert len(results) == 2

    def test_exclude_ids(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        ids = self._setup_nodes_with_embeddings(repo, project_id, count=3)

        results = repo.search_similar_nodes(
            _dummy_vector(0.1),
            project_id,
            exclude_ids={ids[0]},
        )
        result_ids = {r.id for r in results}
        assert ids[0] not in result_ids
        assert len(results) == 2

    def test_exclude_multiple_ids(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        ids = self._setup_nodes_with_embeddings(repo, project_id, count=4)

        results = repo.search_similar_nodes(
            _dummy_vector(0.1),
            project_id,
            exclude_ids={ids[0], ids[1]},
        )
        result_ids = {r.id for r in results}
        assert ids[0] not in result_ids
        assert ids[1] not in result_ids
        assert len(results) == 2

    def test_result_has_correct_fields(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        node = _make_node(
            project_id,
            title="My Title",
            content_hash="fld-test",
            node_type=NodeType.DECISION,
        )
        repo.upsert_node(node)
        repo.upsert_embedding(node.id, project_id, _dummy_vector(0.5), "m")

        results = repo.search_similar_nodes(_dummy_vector(0.5), project_id)
        assert len(results) == 1
        r = results[0]
        assert r.id == node.id
        assert r.type == "DECISION"
        assert r.title == "My Title"
        assert 0 <= r.similarity <= 1
        assert r.depth == 0

    def test_empty_project(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        results = repo.search_similar_nodes(_dummy_vector(0.1), project_id)
        assert results == []

    def test_combined_filters(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        """node_type + exclude_ids + exclude_archived combined."""
        spec1 = _make_node(
            project_id,
            title="Spec 1",
            content_hash="cf-s1",
            node_type=NodeType.SPEC,
        )
        spec2 = _make_node(
            project_id,
            title="Spec 2",
            content_hash="cf-s2",
            node_type=NodeType.SPEC,
        )
        dec = _make_node(
            project_id,
            title="Decision",
            content_hash="cf-d1",
            node_type=NodeType.DECISION,
        )
        for n in [spec1, spec2, dec]:
            repo.upsert_node(n)
            repo.upsert_embedding(n.id, project_id, _dummy_vector(0.3), "m")

        results = repo.search_similar_nodes(
            _dummy_vector(0.3),
            project_id,
            node_type=NodeType.SPEC,
            exclude_ids={spec1.id},
        )
        assert len(results) == 1
        assert results[0].id == spec2.id


# ── Task Queue ─────────────────────────────────────────────────────────


def _make_task_node(
    project_id: uuid.UUID,
    *,
    title: str = "Implement feature",
    created_by: uuid.UUID | None = None,
) -> NodeRecord:
    """Create a TASK-type NodeRecord for task queue tests."""
    return _make_node(
        project_id,
        node_type=NodeType.TASK,
        title=title,
        content_hash=uuid.uuid4().hex,
        created_by=created_by,
    )


class TestEnqueueTask:
    def test_enqueue_returns_uuid(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        task_id = repo.enqueue_task(project_id, node.id)
        assert isinstance(task_id, uuid.UUID)

    def test_enqueue_rejects_non_task_node(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id, node_type=NodeType.SPEC)
        repo.upsert_node(node)
        with pytest.raises(ValueError, match="expected TASK"):
            repo.enqueue_task(project_id, node.id)

    def test_enqueue_rejects_missing_node(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        with pytest.raises(ValueError, match="not found"):
            repo.enqueue_task(project_id, uuid.uuid4())

    def test_enqueue_custom_priority(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        task_id = repo.enqueue_task(project_id, node.id, priority=10)
        item = repo.get_task_status(task_id)
        assert item is not None
        assert item.priority == 10
        assert item.state == "READY"


class TestCheckoutTask:
    def test_checkout_returns_item(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        repo.enqueue_task(project_id, node.id)

        item = repo.checkout_task(project_id, agent_id)
        assert item is not None
        assert item.state == "IN_PROGRESS"
        assert item.leased_by == agent_id
        assert item.lease_expires_at is not None
        assert item.attempts == 1

    def test_checkout_none_when_empty(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        item = repo.checkout_task(project_id, agent_id)
        assert item is None

    def test_checkout_priority_order(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Higher priority (lower number) is consumed first."""
        node_low = _make_task_node(project_id, title="Low priority")
        node_high = _make_task_node(project_id, title="High priority")
        repo.upsert_node(node_low)
        repo.upsert_node(node_high)
        repo.enqueue_task(project_id, node_low.id, priority=200)
        repo.enqueue_task(project_id, node_high.id, priority=10)

        item = repo.checkout_task(project_id, agent_id)
        assert item is not None
        assert item.task_node_id == node_high.id

    def test_checkout_fifo_same_priority(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """Same priority: FIFO by created_at."""
        node_first = _make_task_node(project_id, title="First")
        node_second = _make_task_node(project_id, title="Second")
        repo.upsert_node(node_first)
        repo.upsert_node(node_second)
        repo.enqueue_task(project_id, node_first.id)
        repo.enqueue_task(project_id, node_second.id)

        item = repo.checkout_task(project_id, agent_id)
        assert item is not None
        assert item.task_node_id == node_first.id


class TestCompleteTask:
    def test_complete_in_progress(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        repo.enqueue_task(project_id, node.id)
        item = repo.checkout_task(project_id, agent_id)
        assert item is not None

        repo.complete_task(item.id)
        status = repo.get_task_status(item.id)
        assert status is not None
        assert status.state == "DONE"

    def test_complete_non_in_progress_raises(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        task_id = repo.enqueue_task(project_id, node.id)
        # Still READY, not IN_PROGRESS
        with pytest.raises(ValueError, match="not IN_PROGRESS"):
            repo.complete_task(task_id)

    def test_complete_missing_raises(self, repo: KgnRepository) -> None:
        with pytest.raises(ValueError, match="not IN_PROGRESS"):
            repo.complete_task(uuid.uuid4())


class TestFailTask:
    def test_fail_sets_failed(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        repo.enqueue_task(project_id, node.id)
        item = repo.checkout_task(project_id, agent_id)
        assert item is not None

        repo.fail_task(item.id, reason="test error")
        status = repo.get_task_status(item.id)
        assert status is not None
        assert status.state == "FAILED"

    def test_fail_non_in_progress_raises(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        task_id = repo.enqueue_task(project_id, node.id)
        with pytest.raises(ValueError, match="not IN_PROGRESS"):
            repo.fail_task(task_id)


class TestRequeueExpired:
    def test_requeue_expired_tasks(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        repo.enqueue_task(project_id, node.id)
        item = repo.checkout_task(project_id, agent_id, lease_duration_sec=1)
        assert item is not None

        # Force lease expiry
        db_conn.execute(
            "UPDATE task_queue SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
            (item.id,),
        )

        count = repo.requeue_expired(project_id)
        assert count == 1

        status = repo.get_task_status(item.id)
        assert status is not None
        assert status.state == "READY"
        assert status.leased_by is None

    def test_requeue_skips_under_max_attempts(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        """Tasks at max_attempts should NOT be requeued (stay FAILED-like)."""
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        repo.enqueue_task(project_id, node.id)
        item = repo.checkout_task(project_id, agent_id)
        assert item is not None

        # Force max attempts reached + lease expired
        db_conn.execute(
            "UPDATE task_queue "
            "SET lease_expires_at = now() - interval '1 second', "
            "    attempts = max_attempts "
            "WHERE id = %s",
            (item.id,),
        )

        count = repo.requeue_expired(project_id)
        assert count == 0

    def test_requeue_returns_zero_when_nothing_expired(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        count = repo.requeue_expired(project_id)
        assert count == 0


class TestListTasks:
    def test_list_all(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        n1 = _make_task_node(project_id, title="T1")
        n2 = _make_task_node(project_id, title="T2")
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        repo.enqueue_task(project_id, n1.id, priority=50)
        repo.enqueue_task(project_id, n2.id, priority=100)

        tasks = repo.list_tasks(project_id)
        assert len(tasks) == 2
        # Sorted by priority ASC
        assert tasks[0].priority == 50
        assert tasks[1].priority == 100

    def test_list_with_state_filter(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        n1 = _make_task_node(project_id, title="T-filter-1")
        n2 = _make_task_node(project_id, title="T-filter-2")
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        repo.enqueue_task(project_id, n1.id)
        repo.enqueue_task(project_id, n2.id)
        repo.checkout_task(project_id, agent_id)

        ready = repo.list_tasks(project_id, state="READY")
        in_progress = repo.list_tasks(project_id, state="IN_PROGRESS")
        assert len(ready) == 1
        assert len(in_progress) == 1

    def test_list_empty_project(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        tasks = repo.list_tasks(project_id)
        assert tasks == []


class TestGetTaskStatus:
    def test_existing_task(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_task_node(project_id)
        repo.upsert_node(node)
        task_id = repo.enqueue_task(project_id, node.id)

        item = repo.get_task_status(task_id)
        assert item is not None
        assert item.id == task_id
        assert item.state == "READY"
        assert item.attempts == 0

    def test_missing_task(self, repo: KgnRepository) -> None:
        item = repo.get_task_status(uuid.uuid4())
        assert item is None


# ── Embedding text helpers ─────────────────────────────────────────────


class TestGetNodeTextForEmbedding:
    """Tests for get_node_text_for_embedding()."""

    def test_returns_title_and_body(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id, title="Hello", body="## Context\n\nworld")
        repo.upsert_node(node)

        row = repo.get_node_text_for_embedding(node.id)
        assert row is not None
        assert row["title"] == "Hello"
        assert row["body_md"] == "## Context\n\nworld"

    def test_returns_none_for_missing(self, repo: KgnRepository) -> None:
        row = repo.get_node_text_for_embedding(uuid.uuid4())
        assert row is None


class TestGetNodesTextForEmbedding:
    """Tests for get_nodes_text_for_embedding()."""

    def test_by_node_ids(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        n1 = _make_node(project_id, title="A")
        n2 = _make_node(project_id, title="B")
        repo.upsert_node(n1)
        repo.upsert_node(n2)

        rows = repo.get_nodes_text_for_embedding(node_ids=[n1.id, n2.id])
        ids = {r["id"] for r in rows}
        assert n1.id in ids
        assert n2.id in ids
        assert all("title" in r and "body_md" in r for r in rows)

    def test_by_project_id(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        n1 = _make_node(project_id, title="X")
        repo.upsert_node(n1)

        rows = repo.get_nodes_text_for_embedding(project_id=project_id)
        assert len(rows) >= 1
        assert any(r["id"] == n1.id for r in rows)

    def test_excludes_archived(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id, title="Archived")
        repo.upsert_node(node)
        # Archive the node
        repo._conn.execute(
            "UPDATE nodes SET status = 'ARCHIVED' WHERE id = %s",
            (node.id,),
        )

        rows = repo.get_nodes_text_for_embedding(project_id=project_id)
        ids = {r["id"] for r in rows}
        assert node.id not in ids

    def test_includes_archived_when_disabled(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        node = _make_node(project_id, title="Archived2")
        repo.upsert_node(node)
        repo._conn.execute(
            "UPDATE nodes SET status = 'ARCHIVED' WHERE id = %s",
            (node.id,),
        )

        rows = repo.get_nodes_text_for_embedding(
            project_id=project_id,
            exclude_archived=False,
        )
        ids = {r["id"] for r in rows}
        assert node.id in ids

    def test_raises_without_args(self, repo: KgnRepository) -> None:
        with pytest.raises(ValueError, match="Either node_ids or project_id"):
            repo.get_nodes_text_for_embedding()

    def test_empty_node_ids(self, repo: KgnRepository) -> None:
        rows = repo.get_nodes_text_for_embedding(node_ids=[uuid.uuid4()])
        assert rows == []
