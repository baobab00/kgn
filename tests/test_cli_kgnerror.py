"""Tests for CLI ``except KgnError`` branches and repository defensive raises.

Covers the structured-error handler added in Phase 12 Step 11 across all
CLI modules, plus the ``KgnError`` defensive raises in ``repository.py``.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from kgn.cli import app
from kgn.errors import KgnError, KgnErrorCode

runner = CliRunner()

# ── Helpers ────────────────────────────────────────────────────────────

_PROJ = "cov-test"
_ERR = KgnError(code=KgnErrorCode.INTERNAL_ERROR, message="synthetic error")


def _assert_kgn_error(result) -> None:  # noqa: ANN001
    """Assert the CLI printed structured error and exited 1."""
    assert result.exit_code == 1
    assert "Error" in result.output


# ── CLI _core.py ───────────────────────────────────────────────────────


class TestCoreKgnError:
    """Covers except-KgnError in init, ingest, status, health."""

    def test_init_kgn_error(self) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(app, ["init", "--project", _PROJ])
        _assert_kgn_error(result)

    def test_ingest_kgn_error(self) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(app, ["ingest", "dummy.kgn", "--project", _PROJ])
        _assert_kgn_error(result)

    def test_status_kgn_error(self) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(app, ["status", "--project", _PROJ])
        _assert_kgn_error(result)

    def test_health_kgn_error(self) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(app, ["health", "--project", _PROJ])
        _assert_kgn_error(result)

    def test_embed_after_ingest_kgn_error(self) -> None:
        from click.exceptions import Exit

        from kgn.cli._core import _run_embed_after_ingest

        mock_repo = MagicMock()
        with (
            patch("kgn.embedding.factory.create_embedding_client", return_value=MagicMock()),
            patch("kgn.embedding.service.EmbeddingService.embed_nodes", side_effect=_ERR),
            pytest.raises(Exit),
        ):
            _run_embed_after_ingest(
                repo=mock_repo,
                node_ids=[uuid.uuid4()],
                project_id=uuid.uuid4(),
            )


# ── CLI _agent.py ──────────────────────────────────────────────────────


class TestAgentKgnError:
    """Covers except-KgnError in agent list, role, stats, timeline."""

    def test_agent_list_kgn_error(self) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(app, ["agent", "list", "--project", _PROJ])
        _assert_kgn_error(result)

    def test_agent_role_kgn_error(self) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(
                app,
                ["agent", "role", "--project", _PROJ, "--agent", "test-agent", "worker"],
            )
        _assert_kgn_error(result)

    def test_agent_stats_kgn_error(self) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(app, ["agent", "stats", "--project", _PROJ])
        _assert_kgn_error(result)

    def test_agent_timeline_kgn_error(self) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(app, ["agent", "timeline", "--project", _PROJ])
        _assert_kgn_error(result)


# ── CLI _git.py ────────────────────────────────────────────────────────


class TestGitKgnError:
    """Covers except-KgnError in git init/status/diff/commit/log/show/restore."""

    @pytest.mark.parametrize(
        "subcmd",
        ["init", "status", "diff", "log"],
    )
    def test_git_subcommand_kgn_error(self, subcmd: str, tmp_path) -> None:
        with patch("kgn.git.service.GitService", side_effect=_ERR):
            result = runner.invoke(
                app,
                ["git", subcmd, "--target", str(tmp_path)],
            )
        _assert_kgn_error(result)

    def test_git_branch_list_kgn_error(self, tmp_path) -> None:
        with patch("kgn.git.service.GitService", side_effect=_ERR):
            result = runner.invoke(
                app,
                ["git", "branch", "list", "--target", str(tmp_path)],
            )
        _assert_kgn_error(result)

    def test_git_branch_checkout_kgn_error(self, tmp_path) -> None:
        with patch("kgn.git.service.GitService", side_effect=_ERR):
            result = runner.invoke(
                app,
                ["git", "branch", "checkout", "main", "--target", str(tmp_path)],
            )
        _assert_kgn_error(result)

    def test_git_branch_cleanup_kgn_error(self, tmp_path) -> None:
        with patch("kgn.git.service.GitService", side_effect=_ERR):
            result = runner.invoke(
                app,
                ["git", "branch", "cleanup", "--target", str(tmp_path)],
            )
        _assert_kgn_error(result)

    def test_git_pr_create_kgn_error(self) -> None:
        with patch("kgn.github.client.GitHubConfig.from_env", side_effect=_ERR):
            result = runner.invoke(
                app,
                ["git", "pr", "create", "--title", "t", "--head", "h"],
            )
        _assert_kgn_error(result)

    def test_git_pr_list_kgn_error(self) -> None:
        with patch("kgn.github.client.GitHubConfig.from_env", side_effect=_ERR):
            result = runner.invoke(
                app,
                ["git", "pr", "list"],
            )
        _assert_kgn_error(result)


# ── CLI _sync.py ───────────────────────────────────────────────────────


class TestSyncKgnError:
    """Covers except-KgnError in sync export/import/push/pull."""

    @pytest.mark.parametrize("subcmd", ["export", "import"])
    def test_sync_subcommand_kgn_error(self, subcmd: str, tmp_path) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            target_flag = "--target" if subcmd == "export" else "--source"
            result = runner.invoke(
                app,
                ["sync", subcmd, "--project", _PROJ, target_flag, str(tmp_path)],
            )
        _assert_kgn_error(result)

    @pytest.mark.parametrize("subcmd", ["push", "pull"])
    def test_sync_remote_kgn_error(self, subcmd: str, tmp_path) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(
                app,
                ["sync", subcmd, "--project", _PROJ, "--target", str(tmp_path)],
            )
        _assert_kgn_error(result)


# ── CLI _task.py ───────────────────────────────────────────────────────


class TestTaskKgnError:
    """Covers except-KgnError in task enqueue/checkout/complete/fail/list/log."""

    @pytest.mark.parametrize(
        "args",
        [
            ["task", "enqueue", str(uuid.uuid4()), "--project", _PROJ],
            ["task", "checkout", "--project", _PROJ, "--agent", "test-agent"],
            ["task", "complete", "1", "--project", _PROJ],
            ["task", "fail", "1", "--project", _PROJ, "--reason", "err"],
            ["task", "list", "--project", _PROJ],
            ["task", "log", str(uuid.uuid4()), "--project", _PROJ],
        ],
    )
    def test_task_subcommand_kgn_error(self, args: list[str]) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(app, args)
        _assert_kgn_error(result)


# ── CLI _conflict.py ───────────────────────────────────────────────────


class TestConflictKgnError:
    """Covers except-KgnError in conflict scan/approve/dismiss."""

    @pytest.mark.parametrize(
        "subcmd_args",
        [
            ["conflict", "scan", "--project", _PROJ],
            ["conflict", "approve", str(uuid.uuid4()), str(uuid.uuid4()), "--project", _PROJ],
            ["conflict", "dismiss", str(uuid.uuid4()), str(uuid.uuid4()), "--project", _PROJ],
        ],
    )
    def test_conflict_subcommand_kgn_error(self, subcmd_args: list[str]) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(app, subcmd_args)
        _assert_kgn_error(result)


# ── CLI _query.py ──────────────────────────────────────────────────────


class TestQueryKgnError:
    """Covers except-KgnError in query nodes/subgraph/similar."""

    @pytest.mark.parametrize(
        "subcmd_args",
        [
            ["query", "nodes", "--project", _PROJ],
            ["query", "subgraph", str(uuid.uuid4()), "--project", _PROJ],
            ["query", "similar", str(uuid.uuid4()), "--project", _PROJ],
        ],
    )
    def test_query_subcommand_kgn_error(self, subcmd_args: list[str]) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(app, subcmd_args)
        _assert_kgn_error(result)


# ── CLI _embed.py ──────────────────────────────────────────────────────


class TestEmbedKgnError:
    """Covers except-KgnError in embed nodes."""

    def test_embed_batch_kgn_error(self) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(
                app,
                ["embed", "batch", "--project", _PROJ],
            )
        _assert_kgn_error(result)


# ── CLI _graph.py ──────────────────────────────────────────────────────


class TestGraphKgnError:
    """Covers except-KgnError in graph mermaid/readme."""

    def test_graph_mermaid_kgn_error(self) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(
                app,
                ["graph", "mermaid", "--project", _PROJ],
            )
        _assert_kgn_error(result)

    def test_graph_readme_kgn_error(self, tmp_path) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(
                app,
                ["graph", "readme", "--project", _PROJ, "--target", str(tmp_path)],
            )
        _assert_kgn_error(result)


# ── CLI _workflow.py ───────────────────────────────────────────────────


class TestWorkflowKgnError:
    """Covers except-KgnError in workflow run."""

    def test_workflow_run_kgn_error(self) -> None:
        with patch(
            "kgn.db.connection.get_connection",
            side_effect=_ERR,
        ):
            result = runner.invoke(
                app,
                ["workflow", "run", "design-to-impl", str(uuid.uuid4()), "--project", _PROJ],
            )
        _assert_kgn_error(result)


# ── Repository defensive raises ────────────────────────────────────────


class TestRepositoryDefensiveRaises:
    """Covers KgnError raises for impossible NULL-from-RETURNING paths."""

    def test_get_or_create_project_null_row(self, db_conn) -> None:
        from kgn.db.repository import KgnRepository

        repo = KgnRepository(db_conn)
        with patch.object(db_conn, "execute") as mock_exec:
            # First call: SELECT returns None (project doesn't exist)
            # Second call: INSERT RETURNING returns None (impossible)
            cursor_none = MagicMock()
            cursor_none.fetchone.return_value = None
            mock_exec.return_value = cursor_none

            with pytest.raises(KgnError) as exc_info:
                repo.get_or_create_project("null-project")
            assert exc_info.value.code == KgnErrorCode.INTERNAL_ERROR

    def test_get_or_create_agent_null_row(self, db_conn) -> None:
        from kgn.db.repository import KgnRepository

        repo = KgnRepository(db_conn)
        with patch.object(db_conn, "execute") as mock_exec:
            cursor_none = MagicMock()
            cursor_none.fetchone.return_value = None
            mock_exec.return_value = cursor_none

            with pytest.raises(KgnError) as exc_info:
                repo.get_or_create_agent(uuid.uuid4(), "null-agent")
            assert exc_info.value.code == KgnErrorCode.INTERNAL_ERROR

    def test_insert_edge_null_duplicate(self, db_conn) -> None:
        from kgn.db.repository import KgnRepository

        repo = KgnRepository(db_conn)
        # Simulate: INSERT returns None (duplicate), then SELECT also None
        call_count = 0
        original_execute = db_conn.execute

        def fake_execute(sql, params=None):
            nonlocal call_count
            call_count += 1
            if "INSERT INTO edges" in sql:
                m = MagicMock()
                m.fetchone.return_value = None
                return m
            if "SELECT id FROM edges" in sql:
                m = MagicMock()
                m.fetchone.return_value = None
                return m
            return original_execute(sql, params)

        with patch.object(db_conn, "execute", side_effect=fake_execute):
            from kgn.models.edge import EdgeRecord

            edge = EdgeRecord(
                project_id=uuid.uuid4(),
                from_node_id=uuid.uuid4(),
                to_node_id=uuid.uuid4(),
                type="DEPENDS_ON",
            )
            with pytest.raises(KgnError) as exc_info:
                repo.insert_edge(edge)
            assert exc_info.value.code == KgnErrorCode.INTERNAL_ERROR

    def test_enqueue_task_null_row(self, db_conn) -> None:
        from kgn.db.repository import KgnRepository

        repo = KgnRepository(db_conn)

        # First call: SELECT returns a valid TASK row
        # Second call: INSERT RETURNING returns None (impossible)
        select_cursor = MagicMock()
        select_cursor.fetchone.return_value = ("TASK",)
        insert_cursor = MagicMock()
        insert_cursor.fetchone.return_value = None

        with patch.object(db_conn, "execute", side_effect=[select_cursor, insert_cursor]):
            with pytest.raises(KgnError) as exc_info:
                repo.enqueue_task(uuid.uuid4(), uuid.uuid4())
            assert exc_info.value.code == KgnErrorCode.INTERNAL_ERROR
