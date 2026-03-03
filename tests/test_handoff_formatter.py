"""Pure-unit tests for HandoffFormatter (no DB required)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from kgn.db.repository import SimilarNode, SubgraphNode, TaskQueueItem
from kgn.graph.subgraph import SubgraphEdge, SubgraphResult
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.task.service import ContextPackage

# ── Helpers ────────────────────────────────────────────────────────────


def _build_package(
    *,
    subgraph_nodes: list | None = None,
    subgraph_edges: list | None = None,
    similar_nodes: list | None = None,
    body_md: str = "## Context\n\nTask body",
) -> ContextPackage:
    """Build a dummy ContextPackage from in-memory dataclasses."""
    pid = uuid.uuid4()
    nid = uuid.uuid4()
    tid = uuid.uuid4()

    now = datetime.now(UTC)
    task = TaskQueueItem(
        id=tid,
        project_id=pid,
        task_node_id=nid,
        priority=5,
        state="IN_PROGRESS",
        leased_by=uuid.uuid4(),
        lease_expires_at=now,
        attempts=1,
        max_attempts=3,
        created_at=now,
        updated_at=now,
    )
    node = NodeRecord(
        id=nid,
        project_id=pid,
        type=NodeType.TASK,
        status=NodeStatus.ACTIVE,
        title="Test Task",
        body_md=body_md,
        content_hash="abc123",
    )
    subgraph = SubgraphResult(
        root_id=str(nid),
        depth=2,
        nodes=subgraph_nodes if subgraph_nodes is not None else [],
        edges=subgraph_edges if subgraph_edges is not None else [],
    )
    return ContextPackage(
        task=task,
        node=node,
        subgraph=subgraph,
        similar_nodes=similar_nodes if similar_nodes is not None else [],
    )


# ── to_json ────────────────────────────────────────────────────────────


class TestHandoffFormatterJSON:
    def test_to_json_top_level_keys(self) -> None:
        import json as _json

        from kgn.task.formatter import HandoffFormatter

        pkg = _build_package()
        data = _json.loads(HandoffFormatter.to_json(pkg))
        assert set(data.keys()) == {
            "task",
            "node",
            "subgraph",
            "similar_nodes",
            "metadata",
        }

    def test_to_json_task_fields(self) -> None:
        import json as _json

        from kgn.task.formatter import HandoffFormatter

        pkg = _build_package()
        data = _json.loads(HandoffFormatter.to_json(pkg))
        t = data["task"]
        assert t["node_id"] == str(pkg.task.task_node_id)
        assert t["priority"] == 5
        assert t["attempt"] == 1
        assert t["max_attempts"] == 3

    def test_to_json_node_fields(self) -> None:
        import json as _json

        from kgn.task.formatter import HandoffFormatter

        pkg = _build_package()
        data = _json.loads(HandoffFormatter.to_json(pkg))
        n = data["node"]
        assert n["id"] == str(pkg.node.id)
        assert n["type"] == "TASK"
        assert n["title"] == "Test Task"
        assert n["body_md"] == "## Context\n\nTask body"

    def test_to_json_subgraph_with_nodes_and_edges(self) -> None:
        import json as _json

        from kgn.task.formatter import HandoffFormatter

        nid = uuid.uuid4()
        nodes = [
            SubgraphNode(id=nid, type="SPEC", status="ACTIVE", title="Spec", body_md="x", depth=1)
        ]
        edges = [
            SubgraphEdge(
                from_id=str(uuid.uuid4()),
                to_id=str(nid),
                type="DEPENDS_ON",
                note="",
            )
        ]
        pkg = _build_package(subgraph_nodes=nodes, subgraph_edges=edges)
        data = _json.loads(HandoffFormatter.to_json(pkg))
        assert len(data["subgraph"]["nodes"]) == 1
        assert data["subgraph"]["nodes"][0]["type"] == "SPEC"
        assert len(data["subgraph"]["edges"]) == 1
        assert data["subgraph"]["edges"][0]["type"] == "DEPENDS_ON"

    def test_to_json_empty_similar(self) -> None:
        import json as _json

        from kgn.task.formatter import HandoffFormatter

        pkg = _build_package()
        data = _json.loads(HandoffFormatter.to_json(pkg))
        assert data["similar_nodes"] == []

    def test_to_json_with_similar(self) -> None:
        import json as _json

        from kgn.task.formatter import HandoffFormatter

        similars = [
            SimilarNode(id=uuid.uuid4(), type="TASK", title="Similar A", similarity=0.91234)
        ]
        pkg = _build_package(similar_nodes=similars)
        data = _json.loads(HandoffFormatter.to_json(pkg))
        assert len(data["similar_nodes"]) == 1
        assert data["similar_nodes"][0]["similarity"] == 0.9123  # rounded

    def test_to_json_metadata_version(self) -> None:
        import json as _json

        import kgn
        from kgn.task.formatter import HandoffFormatter

        pkg = _build_package()
        data = _json.loads(HandoffFormatter.to_json(pkg))
        assert data["metadata"]["kgn_version"] == kgn.__version__
        assert "generated_at" in data["metadata"]


# ── to_markdown ────────────────────────────────────────────────────────


class TestHandoffFormatterMarkdown:
    def test_to_markdown_sections_present(self) -> None:
        from kgn.task.formatter import HandoffFormatter

        pkg = _build_package()
        md = HandoffFormatter.to_markdown(pkg)
        assert "# Task Handoff" in md
        assert "## 1. Task" in md
        assert "## 2. Subgraph" in md
        assert "## 3. Similar Cases" in md

    def test_to_markdown_no_similar(self) -> None:
        from kgn.task.formatter import HandoffFormatter

        pkg = _build_package()
        md = HandoffFormatter.to_markdown(pkg)
        assert "No similar cases" in md

    def test_to_markdown_empty_subgraph(self) -> None:
        from kgn.task.formatter import HandoffFormatter

        pkg = _build_package()
        md = HandoffFormatter.to_markdown(pkg)
        assert "No connected nodes" in md

    def test_to_markdown_with_subgraph_nodes(self) -> None:
        from kgn.task.formatter import HandoffFormatter

        nid = uuid.uuid4()
        nodes = [
            SubgraphNode(
                id=nid,
                type="GOAL",
                status="ACTIVE",
                title="My Goal",
                body_md="g",
                depth=1,
            )
        ]
        pkg = _build_package(subgraph_nodes=nodes)
        md = HandoffFormatter.to_markdown(pkg)
        assert "| ID | Type | Status | Title |" in md
        assert "My Goal" in md
        assert "GOAL" in md

    def test_to_markdown_with_edges(self) -> None:
        from kgn.task.formatter import HandoffFormatter

        edges = [
            SubgraphEdge(
                from_id=str(uuid.uuid4()),
                to_id=str(uuid.uuid4()),
                type="RELATES_TO",
                note="notes here",
            )
        ]
        pkg = _build_package(subgraph_edges=edges)
        md = HandoffFormatter.to_markdown(pkg)
        assert "| From | To | Relation | Note |" in md
        assert "RELATES_TO" in md
        assert "notes here" in md

    def test_to_markdown_footer(self) -> None:
        import kgn
        from kgn.task.formatter import HandoffFormatter

        pkg = _build_package()
        md = HandoffFormatter.to_markdown(pkg)
        assert f"kgn v{kgn.__version__}" in md
        assert "---" in md

    def test_to_markdown_task_body(self) -> None:
        from kgn.task.formatter import HandoffFormatter

        pkg = _build_package(body_md="Custom body content")
        md = HandoffFormatter.to_markdown(pkg)
        assert "Custom body content" in md
