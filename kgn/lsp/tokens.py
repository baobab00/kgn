"""Semantic Token types, modifiers, and token builder for KGN/KGE files.

Defines the token legend that the LSP client uses to map integer indices
to logical token categories.  The ordering is significant — indices
into ``TOKEN_TYPES`` and ``TOKEN_MODIFIERS`` are transmitted over the wire.

The public entry point is :func:`build_semantic_tokens`, which turns a
``PartialParseResult`` into the integer array expected by ``SemanticTokens``.

Design rules respected
~~~~~~~~~~~~~~~~~~~~~~
R25  Semantic tokens override TextMate cosmetic layer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lsprotocol import types

from kgn.lsp.position import PositionAdapter

if TYPE_CHECKING:
    from kgn.parser.models import PartialParseResult

logger = logging.getLogger(__name__)

# ── Token Types ────────────────────────────────────────────────────────

TOKEN_TYPES: list[str] = [
    "namespace",  # project_id
    "type",  # NodeType ENUM (SPEC, GOAL, ...)
    "enum",  # NodeStatus, EdgeType
    "property",  # YAML key (kgn_version, id, type, ...)
    "string",  # YAML string value
    "number",  # confidence numeric value
    "variable",  # new:slug pattern
    "keyword",  # --- delimiter
]

# ── Token Modifiers ────────────────────────────────────────────────────

TOKEN_MODIFIERS: list[str] = [
    "declaration",  # id field (this node's ID declaration)
    "deprecated",  # status: DEPRECATED
    "readonly",  # kgn_version (immutable)
]

# ── Legend ──────────────────────────────────────────────────────────────

TOKEN_LEGEND = types.SemanticTokensLegend(
    token_types=TOKEN_TYPES,
    token_modifiers=TOKEN_MODIFIERS,
)

# ── Index helpers ──────────────────────────────────────────────────────

_TYPE_INDEX: dict[str, int] = {t: i for i, t in enumerate(TOKEN_TYPES)}
_MOD_INDEX: dict[str, int] = {m: i for i, m in enumerate(TOKEN_MODIFIERS)}

# YAML key → semantic token type and modifiers for the **key** itself
_KEY_TOKEN_MAP: dict[str, tuple[str, list[str]]] = {
    "kgn_version": ("property", ["readonly"]),
    "id": ("property", ["declaration"]),
    "type": ("property", []),
    "title": ("property", []),
    "status": ("property", []),
    "project_id": ("property", []),
    "agent_id": ("property", []),
    "created_at": ("property", []),
    "supersedes": ("property", []),
    "tags": ("property", []),
    "confidence": ("property", []),
    "edges": ("property", []),
}

# Value classification by key name
_VALUE_KEY_MAP: dict[str, tuple[str, list[str]]] = {
    "kgn_version": ("string", ["readonly"]),
    "id": ("variable", ["declaration"]),
    "type": ("type", []),
    "title": ("string", []),
    "status": ("enum", []),
    "project_id": ("namespace", []),
    "agent_id": ("string", []),
    "created_at": ("string", []),
    "supersedes": ("string", []),
    "confidence": ("number", []),
}


def type_index(name: str) -> int:
    """Return the integer index of a token type."""
    return _TYPE_INDEX[name]


def modifier_bitmask(names: list[str]) -> int:
    """Return a bitmask for the given token modifier names."""
    mask = 0
    for name in names:
        idx = _MOD_INDEX.get(name)
        if idx is not None:
            mask |= 1 << idx
    return mask


def encode_token(
    *,
    delta_line: int,
    delta_start: int,
    length: int,
    token_type: str,
    token_modifiers: list[str] | None = None,
) -> list[int]:
    """Encode a single semantic token as a 5-integer tuple.

    Parameters:
        delta_line: Line delta from previous token.
        delta_start: Character delta from previous token's start
            (or from line start if delta_line > 0).
        length: Token length in characters.
        token_type: Token type name (must be in TOKEN_TYPES).
        token_modifiers: Optional list of modifier names.

    Returns:
        5-element list: [deltaLine, deltaStart, length, tokenType, tokenModifiers]
    """
    return [
        delta_line,
        delta_start,
        length,
        _TYPE_INDEX[token_type],
        modifier_bitmask(token_modifiers or []),
    ]


# ── Semantic Token Builder ─────────────────────────────────────────────

# Internal raw token: (line, utf16_col, utf16_length, type_index, modifier_mask)
_RawToken = tuple[int, int, int, int, int]


def _find_value_span(
    line_text: str,
    key_start_col: int,
    key_len: int,
) -> tuple[int, int] | None:
    """Find the (start_col, length) of a YAML scalar value on the same line.

    Returns ``None`` when no value is found on the line (e.g. block-style
    lists or multi-line strings).
    """
    pos = key_start_col + key_len
    n = len(line_text)
    # skip colon
    if pos < n and line_text[pos] == ":":
        pos += 1
    # skip whitespace
    while pos < n and line_text[pos] == " ":
        pos += 1
    if pos >= n:
        return None
    val_text = line_text[pos:].rstrip()
    if not val_text:
        return None
    return pos, len(val_text)


def _value_token_info(
    key: str,
    value_text: str,
) -> tuple[str, list[str]] | None:
    """Determine the token type and modifiers for a YAML value.

    Returns ``None`` when the value should not be tokenised (e.g. list
    markers, empty strings).
    """
    if not value_text or value_text.startswith("[") or value_text.startswith("-"):
        return None

    base = _VALUE_KEY_MAP.get(key)
    if base is None:
        return "string", []

    vtype, vmods = base
    # Add 'deprecated' modifier for status: DEPRECATED
    if key == "status" and value_text == "DEPRECATED":
        vmods = [*vmods, "deprecated"]
    return vtype, vmods


def build_semantic_tokens(text: str, result: PartialParseResult) -> list[int]:
    """Build the LSP ``SemanticTokens.data`` integer array.

    Scans for ``---`` delimiters and YAML key-value pairs using the
    position data from ``result.yaml_node_positions``.  All column
    values are converted to UTF-16 code units as required by LSP.

    Parameters:
        text: Full document text.
        result: Parser output from ``parse_kgn_tolerant``.

    Returns:
        Flat list of integers (groups of 5) suitable for
        ``types.SemanticTokens(data=...)``.
    """
    lines = text.split("\n")
    raw: list[_RawToken] = []

    # ── 1. ``---`` delimiters → keyword (YAML region only) ──────────
    # Only the opening and closing ``---`` are YAML delimiters.
    # Body ``---`` is a Markdown horizontal rule — do NOT tokenise it.
    opening_line: int | None = None
    closing_line: int | None = None
    for i, line_text in enumerate(lines):
        if line_text.strip() == "---":
            if opening_line is None:
                opening_line = i
            else:
                closing_line = i
                break

    for del_line in (opening_line, closing_line):
        if del_line is not None:
            lt = lines[del_line]
            col = lt.index("-")
            utf16_col = PositionAdapter.utf8_col_to_utf16(lt, col)
            raw.append(
                (
                    del_line,
                    utf16_col,
                    3,  # "---" is always 3 UTF-16 code units
                    _TYPE_INDEX["keyword"],
                    0,
                )
            )

    # ── 2. YAML key → value tokens ──────────────────────────────────
    for key, (sl, sc, _el, _ec) in result.yaml_node_positions.items():
        if sl >= len(lines):
            continue
        line_text = lines[sl]

        # --- Key token ---
        key_type, key_mods = _KEY_TOKEN_MAP.get(key, ("property", []))
        utf16_sc = PositionAdapter.utf8_col_to_utf16(line_text, sc)
        utf16_key_end = PositionAdapter.utf8_col_to_utf16(line_text, sc + len(key))
        raw.append(
            (
                sl,
                utf16_sc,
                utf16_key_end - utf16_sc,
                _TYPE_INDEX[key_type],
                modifier_bitmask(key_mods),
            )
        )

        # --- Value token (same-line scalars only) ---
        span = _find_value_span(line_text, sc, len(key))
        if span is None:
            continue
        val_start, val_len = span
        val_text = line_text[val_start : val_start + val_len]
        info = _value_token_info(key, val_text)
        if info is None:
            continue
        vtype, vmods = info
        utf16_vs = PositionAdapter.utf8_col_to_utf16(line_text, val_start)
        utf16_ve = PositionAdapter.utf8_col_to_utf16(line_text, val_start + val_len)
        raw.append(
            (
                sl,
                utf16_vs,
                utf16_ve - utf16_vs,
                _TYPE_INDEX[vtype],
                modifier_bitmask(vmods),
            )
        )

    # ── 3. Sort by position and compute deltas ──────────────────────
    raw.sort()
    data: list[int] = []
    prev_line = 0
    prev_col = 0
    for line, col, length, tidx, mmask in raw:
        dl = line - prev_line
        dc = col if dl > 0 else col - prev_col
        data.extend([dl, dc, length, tidx, mmask])
        prev_line = line
        prev_col = col

    return data
