"""Validation rules V1–V6, V9, V10 for .kgn files.

V7 (supersedes node DB existence) and V8 (content_hash duplication)
are intentionally excluded — they require DB access and belong in the
repository layer (Step 5).
"""

from __future__ import annotations

import re

from kgn.parser.models import ParsedNode, ValidationResult

# Supported format versions
_SUPPORTED_VERSIONS: frozenset[str] = frozenset({"0.1"})

# UUID v4 pattern (case-insensitive)
_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# ``new:`` prefix pattern
_NEW_ID_RE = re.compile(r"^new:.+$")

# Recommended Markdown sections (heading level 2)
_RECOMMENDED_SECTIONS: list[str] = [
    "Context",
    "Content",
]


# ── Individual rule functions ──────────────────────────────────────────


def _check_v1_front_matter(text: str, result: ValidationResult) -> None:
    """V1: File must start with ``---`` (YAML front matter present)."""
    stripped = text.lstrip("\ufeff").lstrip()
    if not stripped.startswith("---"):
        result.add_error("V1: File does not start with '---' — no YAML front matter")


def _check_v2_version(parsed: ParsedNode, result: ValidationResult) -> None:
    """V2: ``kgn_version`` must be a supported value."""
    version = parsed.front_matter.kgn_version
    if version not in _SUPPORTED_VERSIONS:
        result.add_error(
            f"V2: Unsupported kgn_version '{version}' "
            f"(supported: {', '.join(sorted(_SUPPORTED_VERSIONS))})"
        )


def _check_v3_required_fields(parsed: ParsedNode, result: ValidationResult) -> None:
    """V3: All 7 required fields must be present and non-empty.

    Pydantic already enforces presence at model-creation time, so if we
    have a ``ParsedNode`` this rule always passes.  Kept for explicit
    completeness — callers running ``validate_kgn_text`` will fail at
    the parse stage before reaching this check.
    """
    required = ["id", "type", "title", "status", "project_id", "agent_id"]
    fm = parsed.front_matter
    for field_name in required:
        value = getattr(fm, field_name, None)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            result.add_error(f"V3: Required field '{field_name}' is missing or empty")


def _check_v4_type_enum(parsed: ParsedNode, result: ValidationResult) -> None:  # noqa: ARG001
    """V4: ``type`` must be a valid NodeType.

    Already enforced by Pydantic StrEnum — kept for parity.
    """


def _check_v5_status_enum(parsed: ParsedNode, result: ValidationResult) -> None:  # noqa: ARG001
    """V5: ``status`` must be a valid NodeStatus.

    Already enforced by Pydantic StrEnum — kept for parity.
    """


def _check_v6_id_format(parsed: ParsedNode, result: ValidationResult) -> None:
    """V6: ``id`` must be a valid UUID v4 **or** start with ``new:``."""
    node_id = parsed.front_matter.id
    if _UUID_V4_RE.match(node_id):
        return
    if _NEW_ID_RE.match(node_id):
        return
    result.add_error(f"V6: id '{node_id}' is neither a valid UUID v4 nor a 'new:' temporary id")


def _check_v9_confidence(parsed: ParsedNode, result: ValidationResult) -> None:
    """V9: ``confidence`` must be in 0.0–1.0 range (if present).

    Already enforced by the Pydantic field_validator — kept for parity.
    """
    conf = parsed.front_matter.confidence
    if conf is not None and (conf < 0.0 or conf > 1.0):
        result.add_error(f"V9: confidence {conf} is out of range (must be 0.0–1.0)")


def _check_v10_sections(parsed: ParsedNode, result: ValidationResult) -> None:
    """V10: Markdown body should contain recommended ``## Section`` headings."""
    body = parsed.body
    for section in _RECOMMENDED_SECTIONS:
        pattern = rf"^##\s+{re.escape(section)}\b"
        if not re.search(pattern, body, re.MULTILINE):
            result.add_warning(f"V10: Recommended section '## {section}' not found in body")


# ── Public API ─────────────────────────────────────────────────────────


def validate_kgn(parsed: ParsedNode, *, raw_text: str | None = None) -> ValidationResult:
    """Run V1–V6, V9, V10 on a parsed node.

    Parameters:
        parsed: A successfully parsed node.
        raw_text: Original file text (needed for V1 check).  If ``None``,
                  V1 is skipped (it was already verified during parsing).

    Returns:
        ValidationResult with accumulated errors / warnings.
    """
    result = ValidationResult()

    if raw_text is not None:
        _check_v1_front_matter(raw_text, result)

    _check_v2_version(parsed, result)
    _check_v3_required_fields(parsed, result)
    _check_v4_type_enum(parsed, result)
    _check_v5_status_enum(parsed, result)
    _check_v6_id_format(parsed, result)
    _check_v9_confidence(parsed, result)
    _check_v10_sections(parsed, result)

    return result


def validate_kgn_text(text: str) -> ValidationResult:
    """Convenience: parse *and* validate raw ``.kgn`` text in one call.

    This runs the parser first, converting any ``KgnParseError`` into a
    validation error, then applies the full rule set including V1.
    """
    from kgn.parser.kgn_parser import KgnParseError, parse_kgn_text

    result = ValidationResult()

    # V1 is checked structurally during parsing; we also run it explicitly
    _check_v1_front_matter(text, result)
    if not result.is_valid:
        return result

    try:
        parsed = parse_kgn_text(text)
    except KgnParseError as exc:
        result.add_error(str(exc))
        return result

    # Run remaining rules
    _check_v2_version(parsed, result)
    _check_v3_required_fields(parsed, result)
    _check_v6_id_format(parsed, result)
    _check_v9_confidence(parsed, result)
    _check_v10_sections(parsed, result)

    return result
