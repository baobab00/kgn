"""Phase 7 / Step 7 — Large-scale stress + concurrency verification (R-033, R-034).

Tests:
  1. 100-node + 50-edge roundtrip (ingest → export → verify files → reimport idempotent)
  2. 200-node + 100-edge large scale
  3. 5 / 50 / 100 / 200 node performance baseline measurement
  4. Concurrency scenarios x3 (ThreadPoolExecutor)
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import pytest
import structlog

from kgn.db.repository import KgnRepository
from kgn.models.edge import EdgeRecord
from kgn.models.enums import EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord
from kgn.sync.export_service import ExportService
from kgn.sync.import_service import ImportService
from kgn.sync.layout import find_kge_files, find_kgn_files

log = structlog.get_logger()

# ── Helpers ────────────────────────────────────────────────────────────

NODE_TYPES = list(NodeType)
EDGE_TYPES = [EdgeType.DEPENDS_ON, EdgeType.IMPLEMENTS, EdgeType.DERIVED_FROM]
CREATED_AT = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)


def _generate_nodes(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    count: int,
    *,
    prefix: str = "Stress",
) -> list[NodeRecord]:
    """Generate *count* unique NodeRecords with diverse types."""
    nodes: list[NodeRecord] = []
    for i in range(count):
        node = NodeRecord(
            id=uuid.uuid4(),
            project_id=project_id,
            type=NODE_TYPES[i % len(NODE_TYPES)],
            status=NodeStatus.ACTIVE,
            title=f"{prefix} Node {i:04d}",
            body_md=(
                f"## Context\n\n{prefix} test node number {i}.\n\n"
                "Some filler content to make it realistic."
            ),
            tags=["stress", f"batch-{i // 10}"],
            confidence=round(0.5 + (i % 5) * 0.1, 2),
            created_by=agent_id,
            created_at=CREATED_AT,
        )
        nodes.append(node)
    return nodes


def _generate_edges(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    nodes: list[NodeRecord],
    count: int,
) -> list[EdgeRecord]:
    """Generate *count* edges between random (but valid) distinct nodes."""
    import random

    rng = random.Random(42)
    edges: list[EdgeRecord] = []
    seen: set[tuple[uuid.UUID, uuid.UUID, EdgeType]] = set()

    for _ in range(count * 3):  # attempt budget
        if len(edges) >= count:
            break
        from_node = rng.choice(nodes)
        to_node = rng.choice(nodes)
        if from_node.id == to_node.id:
            continue
        edge_type = rng.choice(EDGE_TYPES)
        key = (from_node.id, to_node.id, edge_type)
        if key in seen:
            continue
        seen.add(key)
        edge = EdgeRecord(
            project_id=project_id,
            from_node_id=from_node.id,
            to_node_id=to_node.id,
            type=edge_type,
            note=f"stress edge {len(edges)}",
            created_by=agent_id,
            created_at=CREATED_AT,
        )
        edges.append(edge)

    return edges


def _ingest_bulk(
    repo: KgnRepository,
    nodes: list[NodeRecord],
    edges: list[EdgeRecord],
) -> None:
    """Bulk-insert nodes then edges into DB."""
    for node in nodes:
        repo.upsert_node(node)
    for edge in edges:
        repo.insert_edge(edge)


# ═══════════════════════════════════════════════════════════════════════
# 1. Large-scale roundtrip test (R-033)
# ═══════════════════════════════════════════════════════════════════════


class TestLargeScaleRoundtrip:
    """100+ node roundtrip: ingest → export → verify files → reimport (idempotent)."""

    def test_100_nodes_50_edges_roundtrip(self, repo, project_id, agent_id, tmp_path):
        """R-033: 100 nodes + 50 edges — export + file verification ≤5s."""
        # ── Generate & ingest ─────────────────────────────────────────
        nodes = _generate_nodes(project_id, agent_id, 100)
        edges = _generate_edges(project_id, agent_id, nodes, 50)
        _ingest_bulk(repo, nodes, edges)

        # Verify DB state
        db_nodes = repo.search_nodes(project_id, exclude_archived=False)
        assert len(db_nodes) == 100

        db_edges = repo.search_edges(project_id)
        assert len(db_edges) == 50

        # ── Export ────────────────────────────────────────────────────
        proj_name = f"stress100-{uuid.uuid4().hex[:6]}"
        export_svc = ExportService(repo)

        t_start = time.perf_counter()
        export_result = export_svc.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
        )
        t_export = time.perf_counter() - t_start

        assert export_result.exported == 150  # 100 nodes + 50 edges
        assert export_result.error_count == 0

        # ── Verify exported files ─────────────────────────────────────
        proj_dir = tmp_path / proj_name
        kgn_files = find_kgn_files(proj_dir)
        kge_files = find_kge_files(proj_dir)
        assert len(kgn_files) == 100, f"Expected 100 .kgn files, got {len(kgn_files)}"
        assert len(kge_files) == 50, f"Expected 50 .kge files, got {len(kge_files)}"

        # ── Re-import into SAME project — nodes get UPDATED (bulk-inserted
        #    nodes lack content_hash so V8-skip doesn't trigger, but it must
        #    complete without errors).
        import_svc = ImportService(repo)
        t_start = time.perf_counter()
        import_result = import_svc.import_project(
            project_name=proj_name,
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )
        t_import = time.perf_counter() - t_start

        assert import_result.failed == 0
        assert (import_result.imported + import_result.skipped) >= 100

        # ── Performance assertion ─────────────────────────────────────
        total_time = t_export + t_import
        log.info(
            "stress.100_roundtrip",
            export_s=round(t_export, 3),
            import_s=round(t_import, 3),
            total_s=round(total_time, 3),
        )
        assert total_time < 5.0, f"100-node roundtrip took {total_time:.2f}s (expected <5s)"

    def test_200_nodes_100_edges_roundtrip(self, repo, project_id, agent_id, tmp_path):
        """R-033: 200 nodes + 100 edges — export + verify ≤10s."""
        nodes = _generate_nodes(project_id, agent_id, 200)
        edges = _generate_edges(project_id, agent_id, nodes, 100)
        _ingest_bulk(repo, nodes, edges)

        db_nodes = repo.search_nodes(project_id, exclude_archived=False)
        assert len(db_nodes) == 200

        proj_name = f"stress200-{uuid.uuid4().hex[:6]}"
        export_svc = ExportService(repo)

        t_start = time.perf_counter()
        export_result = export_svc.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
        )
        t_export = time.perf_counter() - t_start

        assert export_result.exported >= 200  # 200 nodes + ~100 edges
        assert export_result.error_count == 0

        # Verify files
        proj_dir = tmp_path / proj_name
        kgn_files = find_kgn_files(proj_dir)
        assert len(kgn_files) == 200

        # Re-import into same project
        import_svc = ImportService(repo)
        t_start = time.perf_counter()
        import_result = import_svc.import_project(
            project_name=proj_name,
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )
        t_import = time.perf_counter() - t_start

        assert import_result.failed == 0
        assert (import_result.imported + import_result.skipped) >= 200

        total_time = t_export + t_import
        log.info(
            "stress.200_roundtrip",
            export_s=round(t_export, 3),
            import_s=round(t_import, 3),
            total_s=round(total_time, 3),
        )
        assert total_time < 10.0, f"200-node roundtrip took {total_time:.2f}s (expected <10s)"

    def test_export_idempotent(self, repo, project_id, agent_id, tmp_path):
        """Second export skips all — verifies content-hash detection."""
        nodes = _generate_nodes(project_id, agent_id, 20)
        _ingest_bulk(repo, nodes, [])

        proj_name = f"idempotent-{uuid.uuid4().hex[:6]}"
        svc = ExportService(repo)

        r1 = svc.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
        )
        assert r1.exported == 20
        assert r1.skipped == 0

        r2 = svc.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
        )
        assert r2.exported == 0
        assert r2.skipped == 20

    def test_reimport_idempotent(self, repo, project_id, agent_id, tmp_path):
        """Re-importing yields SKIP on second pass (content_hash match)."""
        nodes = _generate_nodes(project_id, agent_id, 30)
        edges = _generate_edges(project_id, agent_id, nodes, 15)
        _ingest_bulk(repo, nodes, edges)

        proj_name = f"reimport-idem-{uuid.uuid4().hex[:6]}"
        export_svc = ExportService(repo)
        export_svc.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
        )

        # First import — updates nodes (bulk-inserted lack content_hash)
        import_svc = ImportService(repo)
        r1 = import_svc.import_project(
            project_name=proj_name,
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )
        assert r1.failed == 0
        assert (r1.imported + r1.skipped) >= 30

        # Second import — now content_hash is set → should skip all
        r2 = import_svc.import_project(
            project_name=proj_name,
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )
        assert r2.failed == 0
        assert r2.skipped >= 30, f"Expected ≥30 skipped on 2nd import, got {r2.skipped}"


# ═══════════════════════════════════════════════════════════════════════
# 2. Performance baseline measurement
# ═══════════════════════════════════════════════════════════════════════


class TestPerformanceBaseline:
    """Measure ingest+export times for 5/50/100/200 nodes."""

    @pytest.mark.parametrize(
        "node_count,edge_count,time_limit",
        [
            (5, 3, 2.0),
            (50, 25, 5.0),
            (100, 50, 8.0),
            (200, 100, 15.0),
        ],
        ids=["5-nodes", "50-nodes", "100-nodes", "200-nodes"],
    )
    def test_performance_baseline(
        self,
        repo,
        project_id,
        agent_id,
        tmp_path,
        node_count,
        edge_count,
        time_limit,
    ):
        """Ingest + export within time_limit seconds."""
        nodes = _generate_nodes(project_id, agent_id, node_count)
        edges = _generate_edges(project_id, agent_id, nodes, edge_count)

        # ── Ingest ────────────────────────────────────────────────────
        t_start = time.perf_counter()
        _ingest_bulk(repo, nodes, edges)
        t_ingest = time.perf_counter() - t_start

        # ── Export ────────────────────────────────────────────────────
        proj_name = f"perf-{node_count}-{uuid.uuid4().hex[:6]}"
        export_svc = ExportService(repo)
        t_start = time.perf_counter()
        export_result = export_svc.export_project(
            project_name=proj_name,
            project_id=project_id,
            target_dir=tmp_path,
        )
        t_export = time.perf_counter() - t_start

        assert export_result.error_count == 0

        # ── Import (idempotent, same project) ─────────────────────────
        import_svc = ImportService(repo)
        t_start = time.perf_counter()
        import_result = import_svc.import_project(
            project_name=proj_name,
            project_id=project_id,
            agent_id=agent_id,
            source_dir=tmp_path,
        )
        t_import = time.perf_counter() - t_start

        assert import_result.failed == 0

        total = t_ingest + t_export + t_import
        log.info(
            "perf.baseline",
            nodes=node_count,
            edges=edge_count,
            ingest_s=round(t_ingest, 3),
            export_s=round(t_export, 3),
            import_s=round(t_import, 3),
            total_s=round(total, 3),
        )
        assert total < time_limit, f"{node_count} nodes roundtrip: {total:.2f}s > {time_limit}s"


# ═══════════════════════════════════════════════════════════════════════
# 3. Concurrency E2E test (R-034)
# ═══════════════════════════════════════════════════════════════════════


class TestConcurrency:
    """Thread-based concurrency tests using ThreadPoolExecutor.

    Workers use independent connections via get_connection() to simulate
    real concurrent agent access.  Data setup is committed explicitly so
    that worker connections can see it.
    """

    def test_concurrent_export_different_projects(self, tmp_path):
        """Scenario 1: 3 threads export 3 different projects concurrently."""
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        # Set up 3 isolated projects with committed data
        project_infos: list[tuple[uuid.UUID, uuid.UUID, str]] = []
        with get_connection() as setup_conn:
            r = Repo(setup_conn)
            for i in range(3):
                pname = f"conc-export-{i}-{uuid.uuid4().hex[:6]}"
                pid = r.get_or_create_project(pname)
                aid = r.get_or_create_agent(pid, f"agent-{i}")
                nodes = _generate_nodes(pid, aid, 20)
                edges = _generate_edges(pid, aid, nodes, 10)
                _ingest_bulk(r, nodes, edges)
                project_infos.append((pid, aid, pname))
            setup_conn.commit()

        errors: list[str] = []
        results: list[int] = []

        def _export_worker(info: tuple[uuid.UUID, uuid.UUID, str]) -> int:
            pid, aid, pname = info
            try:
                with get_connection() as conn:
                    r = Repo(conn)
                    svc = ExportService(r)
                    work_dir = tmp_path / pname
                    work_dir.mkdir(parents=True, exist_ok=True)
                    result = svc.export_project(
                        project_name=pname,
                        project_id=pid,
                        target_dir=work_dir,
                    )
                    return result.exported
            except Exception as exc:
                errors.append(f"export {pname}: {exc}")
                return -1

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(_export_worker, info) for info in project_infos]
            for f in as_completed(futures):
                results.append(f.result())

        assert not errors, f"Export errors: {errors}"
        assert all(r >= 20 for r in results), f"Expected ≥20 each: {results}"
        assert len(results) == 3

    def test_concurrent_import_different_projects(self, tmp_path):
        """Scenario 2: 2 threads import DIFFERENT data into separate projects."""
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        # Create 2 separate exports with unique data
        export_infos: list[tuple[str, uuid.UUID, uuid.UUID]] = []  # (proj_name, pid, aid)
        with get_connection() as setup_conn:
            r = Repo(setup_conn)
            for i in range(2):
                pname = f"conc-src-{i}-{uuid.uuid4().hex[:6]}"
                pid = r.get_or_create_project(pname)
                aid = r.get_or_create_agent(pid, f"src-agent-{i}")
                nodes = _generate_nodes(pid, aid, 30, prefix=f"ConcSrc{i}")
                edges = _generate_edges(pid, aid, nodes, 15)
                _ingest_bulk(r, nodes, edges)

                svc = ExportService(r)
                svc.export_project(
                    project_name=pname,
                    project_id=pid,
                    target_dir=tmp_path,
                )
                export_infos.append((pname, pid, aid))
            setup_conn.commit()

        errors: list[str] = []
        import_counts: list[int] = []

        def _import_worker(idx: int) -> int:
            pname, src_pid, src_aid = export_infos[idx]
            try:
                with get_connection() as conn:
                    r = Repo(conn)
                    # Import into source project itself (idempotent)
                    svc = ImportService(r)
                    result = svc.import_project(
                        project_name=pname,
                        project_id=src_pid,
                        agent_id=src_aid,
                        source_dir=tmp_path,
                    )
                    conn.commit()
                    return result.skipped + result.imported
            except Exception as exc:
                errors.append(f"import-{idx}: {exc}")
                return -1

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(_import_worker, i) for i in range(2)]
            for f in as_completed(futures):
                import_counts.append(f.result())

        assert not errors, f"Import errors: {errors}"
        assert all(c >= 30 for c in import_counts), f"Expected ≥30 each: {import_counts}"

    def test_concurrent_export_and_import(self, tmp_path):
        """Scenario 3: Export + Import running simultaneously — no deadlock."""
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        # Set up project A (to export & also provide import files)
        with get_connection() as setup_conn:
            r = Repo(setup_conn)

            pname_a = f"conc-mixed-a-{uuid.uuid4().hex[:6]}"
            pid_a = r.get_or_create_project(pname_a)
            aid_a = r.get_or_create_agent(pid_a, "agent-a")
            nodes_a = _generate_nodes(pid_a, aid_a, 40, prefix="MixA")
            edges_a = _generate_edges(pid_a, aid_a, nodes_a, 20)
            _ingest_bulk(r, nodes_a, edges_a)

            # Export A's data to disk (for import later)
            ExportService(r).export_project(
                project_name=pname_a,
                project_id=pid_a,
                target_dir=tmp_path,
            )

            # Set up project B (to export concurrently)
            pname_b = f"conc-mixed-b-{uuid.uuid4().hex[:6]}"
            pid_b = r.get_or_create_project(pname_b)
            aid_b = r.get_or_create_agent(pid_b, "agent-b")
            nodes_b = _generate_nodes(pid_b, aid_b, 25, prefix="MixB")
            edges_b = _generate_edges(pid_b, aid_b, nodes_b, 12)
            _ingest_bulk(r, nodes_b, edges_b)

            setup_conn.commit()

        errors: list[str] = []
        results: dict[str, int] = {}

        def _export_b() -> int:
            try:
                with get_connection() as conn:
                    r = Repo(conn)
                    work_dir = tmp_path / "export-b"
                    work_dir.mkdir(parents=True, exist_ok=True)
                    result = ExportService(r).export_project(
                        project_name=pname_b,
                        project_id=pid_b,
                        target_dir=work_dir,
                    )
                    return result.exported
            except Exception as exc:
                errors.append(f"export_b: {exc}")
                return -1

        def _import_a() -> int:
            try:
                with get_connection() as conn:
                    r = Repo(conn)
                    # Re-import A into itself (idempotent)
                    result = ImportService(r).import_project(
                        project_name=pname_a,
                        project_id=pid_a,
                        agent_id=aid_a,
                        source_dir=tmp_path,
                    )
                    conn.commit()
                    return result.skipped + result.imported
            except Exception as exc:
                errors.append(f"import_a: {exc}")
                return -1

        with ThreadPoolExecutor(max_workers=2) as executor:
            f_export = executor.submit(_export_b)
            f_import = executor.submit(_import_a)
            results["export_b"] = f_export.result(timeout=30)
            results["import_a"] = f_import.result(timeout=30)

        assert not errors, f"Concurrent errors: {errors}"
        assert results["export_b"] >= 25, f"Export B: {results['export_b']}"
        assert results["import_a"] >= 40, f"Import A: {results['import_a']}"

    def test_no_data_loss_under_contention(self, tmp_path):
        """Verify no data loss when 4 threads each ingest 25 distinct nodes."""
        from kgn.db.connection import get_connection
        from kgn.db.repository import KgnRepository as Repo

        # Create a shared project (committed)
        with get_connection() as setup_conn:
            r = Repo(setup_conn)
            pname = f"conc-ingest-{uuid.uuid4().hex[:6]}"
            pid = r.get_or_create_project(pname)
            aid = r.get_or_create_agent(pid, "stress-agent")
            setup_conn.commit()

        # Pre-generate 4 batches of 25 nodes, each with unique UUIDs
        batches: list[list[NodeRecord]] = []
        all_node_ids: list[uuid.UUID] = []
        for i in range(4):
            batch = _generate_nodes(pid, aid, 25, prefix=f"Batch{i}")
            all_node_ids.extend(n.id for n in batch)
            batches.append(batch)

        errors: list[str] = []
        success_counts: list[int] = []

        def _ingest_batch(batch: list[NodeRecord]) -> int:
            try:
                with get_connection() as conn:
                    r = Repo(conn)
                    count = 0
                    for node in batch:
                        r.upsert_node(node)
                        count += 1
                    conn.commit()
                    return count
            except Exception as exc:
                errors.append(f"ingest: {exc}")
                return -1

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_ingest_batch, b) for b in batches]
            for f in as_completed(futures):
                success_counts.append(f.result())

        assert not errors, f"Ingest errors: {errors}"
        assert sum(success_counts) == 100

        # Verify all 100 node IDs exist
        with get_connection() as verify_conn:
            vr = Repo(verify_conn)
            found = vr.search_nodes(pid, exclude_archived=False)
            found_ids = {n.id for n in found}

        assert len(found_ids) >= 100, f"Expected 100 nodes, found {len(found_ids)}"
        for nid in all_node_ids:
            assert nid in found_ids, f"Node {nid} missing after concurrent ingest"
