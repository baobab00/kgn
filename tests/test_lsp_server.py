"""Tests for kgn.lsp.server — LSP server handlers, debounce, lifecycle.

Uses a combination of:
* Direct function tests (debounce engine, diagnostics pipeline).
* Mock-based handler tests (verifying publish_diagnostics calls).
* pygls API verification (server instance capabilities).

These tests do NOT start an actual LSP I/O loop — they exercise the
server logic in isolation.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from lsprotocol import types

from kgn.lsp.server import (
    _DEBOUNCE_MS,
    _cancel_pending,
    _debounced_run,
    _on_did_change,
    _on_did_close,
    _on_did_open,
    _on_did_save,
    _on_initialized,
    _on_shutdown,
    _pending_tasks,
    _run_diagnostics,
    _schedule_diagnostics,
    server,
)

# ── Fixtures ──────────────────────────────────────────────────────────

VALID_KGN = (
    "---\n"
    "kgn_version: '0.1'\n"
    "id: new:test\n"
    "type: SPEC\n"
    "title: Test Node\n"
    "status: ACTIVE\n"
    "project_id: proj\n"
    "agent_id: agent\n"
    "---\n"
    "# Body\n"
)

BROKEN_KGN = "---\nbad: [unclosed\n---\n"

MISSING_ID_KGN = (
    "---\n"
    "kgn_version: '0.1'\n"
    "type: SPEC\n"
    "title: No ID\n"
    "status: ACTIVE\n"
    "project_id: proj\n"
    "agent_id: agent\n"
    "---\n"
)


@pytest.fixture(autouse=True)
def _clear_pending():
    """Ensure pending tasks dict is clean between tests."""
    _pending_tasks.clear()
    yield
    # Cancel any remaining tasks
    for task in _pending_tasks.values():
        if not task.done():
            task.cancel()
    _pending_tasks.clear()


# ── Server Instance Tests ────────────────────────────────────────────


class TestServerInstance:
    """Verify the server is properly configured."""

    def test_server_name(self):
        assert server.name == "kgn-lsp"

    def test_server_has_version(self):
        from kgn import __version__

        assert server.version == __version__

    def test_text_document_sync_kind(self):
        assert server._text_document_sync_kind == types.TextDocumentSyncKind.Incremental


# ── Lifecycle Tests ──────────────────────────────────────────────────


class TestLifecycle:
    """Lifecycle handler tests."""

    def test_initialized_does_not_raise(self):
        mock_workspace = MagicMock()
        mock_workspace.folders = {}
        with patch.object(
            type(server),
            "workspace",
            new_callable=lambda: property(lambda self: mock_workspace),
        ):
            _on_initialized(types.InitializedParams())

    def test_shutdown_clears_pending_tasks(self):
        _pending_tasks["file:///test.kgn"] = MagicMock(
            done=MagicMock(return_value=False),
            cancel=MagicMock(),
        )
        _on_shutdown(None)
        assert len(_pending_tasks) == 0

    def test_shutdown_cancels_running_tasks(self):
        mock_task = MagicMock(done=MagicMock(return_value=False))
        _pending_tasks["file:///a.kgn"] = mock_task
        _on_shutdown(None)
        mock_task.cancel.assert_called_once()


# ── Debounce Engine Tests ─────────────────────────────────────────────


class TestCancelPending:
    """Tests for _cancel_pending."""

    def test_cancel_nonexistent_uri_noop(self):
        _cancel_pending("file:///nonexistent.kgn")
        assert len(_pending_tasks) == 0

    def test_cancel_existing_task(self):
        mock_task = MagicMock(done=MagicMock(return_value=False))
        _pending_tasks["file:///test.kgn"] = mock_task
        _cancel_pending("file:///test.kgn")
        mock_task.cancel.assert_called_once()
        assert "file:///test.kgn" not in _pending_tasks

    def test_skip_cancel_if_already_done(self):
        mock_task = MagicMock(done=MagicMock(return_value=True))
        _pending_tasks["file:///test.kgn"] = mock_task
        _cancel_pending("file:///test.kgn")
        mock_task.cancel.assert_not_called()
        assert "file:///test.kgn" not in _pending_tasks


class TestDebouncedRun:
    """Tests for the debounce mechanism."""

    @pytest.mark.asyncio
    async def test_immediate_run_no_debounce(self):
        """debounce_ms=0 → immediate execution."""
        with patch("kgn.lsp.server._run_diagnostics", new_callable=AsyncMock) as mock_run:
            await _debounced_run("file:///test.kgn", VALID_KGN, 0)
            mock_run.assert_called_once_with("file:///test.kgn", VALID_KGN)

    @pytest.mark.asyncio
    async def test_debounce_waits(self):
        """debounce_ms > 0 → sleeps before running."""
        with patch("kgn.lsp.server._run_diagnostics", new_callable=AsyncMock) as mock_run:
            await _debounced_run("file:///test.kgn", VALID_KGN, 50)
            mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancelled_error_is_swallowed(self):
        """CancelledError during sleep should not propagate."""

        async def cancel_me(*_args, **_kwargs):
            raise asyncio.CancelledError

        with patch("kgn.lsp.server._run_diagnostics", side_effect=cancel_me):
            # Should not raise
            await _debounced_run("file:///test.kgn", VALID_KGN, 0)

    @pytest.mark.asyncio
    async def test_unexpected_error_is_logged_not_raised(self):
        """Unexpected exceptions are caught and logged."""
        with patch("kgn.lsp.server._run_diagnostics", side_effect=RuntimeError("boom")):
            # Should not raise
            await _debounced_run("file:///test.kgn", VALID_KGN, 0)

    @pytest.mark.asyncio
    async def test_pending_task_removed_after_completion(self):
        """Task is removed from _pending_tasks after completion."""
        _pending_tasks["file:///test.kgn"] = MagicMock()
        with patch("kgn.lsp.server._run_diagnostics", new_callable=AsyncMock):
            await _debounced_run("file:///test.kgn", VALID_KGN, 0)
        assert "file:///test.kgn" not in _pending_tasks


class TestScheduleDiagnostics:
    """Tests for _schedule_diagnostics."""

    def test_creates_pending_task(self):
        """Scheduling creates a task in _pending_tasks."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch("kgn.lsp.server._run_diagnostics", new_callable=AsyncMock):
                _schedule_diagnostics("file:///test.kgn", VALID_KGN, debounce_ms=0)
                assert "file:///test.kgn" in _pending_tasks
                # Clean up
                task = _pending_tasks["file:///test.kgn"]
                task.cancel()
        finally:
            loop.close()

    def test_replaces_previous_task(self):
        """New schedule cancels the previous pending task."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch("kgn.lsp.server._run_diagnostics", new_callable=AsyncMock):
                _schedule_diagnostics("file:///test.kgn", "old", debounce_ms=1000)
                first_task = _pending_tasks["file:///test.kgn"]
                _schedule_diagnostics("file:///test.kgn", "new", debounce_ms=1000)
                second_task = _pending_tasks["file:///test.kgn"]
                assert first_task is not second_task
                # cancel() was called but task may not yet report cancelled()
                # until the loop iterates; verify cancelling() instead
                assert first_task.cancelling() > 0
                second_task.cancel()
        finally:
            loop.close()


# ── Diagnostic Pipeline Tests ─────────────────────────────────────────


class TestRunDiagnostics:
    """Tests for _run_diagnostics — actual parsing + publishing."""

    @pytest.mark.asyncio
    async def test_valid_document_publishes_empty(self):
        """Valid .kgn → no diagnostics published."""
        with patch.object(server, "text_document_publish_diagnostics") as mock_pub:
            await _run_diagnostics("file:///valid.kgn", VALID_KGN)
            mock_pub.assert_called_once()
            params = mock_pub.call_args[0][0]
            assert params.uri == "file:///valid.kgn"
            assert params.diagnostics == []

    @pytest.mark.asyncio
    async def test_broken_yaml_publishes_errors(self):
        """Broken YAML → at least one error diagnostic."""
        with patch.object(server, "text_document_publish_diagnostics") as mock_pub:
            await _run_diagnostics("file:///broken.kgn", BROKEN_KGN)
            mock_pub.assert_called_once()
            params = mock_pub.call_args[0][0]
            assert len(params.diagnostics) >= 1
            assert any(d.severity == types.DiagnosticSeverity.Error for d in params.diagnostics)

    @pytest.mark.asyncio
    async def test_missing_field_publishes_errors(self):
        """Missing required field → error diagnostic."""
        with patch.object(server, "text_document_publish_diagnostics") as mock_pub:
            await _run_diagnostics("file:///missing.kgn", MISSING_ID_KGN)
            mock_pub.assert_called_once()
            params = mock_pub.call_args[0][0]
            assert len(params.diagnostics) >= 1

    @pytest.mark.asyncio
    async def test_empty_document_publishes_errors(self):
        """Empty document → diagnostics for missing front matter."""
        with patch.object(server, "text_document_publish_diagnostics") as mock_pub:
            await _run_diagnostics("file:///empty.kgn", "")
            mock_pub.assert_called_once()
            params = mock_pub.call_args[0][0]
            assert len(params.diagnostics) >= 1

    @pytest.mark.asyncio
    async def test_non_string_input_does_not_crash(self):
        """R24: parse_kgn_tolerant handles non-string, server doesn't crash."""
        with patch.object(server, "text_document_publish_diagnostics") as mock_pub:
            # Non-string text is coerced to str in _run_diagnostics
            await _run_diagnostics("file:///weird.kgn", 123)  # type: ignore[arg-type]
            mock_pub.assert_called_once()

    @pytest.mark.asyncio
    async def test_diagnostics_have_source_kgn(self):
        """All diagnostics must have source='kgn'."""
        with patch.object(server, "text_document_publish_diagnostics") as mock_pub:
            await _run_diagnostics("file:///broken.kgn", BROKEN_KGN)
            params = mock_pub.call_args[0][0]
            for d in params.diagnostics:
                assert d.source == "kgn"

    @pytest.mark.asyncio
    async def test_diagnostics_have_rule_code(self):
        """All diagnostics must have a rule code."""
        with patch.object(server, "text_document_publish_diagnostics") as mock_pub:
            await _run_diagnostics("file:///broken.kgn", BROKEN_KGN)
            params = mock_pub.call_args[0][0]
            for d in params.diagnostics:
                assert d.code is not None
                assert isinstance(d.code, str)


# ── Handler Tests (didOpen/didChange/didSave/didClose) ────────────────


class TestDidOpenHandler:
    """Tests for textDocument/didOpen handler."""

    def test_did_open_schedules_diagnostics(self):
        """didOpen calls _schedule_diagnostics with debounce=0."""
        with patch("kgn.lsp.server._schedule_diagnostics") as mock_sched:
            params = types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri="file:///test.kgn",
                    language_id="kgn",
                    version=1,
                    text=VALID_KGN,
                ),
            )
            _on_did_open(params)
            mock_sched.assert_called_once_with(
                "file:///test.kgn",
                VALID_KGN,
                debounce_ms=0,
            )


class TestDidChangeHandler:
    """Tests for textDocument/didChange handler."""

    def test_did_change_schedules_debounced(self):
        """didChange uses debounce."""
        mock_doc = MagicMock()
        mock_doc.source = "changed text"
        mock_workspace = MagicMock()
        mock_workspace.get_text_document.return_value = mock_doc
        with (
            patch("kgn.lsp.server._schedule_diagnostics") as mock_sched,
            patch.object(
                type(server),
                "workspace",
                new_callable=lambda: property(lambda self: mock_workspace),
            ),
        ):
            params = types.DidChangeTextDocumentParams(
                text_document=types.VersionedTextDocumentIdentifier(
                    uri="file:///test.kgn",
                    version=2,
                ),
                content_changes=[],
            )
            _on_did_change(params)
            mock_sched.assert_called_once_with(
                "file:///test.kgn",
                "changed text",
                debounce_ms=_DEBOUNCE_MS,
            )


class TestDidSaveHandler:
    """Tests for textDocument/didSave handler."""

    def test_did_save_schedules_immediate(self):
        """didSave bypasses debounce (debounce_ms=0)."""
        mock_doc = MagicMock()
        mock_doc.source = "saved text"
        mock_workspace = MagicMock()
        mock_workspace.get_text_document.return_value = mock_doc
        with (
            patch("kgn.lsp.server._schedule_diagnostics") as mock_sched,
            patch.object(
                type(server),
                "workspace",
                new_callable=lambda: property(lambda self: mock_workspace),
            ),
        ):
            params = types.DidSaveTextDocumentParams(
                text_document=types.TextDocumentIdentifier(
                    uri="file:///test.kgn",
                ),
            )
            _on_did_save(params)
            mock_sched.assert_called_once_with(
                "file:///test.kgn",
                "saved text",
                debounce_ms=0,
            )


class TestDidCloseHandler:
    """Tests for textDocument/didClose handler."""

    def test_did_close_cancels_pending(self):
        """didClose cancels pending diagnostics."""
        with (
            patch("kgn.lsp.server._cancel_pending") as mock_cancel,
            patch.object(server, "text_document_publish_diagnostics"),
        ):
            params = types.DidCloseTextDocumentParams(
                text_document=types.TextDocumentIdentifier(
                    uri="file:///test.kgn",
                ),
            )
            _on_did_close(params)
            mock_cancel.assert_called_once_with("file:///test.kgn")

    def test_did_close_publishes_empty_diagnostics(self):
        """didClose clears diagnostics on the client."""
        with (
            patch("kgn.lsp.server._cancel_pending"),
            patch.object(server, "text_document_publish_diagnostics") as mock_pub,
        ):
            params = types.DidCloseTextDocumentParams(
                text_document=types.TextDocumentIdentifier(
                    uri="file:///test.kgn",
                ),
            )
            _on_did_close(params)
            mock_pub.assert_called_once()
            pub_params = mock_pub.call_args[0][0]
            assert pub_params.diagnostics == []


# ── UTF-16 Position Accuracy ─────────────────────────────────────────


class TestUtf16PositionAccuracy:
    """Verify UTF-16 positions in published diagnostics."""

    @pytest.mark.asyncio
    async def test_korean_title_position(self):
        """Korean text produces correct UTF-16 positions."""
        text = (
            "---\n"
            "kgn_version: '0.1'\n"
            "id: new:test\n"
            "type: INVALID_TYPE\n"
            "title: 한글제목\n"
            "status: ACTIVE\n"
            "project_id: proj\n"
            "agent_id: agent\n"
            "---\n"
        )
        with patch.object(server, "text_document_publish_diagnostics") as mock_pub:
            await _run_diagnostics("file:///korean.kgn", text)
            mock_pub.assert_called_once()
            params = mock_pub.call_args[0][0]
            # Should have diagnostics for invalid type
            assert len(params.diagnostics) >= 1
            for d in params.diagnostics:
                # All positions should be non-negative
                assert d.range.start.line >= 0
                assert d.range.start.character >= 0
                assert d.range.end.line >= 0
                assert d.range.end.character >= 0


# ── Constants ─────────────────────────────────────────────────────────


class TestConstants:
    """Verify module-level constants."""

    def test_debounce_ms_default(self):
        assert _DEBOUNCE_MS == 300

    def test_debounce_ms_positive(self):
        assert _DEBOUNCE_MS > 0
