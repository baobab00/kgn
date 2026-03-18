"""Subgraph extraction and formatting for context packages.

Provides higher-level graph operations on top of ``KgnRepository``,
including subgraph extraction with ARCHIVED-node filtering and
output formatting to JSON / Markdown.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field

from kgn.db.repository import KgnRepository, SubgraphNode
from kgn.models.enums import EdgeType, NodeStatus

# ── Constants ──────────────────────────────────────────────────────────

MAX_SUBGRAPH_DEPTH = 5
"""Hard upper bound for BFS depth to prevent DoS via unbounded traversal."""

# ── Result types ───────────────────────────────────────────────────────


@dataclass
class SubgraphEdge:
    """Lightweight edge for subgraph output."""

    from_id: str
    to_id: str
    type: str
    note: str = ""


@dataclass
class SubgraphResult:
    """Complete subgraph extraction result."""

    root_id: str
    depth: int
    nodes: list[SubgraphNode] = field(default_factory=list)
    edges: list[SubgraphEdge] = field(default_factory=list)


# ── Service ────────────────────────────────────────────────────────────


class SubgraphService:
    """High-level subgraph extraction with formatting."""

    def __init__(self, repo: KgnRepository) -> None:
        self._repo = repo

    def extract(
        self,
        root_id: uuid.UUID,
        project_id: uuid.UUID,
        *,
        depth: int = 2,
        edge_types: list[EdgeType] | None = None,
        include_archived: bool = False,
    ) -> SubgraphResult:
        """Extract a subgraph and optionally filter ARCHIVED nodes.

        Parameters:
            root_id: Starting node UUID.
            project_id: Project scope.
            depth: Max BFS hops (clamped to MAX_SUBGRAPH_DEPTH).
            edge_types: Optional edge type filter.
            include_archived: If False (default), ARCHIVED nodes are excluded.

        Returns:
            SubgraphResult with nodes and edges.
        """
        depth = min(depth, MAX_SUBGRAPH_DEPTH)
        raw_nodes = self._repo.extract_subgraph(
            root_id=root_id,
            project_id=project_id,
            depth=depth,
            edge_types=edge_types,
        )

        # Filter out ARCHIVED nodes unless requested
        if not include_archived:
            raw_nodes = [n for n in raw_nodes if n.status != NodeStatus.ARCHIVED.value]

        # Gather edges between the surviving node IDs
        node_ids = {n.id for n in raw_nodes}
        edge_records = self._repo.get_edges_for_subgraph(node_ids, project_id)

        edges = [
            SubgraphEdge(
                from_id=str(e.from_node_id),
                to_id=str(e.to_node_id),
                type=e.type.value,
                note=e.note,
            )
            for e in edge_records
        ]

        return SubgraphResult(
            root_id=str(root_id),
            depth=depth,
            nodes=raw_nodes,
            edges=edges,
        )

    # ── Formatting ─────────────────────────────────────────────────

    @staticmethod
    def to_json(result: SubgraphResult) -> str:
        """Serialize subgraph to JSON string."""
        data = {
            "root_id": result.root_id,
            "depth": result.depth,
            "nodes": [asdict(n) for n in result.nodes],
            "edges": [asdict(e) for e in result.edges],
        }
        # Convert UUID objects to strings
        for node in data["nodes"]:
            node["id"] = str(node["id"])
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)

    @staticmethod
    def to_markdown(result: SubgraphResult) -> str:
        """Render subgraph as Markdown context package."""
        lines: list[str] = []
        lines.append(f"# Subgraph — root: {result.root_id}")
        lines.append(f"Depth: {result.depth} | Nodes: {len(result.nodes)}")
        lines.append("")

        # Group nodes by depth
        by_depth: dict[int, list[SubgraphNode]] = {}
        for n in result.nodes:
            by_depth.setdefault(n.depth, []).append(n)

        for d in sorted(by_depth):
            lines.append(f"## Depth {d}")
            lines.append("")
            for n in by_depth[d]:
                short_id = str(n.id)[:8]
                lines.append(f"### [{n.type}] {n.title} ({short_id}..)")
                lines.append(f"Status: {n.status}")
                lines.append("")
                if n.body_md:
                    lines.append(n.body_md)
                    lines.append("")

        if result.edges:
            lines.append("## Edges")
            lines.append("")
            for e in result.edges:
                from_short = e.from_id[:8]
                to_short = e.to_id[:8]
                note_part = f" — {e.note}" if e.note else ""
                lines.append(f"- {from_short}.. —[{e.type}]→ {to_short}..{note_part}")
            lines.append("")

        return "\n".join(lines)
