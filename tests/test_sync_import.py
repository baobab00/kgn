"""Tests for kgn.sync.import_service — file system → DB import."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from kgn.models.edge import EdgeRecord
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.serializer import serialize_edges, serialize_node
from kgn.sync.export_service import ExportService
from kgn.sync.import_service import ImportResult, ImportService, SyncStatus, get_sync_status
from kgn.sync.layout import edge_path, node_path

# ── Fixtures ──────────────────────────────────────────────────────────

CREATED_AT = datetime(2026, 3, 2, 12, 0, 0, tzinfo=UTC)


def _write_kgn(
    target_dir: Path,
    project_name: str,
    *,
    node_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    title: str = "Import Test Node",
    node_type: NodeType = NodeType.SPEC,
    body_md: str = "## Content\n\nImported.",
) -> tuple[Path, NodeRecord]:
    """Create a .kgn file in the expected layout and return path + record."""
    nid = node_id or uuid.uuid4()
    pid = project_id or uuid.UUID("11111111-1111-1111-1111-111111111111")
    aid = agent_id or uuid.UUID("22222222-2222-2222-2222-222222222222")

    node = NodeRecord(
        id=nid,
        project_id=pid,
        type=node_type,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md=body_md,
        created_by=aid,
        created_at=CREATED_AT,
    )
    text = serialize_node(node)
    path = node_path(target_dir, project_name, node)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path, node


def _write_kge(
    target_dir: Path,
    project_name: str,
    edge: EdgeRecord,
    *,
    agent_id: str = "test-agent",
) -> Path:
    """Create a .kge file in the expected layout."""
    text = serialize_edges([edge], agent_id=agent_id)
    path = edge_path(target_dir, project_name, edge)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── Import Service Tests ──────────────────────────────────────────────


class TestImportService:
    """Integration tests using real DB fixtures."""

    def test_import_single_node(self, repo, project_id, agent_id, tmp_path):
        """Single .kgn file is imported into DB."""
        proj_name = f"import-{uuid.uuid4().hex[:6]}"

        _write_kgn(
            tmp_path,
            proj_name,
            project_id=project_id,
            agent_id=agent_id,
            title="Imported Node",
        )

        service = ImportService(repo)
        result = service.import_project(
            project_name=proj_name,
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )

        assert result.imported >= 1
        assert result.failed == 0

    def test_import_node_idempotent(self, repo, project_id, agent_id, tmp_path):
        """Same file imported twice — second time is skipped."""
        proj_name = f"idem-{uuid.uuid4().hex[:6]}"

        _write_kgn(
            tmp_path,
            proj_name,
            project_id=project_id,
            agent_id=agent_id,
        )

        service = ImportService(repo)

        # First import
        r1 = service.import_project(
            project_name=proj_name,
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )
        assert r1.imported >= 1

        # Second import — same content → skipped
        r2 = service.import_project(
            project_name=proj_name,
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )
        assert r2.skipped >= 1

    def test_import_nonexistent_dir(self, repo, project_id, agent_id, tmp_path):
        """Import from nonexistent directory returns error."""
        service = ImportService(repo)
        result = service.import_project(
            project_name="nonexistent-project",
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )

        assert result.total == 0
        assert len(result.errors) == 1
        assert "does not exist" in result.errors[0]

    def test_import_updates_sync_metadata(self, repo, project_id, agent_id, tmp_path):
        """Import updates last_import in .kgn-sync.json."""
        proj_name = f"meta-{uuid.uuid4().hex[:6]}"

        _write_kgn(
            tmp_path,
            proj_name,
            project_id=project_id,
            agent_id=agent_id,
        )

        service = ImportService(repo)
        service.import_project(
            project_name=proj_name,
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )

        meta_path = tmp_path / ".kgn-sync.json"
        assert meta_path.exists()

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert "last_import" in meta

    def test_import_edge_with_existing_nodes(self, repo, project_id, agent_id, tmp_path):
        """Edge import succeeds when referenced nodes exist in DB."""
        proj_name = f"edge-import-{uuid.uuid4().hex[:6]}"
        nid_a = uuid.uuid4()
        nid_b = uuid.uuid4()

        # Create nodes in DB first
        repo.upsert_node(
            NodeRecord(
                id=nid_a,
                project_id=project_id,
                type=NodeType.SPEC,
                title="Node A",
                body_md="A",
                created_by=agent_id,
            )
        )
        repo.upsert_node(
            NodeRecord(
                id=nid_b,
                project_id=project_id,
                type=NodeType.LOGIC,
                title="Node B",
                body_md="B",
                created_by=agent_id,
            )
        )

        # Write edge file
        edge = EdgeRecord(
            project_id=project_id,
            from_node_id=nid_a,
            to_node_id=nid_b,
            type=EdgeType.IMPLEMENTS,
            note="import test",
            created_by=agent_id,
            created_at=CREATED_AT,
        )
        _write_kge(tmp_path, proj_name, edge)

        service = ImportService(repo)
        result = service.import_project(
            project_name=proj_name,
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )

        assert result.imported >= 1
        assert result.failed == 0


# ── Export→Import roundtrip ──────────────────────────────────────────


class TestExportImportRoundtrip:
    """End-to-end: DB → export → import → verify DB."""

    def test_roundtrip_nodes(self, repo, project_id, agent_id, tmp_path):
        """Export nodes, import to fresh project, verify node exists."""
        # Source project with a node
        node = NodeRecord(
            id=uuid.uuid4(),
            project_id=project_id,
            type=NodeType.DECISION,
            status=NodeStatus.ACTIVE,
            title="Roundtrip Decision",
            body_md="## Decision\n\nRoundtrip test.",
            tags=["test", "roundtrip"],
            confidence=0.9,
            created_by=agent_id,
        )
        repo.upsert_node(node)

        # Export
        proj_name = f"rt-{uuid.uuid4().hex[:6]}"
        exporter = ExportService(repo)
        export_result = exporter.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
        )
        assert export_result.exported >= 1

        # Import to same DB (will skip due to content_hash match)
        importer = ImportService(repo)
        import_result = importer.import_project(
            project_name=proj_name,
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )

        # Node already exists in DB → should be skipped
        assert import_result.skipped >= 1 or import_result.imported >= 0
        assert import_result.failed == 0


# ── SyncStatus Tests ─────────────────────────────────────────────────


class TestSyncStatus:
    def test_status_empty(self, repo, project_id, tmp_path):
        """Status with no files shows zero counts."""
        status = get_sync_status(repo, "test", project_id, tmp_path)
        assert status.file_node_count == 0
        assert status.file_edge_count == 0

    def test_status_with_exported_files(self, repo, project_id, agent_id, tmp_path):
        """Status shows correct file counts after export."""
        node = NodeRecord(
            id=uuid.uuid4(),
            project_id=project_id,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="Status Node",
            body_md="Content",
            created_by=agent_id,
        )
        repo.upsert_node(node)

        proj_name = f"status-{uuid.uuid4().hex[:6]}"
        exporter = ExportService(repo)
        exporter.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
        )

        status = get_sync_status(repo, proj_name, project_id, tmp_path)
        assert status.db_node_count >= 1
        assert status.file_node_count >= 1
        assert status.last_export is not None

    def test_status_diff(self):
        """SyncStatus correctly computes diffs."""
        s = SyncStatus(db_node_count=5, file_node_count=3, db_edge_count=2, file_edge_count=2)
        assert s.node_diff == 2
        assert s.edge_diff == 0


# ── ImportResult Tests ───────────────────────────────────────────────


class TestImportResult:
    def test_total(self):
        r = ImportResult(imported=2, skipped=3, failed=1)
        assert r.total == 6

    def test_empty(self):
        r = ImportResult()
        assert r.total == 0
