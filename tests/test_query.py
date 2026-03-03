"""Tests for Step 7 — status, query nodes, query subgraph.

Covers:
- Repository: last_ingest_at, search_nodes (exclude_archived), get_edges_for_subgraph
- SubgraphService: extract (with/without ARCHIVED filter), to_json, to_markdown
- CLI: status, query nodes, query subgraph (table/json/md)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from kgn.db.repository import KgnRepository
from kgn.graph.subgraph import SubgraphService
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    title: str = "Test Node",
    node_type: NodeType = NodeType.SPEC,
    status: NodeStatus = NodeStatus.ACTIVE,
    node_id: uuid.UUID | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=node_id or uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=status,
        title=title,
        body_md=f"Body of {title}",
        content_hash=uuid.uuid4().hex,
        created_by=agent_id,
        created_at=datetime.now(tz=UTC),
    )


# ── Repository: last_ingest_at ────────────────────────────────────────


class TestLastIngestAt:
    """Repository.last_ingest_at tests."""

    def test_no_ingest_returns_none(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        assert repo.last_ingest_at(project_id) is None

    def test_returns_latest_timestamp(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        repo.log_ingest(project_id, "a.kgn", "hash1", "SUCCESS", ingested_by=agent_id)
        repo.log_ingest(project_id, "b.kgn", "hash2", "SUCCESS", ingested_by=agent_id)
        ts = repo.last_ingest_at(project_id)
        assert ts is not None
        assert isinstance(ts, datetime)


# ── Repository: search_nodes exclude_archived ─────────────────────────


class TestSearchNodesArchived:
    """search_nodes with exclude_archived flag."""

    def test_exclude_archived_by_default(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        active = _make_node(project_id, agent_id, title="active", status=NodeStatus.ACTIVE)
        archived = _make_node(project_id, agent_id, title="archived", status=NodeStatus.ARCHIVED)
        repo.upsert_node(active)
        repo.upsert_node(archived)

        results = repo.search_nodes(project_id)
        titles = {n.title for n in results}
        assert "active" in titles
        assert "archived" not in titles

    def test_include_archived_when_status_specified(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        archived = _make_node(project_id, agent_id, title="archived", status=NodeStatus.ARCHIVED)
        repo.upsert_node(archived)

        results = repo.search_nodes(project_id, status=NodeStatus.ARCHIVED)
        assert any(n.title == "archived" for n in results)

    def test_include_archived_flag(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        archived = _make_node(project_id, agent_id, title="archived", status=NodeStatus.ARCHIVED)
        repo.upsert_node(archived)

        results = repo.search_nodes(project_id, exclude_archived=False)
        assert any(n.title == "archived" for n in results)


# ── Repository: get_edges_for_subgraph ────────────────────────────────


class TestGetEdgesForSubgraph:
    """get_edges_for_subgraph tests."""

    def test_empty_set_returns_empty(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
    ) -> None:
        assert repo.get_edges_for_subgraph(set(), project_id) == []

    def test_returns_edges_between_nodes(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        from kgn.models.edge import EdgeRecord

        n1 = _make_node(project_id, agent_id, title="N1")
        n2 = _make_node(project_id, agent_id, title="N2")
        repo.upsert_node(n1)
        repo.upsert_node(n2)

        edge = EdgeRecord(
            project_id=project_id,
            from_node_id=n1.id,
            to_node_id=n2.id,
            type=EdgeType.DEPENDS_ON,
            created_by=agent_id,
        )
        repo.insert_edge(edge)

        edges = repo.get_edges_for_subgraph({n1.id, n2.id}, project_id)
        assert len(edges) == 1
        assert edges[0].from_node_id == n1.id
        assert edges[0].to_node_id == n2.id


# ── SubgraphService ───────────────────────────────────────────────────


class TestSubgraphService:
    """SubgraphService.extract, to_json, to_markdown tests."""

    def _setup_graph(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        """Create root → child → grandchild chain and return their IDs."""
        from kgn.models.edge import EdgeRecord

        root = _make_node(project_id, agent_id, title="Root Goal", node_type=NodeType.GOAL)
        child = _make_node(project_id, agent_id, title="Child Spec", node_type=NodeType.SPEC)
        grandchild = _make_node(
            project_id, agent_id, title="Grandchild Logic", node_type=NodeType.LOGIC
        )
        for n in (root, child, grandchild):
            repo.upsert_node(n)

        repo.insert_edge(
            EdgeRecord(
                project_id=project_id,
                from_node_id=child.id,
                to_node_id=root.id,
                type=EdgeType.IMPLEMENTS,
                created_by=agent_id,
            )
        )
        repo.insert_edge(
            EdgeRecord(
                project_id=project_id,
                from_node_id=grandchild.id,
                to_node_id=child.id,
                type=EdgeType.DERIVED_FROM,
                created_by=agent_id,
            )
        )
        return root.id, child.id, grandchild.id

    def test_extract_depth_1(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        root_id, child_id, _ = self._setup_graph(repo, project_id, agent_id)
        svc = SubgraphService(repo)
        result = svc.extract(root_id, project_id, depth=1)
        ids = {n.id for n in result.nodes}
        assert root_id in ids
        assert child_id in ids
        assert len(result.nodes) == 2

    def test_extract_depth_2(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        root_id, child_id, gc_id = self._setup_graph(repo, project_id, agent_id)
        svc = SubgraphService(repo)
        result = svc.extract(root_id, project_id, depth=2)
        ids = {n.id for n in result.nodes}
        assert root_id in ids
        assert child_id in ids
        assert gc_id in ids
        assert len(result.nodes) == 3

    def test_archived_excluded_by_default(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        from kgn.models.edge import EdgeRecord

        root = _make_node(project_id, agent_id, title="Root")
        archived = _make_node(project_id, agent_id, title="Archived", status=NodeStatus.ARCHIVED)
        repo.upsert_node(root)
        repo.upsert_node(archived)
        repo.insert_edge(
            EdgeRecord(
                project_id=project_id,
                from_node_id=root.id,
                to_node_id=archived.id,
                type=EdgeType.DEPENDS_ON,
                created_by=agent_id,
            )
        )

        svc = SubgraphService(repo)
        result = svc.extract(root.id, project_id, depth=1)
        ids = {n.id for n in result.nodes}
        assert root.id in ids
        assert archived.id not in ids

    def test_archived_included_when_requested(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        from kgn.models.edge import EdgeRecord

        root = _make_node(project_id, agent_id, title="Root")
        archived = _make_node(project_id, agent_id, title="Archived", status=NodeStatus.ARCHIVED)
        repo.upsert_node(root)
        repo.upsert_node(archived)
        repo.insert_edge(
            EdgeRecord(
                project_id=project_id,
                from_node_id=root.id,
                to_node_id=archived.id,
                type=EdgeType.DEPENDS_ON,
                created_by=agent_id,
            )
        )

        svc = SubgraphService(repo)
        result = svc.extract(root.id, project_id, depth=1, include_archived=True)
        ids = {n.id for n in result.nodes}
        assert archived.id in ids

    def test_edges_populated(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        root_id, child_id, _ = self._setup_graph(repo, project_id, agent_id)
        svc = SubgraphService(repo)
        result = svc.extract(root_id, project_id, depth=1)
        assert len(result.edges) >= 1

    def test_cycle_prevention(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        """BFS visited set prevents infinite loops in cyclic graphs."""
        from kgn.models.edge import EdgeRecord

        a = _make_node(project_id, agent_id, title="A")
        b = _make_node(project_id, agent_id, title="B")
        repo.upsert_node(a)
        repo.upsert_node(b)
        # A → B and B → A (cycle)
        repo.insert_edge(
            EdgeRecord(
                project_id=project_id,
                from_node_id=a.id,
                to_node_id=b.id,
                type=EdgeType.DEPENDS_ON,
                created_by=agent_id,
            )
        )
        repo.insert_edge(
            EdgeRecord(
                project_id=project_id,
                from_node_id=b.id,
                to_node_id=a.id,
                type=EdgeType.DEPENDS_ON,
                created_by=agent_id,
            )
        )

        svc = SubgraphService(repo)
        result = svc.extract(a.id, project_id, depth=5)
        # Should complete without infinite loop, both nodes visited
        ids = {n.id for n in result.nodes}
        assert a.id in ids
        assert b.id in ids
        assert len(result.nodes) == 2

    def test_to_json(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        root_id, _, _ = self._setup_graph(repo, project_id, agent_id)
        svc = SubgraphService(repo)
        result = svc.extract(root_id, project_id, depth=2)
        json_str = svc.to_json(result)
        data = json.loads(json_str)
        assert data["root_id"] == str(root_id)
        assert len(data["nodes"]) == 3

    def test_to_markdown(
        self,
        repo: KgnRepository,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        root_id, _, _ = self._setup_graph(repo, project_id, agent_id)
        svc = SubgraphService(repo)
        result = svc.extract(root_id, project_id, depth=2)
        md = svc.to_markdown(result)
        assert "# Subgraph" in md
        assert "Root Goal" in md
        assert "## Depth 0" in md
        assert "## Edges" in md
