"""ENUM definitions for KGN file formats and DB schema."""

from enum import StrEnum


class NodeType(StrEnum):
    """Node type classification."""

    GOAL = "GOAL"
    ARCH = "ARCH"
    SPEC = "SPEC"
    LOGIC = "LOGIC"
    DECISION = "DECISION"
    ISSUE = "ISSUE"
    TASK = "TASK"
    CONSTRAINT = "CONSTRAINT"
    ASSUMPTION = "ASSUMPTION"
    SUMMARY = "SUMMARY"


class NodeStatus(StrEnum):
    """Node lifecycle status."""

    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class EdgeType(StrEnum):
    """Edge relationship type between nodes."""

    DEPENDS_ON = "DEPENDS_ON"
    IMPLEMENTS = "IMPLEMENTS"
    RESOLVES = "RESOLVES"
    SUPERSEDES = "SUPERSEDES"
    DERIVED_FROM = "DERIVED_FROM"
    CONTRADICTS = "CONTRADICTS"
    CONSTRAINED_BY = "CONSTRAINED_BY"


class ActivityType(StrEnum):
    """Agent activity types for audit logging."""

    NODE_CREATED = "NODE_CREATED"
    NODE_UPDATED = "NODE_UPDATED"
    NODE_STATUS_CHANGED = "NODE_STATUS_CHANGED"
    EDGE_CREATED = "EDGE_CREATED"
    CONTEXT_ASSEMBLED = "CONTEXT_ASSEMBLED"
    TASK_CHECKOUT = "TASK_CHECKOUT"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    KGN_INGESTED = "KGN_INGESTED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    CONFLICT_RESOLVED = "CONFLICT_RESOLVED"


class TaskState(StrEnum):
    """Task queue lifecycle state."""

    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    FAILED = "FAILED"


class AgentRole(StrEnum):
    """Agent role for multi-agent orchestration.

    Each role defines what actions an agent is permitted to perform:
    - genesis:  Project structure design (GOAL, SPEC, ARCH, CONSTRAINT, ASSUMPTION)
    - worker:   Task implementation (LOGIC, DECISION)
    - reviewer: Review and conflict mediation (ISSUE, SUMMARY)
    - indexer:  Summary generation (SUMMARY only)
    - admin:    All permissions (default, backward-compatible)
    """

    GENESIS = "genesis"
    WORKER = "worker"
    REVIEWER = "reviewer"
    INDEXER = "indexer"
    ADMIN = "admin"
