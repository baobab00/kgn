"""Tests for ``kgn.lsp.hover`` — hover info + go to definition.

Covers:
- UUID hover → node summary Markdown
- ``new:slug`` hover → file path + title
- ENUM hover (NodeType, NodeStatus, EdgeType) → description
- ``supersedes:`` field hover → target node info
- UUID not found → italic message
- Slug not found → italic message
- Go to Definition: UUID → file path
- Go to Definition: slug → file path
- Go to Definition: not found → None
- Empty document / broken state → no crash
- Word extraction edge cases
- YAML key context detection
- Format helpers
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from kgn.lsp.hover import (
    EDGE_TYPE_DESCRIPTIONS,
    NODE_STATUS_DESCRIPTIONS,
    NODE_TYPE_DESCRIPTIONS,
    _get_line_text,
    _word_at_position,
    _yaml_key_for_line,
    format_node_hover,
    get_definition,
    get_hover,
)
from kgn.lsp.indexer import NodeMeta
from kgn.models.enums import NodeStatus, NodeType

# ── Helpers ────────────────────────────────────────────────────────────

SAMPLE_UUID = "550e8400-e29b-41d4-a716-446655440000"
SAMPLE_UUID_2 = "660e8400-e29b-41d4-a716-446655440111"


def _make_meta(
    *,
    node_id: str = SAMPLE_UUID,
    slug: str = "auth-spec",
    node_type: NodeType = NodeType.SPEC,
    title: str = "Auth Flow Specification",
    status: NodeStatus = NodeStatus.ACTIVE,
    confidence: float | None = 0.95,
    path: Path = Path("/workspace/specs/auth-spec.kgn"),
) -> NodeMeta:
    return NodeMeta(
        id=node_id,
        slug=slug,
        type=node_type,
        title=title,
        status=status,
        confidence=confidence,
        path=path,
    )


def _make_indexer(
    *,
    uuid_map: dict[str, Path] | None = None,
    slug_map: dict[str, Path] | None = None,
    meta_map: dict[Path, NodeMeta] | None = None,
) -> MagicMock:
    """Create a mock WorkspaceIndexer."""
    indexer = MagicMock()
    _uuid = uuid_map or {}
    _slug = slug_map or {}
    _meta = meta_map or {}
    indexer.resolve_uuid.side_effect = lambda u: _uuid.get(u)
    indexer.resolve_slug.side_effect = lambda s: _slug.get(s.lower())
    indexer.get_meta.side_effect = lambda p: _meta.get(p)
    return indexer


VALID_KGN = f"""\
---
kgn_version: "1.0"
id: "{SAMPLE_UUID}"
type: SPEC
title: "Auth Flow"
status: ACTIVE
project_id: "proj-a"
agent_id: "agent-1"
supersedes: "{SAMPLE_UUID_2}"
confidence: 0.85
---

## Context

Some body text.
"""


# ── _get_line_text ───────────────────────────────────────────────────


class TestGetLineText:
    def test_valid_line(self):
        assert _get_line_text("a\nb\nc", 1) == "b"

    def test_first_line(self):
        assert _get_line_text("hello\nworld", 0) == "hello"

    def test_out_of_range(self):
        assert _get_line_text("a\nb", 5) is None

    def test_negative_line(self):
        assert _get_line_text("a\nb", -1) is None


# ── _word_at_position ────────────────────────────────────────────────


class TestWordAtPosition:
    def test_simple_word(self):
        assert _word_at_position("type: SPEC", 6) == "SPEC"

    def test_uuid(self):
        line = f'id: "{SAMPLE_UUID}"'
        # cursor in the middle of UUID
        pos = line.index("550")
        assert _word_at_position(line, pos) == SAMPLE_UUID

    def test_new_slug(self):
        assert _word_at_position('id: "new:my-node"', 5) == "new:my-node"

    def test_cursor_at_end(self):
        assert _word_at_position("SPEC", 4) == "SPEC"

    def test_empty_line(self):
        assert _word_at_position("", 0) == ""

    def test_cursor_on_space(self):
        # Position 1 in "a b" is space; extractor expands left to 'a'
        result = _word_at_position("a b", 1)
        assert result == "a"

    def test_cursor_between_words(self):
        # Position 2 in "a  b" (double space) → empty
        result = _word_at_position("a  b", 2)
        assert result == ""

    def test_cursor_beyond_line(self):
        assert _word_at_position("abc", 100) == "abc"


# ── _yaml_key_for_line ──────────────────────────────────────────────


class TestYamlKeyForLine:
    def test_type_key(self):
        assert _yaml_key_for_line("type: SPEC") == "type"

    def test_status_key(self):
        assert _yaml_key_for_line("status: ACTIVE") == "status"

    def test_indented_key(self):
        assert _yaml_key_for_line("  supersedes: abc") == "supersedes"

    def test_no_colon(self):
        assert _yaml_key_for_line("just text") is None

    def test_empty_key(self):
        assert _yaml_key_for_line(": value") is None


# ── format_node_hover ────────────────────────────────────────────────


class TestFormatNodeHover:
    def test_basic_format(self):
        meta = _make_meta()
        md = format_node_hover(meta)
        assert "**[SPEC]**" in md
        assert "Auth Flow Specification" in md
        assert "`ACTIVE`" in md
        assert "`0.95`" in md
        assert "auth-spec.kgn" in md

    def test_no_confidence(self):
        meta = _make_meta(confidence=None)
        md = format_node_hover(meta)
        assert "Confidence" not in md

    def test_contains_table(self):
        meta = _make_meta()
        md = format_node_hover(meta)
        assert "| Field | Value |" in md
        assert "|---|---|" in md


# ── ENUM descriptions ────────────────────────────────────────────────


class TestEnumDescriptions:
    def test_all_node_types_covered(self):
        for nt in NodeType:
            assert nt.value in NODE_TYPE_DESCRIPTIONS

    def test_all_node_statuses_covered(self):
        for ns in NodeStatus:
            assert ns.value in NODE_STATUS_DESCRIPTIONS

    def test_all_edge_types_covered(self):
        from kgn.models.enums import EdgeType

        for et in EdgeType:
            assert et.value in EDGE_TYPE_DESCRIPTIONS


# ── get_hover — UUID ─────────────────────────────────────────────────


class TestHoverUUID:
    def test_uuid_found(self):
        meta = _make_meta()
        indexer = _make_indexer(
            uuid_map={SAMPLE_UUID: meta.path},
            meta_map={meta.path: meta},
        )
        result = get_hover(VALID_KGN, 2, 10, indexer)
        assert result is not None
        assert "**[SPEC]**" in result
        assert "Auth Flow Specification" in result

    def test_uuid_not_found(self):
        indexer = _make_indexer()
        result = get_hover(VALID_KGN, 2, 10, indexer)
        assert result is not None
        assert "not found" in result

    def test_supersedes_uuid_hover(self):
        meta2 = _make_meta(
            node_id=SAMPLE_UUID_2,
            title="Old Flow",
            status=NodeStatus.SUPERSEDED,
            path=Path("/workspace/old.kgn"),
        )
        indexer = _make_indexer(
            uuid_map={SAMPLE_UUID_2: meta2.path},
            meta_map={meta2.path: meta2},
        )
        # Line 8 is 'supersedes: "UUID_2"'
        line_text = VALID_KGN.split("\n")[8]
        col = line_text.index(SAMPLE_UUID_2[:4])
        result = get_hover(VALID_KGN, 8, col, indexer)
        assert result is not None
        assert "Old Flow" in result


# ── get_hover — new:slug ─────────────────────────────────────────────


class TestHoverSlug:
    def test_slug_found(self):
        doc = '---\nid: "new:my-node"\n---\n'
        meta = _make_meta(slug="my-node", path=Path("/workspace/my-node.kgn"))
        indexer = _make_indexer(
            slug_map={"my-node": meta.path},
            meta_map={meta.path: meta},
        )
        result = get_hover(doc, 1, 6, indexer)
        assert result is not None
        assert "new:my-node" in result
        assert "Auth Flow" in result

    def test_slug_not_found(self):
        doc = '---\nid: "new:unknown"\n---\n'
        indexer = _make_indexer()
        result = get_hover(doc, 1, 6, indexer)
        assert result is not None
        assert "not found" in result


# ── get_hover — ENUM ─────────────────────────────────────────────────


class TestHoverEnum:
    def test_node_type_hover(self):
        indexer = _make_indexer()
        result = get_hover(VALID_KGN, 3, 6, indexer)  # "type: SPEC"
        assert result is not None
        assert "SPEC" in result
        assert "specification" in result.lower()

    def test_node_status_hover(self):
        indexer = _make_indexer()
        result = get_hover(VALID_KGN, 5, 9, indexer)  # "status: ACTIVE"
        assert result is not None
        assert "ACTIVE" in result

    def test_edge_type_hover(self):
        doc = "---\ntype: DEPENDS_ON\n---\n"
        indexer = _make_indexer()
        result = get_hover(doc, 1, 7, indexer)
        assert result is not None
        assert "DEPENDS_ON" in result
        assert "depends" in result.lower()


# ── get_hover — edge cases ───────────────────────────────────────────


class TestHoverEdgeCases:
    def test_empty_document(self):
        indexer = _make_indexer()
        result = get_hover("", 0, 0, indexer)
        assert result is None

    def test_line_out_of_range(self):
        indexer = _make_indexer()
        result = get_hover("just text", 999, 0, indexer)
        assert result is None

    def test_cursor_on_whitespace(self):
        indexer = _make_indexer()
        result = get_hover("type: SPEC", 0, 5, indexer)
        # cursor on ": " space — word is empty
        assert result is None

    def test_unknown_word(self):
        indexer = _make_indexer()
        result = get_hover("title: 'Hello World'", 0, 8, indexer)
        # 'Hello is not a UUID, slug, or ENUM
        assert result is None

    def test_body_text_no_hover(self):
        indexer = _make_indexer()
        result = get_hover(VALID_KGN, 14, 5, indexer)  # "Some body text."
        assert result is None


# ── get_hover — bare UUID substring (finditer branch) ────────────────


class TestHoverBareUUID:
    """Cover the finditer fallback when cursor word != full UUID."""

    def test_bare_uuid_found(self):
        # 'ref:' prefix makes word != fullmatch UUID, but UUID is on the line
        uuid = SAMPLE_UUID
        doc = f"ref:{uuid}\n"
        meta = _make_meta()
        indexer = _make_indexer(
            uuid_map={uuid: meta.path},
            meta_map={meta.path: meta},
        )
        # cursor at col 5 — inside the UUID portion
        result = get_hover(doc, 0, 5, indexer)
        assert result is not None
        assert "Auth Flow" in result

    def test_bare_uuid_not_found(self):
        uuid = SAMPLE_UUID
        doc = f"ref:{uuid}\n"
        indexer = _make_indexer()
        result = get_hover(doc, 0, 5, indexer)
        assert result is not None
        assert "not found" in result


# ── get_definition ───────────────────────────────────────────────────


class TestGetDefinition:
    def test_uuid_definition(self):
        meta = _make_meta()
        indexer = _make_indexer(
            uuid_map={SAMPLE_UUID: meta.path},
        )
        result = get_definition(VALID_KGN, 2, 10, indexer)
        assert result == meta.path

    def test_slug_definition(self):
        doc = '---\nid: "new:auth-spec"\n---\n'
        path = Path("/workspace/auth-spec.kgn")
        indexer = _make_indexer(slug_map={"auth-spec": path})
        result = get_definition(doc, 1, 6, indexer)
        assert result == path

    def test_uuid_not_found(self):
        indexer = _make_indexer()
        result = get_definition(VALID_KGN, 2, 10, indexer)
        assert result is None

    def test_slug_not_found(self):
        doc = '---\nid: "new:missing"\n---\n'
        indexer = _make_indexer()
        result = get_definition(doc, 1, 6, indexer)
        assert result is None

    def test_supersedes_uuid_definition(self):
        path2 = Path("/workspace/old.kgn")
        indexer = _make_indexer(uuid_map={SAMPLE_UUID_2: path2})
        line_text = VALID_KGN.split("\n")[8]
        col = line_text.index(SAMPLE_UUID_2[:4])
        result = get_definition(VALID_KGN, 8, col, indexer)
        assert result == path2

    def test_empty_document(self):
        indexer = _make_indexer()
        result = get_definition("", 0, 0, indexer)
        assert result is None

    def test_line_out_of_range(self):
        indexer = _make_indexer()
        result = get_definition("text", 999, 0, indexer)
        assert result is None

    def test_body_text_no_definition(self):
        indexer = _make_indexer()
        result = get_definition(VALID_KGN, 14, 5, indexer)
        assert result is None

    def test_bare_uuid_substring_definition(self):
        """Cover the finditer fallback in get_definition."""
        uuid = SAMPLE_UUID
        doc = f"ref:{uuid}\n"
        path = Path("/workspace/test.kgn")
        indexer = _make_indexer(uuid_map={uuid: path})
        # cursor at col 5 — inside the UUID portion
        result = get_definition(doc, 0, 5, indexer)
        assert result == path
