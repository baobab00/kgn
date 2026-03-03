"""MCP workflow tool — workflow_run, workflow_list."""

from __future__ import annotations

import json
import time

import structlog
from mcp.server.fastmcp import FastMCP

from kgn.db.repository import KgnRepository
from kgn.errors import KgnErrorCode
from kgn.graph.subgraph import SubgraphService
from kgn.mcp._helpers import _error_json, _parse_uuid, safe_tool_call
from kgn.orchestration.templates import BUILTIN_TEMPLATES, register_builtins
from kgn.orchestration.workflow import WorkflowEngine
from kgn.task.service import TaskService

log = structlog.get_logger("kgn.mcp.workflow")


def register_workflow_tools(server: FastMCP) -> None:
    """Register workflow MCP tools on the server."""

    @server.tool(
        name="workflow_list",
        description="Return registered workflow templates as JSON.",
    )
    @safe_tool_call
    def workflow_list() -> str:
        log.info("tool_called", tool="workflow_list")
        templates = []
        for tmpl in BUILTIN_TEMPLATES:
            templates.append(
                {
                    "name": tmpl.name,
                    "display_name": tmpl.display_name,
                    "trigger_node_type": str(tmpl.trigger_node_type),
                    "trigger_status": str(tmpl.trigger_status),
                    "steps": [
                        {
                            "id": s.id,
                            "node_type": str(s.node_type),
                            "assign_role": str(s.assign_role),
                            "depends_on": s.depends_on,
                        }
                        for s in tmpl.steps
                    ],
                }
            )
        return json.dumps({"templates": templates}, ensure_ascii=False, indent=2)

    @server.tool(
        name="workflow_run",
        description="Execute a workflow template to create a subtask DAG from the trigger node.",
    )
    @safe_tool_call
    def workflow_run(
        project: str,
        agent: str,
        template_name: str,
        trigger_node_id: str,
        priority: int = 100,
    ) -> str:
        t0 = time.monotonic()
        log.info(
            "tool_called",
            tool="workflow_run",
            project=project,
            template=template_name,
            trigger=trigger_node_id,
        )

        trigger_uuid = _parse_uuid(trigger_node_id)
        if trigger_uuid is None:
            return _error_json(
                f"Invalid UUID: {trigger_node_id}",
                KgnErrorCode.VALIDATION_ERROR,
            )

        with server._kgn_conn_factory() as c:  # type: ignore[attr-defined]
            repo = KgnRepository(c)
            pid = repo.get_project_by_name(project)
            if pid is None:
                return _error_json(
                    f"Project not found: {project}",
                    KgnErrorCode.PROJECT_NOT_FOUND,
                )

            agent_id = repo.get_or_create_agent(pid, agent)

            subgraph_svc = SubgraphService(repo)
            embed_client = server._kgn_embed_client  # type: ignore[attr-defined]
            task_svc = TaskService(repo, subgraph_svc, embed_client)

            engine = WorkflowEngine(repo, task_svc)
            register_builtins(engine)

            result = engine.execute(
                trigger_node_id=trigger_uuid,
                project_id=pid,
                agent_id=agent_id,
                template_name=template_name,
                priority=priority,
            )

            elapsed = time.monotonic() - t0
            response = {
                "status": "ok",
                "template": result.template_name,
                "trigger_node_id": str(result.trigger_node_id),
                "nodes_created": result.node_count,
                "tasks_enqueued": result.task_count,
                "created_nodes": [
                    {
                        "step_id": cn.step_id,
                        "node_id": str(cn.node_id),
                        "node_type": str(cn.node_type),
                        "title": cn.title,
                        "queue_state": cn.enqueue_result.state if cn.enqueue_result else None,
                    }
                    for cn in result.created_nodes
                ],
                "elapsed_sec": round(elapsed, 3),
            }
            log.info(
                "tool_completed",
                tool="workflow_run",
                nodes_created=result.node_count,
                tasks_enqueued=result.task_count,
                elapsed=round(elapsed, 3),
            )
            return json.dumps(response, ensure_ascii=False, indent=2)
