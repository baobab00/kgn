"""Context-aware completion provider for KGN/KGE files.

Analyses cursor position in YAML front matter or Markdown body to
provide relevant completions:

* **YAML key position** → 12 front matter keys (property)
* **``type:``** → 10 ``NodeType`` enum values  (or 7 ``EdgeType`` for .kge)
* **``status:``** → 4 ``NodeStatus`` values
* **``confidence:``** → 0.0 – 1.0 range hint
* **``## `` in body** → 5 recommended section headings
"""

from __future__ import annotations

from lsprotocol import types

from kgn.models.enums import EdgeType, NodeStatus, NodeType

# ── Constants ──────────────────────────────────────────────────────────

# All KGN front matter keys in canonical order
_KGN_FRONT_MATTER_KEYS: list[str] = [
    "kgn_version",
    "id",
    "type",
    "title",
    "status",
    "project_id",
    "agent_id",
    "created_at",
    "supersedes",
    "tags",
    "confidence",
]

# KGE front matter keys
_KGE_FRONT_MATTER_KEYS: list[str] = [
    "kgn_version",
    "project_id",
    "agent_id",
    "created_at",
    "edges",
]

# Recommended body sections
_BODY_SECTIONS: list[str] = [
    "Context",
    "Decision",
    "Rationale",
    "Consequences",
    "References",
]

# NodeType descriptions (for detail text)
_NODE_TYPE_DOCS: dict[str, str] = {
    "GOAL": "High-level project goal",
    "ARCH": "Architecture component",
    "SPEC": "Specification",
    "LOGIC": "Implementation logic",
    "DECISION": "Architecture decision record",
    "ISSUE": "Open issue / problem",
    "TASK": "Work item",
    "CONSTRAINT": "External constraint",
    "ASSUMPTION": "Working assumption",
    "SUMMARY": "Aggregated summary",
}

_NODE_STATUS_DOCS: dict[str, str] = {
    "ACTIVE": "Currently in effect",
    "DEPRECATED": "No longer recommended",
    "SUPERSEDED": "Replaced by another node",
    "ARCHIVED": "Retained for historical reference",
}

_EDGE_TYPE_DOCS: dict[str, str] = {
    "DEPENDS_ON": "Source depends on target",
    "IMPLEMENTS": "Source implements target",
    "RESOLVES": "Source resolves target issue",
    "SUPERSEDES": "Source supersedes target",
    "DERIVED_FROM": "Source is derived from target",
    "CONTRADICTS": "Source contradicts target",
    "CONSTRAINED_BY": "Source is constrained by target",
}


# ── Cursor context analysis ───────────────────────────────────────────


def _find_closing_delimiter(lines: list[str]) -> int | None:
    """Return the 0-based line number of the closing ``---``, or None.

    Assumes line 0 is the opening ``---``.
    """
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i
    return None


def _in_yaml_region(line: int, closing_line: int | None) -> bool:
    """Return True if *line* is inside the YAML front matter block.

    * Line 0 (opening ``---``) is NOT inside YAML content.
    * Lines between 1 and closing_line-1 are inside.
    * closing_line itself is NOT inside.
    """
    if closing_line is None:
        # No closing delimiter found — treat everything after line 0
        # as YAML (best-effort for broken documents).
        return line >= 1
    return 1 <= line < closing_line


def _get_yaml_key_at_cursor(
    line_text: str,
    character: int,
) -> str | None:
    """Return the YAML key if the cursor is after ``key: ``, else None.

    Examples:
        ``"type: S|"`` with cursor at 7 → ``"type"``
        ``"  id: |"`` with cursor at 6 → ``"id"``
        ``"|type"`` with cursor at 0 → ``None`` (cursor before key)
    """
    colon_pos = line_text.find(":")
    if colon_pos == -1:
        return None
    # Cursor must be AFTER the colon
    if character <= colon_pos:
        return None
    key = line_text[:colon_pos].strip()
    if key:
        return key
    return None


def _is_yaml_key_position(line_text: str, character: int) -> bool:
    """Return True if the cursor is at a YAML key position (start of line).

    True when the line is empty/whitespace-only (user is starting a new key),
    or there is no colon yet (user is typing a key name).
    """
    # Empty line or no colon → key position
    if ":" not in line_text:
        return True
    # If cursor is before the colon → typing a key
    colon_pos = line_text.find(":")
    return character <= colon_pos


# ── Completion builders ───────────────────────────────────────────────


def _node_type_completions() -> list[types.CompletionItem]:
    """10 NodeType enum completions."""
    return [
        types.CompletionItem(
            label=t.value,
            kind=types.CompletionItemKind.EnumMember,
            detail=_NODE_TYPE_DOCS.get(t.value, ""),
            sort_text=f"0{i:02d}",
        )
        for i, t in enumerate(NodeType)
    ]


def _node_status_completions() -> list[types.CompletionItem]:
    """4 NodeStatus enum completions."""
    return [
        types.CompletionItem(
            label=s.value,
            kind=types.CompletionItemKind.EnumMember,
            detail=_NODE_STATUS_DOCS.get(s.value, ""),
            sort_text=f"0{i:02d}",
        )
        for i, s in enumerate(NodeStatus)
    ]


def _edge_type_completions() -> list[types.CompletionItem]:
    """7 EdgeType enum completions."""
    return [
        types.CompletionItem(
            label=e.value,
            kind=types.CompletionItemKind.EnumMember,
            detail=_EDGE_TYPE_DOCS.get(e.value, ""),
            sort_text=f"0{i:02d}",
        )
        for i, e in enumerate(EdgeType)
    ]


def _confidence_completions() -> list[types.CompletionItem]:
    """Confidence range hint completions."""
    values = ["0.0", "0.25", "0.5", "0.75", "1.0"]
    return [
        types.CompletionItem(
            label=v,
            kind=types.CompletionItemKind.Value,
            detail="Confidence score (0.0 ~ 1.0)",
            sort_text=f"0{i:02d}",
        )
        for i, v in enumerate(values)
    ]


def _yaml_key_completions(is_kge: bool = False) -> list[types.CompletionItem]:
    """Front matter key completions with trailing colon + space."""
    keys = _KGE_FRONT_MATTER_KEYS if is_kge else _KGN_FRONT_MATTER_KEYS
    return [
        types.CompletionItem(
            label=k,
            kind=types.CompletionItemKind.Property,
            insert_text=f"{k}: ",
            sort_text=f"0{i:02d}",
        )
        for i, k in enumerate(keys)
    ]


def _body_section_completions() -> list[types.CompletionItem]:
    """Recommended Markdown body section headings."""
    return [
        types.CompletionItem(
            label=s,
            kind=types.CompletionItemKind.Text,
            insert_text=f"## {s}\n\n",
            detail="Recommended section",
            sort_text=f"0{i:02d}",
        )
        for i, s in enumerate(_BODY_SECTIONS)
    ]


# ── Public API ─────────────────────────────────────────────────────────


def get_completions(
    text: str,
    line: int,
    character: int,
    *,
    is_kge: bool = False,
) -> list[types.CompletionItem]:
    """Return completion items based on cursor position within *text*.

    Parameters:
        text: Full document text.
        line: 0-based cursor line.
        character: 0-based cursor column (UTF-16 code units).
        is_kge: Whether the file is a ``.kge`` edge file.

    Returns:
        List of ``CompletionItem`` objects.  May be empty.
    """
    lines = text.split("\n")
    if line >= len(lines):
        return []

    line_text = lines[line]

    # ── Determine front matter region ────────────────────────────────
    closing_line = _find_closing_delimiter(lines)
    in_yaml = _in_yaml_region(line, closing_line)

    if in_yaml:
        # Check if cursor is at a value position (after "key: ")
        yaml_key = _get_yaml_key_at_cursor(line_text, character)
        if yaml_key is not None:
            if yaml_key == "type":
                if is_kge:
                    return _edge_type_completions()
                return _node_type_completions()
            if yaml_key == "status" and not is_kge:
                return _node_status_completions()
            if yaml_key == "confidence":
                return _confidence_completions()
            # No specific completions for other value positions
            return []

        # Check if cursor is at a key position
        if _is_yaml_key_position(line_text, character):
            return _yaml_key_completions(is_kge=is_kge)

        return []

    # ── Markdown body region ─────────────────────────────────────────
    stripped = line_text.lstrip()
    if stripped.startswith("## ") or stripped == "##":
        return _body_section_completions()

    return []
