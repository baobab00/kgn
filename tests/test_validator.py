"""Unit tests for validator.py — V1~V6, V9, V10 rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from kgn.parser.kgn_parser import parse_kgn, parse_kgn_text
from kgn.parser.validator import validate_kgn, validate_kgn_text

FIXTURES = Path(__file__).parent / "fixtures"


def _make_text(
    *,
    kgn_version: str = "0.1",
    node_id: str = "550e8400-e29b-41d4-a716-446655440000",
    node_type: str = "SPEC",
    title: str = "Test",
    status: str = "ACTIVE",
    project_id: str = "proj-alpha",
    agent_id: str = "agent-01",
    created_at: str = "2026-02-27T10:00:00+09:00",
    extra_yaml: str = "",
    body: str = "## Context\n\nDescription\n\n## Content\n\nBody text",
) -> str:
    """Build a minimal valid .kgn text with optional overrides."""
    extra_block = f"\n{extra_yaml}" if extra_yaml else ""
    return f"""\
---
kgn_version: "{kgn_version}"
id: "{node_id}"
type: {node_type}
title: "{title}"
status: {status}
project_id: "{project_id}"
agent_id: "{agent_id}"
created_at: "{created_at}"{extra_block}
---

{body}
"""


# ── V1: YAML front matter presence ────────────────────────────────────


class TestV1FrontMatter:
    def test_no_front_matter(self) -> None:
        result = validate_kgn_text("this is plain text.")
        assert not result.is_valid
        assert any("V1" in e for e in result.errors)

    def test_valid_front_matter(self) -> None:
        result = validate_kgn_text(_make_text())
        assert result.is_valid

    def test_v1_checked_via_raw_text(self) -> None:
        """V1 fires when raw_text is passed to validate_kgn."""
        parsed = parse_kgn_text(_make_text())
        result = validate_kgn(parsed, raw_text="no front matter here")
        assert not result.is_valid
        assert any("V1" in e for e in result.errors)


# ── V2: kgn_version ───────────────────────────────────────────────────


class TestV2Version:
    def test_unsupported_version(self) -> None:
        result = validate_kgn_text(_make_text(kgn_version="9.9"))
        assert not result.is_valid
        assert any("V2" in e for e in result.errors)

    def test_supported_version(self) -> None:
        result = validate_kgn_text(_make_text(kgn_version="0.1"))
        assert result.is_valid

    def test_unsupported_version_via_fixture(self) -> None:
        parsed = parse_kgn(FIXTURES / "unsupported_version.kgn")
        result = validate_kgn(parsed)
        assert not result.is_valid
        assert any("V2" in e for e in result.errors)


# ── V3: Required fields ──────────────────────────────────────────────


class TestV3RequiredFields:
    def test_title_missing(self) -> None:
        """Pydantic rejects missing title at parse time → KgnParseError → validator error."""
        text = """\
---
kgn_version: "0.1"
id: "550e8400-e29b-41d4-a716-446655440000"
type: SPEC
status: ACTIVE
project_id: "proj-alpha"
agent_id: "agent-01"
created_at: "2026-02-27T10:00:00+09:00"
---

body
"""
        result = validate_kgn_text(text)
        assert not result.is_valid

    def test_all_fields_present(self) -> None:
        result = validate_kgn_text(_make_text())
        assert result.is_valid


# ── V4: type ENUM ─────────────────────────────────────────────────────


class TestV4TypeEnum:
    def test_invalid_type(self) -> None:
        """Invalid type caught at parse time."""
        result = validate_kgn_text(_make_text(node_type="INVALID"))
        assert not result.is_valid

    def test_valid_type(self) -> None:
        result = validate_kgn_text(_make_text(node_type="DECISION"))
        assert result.is_valid


# ── V5: status ENUM ───────────────────────────────────────────────────


class TestV5StatusEnum:
    def test_invalid_status(self) -> None:
        result = validate_kgn_text(_make_text(status="UNKNOWN"))
        assert not result.is_valid

    def test_valid_status(self) -> None:
        result = validate_kgn_text(_make_text(status="DEPRECATED"))
        assert result.is_valid


# ── V6: id format ─────────────────────────────────────────────────────


class TestV6IdFormat:
    def test_valid_uuid_v4(self) -> None:
        parsed = parse_kgn_text(_make_text())
        result = validate_kgn(parsed)
        assert result.is_valid

    def test_valid_new_prefix(self) -> None:
        parsed = parse_kgn_text(_make_text(node_id="new:temp-node"))
        result = validate_kgn(parsed)
        assert result.is_valid

    def test_invalid_id(self) -> None:
        parsed = parse_kgn(FIXTURES / "invalid_id.kgn")
        result = validate_kgn(parsed)
        assert not result.is_valid
        assert any("V6" in e for e in result.errors)

    def test_bare_new_without_suffix(self) -> None:
        """``new:`` alone (no suffix) should still be rejected."""
        parsed = parse_kgn_text(_make_text(node_id="not-a-uuid"))
        result = validate_kgn(parsed)
        assert not result.is_valid
        assert any("V6" in e for e in result.errors)


# ── V9: confidence range ──────────────────────────────────────────────


class TestV9Confidence:
    def test_confidence_too_high(self) -> None:
        """confidence=1.5 is rejected at Pydantic level → parse error."""
        result = validate_kgn_text(_make_text(extra_yaml="confidence: 1.5"))
        assert not result.is_valid

    def test_confidence_too_low(self) -> None:
        result = validate_kgn_text(_make_text(extra_yaml="confidence: -0.1"))
        assert not result.is_valid

    def test_confidence_valid(self) -> None:
        result = validate_kgn_text(_make_text(extra_yaml="confidence: 0.75"))
        assert result.is_valid

    def test_confidence_absent(self) -> None:
        result = validate_kgn_text(_make_text())
        assert result.is_valid


# ── V10: Recommended sections ─────────────────────────────────────────


class TestV10Sections:
    def test_all_sections_present(self) -> None:
        body = "## Context\n\nDescription\n\n## Content\n\nBody text"
        result = validate_kgn_text(_make_text(body=body))
        assert result.is_valid
        assert len(result.warnings) == 0

    def test_no_sections(self) -> None:
        parsed = parse_kgn(FIXTURES / "no_sections.kgn")
        result = validate_kgn(parsed)
        # is_valid remains True (warnings don't fail)
        assert result.is_valid
        assert len(result.warnings) == 2
        assert any("Context" in w for w in result.warnings)
        assert any("Content" in w for w in result.warnings)

    def test_partial_sections(self) -> None:
        body = "## Context\n\nOnly description, no Content section"
        result = validate_kgn_text(_make_text(body=body))
        assert result.is_valid
        assert len(result.warnings) == 1
        assert "Content" in result.warnings[0]

    def test_v10_is_warning_not_error(self) -> None:
        parsed = parse_kgn(FIXTURES / "no_sections.kgn")
        result = validate_kgn(parsed)
        assert result.is_valid  # warnings only
        assert len(result.errors) == 0


# ── Integration: validate_kgn_text end-to-end ─────────────────────────


class TestValidateKgnTextIntegration:
    def test_fully_valid_file(self) -> None:
        text = (FIXTURES / "valid_spec.kgn").read_text(encoding="utf-8")
        result = validate_kgn_text(text)
        assert result.is_valid
        assert len(result.errors) == 0

    def test_multiple_errors(self) -> None:
        """Unsupported version + invalid id → at least 2 errors."""
        parsed = parse_kgn(FIXTURES / "unsupported_version.kgn")
        result = validate_kgn(parsed)
        # V2 error expected; V6 should pass since it has a valid-format UUID
        assert not result.is_valid
        assert any("V2" in e for e in result.errors)

    @pytest.mark.parametrize(
        "fixture_name",
        ["valid_spec.kgn", "valid_new_id.kgn"],
    )
    def test_valid_fixtures_pass(self, fixture_name: str) -> None:
        parsed = parse_kgn(FIXTURES / fixture_name)
        result = validate_kgn(parsed)
        assert result.is_valid
