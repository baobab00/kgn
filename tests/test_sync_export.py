"""Tests for kgn.sync.export_service — DB → file system export."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from kgn.models.edge import EdgeRecord
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.sync.export_service import ExportResult, ExportService

# ── Fixtures ──────────────────────────────────────────────────────────

PROJECT_NAME = "test-project"
PROJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
AGENT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
NODE_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
NODE_ID_2 = uuid.UUID("44444444-4444-4444-4444-444444444444")
CREATED_AT = datetime(2026, 3, 2, 12, 0, 0, tzinfo=UTC)


def _make_node(
    *,
    node_id: uuid.UUID = NODE_ID,
    title: str = "Test Node",
    node_type: NodeType = NodeType.SPEC,
    status: NodeStatus = NodeStatus.ACTIVE,
    body_md: str = "## Context\n\nTest content.",
    tags: list[str] | None = None,
    confidence: float | None = None,
) -> NodeRecord:
    return NodeRecord(
        id=node_id,
        project_id=PROJECT_ID,
        type=node_type,
        status=status,
        title=title,
        body_md=body_md,
        tags=tags or [],
        confidence=confidence,
        created_by=AGENT_ID,
        created_at=CREATED_AT,
    )


def _make_edge(
    *,
    from_id: uuid.UUID = NODE_ID,
    to_id: uuid.UUID = NODE_ID_2,
    edge_type: EdgeType = EdgeType.IMPLEMENTS,
    note: str = "",
) -> EdgeRecord:
    return EdgeRecord(
        project_id=PROJECT_ID,
        from_node_id=from_id,
        to_node_id=to_id,
        type=edge_type,
        note=note,
        created_by=AGENT_ID,
        created_at=CREATED_AT,
    )


# ── Export Service Tests (DB-integrated) ──────────────────────────────


class TestExportServiceDB:
    """Integration tests that use real DB fixtures."""

    def test_export_nodes(self, repo, project_id, agent_id, tmp_path):
        """Exported nodes produce .kgn files in correct directory structure."""
        # Insert test nodes
        node = NodeRecord(
            id=uuid.uuid4(),
            project_id=project_id,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="Export Test Node",
            body_md="## Content\n\nTest body.",
            created_by=agent_id,
        )
        repo.upsert_node(node)

        service = ExportService(repo)
        # We need project name — look it up
        proj_name = f"export-test-{uuid.uuid4().hex[:6]}"
        # Use the actual project_id from fixture
        result = service.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
            agent_id="test-agent",
        )

        assert result.exported >= 1
        assert result.error_count == 0

        # Verify file exists
        from kgn.sync.layout import find_kgn_files, project_dir

        proj_dir = project_dir(tmp_path, proj_name)
        kgn_files = find_kgn_files(proj_dir)
        assert len(kgn_files) >= 1

    def test_export_edges(self, repo, project_id, agent_id, tmp_path):
        """Exported edges produce .kge files."""
        # Insert two nodes + edge
        node_a = NodeRecord(
            id=uuid.uuid4(),
            project_id=project_id,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="Node A",
            body_md="Content A",
            created_by=agent_id,
        )
        node_b = NodeRecord(
            id=uuid.uuid4(),
            project_id=project_id,
            type=NodeType.LOGIC,
            status=NodeStatus.ACTIVE,
            title="Node B",
            body_md="Content B",
            created_by=agent_id,
        )
        repo.upsert_node(node_a)
        repo.upsert_node(node_b)

        edge = EdgeRecord(
            project_id=project_id,
            from_node_id=node_a.id,
            to_node_id=node_b.id,
            type=EdgeType.IMPLEMENTS,
            note="test edge",
            created_by=agent_id,
        )
        repo.insert_edge(edge)

        service = ExportService(repo)
        proj_name = f"export-edge-{uuid.uuid4().hex[:6]}"
        result = service.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
        )

        assert result.exported >= 3  # 2 nodes + 1 edge
        assert result.error_count == 0

        from kgn.sync.layout import find_kge_files, project_dir

        proj_dir = project_dir(tmp_path, proj_name)
        kge_files = find_kge_files(proj_dir)
        assert len(kge_files) >= 1

    def test_export_skips_unchanged(self, repo, project_id, agent_id, tmp_path):
        """Second export skips files with identical content."""
        node = NodeRecord(
            id=uuid.uuid4(),
            project_id=project_id,
            type=NodeType.GOAL,
            status=NodeStatus.ACTIVE,
            title="Idempotent Node",
            body_md="Same content.",
            created_by=agent_id,
        )
        repo.upsert_node(node)

        service = ExportService(repo)
        proj_name = f"idempotent-{uuid.uuid4().hex[:6]}"

        # First export
        r1 = service.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
        )
        assert r1.exported >= 1

        # Second export — same content
        r2 = service.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
        )
        assert r2.skipped >= 1
        assert r2.exported == 0

    def test_export_creates_sync_metadata(self, repo, project_id, agent_id, tmp_path):
        """Export creates .kgn-sync.json."""
        service = ExportService(repo)
        proj_name = f"meta-{uuid.uuid4().hex[:6]}"
        service.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
        )

        meta_path = tmp_path / ".kgn-sync.json"
        assert meta_path.exists()

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["version"] == "1.0"
        assert meta["project"] == proj_name
        assert "last_export" in meta

    def test_export_deletes_orphans(self, repo, project_id, agent_id, tmp_path):
        """Files not backed by DB records are cleaned up."""
        proj_name = f"orphan-{uuid.uuid4().hex[:6]}"

        # Create an orphan file
        orphan_dir = tmp_path / proj_name / "nodes" / "SPEC"
        orphan_dir.mkdir(parents=True)
        orphan_file = orphan_dir / "orphan-00000000.kgn"
        orphan_file.write_text("---\nkgn_version: '1.0'\n---\n", encoding="utf-8")

        service = ExportService(repo)
        result = service.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
        )

        assert result.deleted >= 1
        assert not orphan_file.exists()


# ── ExportResult Tests ────────────────────────────────────────────────


class TestExportResult:
    def test_total_count(self):
        r = ExportResult(exported=3, skipped=2)
        assert r.total == 5

    def test_error_count(self):
        r = ExportResult(errors=["a", "b"])
        assert r.error_count == 2

    def test_empty_result(self):
        r = ExportResult()
        assert r.total == 0
        assert r.error_count == 0
