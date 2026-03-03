"""File system layout conventions for sync export/import.

Directory structure:
    <sync_root>/
    ├── <project-name>/
    │   ├── nodes/
    │   │   ├── GOAL/
    │   │   │   └── <slug>.kgn
    │   │   ├── SPEC/
    │   │   │   └── <slug>.kgn
    │   │   └── ...
    │   └── edges/
    │       └── <from_slug>--<edge_type>--<to_slug>.kge
    └── .kgn-sync.json
"""

from __future__ import annotations

import re
from pathlib import Path

from kgn.models.edge import EdgeRecord
from kgn.models.node import NodeRecord


def node_slug(node: NodeRecord) -> str:
    """Generate a filesystem-safe slug from a node title + partial UUID.

    Rules:
        - title → lowercase
        - non-alphanumeric chars (except hyphens) → hyphens
        - collapse consecutive hyphens
        - strip leading/trailing hyphens
        - append first 8 chars of node UUID for uniqueness

    Examples:
        "Auth Module Design" → "auth-module-design-550e8400"
        "OAuth 2.0 / PKCE" → "oauth-2-0-pkce-550e8400"
    """
    slug = node.title.lower().strip()
    # Replace any character that is not a letter, digit, or hyphen
    slug = re.sub(r"[^\w-]", "-", slug, flags=re.UNICODE)
    # Collapse consecutive hyphens
    slug = re.sub(r"-{2,}", "-", slug)
    # Strip leading/trailing hyphens and underscores
    slug = slug.strip("-_")

    if not slug:
        slug = "untitled"

    uuid_prefix = str(node.id).split("-")[0]
    return f"{slug}-{uuid_prefix}"


def edge_slug(edge: EdgeRecord) -> str:
    """Generate a filesystem-safe slug for an edge file.

    Format: ``<from_first8>--<EDGE_TYPE>--<to_first8>``

    Example:
        "550e8400--IMPLEMENTS--661f9511"
    """
    from_prefix = str(edge.from_node_id).split("-")[0]
    to_prefix = str(edge.to_node_id).split("-")[0]
    return f"{from_prefix}--{edge.type.value}--{to_prefix}"


def node_path(sync_root: Path, project_name: str, node: NodeRecord) -> Path:
    """Compute the export file path for a node.

    Returns:
        ``<sync_root>/<project_name>/nodes/<TYPE>/<slug>.kgn``
    """
    return sync_root / project_name / "nodes" / node.type.value / f"{node_slug(node)}.kgn"


def edge_path(sync_root: Path, project_name: str, edge: EdgeRecord) -> Path:
    """Compute the export file path for an edge.

    Returns:
        ``<sync_root>/<project_name>/edges/<slug>.kge``
    """
    return sync_root / project_name / "edges" / f"{edge_slug(edge)}.kge"


def find_kgn_files(project_dir: Path) -> list[Path]:
    """Recursively find all .kgn files under a project directory."""
    if not project_dir.exists():
        return []
    return sorted(project_dir.rglob("*.kgn"))


def find_kge_files(project_dir: Path) -> list[Path]:
    """Recursively find all .kge files under a project directory."""
    if not project_dir.exists():
        return []
    return sorted(project_dir.rglob("*.kge"))


def project_dir(sync_root: Path, project_name: str) -> Path:
    """Return the project directory path."""
    return sync_root / project_name


def nodes_dir(sync_root: Path, project_name: str) -> Path:
    """Return the nodes directory path."""
    return sync_root / project_name / "nodes"


def edges_dir(sync_root: Path, project_name: str) -> Path:
    """Return the edges directory path."""
    return sync_root / project_name / "edges"
