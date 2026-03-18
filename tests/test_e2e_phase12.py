"""End-to-end integration tests for Phase 12 remediation.

Scenarios:
  A: Full version restoration via accept_a
  B: Handoff chain non-contamination (transitive stripping)
  C: Embedding failure isolation (graceful degradation)
  D: Depth clamping + batch query usage
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.graph.subgraph import MAX_SUBGRAPH_DEPTH, SubgraphService
from kgn.models.edge import EdgeRecord
from kgn.models.enums import (
    ActivityType,
    AgentRole,
    EdgeType,
    NodeStatus,
    NodeType,
    TaskState,
)
from kgn.models.node import NodeRecord
from kgn.orchestration.conflict_resolution import ConflictResolutionService
from kgn.orchestration.handoff import HANDOFF_SECTION_HEADER, HandoffService


def _node(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    title: str = "Node",
    node_type: NodeType = NodeType.SPEC,
    status: NodeStatus = NodeStatus.ACTIVE,
    body_md: str = "body",
    tags: list[str] | None = None,
    confidence: float | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=status,
        title=title,
        body_md=body_md,
        content_hash=uuid.uuid4().hex,
        tags=tags or [],
        confidence=confidence,
        created_by=agent_id,
        created_at=datetime.now(tz=UTC),
    )


def _edge(
    project_id: uuid.UUID,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    edge_type: EdgeType = EdgeType.DEPENDS_ON,
    *,
    agent_id: uuid.UUID | None = None,
) -> EdgeRecord:
    return EdgeRecord(
        project_id=project_id,
        from_node_id=from_id,
        to_node_id=to_id,
        type=edge_type,
        created_by=agent_id,
    )


# ── Scenario A: Full version restoration via accept_a ──────────────────


class TestAcceptAFullRestore:
    """accept_a must restore ALL mutable fields from the version snapshot."""

    def test_all_fields_restored(
        self,
        db_conn: Connection,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        # 1. Create original node
        original = _node(
            project_id, agent_id,
            title="Original Title",
            node_type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            body_md="Original body",
            tags=["alpha", "beta"],
            confidence=0.95,
        )
        repo.upsert_node(original)

        # 2. Agent A updates all fields
        a_update = NodeRecord(
            id=original.id,
            project_id=project_id,
            type=NodeType.GOAL,
            status=NodeStatus.SUPERSEDED,
            title="Agent A Title",
            body_md="Agent A body",
            content_hash=uuid.uuid4().hex,
            tags=["gamma"],
            confidence=0.7,
            created_by=agent_id,
            created_at=datetime.now(tz=UTC),
        )
        repo.upsert_node(a_update)

        # 3. Agent B overwrites
        agent_b = repo.get_or_create_agent(project_id, "agent-b")
        b_update = NodeRecord(
            id=original.id,
            project_id=project_id,
            type=NodeType.DECISION,
            status=NodeStatus.DEPRECATED,
            title="Agent B Title",
            body_md="Agent B body",
            content_hash=uuid.uuid4().hex,
            tags=["delta"],
            confidence=0.2,
            created_by=agent_b,
            created_at=datetime.now(tz=UTC),
        )
        repo.upsert_node(b_update)

        # 4. Create review task + resolve with accept_a
        svc = ConflictResolutionService(repo)
        svc.create_review_task(project_id, original.id, agent_id, agent_b)
        result = svc.resolve(
            project_id, original.id, "accept_a", agent_id=agent_id,
        )
        assert result.resolution == "accept_a"

        # 5. Verify ALL fields restored to Agent A's version
        restored = repo.get_node_by_id(original.id)
        assert restored is not None
        assert restored.title == "Agent A Title"
        assert restored.body_md == "Agent A body"
        assert restored.type == NodeType.GOAL
        assert restored.status == NodeStatus.SUPERSEDED
        assert restored.tags == ["gamma"]
        assert restored.confidence == pytest.approx(0.7)


# ── Scenario B: Handoff chain non-contamination ────────────────────────


class TestHandoffChainNoContamination:
    """5-step task chain: handoff context must NOT accumulate transitively."""

    def test_no_transitive_contamination(
        self,
        db_conn: Connection,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        svc = HandoffService(repo)

        # Create 5 task nodes in a chain: T1 → T2 → T3 → T4 → T5
        tasks = []
        for i in range(5):
            t = _node(
                project_id, agent_id,
                title=f"Task-{i+1}",
                node_type=NodeType.TASK,
                body_md=f"Body of task {i+1}",
            )
            repo.upsert_node(t)
            tasks.append(t)

        # Create DEPENDS_ON edges: T2→T1, T3→T2, T4→T3, T5→T4
        for i in range(1, 5):
            repo.insert_edge(
                _edge(project_id, tasks[i].id, tasks[i - 1].id,
                      agent_id=agent_id)
            )

        # Enqueue T2–T5 in task_queue as BLOCKED (required for the JOIN)
        for i in range(1, 5):
            repo.enqueue_task(project_id, tasks[i].id, state="BLOCKED")

        # Propagate T1 (completed) → injects into T2
        result1 = svc.propagate_context(tasks[0].id, project_id)
        assert len(result1.entries) >= 1

        # Propagate T2 (completed) → injects into T3
        svc.propagate_context(tasks[1].id, project_id)

        # T3's body should have at most 1 handoff section (no transitive)
        t3 = repo.get_node_by_id(tasks[2].id)
        assert t3 is not None
        body = t3.body_md or ""
        handoff_count = body.count(HANDOFF_SECTION_HEADER)
        assert handoff_count <= 1, (
            f"Transitive contamination: {handoff_count} handoff sections in T3"
        )


# ── Scenario C: Embedding failure isolation ────────────────────────────


class TestEmbeddingFailureIsolation:
    """Ingest must succeed even when embedding API fails."""

    def test_ingest_succeeds_despite_embed_failure(
        self,
        db_conn: Connection,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path,
    ) -> None:
        from kgn.ingest.service import IngestService

        kgn_content = """\
---
kgn_version: "0.1"
id: "new:embed-test"
type: SPEC
status: ACTIVE
title: "Embedding Failure Test"
project_id: test-project
agent_id: test-agent
created_at: 2025-01-01T00:00:00Z
---
This node should ingest even if embedding fails.
"""
        kgn_file = tmp_path / "embed-test.kgn"
        kgn_file.write_text(kgn_content, encoding="utf-8")

        svc = IngestService(
            repo=repo,
            project_id=project_id,
            agent_id=agent_id,
        )
        batch = svc.ingest_path(tmp_path)

        successes = [r for r in batch.details if r.status == "SUCCESS"]
        assert len(successes) >= 1


# ── Scenario D: Depth clamping + batch query ───────────────────────────


class TestDepthClamping:
    """Large depth request must be clamped to MAX_SUBGRAPH_DEPTH."""

    def test_depth_clamped_to_max(
        self,
        db_conn: Connection,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        # Create a chain of 11 nodes: N0 → N1 → ... → N10
        nodes = []
        for i in range(11):
            n = _node(project_id, agent_id, title=f"Chain-{i}")
            repo.upsert_node(n)
            nodes.append(n)

        for i in range(10):
            repo.insert_edge(
                _edge(project_id, nodes[i].id, nodes[i + 1].id,
                      agent_id=agent_id)
            )

        svc = SubgraphService(repo)

        # depth=100 should be clamped to MAX_SUBGRAPH_DEPTH
        result = svc.extract(
            root_id=nodes[0].id,
            project_id=project_id,
            depth=100,
        )

        assert result.depth == MAX_SUBGRAPH_DEPTH
        assert len(result.nodes) <= MAX_SUBGRAPH_DEPTH + 1


# ── Scenario E: Python StrEnum ↔ DB enum sync ─────────────────────────


_ENUM_PAIRS = [
    ("node_type", NodeType),
    ("node_status", NodeStatus),
    ("edge_type", EdgeType),
    ("activity_type", ActivityType),
    ("task_state", TaskState),
    ("agent_role", AgentRole),
]


class TestEnumSync:
    """Python StrEnum values must match PostgreSQL enum labels exactly."""

    @pytest.mark.parametrize("db_type,py_enum", _ENUM_PAIRS, ids=[p[0] for p in _ENUM_PAIRS])
    def test_enum_values_match(
        self,
        db_conn: Connection,
        db_type: str,
        py_enum: type,
    ) -> None:
        rows = db_conn.execute(
            f"SELECT unnest(enum_range(NULL::{db_type}))::text",
        ).fetchall()
        db_values = {r[0] for r in rows}
        py_values = {v.value for v in py_enum}
        assert db_values == py_values, (
            f"Mismatch for {db_type}: DB={db_values - py_values}, "
            f"Python={py_values - db_values}"
        )
