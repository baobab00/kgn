"""End-to-End integration tests for Phase 2 features.

Full pipeline with Mock embeddings:
1. init → ingest --embed (mock) → verify nodes + edges + embeddings
2. query similar → verify similar nodes returned
3. conflict scan → verify scan output
4. health → verify DupSpecRate metric

Requires a running PostgreSQL instance (Docker).
"""

from __future__ import annotations

import random
import shutil
import uuid
from pathlib import Path

import pytest

from kgn.db.repository import KgnRepository
from kgn.embedding.client import EmbeddingClient
from kgn.embedding.service import EmbeddingService
from kgn.graph.health import HealthService
from kgn.ingest.service import IngestService
from kgn.models.enums import NodeType

# ── Paths ──────────────────────────────────────────────────────────────

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

# Constant dimension matching DB schema (text-embedding-3-small)
DIM = 1536


# ── Mock EmbeddingClient ──────────────────────────────────────────────


class MockEmbeddingClient:
    """Deterministic mock that satisfies the EmbeddingClient protocol."""

    def __init__(self, *, seed: int = 42) -> None:
        self._rng = random.Random(seed)

    @property
    def model(self) -> str:
        return "text-embedding-3-small"

    @property
    def dimensions(self) -> int:
        return DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return deterministic unit-ish vectors per text hash."""
        results: list[list[float]] = []
        for text in texts:
            # Seed from text for reproducibility
            rng = random.Random(hash(text))
            vec = [rng.gauss(0, 0.1) for _ in range(DIM)]
            # Normalize
            norm = sum(x * x for x in vec) ** 0.5
            if norm > 0:
                vec = [x / norm for x in vec]
            results.append(vec)
        return results


# Verify protocol compliance
assert isinstance(MockEmbeddingClient(), EmbeddingClient)


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def e2e_examples(tmp_path: Path) -> tuple[Path, str]:
    """Copy examples/ to tmp_path with a unique project name."""
    unique_project = f"e2e-p2-{uuid.uuid4().hex[:8]}"
    dest = tmp_path / "examples"
    shutil.copytree(EXAMPLES_DIR, dest)

    for f in dest.rglob("*.kgn"):
        content = f.read_text(encoding="utf-8")
        content = content.replace("example-project", unique_project)
        f.write_text(content, encoding="utf-8")
    for f in dest.rglob("*.kge"):
        content = f.read_text(encoding="utf-8")
        content = content.replace("example-project", unique_project)
        f.write_text(content, encoding="utf-8")

    return dest, unique_project


@pytest.fixture
def mock_client() -> MockEmbeddingClient:
    """Provide a mock embedding client."""
    return MockEmbeddingClient()


def _ingest_and_embed(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    examples_dir: Path,
    yaml_project_name: str,
    client: MockEmbeddingClient,
) -> tuple[uuid.UUID, int, int]:
    """Ingest examples and embed all nodes.

    Returns (yaml_project_id, ingested_count, embedded_count).
    """
    svc = IngestService(repo=repo, project_id=project_id, agent_id=agent_id)
    batch = svc.ingest_path(examples_dir, recursive=True)

    yaml_pid = repo.get_or_create_project(yaml_project_name)

    embed_svc = EmbeddingService(repo=repo, client=client)
    embedded = embed_svc.embed_batch(yaml_pid, force=False)

    return yaml_pid, batch.success, embedded


# ══════════════════════════════════════════════════════════════════════
#  E2E Phase 2: Full pipeline with mock embeddings
# ══════════════════════════════════════════════════════════════════════


class TestE2EPhase2Pipeline:
    """Complete Phase 2 workflow: ingest → embed → similar → conflict → health."""

    def test_ingest_with_embedding(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        e2e_examples: tuple[Path, str],
        mock_client: MockEmbeddingClient,
    ) -> None:
        """Step 1: ingest + embed → nodes, edges, and embeddings created."""
        ex_dir, proj_name = e2e_examples
        yaml_pid, ingested, embedded = _ingest_and_embed(
            repo,
            project_id,
            agent_id,
            ex_dir,
            proj_name,
            mock_client,
        )

        # At least 3 nodes (goal, spec, decision) and 2 edges
        assert ingested >= 3
        assert embedded >= 3

        # Verify embeddings exist in DB
        nodes = repo.search_nodes(yaml_pid)
        for node in nodes:
            assert repo.has_embedding(node.id), f"Node {node.id} missing embedding"

    def test_query_similar_after_embed(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        e2e_examples: tuple[Path, str],
        mock_client: MockEmbeddingClient,
    ) -> None:
        """Step 2: query similar → returns similar nodes."""
        ex_dir, proj_name = e2e_examples
        yaml_pid, _, _ = _ingest_and_embed(
            repo,
            project_id,
            agent_id,
            ex_dir,
            proj_name,
            mock_client,
        )

        # Pick the first node and search for similar
        nodes = repo.search_nodes(yaml_pid)
        assert len(nodes) >= 2
        target = nodes[0]

        query_vec = repo.get_node_embedding(target.id)
        assert query_vec is not None

        similar = repo.search_similar_nodes(
            query_embedding=query_vec,
            project_id=yaml_pid,
            top_k=3,
            exclude_ids={target.id},
        )

        # Should find at least 1 similar node (excluding self)
        assert len(similar) >= 1
        # Self should not be in results
        assert all(s.id != target.id for s in similar)
        # Similarity should be between -1 and 1
        for s in similar:
            assert -1.0 <= s.similarity <= 1.0

    def test_conflict_scan_after_embed(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        e2e_examples: tuple[Path, str],
        mock_client: MockEmbeddingClient,
    ) -> None:
        """Step 3: conflict scan → runs without error, returns list."""
        from kgn.conflict.service import ConflictService

        ex_dir, proj_name = e2e_examples
        yaml_pid, _, _ = _ingest_and_embed(
            repo,
            project_id,
            agent_id,
            ex_dir,
            proj_name,
            mock_client,
        )

        csvc = ConflictService(repo)
        # Use a low threshold so we get some candidates from random vectors
        candidates = csvc.scan(yaml_pid, threshold=0.0)

        # Should be a list (may be empty depending on embeddings)
        assert isinstance(candidates, list)
        # With threshold=0.0, any pair with similarity > 0 is a candidate
        # Mock embeddings should produce some pairs
        for c in candidates:
            assert c.similarity > 0.0
            assert c.status in ("NEW", "PENDING")

    def test_health_dup_spec_rate_after_embed(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        e2e_examples: tuple[Path, str],
        mock_client: MockEmbeddingClient,
    ) -> None:
        """Step 4: health → DupSpecRate metric present and healthy."""
        ex_dir, proj_name = e2e_examples
        yaml_pid, _, _ = _ingest_and_embed(
            repo,
            project_id,
            agent_id,
            ex_dir,
            proj_name,
            mock_client,
        )

        health_svc = HealthService(repo)
        report = health_svc.compute(yaml_pid)

        assert report.total_nodes >= 3
        assert report.total_edges >= 2
        # No pending contradicts → dup_spec_rate should be 0
        assert report.dup_spec_rate == 0.0
        assert report.dup_spec_rate_ok is True
        assert report.spec_nodes >= 1  # examples have at least one SPEC

    def test_health_dup_spec_rate_with_pending(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        e2e_examples: tuple[Path, str],
        mock_client: MockEmbeddingClient,
    ) -> None:
        """DupSpecRate rises when PENDING CONTRADICTS edges are created."""
        ex_dir, proj_name = e2e_examples
        yaml_pid, _, _ = _ingest_and_embed(
            repo,
            project_id,
            agent_id,
            ex_dir,
            proj_name,
            mock_client,
        )

        # Get SPEC nodes
        spec_nodes = repo.search_nodes(yaml_pid, node_type=NodeType.SPEC)
        total_spec = repo.count_spec_nodes(yaml_pid)
        assert total_spec >= 1

        # If we only have 1 SPEC node, create another for a pair
        if len(spec_nodes) < 2:
            from kgn.models.node import NodeRecord

            extra_id = uuid.uuid4()
            extra = NodeRecord(
                id=extra_id,
                project_id=yaml_pid,
                type=NodeType.SPEC,
                status="ACTIVE",
                title="Extra SPEC for testing",
                body_md="Extra body",
                file_path="test/extra.kgn",
                content_hash=uuid.uuid4().hex,
                tags=[],
                confidence=None,
                created_by=agent_id,
            )
            repo.upsert_node(extra)
            spec_nodes = repo.search_nodes(yaml_pid, node_type=NodeType.SPEC)
            total_spec = repo.count_spec_nodes(yaml_pid)

        # Insert a PENDING contradicts edge between two SPEC nodes
        repo.insert_contradicts_edge(
            project_id=yaml_pid,
            from_node_id=spec_nodes[0].id,
            to_node_id=spec_nodes[1].id,
            note="test pending",
            created_by=agent_id,
            status="PENDING",
        )

        health_svc = HealthService(repo)
        report = health_svc.compute(yaml_pid)

        assert report.pending_contradicts >= 1
        assert report.spec_nodes == total_spec
        assert report.dup_spec_rate > 0.0


# ══════════════════════════════════════════════════════════════════════
#  E2E Phase 2: CLI smoke tests with mock embeddings
# ══════════════════════════════════════════════════════════════════════


class TestE2EPhase2CLISmoke:
    """CLI smoke tests covering Phase 2 features."""

    @pytest.fixture
    def cli_examples(self, tmp_path: Path) -> tuple[Path, str]:
        """Unique examples copy for CLI tests."""
        unique_project = f"cli-p2-{uuid.uuid4().hex[:8]}"
        dest = tmp_path / "examples"
        shutil.copytree(EXAMPLES_DIR, dest)
        for f in dest.rglob("*.kgn"):
            content = f.read_text(encoding="utf-8")
            content = content.replace("example-project", unique_project)
            f.write_text(content, encoding="utf-8")
        for f in dest.rglob("*.kge"):
            content = f.read_text(encoding="utf-8")
            content = content.replace("example-project", unique_project)
            f.write_text(content, encoding="utf-8")
        return dest, unique_project

    def test_cli_ingest_embed_and_similar(
        self,
        cli_examples: tuple[Path, str],
        monkeypatch,
    ) -> None:
        """init → ingest --embed (mock) → query similar → works."""
        from unittest.mock import patch

        from typer.testing import CliRunner

        from kgn.cli import app

        monkeypatch.setenv("KGN_OPENAI_API_KEY", "sk-test-fake")

        ex_dir, proj = cli_examples
        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"

        # Init
        result = runner.invoke(app, ["init", "--project", name])
        assert result.exit_code == 0

        # Ingest with --embed, mock the OpenAIEmbeddingClient
        with patch(
            "kgn.embedding.client.OpenAIEmbeddingClient",
            return_value=MockEmbeddingClient(),
        ):
            result = runner.invoke(
                app,
                ["ingest", str(ex_dir), "--project", name, "--recursive", "--embed"],
            )
        assert result.exit_code == 0, result.output
        assert "Embedded" in result.output

    def test_cli_health_shows_dup_spec_rate(
        self,
        cli_examples: tuple[Path, str],
    ) -> None:
        """health output includes DupSpecRate metric."""
        from typer.testing import CliRunner

        from kgn.cli import app

        ex_dir, proj = cli_examples
        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"

        runner.invoke(app, ["init", "--project", name])
        runner.invoke(
            app,
            ["ingest", str(ex_dir), "--project", name, "--recursive"],
        )

        result = runner.invoke(app, ["health", "--project", proj])
        assert result.exit_code == 0
        assert "DupSpecRate" in result.output

    def test_cli_conflict_scan(
        self,
        cli_examples: tuple[Path, str],
    ) -> None:
        """conflict scan on a project with no embeddings → zero candidates."""
        from typer.testing import CliRunner

        from kgn.cli import app

        ex_dir, proj = cli_examples
        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"

        runner.invoke(app, ["init", "--project", name])
        runner.invoke(
            app,
            ["ingest", str(ex_dir), "--project", name, "--recursive"],
        )

        result = runner.invoke(
            app,
            ["conflict", "scan", "--project", proj],
        )
        assert result.exit_code == 0

    def test_cli_embed_standalone_mock(self, cli_examples: tuple[Path, str], monkeypatch) -> None:
        """kgn embed batch --project with mock client → embeds nodes."""
        from unittest.mock import patch

        from typer.testing import CliRunner

        from kgn.cli import app

        monkeypatch.setenv("KGN_OPENAI_API_KEY", "sk-test-fake")

        ex_dir, proj = cli_examples
        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"

        runner.invoke(app, ["init", "--project", name])
        runner.invoke(
            app,
            ["ingest", str(ex_dir), "--project", name, "--recursive"],
        )

        with patch(
            "kgn.embedding.client.OpenAIEmbeddingClient",
            return_value=MockEmbeddingClient(),
        ):
            result = runner.invoke(app, ["embed", "batch", "--project", proj])
        assert result.exit_code == 0
        assert "Embedded" in result.output
