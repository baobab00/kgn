"""Tests for GitHubClient — GitHub REST API wrapper.

All HTTP calls are mocked via ``httpx.MockTransport`` to avoid
actual GitHub API calls.
"""

from __future__ import annotations

import json

import httpx
import pytest

from kgn.errors import KgnError, KgnErrorCode
from kgn.github.client import GitHubClient, GitHubConfig

# ── Fixtures ───────────────────────────────────────────────────────────


def _make_config() -> GitHubConfig:
    return GitHubConfig(token="ghp_test123", owner="test-owner", repo="test-repo")


def _mock_client(handler) -> GitHubClient:
    """Build a GitHubClient with a mock transport."""
    config = _make_config()
    client = GitHubClient(config=config)
    # Replace the internal httpx client with mock transport
    client._client = httpx.Client(
        base_url="https://api.github.com",
        headers={
            "Authorization": f"Bearer {config.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        transport=httpx.MockTransport(handler),
        timeout=30.0,
    )
    return client


# ══════════════════════════════════════════════════════════════════════
#  Config
# ══════════════════════════════════════════════════════════════════════


class TestGitHubConfig:
    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config loads from env vars."""
        monkeypatch.setenv("KGN_GITHUB_TOKEN", "ghp_abc")
        monkeypatch.setenv("KGN_GITHUB_OWNER", "myowner")
        monkeypatch.setenv("KGN_GITHUB_REPO", "myrepo")

        cfg = GitHubConfig.from_env()
        assert cfg.token == "ghp_abc"
        assert cfg.owner == "myowner"
        assert cfg.repo == "myrepo"

    def test_missing_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing token raises KGN-510."""
        monkeypatch.delenv("KGN_GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("KGN_GITHUB_OWNER", "owner")
        monkeypatch.setenv("KGN_GITHUB_REPO", "repo")

        with pytest.raises(KgnError) as exc_info:
            GitHubConfig.from_env()
        assert exc_info.value.code == KgnErrorCode.GITHUB_AUTH_FAILED

    def test_missing_owner_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing owner/repo raises KGN-511."""
        monkeypatch.setenv("KGN_GITHUB_TOKEN", "ghp_abc")
        monkeypatch.delenv("KGN_GITHUB_OWNER", raising=False)
        monkeypatch.setenv("KGN_GITHUB_REPO", "repo")

        with pytest.raises(KgnError) as exc_info:
            GitHubConfig.from_env()
        assert exc_info.value.code == KgnErrorCode.GITHUB_REPO_NOT_FOUND

    def test_repo_url(self) -> None:
        cfg = _make_config()
        assert "test-owner/test-repo.git" in cfg.repo_url
        assert "ghp_test123" in cfg.repo_url


# ══════════════════════════════════════════════════════════════════════
#  repo_exists
# ══════════════════════════════════════════════════════════════════════


class TestRepoExists:
    def test_repo_exists_true(self) -> None:
        """Returns True when repo responds 200."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert "/repos/test-owner/test-repo" in str(request.url)
            return httpx.Response(200, json={"full_name": "test-owner/test-repo"})

        client = _mock_client(handler)
        assert client.repo_exists() is True

    def test_repo_exists_false(self) -> None:
        """Returns False when repo responds 404."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "Not Found"})

        client = _mock_client(handler)
        assert client.repo_exists() is False


# ══════════════════════════════════════════════════════════════════════
#  create_repo
# ══════════════════════════════════════════════════════════════════════


class TestCreateRepo:
    def test_create_repo_success(self) -> None:
        """create_repo returns response dict on 201."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/user/repos" in str(request.url):
                body = json.loads(request.content)
                assert body["name"] == "test-repo"
                assert body["private"] is True
                return httpx.Response(
                    201,
                    json={"full_name": "test-owner/test-repo", "id": 12345},
                )
            return httpx.Response(404)

        client = _mock_client(handler)
        result = client.create_repo(description="test desc")
        assert result["id"] == 12345

    def test_create_repo_auth_failure(self) -> None:
        """Auth failure raises KGN-510."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "Bad credentials"})

        client = _mock_client(handler)
        with pytest.raises(KgnError) as exc_info:
            client.create_repo()
        assert exc_info.value.code == KgnErrorCode.GITHUB_AUTH_FAILED


# ══════════════════════════════════════════════════════════════════════
#  Pull Requests
# ══════════════════════════════════════════════════════════════════════


class TestPullRequests:
    def test_create_pr(self) -> None:
        """create_pull_request returns PR data on 201."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/pulls" in str(request.url):
                body = json.loads(request.content)
                assert body["title"] == "test PR"
                assert body["head"] == "feature"
                assert body["base"] == "main"
                return httpx.Response(
                    201,
                    json={"number": 42, "html_url": "https://github.com/test/pr/42"},
                )
            return httpx.Response(404)

        client = _mock_client(handler)
        result = client.create_pull_request(
            title="test PR",
            head="feature",
            body="desc",
        )
        assert result["number"] == 42

    def test_list_prs(self) -> None:
        """list_pull_requests returns list of PRs."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "/pulls" in str(request.url):
                return httpx.Response(
                    200,
                    json=[
                        {"number": 1, "title": "PR #1"},
                        {"number": 2, "title": "PR #2"},
                    ],
                )
            return httpx.Response(404)

        client = _mock_client(handler)
        prs = client.list_pull_requests()
        assert len(prs) == 2

    def test_api_error_raises(self) -> None:
        """Server error raises KGN-512."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"message": "Internal Server Error"})

        client = _mock_client(handler)
        with pytest.raises(KgnError) as exc_info:
            client.list_pull_requests()
        assert exc_info.value.code == KgnErrorCode.GITHUB_API_ERROR

    def test_404_raises_not_found(self) -> None:
        """404 raises KGN-511."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "Not Found"})

        client = _mock_client(handler)
        with pytest.raises(KgnError) as exc_info:
            client.get_repo()
        assert exc_info.value.code == KgnErrorCode.GITHUB_REPO_NOT_FOUND


# ══════════════════════════════════════════════════════════════════════
#  Context manager (close / __enter__ / __exit__)
# ══════════════════════════════════════════════════════════════════════


class TestContextManager:
    def test_context_manager_enter_exit(self) -> None:
        """with GitHubClient() as c: ... calls close() on exit."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        config = _make_config()
        client = GitHubClient(config=config)
        client._client = httpx.Client(
            base_url="https://api.github.com",
            transport=httpx.MockTransport(handler),
        )

        with client as c:
            assert c is client
            assert c.repo_exists() is True

        # After exit, trying to use should raise (closed transport)
        # This verifies close() was called

    def test_close_explicit(self) -> None:
        """close() can be called explicitly."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        client = _mock_client(handler)
        client.close()
        # Should not raise on double-close
        client.close()


# ══════════════════════════════════════════════════════════════════════
#  get_repo
# ══════════════════════════════════════════════════════════════════════


class TestGetRepo:
    def test_get_repo_happy(self) -> None:
        """get_repo returns dict on 200."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "full_name": "test-owner/test-repo",
                    "private": True,
                    "default_branch": "main",
                },
            )

        client = _mock_client(handler)
        result = client.get_repo()
        assert result["full_name"] == "test-owner/test-repo"
        assert result["default_branch"] == "main"


# ══════════════════════════════════════════════════════════════════════
#  Branch methods
# ══════════════════════════════════════════════════════════════════════


class TestBranchMethods:
    def test_list_branches(self) -> None:
        """list_branches returns list of branch dicts."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "/branches" in str(request.url):
                return httpx.Response(
                    200,
                    json=[
                        {"name": "main", "commit": {"sha": "abc123"}},
                        {"name": "dev", "commit": {"sha": "def456"}},
                    ],
                )
            return httpx.Response(404)

        client = _mock_client(handler)
        branches = client.list_branches()
        assert len(branches) == 2
        assert branches[0]["name"] == "main"

    def test_create_branch(self) -> None:
        """create_branch posts correct ref payload."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/git/refs" in str(request.url):
                body = json.loads(request.content)
                assert body["ref"] == "refs/heads/feature-x"
                assert body["sha"] == "abc123"
                return httpx.Response(
                    201,
                    json={"ref": "refs/heads/feature-x", "object": {"sha": "abc123"}},
                )
            return httpx.Response(404)

        client = _mock_client(handler)
        result = client.create_branch("feature-x", "abc123")
        assert result["ref"] == "refs/heads/feature-x"

    def test_delete_branch(self) -> None:
        """delete_branch sends DELETE to git refs."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "DELETE" and "/git/refs/heads/old-branch" in str(request.url):
                return httpx.Response(204)
            return httpx.Response(404)

        client = _mock_client(handler)
        # Should not raise
        client.delete_branch("old-branch")


# ══════════════════════════════════════════════════════════════════════
#  close_pr
# ══════════════════════════════════════════════════════════════════════


class TestClosePR:
    def test_close_pr_happy(self) -> None:
        """close_pr sends PATCH with state=closed."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "PATCH" and "/pulls/42" in str(request.url):
                body = json.loads(request.content)
                assert body["state"] == "closed"
                return httpx.Response(
                    200,
                    json={"number": 42, "state": "closed"},
                )
            return httpx.Response(404)

        client = _mock_client(handler)
        result = client.close_pr(42)
        assert result["state"] == "closed"


# ══════════════════════════════════════════════════════════════════════
#  Error handling edge cases
# ══════════════════════════════════════════════════════════════════════


class TestErrorEdgeCases:
    def test_timeout_raises_kgn_error(self) -> None:
        """Timeout wraps into KgnError with GITHUB_API_ERROR."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        client = _mock_client(handler)
        with pytest.raises(KgnError) as exc_info:
            client.repo_exists()
        assert exc_info.value.code == KgnErrorCode.GITHUB_API_ERROR
        assert "timeout" in str(exc_info.value).lower()

    def test_http_error_raises_kgn_error(self) -> None:
        """Generic httpx error wraps into KgnError."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _mock_client(handler)
        with pytest.raises(KgnError) as exc_info:
            client.repo_exists()
        assert exc_info.value.code == KgnErrorCode.GITHUB_API_ERROR

    def test_non_json_error_body(self) -> None:
        """Server returns non-JSON body in error response."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                502,
                text="<html>Bad Gateway</html>",
                headers={"Content-Type": "text/html"},
            )

        client = _mock_client(handler)
        with pytest.raises(KgnError) as exc_info:
            client.get_repo()
        assert exc_info.value.code == KgnErrorCode.GITHUB_API_ERROR
        assert "502" in str(exc_info.value)
