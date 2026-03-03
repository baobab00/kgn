"""Tests for KGN error code system (Phase 5 Step 6).

Validates:
- KgnErrorCode enum values and ranges
- KgnError exception carries code, message, detail, recoverable
- Recoverable classification
- Error code coverage in MCP tool responses
"""

from __future__ import annotations

import json
import uuid

import pytest

from kgn.errors import _RECOVERABLE_CODES, KgnError, KgnErrorCode

# ── KgnErrorCode enum ─────────────────────────────────────────────────


class TestKgnErrorCode:
    """Validate error code enum structure."""

    def test_infrastructure_codes_1xx(self) -> None:
        assert KgnErrorCode.DB_CONNECTION_FAILED == "KGN-100"
        assert KgnErrorCode.DB_TIMEOUT == "KGN-101"
        assert KgnErrorCode.POOL_EXHAUSTED == "KGN-102"
        assert KgnErrorCode.EMBEDDING_API_FAILED == "KGN-110"
        assert KgnErrorCode.EMBEDDING_API_TIMEOUT == "KGN-111"

    def test_ingest_codes_2xx(self) -> None:
        assert KgnErrorCode.PARSE_FAILED == "KGN-200"
        assert KgnErrorCode.VALIDATION_FAILED == "KGN-201"
        assert KgnErrorCode.INVALID_KGN_FORMAT == "KGN-203"
        assert KgnErrorCode.INVALID_KGE_FORMAT == "KGN-204"
        assert KgnErrorCode.INVALID_UUID == "KGN-210"

    def test_query_codes_3xx(self) -> None:
        assert KgnErrorCode.NODE_NOT_FOUND == "KGN-300"
        assert KgnErrorCode.PROJECT_NOT_FOUND == "KGN-301"
        assert KgnErrorCode.INVALID_NODE_TYPE == "KGN-310"
        assert KgnErrorCode.INVALID_NODE_STATUS == "KGN-311"

    def test_task_codes_4xx(self) -> None:
        assert KgnErrorCode.TASK_QUEUE_EMPTY == "KGN-400"
        assert KgnErrorCode.TASK_NOT_IN_PROGRESS == "KGN-401"
        assert KgnErrorCode.TASK_NODE_INVALID == "KGN-402"

    def test_general_code_9xx(self) -> None:
        assert KgnErrorCode.INTERNAL_ERROR == "KGN-999"

    def test_all_codes_unique(self) -> None:
        values = [c.value for c in KgnErrorCode]
        assert len(values) == len(set(values))

    def test_code_from_value(self) -> None:
        assert KgnErrorCode("KGN-300") == KgnErrorCode.NODE_NOT_FOUND


# ── KgnError exception ────────────────────────────────────────────────


class TestKgnError:
    """Validate KgnError exception class."""

    def test_carries_code_and_message(self) -> None:
        err = KgnError(KgnErrorCode.NODE_NOT_FOUND, "Node xyz not found")
        assert err.code == KgnErrorCode.NODE_NOT_FOUND
        assert str(err) == "Node xyz not found"
        assert err.detail == "Node xyz not found"

    def test_custom_detail(self) -> None:
        err = KgnError(
            KgnErrorCode.NODE_NOT_FOUND,
            "Node not found",
            detail="Node abc-123 does not exist in project my-project",
        )
        assert err.detail == "Node abc-123 does not exist in project my-project"

    def test_recoverable_true_for_infra(self) -> None:
        err = KgnError(KgnErrorCode.DB_CONNECTION_FAILED, "down")
        assert err.recoverable is True

    def test_recoverable_false_for_logic(self) -> None:
        err = KgnError(KgnErrorCode.NODE_NOT_FOUND, "not found")
        assert err.recoverable is False

    def test_is_exception(self) -> None:
        with pytest.raises(KgnError) as exc_info:
            raise KgnError(KgnErrorCode.PARSE_FAILED, "parse error")
        assert exc_info.value.code == KgnErrorCode.PARSE_FAILED


# ── Recoverable classification ─────────────────────────────────────────


class TestRecoverableClassification:
    """Validate which codes are classified as recoverable."""

    def test_infra_codes_are_recoverable(self) -> None:
        assert KgnErrorCode.DB_CONNECTION_FAILED in _RECOVERABLE_CODES
        assert KgnErrorCode.DB_TIMEOUT in _RECOVERABLE_CODES
        assert KgnErrorCode.POOL_EXHAUSTED in _RECOVERABLE_CODES
        assert KgnErrorCode.EMBEDDING_API_TIMEOUT in _RECOVERABLE_CODES

    def test_logic_codes_are_not_recoverable(self) -> None:
        assert KgnErrorCode.NODE_NOT_FOUND not in _RECOVERABLE_CODES
        assert KgnErrorCode.PARSE_FAILED not in _RECOVERABLE_CODES
        assert KgnErrorCode.INTERNAL_ERROR not in _RECOVERABLE_CODES

    def test_task_queue_empty_is_recoverable(self) -> None:
        assert KgnErrorCode.TASK_QUEUE_EMPTY in _RECOVERABLE_CODES


# ── MCP tool error code integration ───────────────────────────────────


def _call_tool(server, tool_name: str, **kwargs) -> str:
    import asyncio

    async def _run():
        return await server.call_tool(tool_name, kwargs)

    raw = asyncio.run(_run())
    content_list = raw[0] if isinstance(raw, tuple) else raw
    if content_list and hasattr(content_list[0], "text"):
        return content_list[0].text
    return str(content_list)


class TestMCPToolErrorCodes:
    """Verify MCP tools return proper error codes in responses."""

    def test_get_node_invalid_uuid_code(self, db_conn, repo, project_id) -> None:
        from kgn.mcp.server import create_server

        project_name = f"errcode-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        result = json.loads(_call_tool(server, "get_node", node_id="not-a-uuid"))
        assert result["code"] == "KGN-210"
        assert result["recoverable"] is False

    def test_get_node_not_found_code(self, db_conn, repo, project_id) -> None:
        from kgn.mcp.server import create_server

        project_name = f"errcode-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        result = json.loads(_call_tool(server, "get_node", node_id=str(uuid.uuid4())))
        assert result["code"] == "KGN-300"
        assert result["recoverable"] is False

    def test_query_nodes_project_not_found_code(self, db_conn, repo, project_id) -> None:
        from kgn.mcp.server import create_server

        project_name = f"errcode-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        result = json.loads(_call_tool(server, "query_nodes", project="non-existent-project"))
        assert result["code"] == "KGN-301"

    def test_query_nodes_invalid_type_code(self, db_conn, repo, project_id) -> None:
        from kgn.mcp.server import create_server

        project_name = f"errcode-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        result = json.loads(_call_tool(server, "query_nodes", project=project_name, type="INVALID"))
        assert result["code"] == "KGN-310"

    def test_ingest_node_validation_error_code(self, db_conn, repo, project_id) -> None:
        from kgn.mcp.server import create_server

        project_name = f"errcode-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        # Invalid KGN content (missing required fields)
        bad_kgn = "---\ntitle: bad\n---\n# Summary\ntest"
        result = json.loads(_call_tool(server, "ingest_node", kgn_content=bad_kgn))
        assert result["code"] in ("KGN-201", "KGN-203")

    def test_task_checkout_project_not_found_code(self, db_conn, repo, project_id) -> None:
        from kgn.mcp.server import create_server

        project_name = f"errcode-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        result = json.loads(
            _call_tool(server, "task_checkout", project="ghost-project", agent="claude")
        )
        assert result["code"] == "KGN-301"

    def test_enqueue_task_invalid_uuid_code(self, db_conn, repo, project_id) -> None:
        from kgn.mcp.server import create_server

        project_name = f"errcode-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        result = json.loads(_call_tool(server, "enqueue_task", task_node_id="bad-uuid"))
        assert result["code"] == "KGN-210"

    def test_error_response_has_all_fields(self, db_conn, repo, project_id) -> None:
        """Every error response must have error, code, detail, recoverable."""
        from kgn.mcp.server import create_server

        project_name = f"errcode-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        server = create_server(project_name, conn=db_conn)

        result = json.loads(_call_tool(server, "get_node", node_id="invalid"))
        assert {"error", "code", "detail", "recoverable"} <= set(result.keys())
