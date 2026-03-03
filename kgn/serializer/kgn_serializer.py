"""Serializer for NodeRecord → .kgn text.

Reverse of ``kgn.parser.kgn_parser.parse_kgn_text``.  The roundtrip
invariant is: ``serialize → parse → serialize`` produces identical text.
"""

from __future__ import annotations

from datetime import UTC

import yaml

from kgn.models.node import NodeRecord


def serialize_node(
    node: NodeRecord,
    *,
    kgn_version: str = "0.1",
    agent_id: str | None = None,
) -> str:
    """Serialize a :class:`NodeRecord` into ``.kgn`` text.

    Parameters:
        node: The node record to serialize.
        kgn_version: KGN format version string.
        agent_id: Agent identifier.  Falls back to ``str(node.created_by)``
            if not provided (and ``created_by`` is set), otherwise ``"unknown"``.

    Returns:
        A string in ``.kgn`` format (YAML front matter + Markdown body).
    """
    resolved_agent = agent_id
    if resolved_agent is None:
        resolved_agent = str(node.created_by) if node.created_by else "unknown"

    front: dict = {
        "kgn_version": kgn_version,
        "id": str(node.id),
        "type": str(node.type),
        "title": node.title,
        "status": str(node.status),
        "project_id": str(node.project_id),
        "agent_id": resolved_agent,
    }

    # created_at — always include when available
    if node.created_at is not None:
        # Ensure timezone-aware ISO format
        dt = node.created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        front["created_at"] = dt.isoformat()

    # Optional fields — only include when present
    if node.tags:
        front["tags"] = list(node.tags)

    if node.confidence is not None:
        front["confidence"] = node.confidence

    yaml_text = yaml.dump(
        front,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=1000,  # prevent line wrapping
    ).rstrip("\n")

    body = node.body_md.strip() if node.body_md else ""

    if body:
        return f"---\n{yaml_text}\n---\n\n{body}\n"
    return f"---\n{yaml_text}\n---\n"
