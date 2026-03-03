"""GitService — subprocess wrapper around the ``git`` CLI.

Provides init, add, commit, status, diff, and log operations for
managing a KGN sync directory as a local git repository.

Design decisions:
- No external git Python library — uses ``subprocess.run`` directly.
- Graceful error when ``git`` is not installed (KGN-500).
- All subprocess output is logged via structlog (R11).
- Error conditions return structured ``KgnError`` with 5xx codes (R13).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from kgn.errors import KgnError, KgnErrorCode

log = structlog.get_logger("kgn.git")

# ── Default .gitignore content ─────────────────────────────────────────

_DEFAULT_GITIGNORE = """\
# KGN Sync - auto-generated
.kgn-sync.json
__pycache__/
*.pyc
"""

# ── Result types ───────────────────────────────────────────────────────


@dataclass
class GitResult:
    """Generic result from a git command."""

    success: bool
    message: str
    returncode: int = 0


@dataclass
class GitStatusResult:
    """Parsed output of ``git status --porcelain``."""

    modified: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """True when there are no changes at all."""
        return not (self.modified or self.added or self.deleted or self.untracked)

    @property
    def total_changes(self) -> int:
        return len(self.modified) + len(self.added) + len(self.deleted) + len(self.untracked)


@dataclass
class GitLogEntry:
    """A single commit from ``git log``."""

    hash: str
    short_hash: str
    subject: str
    author: str
    date: str


# ── Service ────────────────────────────────────────────────────────────


_DEFAULT_GIT_TIMEOUT = 30


class GitService:
    """Git CLI wrapper for KGN sync directories.

    All git commands run in the directory specified by *repo_dir*.
    The constructor verifies that ``git`` is available on PATH;
    raises ``KgnError(KGN-500)`` if not.

    Args:
        repo_dir: Working directory for git commands.
        timeout: Subprocess timeout in seconds.  Defaults to
            ``KGN_GIT_TIMEOUT`` env-var or 30 if unset (R-019).
    """

    def __init__(
        self,
        repo_dir: Path,
        *,
        timeout: int | None = None,
    ) -> None:
        self._repo_dir = repo_dir.resolve()
        self._timeout = timeout or int(os.environ.get("KGN_GIT_TIMEOUT", str(_DEFAULT_GIT_TIMEOUT)))
        self._verify_git_installed()

    # ── Public API ─────────────────────────────────────────────────

    def init(self) -> GitResult:
        """Initialize a git repository and create ``.gitignore``.

        Idempotent — re-running on an existing repo is safe.
        """
        self._repo_dir.mkdir(parents=True, exist_ok=True)

        result = self._run("init")

        # Write .gitignore (overwrite if already exists)
        gitignore_path = self._repo_dir / ".gitignore"
        gitignore_path.write_text(_DEFAULT_GITIGNORE, encoding="utf-8")

        log.info("git.init", repo_dir=str(self._repo_dir))
        return result

    def add_all(self) -> GitResult:
        """Stage all changes (``git add -A``)."""
        self._ensure_initialized()
        return self._run("add", "-A")

    def commit(self, message: str) -> GitResult:
        """Create a commit with the given message.

        Returns a result with ``success=True`` even when there is
        nothing to commit (the message will indicate this).
        """
        self._ensure_initialized()

        # Check if there's anything to commit
        status = self.status()
        if status.is_clean:
            log.info("git.commit.nothing_to_commit")
            return GitResult(
                success=True,
                message="Nothing to commit, working tree clean",
                returncode=0,
            )

        # Stage everything first
        self.add_all()

        result = self._run("commit", "-m", message)
        log.info("git.commit", message=message, success=result.success)
        return result

    def status(self) -> GitStatusResult:
        """Parse ``git status --porcelain`` into structured categories."""
        self._ensure_initialized()

        result = self._run("status", "--porcelain")
        return self._parse_status(result.message)

    def diff(self, *, cached: bool = False) -> str:
        """Return the output of ``git diff``.

        Args:
            cached: If True, show staged changes (``--cached``).
        """
        self._ensure_initialized()

        args = ["diff"]
        if cached:
            args.append("--cached")
        result = self._run(*args)
        return result.message

    def log(self, n: int = 10) -> list[GitLogEntry]:
        """Return the last *n* commits.

        Uses a custom ``--format`` for reliable parsing.
        """
        self._ensure_initialized()

        # Check if there are any commits
        result = self._run(
            "log",
            f"-{n}",
            "--format=%H%n%h%n%s%n%an%n%ai",
            check=False,
        )

        if result.returncode != 0:
            # No commits yet or other issue
            return []

        return self._parse_log(result.message)

    def is_initialized(self) -> bool:
        """Check if the repo_dir is a git repository."""
        git_dir = self._repo_dir / ".git"
        return git_dir.is_dir()

    def push(self, remote: str = "origin", branch: str | None = None) -> GitResult:
        """Push committed changes to remote.

        Args:
            remote: Remote name (default ``origin``).
            branch: Branch to push. If None, pushes current branch.
        """
        self._ensure_initialized()
        args = ["push"]
        if branch:
            args.extend([remote, branch])
        else:
            args.extend(["-u", remote, self.current_branch()])
        return self._run(*args)

    def pull(self, remote: str = "origin", branch: str | None = None) -> GitResult:
        """Pull changes from remote.

        Args:
            remote: Remote name (default ``origin``).
            branch: Branch to pull. If None, pulls current branch.

        Returns:
            GitResult; on merge-conflict ``success`` is False and
            ``message`` contains filenames with ``CONFLICT``.
        """
        self._ensure_initialized()
        args = ["pull", remote]
        if branch:
            args.append(branch)
        result = self._run(*args, check=False)
        return result

    def remote_add(self, name: str, url: str) -> GitResult:
        """Add a git remote."""
        self._ensure_initialized()
        return self._run("remote", "add", name, url)

    def remote_list(self) -> list[tuple[str, str]]:
        """List configured remotes as ``[(name, url), ...]``."""
        self._ensure_initialized()
        result = self._run("remote", "-v", check=False)
        remotes: list[tuple[str, str]] = []
        seen: set[str] = set()
        for line in result.message.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] not in seen:
                remotes.append((parts[0], parts[1]))
                seen.add(parts[0])
        return remotes

    def current_branch(self) -> str:
        """Return the name of the current branch."""
        self._ensure_initialized()
        result = self._run("branch", "--show-current")
        branch = result.message.strip()
        if not branch:
            # Fallback for detached HEAD or initial commit
            return "main"
        return branch

    def detect_default_branch(self) -> str:
        """Detect the remote default branch (R-022).

        Resolution order:
        1. ``git symbolic-ref refs/remotes/origin/HEAD`` → parse branch
        2. ``git remote show origin | HEAD branch`` fallback
        3. Local branch list: prefer ``main``, then ``master``
        4. Final fallback: ``"main"``

        Returns:
            Branch name (e.g. ``"main"`` or ``"master"``).
        """
        # 1. Try symbolic-ref (fast, offline)
        result = self._run(
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            check=False,
        )
        if result.returncode == 0 and result.message.strip():
            ref = result.message.strip()  # e.g. refs/remotes/origin/main
            parts = ref.rsplit("/", maxsplit=1)
            if len(parts) == 2 and parts[1]:
                return parts[1]

        # 2. Try 'git remote show origin' (requires network)
        result = self._run("remote", "show", "origin", check=False)
        if result.returncode == 0:
            for line in result.message.splitlines():
                stripped = line.strip()
                if stripped.startswith("HEAD branch:"):
                    branch = stripped.split(":", maxsplit=1)[1].strip()
                    if branch and branch != "(unknown)":
                        return branch

        # 3. Local branch list detection
        list_result = self._run("branch", check=False)
        local_branches = [
            ln.strip().lstrip("* ") for ln in list_result.message.splitlines() if ln.strip()
        ]
        if "main" in local_branches:
            return "main"
        if "master" in local_branches:
            return "master"

        # 4. Final fallback
        return "main"

    # ── Private helpers ────────────────────────────────────────────

    def _verify_git_installed(self) -> None:
        """Verify that ``git`` is available on PATH."""
        try:
            subprocess.run(
                ["git", "--version"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        except FileNotFoundError:
            raise KgnError(
                KgnErrorCode.GIT_NOT_INSTALLED,
                "git is not installed or not found on PATH",
            ) from None
        except subprocess.SubprocessError as exc:
            raise KgnError(
                KgnErrorCode.GIT_NOT_INSTALLED,
                f"git verification failed: {exc}",
            ) from exc

    def _ensure_initialized(self) -> None:
        """Raise if the repo directory is not a git repository."""
        if not self.is_initialized():
            raise KgnError(
                KgnErrorCode.GIT_NOT_INITIALIZED,
                f"Not a git repository: {self._repo_dir}. Run 'kgn git init' first.",
            )

    def _run(self, *args: str, check: bool = True) -> GitResult:
        """Execute a git command and return the result.

        Args:
            *args: Git subcommand and its arguments.
            check: If True (default), raise ``KgnError`` on non-zero exit.
        """
        cmd = ["git", *args]
        log.debug("git.run", cmd=" ".join(cmd), cwd=str(self._repo_dir))

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self._repo_dir),
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            raise KgnError(
                KgnErrorCode.GIT_COMMAND_FAILED,
                f"git command timed out: {' '.join(cmd)}",
            ) from None
        except subprocess.SubprocessError as exc:
            raise KgnError(
                KgnErrorCode.GIT_COMMAND_FAILED,
                f"git command failed: {exc}",
            ) from exc

        output = proc.stdout.rstrip()
        if proc.returncode != 0 and check:
            stderr = proc.stderr.strip()
            msg = stderr or output or f"git {args[0]} failed with code {proc.returncode}"
            log.warning("git.error", cmd=" ".join(cmd), stderr=stderr, code=proc.returncode)
            raise KgnError(
                KgnErrorCode.GIT_COMMAND_FAILED,
                msg,
            )

        return GitResult(
            success=proc.returncode == 0,
            message=output,
            returncode=proc.returncode,
        )

    @staticmethod
    def _parse_status(porcelain_output: str) -> GitStatusResult:
        """Parse ``git status --porcelain`` output."""
        result = GitStatusResult()

        if not porcelain_output.strip():
            return result

        for line in porcelain_output.splitlines():
            if len(line) < 4:
                continue

            code = line[:2]
            filepath = line[3:].strip()

            if code == "??":
                result.untracked.append(filepath)
            elif code.startswith("A") or code.endswith("A"):
                result.added.append(filepath)
            elif code.startswith("D") or code.endswith("D"):
                result.deleted.append(filepath)
            elif code.startswith("M") or code.endswith("M") or code.startswith("R"):
                result.modified.append(filepath)
            else:
                # Other statuses (copied, etc.) — treat as modified
                result.modified.append(filepath)

        return result

    @staticmethod
    def _parse_log(log_output: str) -> list[GitLogEntry]:
        """Parse custom ``git log --format=%H%n%h%n%s%n%an%n%ai`` output."""
        entries: list[GitLogEntry] = []

        if not log_output.strip():
            return entries

        lines = log_output.strip().splitlines()

        # Each entry is 5 lines: hash, short_hash, subject, author, date
        for i in range(0, len(lines), 5):
            if i + 4 >= len(lines):
                break
            entries.append(
                GitLogEntry(
                    hash=lines[i],
                    short_hash=lines[i + 1],
                    subject=lines[i + 2],
                    author=lines[i + 3],
                    date=lines[i + 4],
                )
            )

        return entries
