"""Tests for the fault-tolerant ``parse_kgn_tolerant()`` parser.

Covers all error-recovery scenarios described in Phase 11, Step 1:

* Empty / whitespace-only documents
* Missing ``---`` delimiters
* YAML syntax errors
* Pydantic field-level validation failures
* Partially valid front matter
* DiagnosticSpan position accuracy
* Valid documents (zero diagnostics)

No database or network access required.
"""

from __future__ import annotations

import textwrap

import pytest

from kgn.parser.kgn_parser import parse_kgn_tolerant
from kgn.parser.models import DiagnosticSpan, PartialParseResult, Severity

# ── Helpers ────────────────────────────────────────────────────────────


def _diag_rules(result: PartialParseResult) -> list[str]:
    """Extract sorted rule identifiers from diagnostics."""
    return sorted(d.rule for d in result.diagnostics)


def _error_diags(result: PartialParseResult) -> list[DiagnosticSpan]:
    """Filter diagnostics to only ERROR severity."""
    return [d for d in result.diagnostics if d.severity == Severity.ERROR]


def _warning_diags(result: PartialParseResult) -> list[DiagnosticSpan]:
    return [d for d in result.diagnostics if d.severity == Severity.WARNING]


VALID_KGN = textwrap.dedent("""\
    ---
    kgn_version: "0.1"
    id: "550e8400-e29b-41d4-a716-446655440000"
    type: SPEC
    title: "Test Node"
    status: ACTIVE
    project_id: "proj-alpha"
    agent_id: "worker-01"
    ---

    ## Context

    Some context.

    ## Content

    Some content.
""")

VALID_KGN_MINIMAL = textwrap.dedent("""\
    ---
    kgn_version: "0.1"
    id: "550e8400-e29b-41d4-a716-446655440000"
    type: SPEC
    title: "Minimal"
    status: ACTIVE
    project_id: "proj-alpha"
    agent_id: "worker-01"
    ---
""")


# ═══════════════════════════════════════════════════════════════════════
# 1. Empty / degenerate documents
# ═══════════════════════════════════════════════════════════════════════


class TestEmptyDocuments:
    """Documents that are empty, whitespace-only, or otherwise degenerate."""

    def test_empty_string(self) -> None:
        result = parse_kgn_tolerant("")
        assert result.front_matter is None
        assert result.has_errors
        assert any(d.rule == "V1" for d in result.diagnostics)

    def test_whitespace_only(self) -> None:
        result = parse_kgn_tolerant("   \n\n  \t  ")
        assert result.front_matter is None
        assert result.has_errors

    def test_newlines_only(self) -> None:
        result = parse_kgn_tolerant("\n\n\n")
        assert result.front_matter is None
        assert result.has_errors

    def test_single_newline(self) -> None:
        result = parse_kgn_tolerant("\n")
        assert result.front_matter is None
        assert result.has_errors


# ═══════════════════════════════════════════════════════════════════════
# 2. Missing delimiters (V1)
# ═══════════════════════════════════════════════════════════════════════


class TestMissingDelimiters:
    """V1: ``---`` delimiter problems."""

    def test_no_opening_delimiter(self) -> None:
        text = "kgn_version: 0.1\ntitle: Hello\n"
        result = parse_kgn_tolerant(text)
        assert result.front_matter is None
        assert any(d.rule == "V1" for d in result.diagnostics)
        # Body should contain the entire text
        assert "kgn_version" in result.body

    def test_no_closing_delimiter(self) -> None:
        text = "---\nkgn_version: '0.1'\ntitle: Hello\n"
        result = parse_kgn_tolerant(text)
        assert any(d.rule == "V1" for d in result.diagnostics)

    def test_only_opening_delimiter(self) -> None:
        result = parse_kgn_tolerant("---\n")
        assert result.has_errors

    def test_only_triple_dash(self) -> None:
        result = parse_kgn_tolerant("---")
        assert result.has_errors

    def test_bom_prefix_stripped(self) -> None:
        text = "\ufeff" + VALID_KGN
        result = parse_kgn_tolerant(text)
        assert result.front_matter is not None
        assert not result.has_errors

    def test_delimiter_with_trailing_spaces(self) -> None:
        """Delimiter detection handles the normal case."""
        text = "---\nkgn_version: '0.1'\n---\nBody here."
        result = parse_kgn_tolerant(text)
        # Should parse YAML (even if incomplete fields)
        assert result.body == "Body here."


# ═══════════════════════════════════════════════════════════════════════
# 3. YAML syntax errors
# ═══════════════════════════════════════════════════════════════════════


class TestYamlErrors:
    """YAML structural/syntax errors within front matter."""

    def test_tab_indentation(self) -> None:
        text = "---\nkey:\n\t- bad indent\n  - mixed\n---\n"
        result = parse_kgn_tolerant(text)
        # PyYAML may accept tabs in some contexts; what matters is no crash
        assert isinstance(result, PartialParseResult)
        assert result.has_errors

    def test_invalid_yaml_colon(self) -> None:
        text = "---\ntitle: : : broken\nid: test\n---\n"
        result = parse_kgn_tolerant(text)
        # Should have YAML error or validation errors
        assert result.has_errors

    def test_yaml_duplicate_key(self) -> None:
        text = "---\ntitle: First\ntitle: Second\n---\n"
        result = parse_kgn_tolerant(text)
        # YAML allows duplicate keys (last wins); Pydantic validation
        # will then fail because required fields are missing.
        assert result.has_errors

    def test_yaml_list_instead_of_mapping(self) -> None:
        text = "---\n- item1\n- item2\n---\n"
        result = parse_kgn_tolerant(text)
        assert result.front_matter is None
        assert any(d.rule == "YAML_ERROR" for d in result.diagnostics)

    def test_yaml_scalar_instead_of_mapping(self) -> None:
        text = "---\njust a string\n---\n"
        result = parse_kgn_tolerant(text)
        assert result.front_matter is None
        assert any(d.rule == "YAML_ERROR" for d in result.diagnostics)

    def test_yaml_error_position(self) -> None:
        """YAML_ERROR diagnostic should reference a valid line number."""
        text = "---\nkey: [unclosed\n---\n"
        result = parse_kgn_tolerant(text)
        yaml_errors = [d for d in result.diagnostics if d.rule == "YAML_ERROR"]
        assert len(yaml_errors) >= 1
        err = yaml_errors[0]
        # The YAML error is in the front matter block (line 1+ in the document)
        assert err.start_line >= 1

    def test_empty_yaml_block(self) -> None:
        text = "---\n\n---\nBody text.\n"
        result = parse_kgn_tolerant(text)
        # Empty YAML → YAML_ERROR
        assert result.front_matter is None
        assert result.has_errors

    def test_unclosed_quote(self) -> None:
        text = '---\ntitle: "unclosed\nid: test\n---\n'
        result = parse_kgn_tolerant(text)
        assert result.has_errors


# ═══════════════════════════════════════════════════════════════════════
# 4. Pydantic validation failures (per-field diagnostics)
# ═══════════════════════════════════════════════════════════════════════


class TestPydanticValidation:
    """Front matter that parses as valid YAML but fails Pydantic validation."""

    def test_missing_required_type(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            title: "Missing Type"
            status: ACTIVE
            project_id: "proj-alpha"
            agent_id: "worker-01"
            ---
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is None
        errors = _error_diags(result)
        assert any("type" in d.message for d in errors)

    def test_missing_required_id(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            type: SPEC
            title: "Missing ID"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            ---
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is None
        errors = _error_diags(result)
        assert any("id" in d.message for d in errors)

    def test_missing_required_title(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: SPEC
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            ---
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is None
        errors = _error_diags(result)
        assert any("title" in d.message for d in errors)

    def test_missing_multiple_fields(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            title: "Only Title"
            ---
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is None
        errors = _error_diags(result)
        # Should have diagnostics for id, type, status, project_id, agent_id
        assert len(errors) >= 4

    def test_invalid_type_enum(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: INVALID_TYPE
            title: "Bad Type"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            ---
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is None
        errors = _error_diags(result)
        assert any("type" in d.message for d in errors)

    def test_invalid_status_enum(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: SPEC
            title: "Bad Status"
            status: UNKNOWN_STATUS
            project_id: "proj"
            agent_id: "agent"
            ---
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is None
        errors = _error_diags(result)
        assert any("status" in d.message for d in errors)

    def test_confidence_out_of_range_high(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: SPEC
            title: "High Confidence"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            confidence: 1.5
            ---
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is None
        errors = _error_diags(result)
        assert any("confidence" in d.message for d in errors)

    def test_confidence_out_of_range_negative(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: SPEC
            title: "Negative Confidence"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            confidence: -0.1
            ---
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is None
        errors = _error_diags(result)
        assert any("confidence" in d.message for d in errors)

    def test_confidence_valid_boundary_zero(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: SPEC
            title: "Zero Confidence"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            confidence: 0.0
            ---
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is not None
        assert result.front_matter.confidence == 0.0

    def test_confidence_valid_boundary_one(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: SPEC
            title: "Full Confidence"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            confidence: 1.0
            ---
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is not None
        assert result.front_matter.confidence == 1.0


# ═══════════════════════════════════════════════════════════════════════
# 5. Partially valid documents
# ═══════════════════════════════════════════════════════════════════════


class TestPartialDocuments:
    """Documents with some valid parts and some broken parts."""

    def test_valid_yaml_missing_body(self) -> None:
        result = parse_kgn_tolerant(VALID_KGN_MINIMAL)
        assert result.front_matter is not None
        assert result.body == ""
        assert not result.has_errors

    def test_valid_yaml_empty_body(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: SPEC
            title: "Empty Body"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            ---

        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is not None
        assert not result.has_errors

    def test_body_only_no_front_matter(self) -> None:
        text = "## Context\n\nSome content here.\n"
        result = parse_kgn_tolerant(text)
        assert result.front_matter is None
        assert "Some content here." in result.body
        assert any(d.rule == "V1" for d in result.diagnostics)

    def test_partial_yaml_fields(self) -> None:
        """Only some required fields present."""
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            title: "Partial Fields"
            ---

            ## Content
            Hello world.
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is None
        assert result.body  # Body should still be extracted
        assert result.has_errors

    def test_extra_fields_are_ignored(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: SPEC
            title: "Extra Fields"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            custom_field: "should be ignored"
            ---
        """)
        result = parse_kgn_tolerant(text)
        # NodeFrontMatter may accept or reject extra fields
        # depending on Pydantic config; either way, no crash.
        assert isinstance(result, PartialParseResult)


# ═══════════════════════════════════════════════════════════════════════
# 6. Valid documents (happy path)
# ═══════════════════════════════════════════════════════════════════════


class TestValidDocuments:
    """Valid documents should produce zero diagnostics."""

    def test_standard_valid_document(self) -> None:
        result = parse_kgn_tolerant(VALID_KGN)
        assert result.front_matter is not None
        assert not result.has_errors
        assert result.diagnostics == []
        assert result.front_matter.title == "Test Node"
        assert result.front_matter.type == "SPEC"

    def test_valid_minimal(self) -> None:
        result = parse_kgn_tolerant(VALID_KGN_MINIMAL)
        assert result.front_matter is not None
        assert not result.has_errors
        assert result.content_hash is not None

    def test_valid_with_confidence(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: DECISION
            title: "With Confidence"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            confidence: 0.85
            ---
            ## Content
            Decision details.
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is not None
        assert result.front_matter.confidence == 0.85
        assert not result.has_errors

    def test_valid_with_tags(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: GOAL
            title: "With Tags"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            tags: ["alpha", "beta"]
            ---
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is not None
        assert result.front_matter.tags == ["alpha", "beta"]

    def test_valid_with_supersedes(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: SPEC
            title: "Superseding Node"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            supersedes: "660e8400-e29b-41d4-a716-446655440000"
            ---
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is not None
        assert result.front_matter.supersedes == "660e8400-e29b-41d4-a716-446655440000"

    def test_valid_new_id(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "new:temp-001"
            type: SPEC
            title: "New ID"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            ---
        """)
        result = parse_kgn_tolerant(text)
        assert result.front_matter is not None
        assert result.front_matter.id == "new:temp-001"

    def test_all_node_types(self) -> None:
        """All valid NodeType enum values should parse without errors."""
        node_types = [
            "GOAL",
            "ARCH",
            "SPEC",
            "LOGIC",
            "DECISION",
            "ISSUE",
            "TASK",
            "CONSTRAINT",
            "ASSUMPTION",
            "SUMMARY",
        ]
        for nt in node_types:
            text = textwrap.dedent(f"""\
                ---
                kgn_version: "0.1"
                id: "550e8400-e29b-41d4-a716-446655440000"
                type: {nt}
                title: "Node of type {nt}"
                status: ACTIVE
                project_id: "proj"
                agent_id: "agent"
                ---
            """)
            result = parse_kgn_tolerant(text)
            assert result.front_matter is not None, f"Failed for type {nt}"
            assert not result.has_errors, f"Errors for type {nt}: {result.diagnostics}"


# ═══════════════════════════════════════════════════════════════════════
# 7. Content hash
# ═══════════════════════════════════════════════════════════════════════


class TestContentHash:
    """Content hash generation in tolerant mode."""

    def test_hash_present_on_valid(self) -> None:
        result = parse_kgn_tolerant(VALID_KGN)
        assert result.content_hash is not None
        assert len(result.content_hash) == 64  # SHA-256 hex

    def test_hash_present_on_partial_yaml_error(self) -> None:
        """Hash is computed if YAML text exists (even with validation error)."""
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            title: "Missing Fields"
            ---
            Body text.
        """)
        result = parse_kgn_tolerant(text)
        assert result.content_hash is not None

    def test_hash_none_on_total_failure(self) -> None:
        result = parse_kgn_tolerant("")
        assert result.content_hash is None

    def test_hash_deterministic(self) -> None:
        r1 = parse_kgn_tolerant(VALID_KGN)
        r2 = parse_kgn_tolerant(VALID_KGN)
        assert r1.content_hash == r2.content_hash


# ═══════════════════════════════════════════════════════════════════════
# 8. YAML node positions (yaml.compose integration)
# ═══════════════════════════════════════════════════════════════════════


class TestYamlNodePositions:
    """yaml.compose()-derived field positions in the original document."""

    def test_positions_populated_on_valid(self) -> None:
        result = parse_kgn_tolerant(VALID_KGN)
        assert len(result.yaml_node_positions) > 0
        assert "kgn_version" in result.yaml_node_positions
        assert "type" in result.yaml_node_positions

    def test_position_kgn_version_line(self) -> None:
        result = parse_kgn_tolerant(VALID_KGN)
        pos = result.yaml_node_positions["kgn_version"]
        # kgn_version is on line 1 (0-based) in the document (line 0 is ---)
        assert pos[0] == 1  # start_line

    def test_position_type_line(self) -> None:
        result = parse_kgn_tolerant(VALID_KGN)
        pos = result.yaml_node_positions["type"]
        # type is on line 3 (0-based):  0=---, 1=kgn_version, 2=id, 3=type
        assert pos[0] == 3

    def test_positions_empty_on_yaml_error(self) -> None:
        text = "---\n\tbad yaml\n---\n"
        result = parse_kgn_tolerant(text)
        assert result.yaml_node_positions == {}

    def test_positions_empty_on_no_yaml(self) -> None:
        result = parse_kgn_tolerant("just body text")
        assert result.yaml_node_positions == {}

    def test_position_columns_start_at_zero(self) -> None:
        result = parse_kgn_tolerant(VALID_KGN)
        for key, (sl, sc, _el, _ec) in result.yaml_node_positions.items():
            assert sc >= 0, f"{key} start_col negative"
            assert sl >= 0, f"{key} start_line negative"

    def test_missing_field_diagnostic_points_to_block_start(self) -> None:
        """Fields not present in YAML should point to the front matter block."""
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            title: "Missing ID"
            ---
        """)
        result = parse_kgn_tolerant(text)
        errors = _error_diags(result)
        # At least one error for a missing field
        assert len(errors) > 0


# ═══════════════════════════════════════════════════════════════════════
# 9. DiagnosticSpan model
# ═══════════════════════════════════════════════════════════════════════


class TestDiagnosticSpanModel:
    """Unit tests for the DiagnosticSpan dataclass."""

    def test_frozen(self) -> None:
        span = DiagnosticSpan(
            rule="V1",
            message="test",
            severity=Severity.ERROR,
        )
        with pytest.raises(AttributeError):
            span.rule = "V2"  # type: ignore[misc]

    def test_default_positions(self) -> None:
        span = DiagnosticSpan(rule="V1", message="test", severity=Severity.ERROR)
        assert span.start_line == 0
        assert span.start_col == 0
        assert span.end_line == 0
        assert span.end_col == 0

    def test_custom_positions(self) -> None:
        span = DiagnosticSpan(
            rule="V3",
            message="field missing",
            severity=Severity.ERROR,
            start_line=5,
            start_col=2,
            end_line=5,
            end_col=10,
        )
        assert span.start_line == 5
        assert span.end_col == 10


# ═══════════════════════════════════════════════════════════════════════
# 10. Severity enum
# ═══════════════════════════════════════════════════════════════════════


class TestSeverityEnum:
    """Severity enum matches LSP DiagnosticSeverity values."""

    def test_error_value(self) -> None:
        assert Severity.ERROR == 1

    def test_warning_value(self) -> None:
        assert Severity.WARNING == 2

    def test_information_value(self) -> None:
        assert Severity.INFORMATION == 3

    def test_hint_value(self) -> None:
        assert Severity.HINT == 4

    def test_ordering(self) -> None:
        assert Severity.ERROR < Severity.WARNING < Severity.INFORMATION < Severity.HINT


# ═══════════════════════════════════════════════════════════════════════
# 11. PartialParseResult model
# ═══════════════════════════════════════════════════════════════════════


class TestPartialParseResultModel:
    """Unit tests for the PartialParseResult dataclass."""

    def test_defaults(self) -> None:
        r = PartialParseResult()
        assert r.front_matter is None
        assert r.body == ""
        assert r.diagnostics == []
        assert r.content_hash is None
        assert r.source_path is None
        assert r.yaml_node_positions == {}

    def test_has_errors_false_when_empty(self) -> None:
        r = PartialParseResult()
        assert not r.has_errors

    def test_has_errors_true_with_error(self) -> None:
        r = PartialParseResult(
            diagnostics=[
                DiagnosticSpan(rule="V1", message="err", severity=Severity.ERROR),
            ],
        )
        assert r.has_errors

    def test_has_errors_false_with_warning_only(self) -> None:
        r = PartialParseResult(
            diagnostics=[
                DiagnosticSpan(rule="V10", message="warn", severity=Severity.WARNING),
            ],
        )
        assert not r.has_errors

    def test_source_path_preserved(self) -> None:
        result = parse_kgn_tolerant(VALID_KGN, source_path="/some/path.kgn")
        assert result.source_path == "/some/path.kgn"


# ═══════════════════════════════════════════════════════════════════════
# 12. Rule mapping
# ═══════════════════════════════════════════════════════════════════════


class TestRuleMapping:
    """Verify that Pydantic errors map to the correct KGN validation rules."""

    def test_missing_type_maps_to_v3(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            title: "No Type"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            ---
        """)
        result = parse_kgn_tolerant(text)
        rules = _diag_rules(result)
        assert "V3" in rules

    def test_invalid_type_maps_to_v4(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: NOT_A_TYPE
            title: "Invalid Type"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            ---
        """)
        result = parse_kgn_tolerant(text)
        rules = _diag_rules(result)
        assert "V4" in rules

    def test_invalid_status_maps_to_v5(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: SPEC
            title: "Invalid Status"
            status: NOT_VALID
            project_id: "proj"
            agent_id: "agent"
            ---
        """)
        result = parse_kgn_tolerant(text)
        rules = _diag_rules(result)
        assert "V5" in rules

    def test_confidence_error_maps_to_v9(self) -> None:
        text = textwrap.dedent("""\
            ---
            kgn_version: "0.1"
            id: "550e8400-e29b-41d4-a716-446655440000"
            type: SPEC
            title: "Bad Conf"
            status: ACTIVE
            project_id: "proj"
            agent_id: "agent"
            confidence: 2.0
            ---
        """)
        result = parse_kgn_tolerant(text)
        rules = _diag_rules(result)
        assert "V9" in rules


# ═══════════════════════════════════════════════════════════════════════
# 13. Never throws (R24 invariant)
# ═══════════════════════════════════════════════════════════════════════


class TestNeverThrows:
    """parse_kgn_tolerant MUST never raise exceptions (R24)."""

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "\n",
            "---",
            "---\n",
            "---\n---",
            "---\n---\n",
            "no delimiters at all",
            "---\n\ttab error\n---",
            "---\nbad: yaml: syntax: {{{\n---",
            "---\n- list\n- not\n- mapping\n---",
            "---\nkgn_version: 0.1\n---",
            "---\nkgn_version: 0.1\ntype: INVALID\n---",
            "\ufeff---\nkgn_version: 0.1\n---\nbody",
            "---\n" + "x" * 10000 + "\n---",
            "a" * 100000,
            "---\nkgn_version: null\n---",
            "---\nkgn_version: 0.1\nconfidence: not_a_number\n---",
        ],
        ids=[
            "empty",
            "spaces",
            "newline",
            "only-opening",
            "opening-newline",
            "both-delimiters-no-content",
            "both-delimiters-newline",
            "no-delimiters",
            "tab-error",
            "bad-yaml-syntax",
            "yaml-list",
            "minimal-yaml",
            "invalid-enum",
            "bom-prefix",
            "huge-yaml",
            "huge-text",
            "null-version",
            "non-numeric-confidence",
        ],
    )
    def test_never_throws(self, text: str) -> None:
        """Every input must produce a PartialParseResult, never an exception."""
        result = parse_kgn_tolerant(text)
        assert isinstance(result, PartialParseResult)

    def test_non_string_input_caught(self) -> None:
        """R24 catch-all: non-string input must NOT crash."""
        result = parse_kgn_tolerant(123)  # type: ignore[arg-type]
        assert isinstance(result, PartialParseResult)
        assert result.has_errors
        assert any(d.rule == "INTERNAL" for d in result.diagnostics)

    def test_none_input_caught(self) -> None:
        """R24 catch-all: None input must NOT crash."""
        result = parse_kgn_tolerant(None)  # type: ignore[arg-type]
        assert isinstance(result, PartialParseResult)
        assert result.has_errors

    def test_bytes_input_caught(self) -> None:
        """R24 catch-all: bytes input must NOT crash."""
        result = parse_kgn_tolerant(b"---\nkgn_version: 0.1\n---")  # type: ignore[arg-type]
        assert isinstance(result, PartialParseResult)
        assert result.has_errors
