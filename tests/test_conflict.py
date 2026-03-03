"""Tests for conflict detection service (Phase 2, Step 6).

Test categories:
- Repository: edge status, CONTRADICTS edge CRUD
- ConflictService: scan, approve, dismiss
- CLI: smoke tests for conflict subcommands
"""

from __future__ import annotations

import uuid

import pytest

from kgn.conflict.service import ConflictService
from kgn.db.repository import KgnRepository
from kgn.models.enums import EdgeType, NodeStatus, NodeType

# ── Helpers ────────────────────────────────────────────────────────────


def _create_node(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    title: str = "Test Node",
    node_type: NodeType = NodeType.SPEC,
    body_md: str = "body",
) -> uuid.UUID:
    """Insert a test node and return its UUID."""
    from kgn.models.node import NodeRecord

    node_id = uuid.uuid4()
    node = NodeRecord(
        id=node_id,
        project_id=project_id,
        type=node_type,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md=body_md,
        created_by=agent_id,
    )
    repo.upsert_node(node)
    return node_id


def _embed_node(
    repo: KgnRepository,
    node_id: uuid.UUID,
    project_id: uuid.UUID,
    embedding: list[float],
) -> None:
    """Insert an embedding for a test node."""
    repo.upsert_embedding(node_id, project_id, embedding, "test-model")


def _make_similar_embeddings(
    dim: int = 1536,
) -> tuple[list[float], list[float]]:
    """Create two highly similar embedding vectors."""
    base = [0.1] * dim
    similar = base.copy()
    similar[0] = 0.1001  # tiny difference → very high cosine similarity
    return base, similar


def _make_different_embeddings(
    dim: int = 1536,
) -> tuple[list[float], list[float]]:
    """Create two very different embedding vectors."""
    a = [1.0] + [0.0] * (dim - 1)
    b = [0.0] + [1.0] + [0.0] * (dim - 2)
    return a, b


# ══════════════════════════════════════════════════════════════════════
# Repository — edge status
# ══════════════════════════════════════════════════════════════════════


class TestEdgeStatus:
    """Tests for edge status column and related repository methods."""

    def test_insert_contradicts_edge_with_status(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Node A")
        node_b = _create_node(repo, project_id, agent_id, title="Node B")

        edge_id = repo.insert_contradicts_edge(project_id, node_a, node_b, "PENDING")
        assert edge_id > 0

        edge = repo.get_edge_by_id(edge_id)
        assert edge is not None
        assert edge["status"] == "PENDING"
        assert edge["type"] == "CONTRADICTS"

    def test_update_edge_status(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Node A")
        node_b = _create_node(repo, project_id, agent_id, title="Node B")

        edge_id = repo.insert_contradicts_edge(project_id, node_a, node_b, "PENDING")
        repo.update_edge_status(edge_id, "APPROVED")

        edge = repo.get_edge_by_id(edge_id)
        assert edge is not None
        assert edge["status"] == "APPROVED"

    def test_find_contradicts_edge_both_directions(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Node A")
        node_b = _create_node(repo, project_id, agent_id, title="Node B")

        repo.insert_contradicts_edge(project_id, node_a, node_b, "PENDING")

        # Should find in both directions
        found_ab = repo.find_contradicts_edge(project_id, node_a, node_b)
        found_ba = repo.find_contradicts_edge(project_id, node_b, node_a)
        assert found_ab is not None
        assert found_ba is not None
        assert found_ab["id"] == found_ba["id"]

    def test_find_contradicts_edge_not_found(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Node A")
        node_b = _create_node(repo, project_id, agent_id, title="Node B")

        found = repo.find_contradicts_edge(project_id, node_a, node_b)
        assert found is None

    def test_get_contradicts_edges_list(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Node A")
        node_b = _create_node(repo, project_id, agent_id, title="Node B")
        node_c = _create_node(repo, project_id, agent_id, title="Node C")

        repo.insert_contradicts_edge(project_id, node_a, node_b, "APPROVED")
        repo.insert_contradicts_edge(project_id, node_b, node_c, "PENDING")

        all_edges = repo.get_contradicts_edges(project_id)
        assert len(all_edges) == 2

        pending = repo.get_contradicts_edges(project_id, status_filter="PENDING")
        assert len(pending) == 1
        assert pending[0]["status"] == "PENDING"

    def test_default_edge_insert_has_approved_status(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        """Regular edge insert (non-CONTRADICTS) should default to APPROVED."""
        from kgn.models.edge import EdgeRecord

        node_a = _create_node(repo, project_id, agent_id, title="Node A")
        node_b = _create_node(repo, project_id, agent_id, title="Node B")

        edge = EdgeRecord(
            project_id=project_id,
            from_node_id=node_a,
            to_node_id=node_b,
            type=EdgeType.DEPENDS_ON,
            created_by=agent_id,
        )
        edge_id = repo.insert_edge(edge)
        row = repo.get_edge_by_id(edge_id)
        assert row is not None
        assert row["status"] == "APPROVED"

    def test_get_edge_by_id_returns_none(self, repo: KgnRepository) -> None:
        assert repo.get_edge_by_id(999999) is None

    def test_compute_cosine_similarity(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Node A")
        node_b = _create_node(repo, project_id, agent_id, title="Node B")

        emb_a, emb_b = _make_similar_embeddings()
        _embed_node(repo, node_a, project_id, emb_a)
        _embed_node(repo, node_b, project_id, emb_b)

        sim = repo.compute_cosine_similarity(node_a, node_b)
        assert sim is not None
        assert sim > 0.99  # very similar

    def test_compute_cosine_similarity_no_embedding(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Node A")
        node_b = _create_node(repo, project_id, agent_id, title="Node B")

        sim = repo.compute_cosine_similarity(node_a, node_b)
        assert sim is None

    def test_get_embedded_node_ids_by_types(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        spec = _create_node(repo, project_id, agent_id, title="Spec", node_type=NodeType.SPEC)
        goal = _create_node(repo, project_id, agent_id, title="Goal", node_type=NodeType.GOAL)
        task = _create_node(repo, project_id, agent_id, title="Task", node_type=NodeType.TASK)

        emb = [0.1] * 1536
        _embed_node(repo, spec, project_id, emb)
        _embed_node(repo, goal, project_id, emb)
        _embed_node(repo, task, project_id, emb)

        # Only SPEC and DECISION
        results = repo.get_embedded_node_ids_by_types(
            project_id, [NodeType.SPEC, NodeType.DECISION]
        )
        assert len(results) == 1
        assert results[0]["id"] == spec


# ══════════════════════════════════════════════════════════════════════
# ConflictService
# ══════════════════════════════════════════════════════════════════════


class TestConflictServiceScan:
    """Tests for ConflictService.scan()."""

    def test_scan_finds_similar_pair(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Spec Alpha")
        node_b = _create_node(repo, project_id, agent_id, title="Spec Beta")

        emb_a, emb_b = _make_similar_embeddings()
        _embed_node(repo, node_a, project_id, emb_a)
        _embed_node(repo, node_b, project_id, emb_b)

        svc = ConflictService(repo)
        candidates = svc.scan(project_id, threshold=0.9)

        assert len(candidates) == 1
        assert candidates[0].status == "NEW"
        assert candidates[0].similarity > 0.9

    def test_scan_skips_different_pair(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Spec Alpha")
        node_b = _create_node(repo, project_id, agent_id, title="Spec Beta")

        emb_a, emb_b = _make_different_embeddings()
        _embed_node(repo, node_a, project_id, emb_a)
        _embed_node(repo, node_b, project_id, emb_b)

        svc = ConflictService(repo)
        candidates = svc.scan(project_id, threshold=0.9)

        assert len(candidates) == 0

    def test_scan_shows_pending_status(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Spec Alpha")
        node_b = _create_node(repo, project_id, agent_id, title="Spec Beta")

        emb_a, emb_b = _make_similar_embeddings()
        _embed_node(repo, node_a, project_id, emb_a)
        _embed_node(repo, node_b, project_id, emb_b)

        # Create PENDING CONTRADICTS edge
        repo.insert_contradicts_edge(project_id, node_a, node_b, "PENDING")

        svc = ConflictService(repo)
        candidates = svc.scan(project_id, threshold=0.9)

        assert len(candidates) == 1
        assert candidates[0].status == "PENDING"

    def test_scan_skips_approved(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Spec Alpha")
        node_b = _create_node(repo, project_id, agent_id, title="Spec Beta")

        emb_a, emb_b = _make_similar_embeddings()
        _embed_node(repo, node_a, project_id, emb_a)
        _embed_node(repo, node_b, project_id, emb_b)

        repo.insert_contradicts_edge(project_id, node_a, node_b, "APPROVED")

        svc = ConflictService(repo)
        candidates = svc.scan(project_id, threshold=0.9)

        assert len(candidates) == 0

    def test_scan_skips_dismissed(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Spec Alpha")
        node_b = _create_node(repo, project_id, agent_id, title="Spec Beta")

        emb_a, emb_b = _make_similar_embeddings()
        _embed_node(repo, node_a, project_id, emb_a)
        _embed_node(repo, node_b, project_id, emb_b)

        repo.insert_contradicts_edge(project_id, node_a, node_b, "DISMISSED")

        svc = ConflictService(repo)
        candidates = svc.scan(project_id, threshold=0.9)

        assert len(candidates) == 0

    def test_scan_no_embedded_nodes(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        _create_node(repo, project_id, agent_id, title="No Embedding")

        svc = ConflictService(repo)
        candidates = svc.scan(project_id, threshold=0.9)

        assert candidates == []

    def test_scan_single_node(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Only One")
        _embed_node(repo, node_a, project_id, [0.1] * 1536)

        svc = ConflictService(repo)
        candidates = svc.scan(project_id, threshold=0.9)

        assert candidates == []

    def test_scan_custom_types(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        """Scan only GOAL type nodes."""
        goal_a = _create_node(repo, project_id, agent_id, title="Goal A", node_type=NodeType.GOAL)
        goal_b = _create_node(repo, project_id, agent_id, title="Goal B", node_type=NodeType.GOAL)

        emb_a, emb_b = _make_similar_embeddings()
        _embed_node(repo, goal_a, project_id, emb_a)
        _embed_node(repo, goal_b, project_id, emb_b)

        svc = ConflictService(repo)
        # Default types (SPEC, DECISION) should find nothing
        assert svc.scan(project_id, threshold=0.9) == []
        # Custom types should find the pair
        candidates = svc.scan(project_id, threshold=0.9, node_types=[NodeType.GOAL])
        assert len(candidates) == 1

    def test_scan_sorted_by_similarity(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        """Multiple candidates are sorted by descending similarity."""
        node_a = _create_node(repo, project_id, agent_id, title="Spec A")
        node_b = _create_node(repo, project_id, agent_id, title="Spec B")
        node_c = _create_node(repo, project_id, agent_id, title="Spec C")

        # a and b are very similar, a and c are somewhat similar
        emb_a = [0.1] * 1536
        emb_b = [0.1] * 1536  # identical = sim 1.0
        emb_c = [0.1] * 1535 + [0.5]  # slightly different

        _embed_node(repo, node_a, project_id, emb_a)
        _embed_node(repo, node_b, project_id, emb_b)
        _embed_node(repo, node_c, project_id, emb_c)

        svc = ConflictService(repo)
        candidates = svc.scan(project_id, threshold=0.9)

        assert len(candidates) >= 2
        # Should be sorted descending
        for i in range(len(candidates) - 1):
            assert candidates[i].similarity >= candidates[i + 1].similarity


# ══════════════════════════════════════════════════════════════════════
# ConflictService — scan() warning threshold
# ══════════════════════════════════════════════════════════════════════


class TestConflictScanWarning:
    """Tests for scan() O(n²) node count warning."""

    def test_scan_warns_when_nodes_exceed_threshold(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """scan() should log warning when node count exceeds threshold."""
        import logging

        import kgn.conflict.service as svc_mod

        # Set threshold very low so our 3 nodes trigger it
        monkeypatch.setattr(svc_mod, "SCAN_WARN_THRESHOLD", 2)

        node_a = _create_node(repo, project_id, agent_id, title="Spec A")
        node_b = _create_node(repo, project_id, agent_id, title="Spec B")
        node_c = _create_node(repo, project_id, agent_id, title="Spec C")

        for nid in (node_a, node_b, node_c):
            _embed_node(repo, nid, project_id, [0.1] * 1536)

        svc = ConflictService(repo)
        with caplog.at_level(logging.WARNING, logger="kgn.conflict"):
            svc.scan(project_id, threshold=0.99)

        assert any("scan_large_node_set" in msg for msg in caplog.messages)
        assert any("3" in msg for msg in caplog.messages)

    def test_scan_no_warning_below_threshold(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """scan() should NOT warn when node count is within threshold."""
        import logging

        import kgn.conflict.service as svc_mod

        monkeypatch.setattr(svc_mod, "SCAN_WARN_THRESHOLD", 100)

        node_a = _create_node(repo, project_id, agent_id, title="Spec A")
        node_b = _create_node(repo, project_id, agent_id, title="Spec B")

        for nid in (node_a, node_b):
            _embed_node(repo, nid, project_id, [0.1] * 1536)

        svc = ConflictService(repo)
        with caplog.at_level(logging.WARNING, logger="kgn.conflict"):
            svc.scan(project_id, threshold=0.99)

        assert not any("scan_large_node_set" in msg for msg in caplog.messages)

    def test_scan_warning_includes_threshold_value(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Warning message should include current threshold value."""
        import logging

        import kgn.conflict.service as svc_mod

        monkeypatch.setattr(svc_mod, "SCAN_WARN_THRESHOLD", 1)

        node_a = _create_node(repo, project_id, agent_id, title="Spec A")
        node_b = _create_node(repo, project_id, agent_id, title="Spec B")

        for nid in (node_a, node_b):
            _embed_node(repo, nid, project_id, [0.1] * 1536)

        svc = ConflictService(repo)
        with caplog.at_level(logging.WARNING, logger="kgn.conflict"):
            svc.scan(project_id, threshold=0.99)

        assert any("'threshold': 1" in msg or "'threshold': 1," in msg for msg in caplog.messages)


class TestConflictServiceApprove:
    """Tests for ConflictService.approve()."""

    def test_approve_creates_edge(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Node A")
        node_b = _create_node(repo, project_id, agent_id, title="Node B")

        svc = ConflictService(repo)
        edge_id = svc.approve(project_id, node_a, node_b, note="confirmed conflict")

        edge = repo.get_edge_by_id(edge_id)
        assert edge is not None
        assert edge["status"] == "APPROVED"
        assert edge["type"] == "CONTRADICTS"

    def test_approve_updates_pending(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Node A")
        node_b = _create_node(repo, project_id, agent_id, title="Node B")

        pending_id = repo.insert_contradicts_edge(project_id, node_a, node_b, "PENDING")

        svc = ConflictService(repo)
        edge_id = svc.approve(project_id, node_a, node_b)

        assert edge_id == pending_id
        edge = repo.get_edge_by_id(edge_id)
        assert edge["status"] == "APPROVED"

    def test_approve_idempotent(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Node A")
        node_b = _create_node(repo, project_id, agent_id, title="Node B")

        svc = ConflictService(repo)
        id1 = svc.approve(project_id, node_a, node_b)
        id2 = svc.approve(project_id, node_a, node_b)

        assert id1 == id2


class TestConflictServiceDismiss:
    """Tests for ConflictService.dismiss()."""

    def test_dismiss_creates_edge(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Node A")
        node_b = _create_node(repo, project_id, agent_id, title="Node B")

        svc = ConflictService(repo)
        edge_id = svc.dismiss(project_id, node_a, node_b)

        edge = repo.get_edge_by_id(edge_id)
        assert edge is not None
        assert edge["status"] == "DISMISSED"

    def test_dismiss_updates_pending(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        node_a = _create_node(repo, project_id, agent_id, title="Node A")
        node_b = _create_node(repo, project_id, agent_id, title="Node B")

        pending_id = repo.insert_contradicts_edge(project_id, node_a, node_b, "PENDING")

        svc = ConflictService(repo)
        edge_id = svc.dismiss(project_id, node_a, node_b)

        assert edge_id == pending_id
        edge = repo.get_edge_by_id(edge_id)
        assert edge["status"] == "DISMISSED"

    def test_dismiss_persists_for_scan_skip(
        self, repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> None:
        """After dismiss, scan should skip the pair."""
        node_a = _create_node(repo, project_id, agent_id, title="Spec Alpha")
        node_b = _create_node(repo, project_id, agent_id, title="Spec Beta")

        emb_a, emb_b = _make_similar_embeddings()
        _embed_node(repo, node_a, project_id, emb_a)
        _embed_node(repo, node_b, project_id, emb_b)

        svc = ConflictService(repo)

        # Before dismiss, scan finds the pair
        candidates = svc.scan(project_id, threshold=0.9)
        assert len(candidates) == 1

        # Dismiss
        svc.dismiss(project_id, node_a, node_b)

        # After dismiss, scan skips the pair
        candidates = svc.scan(project_id, threshold=0.9)
        assert len(candidates) == 0


# ══════════════════════════════════════════════════════════════════════
# CLI smoke tests
# ══════════════════════════════════════════════════════════════════════


class TestConflictCLISmoke:
    """Smoke tests for conflict CLI commands."""

    def test_conflict_scan_no_candidates(self) -> None:
        """kgn conflict scan on empty project → no candidates."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", name])
        result = runner.invoke(app, ["conflict", "scan", "--project", name])
        assert result.exit_code == 0
        assert "no conflict" in result.output.lower()

    def test_conflict_approve_invalid_uuid(self) -> None:
        """kgn conflict approve with bad UUID → error."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", name])
        result = runner.invoke(
            app,
            ["conflict", "approve", "bad-uuid", "bad-uuid-2", "--project", name],
        )
        assert result.exit_code != 0
        assert "UUID" in result.output

    def test_conflict_dismiss_invalid_uuid(self) -> None:
        """kgn conflict dismiss with bad UUID → error."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", name])
        result = runner.invoke(
            app,
            ["conflict", "dismiss", "not-a-uuid", "also-not", "--project", name],
        )
        assert result.exit_code != 0

    def test_conflict_scan_missing_project(self) -> None:
        """kgn conflict scan on non-existent project → error."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["conflict", "scan", "--project", "nonexistent-xyz"])
        assert result.exit_code != 0
