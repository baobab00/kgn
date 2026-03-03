"""Unit tests for kgn_parser and kge_parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from kgn.models.edge import EdgeFrontMatter
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.parser.kge_parser import KgeParseError, parse_kge, parse_kge_text
from kgn.parser.kgn_parser import KgnParseError, parse_kgn, parse_kgn_text
from kgn.parser.models import ParsedNode

FIXTURES = Path(__file__).parent / "fixtures"


# ── .kgn parser ────────────────────────────────────────────────────────


class TestKgnParserFile:
    """Tests using fixture files on disk."""

    def test_valid_spec(self) -> None:
        result = parse_kgn(FIXTURES / "valid_spec.kgn")
        assert isinstance(result, ParsedNode)
        assert result.front_matter.type == NodeType.SPEC
        assert result.front_matter.title == "User Authentication Specification"
        assert result.front_matter.status == NodeStatus.ACTIVE
        assert result.front_matter.confidence == 0.92
        assert result.front_matter.tags == ["auth", "security"]
        assert "## Context" in result.body
        assert len(result.content_hash) == 64  # SHA-256 hex

    def test_valid_new_id(self) -> None:
        result = parse_kgn(FIXTURES / "valid_new_id.kgn")
        assert result.front_matter.id == "new:temp-decision-001"
        assert result.front_matter.type == NodeType.DECISION

    def test_no_front_matter_raises(self) -> None:
        with pytest.raises(KgnParseError, match="V1"):
            parse_kgn(FIXTURES / "no_front_matter.kgn")

    def test_missing_closing_delimiter(self) -> None:
        with pytest.raises(KgnParseError, match="V1.*Closing"):
            parse_kgn(FIXTURES / "missing_closing.kgn")

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(KgnParseError, match="validation failed"):
            parse_kgn(FIXTURES / "invalid_type.kgn")

    def test_confidence_out_of_range(self) -> None:
        with pytest.raises(KgnParseError, match="validation failed"):
            parse_kgn(FIXTURES / "confidence_out_of_range.kgn")

    def test_source_path_recorded(self) -> None:
        result = parse_kgn(FIXTURES / "valid_spec.kgn")
        assert result.source_path is not None
        assert "valid_spec.kgn" in result.source_path


class TestKgnParserText:
    """Tests using raw text (no file I/O)."""

    _VALID_TEXT = """\
---
kgn_version: "0.1"
id: "550e8400-e29b-41d4-a716-446655440000"
type: SPEC
title: "Test Node"
status: ACTIVE
project_id: "proj-alpha"
agent_id: "agent-01"
created_at: "2026-02-27T10:00:00+09:00"
---

## Context

Simple test body.
"""

    def test_parse_text(self) -> None:
        result = parse_kgn_text(self._VALID_TEXT)
        assert result.front_matter.title == "Test Node"
        assert "Simple test body" in result.body

    def test_empty_body(self) -> None:
        text = """\
---
kgn_version: "0.1"
id: "550e8400-e29b-41d4-a716-446655440000"
type: GOAL
title: "Empty Body Node"
status: ACTIVE
project_id: "proj-alpha"
agent_id: "agent-01"
created_at: "2026-02-27T10:00:00+09:00"
---
"""
        result = parse_kgn_text(text)
        assert result.body == ""

    def test_yaml_syntax_error(self) -> None:
        text = """\
---
invalid: yaml: syntax: [
---

body
"""
        with pytest.raises(KgnParseError, match="YAML syntax error"):
            parse_kgn_text(text)

    def test_content_hash_deterministic(self) -> None:
        h1 = parse_kgn_text(self._VALID_TEXT).content_hash
        h2 = parse_kgn_text(self._VALID_TEXT).content_hash
        assert h1 == h2

    def test_content_hash_changes_with_body(self) -> None:
        text2 = self._VALID_TEXT + "\nExtra content"
        h1 = parse_kgn_text(self._VALID_TEXT).content_hash
        h2 = parse_kgn_text(text2).content_hash
        assert h1 != h2

    def test_only_opening_delimiter(self) -> None:
        text = "---\nkgn_version: '0.1'\n"
        with pytest.raises(KgnParseError, match="Closing"):
            parse_kgn_text(text)


# ── .kge parser ────────────────────────────────────────────────────────


class TestKgeParserFile:
    """Tests using fixture files on disk."""

    def test_valid_edges(self) -> None:
        result = parse_kge(FIXTURES / "edges.kge")
        assert isinstance(result, EdgeFrontMatter)
        assert len(result.edges) == 2
        assert result.edges[0].type == EdgeType.IMPLEMENTS
        assert result.edges[1].type == EdgeType.DEPENDS_ON

    def test_edge_from_alias(self) -> None:
        result = parse_kge(FIXTURES / "edges.kge")
        assert result.edges[0].from_node == "550e8400-e29b-41d4-a716-446655440000"

    def test_edge_note(self) -> None:
        result = parse_kge(FIXTURES / "edges.kge")
        assert "SPEC implements the ARCH" in result.edges[0].note
        assert result.edges[1].note == ""


class TestKgeParserText:
    """Tests using raw text."""

    def test_bare_yaml(self) -> None:
        text = """\
kgn_version: "0.1"
project_id: "proj-alpha"
agent_id: "agent-01"
created_at: "2026-02-27T10:00:00+09:00"
edges:
  - from: "aaa"
    to: "bbb"
    type: RESOLVES
"""
        result = parse_kge_text(text)
        assert len(result.edges) == 1
        assert result.edges[0].type == EdgeType.RESOLVES

    def test_yaml_with_delimiters(self) -> None:
        text = """\
---
kgn_version: "0.1"
project_id: "proj-alpha"
agent_id: "agent-01"
created_at: "2026-02-27T10:00:00+09:00"
edges:
  - from: "aaa"
    to: "bbb"
    type: SUPERSEDES
---
"""
        result = parse_kge_text(text)
        assert result.edges[0].type == EdgeType.SUPERSEDES

    def test_invalid_edge_type(self) -> None:
        text = """\
kgn_version: "0.1"
project_id: "proj-alpha"
agent_id: "agent-01"
created_at: "2026-02-27T10:00:00+09:00"
edges:
  - from: "aaa"
    to: "bbb"
    type: NOT_A_TYPE
"""
        with pytest.raises(KgeParseError, match="validation failed"):
            parse_kge_text(text)

    def test_missing_edges_field(self) -> None:
        text = """\
kgn_version: "0.1"
project_id: "proj-alpha"
agent_id: "agent-01"
created_at: "2026-02-27T10:00:00+09:00"
"""
        with pytest.raises(KgeParseError, match="validation failed"):
            parse_kge_text(text)

    def test_yaml_syntax_error(self) -> None:
        text = "invalid: yaml: ["
        with pytest.raises(KgeParseError, match="YAML syntax error"):
            parse_kge_text(text)

    def test_non_mapping_yaml(self) -> None:
        text = "- item1\n- item2\n"
        with pytest.raises(KgeParseError, match="must be a mapping"):
            parse_kge_text(text)
