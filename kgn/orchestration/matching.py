"""MatchingService — role-based agent matching for task assignment.

Finds eligible agents for a task based on the task node's ``role:``
tag, which is set by the WorkflowEngine during task creation.

Rule compliance:
- R1  — no SQL outside repository
- R12 — service-layer logic only
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import structlog

from kgn.db.repository import KgnRepository
from kgn.models.enums import AgentRole

log = structlog.get_logger()


# ── Result types ───────────────────────────────────────────────────────


@dataclass
class AgentCandidate:
    """An eligible agent for a task."""

    agent_id: uuid.UUID
    agent_key: str
    role: str


@dataclass
class MatchResult:
    """Result of agent matching for a task."""

    task_node_id: uuid.UUID
    required_role: str | None
    candidates: list[AgentCandidate] = field(default_factory=list)

    @property
    def has_candidates(self) -> bool:
        return len(self.candidates) > 0

    @property
    def single_candidate(self) -> AgentCandidate | None:
        """Return the sole candidate if exactly one, else None."""
        if len(self.candidates) == 1:
            return self.candidates[0]
        return None


# ── Service ────────────────────────────────────────────────────────────


class MatchingService:
    """Role-based agent matching for task assignment.

    The service extracts the ``role:<name>`` tag from a task node
    and finds agents in the project with the matching role.

    Usage::

        svc = MatchingService(repo)
        result = svc.find_candidates(task_node_id, project_id)
        if result.single_candidate:
            # auto-assign
    """

    def __init__(self, repo: KgnRepository) -> None:
        self._repo = repo

    def find_candidates(
        self,
        task_node_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> MatchResult:
        """Find agents matching the required role for a task.

        Steps:
        1. Read the task node to extract the ``role:<name>`` tag.
        2. Query agents in the project with the matching role.
        3. Admin role agents are always included as fallback candidates.

        Args:
            task_node_id: The node ID of the task.
            project_id: Project scope.

        Returns:
            MatchResult with the required role and candidate agents.
        """
        node = self._repo.get_node_by_id(task_node_id)
        if node is None:
            return MatchResult(task_node_id=task_node_id, required_role=None)

        # Extract role from tags (e.g., "role:worker")
        required_role = self._extract_role(node.tags)

        result = MatchResult(
            task_node_id=task_node_id,
            required_role=required_role,
        )

        if required_role is None:
            log.debug(
                "matching_no_role_tag",
                node_id=str(task_node_id),
            )
            return result

        # Get all agents in the project
        agents = self._repo.list_agents(project_id)

        for agent in agents:
            agent_role = str(agent.get("role", "admin"))
            # Match if: exact role match OR agent is admin
            if agent_role == required_role or agent_role == AgentRole.ADMIN:
                result.candidates.append(
                    AgentCandidate(
                        agent_id=agent["id"],
                        agent_key=agent["agent_key"],
                        role=agent_role,
                    )
                )

        log.info(
            "matching_complete",
            node_id=str(task_node_id),
            required_role=required_role,
            candidates=len(result.candidates),
        )
        return result

    @staticmethod
    def extract_role_from_node(tags: list[str]) -> str | None:
        """Extract the role from node tags (public static utility).

        Looks for tags matching ``role:<name>`` pattern.
        Returns the role name or None.
        """
        return MatchingService._extract_role(tags)

    @staticmethod
    def _extract_role(tags: list[str]) -> str | None:
        """Extract ``role:<name>`` from a list of tags."""
        for tag in tags:
            if tag.startswith("role:"):
                return tag[5:]  # strip "role:" prefix
        return None
