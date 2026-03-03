"""SyncService — DB ↔ GitHub bidirectional synchronisation orchestration.

Coordinates ExportService, ImportService, GitService, and GitHubClient
to implement the full push/pull sync pipeline with conflict detection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import structlog

from kgn.errors import KgnError, KgnErrorCode
from kgn.git.service import GitService
from kgn.github.client import GitHubClient, GitHubConfig

log = structlog.get_logger("kgn.github.sync")


# ── Types ──────────────────────────────────────────────────────────────


class ConflictStrategy(StrEnum):
    """How to resolve DB ↔ file conflicts during pull."""

    DB_WINS = "db-wins"
    FILE_WINS = "file-wins"
    MANUAL = "manual"


@dataclass
class ConflictInfo:
    """A single conflict between local and remote."""

    file_path: str
    reason: str  # e.g. "merge_conflict", "both_modified"


@dataclass
class SyncResult:
    """Result of a push or pull operation."""

    success: bool
    action: str  # "push" | "pull"
    message: str = ""
    exported: int = 0
    imported: int = 0
    committed: bool = False
    pushed: bool = False
    pulled: bool = False
    conflicts: list[ConflictInfo] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return len(self.conflicts) > 0


# ── Conflict detector ─────────────────────────────────────────────────


class ConflictDetector:
    """Detect and optionally resolve conflicts during pull.

    Detection approaches:
    1. ``git pull`` merge conflict markers → parse conflicted filenames
    2. Both DB and file modified since ``last_sync`` → content_hash mismatch

    Resolution strategies:
    - ``db-wins``: re-export from DB, overwrite files, force-commit
    - ``file-wins``: import files into DB, overwriting DB state
    - ``manual``: report conflicts, do not auto-resolve
    """

    def __init__(self, strategy: ConflictStrategy = ConflictStrategy.DB_WINS) -> None:
        self._strategy = strategy

    @property
    def strategy(self) -> ConflictStrategy:
        return self._strategy

    def detect_merge_conflicts(self, pull_output: str) -> list[ConflictInfo]:
        """Parse ``git pull`` output for merge conflict markers."""
        conflicts: list[ConflictInfo] = []
        for line in pull_output.splitlines():
            if "CONFLICT" in line:
                # Extract filename from messages like:
                #   CONFLICT (content): Merge conflict in nodes/SPEC/foo.kgn
                parts = line.rsplit(" in ", maxsplit=1)
                filepath = parts[-1].strip() if len(parts) > 1 else line
                conflicts.append(ConflictInfo(file_path=filepath, reason="merge_conflict"))
        return conflicts

    def detect_sync_conflicts(
        self,
        sync_dir: Path,
        last_sync_hash: dict[str, str],
        current_files: dict[str, str],
    ) -> list[ConflictInfo]:
        """Detect files modified both in DB and on disk since last sync.

        Args:
            sync_dir: Sync root directory.
            last_sync_hash: ``{relative_path: content_hash}`` at last sync.
            current_files: ``{relative_path: content_hash}`` currently on disk.

        Returns:
            List of conflicts where both sides changed.
        """
        conflicts: list[ConflictInfo] = []
        for path, old_hash in last_sync_hash.items():
            new_hash = current_files.get(path)
            if new_hash and new_hash != old_hash:
                conflicts.append(ConflictInfo(file_path=path, reason="both_modified"))
        return conflicts

    def should_auto_resolve(self) -> bool:
        """Whether conflicts should be auto-resolved."""
        return self._strategy != ConflictStrategy.MANUAL


# ── Sync service ───────────────────────────────────────────────────────


class SyncService:
    """DB ↔ GitHub bidirectional sync orchestration.

    Push flow: DB → export → git commit → git push
    Pull flow: git pull → import → DB
    """

    def __init__(
        self,
        git_service: GitService,
        github_client: GitHubClient | None = None,
        conflict_strategy: ConflictStrategy = ConflictStrategy.DB_WINS,
    ) -> None:
        self._git = git_service
        self._github = github_client
        self._conflict = ConflictDetector(conflict_strategy)

    def push(
        self,
        project_name: str,
        project_id: uuid.UUID,
        sync_dir: Path,
        *,
        repo: object,  # KgnRepository — delayed import to avoid circular
        message: str | None = None,
        agent_id: str | None = None,
    ) -> SyncResult:
        """Push DB content to GitHub.

        Pipeline:
        1. ExportService.export_project() → write .kgn/.kge files
        2. GitService.commit() → stage + commit (skip if clean)
        3. GitService.push() → push to remote

        Args:
            project_name: Project name for export.
            project_id: UUID of the project.
            sync_dir: Root sync directory.
            repo: KgnRepository instance.
            message: Custom commit message (auto-generated if None).
            agent_id: Agent key for serialized files.

        Returns:
            SyncResult with details.
        """
        from kgn.sync.export_service import ExportService

        result = SyncResult(success=False, action="push")

        # 1. Export
        try:
            export_svc = ExportService(repo)  # type: ignore[arg-type]
            export_result = export_svc.export_project(
                project_name=project_name,
                project_id=project_id,
                target_dir=sync_dir,
                agent_id=agent_id,
            )
            result.exported = export_result.exported
            log.info(
                "sync.push.exported",
                exported=export_result.exported,
                skipped=export_result.skipped,
            )
        except Exception as exc:
            result.message = f"Export failed: {exc}"
            log.error("sync.push.export_failed", error=str(exc))
            return result

        # 2. Commit
        if message is None:
            message = (
                f"kgn: sync push {project_name} "
                f"({export_result.exported} exported, "
                f"{export_result.deleted} deleted)"
            )

        try:
            commit_result = self._git.commit(message)
            result.committed = "Nothing to commit" not in commit_result.message
            if not result.committed:
                result.success = True
                result.message = "Nothing to push — working tree clean"
                return result
        except KgnError:
            raise
        except Exception as exc:
            result.message = f"Commit failed: {exc}"
            return result

        # 3. Push
        try:
            self._git.push()
            result.pushed = True
            result.success = True
            result.message = "Push complete"
            log.info("sync.push.complete", project=project_name)
        except KgnError as exc:
            # Push might fail if no remote is configured
            if "No configured push destination" in str(exc) or "remote" in str(exc).lower():
                result.success = True
                result.pushed = False
                result.message = "Committed locally (no remote configured)"
            else:
                result.message = f"Push failed: {exc}"
                log.error("sync.push.push_failed", error=str(exc))

        return result

    def pull(
        self,
        project_name: str,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        sync_dir: Path,
        *,
        repo: object,  # KgnRepository
    ) -> SyncResult:
        """Pull GitHub content into DB.

        Pipeline:
        1. GitService.pull() → fetch + merge
        2. Detect conflicts → apply strategy
        3. ImportService.import_project() → files → DB

        Args:
            project_name: Project name for import.
            project_id: UUID of the project.
            agent_id: UUID of the agent.
            sync_dir: Root sync directory.
            repo: KgnRepository instance.

        Returns:
            SyncResult with details.
        """
        from kgn.sync.import_service import ImportService

        result = SyncResult(success=False, action="pull")

        # 1. Git pull
        try:
            pull_result = self._git.pull()
            result.pulled = True

            if not pull_result.success:
                # Check for merge conflicts
                conflicts = self._conflict.detect_merge_conflicts(pull_result.message)
                if conflicts:
                    result.conflicts = conflicts
                    if not self._conflict.should_auto_resolve():
                        result.message = (
                            f"Pull has {len(conflicts)} conflict(s). "
                            f"Strategy is 'manual' — resolve manually."
                        )
                        raise KgnError(
                            KgnErrorCode.SYNC_CONFLICT_UNRESOLVED,
                            result.message,
                        )
                    # Auto-resolve: for db-wins, we'll re-export after import
                    log.warning(
                        "sync.pull.conflicts_detected",
                        count=len(conflicts),
                        strategy=self._conflict.strategy.value,
                    )
                    # Abort the merge so we can work with a clean state
                    self._git._run("merge", "--abort", check=False)

                    # R-023: Ensure working tree is clean after abort
                    status_after = self._git._run(
                        "status",
                        "--porcelain",
                        check=False,
                    )
                    if status_after.message.strip():
                        # Residual changes detected — force-clean
                        self._git._run("checkout", ".", check=False)
                        self._git._run("clean", "-fd", check=False)
                        log.warning(
                            "sync.pull.force_cleaned_after_abort",
                            residual_files=status_after.message.strip(),
                        )

        except KgnError:
            raise
        except Exception as exc:
            # Pull might fail if no remote is configured
            if "No configured pull destination" in str(exc) or "remote" in str(exc).lower():
                result.pulled = False
                log.info("sync.pull.no_remote")
            else:
                result.message = f"Pull failed: {exc}"
                return result

        # 2. Import
        try:
            import_svc = ImportService(repo)  # type: ignore[arg-type]
            import_result = import_svc.import_project(
                project_name=project_name,
                project_id=project_id,
                agent_id=agent_id,
                source_dir=sync_dir,
            )
            result.imported = import_result.imported
            result.success = True
            result.message = (
                f"Pull complete — imported {import_result.imported}, "
                f"skipped {import_result.skipped}"
            )
            log.info(
                "sync.pull.complete",
                imported=import_result.imported,
                skipped=import_result.skipped,
            )
        except Exception as exc:
            result.message = f"Import failed: {exc}"
            log.error("sync.pull.import_failed", error=str(exc))

        # 3. Post-conflict resolution (R-024)
        if result.has_conflicts and result.success:
            strategy = self._conflict.strategy
            if strategy == ConflictStrategy.DB_WINS:
                # Re-export DB state to overwrite conflicted files
                try:
                    from kgn.sync.export_service import ExportService

                    export_svc = ExportService(repo)  # type: ignore[arg-type]
                    export_svc.export_project(
                        project_name=project_name,
                        project_id=project_id,
                        target_dir=sync_dir,
                    )
                    self._git.commit(f"kgn: auto-resolve conflicts (db-wins) for {project_name}")
                    log.info(
                        "sync.pull.conflict_resolved",
                        strategy="db-wins",
                        count=len(result.conflicts),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "sync.pull.conflict_resolve_failed",
                        strategy="db-wins",
                        error=str(exc),
                    )

        return result

    def ensure_remote(self, config: GitHubConfig | None = None) -> None:
        """Ensure the 'origin' remote is configured.

        If no remote exists, adds one using the GitHub config.
        """
        cfg = config or (self._github.config if self._github else None)
        if cfg is None:
            raise KgnError(
                KgnErrorCode.GITHUB_AUTH_FAILED,
                "GitHub configuration required to set up remote.",
            )

        remotes = self._git.remote_list()
        remote_names = {r[0] for r in remotes}

        if "origin" not in remote_names:
            self._git.remote_add("origin", cfg.repo_url)
            log.info("sync.remote_added", url=f"github.com/{cfg.owner}/{cfg.repo}")
