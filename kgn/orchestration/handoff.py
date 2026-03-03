"""HandoffService — context propagation between task transitions.

When a task completes, the HandoffService propagates relevant context
(a summary of the completed task's result) into the body of downstream
dependent tasks. This ensures agents picking up subsequent tasks have
the necessary context from prior work.

Rule compliance:
- R1  — no SQL outside repository
- R12 — service-layer logic only, no MCP-specific code
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import structlog

from kgn.db.repository import KgnRepository

log = structlog.get_logger()

# ── Result types ───────────────────────────────────────────────────────

HANDOFF_SECTION_HEADER = "## Handoff Context"


@dataclass
class HandoffEntry:
    """A single handoff context entry injected into a dependent task."""

    dependent_task_node_id: uuid.UUID
    dependent_title: str
    from_task_node_id: uuid.UUID
    from_title: str


@dataclass
class HandoffResult:
    """Aggregated result of context propagation."""

    completed_task_node_id: uuid.UUID
    entries: list[HandoffEntry] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.entries)


# ── Service ────────────────────────────────────────────────────────────


class HandoffService:
    """Propagate context from completed tasks to their dependents.

    When a task completes, this service:
    1. Finds all tasks that depend on the completed task (via DEPENDS_ON edges).
    2. Builds a handoff context summary from the completed task's node.
    3. Appends the summary to each dependent task's ``body_md``.

    Usage::

        svc = HandoffService(repo)
        result = svc.propagate_context(completed_task_node_id, project_id)
    """

    def __init__(self, repo: KgnRepository) -> None:
        self._repo = repo

    def propagate_context(
        self,
        completed_task_node_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> HandoffResult:
        """Propagate context from a completed task to its dependents.

        Args:
            completed_task_node_id: The *node* ID of the completed task.
            project_id: Project scope.

        Returns:
            HandoffResult with entries for each injected context.
        """
        result = HandoffResult(completed_task_node_id=completed_task_node_id)

        # 1. Get the completed task's node
        completed_node = self._repo.get_node_by_id(completed_task_node_id)
        if completed_node is None:
            log.warning(
                "handoff_skip_missing_node",
                node_id=str(completed_task_node_id),
            )
            return result

        # 2. Find dependent tasks — edges where to_node_id = completed_task_node_id
        # and from_node_id is a node that DEPENDS_ON the completed node
        dependents = self._repo.find_blocked_dependents(completed_task_node_id, project_id)

        # Also find READY dependents (they may have been unblocked already
        # or were never blocked because they had no TASK-type dependencies)
        ready_dependents = self._find_ready_dependents(completed_task_node_id, project_id)

        all_dependent_node_ids = {d.task_node_id for d in dependents}
        all_dependent_node_ids.update(d.task_node_id for d in ready_dependents)

        if not all_dependent_node_ids:
            log.debug(
                "handoff_no_dependents",
                node_id=str(completed_task_node_id),
            )
            return result

        # 3. Build handoff context
        context_block = self._build_context_block(completed_node)

        # 4. Inject context into each dependent's body_md
        for dep_node_id in all_dependent_node_ids:
            dep_node = self._repo.get_node_by_id(dep_node_id)
            if dep_node is None:
                continue

            # Check if handoff context from this task was already injected
            marker = f"<!-- handoff:{completed_task_node_id} -->"
            if marker in (dep_node.body_md or ""):
                log.debug(
                    "handoff_already_injected",
                    from_node=str(completed_task_node_id),
                    to_node=str(dep_node_id),
                )
                continue

            # Append handoff context
            new_body = self._append_context(
                dep_node.body_md or "",
                context_block,
                marker,
            )
            dep_node.body_md = new_body
            self._repo.upsert_node(dep_node)

            entry = HandoffEntry(
                dependent_task_node_id=dep_node_id,
                dependent_title=dep_node.title,
                from_task_node_id=completed_task_node_id,
                from_title=completed_node.title,
            )
            result.entries.append(entry)

            log.info(
                "handoff_context_injected",
                from_node=str(completed_task_node_id),
                from_title=completed_node.title,
                to_node=str(dep_node_id),
                to_title=dep_node.title,
            )

        return result

    # ── Internal helpers ───────────────────────────────────────────

    def _find_ready_dependents(
        self,
        completed_node_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> list:
        """Find READY/IN_PROGRESS tasks that depend on the completed node.

        Delegates to the repository's public ``find_ready_dependents``
        method for proper encapsulation.
        """
        return self._repo.find_ready_dependents(completed_node_id, project_id)

    def _build_context_block(self, completed_node) -> str:
        """Build a handoff context markdown block from a completed node."""
        # Extract meaningful body: strip existing handoff sections
        body = completed_node.body_md or ""
        # Truncate body to reasonable length for handoff
        max_body_len = 2000
        if len(body) > max_body_len:
            body = body[:max_body_len] + "\n\n… (truncated)"

        lines = [
            f"### From: {completed_node.title}",
            f"- **Type**: {completed_node.type}",
            f"- **Status**: {completed_node.status}",
            f"- **Node ID**: `{completed_node.id}`",
            "",
            body,
        ]
        return "\n".join(lines)

    @staticmethod
    def _append_context(
        existing_body: str,
        context_block: str,
        marker: str,
    ) -> str:
        """Append a handoff context block to existing body_md.

        If the body already has a ``## Handoff Context`` section, appends
        a new entry under it. Otherwise, creates the section.
        """
        tagged_block = f"{marker}\n{context_block}"

        if HANDOFF_SECTION_HEADER in existing_body:
            # Append under existing section
            return f"{existing_body}\n\n{tagged_block}"

        # Create new section
        separator = "\n\n---\n\n" if existing_body.strip() else ""
        return f"{existing_body}{separator}{HANDOFF_SECTION_HEADER}\n\n{tagged_block}"
