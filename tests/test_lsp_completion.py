"""Tests for ``kgn.lsp.completion`` — context-aware completion provider.

Covers:
- type: → 10 NodeType ENUMs
- status: → 4 NodeStatus ENUMs
- confidence: → range hint values
- YAML key position → 11 front matter keys (.kgn) or 5 keys (.kge)
- Body ## → 5 recommended sections
- .kge type: → 7 EdgeType ENUMs
- Empty/broken document → no crash
- Cursor before colon → key completions
- Cursor on delimiter line → no completions
- Cursor in body without ## → no completions
"""

from __future__ import annotations

from lsprotocol import types

from kgn.lsp.completion import (
    _BODY_SECTIONS,
    _KGE_FRONT_MATTER_KEYS,
    _KGN_FRONT_MATTER_KEYS,
    _find_closing_delimiter,
    _get_yaml_key_at_cursor,
    _in_yaml_region,
    _is_yaml_key_position,
    get_completions,
)
from kgn.models.enums import EdgeType, NodeStatus, NodeType

# ── Fixtures ───────────────────────────────────────────────────────────

VALID_KGN = """\
---
kgn_version: "1.0"
id: "new:my-node"
type: SPEC
title: "Test Title"
status: ACTIVE
project_id: "proj-a"
agent_id: "agent-1"
created_at: "2025-01-01T00:00:00Z"
confidence: 0.85
---

## Context

Some body text.
"""

INCOMPLETE_KGN = """\
---
kgn_version: "1.0"
type:
"""

EMPTY_DOC = ""

BODY_ONLY = """\
---
kgn_version: "1.0"
id: "new:x"
type: SPEC
title: "T"
status: ACTIVE
project_id: "p"
agent_id: "a"
---

##
"""

KGE_DOC = """\
---
kgn_version: "1.0"
project_id: "proj-a"
agent_id: "a"
created_at: "2025-01-01"
edges:
  - from: "a"
    to: "b"
    type:
---
"""


# ── _find_closing_delimiter ──────────────────────────────────────────


class TestFindClosingDelimiter:
    def test_valid_document(self):
        lines = VALID_KGN.split("\n")
        assert _find_closing_delimiter(lines) == 10

    def test_no_closing(self):
        lines = INCOMPLETE_KGN.split("\n")
        assert _find_closing_delimiter(lines) is None

    def test_empty(self):
        assert _find_closing_delimiter([]) is None


# ── _in_yaml_region ──────────────────────────────────────────────────


class TestInYamlRegion:
    def test_line_0_not_yaml(self):
        assert not _in_yaml_region(0, 11)

    def test_line_1_is_yaml(self):
        assert _in_yaml_region(1, 11)

    def test_line_10_is_yaml(self):
        assert _in_yaml_region(10, 11)

    def test_closing_line_not_yaml(self):
        assert not _in_yaml_region(11, 11)

    def test_after_closing_not_yaml(self):
        assert not _in_yaml_region(12, 11)

    def test_no_closing_all_yaml_after_line_0(self):
        assert _in_yaml_region(5, None)

    def test_no_closing_line_0_not_yaml(self):
        assert not _in_yaml_region(0, None)


# ── _get_yaml_key_at_cursor ──────────────────────────────────────────


class TestGetYamlKeyAtCursor:
    def test_cursor_after_type_colon(self):
        assert _get_yaml_key_at_cursor("type: SPEC", 6) == "type"

    def test_cursor_at_end_of_value(self):
        assert _get_yaml_key_at_cursor("type: SPEC", 10) == "type"

    def test_cursor_on_colon(self):
        assert _get_yaml_key_at_cursor("type: SPEC", 4) is None

    def test_cursor_before_colon(self):
        assert _get_yaml_key_at_cursor("type: SPEC", 2) is None

    def test_no_colon(self):
        assert _get_yaml_key_at_cursor("some text", 5) is None

    def test_indented_key(self):
        assert _get_yaml_key_at_cursor("  type: SPEC", 8) == "type"

    def test_empty_value(self):
        assert _get_yaml_key_at_cursor("type: ", 6) == "type"


# ── _is_yaml_key_position ───────────────────────────────────────────


class TestIsYamlKeyPosition:
    def test_empty_line(self):
        assert _is_yaml_key_position("", 0)

    def test_partial_key(self):
        assert _is_yaml_key_position("typ", 3)

    def test_after_colon(self):
        assert not _is_yaml_key_position("type: SPEC", 6)

    def test_before_colon(self):
        assert _is_yaml_key_position("type: SPEC", 3)


# ── get_completions — NodeType ───────────────────────────────────────


class TestNodeTypeCompletions:
    def test_type_value_returns_10_enums(self):
        items = get_completions(VALID_KGN, 3, 6)  # after "type: "
        labels = {item.label for item in items}
        assert len(items) == 10
        for nt in NodeType:
            assert nt.value in labels

    def test_type_value_kind_is_enum_member(self):
        items = get_completions(VALID_KGN, 3, 6)
        assert all(item.kind == types.CompletionItemKind.EnumMember for item in items)

    def test_incomplete_type_value(self):
        items = get_completions(INCOMPLETE_KGN, 2, 6)  # "type: |"
        assert len(items) == 10


# ── get_completions — NodeStatus ─────────────────────────────────────


class TestNodeStatusCompletions:
    def test_status_value_returns_4_enums(self):
        items = get_completions(VALID_KGN, 5, 8)  # after "status: "
        labels = {item.label for item in items}
        assert len(items) == 4
        for ns in NodeStatus:
            assert ns.value in labels

    def test_status_kind_is_enum_member(self):
        items = get_completions(VALID_KGN, 5, 8)
        assert all(item.kind == types.CompletionItemKind.EnumMember for item in items)


# ── get_completions — confidence ─────────────────────────────────────


class TestConfidenceCompletions:
    def test_confidence_returns_range_hints(self):
        items = get_completions(VALID_KGN, 9, 13)  # after "confidence: "
        labels = {item.label for item in items}
        assert "0.0" in labels
        assert "1.0" in labels
        assert len(items) == 5


# ── get_completions — YAML key position ──────────────────────────────


class TestYamlKeyCompletions:
    def test_empty_line_in_yaml_returns_keys(self):
        # Insert an empty line inside YAML
        doc = "---\n\n---\n"
        items = get_completions(doc, 1, 0)
        assert len(items) == len(_KGN_FRONT_MATTER_KEYS)

    def test_key_completions_are_property_kind(self):
        doc = "---\n\n---\n"
        items = get_completions(doc, 1, 0)
        assert all(item.kind == types.CompletionItemKind.Property for item in items)

    def test_key_completions_include_insert_text(self):
        doc = "---\n\n---\n"
        items = get_completions(doc, 1, 0)
        for item in items:
            assert item.insert_text is not None
            assert item.insert_text.endswith(": ")

    def test_kge_key_completions_have_5_keys(self):
        doc = "---\n\n---\n"
        items = get_completions(doc, 1, 0, is_kge=True)
        assert len(items) == len(_KGE_FRONT_MATTER_KEYS)


# ── get_completions — body sections ──────────────────────────────────


class TestBodySectionCompletions:
    def test_hash_hash_returns_5_sections(self):
        items = get_completions(BODY_ONLY, 10, 3)  # "## |"
        labels = {item.label for item in items}
        assert len(items) == 5
        for s in _BODY_SECTIONS:
            assert s in labels

    def test_section_kind_is_text(self):
        items = get_completions(BODY_ONLY, 10, 3)
        assert all(item.kind == types.CompletionItemKind.Text for item in items)

    def test_body_text_no_hash_returns_empty(self):
        items = get_completions(VALID_KGN, 14, 0)  # "Some body text."
        assert items == []


# ── get_completions — EdgeType (KGE) ────────────────────────────────


class TestEdgeTypeCompletions:
    def test_kge_type_returns_7_edge_types(self):
        # Line 8 in KGE_DOC is "    type: "
        items = get_completions(KGE_DOC, 8, 11, is_kge=True)
        labels = {item.label for item in items}
        assert len(items) == 7
        for et in EdgeType:
            assert et.value in labels


# ── Edge cases ───────────────────────────────────────────────────────


class TestCompletionEdgeCases:
    def test_empty_document_no_crash(self):
        items = get_completions(EMPTY_DOC, 0, 0)
        assert isinstance(items, list)

    def test_line_out_of_range_returns_empty(self):
        items = get_completions(VALID_KGN, 999, 0)
        assert items == []

    def test_delimiter_line_returns_empty(self):
        # Line 0 is "---" — opening delimiter
        items = get_completions(VALID_KGN, 0, 0)
        assert items == []

    def test_closing_delimiter_line_returns_empty(self):
        # Line 10 is closing "---" — not in YAML
        items = get_completions(VALID_KGN, 10, 0)
        assert items == []

    def test_body_plain_text_returns_empty(self):
        items = get_completions(VALID_KGN, 15, 10)
        assert items == []
