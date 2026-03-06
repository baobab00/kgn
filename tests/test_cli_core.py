"""CLI tests for core commands: init, status, query, conflict, health, embed, version.

Covers previously untested paths to raise cli.py coverage.
Requires a running PostgreSQL instance (Docker on port 5433).
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from typer.testing import CliRunner

from kgn.cli import app
from kgn.cli._core import _ensure_env_file
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord

runner = CliRunner()


# ── Helpers ────────────────────────────────────────────────────────────


def _init_project(name: str) -> None:
    runner.invoke(app, ["init", "--project", name])


def _make_node(
    project_id: uuid.UUID,
    *,
    node_type: NodeType = NodeType.SPEC,
    title: str = "Test node",
    body: str = "## Context\n\nBody text",
    tags: list[str] | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md=body,
        content_hash=uuid.uuid4().hex,
        tags=tags or ["test"],
    )


# ══════════════════════════════════════════════════════════════════════
# _ensure_env_file
# ══════════════════════════════════════════════════════════════════════


class TestEnsureEnvFile:
    def test_creates_env_when_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = _ensure_env_file()
        assert result == tmp_path / ".env"
        content = result.read_text(encoding="utf-8")
        assert "KGN_DB_PORT=5433" in content
        assert "KGN_DB_PASSWORD=kgn_dev_password" in content

    def test_returns_none_when_all_keys_present(self, tmp_path: Path, monkeypatch) -> None:
        env = tmp_path / ".env"
        env.write_text(
            "KGN_DB_HOST=h\nKGN_DB_PORT=1\nKGN_DB_NAME=d\nKGN_DB_USER=u\nKGN_DB_PASSWORD=p\n"
        )
        monkeypatch.chdir(tmp_path)
        assert _ensure_env_file() is None

    def test_appends_missing_keys(self, tmp_path: Path, monkeypatch) -> None:
        env = tmp_path / ".env"
        env.write_text("KGN_DB_HOST=localhost\nKGN_DB_PORT=5433\n")
        monkeypatch.chdir(tmp_path)
        result = _ensure_env_file()
        assert result == env
        content = env.read_text(encoding="utf-8")
        assert "# Added by kgn init" in content
        assert "KGN_DB_PASSWORD" in content
        assert "KGN_DB_NAME" in content
        assert "KGN_DB_USER" in content


# ══════════════════════════════════════════════════════════════════════
# Version callback
# ══════════════════════════════════════════════════════════════════════


class TestVersionCallback:
    def test_version_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "kgn" in result.output


# ══════════════════════════════════════════════════════════════════════
# Init
# ══════════════════════════════════════════════════════════════════════


class TestInitCLI:
    def test_init_creates_project(self) -> None:
        proj = f"cli-init-{uuid.uuid4().hex[:8]}"
        result = runner.invoke(app, ["init", "--project", proj])
        assert result.exit_code == 0
        assert "Init complete" in result.output

    def test_init_existing_project(self) -> None:
        proj = f"cli-init-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", proj])
        result = runner.invoke(app, ["init", "--project", proj])
        assert result.exit_code == 0
        assert "already exists" in result.output


# ══════════════════════════════════════════════════════════════════════
# Status
# ══════════════════════════════════════════════════════════════════════


class TestStatusCLI:
    def test_status_shows_counts(self) -> None:
        proj = f"cli-status-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        result = runner.invoke(app, ["status", "--project", proj])
        assert result.exit_code == 0
        assert "Nodes:" in result.output

    def test_status_project_not_found(self) -> None:
        result = runner.invoke(app, ["status", "--project", "nonexistent-xyz-999"])
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# Query Nodes
# ══════════════════════════════════════════════════════════════════════


class TestQueryNodesCLI:
    def test_query_nodes_empty(self) -> None:
        proj = f"cli-qn-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        result = runner.invoke(app, ["query", "nodes", "--project", proj])
        assert result.exit_code == 0
        assert "No nodes found" in result.output

    def test_query_nodes_with_data(self) -> None:
        proj = f"cli-qn-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        # Ingest a spec file to create node
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)
            node = _make_node(pid)
            repo.upsert_node(node)
            conn.commit()

        result = runner.invoke(app, ["query", "nodes", "--project", proj])
        assert result.exit_code == 0
        assert "Test node" in result.output

    def test_query_nodes_with_type_filter(self) -> None:
        proj = f"cli-qn-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)
            node = _make_node(pid, node_type=NodeType.GOAL, title="My Goal")
            repo.upsert_node(node)
            conn.commit()

        result = runner.invoke(app, ["query", "nodes", "--project", proj, "--type", "GOAL"])
        assert result.exit_code == 0
        assert "My Goal" in result.output

    def test_query_nodes_project_not_found(self) -> None:
        result = runner.invoke(app, ["query", "nodes", "--project", "nonexistent-xyz-999"])
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# Query Subgraph
# ══════════════════════════════════════════════════════════════════════


class TestQuerySubgraphCLI:
    def test_query_subgraph_invalid_uuid(self) -> None:
        result = runner.invoke(app, ["query", "subgraph", "not-a-uuid", "--project", "any"])
        assert result.exit_code == 1
        assert "Invalid UUID" in result.output

    def test_query_subgraph_empty(self) -> None:
        proj = f"cli-qsg-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        fake_id = str(uuid.uuid4())
        result = runner.invoke(app, ["query", "subgraph", fake_id, "--project", proj])
        assert result.exit_code == 0
        assert "No nodes found" in result.output

    def test_query_subgraph_json_format(self) -> None:
        proj = f"cli-qsg-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)
            node = _make_node(pid, title="Root Node")
            repo.upsert_node(node)
            conn.commit()

        result = runner.invoke(
            app,
            ["query", "subgraph", str(node.id), "--project", proj, "--format", "json"],
        )
        assert result.exit_code == 0

    def test_query_subgraph_table_format(self) -> None:
        proj = f"cli-qsg-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)
            node = _make_node(pid, title="Table Node")
            repo.upsert_node(node)
            conn.commit()

        result = runner.invoke(
            app,
            ["query", "subgraph", str(node.id), "--project", proj],
        )
        assert result.exit_code == 0

    def test_query_subgraph_md_format(self) -> None:
        proj = f"cli-qsg-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)
            node = _make_node(pid, title="MD Node")
            repo.upsert_node(node)
            conn.commit()

        result = runner.invoke(
            app,
            ["query", "subgraph", str(node.id), "--project", proj, "--format", "md"],
        )
        assert result.exit_code == 0

    def test_query_subgraph_project_not_found(self) -> None:
        fake_id = str(uuid.uuid4())
        result = runner.invoke(
            app, ["query", "subgraph", fake_id, "--project", "nonexistent-xyz-999"]
        )
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# Query Similar
# ══════════════════════════════════════════════════════════════════════


class TestQuerySimilarCLI:
    def test_query_similar_invalid_uuid(self) -> None:
        result = runner.invoke(app, ["query", "similar", "bad-uuid", "--project", "any"])
        assert result.exit_code == 1
        assert "Invalid UUID" in result.output

    def test_query_similar_no_embedding(self) -> None:
        proj = f"cli-qs-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)
            node = _make_node(pid)
            repo.upsert_node(node)
            conn.commit()

        result = runner.invoke(app, ["query", "similar", str(node.id), "--project", proj])
        assert result.exit_code == 1
        assert "no embedding" in result.output.lower()

    def test_query_similar_with_embedding(self) -> None:
        """When embedding exists but no similar nodes, prints empty."""
        proj = f"cli-qs-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)
            node = _make_node(pid)
            repo.upsert_node(node)
            # Store dummy embedding
            embedding = [0.1] * 1536
            repo.upsert_embedding(node.id, pid, embedding, model="test")
            conn.commit()

        result = runner.invoke(app, ["query", "similar", str(node.id), "--project", proj])
        assert result.exit_code == 0
        assert "No similar nodes" in result.output

    def test_query_similar_with_results(self) -> None:
        """Two nodes with same embedding → similarity result."""
        proj = f"cli-qs-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)
            node_a = _make_node(pid, title="Node A")
            node_b = _make_node(pid, title="Node B")
            repo.upsert_node(node_a)
            repo.upsert_node(node_b)
            embedding = [0.1] * 1536
            repo.upsert_embedding(node_a.id, pid, embedding, model="test")
            repo.upsert_embedding(node_b.id, pid, embedding, model="test")
            conn.commit()

        result = runner.invoke(app, ["query", "similar", str(node_a.id), "--project", proj])
        assert result.exit_code == 0
        assert "Node B" in result.output


# ══════════════════════════════════════════════════════════════════════
# Conflict commands
# ══════════════════════════════════════════════════════════════════════


class TestConflictCLI:
    def test_conflict_scan_empty(self) -> None:
        proj = f"cli-cf-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        result = runner.invoke(app, ["conflict", "scan", "--project", proj])
        assert result.exit_code == 0
        assert "No conflict candidates" in result.output

    def test_conflict_scan_project_not_found(self) -> None:
        result = runner.invoke(app, ["conflict", "scan", "--project", "nonexistent-xyz-999"])
        assert result.exit_code == 1

    def test_conflict_approve_invalid_uuid(self) -> None:
        result = runner.invoke(app, ["conflict", "approve", "bad", "bad2", "--project", "any"])
        assert result.exit_code == 1
        assert "Invalid UUID" in result.output

    def test_conflict_dismiss_invalid_uuid(self) -> None:
        result = runner.invoke(app, ["conflict", "dismiss", "bad", "bad2", "--project", "any"])
        assert result.exit_code == 1
        assert "Invalid UUID" in result.output

    def test_conflict_approve_project_not_found(self) -> None:
        id_a = str(uuid.uuid4())
        id_b = str(uuid.uuid4())
        result = runner.invoke(
            app, ["conflict", "approve", id_a, id_b, "--project", "nonexistent-xyz"]
        )
        assert result.exit_code == 1

    def test_conflict_dismiss_project_not_found(self) -> None:
        id_a = str(uuid.uuid4())
        id_b = str(uuid.uuid4())
        result = runner.invoke(
            app, ["conflict", "dismiss", id_a, id_b, "--project", "nonexistent-xyz"]
        )
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# Health
# ══════════════════════════════════════════════════════════════════════


class TestHealthCLI:
    def test_health_shows_report(self) -> None:
        proj = f"cli-hlth-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        result = runner.invoke(app, ["health", "--project", proj])
        assert result.exit_code == 0
        assert "Graph Health" in result.output

    def test_health_project_not_found(self) -> None:
        result = runner.invoke(app, ["health", "--project", "nonexistent-xyz-999"])
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# Embed provider-test
# ══════════════════════════════════════════════════════════════════════


class TestEmbedProviderTestCLI:
    def test_provider_test_no_api_key(self) -> None:
        result = runner.invoke(app, ["embed", "provider-test"])
        assert result.exit_code == 1
        assert "not configured" in result.output.lower() or "KGN_OPENAI_API_KEY" in result.output


# ══════════════════════════════════════════════════════════════════════
# MCP serve (transport validation only — cannot test long-running server)
# ══════════════════════════════════════════════════════════════════════


class TestMCPServeCLI:
    def test_mcp_serve_invalid_transport(self) -> None:
        result = runner.invoke(app, ["mcp", "serve", "--project", "any", "--transport", "invalid"])
        assert result.exit_code == 1
        assert "Unsupported transport" in result.output


# ══════════════════════════════════════════════════════════════════════
# Ingest paths (exercise branches not covered by test_embed_cli.py)
# ══════════════════════════════════════════════════════════════════════


class TestIngestCLI:
    def test_ingest_file_not_found(self) -> None:
        result = runner.invoke(
            app,
            ["ingest", "/nonexistent/path.kgn", "--project", "any"],
        )
        assert result.exit_code == 1
        assert "Path not found" in result.output or "Error" in result.output

    def test_ingest_kgn_file_happy(self, tmp_path: Path) -> None:
        proj = f"cli-ing-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        kgn_file = tmp_path / "test.kgn"
        node_id = uuid.uuid4().hex[:8]
        content = (
            "---\n"
            f'id: "new:{node_id}"\n'
            "type: SPEC\n"
            "status: ACTIVE\n"
            "title: CLI Ingest Test\n"
            'kgn_version: "0.1"\n'
            f'project_id: "{proj}"\n'
            'agent_id: "cli-agent"\n'
            'tags: ["test"]\n'
            "confidence: 0.9\n"
            "---\n"
            "\n"
            "## Context\n"
            "\n"
            "Test ingest via CLI.\n"
        )
        kgn_file.write_text(content, encoding="utf-8")

        result = runner.invoke(app, ["ingest", str(kgn_file), "--project", proj])
        assert result.exit_code == 0
        assert "Success" in result.output

    def test_ingest_directory_recursive(self, tmp_path: Path) -> None:
        proj = f"cli-ing-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        examples_dir = Path(__file__).resolve().parent.parent / "examples"
        dest = tmp_path / "examples"
        shutil.copytree(examples_dir, dest)

        # Replace project name in files
        for f in dest.rglob("*.kgn"):
            content = f.read_text(encoding="utf-8")
            f.write_text(content.replace("example-project", proj), encoding="utf-8")
        for f in dest.rglob("*.kge"):
            content = f.read_text(encoding="utf-8")
            f.write_text(content.replace("example-project", proj), encoding="utf-8")

        result = runner.invoke(app, ["ingest", str(dest), "--project", proj, "--recursive"])
        assert result.exit_code == 0
