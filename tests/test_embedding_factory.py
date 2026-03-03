"""Tests for embedding factory, auto-embed on MCP ingest, and CLI provider-test.

Test categories:
- EmbeddingFactory: create_embedding_client() with various env configs
- MCP auto-embed: ingest_node with/without embedding client
- CLI: embed provider-test, embed batch (smoke tests)
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.helpers import MockEmbeddingClient

# Check if openai package is available
_has_openai = True
try:
    import openai  # noqa: F401
except ImportError:
    _has_openai = False

_skip_no_openai = pytest.mark.skipif(not _has_openai, reason="openai package not installed")


# ══════════════════════════════════════════════════════════════════════
# Factory tests
# ══════════════════════════════════════════════════════════════════════


class TestCreateEmbeddingClient:
    """Tests for create_embedding_client() factory."""

    def test_returns_none_when_no_api_key(self, monkeypatch) -> None:
        """No KGN_OPENAI_API_KEY → None (graceful degradation)."""
        monkeypatch.delenv("KGN_OPENAI_API_KEY", raising=False)

        from kgn.embedding.factory import create_embedding_client

        client = create_embedding_client()
        assert client is None

    def test_returns_none_for_empty_api_key(self, monkeypatch) -> None:
        """Empty string API key → None."""
        monkeypatch.setenv("KGN_OPENAI_API_KEY", "")

        from kgn.embedding.factory import create_embedding_client

        client = create_embedding_client()
        assert client is None

    def test_returns_none_for_whitespace_api_key(self, monkeypatch) -> None:
        """Whitespace-only API key → None."""
        monkeypatch.setenv("KGN_OPENAI_API_KEY", "   ")

        from kgn.embedding.factory import create_embedding_client

        client = create_embedding_client()
        assert client is None

    @_skip_no_openai
    def test_returns_client_with_valid_key(self, monkeypatch) -> None:
        """Valid API key → OpenAIEmbeddingClient instance."""
        monkeypatch.setenv("KGN_OPENAI_API_KEY", "sk-test-valid-key-123")

        from kgn.embedding.client import EmbeddingClient
        from kgn.embedding.factory import create_embedding_client

        client = create_embedding_client()
        assert client is not None
        assert isinstance(client, EmbeddingClient)
        assert client.model == "text-embedding-3-small"

    @_skip_no_openai
    def test_respects_model_env_var(self, monkeypatch) -> None:
        """KGN_OPENAI_EMBED_MODEL is used when set."""
        monkeypatch.setenv("KGN_OPENAI_API_KEY", "sk-test-key")
        monkeypatch.setenv("KGN_OPENAI_EMBED_MODEL", "text-embedding-3-large")

        from kgn.embedding.factory import create_embedding_client

        client = create_embedding_client()
        assert client is not None
        assert client.model == "text-embedding-3-large"

    @_skip_no_openai
    def test_default_model_when_env_not_set(self, monkeypatch) -> None:
        """Default model is text-embedding-3-small."""
        monkeypatch.setenv("KGN_OPENAI_API_KEY", "sk-test-key")
        monkeypatch.delenv("KGN_OPENAI_EMBED_MODEL", raising=False)

        from kgn.embedding.factory import create_embedding_client

        client = create_embedding_client()
        assert client is not None
        assert client.model == "text-embedding-3-small"

    def test_unknown_provider_returns_none(self, monkeypatch) -> None:
        """Unknown provider → None."""
        monkeypatch.setenv("KGN_OPENAI_API_KEY", "sk-test-key")

        from kgn.embedding.factory import create_embedding_client

        client = create_embedding_client(provider="anthropic")
        assert client is None

    @_skip_no_openai
    def test_explicit_openai_provider(self, monkeypatch) -> None:
        """Explicit provider='openai' works."""
        monkeypatch.setenv("KGN_OPENAI_API_KEY", "sk-test-key")

        from kgn.embedding.factory import create_embedding_client

        client = create_embedding_client(provider="openai")
        assert client is not None

    @_skip_no_openai
    def test_provider_case_insensitive(self, monkeypatch) -> None:
        """Provider name is case-insensitive."""
        monkeypatch.setenv("KGN_OPENAI_API_KEY", "sk-test-key")

        from kgn.embedding.factory import create_embedding_client

        client = create_embedding_client(provider="OpenAI")
        assert client is not None


# ══════════════════════════════════════════════════════════════════════
# MCP auto-embed tests
# ══════════════════════════════════════════════════════════════════════


def _make_kgn_content(
    *,
    node_id: str = "new:embed-test",
    project_id: str = "test-project",
    title: str = "Embed Test Node",
) -> str:
    return (
        "---\n"
        'kgn_version: "0.1"\n'
        f'id: "{node_id}"\n'
        "type: SPEC\n"
        f'title: "{title}"\n'
        "status: ACTIVE\n"
        f'project_id: "{project_id}"\n'
        'agent_id: "mcp"\n'
        'created_at: "2026-03-02T00:00:00+09:00"\n'
        'tags: ["test"]\n'
        "confidence: 0.9\n"
        "---\n"
        "\n## Context\n\nTest body for embedding.\n"
    )


def _call_tool(server, tool_name: str, **kwargs) -> str:
    """Invoke a tool on a FastMCP server (sync wrapper)."""
    import asyncio

    async def _run():
        return await server.call_tool(tool_name, kwargs)

    raw = asyncio.run(_run())
    content_list = raw[0] if isinstance(raw, tuple) else raw
    if content_list and hasattr(content_list[0], "text"):
        return content_list[0].text
    return str(content_list)


class TestMCPAutoEmbed:
    """Tests for automatic embedding on MCP ingest_node."""

    def test_ingest_with_mock_embed_client(self, db_conn, repo) -> None:
        """ingest_node with mock embedding client → embedding: success."""
        from kgn.mcp.server import create_server

        project_name = f"embed-test-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        mock_client = MockEmbeddingClient()

        server = create_server(project_name, conn=db_conn, embedding_client=mock_client)

        content = _make_kgn_content(project_id=project_name)
        result = _call_tool(server, "ingest_node", kgn_content=content)
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["embedding"] == "success"
        assert mock_client.call_count == 1

        # Verify embedding actually stored in DB
        node_id = uuid.UUID(data["node_id"])
        assert repo.has_embedding(node_id)

    def test_ingest_without_embed_client(self, db_conn, repo) -> None:
        """ingest_node with embedding_client=None → embedding: skipped."""
        from kgn.mcp.server import create_server

        project_name = f"embed-test-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn, embedding_client=None)

        content = _make_kgn_content(project_id=project_name)
        result = _call_tool(server, "ingest_node", kgn_content=content)
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["embedding"] == "skipped"

        # Node should exist but without embedding
        node_id = uuid.UUID(data["node_id"])
        assert not repo.has_embedding(node_id)

    def test_ingest_embed_failure_graceful(self, db_conn, repo) -> None:
        """If embedding fails, ingest still succeeds with embedding: failed."""
        from kgn.mcp.server import create_server

        project_name = f"embed-test-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        # Create a client that always raises
        class FailingClient:
            @property
            def model(self) -> str:
                return "test"

            @property
            def dimensions(self) -> int:
                return 1536

            def embed(self, texts: list[str]) -> list[list[float]]:
                msg = "API connection failed"
                raise RuntimeError(msg)

        server = create_server(project_name, conn=db_conn, embedding_client=FailingClient())

        content = _make_kgn_content(project_id=project_name)
        result = _call_tool(server, "ingest_node", kgn_content=content)
        data = json.loads(result)

        assert data["status"] == "ok"
        assert data["embedding"] == "failed"
        # Node itself should still be created
        node_id = uuid.UUID(data["node_id"])
        node = repo.get_node_by_id(node_id)
        assert node is not None

    def test_ingest_invalid_kgn_no_embed(self, db_conn, repo) -> None:
        """Invalid .kgn → error response, no embedding attempt."""
        from kgn.mcp.server import create_server

        project_name = f"embed-test-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        mock_client = MockEmbeddingClient()

        server = create_server(project_name, conn=db_conn, embedding_client=mock_client)

        result = _call_tool(server, "ingest_node", kgn_content="not valid kgn")
        data = json.loads(result)

        assert "error" in data
        assert mock_client.call_count == 0

    def test_multiple_ingest_with_embed(self, db_conn, repo) -> None:
        """Multiple ingest_node calls each trigger separate embeddings."""
        from kgn.mcp.server import create_server

        project_name = f"embed-test-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        mock_client = MockEmbeddingClient()

        server = create_server(project_name, conn=db_conn, embedding_client=mock_client)

        for i in range(3):
            content = _make_kgn_content(
                node_id=f"new:node-{i}",
                project_id=project_name,
                title=f"Node {i}",
            )
            result = _call_tool(server, "ingest_node", kgn_content=content)
            data = json.loads(result)
            assert data["embedding"] == "success"

        assert mock_client.call_count == 3


# ══════════════════════════════════════════════════════════════════════
# MCP server factory embedding injection
# ══════════════════════════════════════════════════════════════════════


class TestServerEmbeddingInjection:
    """Tests for create_server embedding_client parameter."""

    def test_default_sentinel_creates_from_factory(self, db_conn, repo, monkeypatch) -> None:
        """Default (no embedding_client param) uses factory."""
        from kgn.mcp.server import create_server

        monkeypatch.delenv("KGN_OPENAI_API_KEY", raising=False)
        project_name = f"embed-test-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn)
        # No API key → factory returns None
        assert server._kgn_embed_client is None  # type: ignore[attr-defined]

    def test_explicit_none_disables_embedding(self, db_conn, repo) -> None:
        """Explicit None disables embedding."""
        from kgn.mcp.server import create_server

        project_name = f"embed-test-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)

        server = create_server(project_name, conn=db_conn, embedding_client=None)
        assert server._kgn_embed_client is None  # type: ignore[attr-defined]

    def test_explicit_mock_client_injected(self, db_conn, repo) -> None:
        """Explicit MockEmbeddingClient is stored on server."""
        from kgn.mcp.server import create_server

        project_name = f"embed-test-{uuid.uuid4().hex[:8]}"
        repo.get_or_create_project(project_name)
        mock = MockEmbeddingClient()

        server = create_server(project_name, conn=db_conn, embedding_client=mock)
        assert server._kgn_embed_client is mock  # type: ignore[attr-defined]


# ══════════════════════════════════════════════════════════════════════
# CLI smoke tests — embed provider-test
# ══════════════════════════════════════════════════════════════════════


class TestEmbedProviderTestCLI:
    """Smoke tests for kgn embed provider-test command."""

    def test_provider_test_no_api_key(self, monkeypatch) -> None:
        """kgn embed provider-test without API key → error."""
        from typer.testing import CliRunner

        from kgn.cli import app

        monkeypatch.delenv("KGN_OPENAI_API_KEY", raising=False)

        runner = CliRunner()
        result = runner.invoke(app, ["embed", "provider-test"])
        assert result.exit_code != 0
        assert "not configured" in result.output.lower() or "KGN_OPENAI_API_KEY" in result.output
