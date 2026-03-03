"""RoleGuard — role-based permission enforcement for multi-agent orchestration.

Each ``AgentRole`` has a set of permitted node types for creation and
permitted actions.  ``RoleGuard.check()`` validates an agent's permission
before any write operation.

Rule R16: agents without explicit role get ``admin`` (full access).
"""

from __future__ import annotations

from dataclasses import dataclass

from kgn.errors import KgnError, KgnErrorCode
from kgn.models.enums import AgentRole, EdgeType, NodeType

# ── Permission Matrix ──────────────────────────────────────────────────

# Node types each role is allowed to CREATE
_ROLE_CREATE_NODES: dict[AgentRole, frozenset[NodeType]] = {
    AgentRole.GENESIS: frozenset(
        {
            NodeType.GOAL,
            NodeType.SPEC,
            NodeType.ARCH,
            NodeType.CONSTRAINT,
            NodeType.ASSUMPTION,
        }
    ),
    AgentRole.WORKER: frozenset(
        {
            NodeType.LOGIC,
            NodeType.DECISION,
        }
    ),
    AgentRole.REVIEWER: frozenset(
        {
            NodeType.ISSUE,
            NodeType.SUMMARY,
        }
    ),
    AgentRole.INDEXER: frozenset(
        {
            NodeType.SUMMARY,
        }
    ),
    AgentRole.ADMIN: frozenset(NodeType),  # all node types
}

# Edge types each role is allowed to CREATE
_ROLE_CREATE_EDGES: dict[AgentRole, frozenset[EdgeType]] = {
    AgentRole.GENESIS: frozenset(EdgeType),  # all edge types
    AgentRole.WORKER: frozenset(EdgeType),
    AgentRole.REVIEWER: frozenset(
        {
            EdgeType.CONTRADICTS,
            EdgeType.RESOLVES,
            EdgeType.DERIVED_FROM,
        }
    ),
    AgentRole.INDEXER: frozenset(),  # no edges
    AgentRole.ADMIN: frozenset(EdgeType),
}

# Can this role check out tasks?
_ROLE_CAN_CHECKOUT: dict[AgentRole, bool] = {
    AgentRole.GENESIS: False,
    AgentRole.WORKER: True,
    AgentRole.REVIEWER: False,
    AgentRole.INDEXER: False,
    AgentRole.ADMIN: True,
}


# ── Result type ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoleCheckResult:
    """Result of a role permission check."""

    allowed: bool
    role: AgentRole
    action: str
    detail: str = ""


# ── RoleGuard ──────────────────────────────────────────────────────────


class RoleGuard:
    """Validate agent permissions based on their role.

    Usage::

        guard = RoleGuard()
        guard.check_node_create(role, node_type)  # raises KgnError on deny
        guard.check_edge_create(role, edge_type)   # raises KgnError on deny
        guard.check_task_checkout(role)             # raises KgnError on deny
    """

    @staticmethod
    def can_create_node(role: AgentRole, node_type: NodeType) -> RoleCheckResult:
        """Check if role can create the given node type (non-raising)."""
        allowed_types = _ROLE_CREATE_NODES.get(role, frozenset())
        allowed = node_type in allowed_types
        return RoleCheckResult(
            allowed=allowed,
            role=role,
            action=f"create_node:{node_type.value}",
            detail=""
            if allowed
            else (
                f"Role '{role.value}' cannot create {node_type.value} nodes. "
                f"Allowed types: {sorted(t.value for t in allowed_types)}"
            ),
        )

    @staticmethod
    def can_create_edge(role: AgentRole, edge_type: EdgeType) -> RoleCheckResult:
        """Check if role can create the given edge type (non-raising)."""
        allowed_types = _ROLE_CREATE_EDGES.get(role, frozenset())
        allowed = edge_type in allowed_types
        return RoleCheckResult(
            allowed=allowed,
            role=role,
            action=f"create_edge:{edge_type.value}",
            detail=""
            if allowed
            else (
                f"Role '{role.value}' cannot create {edge_type.value} edges. "
                f"Allowed types: {sorted(t.value for t in allowed_types)}"
            ),
        )

    @staticmethod
    def can_checkout_task(role: AgentRole) -> RoleCheckResult:
        """Check if role can checkout tasks (non-raising)."""
        allowed = _ROLE_CAN_CHECKOUT.get(role, False)
        return RoleCheckResult(
            allowed=allowed,
            role=role,
            action="task_checkout",
            detail=""
            if allowed
            else (
                f"Role '{role.value}' cannot checkout tasks. "
                f"Only 'worker' and 'admin' roles can checkout tasks."
            ),
        )

    @staticmethod
    def check_node_create(role: AgentRole, node_type: NodeType) -> None:
        """Raise ``KgnError`` if role cannot create the node type."""
        result = RoleGuard.can_create_node(role, node_type)
        if not result.allowed:
            raise KgnError(
                code=KgnErrorCode.ROLE_PERMISSION_DENIED,
                message=result.detail,
            )

    @staticmethod
    def check_edge_create(role: AgentRole, edge_type: EdgeType) -> None:
        """Raise ``KgnError`` if role cannot create the edge type."""
        result = RoleGuard.can_create_edge(role, edge_type)
        if not result.allowed:
            raise KgnError(
                code=KgnErrorCode.ROLE_PERMISSION_DENIED,
                message=result.detail,
            )

    @staticmethod
    def check_task_checkout(role: AgentRole) -> None:
        """Raise ``KgnError`` if role cannot checkout tasks."""
        result = RoleGuard.can_checkout_task(role)
        if not result.allowed:
            raise KgnError(
                code=KgnErrorCode.ROLE_PERMISSION_DENIED,
                message=result.detail,
            )

    @staticmethod
    def get_permitted_node_types(role: AgentRole) -> frozenset[NodeType]:
        """Return the set of node types this role can create."""
        return _ROLE_CREATE_NODES.get(role, frozenset())

    @staticmethod
    def get_permitted_edge_types(role: AgentRole) -> frozenset[EdgeType]:
        """Return the set of edge types this role can create."""
        return _ROLE_CREATE_EDGES.get(role, frozenset())
