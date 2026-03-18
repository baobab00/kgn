"""KGN structured error codes and exception classes.

Provides ``KgnErrorCode`` enum for agent-parseable error classification
and ``KgnError`` exception for raising typed errors within tool handlers.

Error code ranges:
- 1xx: Infrastructure / connection
- 2xx: Ingest / parsing
- 3xx: Query / graph
- 4xx: Task lifecycle
- 9xx: General
"""

from __future__ import annotations

from enum import StrEnum


class KgnErrorCode(StrEnum):
    """KGN structured error codes.

    Agents can use the string value (e.g. ``"KGN-300"``) to
    programmatically branch on error type.
    """

    # 1xx: Infrastructure / connection
    DB_CONNECTION_FAILED = "KGN-100"
    DB_TIMEOUT = "KGN-101"
    POOL_EXHAUSTED = "KGN-102"  # reserved: pool starvation detection
    EMBEDDING_API_FAILED = "KGN-110"
    EMBEDDING_API_TIMEOUT = "KGN-111"

    # 2xx: Ingest / parsing
    PARSE_FAILED = "KGN-200"
    VALIDATION_FAILED = "KGN-201"
    DUPLICATE_CONTENT = "KGN-202"  # reserved: explicit duplicate rejection
    INVALID_KGN_FORMAT = "KGN-203"
    INVALID_KGE_FORMAT = "KGN-204"
    INVALID_UUID = "KGN-210"

    # 3xx: Query / graph
    NODE_NOT_FOUND = "KGN-300"
    PROJECT_NOT_FOUND = "KGN-301"
    SUBGRAPH_TOO_LARGE = "KGN-302"  # reserved: depth/size limit enforcement
    INVALID_NODE_TYPE = "KGN-310"
    INVALID_NODE_STATUS = "KGN-311"

    # 4xx: Task lifecycle
    TASK_QUEUE_EMPTY = "KGN-400"
    TASK_NOT_IN_PROGRESS = "KGN-401"
    TASK_NODE_INVALID = "KGN-402"
    TASK_MAX_ATTEMPTS = "KGN-403"  # reserved: retry limit enforcement
    TASK_DEPENDENCY_CYCLE = "KGN-404"
    TASK_BLOCKED = "KGN-405"  # reserved: blocked-task state transitions

    # 5xx: Git / sync
    GIT_NOT_INSTALLED = "KGN-500"
    GIT_NOT_INITIALIZED = "KGN-501"
    GIT_COMMAND_FAILED = "KGN-502"
    GIT_NOTHING_TO_COMMIT = "KGN-503"  # reserved: clean-tree detection

    # 5xx: Orchestration
    ROLE_PERMISSION_DENIED = "KGN-550"
    ROLE_INVALID = "KGN-551"  # reserved: unrecognized role string
    NODE_LOCKED = "KGN-560"
    NODE_LOCK_NOT_OWNED = "KGN-561"  # reserved: lock ownership mismatch
    CONFLICT_DETECTED = "KGN-570"  # reserved: explicit conflict signal
    CONFLICT_RESOLUTION_FAILED = "KGN-571"

    GITHUB_AUTH_FAILED = "KGN-510"
    GITHUB_REPO_NOT_FOUND = "KGN-511"
    GITHUB_API_ERROR = "KGN-512"

    SYNC_CONFLICT = "KGN-520"  # reserved: git merge conflict detection
    SYNC_CONFLICT_UNRESOLVED = "KGN-521"

    # 9xx: General
    INTERNAL_ERROR = "KGN-999"


# Codes that indicate the operation can be retried
_RECOVERABLE_CODES: frozenset[KgnErrorCode] = frozenset(
    {
        KgnErrorCode.DB_CONNECTION_FAILED,
        KgnErrorCode.DB_TIMEOUT,
        KgnErrorCode.POOL_EXHAUSTED,
        KgnErrorCode.EMBEDDING_API_TIMEOUT,
        KgnErrorCode.TASK_QUEUE_EMPTY,
    }
)


class KgnError(Exception):
    """Typed exception carrying a ``KgnErrorCode``.

    Raised inside MCP tool handlers to signal a structured error.
    ``safe_tool_call`` and ``_error_json`` can inspect the code to
    build the appropriate JSON response.
    """

    def __init__(
        self,
        code: KgnErrorCode,
        message: str,
        *,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or message

    @property
    def recoverable(self) -> bool:
        """Whether the caller should retry this operation."""
        return self.code in _RECOVERABLE_CODES
