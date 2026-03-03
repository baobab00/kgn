"""MCP task tools — task_checkout, task_complete, task_fail."""

from __future__ import annotations

import json
import time

import structlog
from mcp.server.fastmcp import FastMCP
from psycopg import OperationalError

from kgn.db.repository import KgnRepository
from kgn.errors import KgnError, KgnErrorCode
from kgn.graph.subgraph import SubgraphService
from kgn.mcp._helpers import _error_json, _parse_uuid, safe_tool_call
from kgn.models.enums import AgentRole
from kgn.orchestration.roles import RoleGuard
from kgn.task.formatter import HandoffFormatter
from kgn.task.service import TaskService

log = structlog.get_logger("kgn.mcp.task")


def register_task_tools(server: FastMCP) -> None:
    """Register task lifecycle MCP tools on the server."""

    @server.tool(
        name="task_checkout",
        description="Checkout the highest-priority task and return context package as JSON.",
    )
    @safe_tool_call
    def task_checkout(project: str, agent: str) -> str:
        t0 = time.monotonic()
        log.info("tool_called", tool="task_checkout", project=project, agent=agent)

        with server._kgn_conn_factory() as c:  # type: ignore[attr-defined]
            repo = KgnRepository(c)
            pid = repo.get_project_by_name(project)
            if pid is None:
                log.warning(
                    "tool_error",
                    tool="task_checkout",
                    error=f"Project not found: {project}",
                )
                return _error_json(f"Project not found: {project}", KgnErrorCode.PROJECT_NOT_FOUND)

            agent_id = repo.get_or_create_agent(pid, agent)

            # ── Role guard: check task checkout permission ────────────
            agent_role_str = repo.get_agent_role(agent_id) or "admin"
            try:
                agent_role = AgentRole(agent_role_str)
            except ValueError:
                agent_role = AgentRole.ADMIN
            RoleGuard.check_task_checkout(agent_role)

            svc = TaskService(repo, SubgraphService(repo))

            # Step 6: requeue lease-expired tasks before checkout
            requeued = svc.requeue_expired(pid)
            if requeued > 0:
                log.info("tasks_requeued", count=requeued, project=project)

            # Use agent role for role-filtered checkout
            # Admin agents can checkout any task (no filter)
            checkout_role_filter: str | None = None
            if agent_role != AgentRole.ADMIN:
                checkout_role_filter = agent_role_str

            pkg = svc.checkout(pid, agent_id, role_filter=checkout_role_filter)

        elapsed_ms = round((time.monotonic() - t0) * 1000)

        if pkg is None:
            log.info("tool_completed", tool="task_checkout", result="empty", duration_ms=elapsed_ms)
            return json.dumps(
                {"status": "empty", "message": "No tasks available"},
                ensure_ascii=False,
            )

        log.info("tool_completed", tool="task_checkout", result="assigned", duration_ms=elapsed_ms)
        return HandoffFormatter.to_json(pkg)

    @server.tool(
        name="task_complete",
        description="Mark a checked-out task as complete.",
    )
    @safe_tool_call
    def task_complete(task_id: str) -> str:
        t0 = time.monotonic()
        log.info("tool_called", tool="task_complete", task_id=task_id)

        tid = _parse_uuid(task_id)
        if tid is None:
            log.warning("tool_error", tool="task_complete", error=f"Invalid UUID: {task_id}")
            return _error_json(f"Invalid UUID: {task_id}", KgnErrorCode.INVALID_UUID)

        with server._kgn_conn_factory() as c:  # type: ignore[attr-defined]
            repo = KgnRepository(c)
            try:
                svc = TaskService(repo, SubgraphService(repo))
                result = svc.complete(tid)
            except OperationalError:
                raise  # Let safe_tool_call handle DB errors (R-007)
            except KgnError:
                raise  # Let safe_tool_call handle with correct code (R-010)
            except Exception as exc:  # noqa: BLE001
                log.error("tool_error", tool="task_complete", error=str(exc))
                return _error_json(str(exc), KgnErrorCode.TASK_NOT_IN_PROGRESS)

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        log.info("tool_completed", tool="task_complete", task_id=task_id, duration_ms=elapsed_ms)

        response: dict = {"status": "ok", "message": f"Task {task_id} completed"}
        if result.unblocked_tasks:
            response["unblocked_tasks"] = [
                {
                    "task_queue_id": str(ut.task_queue_id),
                    "node_title": ut.node_title,
                }
                for ut in result.unblocked_tasks
            ]
        return json.dumps(response, ensure_ascii=False)

    @server.tool(
        name="task_fail",
        description="Mark a checked-out task as failed.",
    )
    @safe_tool_call
    def task_fail(task_id: str, reason: str = "") -> str:
        t0 = time.monotonic()
        log.info("tool_called", tool="task_fail", task_id=task_id)

        tid = _parse_uuid(task_id)
        if tid is None:
            log.warning("tool_error", tool="task_fail", error=f"Invalid UUID: {task_id}")
            return _error_json(f"Invalid UUID: {task_id}", KgnErrorCode.INVALID_UUID)

        with server._kgn_conn_factory() as c:  # type: ignore[attr-defined]
            repo = KgnRepository(c)
            try:
                svc = TaskService(repo, SubgraphService(repo))
                svc.fail(tid, reason=reason)
            except OperationalError:
                raise  # Let safe_tool_call handle DB errors (R-007)
            except KgnError:
                raise  # Let safe_tool_call handle with correct code (R-010)
            except Exception as exc:  # noqa: BLE001
                log.error("tool_error", tool="task_fail", error=str(exc))
                return _error_json(str(exc), KgnErrorCode.TASK_NOT_IN_PROGRESS)

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        log.info(
            "tool_completed",
            tool="task_fail",
            task_id=task_id,
            reason=reason,
            duration_ms=elapsed_ms,
        )
        return json.dumps(
            {"status": "ok", "message": f"Task {task_id} failed", "reason": reason},
            ensure_ascii=False,
        )
