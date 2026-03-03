"""GitHub API integration tests (R-032).

These tests interact with a REAL GitHub repository and require:
  - ``KGN_GITHUB_TOKEN`` environment variable with a valid PAT
  - ``KGN_GITHUB_OWNER`` environment variable (GitHub user / org)
  - ``KGN_GITHUB_REPO``  environment variable (test repo name)

Run with::

    pytest tests/test_github_integration.py --run-github

The test repo should be a *disposable* repository — tests will create
branches, PRs, and push commits.  Never point at a production repo.
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest

pytestmark = pytest.mark.github

# ── helpers ────────────────────────────────────────────────────────────


def _github_env() -> tuple[str, str, str]:
    """Return (token, owner, repo) or skip if not set."""
    token = os.environ.get("KGN_GITHUB_TOKEN", "")
    owner = os.environ.get("KGN_GITHUB_OWNER", "")
    repo = os.environ.get("KGN_GITHUB_REPO", "")
    if not all([token, owner, repo]):
        pytest.skip("KGN_GITHUB_TOKEN / OWNER / REPO not set")
    return token, owner, repo


# ── Tests ──────────────────────────────────────────────────────────────


class TestGitHubClientAuth:
    """Verify basic GitHub authentication via real API."""

    def test_valid_token_returns_user(self) -> None:
        token, owner, repo = _github_env()

        from kgn.github.client import GitHubClient, GitHubConfig

        config = GitHubConfig(token=token, owner=owner, repo=repo)
        client = GitHubClient(config)

        # A simple check — list branches to verify auth works
        # If the token is invalid, httpx will raise or return 401
        branches = client.list_branches()
        assert isinstance(branches, list)


class TestGitHubPRLifecycle:
    """Test creating and closing a PR via real API."""

    def test_create_pr_and_close(self) -> None:
        token, owner, repo = _github_env()

        import base64

        import httpx

        from kgn.errors import KgnError
        from kgn.github.client import GitHubClient, GitHubConfig

        config = GitHubConfig(token=token, owner=owner, repo=repo)
        client = GitHubClient(config)

        branch_name = f"kgn-test-{uuid.uuid4().hex[:8]}"

        # Get default branch SHA
        branches = client.list_branches()
        default = next((b for b in branches if b.get("name") in ("main", "master")), None)
        if default is None:
            pytest.skip("No main/master branch found in test repo")

        sha = default["commit"]["sha"]

        # Create branch
        client.create_branch(branch_name, sha)

        try:
            # Add a file on the new branch so it diverges from base
            # (GitHub requires at least 1 divergent commit for PR creation)
            content_b64 = base64.b64encode(
                f"# KGN test file\nCreated by R-032 E2E: {branch_name}\n".encode()
            ).decode()
            put_resp = httpx.put(
                f"https://api.github.com/repos/{owner}/{repo}/contents/test-{branch_name}.md",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "message": f"test: add file for PR ({branch_name})",
                    "content": content_b64,
                    "branch": branch_name,
                },
                timeout=30.0,
            )
            assert put_resp.status_code == 201, f"File creation failed: {put_resp.text}"

            # Create PR (requires Pull Requests: Write permission on PAT)
            try:
                pr = client.create_pull_request(
                    title=f"[KGN TEST] Auto-created by R-032 E2E — {branch_name}",
                    head=branch_name,
                    base=default["name"],
                    body="This PR was created by the KGN R-032 E2E test. Safe to close.",
                )
            except KgnError as exc:
                if "403" in str(exc):
                    pytest.skip("PAT lacks Pull Requests: Write permission")
                raise
            assert pr.get("number") is not None

            # Close PR
            pr_number = pr["number"]
            client.close_pr(pr_number)
        finally:
            # Cleanup: delete branch (also removes the test file)
            with contextlib.suppress(Exception):  # noqa: BLE001
                client.delete_branch(branch_name)


class TestGitHubSyncPush:
    """Test that SyncService.push() reaches GitHub."""

    def test_push_creates_commit(self, tmp_path, db_conn, repo, project_id, agent_id) -> None:
        """Full push pipeline: export → commit → push to real remote."""
        token, owner, gh_repo = _github_env()

        from kgn.git.service import GitService
        from kgn.github.client import GitHubClient, GitHubConfig
        from kgn.github.sync_service import SyncService
        from kgn.models.node import NodeRecord, NodeStatus, NodeType

        # Insert a test node directly via repo
        node = NodeRecord(
            id=uuid.uuid4(),
            type=NodeType.SPEC,
            status=NodeStatus.ACTIVE,
            title="R-032 E2E push test",
            body="This node was created by the R-032 E2E test.",
            project_id=project_id,
            confidence=0.9,
        )
        repo.upsert_node(node)

        # Init git repo in tmp_path
        git = GitService(tmp_path)
        git.init()

        config = GitHubConfig(token=token, owner=owner, repo=gh_repo)
        client = GitHubClient(config)

        sync = SyncService(git, client)
        sync.ensure_remote(config)

        result = sync.push(
            project_name="test-project",
            project_id=project_id,
            sync_dir=tmp_path,
            repo=repo,
        )

        # Export should find at least the node we inserted
        assert result.exported >= 1
        # Commit should succeed (local content was staged)
        assert result.committed is True
