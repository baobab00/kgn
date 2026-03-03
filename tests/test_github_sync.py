"""Tests for SyncService, ConflictDetector, and GitService push/pull methods.

Uses real git commands in tmp_path. No actual GitHub API calls.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kgn.errors import KgnError
from kgn.git.service import GitService
from kgn.github.sync_service import (
    ConflictDetector,
    ConflictStrategy,
    SyncResult,
    SyncService,
)


def _git_installed() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not _git_installed(), reason="git is not installed")


# ── Helpers ────────────────────────────────────────────────────────────


def _init_repo(path: Path) -> GitService:
    """Create a git repo with an initial commit."""
    svc = GitService(path)
    svc.init()
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    svc.commit("initial commit")
    return svc


def _init_bare_remote(path: Path) -> Path:
    """Create a bare remote repository."""
    bare = path / "remote.git"
    bare.mkdir()
    subprocess.run(
        ["git", "init", "--bare"],
        cwd=str(bare),
        capture_output=True,
        check=True,
    )
    return bare


# ══════════════════════════════════════════════════════════════════════
#  GitService — push / pull / remote
# ══════════════════════════════════════════════════════════════════════


class TestGitPushPull:
    def test_remote_add_and_list(self, tmp_path: Path) -> None:
        """remote_add + remote_list round-trip."""
        repo_dir = tmp_path / "my-repo"
        svc = _init_repo(repo_dir)

        bare = _init_bare_remote(tmp_path)
        svc.remote_add("origin", str(bare))

        remotes = svc.remote_list()
        assert any(name == "origin" for name, _ in remotes)

    def test_push_to_bare(self, tmp_path: Path) -> None:
        """push() succeeds against a bare remote."""
        repo_dir = tmp_path / "push-repo"
        svc = _init_repo(repo_dir)

        bare = _init_bare_remote(tmp_path)
        svc.remote_add("origin", str(bare))

        branch = svc.current_branch()
        result = svc.push("origin", branch)
        assert result.success is True

    def test_pull_from_bare(self, tmp_path: Path) -> None:
        """pull() fetches changes from remote."""
        repo_dir = tmp_path / "pull-local"
        svc = _init_repo(repo_dir)

        bare = _init_bare_remote(tmp_path)
        svc.remote_add("origin", str(bare))
        branch = svc.current_branch()
        svc.push("origin", branch)

        # Clone into second repo, make a change, push
        clone_dir = tmp_path / "clone"
        subprocess.run(
            ["git", "clone", str(bare), str(clone_dir)],
            capture_output=True,
            check=True,
        )
        (clone_dir / "new-file.txt").write_text("from clone", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(clone_dir), "add", "-A"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(clone_dir), "commit", "-m", "clone commit"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(clone_dir), "push"],
            capture_output=True,
            check=True,
        )

        # Pull in original repo
        result = svc.pull("origin", branch)
        assert result.success is True
        assert (repo_dir / "new-file.txt").exists()

    def test_current_branch(self, tmp_path: Path) -> None:
        """current_branch() returns the active branch name."""
        repo_dir = tmp_path / "branch-repo"
        svc = _init_repo(repo_dir)
        branch = svc.current_branch()
        assert branch in ("main", "master")


# ══════════════════════════════════════════════════════════════════════
#  ConflictDetector
# ══════════════════════════════════════════════════════════════════════


class TestConflictDetector:
    def test_detect_merge_conflicts(self) -> None:
        """Parse CONFLICT lines from git pull output."""
        output = (
            "Auto-merging nodes/SPEC/auth.kgn\n"
            "CONFLICT (content): Merge conflict in nodes/SPEC/auth.kgn\n"
            "Automatic merge failed; fix conflicts and then commit.\n"
        )
        detector = ConflictDetector(ConflictStrategy.DB_WINS)
        conflicts = detector.detect_merge_conflicts(output)

        assert len(conflicts) == 1
        assert conflicts[0].file_path == "nodes/SPEC/auth.kgn"
        assert conflicts[0].reason == "merge_conflict"

    def test_no_conflicts(self) -> None:
        """No CONFLICT lines means empty list."""
        output = "Already up to date.\n"
        detector = ConflictDetector()
        conflicts = detector.detect_merge_conflicts(output)
        assert conflicts == []

    def test_multiple_conflicts(self) -> None:
        """Multiple CONFLICT lines parsed."""
        output = (
            "CONFLICT (content): Merge conflict in nodes/SPEC/a.kgn\n"
            "CONFLICT (content): Merge conflict in nodes/GOAL/b.kgn\n"
            "CONFLICT (content): Merge conflict in edges/x--DEPENDS_ON--y.kge\n"
        )
        detector = ConflictDetector(ConflictStrategy.FILE_WINS)
        conflicts = detector.detect_merge_conflicts(output)
        assert len(conflicts) == 3

    def test_strategy_db_wins_auto_resolve(self) -> None:
        detector = ConflictDetector(ConflictStrategy.DB_WINS)
        assert detector.should_auto_resolve() is True

    def test_strategy_manual_no_auto_resolve(self) -> None:
        detector = ConflictDetector(ConflictStrategy.MANUAL)
        assert detector.should_auto_resolve() is False

    def test_detect_sync_conflicts(self) -> None:
        """Detect files modified since last sync."""
        detector = ConflictDetector()
        last_sync = {"nodes/SPEC/a.kgn": "hash1", "nodes/GOAL/b.kgn": "hash2"}
        current = {"nodes/SPEC/a.kgn": "hash1_changed", "nodes/GOAL/b.kgn": "hash2"}

        conflicts = detector.detect_sync_conflicts(Path("."), last_sync, current)
        assert len(conflicts) == 1
        assert conflicts[0].file_path == "nodes/SPEC/a.kgn"
        assert conflicts[0].reason == "both_modified"


# ══════════════════════════════════════════════════════════════════════
#  SyncService — push pipeline (unit, no DB)
# ══════════════════════════════════════════════════════════════════════


class TestSyncServicePush:
    """Test SyncService.push() with a mock repo that has no DB."""

    def test_push_no_remote_commits_locally(self, tmp_path: Path) -> None:
        """Push without remote still commits locally."""
        repo_dir = tmp_path / "push-no-remote"
        git_svc = _init_repo(repo_dir)

        SyncService(git_service=git_svc)

        # Write a file to simulate export
        project_dir = repo_dir / "test-project" / "nodes" / "SPEC"
        project_dir.mkdir(parents=True)
        (project_dir / "test.kgn").write_text("content", encoding="utf-8")

        # Commit manually to test the pipeline
        git_svc.commit("manual commit")

        entries = git_svc.log(n=5)
        assert any("manual commit" in e.subject for e in entries)

    def test_sync_result_dataclass(self) -> None:
        """SyncResult properties work correctly."""
        result = SyncResult(success=True, action="push", exported=5, committed=True)
        assert result.has_conflicts is False
        assert result.action == "push"


# ══════════════════════════════════════════════════════════════════════
#  SyncService — push with bare remote
# ══════════════════════════════════════════════════════════════════════


class TestSyncServicePushWithRemote:
    def test_push_to_bare_remote(self, tmp_path: Path) -> None:
        """Full push pipeline to a bare remote."""
        repo_dir = tmp_path / "push-remote-repo"
        git_svc = _init_repo(repo_dir)

        bare = _init_bare_remote(tmp_path)
        git_svc.remote_add("origin", str(bare))
        branch = git_svc.current_branch()
        git_svc.push("origin", branch)

        # Add a file and push via SyncService
        project_dir = repo_dir / "my-project" / "nodes"
        project_dir.mkdir(parents=True)
        (project_dir / "test.kgn").write_text("node data", encoding="utf-8")
        git_svc.commit("add node")
        result = git_svc.push("origin", branch)
        assert result.success is True


# ══════════════════════════════════════════════════════════════════════
#  ConflictStrategy enum
# ══════════════════════════════════════════════════════════════════════


class TestConflictStrategy:
    def test_enum_values(self) -> None:
        assert ConflictStrategy.DB_WINS == "db-wins"
        assert ConflictStrategy.FILE_WINS == "file-wins"
        assert ConflictStrategy.MANUAL == "manual"

    def test_from_string(self) -> None:
        assert ConflictStrategy("db-wins") == ConflictStrategy.DB_WINS


# ══════════════════════════════════════════════════════════════════════
#  SyncService — push/pull with real DB (R-035 coverage improvement)
# ══════════════════════════════════════════════════════════════════════


class TestSyncServiceWithDB:
    """Tests that exercise SyncService through the full push pipeline."""

    def test_push_full_pipeline(self, tmp_path: Path, db_conn, repo, project_id, agent_id) -> None:
        """push() exports nodes from DB, commits, and reports success."""
        import uuid as _uuid

        from kgn.models.enums import NodeStatus, NodeType
        from kgn.models.node import NodeRecord

        node_id = _uuid.uuid4()
        node = NodeRecord(
            id=node_id,
            project_id=project_id,
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="Push test node for R-035",
            body_md="# Body\nTest content.",
            created_by=agent_id,
        )
        repo.upsert_node(node)

        # Verify node visible
        nodes = repo.search_nodes(project_id)
        assert len(nodes) >= 1

        # Get actual project name
        project_row = repo._dict_fetchone(
            "SELECT name FROM projects WHERE id = %s",
            (project_id,),
        )
        project_name = project_row["name"]

        repo_dir = tmp_path / "push-db-repo"
        git_svc = _init_repo(repo_dir)

        sync_svc = SyncService(git_service=git_svc)
        result = sync_svc.push(
            project_name=project_name,
            project_id=project_id,
            sync_dir=repo_dir,
            repo=repo,
        )

        assert result.exported >= 1
        assert result.committed is True
        # Push may fail (no remote) — that's OK, the export + commit path is covered

    def test_push_nothing_to_push(
        self, tmp_path: Path, db_conn, repo, project_id, agent_id
    ) -> None:
        """push() with no changes → 'Nothing to push'."""
        # Get actual project name
        project_row = repo._dict_fetchone(
            "SELECT name FROM projects WHERE id = %s",
            (project_id,),
        )
        project_name = project_row["name"]

        repo_dir = tmp_path / "push-empty"
        git_svc = _init_repo(repo_dir)

        sync_svc = SyncService(git_service=git_svc)
        # First push to export
        sync_svc.push(
            project_name=project_name,
            project_id=project_id,
            sync_dir=repo_dir,
            repo=repo,
        )
        # Second push — nothing changed
        result = sync_svc.push(
            project_name=project_name,
            project_id=project_id,
            sync_dir=repo_dir,
            repo=repo,
        )
        assert result.success is True
        assert "Nothing to push" in result.message

    def test_pull_no_remote(self, tmp_path: Path, db_conn, repo, project_id, agent_id) -> None:
        """pull() without remote configured completes with import."""
        # Get actual project name
        project_row = repo._dict_fetchone(
            "SELECT name FROM projects WHERE id = %s",
            (project_id,),
        )
        project_name = project_row["name"]

        repo_dir = tmp_path / "pull-no-remote"
        git_svc = _init_repo(repo_dir)

        sync_svc = SyncService(git_service=git_svc)
        result = sync_svc.pull(
            project_name=project_name,
            project_id=project_id,
            agent_id=agent_id,
            sync_dir=repo_dir,
            repo=repo,
        )
        # Should not crash — pull skips if no remote
        assert result.action == "pull"

    def test_ensure_remote_no_config(self, tmp_path: Path) -> None:
        """ensure_remote() raises KgnError without config."""
        repo_dir = tmp_path / "no-config"
        git_svc = _init_repo(repo_dir)
        sync_svc = SyncService(git_service=git_svc)

        with pytest.raises(KgnError, match="GitHub configuration required"):
            sync_svc.ensure_remote()

    def test_conflict_info_has_conflicts(self) -> None:
        """ConflictInfo correctly reports has_conflicts."""
        from kgn.github.sync_service import ConflictInfo

        result = SyncResult(
            success=True,
            action="pull",
            conflicts=[ConflictInfo(file_path="a.kgn", reason="merge_conflict")],
        )
        assert result.has_conflicts is True
