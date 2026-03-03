"""Tests for Step 5 — Error handling refinement (R-009, R-010, R-025).

R-009: _error_json code is now a required positional argument.
R-010: KgnError re-raised through safe_tool_call for correct error codes.
R-025: PR body truncated at 65536 chars.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kgn.errors import KgnError, KgnErrorCode
from kgn.github.pr_service import (
    _MAX_PR_BODY_LENGTH,
    _TRUNCATION_NOTICE,
    PRContext,
    PullRequestService,
)
from kgn.mcp._helpers import _error_json, safe_tool_call

# ── R-009: _error_json code positional ────────────────────────────────


class TestErrorJsonCodeRequired:
    """Verify _error_json requires an explicit code argument (R-009)."""

    def test_code_is_required(self) -> None:
        """Calling _error_json without code raises TypeError."""
        with pytest.raises(TypeError, match="required"):
            _error_json("some error")  # type: ignore[call-arg]

    def test_with_explicit_code(self) -> None:
        """Calling with explicit code works normally."""
        result = json.loads(_error_json("test error", KgnErrorCode.INVALID_UUID))
        assert result["code"] == "KGN-210"
        assert result["error"] == "test error"

    def test_with_detail(self) -> None:
        """detail kwarg still works with the new signature."""
        result = json.loads(_error_json("msg", KgnErrorCode.NODE_NOT_FOUND, detail="extra info"))
        assert result["detail"] == "extra info"
        assert result["code"] == "KGN-300"

    def test_recoverable_flag(self) -> None:
        """Recoverable codes return recoverable=True."""
        result = json.loads(_error_json("timeout", KgnErrorCode.DB_TIMEOUT))
        assert result["recoverable"] is True

    def test_non_recoverable_flag(self) -> None:
        """Non-recoverable codes return recoverable=False."""
        result = json.loads(_error_json("bad", KgnErrorCode.INTERNAL_ERROR))
        assert result["recoverable"] is False


# ── R-010: KgnError re-raised through safe_tool_call ──────────────────


class TestKgnErrorReraise:
    """Verify KgnError is re-raised and handled by safe_tool_call (R-010)."""

    def test_kgnerror_code_preserved_by_safe_tool_call(self) -> None:
        """When a tool raises KgnError, safe_tool_call uses its code (R-010)."""

        @safe_tool_call
        def failing_tool() -> str:
            raise KgnError(
                KgnErrorCode.TASK_DEPENDENCY_CYCLE,
                "cycle detected",
                detail="A→B→A",
            )

        result = json.loads(failing_tool())
        assert result["code"] == "KGN-404"
        assert "cycle" in result["error"]

    def test_non_kgnerror_falls_through_to_internal(self) -> None:
        """Non-KgnError exceptions become INTERNAL_ERROR via safe_tool_call."""

        @safe_tool_call
        def random_failure() -> str:
            raise RuntimeError("unexpected crash")

        result = json.loads(random_failure())
        assert result["code"] == "KGN-999"

    def test_kgnerror_in_write_tool_reraises(self) -> None:
        """Simulated KgnError from IngestService is handled by safe_tool_call,
        not swallowed into INVALID_KGN_FORMAT (R-010)."""

        @safe_tool_call
        def simulated_ingest_node() -> str:
            # Simulate what happens inside ingest_node when a KgnError is raised
            from psycopg import OperationalError as _OpErr

            try:
                raise KgnError(
                    KgnErrorCode.DUPLICATE_CONTENT,
                    "content hash collision",
                )
            except _OpErr:
                raise
            except KgnError:
                raise  # R-010: re-raise for safe_tool_call
            except Exception:
                # This path should NOT be reached for KgnError
                return _error_json("Ingest failed", KgnErrorCode.INVALID_KGN_FORMAT)

        result = json.loads(simulated_ingest_node())
        # Must be KGN-202 (DUPLICATE_CONTENT), not KGN-203 (INVALID_KGN_FORMAT)
        assert result["code"] == "KGN-202"

    def test_kgnerror_in_task_tool_reraises(self) -> None:
        """Simulated KgnError from TaskService is handled by safe_tool_call,
        not swallowed into TASK_NOT_IN_PROGRESS (R-010)."""

        @safe_tool_call
        def simulated_task_complete() -> str:
            from psycopg import OperationalError as _OpErr

            try:
                raise KgnError(
                    KgnErrorCode.TASK_DEPENDENCY_CYCLE,
                    "circular dependency",
                )
            except _OpErr:
                raise
            except KgnError:
                raise  # R-010: re-raise for safe_tool_call
            except Exception:
                return _error_json("fail", KgnErrorCode.TASK_NOT_IN_PROGRESS)

        result = json.loads(simulated_task_complete())
        # Must be KGN-404 (TASK_DEPENDENCY_CYCLE), not KGN-401 (TASK_NOT_IN_PROGRESS)
        assert result["code"] == "KGN-404"

    def test_generic_exception_still_caught(self) -> None:
        """Non-KgnError exceptions still get the expected fallback code."""

        @safe_tool_call
        def simulated_enqueue() -> str:
            from psycopg import OperationalError as _OpErr

            try:
                raise ValueError("node not found")
            except _OpErr:
                raise
            except KgnError:
                raise  # R-010: re-raise for safe_tool_call
            except Exception as exc:
                return _error_json(str(exc), KgnErrorCode.TASK_NODE_INVALID)

        result = json.loads(simulated_enqueue())
        assert result["code"] == "KGN-402"  # TASK_NODE_INVALID — fallback still works


# ── R-025: PR body truncation ─────────────────────────────────────────


class TestPRBodyTruncation:
    """Verify PR body is truncated at GitHub API limit (R-025)."""

    def _make_service(self) -> PullRequestService:
        mock_client = MagicMock()
        mock_client.create_pull_request.return_value = {
            "number": 42,
            "html_url": "https://github.com/test/repo/pull/42",
        }
        return PullRequestService(mock_client)

    def _make_ctx(self, summary_len: int = 0) -> PRContext:
        return PRContext(
            task_title="Test Task",
            task_id="abc-123",
            branch_name="feature/test",
            base_branch="main",
            agent_key="claude",
            node_summary="x" * summary_len if summary_len else "",
        )

    def test_short_body_unchanged(self) -> None:
        """Body under 65536 chars is not truncated."""
        svc = self._make_service()
        ctx = self._make_ctx(summary_len=100)

        result = svc.create_task_pr(ctx)

        assert result.success
        # Verify the body sent is NOT truncated
        call_kwargs = svc._github.create_pull_request.call_args[1]
        body = call_kwargs["body"]
        assert len(body) < _MAX_PR_BODY_LENGTH
        assert _TRUNCATION_NOTICE not in body

    def test_exact_limit_body_unchanged(self) -> None:
        """Body at exactly 65535 chars is not truncated."""
        svc = self._make_service()

        with patch.object(
            PullRequestService,
            "_build_pr_body",
            return_value="A" * 65_535,
        ):
            ctx = self._make_ctx()
            result = svc.create_task_pr(ctx)
            assert result.success

            call_kwargs = svc._github.create_pull_request.call_args[1]
            body = call_kwargs["body"]
            assert len(body) == 65_535
            assert _TRUNCATION_NOTICE not in body

    def test_body_exceeding_limit_truncated(self) -> None:
        """Body exceeding 65536 chars is truncated with notice."""
        svc = self._make_service()
        # Make a summary long enough to exceed the limit
        ctx = self._make_ctx(summary_len=70_000)

        result = svc.create_task_pr(ctx)
        assert result.success

        call_kwargs = svc._github.create_pull_request.call_args[1]
        body = call_kwargs["body"]
        assert len(body) <= _MAX_PR_BODY_LENGTH
        assert body.endswith(_TRUNCATION_NOTICE)

    def test_boundary_65536(self) -> None:
        """Body of exactly 65536 chars is not truncated (boundary value)."""
        svc = self._make_service()

        # Manually patch _build_pr_body to return exactly 65536 chars
        with patch.object(
            PullRequestService,
            "_build_pr_body",
            return_value="A" * 65_536,
        ):
            ctx = self._make_ctx()
            result = svc.create_task_pr(ctx)
            assert result.success

            call_kwargs = svc._github.create_pull_request.call_args[1]
            body = call_kwargs["body"]
            assert len(body) == 65_536
            assert _TRUNCATION_NOTICE not in body

    def test_boundary_65537(self) -> None:
        """Body of 65537 chars IS truncated (boundary value)."""
        svc = self._make_service()

        with patch.object(
            PullRequestService,
            "_build_pr_body",
            return_value="B" * 65_537,
        ):
            ctx = self._make_ctx()
            result = svc.create_task_pr(ctx)
            assert result.success

            call_kwargs = svc._github.create_pull_request.call_args[1]
            body = call_kwargs["body"]
            assert len(body) <= _MAX_PR_BODY_LENGTH
            assert body.endswith(_TRUNCATION_NOTICE)

    def test_truncated_body_length_exact(self) -> None:
        """Truncated body is exactly _MAX_PR_BODY_LENGTH chars."""
        svc = self._make_service()

        with patch.object(
            PullRequestService,
            "_build_pr_body",
            return_value="C" * 100_000,
        ):
            ctx = self._make_ctx()
            result = svc.create_task_pr(ctx)
            assert result.success

            call_kwargs = svc._github.create_pull_request.call_args[1]
            body = call_kwargs["body"]
            assert len(body) == _MAX_PR_BODY_LENGTH

    def test_max_pr_body_length_constant(self) -> None:
        """_MAX_PR_BODY_LENGTH is 65536."""
        assert _MAX_PR_BODY_LENGTH == 65_536
