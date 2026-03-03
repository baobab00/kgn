"""Tests for import/export service error paths and migrations apply path.

Step 8 coverage gaps:
  - import_service.py: kgn/kge import errors, empty dir, metadata decode error (L107, 123-126, 134-137, 160, 171-172, 214-215)
  - export_service.py: node/edge serialization errors, orphan cleanup, metadata JSON error (L107-109, 122-125, 187-189, 209-210)
  - migrations.py: apply new migration path (L45, 67-71)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch

from kgn.db.migrations import run_migrations
from kgn.models.edge import EdgeRecord
from kgn.models.enums import NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(
    project_id: uuid.UUID,
    *,
    title: str = "Test",
    node_type: NodeType = NodeType.SPEC,
) -> NodeRecord:
    return NodeRecord(
        id=uuid.uuid4(),
        project_id=project_id,
        type=node_type,
        status=NodeStatus.ACTIVE,
        title=title,
        body_md="## C\n\nBody.",
        content_hash=uuid.uuid4().hex,
        tags=["test"],
    )


def _write_kgn_file(
    path: Path, *, project: str, title: str = "Test", node_id: str | None = None
) -> Path:
    nid = node_id or f"new:{uuid.uuid4().hex[:8]}"
    content = (
        f'---\nkgn_version: "0.1"\nid: "{nid}"\ntype: SPEC\ntitle: "{title}"\n'
        f'status: ACTIVE\nproject_id: "{project}"\nagent_id: "test"\n'
        f'created_at: "2026-01-01T00:00:00+09:00"\ntags: ["test"]\nconfidence: 0.9\n'
        f"---\n\n## Context\n\nBody.\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def _write_kge_file(path: Path, *, from_id: str, to_id: str, project: str) -> Path:
    content = (
        f'---\nkgn_version: "0.1"\nproject_id: "{project}"\nagent_id: "test"\n'
        f'created_at: "2026-01-01T00:00:00+09:00"\n'
        f'edges:\n  - from: "{from_id}"\n    to: "{to_id}"\n'
        f'    type: DEPENDS_ON\n    note: "test"\n---\n'
    )
    path.write_text(content, encoding="utf-8")
    return path


# ══════════════════════════════════════════════════════════════════════
# ImportService error paths
# ══════════════════════════════════════════════════════════════════════


class TestImportServiceErrors:
    def test_import_kgn_file_parse_error(
        self, db_conn, repo, project_id, agent_id, tmp_path: Path
    ) -> None:
        """Import a broken .kgn file → failed + 1, error recorded."""
        from kgn.sync.import_service import ImportService

        proj_dir = tmp_path / "test-proj" / "SPEC"
        proj_dir.mkdir(parents=True)
        bad_file = proj_dir / "broken.kgn"
        bad_file.write_text("this is totally invalid kgn content", encoding="utf-8")

        svc = ImportService(repo)
        result = svc.import_project(
            project_name="test-proj",
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )

        assert result.failed >= 1
        assert len(result.errors) >= 1

    def test_import_kge_file_parse_error(
        self, db_conn, repo, project_id, agent_id, tmp_path: Path
    ) -> None:
        """Import a broken .kge file → failed + 1, error recorded."""
        from kgn.sync.import_service import ImportService

        proj_dir = tmp_path / "test-proj" / "edges"
        proj_dir.mkdir(parents=True)
        bad_file = proj_dir / "broken.kge"
        bad_file.write_text("bad edge content", encoding="utf-8")

        svc = ImportService(repo)
        result = svc.import_project(
            project_name="test-proj",
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )

        assert result.failed >= 1
        assert len(result.errors) >= 1

    def test_import_empty_directory(
        self, db_conn, repo, project_id, agent_id, tmp_path: Path
    ) -> None:
        """Empty project directory → 0 imported, 0 failed."""
        from kgn.sync.import_service import ImportService

        proj_dir = tmp_path / "test-proj"
        proj_dir.mkdir(parents=True)

        svc = ImportService(repo)
        result = svc.import_project(
            project_name="test-proj",
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )

        assert result.total == 0

    def test_import_nonexistent_directory(
        self, db_conn, repo, project_id, agent_id, tmp_path: Path
    ) -> None:
        """Non-existent directory → error recorded."""
        from kgn.sync.import_service import ImportService

        svc = ImportService(repo)
        result = svc.import_project(
            project_name="no-such-project",
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )

        assert len(result.errors) >= 1
        assert "does not exist" in result.errors[0]

    def test_import_metadata_decode_error(
        self, db_conn, repo, project_id, agent_id, tmp_path: Path
    ) -> None:
        """Corrupt .kgn-sync.json → still succeeds (metadata silently reset)."""
        from kgn.sync.import_service import ImportService

        proj_dir = tmp_path / "test-proj" / "SPEC"
        proj_dir.mkdir(parents=True)
        _write_kgn_file(proj_dir / "node.kgn", project="test-proj")

        # Write corrupt sync metadata
        meta_file = tmp_path / ".kgn-sync.json"
        meta_file.write_text("{broken json content!!!}", encoding="utf-8")

        svc = ImportService(repo)
        result = svc.import_project(
            project_name="test-proj",
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )

        # Import should still succeed
        assert result.imported >= 1

        # Metadata file should be updated despite corrupt input
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        assert "last_import" in meta


# ══════════════════════════════════════════════════════════════════════
# ExportService error paths
# ══════════════════════════════════════════════════════════════════════


class TestExportServiceErrors:
    def test_export_node_serialization_error(
        self, db_conn, repo, project_id, tmp_path: Path
    ) -> None:
        """Node serialization error → error logged but export continues."""
        from kgn.sync.export_service import ExportService

        node = _make_node(project_id, title="Good node")
        repo.upsert_node(node)

        svc = ExportService(repo)

        # Mock serialize_node to fail for this specific node
        with patch(
            "kgn.sync.export_service.serialize_node",
            side_effect=ValueError("serialize boom"),
        ):
            result = svc.export_project(
                project_name="test-proj",
                project_id=project_id,
                target_dir=tmp_path,
            )

        assert result.error_count >= 1
        assert "serialize boom" in result.errors[0]

    def test_export_edge_serialization_error(
        self, db_conn, repo, project_id, tmp_path: Path
    ) -> None:
        """Edge serialization error → error logged but export continues."""
        from kgn.sync.export_service import ExportService

        n1 = _make_node(project_id, title="N1")
        n2 = _make_node(project_id, title="N2")
        repo.upsert_node(n1)
        repo.upsert_node(n2)

        edge = EdgeRecord(
            from_node_id=n1.id,
            to_node_id=n2.id,
            type="DEPENDS_ON",
            project_id=project_id,
            note="test",
        )
        repo.insert_edge(edge)

        svc = ExportService(repo)

        # Mock serialize_edges to fail
        with patch(
            "kgn.sync.export_service.serialize_edges",
            side_effect=ValueError("edge serialize boom"),
        ):
            result = svc.export_project(
                project_name="test-proj",
                project_id=project_id,
                target_dir=tmp_path,
            )

        assert result.error_count >= 1
        assert "edge serialize boom" in result.errors[0]

    def test_export_orphan_cleanup(self, db_conn, repo, project_id, tmp_path: Path) -> None:
        """Orphan files on disk but not in DB → deleted."""
        from kgn.sync.export_service import ExportService

        # Create orphan .kgn file on disk
        orphan_dir = tmp_path / "test-proj" / "SPEC"
        orphan_dir.mkdir(parents=True)
        orphan_file = orphan_dir / "orphan.kgn"
        orphan_file.write_text("old content", encoding="utf-8")

        svc = ExportService(repo)
        result = svc.export_project(
            project_name="test-proj",
            project_id=project_id,
            target_dir=tmp_path,
        )

        assert result.deleted >= 1
        assert not orphan_file.exists()

    def test_export_metadata_corrupt_json(self, db_conn, repo, project_id, tmp_path: Path) -> None:
        """Corrupt .kgn-sync.json → overwritten with fresh metadata."""
        from kgn.sync.export_service import ExportService

        # Write corrupt sync metadata
        meta_file = tmp_path / ".kgn-sync.json"
        meta_file.write_text("not json!", encoding="utf-8")

        svc = ExportService(repo)
        svc.export_project(
            project_name="test-proj",
            project_id=project_id,
            target_dir=tmp_path,
        )

        # Should succeed; metadata should be valid JSON now
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        assert meta["project"] == "test-proj"
        assert "last_export" in meta

    def test_get_sync_status_corrupt_metadata(
        self, db_conn, repo, project_id, tmp_path: Path
    ) -> None:
        """get_sync_status with corrupt metadata → returns empty status fields."""
        from kgn.sync.import_service import get_sync_status

        meta_file = tmp_path / ".kgn-sync.json"
        meta_file.write_text("{BAD JSON!!", encoding="utf-8")

        status = get_sync_status(repo, "test-proj", project_id, tmp_path)
        assert status.last_export is None
        assert status.last_import is None


# ══════════════════════════════════════════════════════════════════════
# Migrations — apply new migration path
# ══════════════════════════════════════════════════════════════════════


class TestMigrationsApplyPath:
    def test_run_migrations_applies_new(self, db_conn, tmp_path: Path) -> None:
        """A new migration SQL file → applied and recorded."""
        # Create a minimal SQL migration in a temp dir
        sql_file = tmp_path / "999_test_migration.sql"
        sql_file.write_text(
            "CREATE TABLE IF NOT EXISTS _test_step8_migration (id serial PRIMARY KEY);",
            encoding="utf-8",
        )

        with patch("kgn.db.migrations.MIGRATIONS_DIR", tmp_path):
            # First time: should apply the migration
            applied = run_migrations(db_conn)
            assert len(applied) == 1
            assert "999_test_migration.sql" in applied

            # Second time: should skip it
            applied2 = run_migrations(db_conn)
            assert len(applied2) == 0

        # Clean up the test table
        db_conn.execute("DROP TABLE IF EXISTS _test_step8_migration")

    def test_run_migrations_no_files(self, db_conn, tmp_path: Path) -> None:
        """Empty migration directory → no migrations applied."""
        with patch("kgn.db.migrations.MIGRATIONS_DIR", tmp_path):
            applied = run_migrations(db_conn)
        assert applied == []
