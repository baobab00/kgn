"""Code Lens and Find References for KGN/KGE files.

Provides inline code lenses showing reference counts, supersedes
relationships, and edge summaries.  Also implements the
``textDocument/references`` logic to locate all files that reference
a given node ID.

Design rules
~~~~~~~~~~~~
R22  DB-free operation — ``WorkspaceIndexer`` is the primary data source.
R24  Never throw — all public functions are defensive.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kgn.lsp.indexer import WorkspaceIndexer


# ── Regex patterns ─────────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

_NEW_SLUG_RE = re.compile(r"new:([a-zA-Z0-9_-]+)")


# ── Data models (simple dicts for JSON-friendliness) ──────────────────


class LensInfo:
    """Lightweight lens descriptor produced by the logic layer.

    Attributes:
        line: 0-based line number where the lens should appear.
        title: Display label (e.g. ``"3 references"``).
        command_id: VS Code command identifier.
        command_args: Arguments passed to the command.
    """

    __slots__ = ("line", "title", "command_id", "command_args")

    def __init__(
        self,
        line: int,
        title: str,
        command_id: str,
        command_args: list[object] | None = None,
    ) -> None:
        self.line = line
        self.title = title
        self.command_id = command_id
        self.command_args = command_args or []


class ReferenceLocation:
    """A location where a node ID is referenced.

    Attributes:
        path: File system path.
        line: 0-based line number.
        start_col: 0-based start column.
        end_col: 0-based end column (exclusive).
    """

    __slots__ = ("path", "line", "start_col", "end_col")

    def __init__(
        self,
        path: Path,
        line: int,
        start_col: int,
        end_col: int,
    ) -> None:
        self.path = path
        self.line = line
        self.start_col = start_col
        self.end_col = end_col


# ── Code Lens builders ────────────────────────────────────────────────


def build_kgn_lenses(
    text: str,
    file_path: Path,
    indexer: WorkspaceIndexer,
) -> list[LensInfo]:
    """Build code lenses for a ``.kgn`` file.

    Lenses produced:
    * ``id:`` line — reference count with click to show references.
    * ``supersedes:`` line — superseded node title + status.

    Parameters:
        text: Full document text.
        file_path: Path of the current document.
        indexer: Workspace indexer instance.

    Returns:
        List of ``LensInfo`` descriptors.
    """
    lenses: list[LensInfo] = []
    lines = text.split("\n")
    in_front_matter = False
    node_id: str | None = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Track front-matter boundaries
        if stripped == "---":
            in_front_matter = not in_front_matter
            continue

        if not in_front_matter:
            continue

        # ── id: line ──────────────────────────────────────────────
        if stripped.startswith("id:"):
            value = stripped[3:].strip().strip("'\"")
            # UUID id
            uuid_match = _UUID_RE.fullmatch(value)
            if uuid_match:
                node_id = value
                refs = indexer.get_references(node_id)
                count = len(refs)
                label = f"{count} reference{'s' if count != 1 else ''}"
                lenses.append(
                    LensInfo(
                        line=i,
                        title=label,
                        command_id="editor.action.showReferences",
                        command_args=[str(file_path), i, 0],
                    ),
                )
            # new:slug id
            slug_match = _NEW_SLUG_RE.fullmatch(value)
            if slug_match:
                slug = slug_match.group(1)
                # For slugs, we look up references by slug value
                refs = indexer.get_references(f"new:{slug}")
                count = len(refs)
                label = f"{count} reference{'s' if count != 1 else ''}"
                lenses.append(
                    LensInfo(
                        line=i,
                        title=label,
                        command_id="editor.action.showReferences",
                        command_args=[str(file_path), i, 0],
                    ),
                )

        # ── supersedes: line ──────────────────────────────────────
        if stripped.startswith("supersedes:"):
            value = stripped[len("supersedes:") :].strip().strip("'\"")
            uuid_match = _UUID_RE.fullmatch(value)
            if uuid_match:
                path = indexer.resolve_uuid(value)
                if path is not None:
                    meta = indexer.get_meta(path)
                    if meta is not None:
                        label = f"Supersedes: {meta.title} ({meta.status.name})"
                        lenses.append(
                            LensInfo(
                                line=i,
                                title=label,
                                command_id="vscode.open",
                                command_args=[str(path)],
                            ),
                        )
                    else:
                        lenses.append(
                            LensInfo(
                                line=i,
                                title=f"Supersedes: {value[:8]}…",
                                command_id="vscode.open",
                                command_args=[str(path)],
                            ),
                        )
                else:
                    lenses.append(
                        LensInfo(
                            line=i,
                            title=f"Supersedes: {value[:8]}… (not found)",
                            command_id="",
                        ),
                    )

    return lenses


def build_kge_lenses(
    text: str,
    file_path: Path,
    indexer: WorkspaceIndexer,
) -> list[LensInfo]:
    """Build code lenses for a ``.kge`` file.

    Each edge entry line (``- from:``) gets a lens showing
    ``from-slug → to-slug (TYPE)``.

    Parameters:
        text: Full document text.
        file_path: Path of the current document.
        indexer: Workspace indexer instance.

    Returns:
        List of ``LensInfo`` descriptors.
    """
    lenses: list[LensInfo] = []
    lines = text.split("\n")
    in_front_matter = False
    in_edges = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped == "---":
            in_front_matter = not in_front_matter
            if not in_front_matter:
                break  # End of front matter
            continue

        if not in_front_matter:
            continue

        # Track edges: section
        if stripped == "edges:":
            in_edges = True
            continue

        if not in_edges:
            continue

        # Each edge entry starts with "- from:"
        if stripped.startswith("- from:"):
            from_value = stripped[len("- from:") :].strip().strip("'\"")
            # Scan ahead for to: and type: in the same edge block
            to_value = ""
            edge_type = ""
            for j in range(i + 1, min(i + 5, len(lines))):
                sub = lines[j].strip()
                if sub.startswith("to:"):
                    to_value = sub[3:].strip().strip("'\"")
                elif sub.startswith("type:"):
                    edge_type = sub[5:].strip().strip("'\"")
                elif sub.startswith("- from:"):
                    break  # Next edge entry

            from_label = _resolve_label(from_value, indexer)
            to_label = _resolve_label(to_value, indexer)
            label = f"{from_label} → {to_label} ({edge_type})"

            lenses.append(
                LensInfo(
                    line=i,
                    title=label,
                    command_id="",
                ),
            )

    return lenses


def _resolve_label(value: str, indexer: WorkspaceIndexer) -> str:
    """Resolve a node reference to a human-readable label.

    Returns the node's slug (or title if available), falling back to
    a truncated ID if not found.
    """
    if not value:
        return "?"

    # new:slug
    slug_match = _NEW_SLUG_RE.fullmatch(value)
    if slug_match:
        return value  # "new:auth-spec"

    # UUID
    uuid_match = _UUID_RE.fullmatch(value)
    if uuid_match:
        path = indexer.resolve_uuid(value)
        if path is not None:
            meta = indexer.get_meta(path)
            if meta is not None:
                return meta.slug
        return value[:8] + "…"

    return value


# ── Find References ───────────────────────────────────────────────────


def find_references(
    text: str,
    line: int,
    character: int,
    indexer: WorkspaceIndexer,
) -> list[ReferenceLocation]:
    """Find all locations that reference the node ID at (line, character).

    Extracts the word under the cursor, identifies it as a UUID or
    ``new:slug``, then scans all referencing files for exact line/column
    positions.

    Parameters:
        text: Full document text.
        line: 0-based cursor line.
        character: 0-based cursor column.
        indexer: Workspace indexer instance.

    Returns:
        List of ``ReferenceLocation`` objects, or empty list.
    """
    from kgn.lsp.hover import _get_line_text, _word_at_position

    line_text = _get_line_text(text, line)
    if line_text is None:
        return []

    word = _word_at_position(line_text, character)
    if not word:
        return []

    # Determine the node ID to search for
    node_id: str | None = None

    # Direct UUID
    if _UUID_RE.fullmatch(word):
        node_id = word

    # new:slug
    slug_match = _NEW_SLUG_RE.fullmatch(word)
    if slug_match and node_id is None:
        node_id = word  # The reverse refs key is "new:slug"

    # UUID substring on line (fallback)
    if node_id is None:
        for m in _UUID_RE.finditer(line_text):
            if m.start() <= character <= m.end():
                node_id = m.group()
                break

    if node_id is None:
        return []

    # Get all .kge files that reference this node ID
    ref_paths = indexer.get_references(node_id)
    if not ref_paths:
        return []

    # Scan each file for exact positions
    locations: list[ReferenceLocation] = []
    for ref_path in sorted(ref_paths):
        try:
            file_text = ref_path.read_text(encoding="utf-8")
        except OSError:
            continue
        _scan_file_for_id(ref_path, file_text, node_id, locations)

    return locations


def _scan_file_for_id(
    path: Path,
    text: str,
    node_id: str,
    out: list[ReferenceLocation],
) -> None:
    """Scan *text* for occurrences of *node_id* and append to *out*."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        start = 0
        while True:
            idx = line.find(node_id, start)
            if idx == -1:
                break
            out.append(
                ReferenceLocation(
                    path=path,
                    line=i,
                    start_col=idx,
                    end_col=idx + len(node_id),
                ),
            )
            start = idx + len(node_id)
