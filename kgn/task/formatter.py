"""HandoffFormatter — structured output for context packages (R11).

All handoff output (JSON / Markdown) MUST go through this formatter.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import kgn
from kgn.task.service import ContextPackage


class HandoffFormatter:
    """Format a :class:`ContextPackage` into JSON or Markdown."""

    # ── JSON ───────────────────────────────────────────────────────

    @staticmethod
    def to_json(package: ContextPackage) -> str:
        """Serialize *package* to a JSON string.

        Structure::

            {
              "task": {id, node_id, priority, attempt, max_attempts},
              "node": {id, type, title, body_md},
              "subgraph": {"nodes": [...], "edges": [...]},
              "similar_nodes": [{id, type, title, similarity}],
              "metadata": {generated_at, kgn_version}
            }
        """
        data = {
            "task": {
                "id": str(package.task.id),
                "node_id": str(package.task.task_node_id),
                "priority": package.task.priority,
                "attempt": package.task.attempts,
                "max_attempts": package.task.max_attempts,
            },
            "node": {
                "id": str(package.node.id),
                "type": str(package.node.type),
                "title": package.node.title,
                "body_md": package.node.body_md,
            },
            "subgraph": {
                "nodes": [
                    {
                        "id": str(n.id),
                        "type": n.type,
                        "status": n.status,
                        "title": n.title,
                    }
                    for n in package.subgraph.nodes
                ],
                "edges": [
                    {
                        "from_id": e.from_id,
                        "to_id": e.to_id,
                        "type": e.type,
                        "note": e.note,
                    }
                    for e in package.subgraph.edges
                ],
            },
            "similar_nodes": [
                {
                    "id": str(s.id),
                    "type": s.type,
                    "title": s.title,
                    "similarity": round(s.similarity, 4),
                }
                for s in package.similar_nodes
            ],
            "metadata": {
                "generated_at": datetime.now(UTC).isoformat(),
                "kgn_version": kgn.__version__,
            },
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    # ── Markdown ───────────────────────────────────────────────────

    @staticmethod
    def to_markdown(package: ContextPackage) -> str:
        """Render *package* as a Markdown handoff document.

        Section order:
        1. Task (metadata + body)
        2. Subgraph (depth=2, nodes + edges tables)
        3. Similar Cases (Top-3)
        Footer with version and timestamp.
        """
        lines: list[str] = []
        task = package.task
        node = package.node

        # ── Header
        lines.append(f"# Task Handoff — {task.id}")
        lines.append("")

        # ── 1. Task
        lines.append("## 1. Task")
        lines.append("")
        lines.append(f"- **Node ID:** {node.id}")
        lines.append(f"- **Title:** {node.title}")
        lines.append(f"- **Type:** {node.type}")
        lines.append(f"- **Priority:** {task.priority}")
        lines.append(f"- **Attempt:** {task.attempts}/{task.max_attempts}")
        lines.append("")
        lines.append("### Task Body")
        lines.append("")
        lines.append(node.body_md if node.body_md else "> (empty)")
        lines.append("")

        # ── 2. Subgraph
        lines.append("## 2. Subgraph (depth=2)")
        lines.append("")

        # Nodes table
        lines.append("### Nodes")
        lines.append("")
        if package.subgraph.nodes:
            lines.append("| ID | Type | Status | Title |")
            lines.append("|---|---|---|---|")
            for n in package.subgraph.nodes:
                short_id = str(n.id)[:8] + ".."
                lines.append(f"| {short_id} | {n.type} | {n.status} | {n.title} |")
        else:
            lines.append("> No connected nodes.")
        lines.append("")

        # Edges table
        lines.append("### Edges")
        lines.append("")
        if package.subgraph.edges:
            lines.append("| From | To | Relation | Note |")
            lines.append("|---|---|---|---|")
            for e in package.subgraph.edges:
                from_short = e.from_id[:8] + ".."
                to_short = e.to_id[:8] + ".."
                lines.append(f"| {from_short} | {to_short} | {e.type} | {e.note} |")
        else:
            lines.append("> No edges.")
        lines.append("")

        # ── 3. Similar Cases
        lines.append("## 3. Similar Cases (Top-3)")
        lines.append("")
        if package.similar_nodes:
            lines.append("| ID | Type | Title | Similarity |")
            lines.append("|---|---|---|---|")
            for s in package.similar_nodes:
                short_id = str(s.id)[:8] + ".."
                lines.append(f"| {short_id} | {s.type} | {s.title} | {s.similarity:.4f} |")
        else:
            lines.append("> No similar cases (embeddings not available)")
        lines.append("")

        # ── Footer
        lines.append("---")
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f"> Generated by kgn v{kgn.__version__} at {ts}")
        lines.append("")

        return "\n".join(lines)
