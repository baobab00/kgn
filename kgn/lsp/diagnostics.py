"""Convert KGN parser diagnostics to LSP Diagnostic objects.

Handles the critical UTF-16 column conversion required by the LSP spec:
parser columns are Unicode code-point offsets, but LSP clients expect
UTF-16 code-unit columns.

Uses :class:`~kgn.lsp.position.SourceMap` for offset↔position lookups
and :class:`~kgn.lsp.position.PositionAdapter` for the encoding conversion.
"""

from __future__ import annotations

from lsprotocol import types

from kgn.lsp.position import PositionAdapter
from kgn.parser.models import DiagnosticSpan, Severity

# Severity mapping — values are intentionally identical (1:1), but we
# keep an explicit table so a future divergence doesn't break silently.
_SEVERITY_MAP: dict[Severity, types.DiagnosticSeverity] = {
    Severity.ERROR: types.DiagnosticSeverity.Error,
    Severity.WARNING: types.DiagnosticSeverity.Warning,
    Severity.INFORMATION: types.DiagnosticSeverity.Information,
    Severity.HINT: types.DiagnosticSeverity.Hint,
}

_SOURCE: str = "kgn"


def convert_diagnostics(
    spans: list[DiagnosticSpan],
    text: str,
) -> list[types.Diagnostic]:
    """Convert a list of :class:`DiagnosticSpan` to LSP ``Diagnostic`` objects.

    Parameters:
        spans: Parser diagnostic spans (code-point columns).
        text: Full document text — needed for per-line UTF-16 conversion.

    Returns:
        A list of LSP-ready ``Diagnostic`` objects with UTF-16 positions.
    """
    if not spans:
        return []

    lines = text.split("\n")
    diagnostics: list[types.Diagnostic] = []

    for span in spans:
        start_utf16 = _to_utf16_col(lines, span.start_line, span.start_col)
        end_utf16 = _to_utf16_col(lines, span.end_line, span.end_col)

        diag = types.Diagnostic(
            range=types.Range(
                start=types.Position(line=span.start_line, character=start_utf16),
                end=types.Position(line=span.end_line, character=end_utf16),
            ),
            message=span.message,
            severity=_SEVERITY_MAP.get(span.severity, types.DiagnosticSeverity.Error),
            source=_SOURCE,
            code=span.rule,
        )
        diagnostics.append(diag)

    return diagnostics


def _to_utf16_col(lines: list[str], line: int, codepoint_col: int) -> int:
    """Convert a code-point column to a UTF-16 column for a given line.

    Gracefully handles out-of-range line numbers by clamping.
    """
    if line < 0 or line >= len(lines):
        return 0
    return PositionAdapter.utf8_col_to_utf16(lines[line], codepoint_col)
