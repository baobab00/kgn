"""Tests for kgn.lsp.diagnostics — DiagnosticSpan → LSP Diagnostic conversion."""

from __future__ import annotations

from lsprotocol import types

from kgn.lsp.diagnostics import _to_utf16_col, convert_diagnostics
from kgn.parser.models import DiagnosticSpan, Severity

# ── Fixtures ──────────────────────────────────────────────────────────

SIMPLE_TEXT = "---\nid: new:test\ntype: SPEC\ntitle: Hello\nstatus: ACTIVE\n---\n# Body"

KOREAN_TEXT = "---\ntitle: 한글제목입니다\n---\n# 본문"

EMOJI_TEXT = "---\ntitle: test 🎉🎊 emoji\n---\n# Body"


# ── convert_diagnostics ──────────────────────────────────────────────


class TestConvertDiagnostics:
    """Core conversion from DiagnosticSpan → LSP Diagnostic."""

    def test_empty_spans_returns_empty(self):
        result = convert_diagnostics([], "some text")
        assert result == []

    def test_single_error_span(self):
        span = DiagnosticSpan(
            rule="V1",
            message="Missing required field: id",
            severity=Severity.ERROR,
            start_line=1,
            start_col=0,
            end_line=1,
            end_col=5,
        )
        result = convert_diagnostics([span], SIMPLE_TEXT)
        assert len(result) == 1
        diag = result[0]
        assert diag.message == "Missing required field: id"
        assert diag.severity == types.DiagnosticSeverity.Error
        assert diag.source == "kgn"
        assert diag.code == "V1"
        assert diag.range.start.line == 1
        assert diag.range.end.line == 1

    def test_warning_severity_mapping(self):
        span = DiagnosticSpan(
            rule="V5",
            message="warn",
            severity=Severity.WARNING,
        )
        result = convert_diagnostics([span], "text")
        assert result[0].severity == types.DiagnosticSeverity.Warning

    def test_information_severity_mapping(self):
        span = DiagnosticSpan(
            rule="V6",
            message="info",
            severity=Severity.INFORMATION,
        )
        result = convert_diagnostics([span], "text")
        assert result[0].severity == types.DiagnosticSeverity.Information

    def test_hint_severity_mapping(self):
        span = DiagnosticSpan(
            rule="V7",
            message="hint",
            severity=Severity.HINT,
        )
        result = convert_diagnostics([span], "text")
        assert result[0].severity == types.DiagnosticSeverity.Hint

    def test_multiple_spans(self):
        spans = [
            DiagnosticSpan(rule="V1", message="err1", severity=Severity.ERROR),
            DiagnosticSpan(
                rule="V2",
                message="err2",
                severity=Severity.ERROR,
                start_line=2,
                start_col=0,
                end_line=2,
                end_col=4,
            ),
        ]
        result = convert_diagnostics(spans, SIMPLE_TEXT)
        assert len(result) == 2
        assert result[0].code == "V1"
        assert result[1].code == "V2"

    def test_rule_as_diagnostic_code(self):
        span = DiagnosticSpan(
            rule="YAML_ERROR",
            message="bad yaml",
            severity=Severity.ERROR,
        )
        result = convert_diagnostics([span], "text")
        assert result[0].code == "YAML_ERROR"

    def test_source_is_kgn(self):
        span = DiagnosticSpan(rule="V1", message="x", severity=Severity.ERROR)
        result = convert_diagnostics([span], "text")
        assert result[0].source == "kgn"


class TestUtf16ColumnConversion:
    """UTF-16 column conversion via convert_diagnostics."""

    def test_ascii_columns_unchanged(self):
        """ASCII text: code-point col == UTF-16 col."""
        span = DiagnosticSpan(
            rule="V1",
            message="x",
            severity=Severity.ERROR,
            start_line=1,
            start_col=4,
            end_line=1,
            end_col=12,
        )
        result = convert_diagnostics([span], SIMPLE_TEXT)
        assert result[0].range.start.character == 4
        assert result[0].range.end.character == 12

    def test_korean_columns(self):
        """Korean chars are 1 UTF-16 code unit each, same as code point."""
        # title: 한글제목입니다  →  "title" at col 0..4
        span = DiagnosticSpan(
            rule="V1",
            message="x",
            severity=Severity.ERROR,
            start_line=1,
            start_col=7,
            end_line=1,
            end_col=13,
        )
        result = convert_diagnostics([span], KOREAN_TEXT)
        # Korean BMP chars: 1 cp = 1 UTF-16 unit, so columns match
        assert result[0].range.start.character == 7
        assert result[0].range.end.character == 13

    def test_emoji_columns_shift(self):
        """Emoji (supplementary plane) produce 2 UTF-16 code units each."""
        # "title: test 🎉🎊 emoji"
        #  cols:  0123456789...
        # 🎉 is at code-point col 12, 🎊 at col 13
        # After 🎉🎊 (2 emoji), UTF-16 cols shift by +2 (each emoji=2 units)
        span = DiagnosticSpan(
            rule="V1",
            message="x",
            severity=Severity.ERROR,
            start_line=1,
            start_col=15,  # 'e' in "emoji" (code-point)
            end_line=1,
            end_col=20,  # end of "emoji"
        )
        result = convert_diagnostics([span], EMOJI_TEXT)
        # 2 emoji shift by +2 total
        assert result[0].range.start.character == 17
        assert result[0].range.end.character == 22


class TestToUtf16Col:
    """Direct tests for the _to_utf16_col helper."""

    def test_ascii_no_change(self):
        assert _to_utf16_col(["hello world"], 0, 5) == 5

    def test_negative_line_returns_zero(self):
        assert _to_utf16_col(["hello"], -1, 3) == 0

    def test_out_of_range_line_returns_zero(self):
        assert _to_utf16_col(["hello"], 5, 3) == 0

    def test_empty_lines(self):
        assert _to_utf16_col(["", "abc"], 0, 0) == 0
        assert _to_utf16_col(["", "abc"], 1, 2) == 2

    def test_korean_line(self):
        # "가나다" — each is 1 UTF-16 code unit
        assert _to_utf16_col(["가나다"], 0, 2) == 2

    def test_emoji_line(self):
        # "a🎉b" — 🎉 is 2 UTF-16 code units
        # code-point col 2 (b) → UTF-16 col 3
        assert _to_utf16_col(["a🎉b"], 0, 2) == 3


# ── Integration with parse_kgn_tolerant ───────────────────────────────


class TestParserIntegration:
    """Convert actual parse_kgn_tolerant diagnostics to LSP format."""

    def test_valid_document_no_diagnostics(self):
        from kgn.parser import parse_kgn_tolerant

        text = (
            "---\n"
            "kgn_version: '0.1'\n"
            "id: new:test\n"
            "type: SPEC\n"
            "title: Valid\n"
            "status: ACTIVE\n"
            "project_id: proj\n"
            "agent_id: agent\n"
            "---\n"
            "# Body\n"
        )
        result = parse_kgn_tolerant(text)
        diagnostics = convert_diagnostics(result.diagnostics, text)
        assert diagnostics == []

    def test_missing_field_produces_error(self):
        from kgn.parser import parse_kgn_tolerant

        text = (
            "---\n"
            "kgn_version: '0.1'\n"
            "type: SPEC\n"
            "title: Missing ID\n"
            "status: ACTIVE\n"
            "project_id: proj\n"
            "agent_id: agent\n"
            "---\n"
            "# Body\n"
        )
        result = parse_kgn_tolerant(text)
        diagnostics = convert_diagnostics(result.diagnostics, text)
        assert len(diagnostics) >= 1
        assert any(d.severity == types.DiagnosticSeverity.Error for d in diagnostics)

    def test_broken_yaml_produces_error(self):
        from kgn.parser import parse_kgn_tolerant

        text = "---\nbad: [unclosed\n---\n"
        result = parse_kgn_tolerant(text)
        diagnostics = convert_diagnostics(result.diagnostics, text)
        assert len(diagnostics) >= 1
        assert diagnostics[0].severity == types.DiagnosticSeverity.Error
