"""MCP write tools — ingest_node, ingest_edge, enqueue_task."""

from __future__ import annotations

import json
import time

import structlog
from mcp.server.fastmcp import FastMCP
from psycopg import OperationalError

from kgn.db.repository import KgnRepository
from kgn.embedding.service import EmbeddingService
from kgn.errors import KgnError, KgnErrorCode
from kgn.graph.subgraph import SubgraphService
from kgn.ingest.service import IngestService
from kgn.mcp._helpers import _error_json, _parse_uuid, safe_tool_call
from kgn.models.enums import AgentRole, EdgeType, NodeType
from kgn.orchestration.locking import NodeLockService
from kgn.orchestration.roles import RoleGuard
from kgn.parser.kge_parser import parse_kge_text
from kgn.parser.kgn_parser import parse_kgn_text
from kgn.task.service import TaskService

log = structlog.get_logger("kgn.mcp.write")


def register_write_tools(server: FastMCP) -> None:
    """Register write MCP tools on the server."""

    @server.tool(
        name="ingest_node",
        description="Parse a .kgn string and ingest the node.",
    )
    @safe_tool_call
    def ingest_node(kgn_content: str) -> str:
        t0 = time.monotonic()
        log.info("tool_called", tool="ingest_node")

        with server._kgn_conn_factory() as c:  # type: ignore[attr-defined]
            repo = KgnRepository(c)
            agent_id = repo.get_or_create_agent(
                server._kgn_project_id,
                "mcp",  # type: ignore[attr-defined]
            )

            # ── Role guard: pre-parse to check node type permission ───
            agent_role_str = repo.get_agent_role(agent_id) or "admin"
            try:
                agent_role = AgentRole(agent_role_str)
            except ValueError:
                agent_role = AgentRole.ADMIN

            if agent_role != AgentRole.ADMIN:
                try:
                    parsed = parse_kgn_text(kgn_content)
                    node_type = NodeType(parsed.front_matter.type)
                    RoleGuard.check_node_create(agent_role, node_type)
                except KgnError:
                    raise  # permission denied — propagate
                except Exception:  # noqa: BLE001
                    pass  # parse error handled by IngestService below

            # ── Lock guard: check node lock before write ──────────────
            # Pre-parse to get node ID for existing nodes
            lock_svc = NodeLockService(repo)
            try:
                parsed_for_lock = parse_kgn_text(kgn_content)
                node_id_str = parsed_for_lock.front_matter.id
                if not node_id_str.startswith("new:"):
                    from kgn.mcp._helpers import _parse_uuid as _pu

                    existing_node_id = _pu(node_id_str)
                    if existing_node_id is not None:
                        lock_svc.check_write_permission(existing_node_id, agent_id)
            except KgnError:
                raise  # lock denied — propagate
            except Exception:  # noqa: BLE001
                pass  # parse error or new node — no lock to check

            svc = IngestService(
                repo,
                server._kgn_project_id,  # type: ignore[attr-defined]
                agent_id,
                enforce_project=True,
            )
            try:
                result = svc.ingest_text(kgn_content, ".kgn")
            except OperationalError:
                raise  # Let safe_tool_call handle DB errors (R-007)
            except KgnError:
                raise  # Let safe_tool_call handle with correct code (R-010)
            except Exception as exc:  # noqa: BLE001
                log.error("tool_error", tool="ingest_node", error=str(exc))
                return _error_json(
                    "Ingest failed",
                    KgnErrorCode.INVALID_KGN_FORMAT,
                    detail=str(exc),
                )

            if result.failed > 0:
                detail = result.details[0]
                log.warning("tool_error", tool="ingest_node", error=detail.error or "")
                return _error_json(
                    "Ingest failed",
                    KgnErrorCode.VALIDATION_FAILED,
                    detail=detail.error or "",
                )

            node_id = result.details[0].node_id if result.details else None

            # ── Auto-embed (R12: graceful degradation) ────────────────
            embed_status = "skipped"
            embed_client = getattr(server, "_kgn_embed_client", None)
            if embed_client is not None and node_id is not None:
                try:
                    embed_svc = EmbeddingService(repo=repo, client=embed_client)
                    embed_svc.embed_node(node_id, server._kgn_project_id)  # type: ignore[attr-defined]
                    embed_status = "success"
                except Exception as exc:  # noqa: BLE001
                    log.warning("auto_embed_failed", node_id=str(node_id), error=str(exc))
                    embed_status = "failed"

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        log.info(
            "tool_completed",
            tool="ingest_node",
            node_id=str(node_id),
            embedding=embed_status,
            duration_ms=elapsed_ms,
        )
        return json.dumps(
            {"status": "ok", "node_id": str(node_id), "embedding": embed_status},
            ensure_ascii=False,
        )

    @server.tool(
        name="ingest_edge",
        description="Parse a .kge string and ingest edges.",
    )
    @safe_tool_call
    def ingest_edge(kge_content: str) -> str:
        t0 = time.monotonic()
        log.info("tool_called", tool="ingest_edge")

        with server._kgn_conn_factory() as c:  # type: ignore[attr-defined]
            repo = KgnRepository(c)
            agent_id = repo.get_or_create_agent(
                server._kgn_project_id,
                "mcp",  # type: ignore[attr-defined]
            )

            # ── Role guard: pre-parse to check edge type permission ───
            agent_role_str = repo.get_agent_role(agent_id) or "admin"
            try:
                agent_role = AgentRole(agent_role_str)
            except ValueError:
                agent_role = AgentRole.ADMIN

            if agent_role != AgentRole.ADMIN:
                try:
                    parsed_edges = parse_kge_text(kge_content)
                    for edge_def in parsed_edges.edges:
                        edge_type = EdgeType(edge_def.type)
                        RoleGuard.check_edge_create(agent_role, edge_type)
                except KgnError:
                    raise  # permission denied — propagate
                except Exception:  # noqa: BLE001
                    pass  # parse error handled by IngestService below

            svc = IngestService(
                repo,
                server._kgn_project_id,  # type: ignore[attr-defined]
                agent_id,
                enforce_project=True,
            )
            try:
                result = svc.ingest_text(kge_content, ".kge")
            except OperationalError:
                raise  # Let safe_tool_call handle DB errors (R-007)
            except KgnError:
                raise  # Let safe_tool_call handle with correct code (R-010)
            except Exception as exc:  # noqa: BLE001
                log.error("tool_error", tool="ingest_edge", error=str(exc))
                return _error_json(
                    "Ingest failed",
                    KgnErrorCode.INVALID_KGE_FORMAT,
                    detail=str(exc),
                )

        if result.failed > 0:
            detail = result.details[0]
            log.warning("tool_error", tool="ingest_edge", error=detail.error or "")
            return _error_json(
                detail.error or "Ingest failed",
                KgnErrorCode.VALIDATION_FAILED,
            )

        # Count edges from parsed content for response
        try:
            edge_fm = parse_kge_text(kge_content)
            edge_count = len(edge_fm.edges)
        except Exception:  # noqa: BLE001
            edge_count = 0

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        log.info(
            "tool_completed",
            tool="ingest_edge",
            edge_count=edge_count,
            duration_ms=elapsed_ms,
        )
        return json.dumps(
            {"status": "ok", "edge_count": edge_count},
            ensure_ascii=False,
        )

    @server.tool(
        name="enqueue_task",
        description="Enqueue a TASK node into the task queue.",
    )
    @safe_tool_call
    def enqueue_task(task_node_id: str, priority: int = 100) -> str:
        t0 = time.monotonic()
        log.info("tool_called", tool="enqueue_task", task_node_id=task_node_id)

        tid = _parse_uuid(task_node_id)
        if tid is None:
            log.warning("tool_error", tool="enqueue_task", error=f"Invalid UUID: {task_node_id}")
            return _error_json(f"Invalid UUID: {task_node_id}", KgnErrorCode.INVALID_UUID)

        with server._kgn_conn_factory() as c:  # type: ignore[attr-defined]
            repo = KgnRepository(c)
            try:
                svc = TaskService(repo, SubgraphService(repo))
                result = svc.enqueue(
                    server._kgn_project_id,  # type: ignore[attr-defined]
                    tid,
                    priority=priority,
                )
            except OperationalError:
                raise  # Let safe_tool_call handle DB errors (R-007)
            except KgnError:
                raise  # Let safe_tool_call handle with correct code (R-010)
            except Exception as exc:  # noqa: BLE001
                log.error("tool_error", tool="enqueue_task", error=str(exc))
                return _error_json(str(exc), KgnErrorCode.TASK_NODE_INVALID)

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        log.info(
            "tool_completed",
            tool="enqueue_task",
            task_queue_id=str(result.task_queue_id),
            state=result.state,
            duration_ms=elapsed_ms,
        )

        response: dict = {
            "status": result.state,
            "task_queue_id": str(result.task_queue_id),
        }
        if result.state == "BLOCKED":
            response["blocking_tasks"] = [
                {
                    "node_id": str(bt.node_id),
                    "title": bt.title,
                    "state": bt.state,
                }
                for bt in result.dependency_check.blocking_tasks
            ]
            n = len(result.dependency_check.blocking_tasks)
            response["message"] = (
                f"{n} prerequisite DEPENDS_ON task(s) incomplete. Enqueued as BLOCKED."
            )
        return json.dumps(response, ensure_ascii=False)
