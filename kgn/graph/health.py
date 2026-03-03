"""Graph Health Index — knowledge graph quality metrics.

Computes five health indicators from the repository and provides
a structured result for CLI rendering.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from kgn.db.repository import KgnRepository


@dataclass
class HealthReport:
    """Computed health metrics for a single project."""

    total_nodes: int
    total_edges: int

    active_nodes: int
    orphan_active: int
    conflict_count: int
    superseded_stale: int
    wip_tasks: int
    open_assumptions: int
    pending_contradicts: int
    spec_nodes: int

    @property
    def orphan_rate(self) -> float:
        """Fraction of ACTIVE nodes without any edges."""
        if self.active_nodes == 0:
            return 0.0
        return self.orphan_active / self.active_nodes

    @property
    def orphan_rate_ok(self) -> bool:
        return self.orphan_rate < 0.2

    @property
    def conflict_ok(self) -> bool:
        return self.conflict_count == 0

    @property
    def superseded_stale_ok(self) -> bool:
        return self.superseded_stale == 0

    @property
    def dup_spec_rate(self) -> float:
        """(PENDING CONTRADICTS count) / (total SPEC nodes)."""
        if self.spec_nodes == 0:
            return 0.0
        return self.pending_contradicts / self.spec_nodes

    @property
    def dup_spec_rate_ok(self) -> bool:
        """< 0.1 is considered healthy."""
        return self.dup_spec_rate < 0.1


class HealthService:
    """Compute graph health metrics from the repository."""

    def __init__(self, repo: KgnRepository) -> None:
        self._repo = repo

    def compute(self, project_id: uuid.UUID) -> HealthReport:
        """Gather all health indicators for *project_id*."""
        node_counts = self._repo.count_nodes(project_id)
        edge_counts = self._repo.count_edges(project_id)

        total_nodes = sum(node_counts.values())
        total_edges = sum(edge_counts.values())

        return HealthReport(
            total_nodes=total_nodes,
            total_edges=total_edges,
            active_nodes=self._repo.count_active_nodes(project_id),
            orphan_active=self._repo.count_active_orphan_nodes(project_id),
            conflict_count=self._repo.count_contradicts_edges(project_id),
            superseded_stale=self._repo.count_superseded_stale(project_id),
            wip_tasks=self._repo.count_wip_tasks(project_id),
            open_assumptions=self._repo.count_open_assumptions(project_id),
            pending_contradicts=self._repo.count_pending_contradicts(project_id),
            spec_nodes=self._repo.count_spec_nodes(project_id),
        )
