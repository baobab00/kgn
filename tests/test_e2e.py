"""End-to-End integration tests for the full KGN pipeline (Step 9).

Scenarios:
1. init → ingest → query nodes → query subgraph → health
2. Duplicate ingest → SKIPPED
3. Modified file re-ingest → UPDATE + node_versions
4. CLI smoke tests via typer.testing.CliRunner

Requires a running PostgreSQL instance (Docker).
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.graph.health import HealthService
from kgn.graph.subgraph import SubgraphService
from kgn.ingest.service import IngestBatchResult, IngestService
from kgn.models.enums import NodeType

# ── Paths ──────────────────────────────────────────────────────────────

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ── Helpers ────────────────────────────────────────────────────────────


def _assert_batch(
    batch: IngestBatchResult,
    *,
    success: int,
    skipped: int = 0,
    failed: int = 0,
) -> None:
    assert batch.success == success, f"success: {batch.success} != {success}"
    assert batch.skipped == skipped, f"skipped: {batch.skipped} != {skipped}"
    assert batch.failed == failed, f"failed: {batch.failed} != {failed}"


def _make_service(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> IngestService:
    return IngestService(repo=repo, project_id=project_id, agent_id=agent_id)


# ── Fixture: unique examples copy ─────────────────────────────────────


@pytest.fixture
def e2e_examples(tmp_path: Path) -> tuple[Path, str]:
    """Copy examples/ to tmp_path with a unique ``project_id``.

    Avoids content_hash collisions between SAVEPOINT-isolated tests
    and committed data from prior test runs.

    Returns:
        (path_to_copied_dir, unique_project_name)
    """
    unique_project = f"e2e-{uuid.uuid4().hex[:8]}"
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


def _ingest(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    examples_dir: Path,
    yaml_project_name: str,
) -> tuple[IngestBatchResult, uuid.UUID]:
    """Ingest examples and return (batch, yaml_project_uuid)."""
    svc = _make_service(repo, project_id, agent_id)
    batch = svc.ingest_path(examples_dir, recursive=True)
    yaml_pid = repo.get_or_create_project(yaml_project_name)
    return batch, yaml_pid


# ══════════════════════════════════════════════════════════════════════
#  E2E: Full pipeline
# ══════════════════════════════════════════════════════════════════════


class TestE2EFullPipeline:
    """Complete workflow: ingest → query → subgraph → health."""

    def test_ingest_creates_nodes_and_edges(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        e2e_examples: tuple[Path, str],
    ) -> None:
        """Ingest examples/ recursively → 3 nodes + 2 edges."""
        ex_dir, proj_name = e2e_examples
        batch, yaml_pid = _ingest(repo, project_id, agent_id, ex_dir, proj_name)

        _assert_batch(batch, success=4)

        counts = repo.count_nodes(yaml_pid)
        assert counts.get("GOAL", 0) >= 1
        assert counts.get("SPEC", 0) >= 1
        assert counts.get("DECISION", 0) >= 1

        edge_counts = repo.count_edges(yaml_pid)
        assert sum(edge_counts.values()) >= 2

    def test_query_nodes_after_ingest(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        e2e_examples: tuple[Path, str],
    ) -> None:
        """After ingest, query nodes by type returns results."""
        ex_dir, proj_name = e2e_examples
        _, yaml_pid = _ingest(repo, project_id, agent_id, ex_dir, proj_name)

        spec_nodes = repo.search_nodes(yaml_pid, node_type=NodeType.SPEC)
        assert len(spec_nodes) >= 1
        assert all(n.type == NodeType.SPEC for n in spec_nodes)

        goal_nodes = repo.search_nodes(yaml_pid, node_type=NodeType.GOAL)
        assert len(goal_nodes) >= 1

    def test_subgraph_from_goal(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        e2e_examples: tuple[Path, str],
    ) -> None:
        """Subgraph from GOAL at depth 2 includes connected nodes."""
        ex_dir, proj_name = e2e_examples
        _, yaml_pid = _ingest(repo, project_id, agent_id, ex_dir, proj_name)

        goal_nodes = repo.search_nodes(yaml_pid, node_type=NodeType.GOAL)
        assert len(goal_nodes) >= 1
        goal_id = goal_nodes[0].id

        sg_svc = SubgraphService(repo)
        result = sg_svc.extract(root_id=goal_id, project_id=yaml_pid, depth=2)

        assert len(result.nodes) >= 1
        node_ids = {n.id for n in result.nodes}
        assert goal_id in node_ids
        assert len(result.edges) >= 1

    def test_subgraph_to_json(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        e2e_examples: tuple[Path, str],
    ) -> None:
        """Subgraph to_json produces valid JSON."""
        import json

        ex_dir, proj_name = e2e_examples
        _, yaml_pid = _ingest(repo, project_id, agent_id, ex_dir, proj_name)

        goal_nodes = repo.search_nodes(yaml_pid, node_type=NodeType.GOAL)
        goal_id = goal_nodes[0].id

        sg_svc = SubgraphService(repo)
        result = sg_svc.extract(root_id=goal_id, project_id=yaml_pid, depth=2)
        json_str = sg_svc.to_json(result)

        data = json.loads(json_str)
        assert "root_id" in data
        assert "nodes" in data
        assert "edges" in data

    def test_subgraph_to_markdown(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        e2e_examples: tuple[Path, str],
    ) -> None:
        """Subgraph to_markdown produces expected sections."""
        ex_dir, proj_name = e2e_examples
        _, yaml_pid = _ingest(repo, project_id, agent_id, ex_dir, proj_name)

        goal_nodes = repo.search_nodes(yaml_pid, node_type=NodeType.GOAL)
        goal_id = goal_nodes[0].id

        sg_svc = SubgraphService(repo)
        result = sg_svc.extract(root_id=goal_id, project_id=yaml_pid, depth=2)
        md = sg_svc.to_markdown(result)

        assert "# Subgraph" in md
        assert "## Depth 0" in md
        assert "## Edges" in md

    def test_health_after_ingest(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        e2e_examples: tuple[Path, str],
    ) -> None:
        """Health after ingest — no conflicts, no superseded stale."""
        ex_dir, proj_name = e2e_examples
        _, yaml_pid = _ingest(repo, project_id, agent_id, ex_dir, proj_name)

        health_svc = HealthService(repo)
        report = health_svc.compute(yaml_pid)

        assert report.total_nodes >= 3
        assert report.total_edges >= 2
        assert report.conflict_ok is True
        assert report.superseded_stale_ok is True


# ══════════════════════════════════════════════════════════════════════
#  E2E: Duplicate ingest → SKIPPED
# ══════════════════════════════════════════════════════════════════════


class TestE2EDuplicateIngest:
    """Re-ingesting the same files → SKIPPED (V8 content_hash)."""

    def test_duplicate_ingest_skipped(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        e2e_examples: tuple[Path, str],
    ) -> None:
        ex_dir, proj_name = e2e_examples
        batch1, _ = _ingest(repo, project_id, agent_id, ex_dir, proj_name)
        assert batch1.success >= 3

        # Re-ingest with a fresh IngestService
        svc2 = _make_service(repo, project_id, agent_id)
        batch2 = svc2.ingest_path(ex_dir, recursive=True)

        assert batch2.skipped >= 3


# ══════════════════════════════════════════════════════════════════════
#  E2E: Modified file → UPDATE + node_versions
# ══════════════════════════════════════════════════════════════════════


class TestE2EModifiedIngest:
    """Modified file re-ingest produces UPDATE and creates node_versions."""

    def test_modified_file_updates_node(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
        db_conn: Connection,
    ) -> None:
        # Create a temp .kgn file with a FIXED UUID
        fixed_uuid = str(uuid.uuid4())
        proj_name = f"mod-test-{uuid.uuid4().hex[:6]}"
        original_content = (
            "---\n"
            'kgn_version: "0.1"\n'
            f'id: "{fixed_uuid}"\n'
            "type: SPEC\n"
            'title: "Modifiable Node"\n'
            "status: ACTIVE\n"
            f'project_id: "{proj_name}"\n'
            'agent_id: "test-agent"\n'
            'created_at: "2026-02-28T09:00:00+09:00"\n'
            "tags: []\n"
            "---\n\n"
            "## Context\n\nOriginal content.\n\n"
            "## Content\n\nSome spec body.\n"
        )
        dest = tmp_path / "modifiable.kgn"
        dest.write_text(original_content, encoding="utf-8")

        # First ingest → CREATED
        svc1 = _make_service(repo, project_id, agent_id)
        batch1 = svc1.ingest_path(dest)
        _assert_batch(batch1, success=1)
        node_id = batch1.details[0].node_id
        assert node_id is not None

        # Modify the file (changes content_hash)
        modified_content = original_content.replace(
            "Some spec body.",
            "Some spec body.\n- Additional detail added.",
        )
        dest.write_text(modified_content, encoding="utf-8")

        # Re-ingest → same UUID, different hash → UPDATE
        svc2 = _make_service(repo, project_id, agent_id)
        batch2 = svc2.ingest_path(dest)
        _assert_batch(batch2, success=1)

        # Verify node_versions record was created
        row = db_conn.execute(
            "SELECT COUNT(*) FROM node_versions WHERE node_id = %s",
            (node_id,),
        ).fetchone()
        assert row is not None
        assert row[0] >= 1, "Expected at least one version record"


# ══════════════════════════════════════════════════════════════════════
#  E2E: Empty project handling
# ══════════════════════════════════════════════════════════════════════


class TestE2EEmptyProject:
    """Operations on an empty project should not error."""

    def test_health_empty_project(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        health_svc = HealthService(repo)
        report = health_svc.compute(project_id)

        assert report.total_nodes == 0
        assert report.total_edges == 0
        assert report.orphan_rate == 0.0
        assert report.orphan_rate_ok is True

    def test_search_nodes_empty_project(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        nodes = repo.search_nodes(project_id, node_type=NodeType.SPEC)
        assert nodes == []

    def test_count_nodes_empty_project(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        counts = repo.count_nodes(project_id)
        assert sum(counts.values()) == 0


# ══════════════════════════════════════════════════════════════════════
#  CLI smoke tests via CliRunner
# ══════════════════════════════════════════════════════════════════════


class TestCLISmoke:
    """Smoke tests exercising CLI commands through typer CliRunner."""

    @pytest.fixture
    def cli_examples(self, tmp_path: Path) -> tuple[Path, str]:
        """Unique examples copy for CLI tests (commits to DB)."""
        unique_project = f"cli-ex-{uuid.uuid4().hex[:8]}"
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

    def test_cli_init(self) -> None:
        """kgn init --project creates a project."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"
        result = runner.invoke(app, ["init", "--project", name])
        assert result.exit_code == 0
        assert "Init complete" in result.output

    def test_cli_ingest(self, cli_examples: tuple[Path, str]) -> None:
        """kgn ingest examples/ --project ... succeeds."""
        from typer.testing import CliRunner

        from kgn.cli import app

        ex_dir, _ = cli_examples
        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", name])
        result = runner.invoke(
            app,
            ["ingest", str(ex_dir), "--project", name, "--recursive"],
        )
        assert result.exit_code == 0

    def test_cli_status(self, cli_examples: tuple[Path, str]) -> None:
        """kgn status --project shows project info."""
        from typer.testing import CliRunner

        from kgn.cli import app

        ex_dir, _ = cli_examples
        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", name])
        runner.invoke(
            app,
            ["ingest", str(ex_dir), "--project", name, "--recursive"],
        )
        result = runner.invoke(app, ["status", "--project", name])
        assert result.exit_code == 0

    def test_cli_query_nodes(self, cli_examples: tuple[Path, str]) -> None:
        """kgn query nodes --project works."""
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
        # query against the YAML project (where nodes actually reside)
        result = runner.invoke(
            app,
            ["query", "nodes", "--project", proj, "--type", "SPEC"],
        )
        assert result.exit_code == 0

    def test_cli_health(self, cli_examples: tuple[Path, str]) -> None:
        """kgn health --project works."""
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

    def test_cli_health_missing_project(self) -> None:
        """kgn health on non-existent project → error exit."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["health", "--project", "nonexistent-xyz"])
        assert result.exit_code != 0

    def test_cli_ingest_nonexistent_path(self) -> None:
        """kgn ingest /nonexistent → error exit."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", name])
        result = runner.invoke(
            app,
            ["ingest", "/nonexistent/path", "--project", name],
        )
        assert result.exit_code != 0

    def test_cli_query_similar_no_embedding(self, cli_examples: tuple[Path, str]) -> None:
        """kgn query similar on a node without embedding → error."""
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        ex_dir, proj = cli_examples
        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", name])
        runner.invoke(
            app,
            ["ingest", str(ex_dir), "--project", name, "--recursive"],
        )

        # Get a real node id from the YAML project
        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_project_by_name(proj)
            nodes = repo.search_nodes(pid)
            assert nodes, "Expected at least one ingested node"
            node_id = str(nodes[0].id)

        result = runner.invoke(
            app,
            ["query", "similar", node_id, "--project", proj],
        )
        assert result.exit_code != 0
        assert "no embedding" in result.output.lower()

    def test_cli_query_similar_invalid_uuid(self) -> None:
        """kgn query similar with bad UUID → error exit."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        name = f"cli-test-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", name])
        result = runner.invoke(
            app,
            ["query", "similar", "not-a-uuid", "--project", name],
        )
        assert result.exit_code != 0
        assert "UUID" in result.output
