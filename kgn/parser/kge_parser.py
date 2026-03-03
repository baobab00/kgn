"""Parser for .kge (Knowledge Graph Edge) files.

Parses the entire file as YAML and validates it as an EdgeFrontMatter model.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from kgn.models.edge import EdgeFrontMatter


class KgeParseError(Exception):
    """Raised when a .kge file cannot be parsed."""


def parse_kge(source: str | Path) -> EdgeFrontMatter:
    """Parse a ``.kge`` file into :class:`EdgeFrontMatter`.

    Parameters:
        source: File path (str or Path) to read.

    Returns:
        Validated EdgeFrontMatter model.

    Raises:
        KgeParseError: on YAML or validation errors.
    """
    path = Path(source)
    text = path.read_text(encoding="utf-8")
    return parse_kge_text(text)


def parse_kge_text(text: str) -> EdgeFrontMatter:
    """Parse raw ``.kge`` text (useful for testing without files).

    The text may optionally be wrapped in ``---`` delimiters; they are
    stripped before YAML parsing so both bare YAML and front-matter-style
    files are accepted.

    Parameters:
        text: Full file content as a string.

    Returns:
        Validated EdgeFrontMatter model.

    Raises:
        KgeParseError: on YAML or validation errors.
    """
    # Strip optional --- delimiters
    stripped = text.strip().lstrip("\ufeff")
    if stripped.startswith("---"):
        stripped = stripped[3:]
        end_idx = stripped.find("---")
        if end_idx != -1:
            stripped = stripped[:end_idx]
        stripped = stripped.strip()

    try:
        data = yaml.safe_load(stripped)
    except yaml.YAMLError as exc:
        raise KgeParseError(f"YAML syntax error: {exc}") from exc

    if not isinstance(data, dict):
        raise KgeParseError("YAML content must be a mapping")

    try:
        return EdgeFrontMatter(**data)
    except ValidationError as exc:
        raise KgeParseError(f"Edge validation failed: {exc}") from exc
