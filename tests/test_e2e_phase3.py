"""End-to-End integration tests for Phase 3 — Task Orchestration.

Scenarios:
1. Full flow: init → ingest → enqueue → checkout → complete → log
2. Lease expiry recovery via requeue_expired
3. max_attempts exceeded (FAILED stays FAILED)
4. Handoff package (--format md / --format json)

Requires a running PostgreSQL instance (Docker on port 5433).
"""

from __future__ import annotations

import json
import uuid

from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.graph.subgraph import SubgraphService
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.task.service import TaskService

# ── Helpers ────────────────────────────────────────────────────────────


def _make_task_node(
    project_id: uuid.UUID,
    *,
    title: str = "E2E Task",
    body: str = "## Context\n\nE2E task body",
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


def _make_spec_node(
    project_id: uuid.UUID,
    *,
    title: str = "E2E Spec",
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.SPEC,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md="## Context\n\nSpec body",
        content_hash=uuid.uuid4().hex,
    )


# ══════════════════════════════════════════════════════════════════════
#  Scenario 1 — Full Flow
# ══════════════════════════════════════════════════════════════════════


class TestE2EFullTaskFlow:
    """init → ingest → enqueue → checkout → complete → log"""

    def test_full_task_lifecycle(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        # 1. Create TASK node (simulates ingest)
        task_node = _make_task_node(project_id, title="E2E full flow task")
        repo.upsert_node(task_node)

        # Verify node exists
        fetched = repo.get_node_by_id(task_node.id)
        assert fetched is not None
        assert fetched.type == NodeType.TASK

        # 2. Enqueue
        sg_svc = SubgraphService(repo)
        svc = TaskService(repo, sg_svc)
        enqueue_result = svc.enqueue(project_id, task_node.id)
        task_id = enqueue_result.task_queue_id
        assert isinstance(task_id, uuid.UUID)
        assert enqueue_result.state == "READY"

        status = repo.get_task_status(task_id)
        assert status is not None
        assert status.state == "READY"

        # 3. Checkout → ContextPackage
        pkg = svc.checkout(project_id, agent_id)
        assert pkg is not None
        assert pkg.task.state == "IN_PROGRESS"
        assert pkg.node.id == task_node.id
        assert pkg.subgraph.root_id == str(task_node.id)

        # 4. Complete
        svc.complete(pkg.task.id)
        status = repo.get_task_status(pkg.task.id)
        assert status is not None
        assert status.state == "DONE"

        # 5. Activity log — should have CHECKOUT + COMPLETED
        activities = repo.get_task_activities(pkg.task.id)
        assert len(activities) == 2
        types = [a["activity_type"] for a in activities]
        assert types == ["TASK_CHECKOUT", "TASK_COMPLETED"]

    def test_full_flow_via_cli(self) -> None:
        """Same lifecycle via CLI commands."""
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        runner = CliRunner()
        proj = f"e2e-p3-{uuid.uuid4().hex[:8]}"

        # 1. init
        result = runner.invoke(app, ["init", "--project", proj])
        assert result.exit_code == 0

        # 2. Create TASK node via DB (simulates ingest of TASK .kgn)
        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            task_node = _make_task_node(pid, title="CLI-E2E-task")
            r.upsert_node(task_node)
            conn.commit()

        # 3. enqueue
        result = runner.invoke(app, ["task", "enqueue", str(task_node.id), "--project", proj])
        assert result.exit_code == 0

        # 4. checkout
        result = runner.invoke(app, ["task", "checkout", "--project", proj, "--agent", "e2e-bot"])
        assert result.exit_code == 0
        assert "CLI-E2E-task" in result.output

        # Get task_id
        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            tasks = r.list_tasks(pid, state="IN_PROGRESS")
            assert len(tasks) == 1
            task_id = str(tasks[0].id)

        # 5. complete
        result = runner.invoke(app, ["task", "complete", task_id, "--project", proj])
        assert result.exit_code == 0
        assert "completed" in result.output.lower()

        # 6. log
        result = runner.invoke(app, ["task", "log", task_id, "--project", proj])
        assert result.exit_code == 0
        assert "TASK_CHECKOUT" in result.output
        assert "TASK_COMPLETED" in result.output


# ══════════════════════════════════════════════════════════════════════
#  Scenario 2 — Lease Expiry Recovery
# ══════════════════════════════════════════════════════════════════════


class TestE2ELeaseExpiry:
    """Checkout with 0s lease → expire → requeue → re-checkout."""

    def test_lease_expiry_recovery(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        sg_svc = SubgraphService(repo)
        svc = TaskService(repo, sg_svc)

        # Enqueue
        task_node = _make_task_node(project_id, title="Lease-expiry task")
        repo.upsert_node(task_node)
        svc.enqueue(project_id, task_node.id)

        # Checkout with very short lease
        pkg = svc.checkout(project_id, agent_id, lease_duration_sec=1)
        assert pkg is not None
        assert pkg.task.state == "IN_PROGRESS"

        # Force lease expiry
        db_conn.execute(
            "UPDATE task_queue SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
            (pkg.task.id,),
        )

        # Requeue expired → should recover 1 task
        count = svc.requeue_expired(project_id)
        assert count == 1

        status = repo.get_task_status(pkg.task.id)
        assert status is not None
        assert status.state == "READY"

        # Re-checkout should succeed
        pkg2 = svc.checkout(project_id, agent_id)
        assert pkg2 is not None
        assert pkg2.task.id == pkg.task.id
        assert pkg2.task.state == "IN_PROGRESS"
        assert pkg2.task.attempts == 2  # second attempt


# ══════════════════════════════════════════════════════════════════════
#  Scenario 3 — max_attempts Exceeded
# ══════════════════════════════════════════════════════════════════════


class TestE2EMaxAttempts:
    """After max_attempts=3 failures, requeue should NOT recover."""

    def test_max_attempts_exceeded(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        db_conn: Connection,
    ) -> None:
        sg_svc = SubgraphService(repo)
        svc = TaskService(repo, sg_svc)

        task_node = _make_task_node(project_id, title="Max-attempts task")
        repo.upsert_node(task_node)
        svc.enqueue(project_id, task_node.id)

        # Default max_attempts = 3
        # Cycle: checkout → fail → force-expire → requeue, repeat 3 times
        for attempt_num in range(1, 4):
            pkg = svc.checkout(project_id, agent_id, lease_duration_sec=1)
            assert pkg is not None, f"Checkout #{attempt_num} should succeed"
            assert pkg.task.attempts == attempt_num

            svc.fail(pkg.task.id, reason=f"Attempt #{attempt_num} failed")

            if attempt_num < 3:
                # Force lease expiry for requeue
                db_conn.execute(
                    "UPDATE task_queue "
                    "SET state = 'READY', "
                    "    leased_by = NULL, "
                    "    lease_expires_at = NULL, "
                    "    updated_at = now() "
                    "WHERE id = %s",
                    (pkg.task.id,),
                )

        # After 3 failures, task should be FAILED
        status = repo.get_task_status(pkg.task.id)
        assert status is not None
        assert status.state == "FAILED"
        assert status.attempts == 3

        # requeue_expired should NOT recover it (attempts = max_attempts)
        # First, set it to look like an expired IN_PROGRESS (to test guard)
        db_conn.execute(
            "UPDATE task_queue "
            "SET state = 'IN_PROGRESS', "
            "    lease_expires_at = now() - interval '1 second' "
            "WHERE id = %s",
            (pkg.task.id,),
        )
        recovered = svc.requeue_expired(project_id)
        assert recovered == 0

        # Verify activities: 3 checkouts + 3 failures = 6
        activities = repo.get_task_activities(pkg.task.id)
        types = [a["activity_type"] for a in activities]
        checkout_count = types.count("TASK_CHECKOUT")
        fail_count = types.count("TASK_FAILED")
        assert checkout_count == 3
        assert fail_count == 3


# ══════════════════════════════════════════════════════════════════════
#  Scenario 4 — Handoff Package
# ══════════════════════════════════════════════════════════════════════


class TestE2EHandoffPackage:
    """Checkout with --format md / json verifies structured output."""

    def test_handoff_markdown(self) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo
        from kgn.models.edge import EdgeRecord
        from kgn.models.enums import EdgeType

        runner = CliRunner()
        proj = f"e2e-hoff-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", proj])

        # Create TASK + SPEC nodes + edge
        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            aid = r.get_or_create_agent(pid, "e2e-agent")

            task_node = _make_task_node(pid, title="Handoff-MD-task", created_by=aid)
            spec_node = _make_spec_node(pid, title="Related-Spec")
            r.upsert_node(task_node)
            r.upsert_node(spec_node)

            edge = EdgeRecord(
                project_id=pid,
                from_node_id=task_node.id,
                to_node_id=spec_node.id,
                type=EdgeType.DEPENDS_ON,
                note="task depends on spec",
                created_by=aid,
            )
            r.insert_edge(edge)
            r.enqueue_task(pid, task_node.id)
            conn.commit()

        result = runner.invoke(
            app,
            [
                "task",
                "checkout",
                "--project",
                proj,
                "--agent",
                "md-bot",
                "--format",
                "md",
            ],
        )
        assert result.exit_code == 0

        output = result.output
        # Markdown sections
        assert "# Task Handoff" in output
        assert "## 1. Task" in output
        assert "## 2. Subgraph" in output
        assert "## 3. Similar Cases" in output
        # Node info
        assert "Handoff-MD-task" in output
        # Subgraph should include the connected spec
        assert "Related-Spec" in output

    def test_handoff_json(self) -> None:
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo
        from kgn.models.edge import EdgeRecord
        from kgn.models.enums import EdgeType

        runner = CliRunner()
        proj = f"e2e-hoff-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", proj])

        # Create TASK + SPEC nodes + edge
        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            aid = r.get_or_create_agent(pid, "e2e-agent")

            task_node = _make_task_node(pid, title="Handoff-JSON-task", created_by=aid)
            spec_node = _make_spec_node(pid, title="JSON-Spec")
            r.upsert_node(task_node)
            r.upsert_node(spec_node)

            edge = EdgeRecord(
                project_id=pid,
                from_node_id=task_node.id,
                to_node_id=spec_node.id,
                type=EdgeType.IMPLEMENTS,
                note="task implements spec",
                created_by=aid,
            )
            r.insert_edge(edge)
            r.enqueue_task(pid, task_node.id)
            conn.commit()

        result = runner.invoke(
            app,
            [
                "task",
                "checkout",
                "--project",
                proj,
                "--agent",
                "json-bot",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert data["node"]["title"] == "Handoff-JSON-task"
        assert "subgraph" in data
        assert len(data["subgraph"]["nodes"]) >= 1
        assert len(data["subgraph"]["edges"]) >= 1
        assert "metadata" in data
        assert "kgn_version" in data["metadata"]

    def test_handoff_json_structure(self) -> None:
        """Verify all top-level keys in JSON handoff output."""
        from typer.testing import CliRunner

        from kgn.cli import app
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        runner = CliRunner()
        proj = f"e2e-hoff-{uuid.uuid4().hex[:8]}"
        runner.invoke(app, ["init", "--project", proj])

        with get_connection() as conn:
            r = Repo(conn)
            pid = r.get_or_create_project(proj)
            task_node = _make_task_node(pid, title="JSON-structure")
            r.upsert_node(task_node)
            r.enqueue_task(pid, task_node.id)
            conn.commit()

        result = runner.invoke(
            app,
            [
                "task",
                "checkout",
                "--project",
                proj,
                "--agent",
                "struct-bot",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert set(data.keys()) == {
            "task",
            "node",
            "subgraph",
            "similar_nodes",
            "metadata",
        }
        assert data["similar_nodes"] == []  # no embeddings
