"""Pydantic models for .kgn node files."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from kgn.models.enums import NodeStatus, NodeType


def _utcnow() -> datetime:
    return datetime.now(UTC)


class NodeFrontMatter(BaseModel):
    """Schema for .kgn file YAML front matter validation."""

    kgn_version: str
    id: str
    type: NodeType
    title: str
    status: NodeStatus
    project_id: str
    agent_id: str
    created_at: datetime = Field(default_factory=_utcnow)

    # Optional fields
    supersedes: str | None = None
    tags: list[str] = Field(default_factory=list)
    confidence: float | None = None

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float | None) -> float | None:
        """Ensure confidence is within 0.0~1.0 range."""
        if v is not None and (v < 0.0 or v > 1.0):
            msg = "confidence must be between 0.0 and 1.0"
            raise ValueError(msg)
        return v


class NodeRecord(BaseModel):
    """Node record for DB operations."""

    id: uuid.UUID
    project_id: uuid.UUID
    type: NodeType
    status: NodeStatus = NodeStatus.ACTIVE
    title: str
    body_md: str = ""

    file_path: str | None = None
    content_hash: str | None = None

    tags: list[str] = Field(default_factory=list)
    confidence: float | None = None

    created_by: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
