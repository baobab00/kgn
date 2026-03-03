"""Serializer for EdgeRecord list → .kge text.

Reverse of ``kgn.parser.kge_parser.parse_kge_text``.  The roundtrip
invariant is: ``serialize → parse → serialize`` produces identical text.
"""

from __future__ import annotations

import uuid
from datetime import UTC

import yaml

from kgn.models.edge import EdgeRecord


def serialize_edges(
    edges: list[EdgeRecord],
    *,
    project_id: uuid.UUID | None = None,
    agent_id: str | None = None,
    kgn_version: str = "0.1",
) -> str:
    """Serialize a list of :class:`EdgeRecord` into ``.kge`` text.

    All edges must belong to the same project.  If ``project_id`` or
    ``agent_id`` are not supplied they are inferred from the first edge.

    Parameters:
        edges: Edge records to serialize.
        project_id: Override project identifier.
        agent_id: Override agent identifier.
        kgn_version: KGN format version string.

    Returns:
        A string in ``.kge`` format (YAML with ``edges`` list).

    Raises:
        ValueError: When *edges* is empty or contains mixed projects.
    """
    if not edges:
        msg = "Cannot serialize an empty edge list"
        raise ValueError(msg)

    # Resolve project / agent
    resolved_project = project_id or edges[0].project_id
    resolved_agent = agent_id
    if resolved_agent is None:
        resolved_agent = str(edges[0].created_by) if edges[0].created_by else "unknown"

    # Validate all edges share the same project
    for edge in edges:
        if edge.project_id != resolved_project:
            msg = (
                f"All edges must belong to the same project. "
                f"Expected {resolved_project}, got {edge.project_id}"
            )
            raise ValueError(msg)

    # created_at from first edge (or omit)
    created_at_str: str | None = None
    if edges[0].created_at is not None:
        dt = edges[0].created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        created_at_str = dt.isoformat()

    # Build edge entries
    edge_entries = []
    for edge in edges:
        entry: dict = {
            "from": str(edge.from_node_id),
            "to": str(edge.to_node_id),
            "type": str(edge.type),
        }
        if edge.note:
            entry["note"] = edge.note
        edge_entries.append(entry)

    front: dict = {
        "kgn_version": kgn_version,
        "project_id": str(resolved_project),
        "agent_id": resolved_agent,
    }
    if created_at_str:
        front["created_at"] = created_at_str

    front["edges"] = edge_entries

    yaml_text = yaml.dump(
        front,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=1000,
    ).rstrip("\n")

    return f"---\n{yaml_text}\n---\n"
