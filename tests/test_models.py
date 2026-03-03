"""Tests for Pydantic models — enums, NodeFrontMatter, EdgeFrontMatter."""

import pytest
from pydantic import ValidationError

from kgn.models import (
    EdgeEntry,
    EdgeFrontMatter,
    EdgeType,
    NodeFrontMatter,
    NodeStatus,
    NodeType,
)

# ============================================================
# ENUM Tests
# ============================================================


class TestNodeType:
    """NodeType ENUM tests."""

    def test_valid_values(self) -> None:
        assert NodeType("GOAL") == NodeType.GOAL
        assert NodeType("SPEC") == NodeType.SPEC
        assert NodeType("TASK") == NodeType.TASK

    def test_all_10_types_exist(self) -> None:
        assert len(NodeType) == 10

    def test_invalid_value(self) -> None:
        with pytest.raises(ValueError):
            NodeType("INVALID")


class TestNodeStatus:
    """NodeStatus ENUM tests."""

    def test_valid_values(self) -> None:
        assert NodeStatus("ACTIVE") == NodeStatus.ACTIVE
        assert NodeStatus("ARCHIVED") == NodeStatus.ARCHIVED

    def test_all_4_statuses_exist(self) -> None:
        assert len(NodeStatus) == 4

    def test_invalid_value(self) -> None:
        with pytest.raises(ValueError):
            NodeStatus("UNKNOWN")


class TestEdgeType:
    """EdgeType ENUM tests."""

    def test_valid_values(self) -> None:
        assert EdgeType("DEPENDS_ON") == EdgeType.DEPENDS_ON
        assert EdgeType("SUPERSEDES") == EdgeType.SUPERSEDES

    def test_all_7_types_exist(self) -> None:
        assert len(EdgeType) == 7

    def test_invalid_value(self) -> None:
        with pytest.raises(ValueError):
            EdgeType("INVALID_EDGE")


# ============================================================
# NodeFrontMatter Tests
# ============================================================

_VALID_NODE_DATA = {
    "kgn_version": "0.1",
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "type": "SPEC",
    "title": "POST /auth/login endpoint spec",
    "status": "ACTIVE",
    "project_id": "proj-alpha",
    "agent_id": "worker-agent-03",
    "created_at": "2026-02-27T10:00:00+09:00",
}


class TestNodeFrontMatter:
    """NodeFrontMatter model tests."""

    def test_valid_full(self) -> None:
        data = {
            **_VALID_NODE_DATA,
            "supersedes": "450e8400-e29b-41d4-a716-446655440001",
            "tags": ["auth", "security"],
            "confidence": 0.92,
        }
        node = NodeFrontMatter(**data)
        assert node.type == NodeType.SPEC
        assert node.status == NodeStatus.ACTIVE
        assert node.confidence == 0.92
        assert node.tags == ["auth", "security"]

    def test_valid_minimal(self) -> None:
        node = NodeFrontMatter(**_VALID_NODE_DATA)
        assert node.supersedes is None
        assert node.tags == []
        assert node.confidence is None

    def test_new_id_format(self) -> None:
        data = {**_VALID_NODE_DATA, "id": "new:auth-login-spec"}
        node = NodeFrontMatter(**data)
        assert node.id == "new:auth-login-spec"

    def test_missing_required_title(self) -> None:
        data = {k: v for k, v in _VALID_NODE_DATA.items() if k != "title"}
        with pytest.raises(ValidationError) as exc_info:
            NodeFrontMatter(**data)
        assert "title" in str(exc_info.value)

    def test_missing_required_type(self) -> None:
        data = {k: v for k, v in _VALID_NODE_DATA.items() if k != "type"}
        with pytest.raises(ValidationError) as exc_info:
            NodeFrontMatter(**data)
        assert "type" in str(exc_info.value)

    def test_missing_required_status(self) -> None:
        data = {k: v for k, v in _VALID_NODE_DATA.items() if k != "status"}
        with pytest.raises(ValidationError) as exc_info:
            NodeFrontMatter(**data)
        assert "status" in str(exc_info.value)

    def test_missing_required_id(self) -> None:
        data = {k: v for k, v in _VALID_NODE_DATA.items() if k != "id"}
        with pytest.raises(ValidationError):
            NodeFrontMatter(**data)

    def test_missing_required_project_id(self) -> None:
        data = {k: v for k, v in _VALID_NODE_DATA.items() if k != "project_id"}
        with pytest.raises(ValidationError):
            NodeFrontMatter(**data)

    def test_missing_required_agent_id(self) -> None:
        data = {k: v for k, v in _VALID_NODE_DATA.items() if k != "agent_id"}
        with pytest.raises(ValidationError):
            NodeFrontMatter(**data)

    def test_missing_created_at_uses_default(self) -> None:
        data = {k: v for k, v in _VALID_NODE_DATA.items() if k != "created_at"}
        node = NodeFrontMatter(**data)
        assert node.created_at is not None

    def test_invalid_type_enum(self) -> None:
        data = {**_VALID_NODE_DATA, "type": "INVALID"}
        with pytest.raises(ValidationError) as exc_info:
            NodeFrontMatter(**data)
        assert "type" in str(exc_info.value)

    def test_invalid_status_enum(self) -> None:
        data = {**_VALID_NODE_DATA, "status": "UNKNOWN"}
        with pytest.raises(ValidationError) as exc_info:
            NodeFrontMatter(**data)
        assert "status" in str(exc_info.value)

    def test_confidence_too_high(self) -> None:
        data = {**_VALID_NODE_DATA, "confidence": 1.5}
        with pytest.raises(ValidationError) as exc_info:
            NodeFrontMatter(**data)
        assert "confidence" in str(exc_info.value)

    def test_confidence_too_low(self) -> None:
        data = {**_VALID_NODE_DATA, "confidence": -0.1}
        with pytest.raises(ValidationError) as exc_info:
            NodeFrontMatter(**data)
        assert "confidence" in str(exc_info.value)

    def test_confidence_boundary_zero(self) -> None:
        data = {**_VALID_NODE_DATA, "confidence": 0.0}
        node = NodeFrontMatter(**data)
        assert node.confidence == 0.0

    def test_confidence_boundary_one(self) -> None:
        data = {**_VALID_NODE_DATA, "confidence": 1.0}
        node = NodeFrontMatter(**data)
        assert node.confidence == 1.0

    def test_all_node_types(self) -> None:
        for nt in NodeType:
            data = {**_VALID_NODE_DATA, "type": nt.value}
            node = NodeFrontMatter(**data)
            assert node.type == nt

    def test_all_node_statuses(self) -> None:
        for ns in NodeStatus:
            data = {**_VALID_NODE_DATA, "status": ns.value}
            node = NodeFrontMatter(**data)
            assert node.status == ns

    def test_created_at_parsed_as_datetime(self) -> None:
        from datetime import datetime

        node = NodeFrontMatter(**_VALID_NODE_DATA)
        assert isinstance(node.created_at, datetime)


# ============================================================
# EdgeFrontMatter Tests
# ============================================================

_VALID_EDGE_DATA = {
    "kgn_version": "0.1",
    "project_id": "proj-alpha",
    "agent_id": "worker-agent-03",
    "created_at": "2026-02-27T10:30:00+09:00",
    "edges": [
        {
            "from": "550e8400-e29b-41d4-a716-446655440000",
            "to": "450e8400-e29b-41d4-a716-446655440001",
            "type": "IMPLEMENTS",
            "note": "this SPEC implements the GOAL",
        },
        {
            "from": "550e8400-e29b-41d4-a716-446655440000",
            "to": "350e8400-e29b-41d4-a716-446655440002",
            "type": "DEPENDS_ON",
        },
    ],
}


class TestEdgeFrontMatter:
    """EdgeFrontMatter model tests."""

    def test_valid_full(self) -> None:
        efm = EdgeFrontMatter(**_VALID_EDGE_DATA)
        assert len(efm.edges) == 2
        assert efm.edges[0].type == EdgeType.IMPLEMENTS
        assert efm.edges[0].note == "this SPEC implements the GOAL"
        assert efm.edges[1].note == ""

    def test_missing_edges(self) -> None:
        data = {k: v for k, v in _VALID_EDGE_DATA.items() if k != "edges"}
        with pytest.raises(ValidationError):
            EdgeFrontMatter(**data)

    def test_empty_edges_list(self) -> None:
        data = {**_VALID_EDGE_DATA, "edges": []}
        efm = EdgeFrontMatter(**data)
        assert efm.edges == []

    def test_invalid_edge_type(self) -> None:
        data = {
            **_VALID_EDGE_DATA,
            "edges": [
                {
                    "from": "550e8400-e29b-41d4-a716-446655440000",
                    "to": "450e8400-e29b-41d4-a716-446655440001",
                    "type": "INVALID_EDGE",
                }
            ],
        }
        with pytest.raises(ValidationError):
            EdgeFrontMatter(**data)

    def test_edge_alias_from(self) -> None:
        """'from' is a Python keyword, aliased to 'from_node'."""
        edge = EdgeEntry(
            **{
                "from": "aaa",
                "to": "bbb",
                "type": "DEPENDS_ON",
            }
        )
        assert edge.from_node == "aaa"

    def test_edge_new_id_format(self) -> None:
        data = {
            **_VALID_EDGE_DATA,
            "edges": [
                {
                    "from": "new:auth-login-spec",
                    "to": "goal-social-login-uuid",
                    "type": "IMPLEMENTS",
                }
            ],
        }
        efm = EdgeFrontMatter(**data)
        assert efm.edges[0].from_node == "new:auth-login-spec"

    def test_all_edge_types(self) -> None:
        for et in EdgeType:
            data = {
                **_VALID_EDGE_DATA,
                "edges": [
                    {
                        "from": "aaa",
                        "to": "bbb",
                        "type": et.value,
                    }
                ],
            }
            efm = EdgeFrontMatter(**data)
            assert efm.edges[0].type == et

    def test_created_at_parsed(self) -> None:
        from datetime import datetime

        efm = EdgeFrontMatter(**_VALID_EDGE_DATA)
        assert isinstance(efm.created_at, datetime)
