"""Integration tests for embedding service.

Tests cover:
- Repository embedding methods (upsert, has, get_unembedded, get_node_embedding)
- EmbeddingService with a mock client (embed_node, embed_batch, embed_nodes)
- EmbeddingClient protocol compliance
- Dimension mismatch warning

Requires a running PostgreSQL instance (Docker on port 5433).
"""

from __future__ import annotations

import uuid

import pytest

from kgn.db.repository import KgnRepository
from kgn.embedding.client import EmbeddingClient
from kgn.embedding.service import EmbeddingService
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord
from tests.helpers import EMBEDDING_DIMS, MockEmbeddingClient

# ── Helpers ────────────────────────────────────────────────────────────

DIMS = EMBEDDING_DIMS


def _dummy_vector(seed: float = 0.1) -> list[float]:
    """Return a 1536-dim dummy vector."""
    return [seed] * DIMS


def _make_node(
    project_id: uuid.UUID,
    *,
    node_id: uuid.UUID | None = None,
    title: str = "Test Node",
    body: str = "## Context\n\ntest body",
    node_type: NodeType = NodeType.SPEC,
) -> NodeRecord:
    return NodeRecord(
        id=node_id or uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md=body,
        content_hash=uuid.uuid4().hex,
    )


# ── Protocol compliance ───────────────────────────────────────────────


class TestEmbeddingClientProtocol:
    """Verify MockEmbeddingClient satisfies the Protocol."""

    def test_mock_is_embedding_client(self) -> None:
        client = MockEmbeddingClient()
        assert isinstance(client, EmbeddingClient)

    def test_mock_embed_returns_correct_shape(self) -> None:
        client = MockEmbeddingClient()
        result = client.embed(["hello", "world"])
        assert len(result) == 2
        assert all(len(v) == DIMS for v in result)

    def test_mock_model_property(self) -> None:
        client = MockEmbeddingClient(model="custom-model")
        assert client.model == "custom-model"

    def test_mock_dimensions_property(self) -> None:
        client = MockEmbeddingClient(dimensions=768)
        assert client.dimensions == 768


# ── Repository embedding methods ──────────────────────────────────────


class TestRepositoryEmbedding:
    """DB integration tests for embedding repository methods."""

    def test_upsert_embedding_insert(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)

        vec = _dummy_vector(0.1)
        repo.upsert_embedding(node.id, project_id, vec, "test-model")

        assert repo.has_embedding(node.id)

    def test_upsert_embedding_update(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)

        repo.upsert_embedding(node.id, project_id, _dummy_vector(0.1), "model-v1")
        repo.upsert_embedding(node.id, project_id, _dummy_vector(0.2), "model-v2")

        # Should still have exactly one embedding
        assert repo.has_embedding(node.id)
        vec = repo.get_node_embedding(node.id)
        assert vec is not None
        assert abs(vec[0] - 0.2) < 1e-6

    def test_has_embedding_false(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)
        assert not repo.has_embedding(node.id)

    def test_has_embedding_nonexistent_node(self, repo: KgnRepository) -> None:
        assert not repo.has_embedding(uuid.uuid4())

    def test_get_unembedded_nodes_all(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        n1 = _make_node(project_id, title="Node A")
        n2 = _make_node(project_id, title="Node B")
        repo.upsert_node(n1)
        repo.upsert_node(n2)

        unembedded = repo.get_unembedded_nodes(project_id)
        ids = {r["id"] for r in unembedded}
        assert n1.id in ids
        assert n2.id in ids

    def test_get_unembedded_nodes_partial(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        n1 = _make_node(project_id, title="Embedded")
        n2 = _make_node(project_id, title="Not Embedded")
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        repo.upsert_embedding(n1.id, project_id, _dummy_vector(), "test-model")

        unembedded = repo.get_unembedded_nodes(project_id)
        ids = {r["id"] for r in unembedded}
        assert n1.id not in ids
        assert n2.id in ids

    def test_get_unembedded_excludes_archived(
        self, repo: KgnRepository, project_id: uuid.UUID
    ) -> None:
        node = _make_node(project_id, title="Archived Node")
        node_record = NodeRecord(
            id=node.id,
            project_id=project_id,
            type=NodeType.SPEC,
            status=NodeStatus.ARCHIVED,
            title="Archived Node",
            body_md="test",
            content_hash=uuid.uuid4().hex,
        )
        repo.upsert_node(node_record)

        unembedded = repo.get_unembedded_nodes(project_id)
        ids = {r["id"] for r in unembedded}
        assert node.id not in ids

    def test_get_node_embedding(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        node = _make_node(project_id)
        repo.upsert_node(node)

        vec = _dummy_vector(0.42)
        repo.upsert_embedding(node.id, project_id, vec, "test-model")

        result = repo.get_node_embedding(node.id)
        assert result is not None
        assert len(result) == DIMS
        assert abs(result[0] - 0.42) < 1e-4

    def test_get_node_embedding_none(self, repo: KgnRepository) -> None:
        assert repo.get_node_embedding(uuid.uuid4()) is None

    def test_get_unembedded_empty_project(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        assert repo.get_unembedded_nodes(project_id) == []


# ── EmbeddingService ──────────────────────────────────────────────────


class TestEmbeddingServiceEmbedNode:
    """Tests for EmbeddingService.embed_node()."""

    def test_embed_single_node(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        node = _make_node(project_id, title="My Title", body="Some body text")
        repo.upsert_node(node)

        client = MockEmbeddingClient()
        svc = EmbeddingService(repo, client)
        svc.embed_node(node.id, project_id)

        assert repo.has_embedding(node.id)
        assert client.call_count == 1
        assert "My Title" in client.last_texts[0]
        assert "Some body text" in client.last_texts[0]

    def test_embed_node_not_found(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        client = MockEmbeddingClient()
        svc = EmbeddingService(repo, client)

        with pytest.raises(ValueError, match="not found"):
            svc.embed_node(uuid.uuid4(), project_id)

    def test_embed_node_title_only(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        """Node with empty body should still embed (title only)."""
        node = _make_node(project_id, title="Title Only", body="")
        repo.upsert_node(node)

        client = MockEmbeddingClient()
        svc = EmbeddingService(repo, client)
        svc.embed_node(node.id, project_id)

        assert repo.has_embedding(node.id)
        assert client.last_texts[0] == "Title Only"

    def test_embed_node_updates_existing(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        """Re-embedding should update the vector."""
        node = _make_node(project_id)
        repo.upsert_node(node)

        client1 = MockEmbeddingClient(seed=1)
        svc1 = EmbeddingService(repo, client1)
        svc1.embed_node(node.id, project_id)
        vec1 = repo.get_node_embedding(node.id)

        client2 = MockEmbeddingClient(seed=99)
        svc2 = EmbeddingService(repo, client2)
        svc2.embed_node(node.id, project_id)

        vec2 = repo.get_node_embedding(node.id)
        assert vec2 is not None
        assert vec1 != vec2  # Re-embedding should update the vector


class TestEmbeddingServiceEmbedBatch:
    """Tests for EmbeddingService.embed_batch()."""

    def test_embed_batch_all(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        nodes = [_make_node(project_id, title=f"Node {i}") for i in range(3)]
        for n in nodes:
            repo.upsert_node(n)

        client = MockEmbeddingClient()
        svc = EmbeddingService(repo, client)
        count = svc.embed_batch(project_id)

        assert count == 3
        for n in nodes:
            assert repo.has_embedding(n.id)

    def test_embed_batch_skips_embedded(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        n1 = _make_node(project_id, title="Already embedded")
        n2 = _make_node(project_id, title="Not embedded")
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        repo.upsert_embedding(n1.id, project_id, _dummy_vector(), "pre-model")

        client = MockEmbeddingClient()
        svc = EmbeddingService(repo, client)
        count = svc.embed_batch(project_id)

        assert count == 1  # Only n2

    def test_embed_batch_force(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        n1 = _make_node(project_id, title="Already embedded")
        n2 = _make_node(project_id, title="Not embedded")
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        repo.upsert_embedding(n1.id, project_id, _dummy_vector(0.1), "old-model")

        client = MockEmbeddingClient(seed=8)
        svc = EmbeddingService(repo, client)
        count = svc.embed_batch(project_id, force=True)

        assert count == 2  # Both re-embedded

    def test_embed_batch_empty(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        client = MockEmbeddingClient()
        svc = EmbeddingService(repo, client)
        count = svc.embed_batch(project_id)

        assert count == 0
        assert client.call_count == 0

    def test_embed_batch_respects_batch_size(
        self, repo: KgnRepository, project_id: uuid.UUID
    ) -> None:
        """With batch_size=2, 5 nodes should result in 3 API calls."""
        for i in range(5):
            repo.upsert_node(_make_node(project_id, title=f"Node {i}"))

        client = MockEmbeddingClient()
        svc = EmbeddingService(repo, client, batch_size=2)
        count = svc.embed_batch(project_id)

        assert count == 5
        assert client.call_count == 3  # ceil(5/2) = 3

    def test_embed_batch_skips_archived(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        active = _make_node(project_id, title="Active")
        archived = NodeRecord(
            id=uuid.uuid4(),
            project_id=project_id,
            type=NodeType.SPEC,
            status=NodeStatus.ARCHIVED,
            title="Archived",
            body_md="test",
            content_hash=uuid.uuid4().hex,
        )
        repo.upsert_node(active)
        repo.upsert_node(archived)

        client = MockEmbeddingClient()
        svc = EmbeddingService(repo, client)
        count = svc.embed_batch(project_id)

        assert count == 1
        assert repo.has_embedding(active.id)
        assert not repo.has_embedding(archived.id)


class TestEmbeddingServiceEmbedNodes:
    """Tests for EmbeddingService.embed_nodes()."""

    def test_embed_specific_nodes(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        n1 = _make_node(project_id, title="Node 1")
        n2 = _make_node(project_id, title="Node 2")
        n3 = _make_node(project_id, title="Node 3")
        repo.upsert_node(n1)
        repo.upsert_node(n2)
        repo.upsert_node(n3)

        client = MockEmbeddingClient()
        svc = EmbeddingService(repo, client)
        count = svc.embed_nodes([n1.id, n3.id], project_id)

        assert count == 2
        assert repo.has_embedding(n1.id)
        assert not repo.has_embedding(n2.id)
        assert repo.has_embedding(n3.id)

    def test_embed_nodes_empty_list(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        client = MockEmbeddingClient()
        svc = EmbeddingService(repo, client)
        count = svc.embed_nodes([], project_id)

        assert count == 0
        assert client.call_count == 0

    def test_embed_nodes_nonexistent_ids(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        """Non-existent node IDs should be silently skipped."""
        client = MockEmbeddingClient()
        svc = EmbeddingService(repo, client)
        count = svc.embed_nodes([uuid.uuid4(), uuid.uuid4()], project_id)

        assert count == 0

    def test_embed_nodes_skips_archived(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        active = _make_node(project_id, title="Active")
        archived = NodeRecord(
            id=uuid.uuid4(),
            project_id=project_id,
            type=NodeType.SPEC,
            status=NodeStatus.ARCHIVED,
            title="Archived",
            body_md="test",
            content_hash=uuid.uuid4().hex,
        )
        repo.upsert_node(active)
        repo.upsert_node(archived)

        client = MockEmbeddingClient()
        svc = EmbeddingService(repo, client)
        count = svc.embed_nodes([active.id, archived.id], project_id)

        assert count == 1


# ── Build text helper ─────────────────────────────────────────────────


class TestBuildText:
    """Tests for the _build_text static method."""

    def test_title_and_body(self) -> None:
        text = EmbeddingService._build_text("Title", "Body content")
        assert text == "Title\n\nBody content"

    def test_title_only(self) -> None:
        text = EmbeddingService._build_text("Title", "")
        assert text == "Title"

    def test_whitespace_body(self) -> None:
        text = EmbeddingService._build_text("Title", "   \n  ")
        assert text == "Title"


# ── Dimension warning ─────────────────────────────────────────────────


class TestDimensionWarning:
    """Test that dimension mismatch produces a warning."""

    def test_matching_dimensions_no_warning(
        self, repo: KgnRepository, capsys: pytest.CaptureFixture
    ) -> None:
        client = MockEmbeddingClient(model="text-embedding-3-small", dimensions=1536)
        EmbeddingService(repo, client)
        captured = capsys.readouterr()
        assert "Warning" not in captured.out

    def test_mismatched_dimensions_warning(
        self, repo: KgnRepository, capsys: pytest.CaptureFixture
    ) -> None:
        client = MockEmbeddingClient(model="text-embedding-3-large", dimensions=3072)
        EmbeddingService(repo, client)
        captured = capsys.readouterr()
        assert "Warning" in captured.out or "3072" in captured.out

    def test_unknown_model_warning(
        self, repo: KgnRepository, capsys: pytest.CaptureFixture
    ) -> None:
        client = MockEmbeddingClient(model="unknown-model-xyz", dimensions=999)
        EmbeddingService(repo, client)
        captured = capsys.readouterr()
        assert "Warning" in captured.out or "unknown" in captured.out.lower()
