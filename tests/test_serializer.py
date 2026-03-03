"""Tests for kgn.serializer — .kgn/.kge serialization + roundtrip."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from kgn.models.edge import EdgeRecord
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.parser.kge_parser import parse_kge_text
from kgn.parser.kgn_parser import parse_kgn_text
from kgn.serializer.kge_serializer import serialize_edges
from kgn.serializer.kgn_serializer import serialize_node

# ── Fixtures ──────────────────────────────────────────────────────────

PROJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
AGENT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
NODE_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
NODE_ID_2 = uuid.UUID("44444444-4444-4444-4444-444444444444")

CREATED_AT = datetime(2026, 3, 2, 12, 0, 0, tzinfo=UTC)


def _make_node(
    *,
    node_id: uuid.UUID = NODE_ID,
    node_type: NodeType = NodeType.SPEC,
    title: str = "Test Node",
    status: NodeStatus = NodeStatus.ACTIVE,
    body_md: str = "## Context\n\nTest content.",
    tags: list[str] | None = None,
    confidence: float | None = None,
    created_at: datetime | None = CREATED_AT,
) -> NodeRecord:
    return NodeRecord(
        id=node_id,
        project_id=PROJECT_ID,
        type=node_type,
        status=status,
        title=title,
        body_md=body_md,
        tags=tags or [],
        confidence=confidence,
        created_by=AGENT_ID,
        created_at=created_at,
    )


def _make_edge(
    *,
    from_id: uuid.UUID = NODE_ID,
    to_id: uuid.UUID = NODE_ID_2,
    edge_type: EdgeType = EdgeType.IMPLEMENTS,
    note: str = "",
) -> EdgeRecord:
    return EdgeRecord(
        project_id=PROJECT_ID,
        from_node_id=from_id,
        to_node_id=to_id,
        type=edge_type,
        note=note,
        created_by=AGENT_ID,
        created_at=CREATED_AT,
    )


# ── KGN Serializer Tests ─────────────────────────────────────────────


class TestSerializeNode:
    """Unit tests for serialize_node()."""

    def test_basic_serialization(self):
        """Basic node serializes to valid .kgn text with front matter."""
        node = _make_node()
        text = serialize_node(node)

        assert text.startswith("---\n")
        assert "---" in text[4:]  # closing delimiter
        assert "type: SPEC" in text
        assert 'title: "\\uD14C\\uC2A4\\uD2B8' in text or "title:" in text
        assert "status: ACTIVE" in text

    def test_contains_required_fields(self):
        """All required front matter fields are present."""
        node = _make_node()
        text = serialize_node(node)
        parsed = parse_kgn_text(text)

        assert str(parsed.front_matter.id) == str(NODE_ID)
        assert parsed.front_matter.type == NodeType.SPEC
        assert parsed.front_matter.status == NodeStatus.ACTIVE
        assert str(parsed.front_matter.project_id) == str(PROJECT_ID)

    def test_body_preserved(self):
        """Markdown body is correctly serialized."""
        node = _make_node(body_md="## Section\n\nParagraph text.")
        text = serialize_node(node)
        parsed = parse_kgn_text(text)

        assert "## Section" in parsed.body
        assert "Paragraph text." in parsed.body

    def test_empty_body(self):
        """Node with empty body produces valid .kgn."""
        node = _make_node(body_md="")
        text = serialize_node(node)
        parsed = parse_kgn_text(text)

        assert parsed.body == ""

    def test_tags_serialized(self):
        """Tags list appears in front matter."""
        node = _make_node(tags=["auth", "security", "oauth"])
        text = serialize_node(node)
        parsed = parse_kgn_text(text)

        assert parsed.front_matter.tags == ["auth", "security", "oauth"]

    def test_no_tags(self):
        """Empty tags list is omitted from front matter."""
        node = _make_node(tags=[])
        text = serialize_node(node)

        # tags should not appear when empty
        assert "tags:" not in text

    def test_confidence_serialized(self):
        """Confidence value appears in front matter."""
        node = _make_node(confidence=0.85)
        text = serialize_node(node)
        parsed = parse_kgn_text(text)

        assert parsed.front_matter.confidence == pytest.approx(0.85)

    def test_confidence_none_omitted(self):
        """None confidence is not serialized."""
        node = _make_node(confidence=None)
        text = serialize_node(node)

        assert "confidence:" not in text

    def test_custom_agent_id(self):
        """Custom agent_id overrides created_by."""
        node = _make_node()
        text = serialize_node(node, agent_id="custom-agent")
        parsed = parse_kgn_text(text)

        assert parsed.front_matter.agent_id == "custom-agent"

    def test_custom_kgn_version(self):
        """Custom kgn_version is used."""
        node = _make_node()
        text = serialize_node(node, kgn_version="2.0")
        parsed = parse_kgn_text(text)

        assert parsed.front_matter.kgn_version == "2.0"

    def test_all_node_types(self):
        """Serialization works for every NodeType."""
        for nt in NodeType:
            node = _make_node(node_type=nt)
            text = serialize_node(node)
            parsed = parse_kgn_text(text)
            assert parsed.front_matter.type == nt

    def test_all_statuses(self):
        """Serialization works for every NodeStatus."""
        for ns in NodeStatus:
            node = _make_node(status=ns)
            text = serialize_node(node)
            parsed = parse_kgn_text(text)
            assert parsed.front_matter.status == ns

    def test_unicode_title(self):
        """Unicode characters in title are preserved."""
        node = _make_node(title="인증 모듈 설계 — OAuth 2.0 / PKCE")
        text = serialize_node(node)
        parsed = parse_kgn_text(text)

        assert parsed.front_matter.title == "인증 모듈 설계 — OAuth 2.0 / PKCE"

    def test_multiline_body(self):
        """Multiline markdown body with various formatting."""
        body = (
            "## Context\n\n"
            "First paragraph.\n\n"
            "## Content\n\n"
            "- Item 1\n"
            "- Item 2\n"
            "- Item 3\n\n"
            "## Rationale\n\n"
            "Explanation."
        )
        node = _make_node(body_md=body)
        text = serialize_node(node)
        parsed = parse_kgn_text(text)

        assert "## Context" in parsed.body
        assert "## Content" in parsed.body
        assert "- Item 1" in parsed.body
        assert "## Rationale" in parsed.body

    def test_no_created_by_uses_unknown(self):
        """Node without created_by uses 'unknown' as agent_id."""
        node = _make_node()
        node = node.model_copy(update={"created_by": None})
        text = serialize_node(node)
        parsed = parse_kgn_text(text)

        assert parsed.front_matter.agent_id == "unknown"


# ── KGN Roundtrip Tests ──────────────────────────────────────────────


class TestKgnRoundtrip:
    """Roundtrip invariant: serialize → parse → serialize = identical."""

    def _roundtrip(self, node: NodeRecord, **kwargs) -> None:
        """Helper: serialize → parse → reconstruct → serialize again."""
        text1 = serialize_node(node, **kwargs)
        parsed = parse_kgn_text(text1)

        # Reconstruct NodeRecord from parsed data
        agent_str = parsed.front_matter.agent_id
        try:
            created_by = uuid.UUID(agent_str)
        except ValueError:
            created_by = None

        reconstructed = NodeRecord(
            id=uuid.UUID(parsed.front_matter.id),
            project_id=uuid.UUID(parsed.front_matter.project_id),
            type=parsed.front_matter.type,
            status=parsed.front_matter.status,
            title=parsed.front_matter.title,
            body_md=parsed.body,
            tags=parsed.front_matter.tags,
            confidence=parsed.front_matter.confidence,
            created_by=created_by,
            created_at=parsed.front_matter.created_at,
        )

        agent_id_kwarg = kwargs.get("agent_id") or parsed.front_matter.agent_id
        text2 = serialize_node(
            reconstructed,
            kgn_version=parsed.front_matter.kgn_version,
            agent_id=agent_id_kwarg,
        )
        assert text1 == text2, f"Roundtrip mismatch:\n---TEXT1---\n{text1}\n---TEXT2---\n{text2}"

    def test_roundtrip_basic(self):
        self._roundtrip(_make_node())

    def test_roundtrip_with_tags(self):
        self._roundtrip(_make_node(tags=["a", "b", "c"]))

    def test_roundtrip_with_confidence(self):
        self._roundtrip(_make_node(confidence=0.75))

    def test_roundtrip_empty_body(self):
        self._roundtrip(_make_node(body_md=""))

    def test_roundtrip_all_fields(self):
        self._roundtrip(
            _make_node(
                title="Comprehensive Test — R14 Verify",
                tags=["test", "roundtrip"],
                confidence=0.99,
                body_md="## Test\n\nFull roundtrip verification.",
            )
        )

    def test_roundtrip_task_type(self):
        self._roundtrip(_make_node(node_type=NodeType.TASK, title="Task Work"))

    def test_roundtrip_custom_agent(self):
        self._roundtrip(_make_node(), agent_id="my-custom-agent")


# ── KGE Serializer Tests ─────────────────────────────────────────────


class TestSerializeEdges:
    """Unit tests for serialize_edges()."""

    def test_basic_serialization(self):
        """Single edge serializes to valid .kge text."""
        edge = _make_edge()
        text = serialize_edges([edge])

        assert text.startswith("---\n")
        assert "edges:" in text
        assert "IMPLEMENTS" in text

    def test_parses_back(self):
        """Serialized .kge text can be parsed back."""
        edge = _make_edge(note="test note")
        text = serialize_edges([edge])
        parsed = parse_kge_text(text)

        assert len(parsed.edges) == 1
        assert parsed.edges[0].type == EdgeType.IMPLEMENTS
        assert parsed.edges[0].note == "test note"

    def test_multiple_edges(self):
        """Multiple edges in one .kge file."""
        edges = [
            _make_edge(edge_type=EdgeType.IMPLEMENTS, note="implements relation"),
            _make_edge(
                from_id=NODE_ID_2,
                to_id=NODE_ID,
                edge_type=EdgeType.DEPENDS_ON,
                note="depends-on relation",
            ),
        ]
        text = serialize_edges(edges)
        parsed = parse_kge_text(text)

        assert len(parsed.edges) == 2
        assert parsed.edges[0].type == EdgeType.IMPLEMENTS
        assert parsed.edges[1].type == EdgeType.DEPENDS_ON

    def test_empty_note_omitted(self):
        """Empty note is not serialized."""
        edge = _make_edge(note="")
        text = serialize_edges([edge])

        # note should not appear when empty
        lines = text.split("\n")
        note_lines = [line for line in lines if "note:" in line]
        assert len(note_lines) == 0

    def test_empty_list_raises(self):
        """Empty edge list raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            serialize_edges([])

    def test_mixed_projects_raises(self):
        """Edges from different projects raise ValueError."""
        other_project = uuid.UUID("99999999-9999-9999-9999-999999999999")
        edges = [
            _make_edge(),
            EdgeRecord(
                project_id=other_project,
                from_node_id=NODE_ID,
                to_node_id=NODE_ID_2,
                type=EdgeType.IMPLEMENTS,
                created_by=AGENT_ID,
                created_at=CREATED_AT,
            ),
        ]
        with pytest.raises(ValueError, match="same project"):
            serialize_edges(edges)

    def test_custom_agent_and_project(self):
        """Custom project_id and agent_id override edge values."""
        custom_proj = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        edge = EdgeRecord(
            project_id=custom_proj,
            from_node_id=NODE_ID,
            to_node_id=NODE_ID_2,
            type=EdgeType.IMPLEMENTS,
            created_by=AGENT_ID,
            created_at=CREATED_AT,
        )
        text = serialize_edges(
            [edge],
            project_id=custom_proj,
            agent_id="special-agent",
        )
        parsed = parse_kge_text(text)

        assert parsed.project_id == str(custom_proj)
        assert parsed.agent_id == "special-agent"

    def test_all_edge_types(self):
        """Serialization works for every EdgeType."""
        for et in EdgeType:
            edge = _make_edge(edge_type=et)
            text = serialize_edges([edge])
            parsed = parse_kge_text(text)
            assert parsed.edges[0].type == et


# ── KGE Roundtrip Tests ──────────────────────────────────────────────


class TestKgeRoundtrip:
    """Roundtrip invariant: serialize → parse → serialize = identical."""

    def _roundtrip(self, edges: list[EdgeRecord], **kwargs) -> None:
        """Helper: serialize → parse → reconstruct → serialize again."""
        text1 = serialize_edges(edges, **kwargs)
        parsed = parse_kge_text(text1)

        # Reconstruct EdgeRecords from parsed data
        reconstructed = []
        for pe in parsed.edges:
            reconstructed.append(
                EdgeRecord(
                    project_id=uuid.UUID(parsed.project_id),
                    from_node_id=uuid.UUID(pe.from_node),
                    to_node_id=uuid.UUID(pe.to),
                    type=pe.type,
                    note=pe.note,
                    created_by=uuid.UUID(parsed.agent_id) if parsed.agent_id != "unknown" else None,
                    created_at=parsed.created_at,
                )
            )

        agent_kwarg = kwargs.get("agent_id") or parsed.agent_id
        project_kwarg = kwargs.get("project_id") or uuid.UUID(parsed.project_id)
        text2 = serialize_edges(
            reconstructed,
            kgn_version=parsed.kgn_version,
            agent_id=agent_kwarg,
            project_id=project_kwarg,
        )
        assert text1 == text2, f"Roundtrip mismatch:\n---TEXT1---\n{text1}\n---TEXT2---\n{text2}"

    def test_roundtrip_single_edge(self):
        self._roundtrip([_make_edge(note="test")])

    def test_roundtrip_multiple_edges(self):
        self._roundtrip(
            [
                _make_edge(edge_type=EdgeType.IMPLEMENTS, note="implements"),
                _make_edge(
                    from_id=NODE_ID_2,
                    to_id=NODE_ID,
                    edge_type=EdgeType.DEPENDS_ON,
                    note="depends",
                ),
            ]
        )

    def test_roundtrip_no_notes(self):
        self._roundtrip([_make_edge(note="")])

    def test_roundtrip_all_edge_types(self):
        """Roundtrip for each edge type individually."""
        for et in EdgeType:
            self._roundtrip([_make_edge(edge_type=et, note=f"{et} edge")])
