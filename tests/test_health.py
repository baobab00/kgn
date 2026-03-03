"""Tests for health metrics — repository queries + HealthService."""

from __future__ import annotations

import uuid

import pytest

from kgn.db.repository import KgnRepository
from kgn.graph.health import HealthReport, HealthService
from kgn.models.edge import EdgeRecord
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _insert_node(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    node_type: NodeType = NodeType.DECISION,
    status: NodeStatus = NodeStatus.ACTIVE,
    slug: str | None = None,
) -> uuid.UUID:
    """Insert a minimal node and return its id."""
    title = slug or f"node-{uuid.uuid4().hex[:6]}"
    node_id = uuid.uuid4()
    node = NodeRecord(
        id=node_id,
        project_id=project_id,
        type=node_type,
        status=status,
        title=title,
        body_md=f"body of {title}",
        file_path=f"test/{title}.kgn",
        content_hash=uuid.uuid4().hex,
        tags=[],
        confidence=None,
        created_by=agent_id,
    )
    result = repo.upsert_node(node)
    return result.node_id


def _insert_edge(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    edge_type: EdgeType = EdgeType.DEPENDS_ON,
) -> uuid.UUID:
    """Insert an edge and return its id."""
    edge = EdgeRecord(
        project_id=project_id,
        from_node_id=from_id,
        to_node_id=to_id,
        type=edge_type,
        note="",
        created_by=agent_id,
    )
    return repo.insert_edge(edge)


# ── HealthReport dataclass tests ──────────────────────────────────────


class TestHealthReport:
    """Unit tests for HealthReport properties (no DB)."""

    def test_orphan_rate_zero_active(self) -> None:
        r = HealthReport(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        assert r.orphan_rate == 0.0
        assert r.orphan_rate_ok is True

    def test_orphan_rate_below_threshold(self) -> None:
        r = HealthReport(10, 5, 10, 1, 0, 0, 0, 0, 0, 0)
        assert r.orphan_rate == pytest.approx(0.1)
        assert r.orphan_rate_ok is True

    def test_orphan_rate_above_threshold(self) -> None:
        r = HealthReport(5, 2, 5, 2, 0, 0, 0, 0, 0, 0)
        assert r.orphan_rate == pytest.approx(0.4)
        assert r.orphan_rate_ok is False

    def test_orphan_rate_exact_boundary(self) -> None:
        r = HealthReport(5, 2, 5, 1, 0, 0, 0, 0, 0, 0)
        assert r.orphan_rate == pytest.approx(0.2)
        # 0.2 is NOT < 0.2 → should be False
        assert r.orphan_rate_ok is False

    def test_conflict_ok(self) -> None:
        r = HealthReport(1, 0, 1, 0, 0, 0, 0, 0, 0, 0)
        assert r.conflict_ok is True

    def test_conflict_not_ok(self) -> None:
        r = HealthReport(2, 1, 2, 0, 1, 0, 0, 0, 0, 0)
        assert r.conflict_ok is False

    def test_superseded_stale_ok(self) -> None:
        r = HealthReport(1, 0, 1, 0, 0, 0, 0, 0, 0, 0)
        assert r.superseded_stale_ok is True

    def test_superseded_stale_not_ok(self) -> None:
        r = HealthReport(2, 0, 1, 0, 0, 1, 0, 0, 0, 0)
        assert r.superseded_stale_ok is False

    def test_dup_spec_rate_no_spec_nodes(self) -> None:
        r = HealthReport(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        assert r.dup_spec_rate == 0.0
        assert r.dup_spec_rate_ok is True

    def test_dup_spec_rate_healthy(self) -> None:
        # 0 pending / 10 spec = 0.0 → ok
        r = HealthReport(10, 0, 10, 0, 0, 0, 0, 0, 0, 10)
        assert r.dup_spec_rate == 0.0
        assert r.dup_spec_rate_ok is True

    def test_dup_spec_rate_below_threshold(self) -> None:
        # 1 pending / 20 spec = 0.05 → ok
        r = HealthReport(20, 0, 20, 0, 0, 0, 0, 0, 1, 20)
        assert r.dup_spec_rate == pytest.approx(0.05)
        assert r.dup_spec_rate_ok is True

    def test_dup_spec_rate_above_threshold(self) -> None:
        # 2 pending / 10 spec = 0.2 → not ok
        r = HealthReport(10, 0, 10, 0, 0, 0, 0, 0, 2, 10)
        assert r.dup_spec_rate == pytest.approx(0.2)
        assert r.dup_spec_rate_ok is False

    def test_dup_spec_rate_exact_boundary(self) -> None:
        # 1 pending / 10 spec = 0.1 → NOT < 0.1 → should be False
        r = HealthReport(10, 0, 10, 0, 0, 0, 0, 0, 1, 10)
        assert r.dup_spec_rate == pytest.approx(0.1)
        assert r.dup_spec_rate_ok is False


# ── Repository health queries ─────────────────────────────────────────


class TestRepositoryHealthQueries:
    """Integration tests for repository health helper methods."""

    def test_count_active_nodes_empty(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        assert repo.count_active_nodes(project_id) == 0

    def test_count_active_nodes(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        _insert_node(repo, project_id, agent_id, status=NodeStatus.ACTIVE)
        _insert_node(repo, project_id, agent_id, status=NodeStatus.ACTIVE)
        _insert_node(repo, project_id, agent_id, status=NodeStatus.ARCHIVED)
        assert repo.count_active_nodes(project_id) == 2

    def test_count_active_orphan_nodes_empty(
        self, repo: KgnRepository, project_id: uuid.UUID
    ) -> None:
        assert repo.count_active_orphan_nodes(project_id) == 0

    def test_count_active_orphan_includes_only_orphans(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        n1 = _insert_node(repo, project_id, agent_id)
        n2 = _insert_node(repo, project_id, agent_id)
        _insert_node(repo, project_id, agent_id)  # orphan
        _insert_edge(repo, project_id, agent_id, n1, n2)
        # n1 and n2 have edges, third is orphan
        assert repo.count_active_orphan_nodes(project_id) == 1

    def test_count_active_orphan_excludes_archived(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        _insert_node(repo, project_id, agent_id, status=NodeStatus.ARCHIVED)
        # archived orphan should NOT be counted
        assert repo.count_active_orphan_nodes(project_id) == 0

    def test_count_contradicts_edges_zero(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        assert repo.count_contradicts_edges(project_id) == 0

    def test_count_contradicts_edges(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        n1 = _insert_node(repo, project_id, agent_id)
        n2 = _insert_node(repo, project_id, agent_id)
        _insert_edge(repo, project_id, agent_id, n1, n2, EdgeType.CONTRADICTS)
        assert repo.count_contradicts_edges(project_id) == 1

    def test_count_superseded_stale_zero(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        assert repo.count_superseded_stale(project_id) == 0

    def test_count_superseded_stale_with_edge(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        """SUPERSEDED node WITH a SUPERSEDES edge → not stale."""
        old = _insert_node(repo, project_id, agent_id, status=NodeStatus.SUPERSEDED)
        new = _insert_node(repo, project_id, agent_id)
        _insert_edge(repo, project_id, agent_id, new, old, EdgeType.SUPERSEDES)
        assert repo.count_superseded_stale(project_id) == 0

    def test_count_superseded_stale_without_edge(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        """SUPERSEDED node WITHOUT a SUPERSEDES edge → stale."""
        _insert_node(repo, project_id, agent_id, status=NodeStatus.SUPERSEDED)
        assert repo.count_superseded_stale(project_id) == 1

    def test_count_wip_tasks_zero(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        assert repo.count_wip_tasks(project_id) == 0

    def test_count_wip_tasks(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        """WIP = task_queue IN_PROGRESS count (not TASK node count)."""
        # Create a TASK node and enqueue it
        node_id = _insert_node(
            repo, project_id, agent_id, node_type=NodeType.TASK, status=NodeStatus.ACTIVE
        )
        repo.enqueue_task(project_id, node_id)
        # READY task → not WIP
        assert repo.count_wip_tasks(project_id) == 0
        # Checkout → IN_PROGRESS → WIP
        repo.checkout_task(project_id, agent_id)
        assert repo.count_wip_tasks(project_id) == 1
        # Complete → DONE → not WIP
        task = repo.list_tasks(project_id, state="IN_PROGRESS")[0]
        repo.complete_task(task.id)
        assert repo.count_wip_tasks(project_id) == 0

    def test_count_open_assumptions_zero(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        assert repo.count_open_assumptions(project_id) == 0

    def test_count_open_assumptions(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        _insert_node(
            repo,
            project_id,
            agent_id,
            node_type=NodeType.ASSUMPTION,
            status=NodeStatus.ACTIVE,
        )
        _insert_node(
            repo,
            project_id,
            agent_id,
            node_type=NodeType.ASSUMPTION,
            status=NodeStatus.ARCHIVED,
        )
        assert repo.count_open_assumptions(project_id) == 1


# ── Repository: DupSpecRate queries ────────────────────────────────────────────


class TestRepositoryDupSpecQueries:
    """Integration tests for count_pending_contradicts / count_spec_nodes."""

    def test_count_pending_contradicts_zero(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        assert repo.count_pending_contradicts(project_id) == 0

    def test_count_pending_contradicts_only_pending(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        n1 = _insert_node(repo, project_id, agent_id, node_type=NodeType.SPEC)
        n2 = _insert_node(repo, project_id, agent_id, node_type=NodeType.SPEC)
        n3 = _insert_node(repo, project_id, agent_id, node_type=NodeType.SPEC)
        # PENDING contradicts
        repo.insert_contradicts_edge(
            project_id=project_id,
            from_node_id=n1,
            to_node_id=n2,
            note="dup",
            created_by=agent_id,
            status="PENDING",
        )
        # APPROVED contradicts (should NOT count)
        repo.insert_contradicts_edge(
            project_id=project_id,
            from_node_id=n2,
            to_node_id=n3,
            note="dup",
            created_by=agent_id,
            status="APPROVED",
        )
        assert repo.count_pending_contradicts(project_id) == 1

    def test_count_spec_nodes_zero(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        assert repo.count_spec_nodes(project_id) == 0

    def test_count_spec_nodes_excludes_archived(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        _insert_node(
            repo,
            project_id,
            agent_id,
            node_type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
        )
        _insert_node(
            repo,
            project_id,
            agent_id,
            node_type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
        )
        _insert_node(
            repo,
            project_id,
            agent_id,
            node_type=NodeType.SPEC,
            status=NodeStatus.ARCHIVED,
        )
        _insert_node(repo, project_id, agent_id, node_type=NodeType.DECISION)  # wrong type
        assert repo.count_spec_nodes(project_id) == 2


# ── HealthService.compute ──────────────────────────────────────────────


class TestHealthService:
    """Integration tests for HealthService.compute()."""

    def test_compute_empty_project(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        svc = HealthService(repo)
        report = svc.compute(project_id)
        assert report.total_nodes == 0
        assert report.total_edges == 0
        assert report.active_nodes == 0
        assert report.orphan_rate == 0.0
        assert report.orphan_rate_ok is True
        assert report.conflict_ok is True
        assert report.superseded_stale_ok is True
        assert report.wip_tasks == 0
        assert report.open_assumptions == 0
        assert report.dup_spec_rate == 0.0
        assert report.dup_spec_rate_ok is True

    def test_compute_healthy_graph(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        """All nodes connected, no conflicts → healthy."""
        n1 = _insert_node(repo, project_id, agent_id)
        n2 = _insert_node(repo, project_id, agent_id)
        _insert_edge(repo, project_id, agent_id, n1, n2)

        svc = HealthService(repo)
        report = svc.compute(project_id)

        assert report.total_nodes == 2
        assert report.total_edges == 1
        assert report.active_nodes == 2
        assert report.orphan_active == 0
        assert report.orphan_rate_ok is True
        assert report.conflict_ok is True

    def test_compute_unhealthy_graph(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        """Orphans, conflicts, superseded stale, WIP, assumptions."""
        # 5 orphan active nodes
        node_ids = [_insert_node(repo, project_id, agent_id) for _ in range(5)]

        # 1 conflict edge between first two (makes them non-orphan)
        _insert_edge(
            repo,
            project_id,
            agent_id,
            node_ids[0],
            node_ids[1],
            EdgeType.CONTRADICTS,
        )

        # 1 superseded stale
        _insert_node(repo, project_id, agent_id, status=NodeStatus.SUPERSEDED)

        # 1 WIP task (must be enqueued + checked out to be IN_PROGRESS)
        wip_node_id = _insert_node(repo, project_id, agent_id, node_type=NodeType.TASK)
        repo.enqueue_task(project_id, wip_node_id)
        repo.checkout_task(project_id, agent_id)

        # 1 assumption
        _insert_node(repo, project_id, agent_id, node_type=NodeType.ASSUMPTION)

        svc = HealthService(repo)
        report = svc.compute(project_id)

        # nodes[0] and nodes[1] have edges → not orphans
        # Remaining 3 original + task + assumption = 5 active orphans
        # active_nodes = 7 (5 original + task + assumption; superseded excluded)
        assert report.orphan_rate_ok is False
        assert report.conflict_count == 1
        assert report.conflict_ok is False
        assert report.superseded_stale == 1
        assert report.superseded_stale_ok is False
        assert report.wip_tasks == 1
        assert report.open_assumptions == 1
        assert report.dup_spec_rate_ok is True  # no SPEC nodes, no PENDING
