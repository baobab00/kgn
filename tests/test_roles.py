"""Tests for kgn.orchestration.roles — RoleGuard permission system.

Covers:
- AgentRole enum validation
- RoleGuard.can_create_node / check_node_create for all roles × node types
- RoleGuard.can_create_edge / check_edge_create for all roles × edge types
- RoleGuard.can_checkout_task / check_task_checkout for all roles
- get_permitted_node_types / get_permitted_edge_types helpers
- Repository: get_agent_role, set_agent_role, list_agents, get_agent_by_key
- Backward compatibility: default admin role
"""

from __future__ import annotations

import uuid

import pytest

from kgn.errors import KgnError, KgnErrorCode
from kgn.models.enums import AgentRole, EdgeType, NodeType
from kgn.orchestration.roles import RoleGuard

# ── AgentRole enum ─────────────────────────────────────────────────────


class TestAgentRoleEnum:
    """Test AgentRole has all expected values."""

    def test_has_five_roles(self):
        assert len(AgentRole) == 5

    def test_role_values(self):
        assert AgentRole.GENESIS.value == "genesis"
        assert AgentRole.WORKER.value == "worker"
        assert AgentRole.REVIEWER.value == "reviewer"
        assert AgentRole.INDEXER.value == "indexer"
        assert AgentRole.ADMIN.value == "admin"

    def test_from_string(self):
        assert AgentRole("admin") == AgentRole.ADMIN
        assert AgentRole("worker") == AgentRole.WORKER

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError):
            AgentRole("unknown")


# ── RoleGuard: Node creation ──────────────────────────────────────────


class TestRoleGuardNodeCreate:
    """Test node creation permissions for each role."""

    # Genesis can create: GOAL, SPEC, ARCH, CONSTRAINT, ASSUMPTION
    @pytest.mark.parametrize(
        "node_type",
        [NodeType.GOAL, NodeType.SPEC, NodeType.ARCH, NodeType.CONSTRAINT, NodeType.ASSUMPTION],
    )
    def test_genesis_allowed_nodes(self, node_type):
        result = RoleGuard.can_create_node(AgentRole.GENESIS, node_type)
        assert result.allowed is True
        assert result.detail == ""

    @pytest.mark.parametrize(
        "node_type",
        [NodeType.LOGIC, NodeType.DECISION, NodeType.TASK, NodeType.ISSUE, NodeType.SUMMARY],
    )
    def test_genesis_denied_nodes(self, node_type):
        result = RoleGuard.can_create_node(AgentRole.GENESIS, node_type)
        assert result.allowed is False
        assert "genesis" in result.detail

    # Worker can create: LOGIC, DECISION
    @pytest.mark.parametrize("node_type", [NodeType.LOGIC, NodeType.DECISION])
    def test_worker_allowed_nodes(self, node_type):
        result = RoleGuard.can_create_node(AgentRole.WORKER, node_type)
        assert result.allowed is True

    @pytest.mark.parametrize(
        "node_type",
        [NodeType.GOAL, NodeType.SPEC, NodeType.ARCH, NodeType.TASK, NodeType.ISSUE],
    )
    def test_worker_denied_nodes(self, node_type):
        result = RoleGuard.can_create_node(AgentRole.WORKER, node_type)
        assert result.allowed is False

    # Reviewer can create: ISSUE, SUMMARY
    @pytest.mark.parametrize("node_type", [NodeType.ISSUE, NodeType.SUMMARY])
    def test_reviewer_allowed_nodes(self, node_type):
        result = RoleGuard.can_create_node(AgentRole.REVIEWER, node_type)
        assert result.allowed is True

    @pytest.mark.parametrize(
        "node_type",
        [NodeType.GOAL, NodeType.SPEC, NodeType.LOGIC, NodeType.TASK],
    )
    def test_reviewer_denied_nodes(self, node_type):
        result = RoleGuard.can_create_node(AgentRole.REVIEWER, node_type)
        assert result.allowed is False

    # Indexer can create: SUMMARY only
    def test_indexer_allowed_summary(self):
        result = RoleGuard.can_create_node(AgentRole.INDEXER, NodeType.SUMMARY)
        assert result.allowed is True

    @pytest.mark.parametrize(
        "node_type",
        [NodeType.GOAL, NodeType.SPEC, NodeType.LOGIC, NodeType.ISSUE, NodeType.TASK],
    )
    def test_indexer_denied_nodes(self, node_type):
        result = RoleGuard.can_create_node(AgentRole.INDEXER, node_type)
        assert result.allowed is False

    # Admin can create everything
    @pytest.mark.parametrize("node_type", list(NodeType))
    def test_admin_allowed_all(self, node_type):
        result = RoleGuard.can_create_node(AgentRole.ADMIN, node_type)
        assert result.allowed is True


class TestRoleGuardNodeCreateRaising:
    """Test that check_node_create raises KgnError on deny."""

    def test_denied_raises_kgn_error(self):
        with pytest.raises(KgnError) as exc_info:
            RoleGuard.check_node_create(AgentRole.WORKER, NodeType.GOAL)
        assert exc_info.value.code == KgnErrorCode.ROLE_PERMISSION_DENIED
        assert "worker" in str(exc_info.value)

    def test_allowed_does_not_raise(self):
        # Should not raise
        RoleGuard.check_node_create(AgentRole.ADMIN, NodeType.GOAL)
        RoleGuard.check_node_create(AgentRole.GENESIS, NodeType.SPEC)


# ── RoleGuard: Edge creation ──────────────────────────────────────────


class TestRoleGuardEdgeCreate:
    """Test edge creation permissions for each role."""

    # Genesis and Worker can create all edge types
    @pytest.mark.parametrize("edge_type", list(EdgeType))
    def test_genesis_allowed_all_edges(self, edge_type):
        result = RoleGuard.can_create_edge(AgentRole.GENESIS, edge_type)
        assert result.allowed is True

    @pytest.mark.parametrize("edge_type", list(EdgeType))
    def test_worker_allowed_all_edges(self, edge_type):
        result = RoleGuard.can_create_edge(AgentRole.WORKER, edge_type)
        assert result.allowed is True

    # Reviewer can create: CONTRADICTS, RESOLVES, DERIVED_FROM
    @pytest.mark.parametrize(
        "edge_type",
        [EdgeType.CONTRADICTS, EdgeType.RESOLVES, EdgeType.DERIVED_FROM],
    )
    def test_reviewer_allowed_edges(self, edge_type):
        result = RoleGuard.can_create_edge(AgentRole.REVIEWER, edge_type)
        assert result.allowed is True

    @pytest.mark.parametrize(
        "edge_type",
        [EdgeType.DEPENDS_ON, EdgeType.IMPLEMENTS, EdgeType.SUPERSEDES, EdgeType.CONSTRAINED_BY],
    )
    def test_reviewer_denied_edges(self, edge_type):
        result = RoleGuard.can_create_edge(AgentRole.REVIEWER, edge_type)
        assert result.allowed is False

    # Indexer cannot create any edges
    @pytest.mark.parametrize("edge_type", list(EdgeType))
    def test_indexer_denied_all_edges(self, edge_type):
        result = RoleGuard.can_create_edge(AgentRole.INDEXER, edge_type)
        assert result.allowed is False

    # Admin can create all edge types
    @pytest.mark.parametrize("edge_type", list(EdgeType))
    def test_admin_allowed_all_edges(self, edge_type):
        result = RoleGuard.can_create_edge(AgentRole.ADMIN, edge_type)
        assert result.allowed is True

    def test_denied_raises_kgn_error(self):
        with pytest.raises(KgnError) as exc_info:
            RoleGuard.check_edge_create(AgentRole.INDEXER, EdgeType.DEPENDS_ON)
        assert exc_info.value.code == KgnErrorCode.ROLE_PERMISSION_DENIED


# ── RoleGuard: Task checkout ──────────────────────────────────────────


class TestRoleGuardTaskCheckout:
    """Test task checkout permissions."""

    def test_worker_can_checkout(self):
        result = RoleGuard.can_checkout_task(AgentRole.WORKER)
        assert result.allowed is True

    def test_admin_can_checkout(self):
        result = RoleGuard.can_checkout_task(AgentRole.ADMIN)
        assert result.allowed is True

    @pytest.mark.parametrize(
        "role",
        [AgentRole.GENESIS, AgentRole.REVIEWER, AgentRole.INDEXER],
    )
    def test_non_worker_cannot_checkout(self, role):
        result = RoleGuard.can_checkout_task(role)
        assert result.allowed is False

    def test_denied_raises_kgn_error(self):
        with pytest.raises(KgnError) as exc_info:
            RoleGuard.check_task_checkout(AgentRole.GENESIS)
        assert exc_info.value.code == KgnErrorCode.ROLE_PERMISSION_DENIED

    def test_allowed_does_not_raise(self):
        RoleGuard.check_task_checkout(AgentRole.WORKER)
        RoleGuard.check_task_checkout(AgentRole.ADMIN)


# ── RoleGuard: Permission helpers ──────────────────────────────────────


class TestRoleGuardHelpers:
    """Test get_permitted_* helpers."""

    def test_admin_has_all_node_types(self):
        types = RoleGuard.get_permitted_node_types(AgentRole.ADMIN)
        assert types == frozenset(NodeType)

    def test_worker_node_types(self):
        types = RoleGuard.get_permitted_node_types(AgentRole.WORKER)
        assert types == frozenset({NodeType.LOGIC, NodeType.DECISION})

    def test_indexer_edge_types_empty(self):
        types = RoleGuard.get_permitted_edge_types(AgentRole.INDEXER)
        assert len(types) == 0

    def test_genesis_node_count(self):
        types = RoleGuard.get_permitted_node_types(AgentRole.GENESIS)
        assert len(types) == 5


# ── Repository: Agent role methods ─────────────────────────────────────


class TestRepositoryAgentRole:
    """Test repository methods for agent role management.

    These tests require a running PostgreSQL instance.
    """

    def test_default_role_is_admin(self, repo, project_id):
        """New agents get 'admin' role by default (R16)."""
        agent_id = repo.get_or_create_agent(project_id, "test-default")
        role = repo.get_agent_role(agent_id)
        assert role == "admin"

    def test_create_agent_with_explicit_role(self, repo, project_id):
        """Agent created with explicit role."""
        agent_id = repo.get_or_create_agent(project_id, "test-genesis", role="genesis")
        role = repo.get_agent_role(agent_id)
        assert role == "genesis"

    def test_set_agent_role(self, repo, project_id):
        """Can change an agent's role."""
        agent_id = repo.get_or_create_agent(project_id, "test-roleset")
        assert repo.get_agent_role(agent_id) == "admin"

        updated = repo.set_agent_role(agent_id, "reviewer")
        assert updated is True
        assert repo.get_agent_role(agent_id) == "reviewer"

    def test_set_role_nonexistent_agent(self, repo):
        """set_agent_role returns False for unknown agent."""
        fake_id = uuid.uuid4()
        updated = repo.set_agent_role(fake_id, "admin")
        assert updated is False

    def test_get_role_nonexistent_agent(self, repo):
        """get_agent_role returns None for unknown agent."""
        fake_id = uuid.uuid4()
        role = repo.get_agent_role(fake_id)
        assert role is None

    def test_list_agents(self, repo, project_id):
        """list_agents returns all agents for the project."""
        repo.get_or_create_agent(project_id, "agent-a", role="worker")
        repo.get_or_create_agent(project_id, "agent-b", role="genesis")
        agents = repo.list_agents(project_id)
        keys = [a["agent_key"] for a in agents]
        assert "agent-a" in keys
        assert "agent-b" in keys

    def test_list_agents_has_role(self, repo, project_id):
        """list_agents includes role info."""
        repo.get_or_create_agent(project_id, "agent-c", role="reviewer")
        agents = repo.list_agents(project_id)
        agent_c = next(a for a in agents if a["agent_key"] == "agent-c")
        assert str(agent_c["role"]) == "reviewer"

    def test_get_agent_by_key(self, repo, project_id):
        """get_agent_by_key returns agent dict."""
        repo.get_or_create_agent(project_id, "agent-d", role="indexer")
        agent = repo.get_agent_by_key(project_id, "agent-d")
        assert agent is not None
        assert agent["agent_key"] == "agent-d"
        assert str(agent["role"]) == "indexer"

    def test_get_agent_by_key_not_found(self, repo, project_id):
        """get_agent_by_key returns None for unknown agent."""
        agent = repo.get_agent_by_key(project_id, "nonexistent")
        assert agent is None

    def test_backward_compat_existing_agents(self, repo, project_id):
        """Existing get_or_create_agent still works with default role."""
        aid1 = repo.get_or_create_agent(project_id, "compat-test")
        aid2 = repo.get_or_create_agent(project_id, "compat-test")
        assert aid1 == aid2
        assert repo.get_agent_role(aid1) == "admin"
