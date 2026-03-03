"""Built-in workflow templates for common KGN patterns.

Provides three templates:
1. ``design-to-impl`` — GOAL → SPEC → ARCH → TASK(impl) → TASK(review)
2. ``issue-resolution`` — ISSUE → TASK(fix) → TASK(review)
3. ``knowledge-indexing`` — SUMMARY trigger → TASK(index) → TASK(review)
"""

from __future__ import annotations

from kgn.orchestration.workflow import WorkflowTemplate, create_workflow_template

# ── Template definitions (dict form, converted to WorkflowTemplate) ──

_DESIGN_TO_IMPL_DICT: dict = {
    "name": "design-to-impl",
    "display_name": "Design to Implementation",
    "trigger": {
        "node_type": "GOAL",
        "status": "ACTIVE",
    },
    "steps": [
        {
            "id": "spec",
            "action": "create_subtask",
            "node_type": "SPEC",
            "title_template": "{parent.title} — Detailed Spec",
            "assign_role": "genesis",
            "edge": "DERIVED_FROM",
        },
        {
            "id": "arch",
            "action": "create_subtask",
            "node_type": "ARCH",
            "title_template": "{parent.title} — Architecture Design",
            "assign_role": "genesis",
            "edge": "DERIVED_FROM",
            "depends_on": ["spec"],
        },
        {
            "id": "impl",
            "action": "create_subtask",
            "node_type": "TASK",
            "title_template": "{parent.title} — Implementation",
            "assign_role": "worker",
            "edge": "IMPLEMENTS",
            "depends_on": ["arch"],
        },
        {
            "id": "review",
            "action": "create_subtask",
            "node_type": "TASK",
            "title_template": "{parent.title} — Review",
            "assign_role": "reviewer",
            "edge": "DERIVED_FROM",
            "depends_on": ["impl"],
        },
    ],
}

_ISSUE_RESOLUTION_DICT: dict = {
    "name": "issue-resolution",
    "display_name": "Issue Resolution",
    "trigger": {
        "node_type": "ISSUE",
        "status": "ACTIVE",
    },
    "steps": [
        {
            "id": "fix",
            "action": "create_subtask",
            "node_type": "TASK",
            "title_template": "{parent.title} — Fix",
            "assign_role": "worker",
            "edge": "RESOLVES",
        },
        {
            "id": "review",
            "action": "create_subtask",
            "node_type": "TASK",
            "title_template": "{parent.title} — Review",
            "assign_role": "reviewer",
            "edge": "DERIVED_FROM",
            "depends_on": ["fix"],
        },
    ],
}

_KNOWLEDGE_INDEXING_DICT: dict = {
    "name": "knowledge-indexing",
    "display_name": "Knowledge Indexing",
    "trigger": {
        "node_type": "SUMMARY",
        "status": "ACTIVE",
    },
    "steps": [
        {
            "id": "index",
            "action": "create_subtask",
            "node_type": "TASK",
            "title_template": "{parent.title} — Indexing",
            "assign_role": "indexer",
            "edge": "DERIVED_FROM",
        },
        {
            "id": "review",
            "action": "create_subtask",
            "node_type": "TASK",
            "title_template": "{parent.title} — Index Verification",
            "assign_role": "reviewer",
            "edge": "DERIVED_FROM",
            "depends_on": ["index"],
        },
    ],
}

# ── Public API ─────────────────────────────────────────────────────────

DESIGN_TO_IMPL: WorkflowTemplate = create_workflow_template(_DESIGN_TO_IMPL_DICT)
ISSUE_RESOLUTION: WorkflowTemplate = create_workflow_template(_ISSUE_RESOLUTION_DICT)
KNOWLEDGE_INDEXING: WorkflowTemplate = create_workflow_template(_KNOWLEDGE_INDEXING_DICT)

BUILTIN_TEMPLATES: list[WorkflowTemplate] = [
    DESIGN_TO_IMPL,
    ISSUE_RESOLUTION,
    KNOWLEDGE_INDEXING,
]


def register_builtins(engine: WorkflowEngine) -> None:  # noqa: F821
    """Register all built-in templates into a WorkflowEngine instance."""
    from kgn.orchestration.workflow import WorkflowEngine  # avoid circular

    assert isinstance(engine, WorkflowEngine)
    for tmpl in BUILTIN_TEMPLATES:
        engine.register(tmpl)
