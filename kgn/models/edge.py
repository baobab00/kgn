"""Pydantic models for .kge edge files."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from kgn.models.enums import EdgeType


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EdgeEntry(BaseModel):
    """Single edge definition within a .kge file."""

    from_node: str = Field(alias="from")
    to: str
    type: EdgeType
    note: str = ""

    model_config = {"populate_by_name": True}


class EdgeFrontMatter(BaseModel):
    """Schema for .kge file YAML front matter validation."""

    kgn_version: str
    project_id: str
    agent_id: str
    created_at: datetime = Field(default_factory=_utcnow)
    edges: list[EdgeEntry]


class EdgeRecord(BaseModel):
    """Edge record for DB operations."""

    id: int | None = None
    project_id: uuid.UUID
    from_node_id: uuid.UUID
    to_node_id: uuid.UUID
    type: EdgeType
    note: str = ""
    created_by: uuid.UUID | None = None
    created_at: datetime | None = None
