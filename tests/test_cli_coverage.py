"""CLI coverage gap tests — Step 8.

Targets highest-value uncovered paths in cli.py to push coverage > 90%.
Covers:
  - _run_embed_after_ingest fallback paths (L257-268)
  - _print_batch_result failed details (L293-295)
  - _print_subgraph_table with edges (L570-579)
  - Conflict scan with results (L611-628)
  - Conflict approve/dismiss success (L663-674, L709-720)
  - Task enqueue BLOCKED display (L915-916)
  - Task checkout/complete/fail with real data (L957+, L1032+, L1079+)
  - Task list with data (L1116+)
  - Task log with data (L1182+)
  - Various exception handlers
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from kgn.cli import app
from kgn.models.edge import EdgeRecord
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
# _run_embed_after_ingest: client=None & embed failure
# ══════════════════════════════════════════════════════════════════════


class TestEmbedAfterIngest:
    def test_embed_provider_not_configured(self, tmp_path: Path) -> None:
        """When no embed client configured, ingest succeeds but warns."""
        proj = f"cli-emb-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        kgn_file = tmp_path / "test.kgn"
        content = (
            '---\nid: "new:emb-test"\ntype: SPEC\nstatus: ACTIVE\n'
            f'title: "Embed test"\nkgn_version: "0.1"\nproject_id: "{proj}"\n'
            f'agent_id: "cli"\ntags: ["t"]\nconfidence: 0.9\n---\n\n## C\n\nBody.\n'
        )
        kgn_file.write_text(content, encoding="utf-8")

        with (
            patch.dict(os.environ, {"KGN_OPENAI_API_KEY": "test-key"}),
            patch("kgn.embedding.factory.create_embedding_client", return_value=None),
        ):
            result = runner.invoke(app, ["ingest", str(kgn_file), "--project", proj, "--embed"])

        assert result.exit_code == 0
        assert "Embed" in result.output or "Warning" in result.output or "Success" in result.output

    def test_embed_failure_during_ingest(self, tmp_path: Path) -> None:
        """When embed fails after ingest, ingest still succeeds."""
        proj = f"cli-emb-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        kgn_file = tmp_path / "test.kgn"
        content = (
            '---\nid: "new:emb-fail"\ntype: SPEC\nstatus: ACTIVE\n'
            f'title: "Embed fail test"\nkgn_version: "0.1"\nproject_id: "{proj}"\n'
            f'agent_id: "cli"\ntags: ["t"]\nconfidence: 0.9\n---\n\n## C\n\nBody.\n'
        )
        kgn_file.write_text(content, encoding="utf-8")

        mock_client = MagicMock()
        with (
            patch.dict(os.environ, {"KGN_OPENAI_API_KEY": "test-key"}),
            patch("kgn.embedding.factory.create_embedding_client", return_value=mock_client),
            patch(
                "kgn.embedding.service.EmbeddingService.embed_nodes",
                side_effect=RuntimeError("API fail"),
            ),
        ):
            result = runner.invoke(app, ["ingest", str(kgn_file), "--project", proj, "--embed"])

        assert result.exit_code == 0
        assert "Success" in result.output


# ══════════════════════════════════════════════════════════════════════
# _print_batch_result with failed items
# ══════════════════════════════════════════════════════════════════════


class TestIngestFailedDetails:
    def test_ingest_with_invalid_kgn_shows_failed(self, tmp_path: Path) -> None:
        """Directory with invalid .kgn files → failed file details printed."""
        proj = f"cli-fail-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        sub = tmp_path / "bad_files"
        sub.mkdir()
        bad_file = sub / "broken.kgn"
        bad_file.write_text("this is not valid kgn content at all", encoding="utf-8")

        result = runner.invoke(app, ["ingest", str(sub), "--project", proj, "--recursive"])
        # Should show failed items and exit with 1
        assert result.exit_code == 1
        assert "Failed" in result.output


# ══════════════════════════════════════════════════════════════════════
# Subgraph with edges
# ══════════════════════════════════════════════════════════════════════


class TestSubgraphWithEdges:
    def test_subgraph_table_with_edges(self) -> None:
        """Subgraph with edges → edge table rendered."""
        proj = f"cli-sg-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)
            n1 = _make_node(pid, title="Root")
            n2 = _make_node(pid, title="Dependency")
            repo.upsert_node(n1)
            repo.upsert_node(n2)
            edge = EdgeRecord(
                from_node_id=n1.id,
                to_node_id=n2.id,
                type="DEPENDS_ON",
                project_id=pid,
                note="test edge note",
            )
            repo.insert_edge(edge)
            conn.commit()

        result = runner.invoke(app, ["query", "subgraph", str(n1.id), "--project", proj])
        assert result.exit_code == 0
        assert "Root" in result.output
        assert "Edges" in result.output


# ══════════════════════════════════════════════════════════════════════
# Conflict scan with results + approve/dismiss success
# ══════════════════════════════════════════════════════════════════════


class TestConflictScanWithResults:
    def test_conflict_scan_with_candidates(self) -> None:
        """Scan returns candidates → table is rendered."""
        proj = f"cli-cs-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)
            n1 = _make_node(pid, title="Duplicate A")
            n2 = _make_node(pid, title="Duplicate B")
            repo.upsert_node(n1)
            repo.upsert_node(n2)
            # Store identical embeddings to trigger conflict scan
            embedding = [0.1] * 1536
            repo.upsert_embedding(n1.id, pid, embedding, model="test")
            repo.upsert_embedding(n2.id, pid, embedding, model="test")
            conn.commit()

        result = runner.invoke(app, ["conflict", "scan", "--project", proj, "--threshold", "0.5"])
        assert result.exit_code == 0
        # Should show conflict table (not "No conflict candidates")
        assert "Duplicate" in result.output or "Conflict Candidates" in result.output

    def test_conflict_approve_success(self) -> None:
        """Approve conflict between two existing nodes → success."""
        proj = f"cli-ca-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)
            n1 = _make_node(pid, title="Node A")
            n2 = _make_node(pid, title="Node B")
            repo.upsert_node(n1)
            repo.upsert_node(n2)
            conn.commit()

        result = runner.invoke(
            app,
            [
                "conflict",
                "approve",
                str(n1.id),
                str(n2.id),
                "--project",
                proj,
                "--note",
                "test approval",
            ],
        )
        assert result.exit_code == 0
        assert "approved" in result.output.lower()

    def test_conflict_dismiss_success(self) -> None:
        """Dismiss conflict between two existing nodes → success."""
        proj = f"cli-cd-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)
            n1 = _make_node(pid, title="Node A")
            n2 = _make_node(pid, title="Node B")
            repo.upsert_node(n1)
            repo.upsert_node(n2)
            conn.commit()

        result = runner.invoke(
            app,
            [
                "conflict",
                "dismiss",
                str(n1.id),
                str(n2.id),
                "--project",
                proj,
                "--note",
                "test dismiss",
            ],
        )
        assert result.exit_code == 0
        assert "dismissed" in result.output.lower()


# ══════════════════════════════════════════════════════════════════════
# Task commands: enqueue, checkout, complete, fail, list, log
# ══════════════════════════════════════════════════════════════════════


class TestTaskCommands:
    @staticmethod
    def _create_task_node(proj: str) -> tuple[str, uuid.UUID]:
        """Create a TASK node in the given project. Returns (project, node_id)."""
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)
            node = NodeRecord(
                id=uuid.uuid4(),
                project_id=pid,
                type=NodeType.TASK,
                status=NodeStatus.ACTIVE,
                title="CLI Task Test",
                body_md="## Steps\n\nDo the thing.",
                content_hash=uuid.uuid4().hex,
                tags=["test"],
            )
            repo.upsert_node(node)
            conn.commit()
        return proj, node.id

    def test_task_enqueue_success(self) -> None:
        """Enqueue a task → prints success with queue ID."""
        proj = f"cli-tq-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        proj, node_id = self._create_task_node(proj)

        result = runner.invoke(
            app,
            ["task", "enqueue", str(node_id), "--project", proj],
        )
        assert result.exit_code == 0
        assert "enqueued" in result.output.lower() or "✅" in result.output

    def test_task_enqueue_blocked_display(self) -> None:
        """Enqueue a BLOCKED task → prints BLOCKED warning."""
        proj = f"cli-tb-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_or_create_project(proj)

            # Parent task (not enqueued/completed)
            parent = NodeRecord(
                id=uuid.uuid4(),
                project_id=pid,
                type=NodeType.TASK,
                status=NodeStatus.ACTIVE,
                title="Parent Task",
                body_md="parent",
                content_hash=uuid.uuid4().hex,
            )
            repo.upsert_node(parent)

            # Child task depends on parent
            child = NodeRecord(
                id=uuid.uuid4(),
                project_id=pid,
                type=NodeType.TASK,
                status=NodeStatus.ACTIVE,
                title="Child Task",
                body_md="child",
                content_hash=uuid.uuid4().hex,
            )
            repo.upsert_node(child)

            edge = EdgeRecord(
                from_node_id=child.id,
                to_node_id=parent.id,
                type="DEPENDS_ON",
                project_id=pid,
                note="depends",
            )
            repo.insert_edge(edge)
            conn.commit()

        result = runner.invoke(
            app,
            ["task", "enqueue", str(child.id), "--project", proj],
        )
        assert result.exit_code == 0
        assert "BLOCKED" in result.output

    def test_task_enqueue_project_not_found(self) -> None:
        result = runner.invoke(
            app, ["task", "enqueue", str(uuid.uuid4()), "--project", "nope-proj"]
        )
        assert result.exit_code == 1

    def test_task_checkout_and_complete(self) -> None:
        """Full lifecycle: enqueue → checkout → complete."""
        proj = f"cli-tc-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        proj, node_id = self._create_task_node(proj)

        # Enqueue
        runner.invoke(app, ["task", "enqueue", str(node_id), "--project", proj])

        # Checkout
        co_result = runner.invoke(
            app,
            ["task", "checkout", "--project", proj, "--agent", "test-agent"],
        )
        assert co_result.exit_code == 0
        assert "Task" in co_result.output

        # Extract task ID from output (look for UUID pattern)
        import re

        id_match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", co_result.output)
        assert id_match is not None

        # Get the actual task queue ID from DB
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_project_by_name(proj)
            tasks = repo.list_tasks(pid, state="IN_PROGRESS")
            assert len(tasks) >= 1
            task_id = str(tasks[0].id)

        # Complete
        comp_result = runner.invoke(app, ["task", "complete", task_id, "--project", proj])
        assert comp_result.exit_code == 0
        assert "completed" in comp_result.output.lower() or "✅" in comp_result.output

    def test_task_checkout_json_format(self) -> None:
        """Checkout with --format json → JSON output."""
        proj = f"cli-tcj-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        proj, node_id = self._create_task_node(proj)

        runner.invoke(app, ["task", "enqueue", str(node_id), "--project", proj])

        result = runner.invoke(
            app,
            ["task", "checkout", "--project", proj, "--agent", "test-agent", "--format", "json"],
        )
        assert result.exit_code == 0

    def test_task_checkout_md_format(self) -> None:
        """Checkout with --format md → Markdown output."""
        proj = f"cli-tcm-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        proj, node_id = self._create_task_node(proj)

        runner.invoke(app, ["task", "enqueue", str(node_id), "--project", proj])

        result = runner.invoke(
            app,
            ["task", "checkout", "--project", proj, "--agent", "test-agent", "--format", "md"],
        )
        assert result.exit_code == 0

    def test_task_checkout_empty_queue(self) -> None:
        """Checkout on empty queue → 'No tasks available'."""
        proj = f"cli-tce-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        result = runner.invoke(
            app,
            ["task", "checkout", "--project", proj, "--agent", "test-agent"],
        )
        assert result.exit_code == 0
        assert "No tasks available" in result.output

    def test_task_checkout_project_not_found(self) -> None:
        result = runner.invoke(
            app,
            ["task", "checkout", "--project", "nope-proj", "--agent", "x"],
        )
        assert result.exit_code == 1

    def test_task_fail_lifecycle(self) -> None:
        """Enqueue → checkout → fail."""
        proj = f"cli-tf-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        proj, node_id = self._create_task_node(proj)

        runner.invoke(app, ["task", "enqueue", str(node_id), "--project", proj])
        runner.invoke(app, ["task", "checkout", "--project", proj, "--agent", "test-agent"])

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_project_by_name(proj)
            tasks = repo.list_tasks(pid, state="IN_PROGRESS")
            task_id = str(tasks[0].id)

        fail_result = runner.invoke(
            app, ["task", "fail", task_id, "--project", proj, "--reason", "test fail"]
        )
        assert fail_result.exit_code == 0
        assert "failed" in fail_result.output.lower() or "❌" in fail_result.output

    def test_task_complete_project_not_found(self) -> None:
        result = runner.invoke(
            app, ["task", "complete", str(uuid.uuid4()), "--project", "nope-proj"]
        )
        assert result.exit_code == 1

    def test_task_fail_project_not_found(self) -> None:
        result = runner.invoke(
            app,
            ["task", "fail", str(uuid.uuid4()), "--project", "nope-proj", "--reason", "x"],
        )
        assert result.exit_code == 1

    def test_task_list_with_data(self) -> None:
        """List tasks → table output."""
        proj = f"cli-tl-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        proj, node_id = self._create_task_node(proj)

        runner.invoke(app, ["task", "enqueue", str(node_id), "--project", proj])

        result = runner.invoke(app, ["task", "list", "--project", proj])
        assert result.exit_code == 0
        assert "Task Queue" in result.output or "CLI Task Test" in result.output

    def test_task_list_project_not_found(self) -> None:
        result = runner.invoke(app, ["task", "list", "--project", "nope-proj"])
        assert result.exit_code == 1

    def test_task_log_with_data(self) -> None:
        """Log for an active task → activity table."""
        proj = f"cli-tlog-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        proj, node_id = self._create_task_node(proj)

        runner.invoke(app, ["task", "enqueue", str(node_id), "--project", proj])

        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository

        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_project_by_name(proj)
            tasks = repo.list_tasks(pid)
            task_id = str(tasks[0].id)

        # Checkout to create at least one activity entry
        runner.invoke(app, ["task", "checkout", "--project", proj, "--agent", "test-agent"])

        result = runner.invoke(app, ["task", "log", task_id, "--project", proj])
        # Either shows activities or "No activities found"
        assert result.exit_code == 0

    def test_task_log_project_not_found(self) -> None:
        result = runner.invoke(app, ["task", "log", str(uuid.uuid4()), "--project", "nope-proj"])
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# Embed batch project not found
# ══════════════════════════════════════════════════════════════════════


class TestEmbedBatch:
    def test_embed_batch_project_not_found(self) -> None:
        with patch("kgn.embedding.factory.create_embedding_client", return_value=MagicMock()):
            result = runner.invoke(app, ["embed", "batch", "--project", "nope-proj-embed"])
        assert result.exit_code == 1


# ══════════════════════════════════════════════════════════════════════
# Sync export with README generation
# ══════════════════════════════════════════════════════════════════════


class TestSyncExportReadme:
    def test_export_readme_generation_failure(self, tmp_path: Path) -> None:
        """README generation fails → export still succeeds (R-029)."""
        proj = f"cli-exp-rd-{uuid.uuid4().hex[:8]}"
        _init_project(proj)

        from kgn.sync.export_service import ExportResult

        mock_result = ExportResult(exported=1, skipped=0, deleted=0)

        with (
            patch("kgn.sync.export_service.ExportService") as MockExport,
            patch("kgn.graph.mermaid.MermaidGenerator") as MockMermaid,
        ):
            MockExport.return_value.export_project.return_value = mock_result
            MockMermaid.return_value.generate_readme.side_effect = RuntimeError("readme boom")

            result = runner.invoke(
                app,
                ["sync", "export", "--project", proj, "--target", str(tmp_path)],
            )

        # Export should succeed despite readme failure
        assert result.exit_code == 0
        assert "Export complete" in result.output


# ══════════════════════════════════════════════════════════════════════
# Exception handlers in misc commands
# ══════════════════════════════════════════════════════════════════════


class TestMiscExceptionHandlers:
    def test_init_db_error(self) -> None:
        """Init with DB connection failure → Error output."""
        proj = f"cli-init-err-{uuid.uuid4().hex[:8]}"
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("DB down"),
        ):
            result = runner.invoke(app, ["init", "--project", proj])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_query_nodes_db_error(self) -> None:
        """Query nodes with DB failure → Error."""
        proj = f"cli-qn-err-{uuid.uuid4().hex[:8]}"
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("DB gone"),
        ):
            result = runner.invoke(app, ["query", "nodes", "--project", proj])
        assert result.exit_code == 1

    def test_status_db_error(self) -> None:
        """Status with DB failure → Error."""
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("DB error"),
        ):
            result = runner.invoke(app, ["status", "--project", "any"])
        assert result.exit_code == 1

    def test_ingest_exception(self) -> None:
        """Ingest with unexpected exception → Error."""
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("boom"),
        ):
            result = runner.invoke(app, ["ingest", ".", "--project", "any"])
        assert result.exit_code == 1

    def test_query_subgraph_db_error(self) -> None:
        """Subgraph with DB failure → Error."""
        fake_id = str(uuid.uuid4())
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("DB error"),
        ):
            result = runner.invoke(app, ["query", "subgraph", fake_id, "--project", "any"])
        assert result.exit_code == 1

    def test_query_similar_db_error(self) -> None:
        """Similar with DB failure → Error."""
        fake_id = str(uuid.uuid4())
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("DB error"),
        ):
            result = runner.invoke(app, ["query", "similar", fake_id, "--project", "any"])
        assert result.exit_code == 1

    def test_health_db_error(self) -> None:
        """Health with DB failure → Error."""
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("DB error"),
        ):
            result = runner.invoke(app, ["health", "--project", "any"])
        assert result.exit_code == 1

    def test_conflict_scan_db_error(self) -> None:
        """Conflict scan with DB failure → Error."""
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("DB error"),
        ):
            result = runner.invoke(app, ["conflict", "scan", "--project", "any"])
        assert result.exit_code == 1

    def test_task_enqueue_exception(self) -> None:
        """Task enqueue with unexpected exception."""
        fake_id = str(uuid.uuid4())
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("boom"),
        ):
            result = runner.invoke(app, ["task", "enqueue", fake_id, "--project", "any"])
        assert result.exit_code == 1

    def test_task_checkout_exception(self) -> None:
        """Task checkout with unexpected exception."""
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("boom"),
        ):
            result = runner.invoke(app, ["task", "checkout", "--project", "any", "--agent", "x"])
        assert result.exit_code == 1

    def test_task_complete_exception(self) -> None:
        """Task complete with unexpected ValueError."""
        proj = f"cli-tce-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        fake_id = str(uuid.uuid4())
        result = runner.invoke(app, ["task", "complete", fake_id, "--project", proj])
        # Should fail because task doesn't exist
        assert result.exit_code == 1

    def test_task_fail_exception(self) -> None:
        """Task fail with unexpected ValueError."""
        proj = f"cli-tfe-{uuid.uuid4().hex[:8]}"
        _init_project(proj)
        fake_id = str(uuid.uuid4())
        result = runner.invoke(app, ["task", "fail", fake_id, "--project", proj, "--reason", "x"])
        assert result.exit_code == 1

    def test_task_list_exception(self) -> None:
        """Task list with DB exception."""
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("boom"),
        ):
            result = runner.invoke(app, ["task", "list", "--project", "any"])
        assert result.exit_code == 1

    def test_task_log_exception(self) -> None:
        """Task log with DB exception."""
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("boom"),
        ):
            result = runner.invoke(app, ["task", "log", str(uuid.uuid4()), "--project", "any"])
        assert result.exit_code == 1

    def test_embed_batch_exception(self) -> None:
        """Embed batch with exception."""
        mock_client = MagicMock()
        with (
            patch("kgn.embedding.factory.create_embedding_client", return_value=mock_client),
            patch("kgn.db.connection.get_connection", side_effect=RuntimeError("boom")),
        ):
            result = runner.invoke(app, ["embed", "batch", "--project", "any"])
        assert result.exit_code == 1

    def test_sync_export_exception(self) -> None:
        """Sync export exception."""
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("boom"),
        ):
            result = runner.invoke(app, ["sync", "export", "--project", "any"])
        assert result.exit_code == 1

    def test_sync_import_exception(self) -> None:
        """Sync import exception."""
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("boom"),
        ):
            result = runner.invoke(app, ["sync", "import", "--project", "any"])
        assert result.exit_code == 1

    def test_sync_status_exception(self) -> None:
        """Sync status exception."""
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("boom"),
        ):
            result = runner.invoke(app, ["sync", "status", "--project", "any"])
        assert result.exit_code == 1

    def test_graph_mermaid_exception(self) -> None:
        """Graph mermaid exception."""
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("boom"),
        ):
            result = runner.invoke(app, ["graph", "mermaid", "--project", "any"])
        assert result.exit_code == 1

    def test_graph_readme_exception(self) -> None:
        """Graph readme exception."""
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=RuntimeError("boom"),
        ):
            result = runner.invoke(app, ["graph", "readme", "--project", "any"])
        assert result.exit_code == 1
