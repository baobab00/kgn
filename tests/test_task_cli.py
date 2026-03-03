"""CLI smoke tests for `kgn task` subcommands.

Requires a running PostgreSQL instance (Docker on port 5433).
"""

from __future__ import annotations

import re
import uuid

from kgn.db.repository import KgnRepository
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _make_task_node(
    project_id: uuid.UUID,
    *,
    title: str = "Implement feature",
    body: str = "## Context\n\nTask body",
    created_by: uuid.UUID | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.TASK,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md=body,
        content_hash=uuid.uuid4().hex,
        created_by=created_by,
    )


# ── CLI Smoke Tests ────────────────────────────────────────────────────


class TestTaskCLI:
    """Smoke tests for `kgn task` subcommands via CliRunner."""

    def _setup_project(self, runner, app, project_name: str) -> None:
        runner.invoke(app, ["init", "--project", project_name])

    def test_task_enqueue_cli(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app

        proj = f"cli-task-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        # Create a TASK node via repo
        node = _make_task_node(project_id)
        repo.upsert_node(node)

        # Need to use the same project name in DB
        # Re-resolve: use the fixture project by getting its name
        # Simpler: create node in the CLI project
        # Actually, we need matching project. Let's create via repo.
        from kgn.db.connection import get_connection

        with get_connection() as conn:
            from kgn.db.repository import KgnRepository as Repo

            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            task_node = _make_task_node(pid)
            r.upsert_node(task_node)
            conn.commit()

        result = runner.invoke(app, ["task", "enqueue", str(task_node.id), "--project", proj])
        assert result.exit_code == 0
        assert "Task enqueued" in result.output

    def test_task_enqueue_invalid_uuid(self) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["task", "enqueue", "not-a-uuid", "--project", "x"])
        assert result.exit_code != 0
        assert "Invalid UUID" in result.output

    def test_task_checkout_empty(self) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app

        proj = f"cli-task-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        result = runner.invoke(app, ["task", "checkout", "--project", proj, "--agent", "a1"])
        assert result.exit_code == 0
        assert "No tasks available" in result.output

    def test_task_checkout_requeues_expired(self) -> None:
        """Expired lease task is auto-requeued and re-consumed on CLI checkout."""
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        proj = f"cli-task-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        # Create TASK node + enqueue + checkout (→ IN_PROGRESS)
        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            task_node = _make_task_node(pid, title="Requeue Me")
            r.upsert_node(task_node)
            r.enqueue_task(pid, task_node.id)
            agent_id = r.get_or_create_agent(pid, "expire-agent")
            task = r.checkout_task(pid, agent_id)
            assert task is not None
            # Expire the lease
            conn.execute(
                "UPDATE task_queue SET lease_expires_at = now() - interval '1 hour' WHERE id = %s",
                (task.id,),
            )
            conn.commit()

        # CLI checkout → requeue_expired runs first, then checkout succeeds
        result = runner.invoke(
            app,
            ["task", "checkout", "--project", proj, "--agent", "new-agent"],
        )
        assert result.exit_code == 0
        assert "Task Checkout" in result.output
        assert "Requeue Me" in result.output

    def test_task_full_lifecycle(self) -> None:
        """enqueue → checkout → complete lifecycle via CLI."""
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        proj = f"cli-task-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        # Create TASK node
        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            task_node = _make_task_node(pid)
            r.upsert_node(task_node)
            conn.commit()

        # Enqueue
        result = runner.invoke(app, ["task", "enqueue", str(task_node.id), "--project", proj])
        assert result.exit_code == 0
        assert "Task enqueued" in result.output

        # Checkout
        result = runner.invoke(
            app, ["task", "checkout", "--project", proj, "--agent", "test-agent"]
        )
        assert result.exit_code == 0
        assert "Task Checkout" in result.output
        assert task_node.title in result.output

        # Extract task ID from checkout output for complete
        # The task ID is shown in the panel output
        match = re.search(r"Task ID:\s+([0-9a-f\-]{36})", result.output)
        assert match is not None
        task_id = match.group(1)

        # Complete
        result = runner.invoke(app, ["task", "complete", task_id, "--project", proj])
        assert result.exit_code == 0
        assert "completed" in result.output

    def test_task_fail_cli(self) -> None:
        """enqueue → checkout → fail lifecycle via CLI."""
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        proj = f"cli-task-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            task_node = _make_task_node(pid)
            r.upsert_node(task_node)
            conn.commit()

        runner.invoke(app, ["task", "enqueue", str(task_node.id), "--project", proj])
        result = runner.invoke(app, ["task", "checkout", "--project", proj, "--agent", "ag"])

        match = re.search(r"Task ID:\s+([0-9a-f\-]{36})", result.output)
        assert match is not None
        task_id = match.group(1)

        result = runner.invoke(
            app,
            ["task", "fail", task_id, "--project", proj, "--reason", "broken"],
        )
        assert result.exit_code == 0
        assert "failed" in result.output
        assert "broken" in result.output

    def test_task_list_cli(self) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        proj = f"cli-task-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            task_node = _make_task_node(pid, title="CLI list test")
            r.upsert_node(task_node)
            r.enqueue_task(pid, task_node.id)
            conn.commit()

        result = runner.invoke(app, ["task", "list", "--project", proj])
        assert result.exit_code == 0
        assert "Task Queue" in result.output
        assert "CLI list test" in result.output
        assert "READY" in result.output

    def test_task_list_empty(self) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app

        proj = f"cli-task-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        result = runner.invoke(app, ["task", "list", "--project", proj])
        assert result.exit_code == 0
        assert "No tasks found" in result.output

    def test_task_list_state_filter(self) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        proj = f"cli-task-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            t1 = _make_task_node(pid, title="T-filter-A")
            t2 = _make_task_node(pid, title="T-filter-B")
            r.upsert_node(t1)
            r.upsert_node(t2)
            r.enqueue_task(pid, t1.id)
            r.enqueue_task(pid, t2.id)
            conn.commit()

        # All should be READY
        result = runner.invoke(app, ["task", "list", "--project", proj, "--state", "READY"])
        assert result.exit_code == 0
        assert "T-filter-A" in result.output
        assert "T-filter-B" in result.output

        # No IN_PROGRESS tasks
        result = runner.invoke(app, ["task", "list", "--project", proj, "--state", "IN_PROGRESS"])
        assert result.exit_code == 0
        assert "No tasks found" in result.output


# ── CLI --format Tests ─────────────────────────────────────────────────


class TestTaskCLIFormat(TestTaskCLI):
    """Extend TestTaskCLI to test --format json|md on checkout."""

    def test_checkout_format_json(self) -> None:
        import json as _json

        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        proj = f"cli-fmt-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            node = _make_task_node(pid, title="JSON-test-task")
            r.upsert_node(node)
            r.enqueue_task(pid, node.id)
            conn.commit()

        result = runner.invoke(
            app,
            ["task", "checkout", "--project", proj, "--agent", "bot", "--format", "json"],
        )
        assert result.exit_code == 0
        data = _json.loads(result.output)
        assert data["task"]["node_id"] == str(node.id)
        assert data["node"]["title"] == "JSON-test-task"
        assert "subgraph" in data
        assert "metadata" in data

    def test_checkout_format_md(self) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        proj = f"cli-fmt-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            node = _make_task_node(pid, title="MD-test-task")
            r.upsert_node(node)
            r.enqueue_task(pid, node.id)
            conn.commit()

        result = runner.invoke(
            app,
            ["task", "checkout", "--project", proj, "--agent", "bot", "--format", "md"],
        )
        assert result.exit_code == 0
        assert "# Task Handoff" in result.output
        assert "## 1. Task" in result.output
        assert "MD-test-task" in result.output

    def test_checkout_format_invalid(self) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app

        proj = f"cli-fmt-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        result = runner.invoke(
            app,
            [
                "task",
                "checkout",
                "--project",
                proj,
                "--agent",
                "bot",
                "--format",
                "xml",
            ],
        )
        assert result.exit_code == 1

    def test_checkout_no_format_uses_panel(self) -> None:
        """Without --format, the output should contain the Rich Panel output."""
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        proj = f"cli-fmt-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            node = _make_task_node(pid, title="Panel-test-task")
            r.upsert_node(node)
            r.enqueue_task(pid, node.id)
            conn.commit()

        result = runner.invoke(
            app,
            ["task", "checkout", "--project", proj, "--agent", "bot"],
        )
        assert result.exit_code == 0
        # Panel output should NOT contain JSON or Markdown headers
        assert "# Task Handoff" not in result.output
        # Panel should contain task info
        assert "Panel-test-task" in result.output


# ── CLI task log Tests ─────────────────────────────────────────────────


class TestTaskCLILog(TestTaskCLI):
    """Tests for `kgn task log` CLI command."""

    def test_task_log_shows_activities(self) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        proj = f"cli-log-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            node = _make_task_node(pid, title="Log-test-task")
            r.upsert_node(node)
            r.enqueue_task(pid, node.id)
            conn.commit()

        # Checkout the task first
        result = runner.invoke(
            app,
            ["task", "checkout", "--project", proj, "--agent", "bot"],
        )
        assert result.exit_code == 0

        # Get task_id from the database
        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            tasks = r.list_tasks(pid)
            assert len(tasks) > 0
            task_id = str(tasks[0].id)

        result = runner.invoke(
            app,
            ["task", "log", task_id, "--project", proj],
        )
        assert result.exit_code == 0
        assert "TASK_CHECKOUT" in result.output
        assert "Activity Log" in result.output

    def test_task_log_empty(self) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app

        proj = f"cli-log-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        # Random UUID — no activities
        fake_id = str(uuid.uuid4())
        result = runner.invoke(
            app,
            ["task", "log", fake_id, "--project", proj],
        )
        assert result.exit_code == 0
        assert "No activities found" in result.output

    def test_task_log_invalid_uuid(self) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app

        proj = f"cli-log-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        result = runner.invoke(
            app,
            ["task", "log", "not-a-uuid", "--project", proj],
        )
        assert result.exit_code == 1

    def test_task_log_after_complete(self) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        proj = f"cli-log-{uuid.uuid4().hex[:8]}"
        runner = CliRunner()
        self._setup_project(runner, app, proj)

        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            node = _make_task_node(pid, title="Log-complete")
            r.upsert_node(node)
            r.enqueue_task(pid, node.id)
            conn.commit()

        # Checkout
        runner.invoke(app, ["task", "checkout", "--project", proj, "--agent", "bot"])

        # Get task_id
        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            tasks = r.list_tasks(pid)
            task_id = str(tasks[0].id)

        # Complete
        runner.invoke(app, ["task", "complete", task_id, "--project", proj])

        # Log should show both activities
        result = runner.invoke(app, ["task", "log", task_id, "--project", proj])
        assert result.exit_code == 0
        assert "TASK_CHECKOUT" in result.output
        assert "TASK_COMPLETED" in result.output
