"""Tests for ingest --embed and kgn embed command (Phase 2, Step 7).

Test categories:
- IngestBatchResult.mutated_node_ids property
- CLI smoke: ingest --embed, kgn embed
"""

from __future__ import annotations

import uuid
from pathlib import Path

from kgn.ingest.service import IngestBatchResult, IngestFileResult

# ══════════════════════════════════════════════════════════════════════
# IngestBatchResult.mutated_node_ids
# ══════════════════════════════════════════════════════════════════════


class TestMutatedNodeIds:
    """Tests for IngestBatchResult.mutated_node_ids property."""

    def test_returns_success_node_ids(self) -> None:
        batch = IngestBatchResult()
        id1 = uuid.uuid4()
        id2 = uuid.uuid4()
        batch.add(IngestFileResult(file_path="a.kgn", status="SUCCESS", node_id=id1))
        batch.add(IngestFileResult(file_path="b.kgn", status="SUCCESS", node_id=id2))
        assert batch.mutated_node_ids == [id1, id2]

    def test_excludes_skipped(self) -> None:
        batch = IngestBatchResult()
        id1 = uuid.uuid4()
        id_skip = uuid.uuid4()
        batch.add(IngestFileResult(file_path="a.kgn", status="SUCCESS", node_id=id1))
        batch.add(IngestFileResult(file_path="b.kgn", status="SKIPPED", node_id=id_skip))
        assert batch.mutated_node_ids == [id1]

    def test_excludes_failed(self) -> None:
        batch = IngestBatchResult()
        batch.add(IngestFileResult(file_path="a.kgn", status="FAILED", error="bad"))
        assert batch.mutated_node_ids == []

    def test_excludes_none_node_id(self) -> None:
        batch = IngestBatchResult()
        batch.add(IngestFileResult(file_path="a.kge", status="SUCCESS", node_id=None))
        assert batch.mutated_node_ids == []

    def test_empty_batch(self) -> None:
        batch = IngestBatchResult()
        assert batch.mutated_node_ids == []


# ══════════════════════════════════════════════════════════════════════
# CLI smoke tests — ingest --embed
# ══════════════════════════════════════════════════════════════════════


class TestIngestEmbedCLI:
    """Smoke tests for kgn ingest --embed."""

    def test_ingest_without_embed_backward_compat(self, tmp_path: Path) -> None:
        """kgn ingest without --embed should work as before (no API key needed)."""
        import shutil

        from typer.testing import CliRunner

        from kgn.cli import app

        examples = Path(__file__).resolve().parent.parent / "examples"
        dest = tmp_path / "examples"
        shutil.copytree(examples, dest)
        proj = f"test-{uuid.uuid4().hex[:8]}"
        for f in dest.rglob("*.kgn"):
            content = f.read_text(encoding="utf-8")
            f.write_text(content.replace("example-project", proj), encoding="utf-8")
        for f in dest.rglob("*.kge"):
            content = f.read_text(encoding="utf-8")
            f.write_text(content.replace("example-project", proj), encoding="utf-8")

        runner = CliRunner()
        runner.invoke(app, ["init", "--project", proj])
        result = runner.invoke(
            app,
            ["ingest", str(dest), "--project", proj, "--recursive"],
        )
        assert result.exit_code == 0
        # Should NOT contain Embedded line
        assert "Embedded" not in result.output

    def test_ingest_embed_no_api_key(self, tmp_path: Path, monkeypatch) -> None:
        """kgn ingest --embed without KGN_OPENAI_API_KEY → error."""
        from typer.testing import CliRunner

        from kgn.cli import app

        monkeypatch.delenv("KGN_OPENAI_API_KEY", raising=False)

        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", name])
        # load_dotenv may re-set the key from .env; ensure it's removed
        monkeypatch.delenv("KGN_OPENAI_API_KEY", raising=False)
        result = runner.invoke(
            app,
            ["ingest", str(tmp_path), "--project", name, "--embed"],
        )
        assert result.exit_code != 0
        assert "KGN_OPENAI_API_KEY" in result.output


# ══════════════════════════════════════════════════════════════════════
# CLI smoke tests — kgn embed
# ══════════════════════════════════════════════════════════════════════


class TestEmbedCLI:
    """Smoke tests for kgn embed command."""

    def test_embed_no_api_key(self, monkeypatch) -> None:
        """kgn embed batch without KGN_OPENAI_API_KEY → error."""
        from typer.testing import CliRunner

        from kgn.cli import app

        monkeypatch.delenv("KGN_OPENAI_API_KEY", raising=False)

        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", name])
        # load_dotenv may re-set the key from .env; ensure it's removed
        monkeypatch.delenv("KGN_OPENAI_API_KEY", raising=False)
        result = runner.invoke(app, ["embed", "batch", "--project", name])
        assert result.exit_code != 0
        assert "KGN_OPENAI_API_KEY" in result.output

    def test_embed_missing_project(self, monkeypatch) -> None:
        """kgn embed batch on non-existent project → error."""
        from typer.testing import CliRunner

        from kgn.cli import app

        monkeypatch.setenv("KGN_OPENAI_API_KEY", "sk-test-fake")

        runner = CliRunner()
        result = runner.invoke(app, ["embed", "batch", "--project", "nonexistent-xyz-abc"])
        assert result.exit_code != 0
