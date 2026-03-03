"""Tests for Mermaid diagram generation."""

from __future__ import annotations

import uuid
from pathlib import Path

from kgn.db.repository import KgnRepository
from kgn.graph.mermaid import (
    MermaidGenerator,
    MermaidResult,
    _classdefs,
    _edge_line,
    _escape_label,
    _node_line,
    _short_id,
    _task_classdefs,
)
from kgn.models.edge import EdgeRecord
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _insert_node(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    node_type: NodeType = NodeType.GOAL,
    status: NodeStatus = NodeStatus.ACTIVE,
    title: str = "test-node",
) -> uuid.UUID:
    """Insert a minimal node and return its id."""
    node_id = uuid.uuid4()
    node = NodeRecord(
        id=node_id,
        project_id=project_id,
        type=node_type,
        status=status,
        title=title,
        body_md=f"body of {title}",
        file_path=f"test/{title}.kgn",
        content_hash=uuid.uuid4().hex,
        tags=[],
        confidence=None,
        created_by=agent_id,
    )
    repo.upsert_node(node)
    return node_id


def _insert_edge(
    repo: KgnRepository,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    edge_type: EdgeType = EdgeType.DEPENDS_ON,
) -> None:
    """Insert an edge."""
    edge = EdgeRecord(
        project_id=project_id,
        from_node_id=from_id,
        to_node_id=to_id,
        type=edge_type,
        note="",
        created_by=agent_id,
    )
    repo.insert_edge(edge)


# ── Unit tests: helpers ────────────────────────────────────────────────


class TestShortId:
    def test_returns_12_chars(self) -> None:
        uid = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
        assert _short_id(uid) == "550e8400e29b"
        assert len(_short_id(uid)) == 12

    def test_string_input(self) -> None:
        assert _short_id("550e8400-e29b-41d4-a716-446655440000") == "550e8400e29b"


class TestEscapeLabel:
    def test_quotes_replaced(self) -> None:
        assert '"' not in _escape_label('say "hello"')

    def test_brackets_replaced(self) -> None:
        result = _escape_label("list [items]")
        assert "[" not in result
        assert "]" not in result

    def test_newlines_removed(self) -> None:
        assert "\n" not in _escape_label("line1\nline2")


class TestNodeLine:
    def test_contains_type_and_title(self) -> None:
        line = _node_line("550e8400-e29b-41d4-a716-446655440000", "GOAL", "My Goal")
        assert "GOAL-550e8400" in line
        assert "My Goal" in line
        assert ":::goal" in line

    def test_with_status(self) -> None:
        line = _node_line("550e8400-e29b-41d4-a716-446655440000", "TASK", "Job", "ACTIVE")
        assert "(ACTIVE)" in line

    def test_without_status(self) -> None:
        line = _node_line("550e8400-e29b-41d4-a716-446655440000", "SPEC", "Design")
        assert "()" not in line


class TestEdgeLine:
    def test_format(self) -> None:
        line = _edge_line(
            "550e8400-e29b-41d4-a716-446655440000",
            "661f9500-e29b-41d4-a716-446655440000",
            "IMPLEMENTS",
        )
        assert "550e8400" in line
        assert "661f9500" in line
        assert "|IMPLEMENTS|" in line


class TestClassDefs:
    def test_all_node_types(self) -> None:
        lines = _classdefs()
        for ntype in NodeType:
            assert any(ntype.value.lower() in line for line in lines)

    def test_task_state_defs(self) -> None:
        lines = _task_classdefs()
        for state in ["ready", "in_progress", "blocked", "done", "failed"]:
            assert any(state in line for line in lines)


# ── Integration tests: generate_graph ──────────────────────────────────


class TestGenerateGraph:
    def test_empty_project(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        gen = MermaidGenerator(repo)
        result = gen.generate_graph(project_id)
        assert isinstance(result, MermaidResult)
        assert result.node_count == 0
        assert result.edge_count == 0
        assert "flowchart TD" in result.diagram

    def test_single_node(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _insert_node(repo, project_id, agent_id, title="Auth System", node_type=NodeType.GOAL)
        gen = MermaidGenerator(repo)
        result = gen.generate_graph(project_id)
        assert result.node_count == 1
        assert "Auth System" in result.diagram
        assert ":::goal" in result.diagram

    def test_nodes_and_edges(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        g = _insert_node(repo, project_id, agent_id, title="Goal", node_type=NodeType.GOAL)
        s = _insert_node(repo, project_id, agent_id, title="Spec", node_type=NodeType.SPEC)
        _insert_edge(repo, project_id, agent_id, g, s, EdgeType.IMPLEMENTS)

        gen = MermaidGenerator(repo)
        result = gen.generate_graph(project_id)
        assert result.node_count == 2
        assert result.edge_count == 1
        assert "|IMPLEMENTS|" in result.diagram

    def test_include_status(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _insert_node(repo, project_id, agent_id, title="N1", status=NodeStatus.ACTIVE)
        gen = MermaidGenerator(repo)

        with_status = gen.generate_graph(project_id, include_status=True)
        assert "(ACTIVE)" in with_status.diagram

        without_status = gen.generate_graph(project_id, include_status=False)
        assert "(ACTIVE)" not in without_status.diagram

    def test_classdefs_in_output(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        _insert_node(repo, project_id, agent_id)
        gen = MermaidGenerator(repo)
        result = gen.generate_graph(project_id)
        assert "classDef goal" in result.diagram
        assert "classDef task" in result.diagram

    def test_subgraph_with_root(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        root = _insert_node(repo, project_id, agent_id, title="Root", node_type=NodeType.GOAL)
        child = _insert_node(repo, project_id, agent_id, title="Child", node_type=NodeType.SPEC)
        _insert_edge(repo, project_id, agent_id, root, child, EdgeType.IMPLEMENTS)
        # unconnected node
        _insert_node(repo, project_id, agent_id, title="Isolated", node_type=NodeType.ISSUE)

        gen = MermaidGenerator(repo)
        result = gen.generate_graph(project_id, root_node_id=root, depth=1)
        # root+child only, not the isolated node
        assert result.node_count == 2
        assert "Root" in result.diagram
        assert "Child" in result.diagram

    def test_all_node_types_styled(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        for nt in NodeType:
            _insert_node(repo, project_id, agent_id, title=f"N-{nt.value}", node_type=nt)

        gen = MermaidGenerator(repo)
        result = gen.generate_graph(project_id)
        assert result.node_count == len(NodeType)
        for nt in NodeType:
            assert f":::{nt.value.lower()}" in result.diagram


# ── Integration tests: generate_task_board ─────────────────────────────


class TestGenerateTaskBoard:
    def test_empty_project(self, repo: KgnRepository, project_id: uuid.UUID) -> None:
        gen = MermaidGenerator(repo)
        result = gen.generate_task_board(project_id)
        assert result.node_count == 0
        assert "flowchart LR" in result.diagram

    def test_tasks_grouped_by_state(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        t1 = _insert_node(repo, project_id, agent_id, title="Task Ready", node_type=NodeType.TASK)
        t2 = _insert_node(repo, project_id, agent_id, title="Task Done", node_type=NodeType.TASK)

        repo.enqueue_task(project_id=project_id, task_node_id=t1, priority=1)
        repo.enqueue_task(project_id=project_id, task_node_id=t2, priority=2)
        # Complete one
        task_item = repo.checkout_task(project_id, agent_id)
        if task_item and task_item.task_node_id == t1:
            pass  # t1 is IN_PROGRESS, t2 is READY

        gen = MermaidGenerator(repo)
        result = gen.generate_task_board(project_id)
        assert result.node_count >= 2
        assert "subgraph" in result.diagram

    def test_task_board_classdefs(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        t = _insert_node(repo, project_id, agent_id, title="X", node_type=NodeType.TASK)
        repo.enqueue_task(project_id=project_id, task_node_id=t, priority=1)

        gen = MermaidGenerator(repo)
        result = gen.generate_task_board(project_id)
        assert "classDef ready" in result.diagram


# ── Integration tests: generate_readme ─────────────────────────────────


class TestGenerateReadme:
    def test_creates_file(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        _insert_node(repo, project_id, agent_id, title="Goal A", node_type=NodeType.GOAL)

        gen = MermaidGenerator(repo)
        path = gen.generate_readme(project_id, "test-project", tmp_path)
        assert path.exists()
        assert path.name == "README.md"

    def test_content_includes_mermaid(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        g = _insert_node(repo, project_id, agent_id, title="Goal B", node_type=NodeType.GOAL)
        s = _insert_node(repo, project_id, agent_id, title="Spec B", node_type=NodeType.SPEC)
        _insert_edge(repo, project_id, agent_id, g, s, EdgeType.IMPLEMENTS)

        gen = MermaidGenerator(repo)
        path = gen.generate_readme(project_id, "my-project", tmp_path)
        content = path.read_text(encoding="utf-8")

        assert "```mermaid" in content
        assert "flowchart TD" in content
        assert "my-project" in content

    def test_content_includes_stats_table(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        _insert_node(repo, project_id, agent_id, title="G1", node_type=NodeType.GOAL)
        _insert_node(repo, project_id, agent_id, title="S1", node_type=NodeType.SPEC)

        gen = MermaidGenerator(repo)
        path = gen.generate_readme(project_id, "stats-project", tmp_path)
        content = path.read_text(encoding="utf-8")

        assert "Node Statistics" in content
        assert "| GOAL |" in content
        assert "| SPEC |" in content

    def test_readme_overwrite(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        gen = MermaidGenerator(repo)
        gen.generate_readme(project_id, "p1", tmp_path)
        _insert_node(repo, project_id, agent_id, title="New Node", node_type=NodeType.TASK)
        path = gen.generate_readme(project_id, "p1", tmp_path)
        content = path.read_text(encoding="utf-8")
        # Second call re-generates so new node IS included
        assert "New Node" in content
        # File was overwritten, not appended (single heading)
        assert content.count("# p1") == 1

    def test_creates_target_dir(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        nested = tmp_path / "nested" / "deep"
        gen = MermaidGenerator(repo)
        path = gen.generate_readme(project_id, "nested-project", nested)
        assert path.exists()
        assert nested.exists()

    def test_task_board_included_when_tasks_exist(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        t = _insert_node(repo, project_id, agent_id, title="Board Task", node_type=NodeType.TASK)
        repo.enqueue_task(project_id=project_id, task_node_id=t, priority=1)

        gen = MermaidGenerator(repo)
        path = gen.generate_readme(project_id, "board-project", tmp_path)
        content = path.read_text(encoding="utf-8")
        assert "Task Board" in content
        assert "flowchart LR" in content

    def test_no_task_board_when_empty(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        _insert_node(repo, project_id, agent_id, title="Just Goal", node_type=NodeType.GOAL)

        gen = MermaidGenerator(repo)
        path = gen.generate_readme(project_id, "no-board", tmp_path)
        content = path.read_text(encoding="utf-8")
        assert "Task Board" not in content


# ── MermaidResult dataclass ────────────────────────────────────────────


class TestMermaidResult:
    def test_fields(self) -> None:
        r = MermaidResult(diagram="flowchart TD\n", node_count=3, edge_count=2)
        assert r.diagram == "flowchart TD\n"
        assert r.node_count == 3
        assert r.edge_count == 2
