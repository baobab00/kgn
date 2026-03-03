"""Tests for ``kgn.lsp.tokens`` — Semantic Token builder + constants.

Covers:
- Token type/modifier indexing and bitmasks
- encode_token helper
- build_semantic_tokens with valid .kgn documents
- YAML key → property token
- ENUM values → type/enum tokens
- UUID → variable + declaration
- new:slug → variable + declaration
- --- → keyword token
- status: DEPRECATED → enum + deprecated modifier
- kgn_version → property + readonly, value → string + readonly
- confidence → property, value → number
- project_id → property, value → namespace
- Incomplete/broken documents (no crash)
- Empty document
- Multi-line values (tags list) handled gracefully
"""

from __future__ import annotations

import pytest

from kgn.lsp.tokens import (
    _TYPE_INDEX,
    TOKEN_LEGEND,
    TOKEN_MODIFIERS,
    TOKEN_TYPES,
    _find_value_span,
    _value_token_info,
    build_semantic_tokens,
    encode_token,
    modifier_bitmask,
    type_index,
)
from kgn.parser import parse_kgn_tolerant

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

DEPRECATED_KGN = """\
---
kgn_version: "1.0"
id: "550e8400-e29b-41d4-a716-446655440000"
type: DECISION
title: "Old decision"
status: DEPRECATED
project_id: "proj-b"
agent_id: "agent-2"
created_at: "2025-06-01T00:00:00Z"
---

## Decision

Deprecated.
"""

EMPTY_DOC = ""
BROKEN_YAML = """\
---
kgn_version: "1.0"
type: SPEC
title: "Broken
---
"""

BODY_TRIPLE_DASH = """\
---
kgn_version: "1.0"
id: "new:test"
type: SPEC
title: "Test"
status: ACTIVE
project_id: "p"
agent_id: "a"
created_at: "2025-01-01T00:00:00Z"
---

## Context

Some text with --- in the body.
"""


# ── Token type / modifier constants ──────────────────────────────────


class TestTokenConstants:
    def test_token_types_has_8_entries(self):
        assert len(TOKEN_TYPES) == 8

    def test_token_modifiers_has_3_entries(self):
        assert len(TOKEN_MODIFIERS) == 3

    def test_token_legend_types_match(self):
        assert TOKEN_LEGEND.token_types == TOKEN_TYPES

    def test_token_legend_modifiers_match(self):
        assert TOKEN_LEGEND.token_modifiers == TOKEN_MODIFIERS

    def test_type_index_returns_correct(self):
        assert type_index("namespace") == 0
        assert type_index("keyword") == 7

    def test_type_index_unknown_raises(self):
        with pytest.raises(KeyError):
            type_index("nonexistent")

    def test_modifier_bitmask_empty(self):
        assert modifier_bitmask([]) == 0

    def test_modifier_bitmask_single(self):
        assert modifier_bitmask(["declaration"]) == 1  # 1 << 0

    def test_modifier_bitmask_deprecated(self):
        assert modifier_bitmask(["deprecated"]) == 2  # 1 << 1

    def test_modifier_bitmask_readonly(self):
        assert modifier_bitmask(["readonly"]) == 4  # 1 << 2

    def test_modifier_bitmask_combined(self):
        assert modifier_bitmask(["declaration", "deprecated"]) == 3  # 0b11

    def test_modifier_bitmask_unknown_ignored(self):
        assert modifier_bitmask(["unknown"]) == 0

    def test_encode_token_basic(self):
        result = encode_token(
            delta_line=1,
            delta_start=2,
            length=4,
            token_type="property",
        )
        assert result == [1, 2, 4, _TYPE_INDEX["property"], 0]

    def test_encode_token_with_modifiers(self):
        result = encode_token(
            delta_line=0,
            delta_start=5,
            length=3,
            token_type="string",
            token_modifiers=["declaration", "readonly"],
        )
        assert result[4] == modifier_bitmask(["declaration", "readonly"])


# ── _find_value_span ─────────────────────────────────────────────────


class TestFindValueSpan:
    def test_simple_value(self):
        result = _find_value_span("type: SPEC", 0, 4)
        assert result == (6, 4)

    def test_quoted_value(self):
        result = _find_value_span('title: "Test Title"', 0, 5)
        assert result == (7, 12)

    def test_no_value_empty(self):
        result = _find_value_span("tags:", 0, 4)
        assert result is None

    def test_no_value_whitespace_only(self):
        result = _find_value_span("tags:   ", 0, 4)
        assert result is None

    def test_value_with_extra_spaces(self):
        result = _find_value_span("type:  SPEC", 0, 4)
        assert result == (7, 4)


# ── _value_token_info ────────────────────────────────────────────────


class TestValueTokenInfo:
    def test_type_value(self):
        assert _value_token_info("type", "SPEC") == ("type", [])

    def test_status_active(self):
        assert _value_token_info("status", "ACTIVE") == ("enum", [])

    def test_status_deprecated(self):
        result = _value_token_info("status", "DEPRECATED")
        assert result is not None
        assert result[0] == "enum"
        assert "deprecated" in result[1]

    def test_confidence_value(self):
        assert _value_token_info("confidence", "0.85") == ("number", [])

    def test_id_value(self):
        result = _value_token_info("id", '"new:slug"')
        assert result is not None
        assert result[0] == "variable"
        assert "declaration" in result[1]

    def test_project_id_value(self):
        assert _value_token_info("project_id", '"proj-a"') == ("namespace", [])

    def test_kgn_version_value(self):
        result = _value_token_info("kgn_version", '"1.0"')
        assert result is not None
        assert result[0] == "string"
        assert "readonly" in result[1]

    def test_list_marker_returns_none(self):
        assert _value_token_info("tags", '["auth"]') is None

    def test_empty_value_returns_none(self):
        assert _value_token_info("tags", "") is None

    def test_unknown_key_string(self):
        assert _value_token_info("custom_field", "value") == ("string", [])


# ── build_semantic_tokens ────────────────────────────────────────────


class TestBuildSemanticTokens:
    def test_valid_kgn_returns_nonempty_data(self):
        result = parse_kgn_tolerant(VALID_KGN)
        data = build_semantic_tokens(VALID_KGN, result)
        assert len(data) > 0
        assert len(data) % 5 == 0

    def test_delimiter_tokens_are_keyword(self):
        result = parse_kgn_tolerant(VALID_KGN)
        data = build_semantic_tokens(VALID_KGN, result)
        tokens = _data_to_absolute(data)
        # Find keyword tokens (type index 7)
        keywords = [t for t in tokens if t[3] == _TYPE_INDEX["keyword"]]
        # At least 2 --- delimiters
        assert len(keywords) >= 2

    def test_yaml_keys_are_property(self):
        result = parse_kgn_tolerant(VALID_KGN)
        data = build_semantic_tokens(VALID_KGN, result)
        tokens = _data_to_absolute(data)
        property_tokens = [t for t in tokens if t[3] == _TYPE_INDEX["property"]]
        # Multiple YAML keys should be marked as property
        assert len(property_tokens) >= 5

    def test_type_value_is_type_token(self):
        result = parse_kgn_tolerant(VALID_KGN)
        data = build_semantic_tokens(VALID_KGN, result)
        tokens = _data_to_absolute(data)
        type_tokens = [t for t in tokens if t[3] == _TYPE_INDEX["type"]]
        # 'SPEC' value should be a type token
        assert len(type_tokens) >= 1

    def test_status_value_is_enum_token(self):
        result = parse_kgn_tolerant(VALID_KGN)
        data = build_semantic_tokens(VALID_KGN, result)
        tokens = _data_to_absolute(data)
        enum_tokens = [t for t in tokens if t[3] == _TYPE_INDEX["enum"]]
        # 'ACTIVE' value should be an enum token
        assert len(enum_tokens) >= 1

    def test_id_value_has_declaration_modifier(self):
        result = parse_kgn_tolerant(VALID_KGN)
        data = build_semantic_tokens(VALID_KGN, result)
        tokens = _data_to_absolute(data)
        var_decl_tokens = [
            t
            for t in tokens
            if t[3] == _TYPE_INDEX["variable"] and (t[4] & 1)  # declaration bit
        ]
        assert len(var_decl_tokens) >= 1

    def test_confidence_value_is_number(self):
        result = parse_kgn_tolerant(VALID_KGN)
        data = build_semantic_tokens(VALID_KGN, result)
        tokens = _data_to_absolute(data)
        number_tokens = [t for t in tokens if t[3] == _TYPE_INDEX["number"]]
        assert len(number_tokens) >= 1

    def test_project_id_value_is_namespace(self):
        result = parse_kgn_tolerant(VALID_KGN)
        data = build_semantic_tokens(VALID_KGN, result)
        tokens = _data_to_absolute(data)
        ns_tokens = [t for t in tokens if t[3] == _TYPE_INDEX["namespace"]]
        assert len(ns_tokens) >= 1

    def test_deprecated_status_has_modifier(self):
        result = parse_kgn_tolerant(DEPRECATED_KGN)
        data = build_semantic_tokens(DEPRECATED_KGN, result)
        tokens = _data_to_absolute(data)
        deprecated_tokens = [
            t
            for t in tokens
            if t[3] == _TYPE_INDEX["enum"] and (t[4] & 2)  # deprecated bit
        ]
        assert len(deprecated_tokens) >= 1

    def test_kgn_version_key_has_readonly(self):
        result = parse_kgn_tolerant(VALID_KGN)
        data = build_semantic_tokens(VALID_KGN, result)
        tokens = _data_to_absolute(data)
        readonly_props = [
            t
            for t in tokens
            if t[3] == _TYPE_INDEX["property"] and (t[4] & 4)  # readonly bit
        ]
        assert len(readonly_props) >= 1

    def test_empty_document_returns_empty(self):
        result = parse_kgn_tolerant(EMPTY_DOC)
        data = build_semantic_tokens(EMPTY_DOC, result)
        assert data == []

    def test_broken_yaml_no_crash(self):
        result = parse_kgn_tolerant(BROKEN_YAML)
        data = build_semantic_tokens(BROKEN_YAML, result)
        # Should still return some tokens (--- delimiters at minimum)
        assert len(data) % 5 == 0

    def test_body_triple_dash_not_tokenised(self):
        """Body '---' is a Markdown HR, NOT a YAML delimiter (R-108 fix).

        Only the opening and closing ``---`` should be keyword tokens.
        """
        result = parse_kgn_tolerant(BODY_TRIPLE_DASH)
        data = build_semantic_tokens(BODY_TRIPLE_DASH, result)
        tokens = _data_to_absolute(data)
        keywords = [t for t in tokens if t[3] == _TYPE_INDEX["keyword"]]
        # Opening + closing = 2 (body --- excluded)
        assert len(keywords) == 2

    def test_delta_encoding_is_relative(self):
        """Verify delta encoding: first token uses absolute, rest relative."""
        result = parse_kgn_tolerant(VALID_KGN)
        data = build_semantic_tokens(VALID_KGN, result)
        # First 5 ints: deltaLine from 0, deltaStart from 0
        assert len(data) >= 10
        # All delta values should be non-negative
        for i in range(0, len(data), 5):
            assert data[i] >= 0, f"deltaLine at {i} is negative"
            assert data[i + 1] >= 0, f"deltaStart at {i} is negative"
            assert data[i + 2] > 0, f"length at {i} is non-positive"

    def test_tokens_cover_all_yaml_keys(self):
        """Every key in yaml_node_positions should produce a token."""
        result = parse_kgn_tolerant(VALID_KGN)
        data = build_semantic_tokens(VALID_KGN, result)
        tokens = _data_to_absolute(data)
        # Expect at least one token per yaml key + values + 2 delimiters
        n_keys = len(result.yaml_node_positions)
        # Each key produces at least 1 token (key), some produce 2 (key+value)
        # Plus 2+ delimiter tokens
        assert len(tokens) >= n_keys + 2

    def test_uuid_id_value_is_variable(self):
        result = parse_kgn_tolerant(DEPRECATED_KGN)
        data = build_semantic_tokens(DEPRECATED_KGN, result)
        tokens = _data_to_absolute(data)
        var_tokens = [t for t in tokens if t[3] == _TYPE_INDEX["variable"]]
        # UUID value of id field should be variable + declaration
        assert len(var_tokens) >= 1


# ── Helper ───────────────────────────────────────────────────────────


def _data_to_absolute(
    data: list[int],
) -> list[tuple[int, int, int, int, int]]:
    """Convert delta-encoded token data to absolute (line, col, len, type, mod) tuples."""
    tokens: list[tuple[int, int, int, int, int]] = []
    prev_line = 0
    prev_col = 0
    for i in range(0, len(data), 5):
        dl, dc, length, tidx, mmask = data[i : i + 5]
        line = prev_line + dl
        col = dc if dl > 0 else prev_col + dc
        tokens.append((line, col, length, tidx, mmask))
        prev_line = line
        prev_col = col
    return tokens
