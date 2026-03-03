"""Data classes for parser output."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from kgn.models.node import NodeFrontMatter

# ── Diagnostic models (Phase 11 — fault-tolerant parsing) ─────────────


class Severity(IntEnum):
    """Diagnostic severity levels, 1:1 with LSP DiagnosticSeverity.

    Values intentionally match the LSP specification so that conversion
    to ``lsprotocol.types.DiagnosticSeverity`` is a no-op cast.
    """

    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    HINT = 4


@dataclass(frozen=True)
class DiagnosticSpan:
    """A single diagnostic message anchored to a source range.

    All line/column values are **0-based** to align with LSP Position.
    A span covering an entire line uses ``start_col=0, end_col=<line_length>``.

    Attributes:
        rule: Validation rule identifier (e.g. ``"V1"``, ``"YAML_ERROR"``).
        message: Human-readable description of the problem.
        severity: One of :class:`Severity` values.
        start_line: 0-based starting line number.
        start_col: 0-based starting column (**Unicode code-point** offset within the line).
        end_line: 0-based ending line number (inclusive).
        end_col: 0-based ending column (exclusive, **Unicode code-point** offset).

    .. note::
        Column values are **code-point offsets** (Python ``str`` indexing),
        **not** UTF-8 byte offsets.  Use :class:`~kgn.lsp.position.PositionAdapter`
        to convert to UTF-16 code-unit offsets before sending to LSP clients.
    """

    rule: str
    message: str
    severity: Severity
    start_line: int = 0
    start_col: int = 0
    end_line: int = 0
    end_col: int = 0


@dataclass
class PartialParseResult:
    """Result of fault-tolerant .kgn parsing — **never** raises exceptions.

    Even when the document is severely broken, this object is returned with
    ``front_matter=None`` and one or more diagnostics describing the problems.

    Attributes:
        front_matter: Validated front matter, or ``None`` on parse/validation failure.
        body: Markdown body text (best-effort extraction).
        diagnostics: All accumulated diagnostic spans.
        content_hash: SHA-256 hex digest, or ``None`` when hashing is impossible.
        source_path: Original file path, if available.
        yaml_node_positions: Mapping of YAML key names to their (start_line, start_col,
            end_line, end_col) ranges within the *original* document (0-based).
            Populated via ``yaml.compose()`` when YAML parsing succeeds.
    """

    front_matter: NodeFrontMatter | None = None
    body: str = ""
    diagnostics: list[DiagnosticSpan] = field(default_factory=list)
    content_hash: str | None = None
    source_path: str | None = None
    yaml_node_positions: dict[str, tuple[int, int, int, int]] = field(
        default_factory=dict,
    )

    @property
    def has_errors(self) -> bool:
        """Return ``True`` if any diagnostic has ERROR severity."""
        return any(d.severity == Severity.ERROR for d in self.diagnostics)


# ── Strict-mode models (Phase 1–10, unchanged) ────────────────────────


@dataclass(frozen=True)
class ParsedNode:
    """Result of parsing a .kgn file.

    Attributes:
        front_matter: Validated YAML front matter as a Pydantic model.
        body: Raw Markdown body text (after the closing ``---``).
        content_hash: SHA-256 hex digest of (front_matter YAML + body).
        source_path: Original file path, if available.
    """

    front_matter: NodeFrontMatter
    body: str
    content_hash: str
    source_path: str | None = None


@dataclass
class ValidationResult:
    """Aggregated validation outcome.

    Attributes:
        is_valid: ``True`` when there are zero errors (warnings are OK).
        errors: List of FAILED rule descriptions.
        warnings: List of WARNING rule descriptions.
    """

    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        """Record a FAILED validation rule."""
        self.errors.append(msg)
        self.is_valid = False

    def add_warning(self, msg: str) -> None:
        """Record a WARNING (non-fatal)."""
        self.warnings.append(msg)
