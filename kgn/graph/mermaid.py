"""Mermaid diagram generation for knowledge graph visualisation.

Generates ``flowchart`` and ``task-board`` Mermaid diagrams that render
natively in GitHub Markdown.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

from kgn.db.repository import KgnRepository, SubgraphNode, TaskQueueItem
from kgn.models.edge import EdgeRecord
from kgn.models.enums import NodeType

_log = structlog.get_logger("kgn.graph.mermaid")

# Maximum number of nodes rendered in a full-project graph.  Prevents
# OOM and avoids GitHub-Mermaid render failures (>50 KB).
DEFAULT_MAX_NODES: int = 200

# ── Style definitions ──────────────────────────────────────────────────

NODE_STYLES: dict[str, str] = {
    "GOAL": "fill:#f9f,stroke:#333",
    "SPEC": "fill:#bbf,stroke:#333",
    "TASK": "fill:#bfb,stroke:#333",
    "DECISION": "fill:#ffb,stroke:#333",
    "ISSUE": "fill:#fbb,stroke:#333",
    "CONSTRAINT": "fill:#ddd,stroke:#333",
    "LOGIC": "fill:#dbf,stroke:#333",
    "ASSUMPTION": "fill:#fdb,stroke:#333",
    "ARCH": "fill:#bff,stroke:#333",
    "SUMMARY": "fill:#fff,stroke:#333",
}

TASK_STATE_STYLES: dict[str, str] = {
    "READY": "fill:#bfb,stroke:#333",
    "IN_PROGRESS": "fill:#bbf,stroke:#333",
    "BLOCKED": "fill:#fbb,stroke:#333",
    "DONE": "fill:#ddd,stroke:#333",
    "FAILED": "fill:#f66,stroke:#fff,color:#fff",
}


# ── Result dataclass ───────────────────────────────────────────────────


@dataclass
class MermaidResult:
    """Result of a Mermaid generation call."""

    diagram: str
    node_count: int
    edge_count: int
    truncated: bool = False


# ── Helpers ────────────────────────────────────────────────────────────


def _short_id(node_id: uuid.UUID | str) -> str:
    """Return first 12 chars of a UUID for Mermaid node IDs.

    12 hex chars = 48 bits → birthday-paradox 50% collision at ~16.7M nodes.
    Previous 8-char version collided at ~65K nodes (R-026).
    """
    return str(node_id).replace("-", "")[:12]


def _escape_label(text: str) -> str:
    """Escape characters that break Mermaid syntax.

    Handles quotes, newlines, brackets, braces, HTML entities,
    backticks, and hash symbols (R-030).
    """
    return (
        text.replace('"', "'")
        .replace("\n", " ")
        .replace("[", "(")
        .replace("]", ")")
        .replace("{", "(")
        .replace("}", ")")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("&", "&amp;")
        .replace("#", "&#35;")
        .replace("`", "'")
    )


def _node_line(node_id: str, node_type: str, title: str, status: str | None = None) -> str:
    """Build a single Mermaid node declaration."""
    short = _short_id(node_id)
    label = _escape_label(title)
    css_class = node_type.lower()
    suffix = f" ({status})" if status else ""
    return f'    {short}["{node_type}-{short}: {label}{suffix}"]:::{css_class}'


def _edge_line(from_id: str, to_id: str, edge_type: str) -> str:
    """Build a single Mermaid edge declaration."""
    return f"    {_short_id(from_id)} -->|{edge_type}| {_short_id(to_id)}"


def _classdefs() -> list[str]:
    """Generate classDef lines for all node types."""
    lines: list[str] = []
    for ntype, style in NODE_STYLES.items():
        lines.append(f"    classDef {ntype.lower()} {style}")
    return lines


def _task_classdefs() -> list[str]:
    """Generate classDef lines for task states."""
    lines: list[str] = []
    for state, style in TASK_STATE_STYLES.items():
        lines.append(f"    classDef {state.lower()} {style}")
    return lines


# ── Generator ──────────────────────────────────────────────────────────


class MermaidGenerator:
    """Generates Mermaid diagrams from the knowledge graph."""

    def __init__(self, repo: KgnRepository) -> None:
        self._repo = repo

    # ── Public API ─────────────────────────────────────────────────

    def generate_graph(
        self,
        project_id: uuid.UUID,
        *,
        root_node_id: uuid.UUID | None = None,
        depth: int = 3,
        include_status: bool = True,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> MermaidResult:
        """Generate a flowchart Mermaid diagram.

        If *root_node_id* is given, only the subgraph around that node
        (up to *depth* hops) is included; otherwise the full project
        graph is rendered.

        *max_nodes* caps the number of rendered nodes for full-project
        graphs.  Set to ``0`` to disable the limit.
        """
        if root_node_id is not None:
            nodes, edges = self._subgraph_data(root_node_id, project_id, depth)
        else:
            nodes, edges = self._full_graph_data(project_id)

        # ── Truncation guard (R-027) ──────────────────────────────
        truncated = False
        if max_nodes > 0 and len(nodes) > max_nodes:
            _log.warning(
                "graph_truncated",
                total_nodes=len(nodes),
                max_nodes=max_nodes,
                project_id=str(project_id),
            )
            nodes = nodes[:max_nodes]
            # Keep only edges whose both ends are in the retained set
            retained_ids = {n.id for n in nodes}
            edges = [
                e for e in edges if e.from_node_id in retained_ids and e.to_node_id in retained_ids
            ]
            truncated = True

        lines = ["flowchart TD"]

        # Nodes
        for n in nodes:
            status = n.status if include_status else None
            lines.append(_node_line(str(n.id), n.type, n.title, status))

        lines.append("")

        # Edges
        for e in edges:
            edge_type = e.type.value if hasattr(e.type, "value") else str(e.type)
            lines.append(_edge_line(str(e.from_node_id), str(e.to_node_id), edge_type))

        lines.append("")
        lines.extend(_classdefs())

        diagram = "\n".join(lines)
        return MermaidResult(
            diagram=diagram,
            node_count=len(nodes),
            edge_count=len(edges),
            truncated=truncated,
        )

    def generate_task_board(
        self,
        project_id: uuid.UUID,
    ) -> MermaidResult:
        """Generate a task-board Mermaid diagram grouped by state."""
        tasks = self._repo.list_tasks(project_id)

        # Batch-fetch node titles (R-028: avoid N+1 queries)
        task_node_ids = {t.task_node_id for t in tasks}
        node_map = self._repo.get_nodes_by_ids(task_node_ids)

        # Group tasks by state
        groups: dict[str, list[TaskQueueItem]] = {}
        for t in tasks:
            groups.setdefault(t.state, []).append(t)

        lines = ["flowchart LR"]

        # Ordered state names
        state_order = ["READY", "IN_PROGRESS", "BLOCKED", "DONE", "FAILED"]
        task_count = 0

        for state in state_order:
            state_tasks = groups.get(state, [])
            if not state_tasks:
                continue
            lines.append(f"    subgraph {state}")
            for t in state_tasks:
                short = _short_id(t.task_node_id)
                node = node_map.get(t.task_node_id)
                label = _escape_label(node.title) if node else short
                lines.append(f'        T{short}["{label}"]:::{state.lower()}')
                task_count += 1
            lines.append("    end")

        lines.append("")

        # Add dependency edges between tasks
        edge_count = 0
        dep_edges = self._task_dependency_edges(tasks, project_id)
        for from_tid, to_tid in dep_edges:
            lines.append(f"    T{_short_id(from_tid)} -.->|unblocks| T{_short_id(to_tid)}")
            edge_count += 1

        lines.append("")
        lines.extend(_task_classdefs())

        diagram = "\n".join(lines)
        return MermaidResult(diagram=diagram, node_count=task_count, edge_count=edge_count)

    def generate_readme(
        self,
        project_id: uuid.UUID,
        project_name: str,
        target_dir: Path,
    ) -> Path:
        """Generate a README.md with Mermaid diagrams and node stats.

        Returns the path to the written file.
        """
        nodes = self._repo.search_nodes(project_id)
        graph = self.generate_graph(project_id)
        task_board = self.generate_task_board(project_id)

        # Node type stats
        type_counts: dict[str, int] = {}
        for n in nodes:
            key = n.type.value if hasattr(n.type, "value") else str(n.type)
            type_counts[key] = type_counts.get(key, 0) + 1

        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            f"# {project_name}",
            "",
            f"> Auto-generated by KGN · {now}",
            "",
            "## Graph Overview",
            "",
            f"Nodes: **{graph.node_count}** | Edges: **{graph.edge_count}**",
            "",
            "```mermaid",
            graph.diagram,
            "```",
            "",
        ]

        # Task board (only if tasks exist)
        if task_board.node_count > 0:
            lines.extend(
                [
                    "## Task Board",
                    "",
                    "```mermaid",
                    task_board.diagram,
                    "```",
                    "",
                ]
            )

        # Stats table
        lines.extend(
            [
                "## Node Statistics",
                "",
                "| Type | Count |",
                "|------|-------|",
            ]
        )
        for ntype in NodeType:
            count = type_counts.get(ntype.value, 0)
            if count > 0:
                lines.append(f"| {ntype.value} | {count} |")
        lines.extend(
            [
                "",
                "---",
                f"*Last updated: {now}*",
                "",
            ]
        )

        readme_path = target_dir / "README.md"
        target_dir.mkdir(parents=True, exist_ok=True)
        readme_path.write_text("\n".join(lines), encoding="utf-8")
        return readme_path

    # ── Private helpers ────────────────────────────────────────────

    def _full_graph_data(
        self,
        project_id: uuid.UUID,
    ) -> tuple[list[SubgraphNode], list[EdgeRecord]]:
        """Fetch all nodes and edges for a project."""
        node_records = self._repo.search_nodes(project_id)
        edge_records = self._repo.search_edges(project_id)

        # Convert NodeRecord → SubgraphNode for uniform interface
        nodes = [
            SubgraphNode(
                id=n.id,
                type=n.type.value if hasattr(n.type, "value") else str(n.type),
                status=n.status.value if hasattr(n.status, "value") else str(n.status),
                title=n.title,
                body_md=n.body_md,
                depth=0,
            )
            for n in node_records
        ]
        return nodes, edge_records

    def _subgraph_data(
        self,
        root_id: uuid.UUID,
        project_id: uuid.UUID,
        depth: int,
    ) -> tuple[list[SubgraphNode], list[EdgeRecord]]:
        """Fetch subgraph around a root node."""
        raw_nodes = self._repo.extract_subgraph(root_id, project_id, depth=depth)
        node_ids = {n.id for n in raw_nodes}
        edges = self._repo.get_edges_for_subgraph(node_ids, project_id)
        return raw_nodes, edges

    def _task_dependency_edges(
        self,
        tasks: list[TaskQueueItem],
        project_id: uuid.UUID,
    ) -> list[tuple[str, str]]:
        """Find DEPENDS_ON edges between task nodes for the board."""
        task_node_ids = {t.task_node_id for t in tasks}
        if not task_node_ids:
            return []

        edges = self._repo.get_edges_for_subgraph(task_node_ids, project_id)
        return [
            (str(e.from_node_id), str(e.to_node_id))
            for e in edges
            if (e.type.value if hasattr(e.type, "value") else str(e.type)) == "DEPENDS_ON"
        ]
