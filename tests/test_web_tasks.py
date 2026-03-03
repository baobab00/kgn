"""Tests for Phase 9 Step 4 — Tasks API + Kanban Board.

Covers:
- GET /api/v1/tasks — task list grouped by state
- GET /api/v1/tasks/{id} — single task detail
- GET /api/v1/tasks/{id}/activities — activity log
- 400 invalid UUID / invalid state
- 404 non-existent task
- Kanban board HTML structure
- Task grouping by state
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from psycopg import Connection

from kgn.db.repository import KgnRepository
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _make_task_node(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    title: str = "Test Task",
    node_id: uuid.UUID | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=node_id or uuid.uuid4(),
        project_id=project_id,
        type=NodeType.TASK,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md=f"Body of {title}",
        content_hash=uuid.uuid4().hex,
        created_by=agent_id,
        created_at=datetime.now(tz=UTC),
    )


@contextmanager
def _mock_connection(db_conn: Connection) -> Generator[Connection, None, None]:
    yield db_conn


def _all_patches(db_conn: Connection):
    """Return patch contexts for all web route modules."""
    mock = lambda: _mock_connection(db_conn)  # noqa: E731
    return (
        patch("kgn.web.routes.nodes.get_connection", mock),
        patch("kgn.web.routes.health.get_connection", mock),
        patch("kgn.web.routes.subgraph.get_connection", mock),
        patch("kgn.web.routes.edges.get_connection", mock),
        patch("kgn.web.routes.tasks.get_connection", mock),
        patch("kgn.web.routes.stats.get_connection", mock),
        patch("kgn.web.routes.search.get_connection", mock),
    )


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def tasks_client(
    db_conn: Connection,
    project_id: uuid.UUID,
) -> TestClient:
    """TestClient with no task data."""
    from kgn.web.app import create_app

    app = create_app(project_name="test-tasks", project_id=project_id)

    patches = _all_patches(db_conn)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        yield TestClient(app)


@pytest.fixture
def tasks_with_data(
    db_conn: Connection,
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> tuple[TestClient, dict[str, uuid.UUID]]:
    """TestClient with tasks in various states.

    Creates:
    - task_a: READY, priority 5
    - task_b: READY, priority 10
    - task_c: IN_PROGRESS (checked out by agent), priority 1
    - task_d: DONE, priority 3
    """
    task_a = _make_task_node(project_id, agent_id, title="Write Tests")
    task_b = _make_task_node(project_id, agent_id, title="Implement Auth")
    task_c = _make_task_node(project_id, agent_id, title="Design API")
    task_d = _make_task_node(project_id, agent_id, title="Setup DB")

    for n in (task_a, task_b, task_c, task_d):
        repo.upsert_node(n)

    # Enqueue tasks
    qa_id = repo.enqueue_task(project_id, task_a.id, priority=5)
    qb_id = repo.enqueue_task(project_id, task_b.id, priority=10)
    qc_id = repo.enqueue_task(project_id, task_c.id, priority=1)
    qd_id = repo.enqueue_task(project_id, task_d.id, priority=3)

    # Checkout task_c (priority=1 → picked first) to make it IN_PROGRESS
    repo.checkout_task(project_id, agent_id)

    # Checkout task_d (priority=3 → picked next) then complete it
    repo.checkout_task(project_id, agent_id)
    repo.complete_task(qd_id)

    from kgn.web.app import create_app

    app = create_app(project_name="test-tasks", project_id=project_id)

    ids = {
        "task_a_node": task_a.id,
        "task_b_node": task_b.id,
        "task_c_node": task_c.id,
        "task_d_node": task_d.id,
        "qa": qa_id,
        "qb": qb_id,
        "qc": qc_id,
        "qd": qd_id,
    }

    patches = _all_patches(db_conn)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        yield TestClient(app), ids


# ── Task list tests ───────────────────────────────────────────────────


class TestTaskListAPI:
    """GET /api/v1/tasks tests."""

    def test_empty_task_list(self, tasks_client: TestClient) -> None:
        """Empty project should return empty grouped lists."""
        resp = tasks_client.get("/api/v1/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["grouped"]["READY"] == []
        assert data["grouped"]["IN_PROGRESS"] == []
        assert data["grouped"]["DONE"] == []
        assert data["grouped"]["FAILED"] == []

    def test_returns_all_tasks(
        self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Should return all 4 tasks."""
        client, ids = tasks_with_data
        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4

    def test_grouped_by_state(
        self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Tasks should be grouped correctly."""
        client, ids = tasks_with_data
        resp = client.get("/api/v1/tasks")
        data = resp.json()
        grouped = data["grouped"]

        assert len(grouped["READY"]) == 2
        assert len(grouped["IN_PROGRESS"]) == 1
        assert len(grouped["DONE"]) == 1
        assert len(grouped["FAILED"]) == 0

    def test_state_filter(self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]) -> None:
        """State filter should return only matching tasks."""
        client, ids = tasks_with_data
        resp = client.get("/api/v1/tasks?state=READY")
        data = resp.json()
        assert data["total"] == 2
        for t in data["tasks"]:
            assert t["state"] == "READY"

    def test_invalid_state_returns_400(self, tasks_client: TestClient) -> None:
        resp = tasks_client.get("/api/v1/tasks?state=INVALID")
        assert resp.status_code == 400
        assert "Invalid state" in resp.json()["detail"]

    def test_task_has_title(self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]) -> None:
        """Tasks should have resolved node title."""
        client, ids = tasks_with_data
        resp = client.get("/api/v1/tasks")
        titles = {t["title"] for t in resp.json()["tasks"]}
        assert "Write Tests" in titles
        assert "Design API" in titles

    def test_task_structure(self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]) -> None:
        """Task dict should have expected fields."""
        client, ids = tasks_with_data
        resp = client.get("/api/v1/tasks")
        task = resp.json()["tasks"][0]

        assert "id" in task
        assert "task_node_id" in task
        assert "priority" in task
        assert "state" in task
        assert "title" in task
        assert "attempts" in task
        assert "max_attempts" in task
        assert "created_at" in task

    def test_in_progress_has_lease(
        self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """IN_PROGRESS task should have lease_expires_at set."""
        client, ids = tasks_with_data
        resp = client.get("/api/v1/tasks")
        in_prog = resp.json()["grouped"]["IN_PROGRESS"]
        assert len(in_prog) == 1
        assert in_prog[0]["lease_expires_at"] is not None
        assert in_prog[0]["leased_by"] is not None

    def test_priority_ordering(
        self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Tasks should be ordered by priority ASC."""
        client, ids = tasks_with_data
        resp = client.get("/api/v1/tasks?state=READY")
        tasks = resp.json()["tasks"]
        priorities = [t["priority"] for t in tasks]
        assert priorities == sorted(priorities)


# ── Single task tests ─────────────────────────────────────────────────


class TestTaskDetailAPI:
    """GET /api/v1/tasks/{id} tests."""

    def test_invalid_uuid_returns_400(self, tasks_client: TestClient) -> None:
        resp = tasks_client.get("/api/v1/tasks/not-a-uuid")
        assert resp.status_code == 400

    def test_not_found_returns_404(self, tasks_client: TestClient) -> None:
        fake_id = uuid.uuid4()
        resp = tasks_client.get(f"/api/v1/tasks/{fake_id}")
        assert resp.status_code == 404

    def test_returns_task_detail(
        self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Should return task with title."""
        client, ids = tasks_with_data
        resp = client.get(f"/api/v1/tasks/{ids['qa']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(ids["qa"])
        assert data["title"] == "Write Tests"
        assert data["priority"] == 5


# ── Activities tests ──────────────────────────────────────────────────


class TestTaskActivitiesAPI:
    """GET /api/v1/tasks/{id}/activities tests."""

    def test_invalid_uuid_returns_400(self, tasks_client: TestClient) -> None:
        resp = tasks_client.get("/api/v1/tasks/not-a-uuid/activities")
        assert resp.status_code == 400

    def test_not_found_returns_404(self, tasks_client: TestClient) -> None:
        fake_id = uuid.uuid4()
        resp = tasks_client.get(f"/api/v1/tasks/{fake_id}/activities")
        assert resp.status_code == 404

    def test_returns_activities(
        self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Task with activity should return activities list."""
        client, ids = tasks_with_data
        # qc was checked out, so it should have activity
        resp = client.get(f"/api/v1/tasks/{ids['qc']}/activities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == str(ids["qc"])
        assert isinstance(data["activities"], list)
        assert isinstance(data["total"], int)

    def test_empty_activities(
        self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        """Task with no activity should return empty list."""
        client, ids = tasks_with_data
        # qa was never checked out
        resp = client.get(f"/api/v1/tasks/{ids['qa']}/activities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["activities"] == []
        assert data["total"] == 0


# ── Kanban HTML tests ────────────────────────────────────────────────


class TestKanbanHTML:
    """HTML structure tests for kanban board."""

    def test_kanban_css_linked(
        self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        client, _ = tasks_with_data
        resp = client.get("/")
        assert "kanban.css" in resp.text

    def test_kanban_js_linked(
        self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        client, _ = tasks_with_data
        resp = client.get("/")
        assert "kanban.js" in resp.text

    def test_kanban_container(
        self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]
    ) -> None:
        client, _ = tasks_with_data
        resp = client.get("/")
        assert 'id="kanban-container"' in resp.text

    def test_tasks_tab(self, tasks_with_data: tuple[TestClient, dict[str, uuid.UUID]]) -> None:
        """Nav should include Tasks tab."""
        client, _ = tasks_with_data
        resp = client.get("/")
        assert "Tasks" in resp.text
        assert 'data-tab="kanban"' in resp.text
