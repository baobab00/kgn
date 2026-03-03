"""WorkflowEngine — declarative TASK decomposition from workflow templates.

Given a trigger node (e.g. a GOAL) and a workflow template, the engine
creates a DAG of subtask/sub-node records, connects them with appropriate
edges, and enqueues TASK-type nodes into the task queue.

Rule compliance:
- R1  — no SQL outside repository
- R5  — activity logging via repo.log_activity
- R10 — task_queue transitions only via TaskService / Repository
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from kgn.db.repository import KgnRepository
from kgn.errors import KgnError, KgnErrorCode
from kgn.models.edge import EdgeRecord
from kgn.models.enums import AgentRole, EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.task.service import EnqueueResult, TaskService

log = structlog.get_logger()

# ── Data classes ───────────────────────────────────────────────────────


@dataclass
class WorkflowStep:
    """A single step within a workflow template."""

    id: str
    action: str  # "create_subtask"
    node_type: NodeType
    title_template: str
    assign_role: AgentRole
    edge: EdgeType
    depends_on: list[str] = field(default_factory=list)


@dataclass
class WorkflowTemplate:
    """Declarative workflow definition.

    Attributes:
        name: Machine-readable identifier (e.g. ``"design-to-impl"``).
        display_name: Human-readable label.
        trigger_node_type: Required node type for the trigger node.
        trigger_status: Required status for the trigger node (default ACTIVE).
        steps: Ordered list of :class:`WorkflowStep` definitions.
    """

    name: str
    display_name: str
    trigger_node_type: NodeType
    trigger_status: NodeStatus = NodeStatus.ACTIVE
    steps: list[WorkflowStep] = field(default_factory=list)


@dataclass
class CreatedNode:
    """Record of a node created by the engine during execution."""

    step_id: str
    node_id: uuid.UUID
    node_type: NodeType
    title: str
    enqueue_result: EnqueueResult | None = None  # only for TASK nodes


@dataclass
class WorkflowExecutionResult:
    """Aggregated result of :meth:`WorkflowEngine.execute`."""

    trigger_node_id: uuid.UUID
    template_name: str
    created_nodes: list[CreatedNode] = field(default_factory=list)

    @property
    def task_count(self) -> int:
        return sum(1 for n in self.created_nodes if n.enqueue_result is not None)

    @property
    def node_count(self) -> int:
        return len(self.created_nodes)


# ── Engine ─────────────────────────────────────────────────────────────


class WorkflowEngine:
    """Declarative workflow execution engine.

    Typical lifecycle::

        engine = WorkflowEngine(repo, task_service)
        engine.register(design_to_impl_template)
        result = engine.execute(trigger_node_id, project_id, agent_id, "design-to-impl")
    """

    def __init__(
        self,
        repo: KgnRepository,
        task_service: TaskService,
    ) -> None:
        self._repo = repo
        self._task_service = task_service
        self._templates: dict[str, WorkflowTemplate] = {}

    # ── Template management ────────────────────────────────────────

    def register(self, template: WorkflowTemplate) -> None:
        """Register a workflow template by name.

        Overwrites any existing template with the same name.
        """
        self._templates[template.name] = template
        log.info("workflow_template_registered", name=template.name)

    def list_templates(self) -> list[WorkflowTemplate]:
        """Return all registered templates, sorted by name."""
        return sorted(self._templates.values(), key=lambda t: t.name)

    def get_template(self, name: str) -> WorkflowTemplate | None:
        """Retrieve a template by name, or ``None`` if not found."""
        return self._templates.get(name)

    # ── Execution ──────────────────────────────────────────────────

    def execute(
        self,
        trigger_node_id: uuid.UUID,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        template_name: str,
        *,
        priority: int = 100,
    ) -> WorkflowExecutionResult:
        """Execute a workflow template against a trigger node.

        Steps:
        1. Validate trigger node (exists, correct type & status).
        2. For each template step, create a ``NodeRecord`` with
           rendered title.
        3. Create edges: step → trigger node (edge type from template),
           plus DEPENDS_ON edges between inter-step dependencies.
        4. Enqueue TASK-type nodes into the task queue.

        Args:
            trigger_node_id: The node that triggers the workflow.
            project_id: Project scope.
            agent_id: Agent performing the execution.
            template_name: Registered template name.
            priority: Base priority for enqueued tasks (default 100).

        Returns:
            WorkflowExecutionResult with all created nodes.

        Raises:
            KgnError: If template not found, trigger node invalid, or
                step references unknown dependency.
        """
        # 1. Resolve template
        template = self._templates.get(template_name)
        if template is None:
            raise KgnError(
                KgnErrorCode.INTERNAL_ERROR,
                f"Workflow template '{template_name}' not found. "
                f"Available: {sorted(self._templates.keys())}",
            )

        # 2. Validate trigger node
        trigger_node = self._repo.get_node_by_id(trigger_node_id)
        if trigger_node is None:
            raise KgnError(
                KgnErrorCode.NODE_NOT_FOUND,
                f"Trigger node {trigger_node_id} not found",
            )
        if trigger_node.project_id != project_id:
            raise KgnError(
                KgnErrorCode.NODE_NOT_FOUND,
                f"Trigger node {trigger_node_id} does not belong to project {project_id}",
            )
        if trigger_node.type != template.trigger_node_type:
            raise KgnError(
                KgnErrorCode.INVALID_NODE_TYPE,
                f"Trigger node type is {trigger_node.type}, expected {template.trigger_node_type}",
            )
        if trigger_node.status != template.trigger_status:
            raise KgnError(
                KgnErrorCode.INVALID_NODE_STATUS,
                f"Trigger node status is {trigger_node.status}, expected {template.trigger_status}",
            )

        # 3. Validate step dependency references
        step_ids = {s.id for s in template.steps}
        for step in template.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    raise KgnError(
                        KgnErrorCode.INTERNAL_ERROR,
                        f"Step '{step.id}' references unknown dependency '{dep}'",
                    )

        # 4. Execute steps
        result = WorkflowExecutionResult(
            trigger_node_id=trigger_node_id,
            template_name=template_name,
        )
        # Maps step_id → created node UUID
        step_node_map: dict[str, uuid.UUID] = {}

        for step in template.steps:
            created = self._execute_step(
                step=step,
                trigger_node=trigger_node,
                project_id=project_id,
                agent_id=agent_id,
                step_node_map=step_node_map,
                priority=priority,
            )
            step_node_map[step.id] = created.node_id
            result.created_nodes.append(created)

        log.info(
            "workflow_executed",
            template=template_name,
            trigger_node_id=str(trigger_node_id),
            nodes_created=result.node_count,
            tasks_enqueued=result.task_count,
        )
        return result

    # ── Internal helpers ───────────────────────────────────────────

    def _execute_step(
        self,
        *,
        step: WorkflowStep,
        trigger_node: NodeRecord,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        step_node_map: dict[str, uuid.UUID],
        priority: int,
    ) -> CreatedNode:
        """Create a single node + edges for one workflow step."""
        # 1. Render title
        title = self._render_title(step.title_template, trigger_node)

        # 2. Create node
        node_id = uuid.uuid4()
        now = datetime.now(UTC)
        body_parts = [
            f"Workflow: {step.id}",
            f"Trigger: {trigger_node.id}",
            f"Role: {step.assign_role}",
        ]
        if step.depends_on:
            body_parts.append(f"Depends on: {', '.join(step.depends_on)}")

        node = NodeRecord(
            id=node_id,
            project_id=project_id,
            type=step.node_type,
            status=NodeStatus.ACTIVE,
            title=title,
            body_md="\n".join(body_parts),
            tags=["workflow", f"role:{step.assign_role}"],
            created_by=agent_id,
            created_at=now,
        )
        self._repo.upsert_node(node)

        # 3. Create edge: new_node → trigger_node
        edge_to_trigger = EdgeRecord(
            project_id=project_id,
            from_node_id=node_id,
            to_node_id=trigger_node.id,
            type=step.edge,
            note=f"Workflow step '{step.id}'",
            created_by=agent_id,
        )
        self._repo.insert_edge(edge_to_trigger)

        # 4. Create DEPENDS_ON edges for inter-step dependencies
        for dep_id in step.depends_on:
            dep_node_id = step_node_map[dep_id]
            dep_edge = EdgeRecord(
                project_id=project_id,
                from_node_id=node_id,
                to_node_id=dep_node_id,
                type=EdgeType.DEPENDS_ON,
                note=f"Workflow dependency: {step.id} → {dep_id}",
                created_by=agent_id,
            )
            self._repo.insert_edge(dep_edge)

        # 5. Enqueue TASK-type nodes
        enqueue_result: EnqueueResult | None = None
        if step.node_type == NodeType.TASK:
            enqueue_result = self._task_service.enqueue(
                project_id,
                node_id,
                priority=priority,
            )
            log.info(
                "workflow_task_enqueued",
                step_id=step.id,
                node_id=str(node_id),
                state=enqueue_result.state,
            )

        return CreatedNode(
            step_id=step.id,
            node_id=node_id,
            node_type=step.node_type,
            title=title,
            enqueue_result=enqueue_result,
        )

    @staticmethod
    def _render_title(template: str, trigger_node: NodeRecord) -> str:
        """Render a title template with ``{parent.title}`` placeholders."""
        return template.replace("{parent.title}", trigger_node.title)


# ── Factory ────────────────────────────────────────────────────────────


def create_workflow_template(data: dict) -> WorkflowTemplate:
    """Build a :class:`WorkflowTemplate` from a dict (YAML-like structure).

    Expected keys::

        name: str
        display_name: str  (optional, defaults to name)
        trigger:
          node_type: str  (NodeType value)
          status: str     (NodeStatus value, default "ACTIVE")
        steps:
          - id: str
            action: str
            node_type: str
            title_template: str
            assign_role: str
            edge: str
            depends_on: [str]  (optional)

    Raises:
        KgnError: On validation failure.
    """
    try:
        name = data["name"]
        display_name = data.get("display_name", name)
        trigger = data["trigger"]
        trigger_node_type = NodeType(trigger["node_type"])
        trigger_status = NodeStatus(trigger.get("status", "ACTIVE"))

        steps: list[WorkflowStep] = []
        for s in data.get("steps", []):
            steps.append(
                WorkflowStep(
                    id=s["id"],
                    action=s.get("action", "create_subtask"),
                    node_type=NodeType(s["node_type"]),
                    title_template=s["title_template"],
                    assign_role=AgentRole(s["assign_role"]),
                    edge=EdgeType(s["edge"]),
                    depends_on=s.get("depends_on", []),
                )
            )

        return WorkflowTemplate(
            name=name,
            display_name=display_name,
            trigger_node_type=trigger_node_type,
            trigger_status=trigger_status,
            steps=steps,
        )
    except KeyError as exc:
        raise KgnError(
            KgnErrorCode.INTERNAL_ERROR,
            f"Invalid workflow template: missing key {exc}",
        ) from exc
    except ValueError as exc:
        raise KgnError(
            KgnErrorCode.INTERNAL_ERROR,
            f"Invalid workflow template: {exc}",
        ) from exc
