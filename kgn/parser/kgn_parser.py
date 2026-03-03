"""Parser for .kgn (Knowledge Graph Node) files.

Splits a .kgn file into YAML front matter and Markdown body,
validates the front matter through the Pydantic model, and
computes a content hash.

Two parsing modes are provided:

* **Strict** (``parse_kgn_text``) — raises ``KgnParseError`` on any error.
  Used by the ingest pipeline (Phase 1–10, unchanged).
* **Tolerant** (``parse_kgn_tolerant``) — **never** raises.  Returns a
  ``PartialParseResult`` with accumulated ``DiagnosticSpan`` items.
  Used by the LSP server (Phase 11+).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from kgn.models.node import NodeFrontMatter
from kgn.parser.models import (
    DiagnosticSpan,
    ParsedNode,
    PartialParseResult,
    Severity,
)


class KgnParseError(Exception):
    """Raised when a .kgn file cannot be parsed."""


# ── Shared helpers ─────────────────────────────────────────────────────


def _split_front_matter(text: str) -> tuple[str, str]:
    """Split ``---`` delimited YAML front matter from Markdown body.

    Returns:
        (yaml_text, body_text) — both as raw strings.

    Raises:
        KgnParseError: when front matter delimiters are missing.
    """
    stripped = text.lstrip("\ufeff")  # strip optional BOM
    if not stripped.startswith("---"):
        raise KgnParseError("V1: File does not start with '---' (no YAML front matter)")

    # Find the closing '---' (skip the opening one)
    end_idx = stripped.find("---", 3)
    if end_idx == -1:
        raise KgnParseError("V1: Closing '---' delimiter not found")

    yaml_text = stripped[3:end_idx].strip()
    body_text = stripped[end_idx + 3 :].strip()
    return yaml_text, body_text


def _compute_hash(yaml_text: str, body: str) -> str:
    """SHA-256 hex digest of the concatenated front matter + body."""
    payload = (yaml_text + "\n" + body).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ── Strict mode (Phase 1–10, unchanged) ───────────────────────────────


def parse_kgn(source: str | Path) -> ParsedNode:
    """Parse a ``.kgn`` file into a :class:`ParsedNode`.

    Parameters:
        source: File path (str or Path) to read.

    Returns:
        ParsedNode with validated front matter, body text, and content hash.

    Raises:
        KgnParseError: on structural or validation errors.
    """
    path = Path(source)
    text = path.read_text(encoding="utf-8")
    return parse_kgn_text(text, source_path=str(path))


def parse_kgn_text(text: str, *, source_path: str | None = None) -> ParsedNode:
    """Parse raw ``.kgn`` text (useful for testing without files).

    Parameters:
        text: Full file content as a string.
        source_path: Optional origin path for diagnostics.

    Returns:
        ParsedNode

    Raises:
        KgnParseError: on structural or YAML/validation errors.
    """
    yaml_text, body = _split_front_matter(text)

    # YAML parse
    try:
        data: dict = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise KgnParseError(f"YAML syntax error: {exc}") from exc

    if not isinstance(data, dict):
        raise KgnParseError("YAML front matter must be a mapping")

    # Pydantic validation
    try:
        front_matter = NodeFrontMatter(**data)
    except ValidationError as exc:
        raise KgnParseError(f"Front matter validation failed: {exc}") from exc

    content_hash = _compute_hash(yaml_text, body)

    return ParsedNode(
        front_matter=front_matter,
        body=body,
        content_hash=content_hash,
        source_path=source_path,
    )


# ── Tolerant mode (Phase 11+ LSP) ─────────────────────────────────────


def _line_end_col(text: str, line: int) -> int:
    """Return the length (in chars) of the given 0-based *line*."""
    lines = text.split("\n")
    if 0 <= line < len(lines):
        return len(lines[line])
    return 0


def _split_front_matter_tolerant(
    text: str,
) -> tuple[str | None, str, list[DiagnosticSpan], int]:
    """Tolerant version of ``_split_front_matter``.

    Returns:
        (yaml_text_or_None, body_text, diagnostics, yaml_end_line_offset)
        ``yaml_end_line_offset`` is the number of lines consumed by the
        front matter block (including delimiters), so body line numbers
        can be offset-adjusted.
    """
    diags: list[DiagnosticSpan] = []
    stripped = text.lstrip("\ufeff")

    if not stripped.startswith("---"):
        diags.append(
            DiagnosticSpan(
                rule="V1",
                message="File does not start with '---' — no YAML front matter",
                severity=Severity.ERROR,
                start_line=0,
                start_col=0,
                end_line=0,
                end_col=_line_end_col(stripped, 0),
            ),
        )
        return None, stripped, diags, 0

    # Find closing '---'
    end_idx = stripped.find("---", 3)
    if end_idx == -1:
        diags.append(
            DiagnosticSpan(
                rule="V1",
                message="Closing '---' delimiter not found",
                severity=Severity.ERROR,
                start_line=0,
                start_col=0,
                end_line=0,
                end_col=3,
            ),
        )
        # Try the entire text after '---\n' as YAML
        candidate = stripped[3:].strip()
        # Count lines consumed (entire document)
        yaml_end_offset = stripped.count("\n") + 1
        return candidate if candidate else None, "", diags, yaml_end_offset

    yaml_text = stripped[3:end_idx].strip()
    body_text = stripped[end_idx + 3 :].strip()
    # Lines consumed = everything up to and including the closing '---' line
    yaml_end_offset = stripped[: end_idx + 3].count("\n") + 1
    return yaml_text, body_text, diags, yaml_end_offset


def _yaml_compose_safe(
    yaml_text: str,
    yaml_line_offset: int,
) -> tuple[dict[str, Any] | None, dict[str, tuple[int, int, int, int]], list[DiagnosticSpan]]:
    """Parse YAML via ``yaml.compose()`` to extract node positions.

    Returns:
        (data_dict_or_None, positions, diagnostics)

    *positions* maps each top-level key name to its (start_line, start_col,
    end_line, end_col) range in the *original* document (0-based).
    The ``yaml_line_offset`` accounts for lines consumed by the opening
    ``---`` and any leading whitespace.
    """
    diags: list[DiagnosticSpan] = []
    positions: dict[str, tuple[int, int, int, int]] = {}

    try:
        root_node: yaml.Node | None = yaml.compose(yaml_text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            err_line = mark.line + yaml_line_offset
            err_col = mark.column
        else:
            err_line = yaml_line_offset
            err_col = 0
        problem = getattr(exc, "problem", str(exc))
        diags.append(
            DiagnosticSpan(
                rule="YAML_ERROR",
                message=f"YAML syntax error: {problem}",
                severity=Severity.ERROR,
                start_line=err_line,
                start_col=err_col,
                end_line=err_line,
                end_col=err_col + 1,
            ),
        )
        return None, positions, diags

    if root_node is None:
        # Empty YAML document
        diags.append(
            DiagnosticSpan(
                rule="YAML_ERROR",
                message="YAML front matter is empty",
                severity=Severity.ERROR,
                start_line=yaml_line_offset,
                start_col=0,
                end_line=yaml_line_offset,
                end_col=0,
            ),
        )
        return None, positions, diags

    if not isinstance(root_node, yaml.MappingNode):
        diags.append(
            DiagnosticSpan(
                rule="YAML_ERROR",
                message="YAML front matter must be a mapping",
                severity=Severity.ERROR,
                start_line=root_node.start_mark.line + yaml_line_offset,
                start_col=root_node.start_mark.column,
                end_line=root_node.end_mark.line + yaml_line_offset,
                end_col=root_node.end_mark.column,
            ),
        )
        return None, positions, diags

    # Build data dict and position map from MappingNode
    data: dict[str, Any] = {}
    for key_node, value_node in root_node.value:
        if isinstance(key_node, yaml.ScalarNode):
            key_str = key_node.value
            positions[key_str] = (
                key_node.start_mark.line + yaml_line_offset,
                key_node.start_mark.column,
                value_node.end_mark.line + yaml_line_offset,
                value_node.end_mark.column,
            )

    # Use safe_load for actual data construction (handles anchors/tags correctly)
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        # compose() succeeded but safe_load failed — unlikely but handle gracefully
        return None, positions, diags

    if not isinstance(data, dict):
        return None, positions, diags

    return data, positions, diags


def _validate_front_matter_tolerant(
    data: dict[str, Any],
    positions: dict[str, tuple[int, int, int, int]],
    yaml_line_offset: int,
) -> tuple[NodeFrontMatter | None, list[DiagnosticSpan]]:
    """Attempt Pydantic validation; decompose errors into DiagnosticSpans."""
    diags: list[DiagnosticSpan] = []
    try:
        fm = NodeFrontMatter(**data)
    except ValidationError as exc:
        for error in exc.errors():
            loc_parts = error.get("loc", ())
            field_name = str(loc_parts[0]) if loc_parts else "unknown"
            msg = error.get("msg", "validation error")
            err_type = error.get("type", "value_error")

            if field_name in positions:
                sl, sc, el, ec = positions[field_name]
            else:
                # Field not present in YAML — point to the front matter block start
                sl = yaml_line_offset
                sc = 0
                el = yaml_line_offset
                ec = 0

            rule = _map_field_to_rule(field_name, err_type)
            diags.append(
                DiagnosticSpan(
                    rule=rule,
                    message=f"{field_name}: {msg}",
                    severity=Severity.ERROR,
                    start_line=sl,
                    start_col=sc,
                    end_line=el,
                    end_col=ec,
                ),
            )
        return None, diags

    return fm, diags


def _map_field_to_rule(field_name: str, err_type: str) -> str:
    """Map a Pydantic field/error to the corresponding KGN validation rule."""
    if field_name == "kgn_version":
        return "V2"
    if field_name in {"id", "type", "title", "status", "project_id", "agent_id"}:
        if "missing" in err_type:
            return "V3"
        if field_name == "id":
            return "V6"
        if field_name == "type":
            return "V4"
        if field_name == "status":
            return "V5"
        return "V3"
    if field_name == "confidence":
        return "V9"
    return "VALIDATE"


def parse_kgn_tolerant(
    text: str,
    *,
    source_path: str | None = None,
) -> PartialParseResult:
    """Fault-tolerant parser — **never** raises exceptions (R24).

    Designed for LSP usage where every keystroke produces a potentially
    broken document.  All errors are reported as :class:`DiagnosticSpan`
    items inside the returned :class:`PartialParseResult`.

    Recovery strategies:
    * ``---`` missing → V1 diagnostic, entire text treated as body.
    * Closing ``---`` missing → attempt YAML parse on text after opening
      delimiter; body is empty.
    * YAML syntax error → ``YAML_ERROR`` diagnostic, ``front_matter=None``.
    * Pydantic validation failure → per-field diagnostics with source
      positions obtained from ``yaml.compose()`` node marks.

    Parameters:
        text: Full .kgn document text (may be incomplete/broken).
        source_path: Optional origin path for the result.

    Returns:
        PartialParseResult (always; never raises).
    """
    result = PartialParseResult(source_path=source_path)

    try:
        return _parse_kgn_tolerant_inner(text, result)
    except Exception as exc:  # noqa: BLE001  — R24 catch-all
        result.diagnostics.append(
            DiagnosticSpan(
                rule="INTERNAL",
                message=f"Unexpected parser error: {type(exc).__name__}: {exc}",
                severity=Severity.ERROR,
                start_line=0,
                start_col=0,
                end_line=0,
                end_col=0,
            ),
        )
        return result


def _parse_kgn_tolerant_inner(
    text: str,
    result: PartialParseResult,
) -> PartialParseResult:
    """Inner implementation for :func:`parse_kgn_tolerant`.

    Separated so the outer function can wrap everything in a
    catch-all ``except Exception`` to guarantee R24.
    """
    if not text or not text.strip():
        result.diagnostics.append(
            DiagnosticSpan(
                rule="V1",
                message="Document is empty",
                severity=Severity.ERROR,
                start_line=0,
                start_col=0,
                end_line=0,
                end_col=0,
            ),
        )
        return result

    # Phase 1: Split front matter / body
    yaml_text, body, split_diags, yaml_line_offset = _split_front_matter_tolerant(text)
    result.body = body
    result.diagnostics.extend(split_diags)

    if yaml_text is None:
        # No YAML to parse — already diagnosed
        return result

    # Phase 2: YAML parse + position extraction
    # yaml_line_offset for compose: the opening '---' occupies line 0,
    # YAML content starts at line 1 (the offset includes the delimiter lines).
    # For compose(), we pass the raw yaml_text and add the offset to marks.
    # The offset to use = number of lines before yaml_text begins.
    # In a well-formed doc:  line 0 = "---", YAML starts at line 1
    # yaml_line_offset from _split_front_matter_tolerant counts lines
    # up to (and including) the closing "---".  The yaml_text itself starts
    # at line 1.  We need the offset to the first YAML line.
    stripped = text.lstrip("\ufeff")
    first_newline = stripped.find("\n")
    yaml_content_start_line = 1 if first_newline != -1 else 0

    data, positions, yaml_diags = _yaml_compose_safe(yaml_text, yaml_content_start_line)
    result.yaml_node_positions = positions
    result.diagnostics.extend(yaml_diags)

    if data is None:
        # YAML parse failed — compute hash from whatever we have
        if yaml_text:
            result.content_hash = _compute_hash(yaml_text, body)
        return result

    # Phase 3: Pydantic validation
    fm, val_diags = _validate_front_matter_tolerant(data, positions, yaml_content_start_line)
    result.front_matter = fm
    result.diagnostics.extend(val_diags)

    # Phase 4: Content hash
    result.content_hash = _compute_hash(yaml_text, body)

    return result
