"""Hover information and Go to Definition for KGN/KGE files.

Extracts the word under the cursor, identifies its semantic kind
(UUID, ``new:slug``, NodeType ENUM, NodeStatus ENUM, EdgeType ENUM),
and produces a Markdown hover card or a definition location.

Design rules
~~~~~~~~~~~~
R22  DB-free operation — ``WorkspaceIndexer`` is the primary data source.
R23  Any DB fallback runs via ``asyncio.to_thread()``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kgn.lsp.indexer import NodeMeta, WorkspaceIndexer

# ── Regex patterns ─────────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

_NEW_SLUG_RE = re.compile(r"new:([a-zA-Z0-9_-]+)")

# ── ENUM description dictionaries ─────────────────────────────────────

NODE_TYPE_DESCRIPTIONS: dict[str, str] = {
    "GOAL": "Architecture-level objective or milestone",
    "ARCH": "Architecture component or design block",
    "SPEC": "Functional/non-functional requirement specification",
    "LOGIC": "Implementation logic or algorithm description",
    "DECISION": "Architecture decision record (ADR)",
    "ISSUE": "Open issue or problem statement",
    "TASK": "Work item for agent execution",
    "CONSTRAINT": "External constraint or limitation",
    "ASSUMPTION": "Working assumption (may be revisited)",
    "SUMMARY": "Aggregated summary of related nodes",
}

NODE_STATUS_DESCRIPTIONS: dict[str, str] = {
    "ACTIVE": "Currently in effect and maintained",
    "DEPRECATED": "No longer recommended; use superseding node",
    "SUPERSEDED": "Replaced by another node (see `supersedes` field)",
    "ARCHIVED": "Retained for historical reference only",
}

EDGE_TYPE_DESCRIPTIONS: dict[str, str] = {
    "DEPENDS_ON": "Source node depends on target node",
    "IMPLEMENTS": "Source node implements target specification",
    "RESOLVES": "Source node resolves target issue",
    "SUPERSEDES": "Source node supersedes (replaces) target",
    "DERIVED_FROM": "Source node is derived from target",
    "CONTRADICTS": "Source node contradicts target",
    "CONSTRAINED_BY": "Source node is constrained by target",
}


# ── Word extraction ────────────────────────────────────────────────────


def _get_line_text(text: str, line: int) -> str | None:
    """Return the text of *line* (0-based), or ``None`` if out of range."""
    lines = text.split("\n")
    if 0 <= line < len(lines):
        return lines[line]
    return None


def _word_at_position(
    line_text: str,
    character: int,
) -> str:
    """Extract the 'word' around *character* in *line_text*.

    A word is a maximal run of ``[a-zA-Z0-9_:.-]`` characters that
    includes the position *character*.  This covers UUIDs, ``new:slug``
    patterns, and ENUM names.
    """
    if character > len(line_text):
        character = len(line_text)
    # Expand left
    left = character
    while left > 0 and _is_word_char(line_text[left - 1]):
        left -= 1
    # Expand right
    right = character
    while right < len(line_text) and _is_word_char(line_text[right]):
        right += 1
    return line_text[left:right]


def _is_word_char(ch: str) -> bool:
    """Return True for characters that form a 'word' in KGN context."""
    return ch.isalnum() or ch in ("_", ":", "-", ".")


# ── YAML key context ──────────────────────────────────────────────────


def _yaml_key_for_line(line_text: str) -> str | None:
    """Return the YAML key on *line_text*, or ``None``.

    Example: ``"  type: SPEC"`` → ``"type"``
    """
    colon_pos = line_text.find(":")
    if colon_pos == -1:
        return None
    key = line_text[:colon_pos].strip()
    return key if key else None


# ── Hover content builders ────────────────────────────────────────────


def format_node_hover(meta: NodeMeta) -> str:
    """Build a Markdown hover card for a resolved node.

    Parameters:
        meta: Node metadata from the workspace indexer.

    Returns:
        Markdown string suitable for ``Hover.contents``.
    """
    lines: list[str] = [
        f"**[{meta.type.value}]** {meta.title}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Status | `{meta.status.value}` |",
    ]
    if meta.confidence is not None:
        lines.append(f"| Confidence | `{meta.confidence}` |")
    lines.append(f"| File | `{meta.path.name}` |")
    return "\n".join(lines)


def _format_enum_hover(enum_name: str, description: str) -> str:
    """Build a Markdown hover card for an ENUM value."""
    return f"**`{enum_name}`** — {description}"


def _format_slug_hover(slug: str, meta: NodeMeta) -> str:
    """Build a Markdown hover card for a ``new:slug`` reference."""
    return f"**new:{slug}** → `{meta.path.name}`\n\n**[{meta.type.value}]** {meta.title}"


# ── Public API ─────────────────────────────────────────────────────────


def get_hover(
    text: str,
    line: int,
    character: int,
    indexer: WorkspaceIndexer,
) -> str | None:
    """Return Markdown hover content for the word at *(line, character)*.

    Resolution order:
    1. UUID → ``indexer.resolve_uuid()`` → node hover card
    2. ``new:slug`` → ``indexer.resolve_slug()`` → slug hover card
    3. ENUM value in YAML context → description

    Parameters:
        text: Full document text.
        line: 0-based cursor line.
        character: 0-based cursor column.
        indexer: Workspace indexer instance.

    Returns:
        Markdown string, or ``None`` if nothing to show.
    """
    line_text = _get_line_text(text, line)
    if line_text is None:
        return None

    word = _word_at_position(line_text, character)
    if not word:
        return None

    # ── UUID hover ───────────────────────────────────────────────────
    uuid_match = _UUID_RE.fullmatch(word)
    if uuid_match:
        path = indexer.resolve_uuid(word)
        if path is not None:
            meta = indexer.get_meta(path)
            if meta is not None:
                return format_node_hover(meta)
        return f"**UUID** `{word}` — *not found in workspace*"

    # ── new:slug hover ───────────────────────────────────────────────
    slug_match = _NEW_SLUG_RE.fullmatch(word)
    if slug_match:
        slug = slug_match.group(1)
        path = indexer.resolve_slug(slug)
        if path is not None:
            meta = indexer.get_meta(path)
            if meta is not None:
                return _format_slug_hover(slug, meta)
        return f"**new:{slug}** — *not found in workspace*"

    # ── ENUM hover (context-sensitive) ───────────────────────────────
    yaml_key = _yaml_key_for_line(line_text)

    if yaml_key == "type":
        # Check NodeType
        desc = NODE_TYPE_DESCRIPTIONS.get(word)
        if desc:
            return _format_enum_hover(word, desc)
        # Check EdgeType (for .kge files)
        desc = EDGE_TYPE_DESCRIPTIONS.get(word)
        if desc:
            return _format_enum_hover(word, desc)

    if yaml_key == "status":
        desc = NODE_STATUS_DESCRIPTIONS.get(word)
        if desc:
            return _format_enum_hover(word, desc)

    # ── Bare UUID in values (e.g. supersedes: <uuid>) ────────────────
    # Check if word is part of a UUID on this line
    for m in _UUID_RE.finditer(line_text):
        if m.start() <= character <= m.end():
            uuid_str = m.group()
            path = indexer.resolve_uuid(uuid_str)
            if path is not None:
                meta = indexer.get_meta(path)
                if meta is not None:
                    return format_node_hover(meta)
            return f"**UUID** `{uuid_str}` — *not found in workspace*"

    return None


def get_definition(
    text: str,
    line: int,
    character: int,
    indexer: WorkspaceIndexer,
) -> Path | None:
    """Return the file path for the definition at *(line, character)*.

    Resolves UUIDs and ``new:slug`` references to file paths via the
    workspace indexer.

    Parameters:
        text: Full document text.
        line: 0-based cursor line.
        character: 0-based cursor column.
        indexer: Workspace indexer instance.

    Returns:
        File path, or ``None`` if no definition found.
    """
    line_text = _get_line_text(text, line)
    if line_text is None:
        return None

    word = _word_at_position(line_text, character)
    if not word:
        return None

    # ── UUID → file ──────────────────────────────────────────────────
    uuid_match = _UUID_RE.fullmatch(word)
    if uuid_match:
        return indexer.resolve_uuid(word)

    # ── new:slug → file ──────────────────────────────────────────────
    slug_match = _NEW_SLUG_RE.fullmatch(word)
    if slug_match:
        slug = slug_match.group(1)
        return indexer.resolve_slug(slug)

    # ── Check for UUID substring on the line ─────────────────────────
    for m in _UUID_RE.finditer(line_text):
        if m.start() <= character <= m.end():
            return indexer.resolve_uuid(m.group())

    return None
