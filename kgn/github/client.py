"""GitHub REST API v3 client — httpx-based, no extra dependencies.

Uses httpx (already available as an ``mcp`` transitive dependency)
to communicate with the GitHub API.

Environment variables:
    KGN_GITHUB_TOKEN  — Personal Access Token (``repo`` scope)
    KGN_GITHUB_OWNER  — Repository owner (user or org)
    KGN_GITHUB_REPO   — Repository name
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
import structlog

from kgn.errors import KgnError, KgnErrorCode

log = structlog.get_logger("kgn.github")

_API_BASE = "https://api.github.com"


@dataclass(frozen=True)
class GitHubConfig:
    """Validated GitHub configuration."""

    token: str
    owner: str
    repo: str

    @classmethod
    def from_env(cls) -> GitHubConfig:
        """Load from environment variables.

        Raises ``KgnError(KGN-510)`` if ``KGN_GITHUB_TOKEN`` is missing.
        """
        token = os.environ.get("KGN_GITHUB_TOKEN", "")
        owner = os.environ.get("KGN_GITHUB_OWNER", "")
        repo = os.environ.get("KGN_GITHUB_REPO", "")

        if not token:
            raise KgnError(
                KgnErrorCode.GITHUB_AUTH_FAILED,
                "KGN_GITHUB_TOKEN environment variable is not set. "
                "Create a Personal Access Token with 'repo' scope.",
            )
        if not owner or not repo:
            raise KgnError(
                KgnErrorCode.GITHUB_REPO_NOT_FOUND,
                "KGN_GITHUB_OWNER and KGN_GITHUB_REPO must be set.",
            )
        return cls(token=token, owner=owner, repo=repo)

    @property
    def repo_url(self) -> str:
        """HTTPS clone URL with embedded token for push."""
        return f"https://{self.token}@github.com/{self.owner}/{self.repo}.git"


class GitHubClient:
    """Thin wrapper around GitHub REST API v3.

    Thread-unsafe — create one per request / CLI invocation.
    """

    def __init__(self, config: GitHubConfig | None = None) -> None:
        self._config = config or GitHubConfig.from_env()
        self._client = httpx.Client(
            base_url=_API_BASE,
            headers={
                "Authorization": f"Bearer {self._config.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── Repository methods ─────────────────────────────────────────

    def repo_exists(self) -> bool:
        """Check if the configured repository exists."""
        resp = self._request("GET", self._repo_path())
        return resp.status_code == 200

    def create_repo(
        self,
        *,
        private: bool = True,
        description: str = "",
    ) -> dict:
        """Create a new repository under the configured owner.

        Returns the GitHub API response dict.
        """
        payload: dict = {
            "name": self._config.repo,
            "private": private,
            "auto_init": False,
        }
        if description:
            payload["description"] = description

        resp = self._request("POST", "/user/repos", json=payload)
        self._check_response(resp, 201)
        return resp.json()

    def get_repo(self) -> dict:
        """Get repository metadata."""
        resp = self._request("GET", self._repo_path())
        self._check_response(resp, 200)
        return resp.json()

    # ── Branch methods ─────────────────────────────────────────────

    def list_branches(self, per_page: int = 100) -> list[dict]:
        """List repository branches.

        Returns:
            List of branch dicts (each has ``name`` and ``commit.sha``).
        """
        resp = self._request(
            "GET",
            f"{self._repo_path()}/branches",
            params={"per_page": per_page},
        )
        self._check_response(resp, 200)
        return resp.json()

    def create_branch(self, branch_name: str, sha: str) -> dict:
        """Create a new branch pointing at *sha*.

        Uses the Git refs API: ``POST /repos/{owner}/{repo}/git/refs``.

        Returns:
            GitHub ref response dict.
        """
        payload = {
            "ref": f"refs/heads/{branch_name}",
            "sha": sha,
        }
        resp = self._request(
            "POST",
            f"{self._repo_path()}/git/refs",
            json=payload,
        )
        self._check_response(resp, 201)
        return resp.json()

    def delete_branch(self, branch_name: str) -> None:
        """Delete a branch by name.

        Uses the Git refs API: ``DELETE /repos/{owner}/{repo}/git/refs/heads/{branch}``.
        """
        resp = self._request(
            "DELETE",
            f"{self._repo_path()}/git/refs/heads/{branch_name}",
        )
        self._check_response(resp, 204)

    # ── Pull Request methods ───────────────────────────────────────

    def close_pr(self, pr_number: int) -> dict:
        """Close a Pull Request by number.

        Returns:
            Updated PR response dict.
        """
        resp = self._request(
            "PATCH",
            f"{self._repo_path()}/pulls/{pr_number}",
            json={"state": "closed"},
        )
        self._check_response(resp, 200)
        return resp.json()

    def create_pull_request(
        self,
        title: str,
        head: str,
        base: str = "main",
        *,
        body: str = "",
    ) -> dict:
        """Create a Pull Request.

        Args:
            title: PR title.
            head: Source branch.
            base: Target branch (default ``main``).
            body: PR description (Markdown).

        Returns:
            GitHub PR response dict (includes ``number``, ``html_url``).
        """
        payload = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
        }
        resp = self._request("POST", f"{self._repo_path()}/pulls", json=payload)
        self._check_response(resp, 201)
        return resp.json()

    def list_pull_requests(
        self,
        state: str = "open",
    ) -> list[dict]:
        """List Pull Requests.

        Args:
            state: Filter by state (``open``, ``closed``, ``all``).
        """
        resp = self._request(
            "GET",
            f"{self._repo_path()}/pulls",
            params={"state": state},
        )
        self._check_response(resp, 200)
        return resp.json()

    # ── Internals ──────────────────────────────────────────────────

    @property
    def config(self) -> GitHubConfig:
        return self._config

    def _repo_path(self) -> str:
        return f"/repos/{self._config.owner}/{self._config.repo}"

    def _request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> httpx.Response:
        """Make an HTTP request with error wrapping."""
        try:
            log.debug("github.request", method=method, url=url)
            return self._client.request(method, url, **kwargs)  # type: ignore[arg-type]
        except httpx.TimeoutException:
            raise KgnError(
                KgnErrorCode.GITHUB_API_ERROR,
                f"GitHub API timeout: {method} {url}",
            ) from None
        except httpx.HTTPError as exc:
            raise KgnError(
                KgnErrorCode.GITHUB_API_ERROR,
                f"GitHub API error: {exc}",
            ) from exc

    @staticmethod
    def _check_response(resp: httpx.Response, expected: int) -> None:
        """Raise ``KgnError`` if status code doesn't match."""
        if resp.status_code == expected:
            return

        if resp.status_code == 401:
            raise KgnError(
                KgnErrorCode.GITHUB_AUTH_FAILED,
                "GitHub authentication failed. Check KGN_GITHUB_TOKEN.",
            )
        if resp.status_code == 404:
            raise KgnError(
                KgnErrorCode.GITHUB_REPO_NOT_FOUND,
                f"GitHub resource not found: {resp.url}",
            )

        # Generic error
        try:
            body = resp.json()
            message = body.get("message", resp.text)
        except Exception:
            message = resp.text

        raise KgnError(
            KgnErrorCode.GITHUB_API_ERROR,
            f"GitHub API returned {resp.status_code}: {message}",
        )
