"""Tests for WorkflowEngine — declarative TASK decomposition.

Covers:
- WorkflowTemplate / WorkflowStep data classes
- create_workflow_template() factory from dict
- WorkflowEngine.register / list_templates / get_template
- WorkflowEngine.execute() — full DAG creation
- Built-in templates (design-to-impl, issue-resolution, knowledge-indexing)
- Edge creation (trigger edges + DEPENDS_ON)
- Error cases: missing template, wrong node type/status, unknown dependency
- CLI workflow list / run commands
- MCP workflow_list / workflow_run tools

Target: 30+ tests
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest

from kgn.db.repository import KgnRepository
from kgn.errors import KgnError
from kgn.graph.subgraph import SubgraphService
from kgn.models.enums import (
    AgentRole,
    EdgeType,
    NodeStatus,
    NodeType,
)
from kgn.models.node import NodeRecord
from kgn.orchestration.templates import (
    BUILTIN_TEMPLATES,
    DESIGN_TO_IMPL,
    ISSUE_RESOLUTION,
    KNOWLEDGE_INDEXING,
    register_builtins,
)
from kgn.orchestration.workflow import (
    CreatedNode,
    WorkflowEngine,
    WorkflowExecutionResult,
    WorkflowStep,
    WorkflowTemplate,
    create_workflow_template,
)
from kgn.task.service import TaskService

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def subgraph_service(repo: KgnRepository) -> SubgraphService:
    return SubgraphService(repo)


@pytest.fixture
def task_service(repo: KgnRepository, subgraph_service: SubgraphService) -> TaskService:
    return TaskService(repo, subgraph_service)


@pytest.fixture
def engine(repo: KgnRepository, task_service: TaskService) -> WorkflowEngine:
    eng = WorkflowEngine(repo, task_service)
    register_builtins(eng)
    return eng


@pytest.fixture
def goal_node(repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID) -> NodeRecord:
    """Create a GOAL node for triggering workflows."""
    node = NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.GOAL,
        status=NodeStatus.ACTIVE,
        title="Test Goal",
        body_md="A test goal for workflow testing",
        created_by=agent_id,
    )
    repo.upsert_node(node)
    return node


@pytest.fixture
def issue_node(repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID) -> NodeRecord:
    """Create an ISSUE node for triggering issue-resolution workflow."""
    node = NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.ISSUE,
        status=NodeStatus.ACTIVE,
        title="Test Issue",
        body_md="A test issue for workflow testing",
        created_by=agent_id,
    )
    repo.upsert_node(node)
    return node


@pytest.fixture
def summary_node(repo: KgnRepository, project_id: uuid.UUID, agent_id: uuid.UUID) -> NodeRecord:
    """Create a SUMMARY node for triggering knowledge-indexing workflow."""
    node = NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=NodeType.SUMMARY,
        status=NodeStatus.ACTIVE,
        title="Test Summary",
        body_md="A test summary for workflow testing",
        created_by=agent_id,
    )
    repo.upsert_node(node)
    return node


# ═══════════════════════════════════════════════════════════════════════
# 1. Data classes & factory
# ═══════════════════════════════════════════════════════════════════════


class TestWorkflowDataClasses:
    """WorkflowStep, WorkflowTemplate, CreatedNode data class tests."""

    def test_workflow_step_defaults(self):
        step = WorkflowStep(
            id="test",
            action="create_subtask",
            node_type=NodeType.TASK,
            title_template="{parent.title} — Test",
            assign_role=AgentRole.WORKER,
            edge=EdgeType.IMPLEMENTS,
        )
        assert step.depends_on == []

    def test_workflow_step_with_dependencies(self):
        step = WorkflowStep(
            id="review",
            action="create_subtask",
            node_type=NodeType.TASK,
            title_template="Review",
            assign_role=AgentRole.REVIEWER,
            edge=EdgeType.DERIVED_FROM,
            depends_on=["impl", "test"],
        )
        assert step.depends_on == ["impl", "test"]

    def test_workflow_template_defaults(self):
        tmpl = WorkflowTemplate(name="test", display_name="Test", trigger_node_type=NodeType.GOAL)
        assert tmpl.trigger_status == NodeStatus.ACTIVE
        assert tmpl.steps == []

    def test_execution_result_counts(self):
        from kgn.task.dependency import DependencyCheckResult
        from kgn.task.service import EnqueueResult

        dep_check = DependencyCheckResult(all_satisfied=True)
        enqueue = EnqueueResult(
            task_queue_id=uuid.uuid4(), state="READY", dependency_check=dep_check
        )
        result = WorkflowExecutionResult(
            trigger_node_id=uuid.uuid4(),
            template_name="test",
            created_nodes=[
                CreatedNode("spec", uuid.uuid4(), NodeType.SPEC, "Spec", None),
                CreatedNode("impl", uuid.uuid4(), NodeType.TASK, "Impl", enqueue),
            ],
        )
        assert result.node_count == 2
        assert result.task_count == 1


class TestCreateWorkflowTemplate:
    """create_workflow_template() factory tests."""

    def test_valid_template(self):
        data = {
            "name": "test-wf",
            "display_name": "Test WF",
            "trigger": {"node_type": "GOAL", "status": "ACTIVE"},
            "steps": [
                {
                    "id": "s1",
                    "action": "create_subtask",
                    "node_type": "TASK",
                    "title_template": "{parent.title} — S1",
                    "assign_role": "worker",
                    "edge": "IMPLEMENTS",
                    "depends_on": [],
                }
            ],
        }
        tmpl = create_workflow_template(data)
        assert tmpl.name == "test-wf"
        assert tmpl.display_name == "Test WF"
        assert tmpl.trigger_node_type == NodeType.GOAL
        assert len(tmpl.steps) == 1
        assert tmpl.steps[0].assign_role == AgentRole.WORKER

    def test_default_display_name(self):
        data = {
            "name": "no-display",
            "trigger": {"node_type": "ISSUE"},
            "steps": [],
        }
        tmpl = create_workflow_template(data)
        assert tmpl.display_name == "no-display"

    def test_default_status_active(self):
        data = {
            "name": "default-status",
            "trigger": {"node_type": "GOAL"},
            "steps": [],
        }
        tmpl = create_workflow_template(data)
        assert tmpl.trigger_status == NodeStatus.ACTIVE

    def test_missing_key_raises(self):
        with pytest.raises(KgnError, match="missing key"):
            create_workflow_template({"trigger": {"node_type": "GOAL"}})  # no 'name'

    def test_invalid_node_type_raises(self):
        with pytest.raises(KgnError, match="Invalid workflow template"):
            create_workflow_template(
                {
                    "name": "bad",
                    "trigger": {"node_type": "NONEXISTENT"},
                    "steps": [],
                }
            )

    def test_invalid_role_raises(self):
        with pytest.raises(KgnError, match="Invalid workflow template"):
            create_workflow_template(
                {
                    "name": "bad-role",
                    "trigger": {"node_type": "GOAL"},
                    "steps": [
                        {
                            "id": "s1",
                            "node_type": "TASK",
                            "title_template": "T",
                            "assign_role": "nonexistent_role",
                            "edge": "IMPLEMENTS",
                        }
                    ],
                }
            )


# ═══════════════════════════════════════════════════════════════════════
# 2. Built-in templates
# ═══════════════════════════════════════════════════════════════════════


class TestBuiltinTemplates:
    """Validate the three built-in templates."""

    def test_builtin_count(self):
        assert len(BUILTIN_TEMPLATES) == 3

    def test_design_to_impl(self):
        assert DESIGN_TO_IMPL.name == "design-to-impl"
        assert DESIGN_TO_IMPL.trigger_node_type == NodeType.GOAL
        assert len(DESIGN_TO_IMPL.steps) == 4
        step_ids = [s.id for s in DESIGN_TO_IMPL.steps]
        assert step_ids == ["spec", "arch", "impl", "review"]

    def test_design_to_impl_dependencies(self):
        steps = {s.id: s for s in DESIGN_TO_IMPL.steps}
        assert steps["spec"].depends_on == []
        assert steps["arch"].depends_on == ["spec"]
        assert steps["impl"].depends_on == ["arch"]
        assert steps["review"].depends_on == ["impl"]

    def test_design_to_impl_roles(self):
        steps = {s.id: s for s in DESIGN_TO_IMPL.steps}
        assert steps["spec"].assign_role == AgentRole.GENESIS
        assert steps["arch"].assign_role == AgentRole.GENESIS
        assert steps["impl"].assign_role == AgentRole.WORKER
        assert steps["review"].assign_role == AgentRole.REVIEWER

    def test_issue_resolution(self):
        assert ISSUE_RESOLUTION.name == "issue-resolution"
        assert ISSUE_RESOLUTION.trigger_node_type == NodeType.ISSUE
        assert len(ISSUE_RESOLUTION.steps) == 2
        assert ISSUE_RESOLUTION.steps[0].id == "fix"
        assert ISSUE_RESOLUTION.steps[1].depends_on == ["fix"]

    def test_knowledge_indexing(self):
        assert KNOWLEDGE_INDEXING.name == "knowledge-indexing"
        assert KNOWLEDGE_INDEXING.trigger_node_type == NodeType.SUMMARY
        assert len(KNOWLEDGE_INDEXING.steps) == 2
        assert KNOWLEDGE_INDEXING.steps[0].assign_role == AgentRole.INDEXER

    def test_register_builtins(self):
        mock_repo = MagicMock()
        mock_task_svc = MagicMock()
        eng = WorkflowEngine(mock_repo, mock_task_svc)
        register_builtins(eng)
        assert len(eng.list_templates()) == 3


# ═══════════════════════════════════════════════════════════════════════
# 3. WorkflowEngine — template management
# ═══════════════════════════════════════════════════════════════════════


class TestEngineTemplateManagement:
    """Template register/list/get operations."""

    def test_register_and_list(self):
        mock_repo = MagicMock()
        mock_task_svc = MagicMock()
        eng = WorkflowEngine(mock_repo, mock_task_svc)
        assert eng.list_templates() == []

        eng.register(DESIGN_TO_IMPL)
        assert len(eng.list_templates()) == 1

    def test_get_template_found(self):
        mock_repo = MagicMock()
        mock_task_svc = MagicMock()
        eng = WorkflowEngine(mock_repo, mock_task_svc)
        eng.register(ISSUE_RESOLUTION)
        assert eng.get_template("issue-resolution") is not None

    def test_get_template_not_found(self):
        mock_repo = MagicMock()
        mock_task_svc = MagicMock()
        eng = WorkflowEngine(mock_repo, mock_task_svc)
        assert eng.get_template("nonexistent") is None

    def test_register_overwrites(self):
        mock_repo = MagicMock()
        mock_task_svc = MagicMock()
        eng = WorkflowEngine(mock_repo, mock_task_svc)
        eng.register(DESIGN_TO_IMPL)
        eng.register(DESIGN_TO_IMPL)  # overwrite
        assert len(eng.list_templates()) == 1

    def test_list_sorted_by_name(self):
        mock_repo = MagicMock()
        mock_task_svc = MagicMock()
        eng = WorkflowEngine(mock_repo, mock_task_svc)
        register_builtins(eng)
        names = [t.name for t in eng.list_templates()]
        assert names == sorted(names)


# ═══════════════════════════════════════════════════════════════════════
# 4. WorkflowEngine.execute() — integration tests (DB required)
# ═══════════════════════════════════════════════════════════════════════


class TestWorkflowExecution:
    """Full execution tests with real DB."""

    def test_design_to_impl_creates_4_nodes(
        self,
        engine: WorkflowEngine,
        goal_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        repo: KgnRepository,
    ):
        result = engine.execute(goal_node.id, project_id, agent_id, "design-to-impl")
        assert result.node_count == 4
        assert result.template_name == "design-to-impl"
        assert result.trigger_node_id == goal_node.id

    def test_design_to_impl_creates_2_tasks(
        self,
        engine: WorkflowEngine,
        goal_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        result = engine.execute(goal_node.id, project_id, agent_id, "design-to-impl")
        # TASK nodes: impl + review = 2
        assert result.task_count == 2

    def test_design_to_impl_node_types(
        self,
        engine: WorkflowEngine,
        goal_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        result = engine.execute(goal_node.id, project_id, agent_id, "design-to-impl")
        types = {cn.step_id: cn.node_type for cn in result.created_nodes}
        assert types["spec"] == NodeType.SPEC
        assert types["arch"] == NodeType.ARCH
        assert types["impl"] == NodeType.TASK
        assert types["review"] == NodeType.TASK

    def test_design_to_impl_titles_rendered(
        self,
        engine: WorkflowEngine,
        goal_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        result = engine.execute(goal_node.id, project_id, agent_id, "design-to-impl")
        for cn in result.created_nodes:
            assert "Test Goal" in cn.title

    def test_design_to_impl_trigger_edges(
        self,
        engine: WorkflowEngine,
        goal_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        repo: KgnRepository,
    ):
        """Each created node should have an edge to the trigger node."""
        engine.execute(goal_node.id, project_id, agent_id, "design-to-impl")
        edges_to_trigger = repo.get_edges_to(goal_node.id)
        assert len(edges_to_trigger) >= 4  # spec, arch, impl, review all point to goal

    def test_design_to_impl_depends_on_edges(
        self,
        engine: WorkflowEngine,
        goal_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        repo: KgnRepository,
    ):
        """Inter-step DEPENDS_ON edges should be created."""
        result = engine.execute(goal_node.id, project_id, agent_id, "design-to-impl")
        node_map = {cn.step_id: cn.node_id for cn in result.created_nodes}

        # arch depends_on spec
        arch_edges = repo.get_edges_from(node_map["arch"])
        dep_targets = {e.to_node_id for e in arch_edges if e.type == EdgeType.DEPENDS_ON}
        assert node_map["spec"] in dep_targets

        # impl depends_on arch
        impl_edges = repo.get_edges_from(node_map["impl"])
        dep_targets = {e.to_node_id for e in impl_edges if e.type == EdgeType.DEPENDS_ON}
        assert node_map["arch"] in dep_targets

        # review depends_on impl
        review_edges = repo.get_edges_from(node_map["review"])
        dep_targets = {e.to_node_id for e in review_edges if e.type == EdgeType.DEPENDS_ON}
        assert node_map["impl"] in dep_targets

    def test_design_to_impl_task_queue_states(
        self,
        engine: WorkflowEngine,
        goal_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """impl depends on arch (ARCH, non-TASK) → READY;
        review depends on impl (TASK, not yet DONE) → BLOCKED."""
        result = engine.execute(goal_node.id, project_id, agent_id, "design-to-impl")
        task_nodes = {cn.step_id: cn for cn in result.created_nodes if cn.enqueue_result}
        # impl depends on arch (ARCH type, not TASK) → dependency skipped → READY
        assert task_nodes["impl"].enqueue_result.state == "READY"
        # review depends on impl (TASK, not yet DONE) → BLOCKED
        assert task_nodes["review"].enqueue_result.state == "BLOCKED"

    def test_issue_resolution_creates_2_nodes(
        self,
        engine: WorkflowEngine,
        issue_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        result = engine.execute(issue_node.id, project_id, agent_id, "issue-resolution")
        assert result.node_count == 2
        assert result.task_count == 2

    def test_issue_resolution_fix_ready(
        self,
        engine: WorkflowEngine,
        issue_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """fix has no deps → READY; review depends on fix → BLOCKED."""
        result = engine.execute(issue_node.id, project_id, agent_id, "issue-resolution")
        task_map = {cn.step_id: cn for cn in result.created_nodes}
        assert task_map["fix"].enqueue_result.state == "READY"
        assert task_map["review"].enqueue_result.state == "BLOCKED"

    def test_issue_resolution_edge_types(
        self,
        engine: WorkflowEngine,
        issue_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        repo: KgnRepository,
    ):
        engine.execute(issue_node.id, project_id, agent_id, "issue-resolution")
        edges_to_issue = repo.get_edges_to(issue_node.id)
        edge_types = {e.type for e in edges_to_issue}
        assert EdgeType.RESOLVES in edge_types

    def test_knowledge_indexing_creates_2_nodes(
        self,
        engine: WorkflowEngine,
        summary_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        result = engine.execute(summary_node.id, project_id, agent_id, "knowledge-indexing")
        assert result.node_count == 2
        assert result.task_count == 2

    def test_knowledge_indexing_index_ready(
        self,
        engine: WorkflowEngine,
        summary_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        result = engine.execute(summary_node.id, project_id, agent_id, "knowledge-indexing")
        task_map = {cn.step_id: cn for cn in result.created_nodes}
        assert task_map["index"].enqueue_result.state == "READY"
        assert task_map["review"].enqueue_result.state == "BLOCKED"

    def test_nodes_persisted_in_db(
        self,
        engine: WorkflowEngine,
        goal_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        repo: KgnRepository,
    ):
        result = engine.execute(goal_node.id, project_id, agent_id, "design-to-impl")
        for cn in result.created_nodes:
            db_node = repo.get_node_by_id(cn.node_id)
            assert db_node is not None
            assert db_node.title == cn.title
            assert db_node.type == cn.node_type

    def test_node_tags_include_workflow(
        self,
        engine: WorkflowEngine,
        goal_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        repo: KgnRepository,
    ):
        result = engine.execute(goal_node.id, project_id, agent_id, "design-to-impl")
        for cn in result.created_nodes:
            db_node = repo.get_node_by_id(cn.node_id)
            assert "workflow" in db_node.tags

    def test_custom_priority(
        self,
        engine: WorkflowEngine,
        issue_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        result = engine.execute(
            issue_node.id,
            project_id,
            agent_id,
            "issue-resolution",
            priority=50,
        )
        assert result.task_count == 2  # still creates tasks with custom priority


# ═══════════════════════════════════════════════════════════════════════
# 5. Error cases
# ═══════════════════════════════════════════════════════════════════════


class TestWorkflowErrors:
    """Error handling in WorkflowEngine.execute()."""

    def test_template_not_found(
        self,
        engine: WorkflowEngine,
        goal_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        with pytest.raises(KgnError, match="not found"):
            engine.execute(goal_node.id, project_id, agent_id, "nonexistent-template")

    def test_trigger_node_not_found(
        self,
        engine: WorkflowEngine,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        fake_id = uuid.uuid4()
        with pytest.raises(KgnError, match="not found"):
            engine.execute(fake_id, project_id, agent_id, "design-to-impl")

    def test_wrong_trigger_node_type(
        self,
        engine: WorkflowEngine,
        issue_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """design-to-impl expects GOAL, not ISSUE."""
        with pytest.raises(KgnError, match="expected GOAL"):
            engine.execute(issue_node.id, project_id, agent_id, "design-to-impl")

    def test_wrong_trigger_node_status(
        self,
        engine: WorkflowEngine,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Trigger node with DEPRECATED status should fail."""
        node = NodeRecord(
            id=uuid.uuid4(),
            project_id=project_id,
            type=NodeType.GOAL,
            status=NodeStatus.DEPRECATED,
            title="Deprecated Goal",
            body_md="",
            created_by=agent_id,
        )
        repo.upsert_node(node)
        with pytest.raises(KgnError, match="expected ACTIVE"):
            engine.execute(node.id, project_id, agent_id, "design-to-impl")

    def test_wrong_project_id(
        self,
        engine: WorkflowEngine,
        goal_node: NodeRecord,
        agent_id: uuid.UUID,
    ):
        """Trigger node from different project should fail."""
        fake_project = uuid.uuid4()
        with pytest.raises(KgnError, match="does not belong"):
            engine.execute(goal_node.id, fake_project, agent_id, "design-to-impl")

    def test_unknown_step_dependency(
        self,
        repo: KgnRepository,
        task_service: TaskService,
        goal_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ):
        """Template with reference to non-existent step should fail."""
        bad_tmpl = WorkflowTemplate(
            name="bad-deps",
            display_name="Bad Deps",
            trigger_node_type=NodeType.GOAL,
            steps=[
                WorkflowStep(
                    id="s1",
                    action="create_subtask",
                    node_type=NodeType.TASK,
                    title_template="T",
                    assign_role=AgentRole.WORKER,
                    edge=EdgeType.IMPLEMENTS,
                    depends_on=["nonexistent"],
                ),
            ],
        )
        eng = WorkflowEngine(repo, task_service)
        eng.register(bad_tmpl)
        with pytest.raises(KgnError, match="unknown dependency"):
            eng.execute(goal_node.id, project_id, agent_id, "bad-deps")


# ═══════════════════════════════════════════════════════════════════════
# 6. Title rendering
# ═══════════════════════════════════════════════════════════════════════


class TestTitleRendering:
    """WorkflowEngine._render_title() tests."""

    def test_parent_title_replaced(self):
        node = NodeRecord(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            type=NodeType.GOAL,
            title="My Goal",
        )
        result = WorkflowEngine._render_title("{parent.title} — Task", node)
        assert result == "My Goal — Task"

    def test_no_placeholder(self):
        node = NodeRecord(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            type=NodeType.GOAL,
            title="My Goal",
        )
        result = WorkflowEngine._render_title("Static Title", node)
        assert result == "Static Title"

    def test_multiple_placeholders(self):
        node = NodeRecord(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            type=NodeType.GOAL,
            title="X",
        )
        result = WorkflowEngine._render_title("{parent.title}+{parent.title}", node)
        assert result == "X+X"


# ═══════════════════════════════════════════════════════════════════════
# 7. MCP workflow tools
# ═══════════════════════════════════════════════════════════════════════


class TestMCPWorkflowTools:
    """MCP workflow_list and workflow_run tool tests."""

    def test_workflow_list_tool(
        self,
        db_conn,
        project_id: uuid.UUID,
        repo: KgnRepository,
    ):
        """workflow_list returns JSON with 3 built-in templates."""
        import asyncio

        from kgn.mcp.server import create_server

        project_name = f"wf-test-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn, embedding_client=None)

        # Call the tool
        async def _run():
            return await server.call_tool("workflow_list", {})

        raw = asyncio.run(_run())
        content_list = raw[0] if isinstance(raw, tuple) else raw
        text = content_list[0].text if hasattr(content_list[0], "text") else str(content_list)
        data = json.loads(text)
        assert len(data["templates"]) == 3
        names = {t["name"] for t in data["templates"]}
        assert "design-to-impl" in names
        assert "issue-resolution" in names
        assert "knowledge-indexing" in names

    def test_workflow_run_tool(
        self,
        db_conn,
        repo: KgnRepository,
    ):
        """workflow_run creates DAG and returns result JSON."""
        import asyncio

        from kgn.mcp.server import create_server

        project_name = f"wf-run-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        agent_id = repo.get_or_create_agent(pid, "test-agent")

        # Create a GOAL node
        goal_id = uuid.uuid4()
        goal = NodeRecord(
            id=goal_id,
            project_id=pid,
            type=NodeType.GOAL,
            status=NodeStatus.ACTIVE,
            title="MCP Goal",
            body_md="Test",
            created_by=agent_id,
        )
        repo.upsert_node(goal)

        server = create_server(project_name, conn=db_conn, embedding_client=None)

        async def _run():
            return await server.call_tool(
                "workflow_run",
                {
                    "project": project_name,
                    "agent": "test-agent",
                    "template_name": "design-to-impl",
                    "trigger_node_id": str(goal_id),
                },
            )

        raw = asyncio.run(_run())
        content_list = raw[0] if isinstance(raw, tuple) else raw
        text = content_list[0].text if hasattr(content_list[0], "text") else str(content_list)
        data = json.loads(text)
        assert data["status"] == "ok"
        assert data["nodes_created"] == 4
        assert data["tasks_enqueued"] == 2
        assert len(data["created_nodes"]) == 4

    def test_workflow_run_tool_bad_template(
        self,
        db_conn,
        repo: KgnRepository,
    ):
        """workflow_run with non-existent template returns error."""
        import asyncio

        from kgn.mcp.server import create_server

        project_name = f"wf-bad-{uuid.uuid4().hex[:8]}"
        pid = repo.get_or_create_project(project_name)
        agent_id = repo.get_or_create_agent(pid, "test-agent")

        goal_id = uuid.uuid4()
        goal = NodeRecord(
            id=goal_id,
            project_id=pid,
            type=NodeType.GOAL,
            status=NodeStatus.ACTIVE,
            title="Goal",
            body_md="",
            created_by=agent_id,
        )
        repo.upsert_node(goal)

        server = create_server(project_name, conn=db_conn, embedding_client=None)

        async def _run():
            return await server.call_tool(
                "workflow_run",
                {
                    "project": project_name,
                    "agent": "test-agent",
                    "template_name": "nonexistent",
                    "trigger_node_id": str(goal_id),
                },
            )

        raw = asyncio.run(_run())
        content_list = raw[0] if isinstance(raw, tuple) else raw
        text = content_list[0].text if hasattr(content_list[0], "text") else str(content_list)
        data = json.loads(text)
        assert "error" in data
        assert data["code"] == "KGN-999"


# ═══════════════════════════════════════════════════════════════════════
# 8. CLI workflow commands
# ═══════════════════════════════════════════════════════════════════════


class TestCLIWorkflow:
    """CLI workflow list and run commands."""

    def test_workflow_list_cli(self):
        """kgn workflow list should not error."""
        from typer.testing import CliRunner

        from kgn.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["workflow", "list"])
        assert result.exit_code == 0
        assert "design-to-impl" in result.output
        assert "issue-resolution" in result.output
        assert "knowledge-indexing" in result.output
