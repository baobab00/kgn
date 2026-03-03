"""Unit tests for OpenAIEmbeddingClient (kgn/embedding/client.py).

Mocks the ``openai`` package to test without real API calls.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from kgn.embedding.client import MODEL_DIMENSIONS

# ── Helpers ────────────────────────────────────────────────────────────


def _make_openai_mock() -> tuple[MagicMock, MagicMock]:
    """Create mocked openai module + OpenAI client.

    Returns (openai_module_mock, openai_client_instance).
    """
    openai_mod = MagicMock(spec=["OpenAI"])
    openai_client = MagicMock()
    openai_mod.OpenAI.return_value = openai_client
    return openai_mod, openai_client


# ══════════════════════════════════════════════════════════════════════
#  MODEL_DIMENSIONS constant
# ══════════════════════════════════════════════════════════════════════


class TestModelDimensions:
    def test_known_models(self) -> None:
        assert MODEL_DIMENSIONS["text-embedding-3-small"] == 1536
        assert MODEL_DIMENSIONS["text-embedding-3-large"] == 3072
        assert MODEL_DIMENSIONS["text-embedding-ada-002"] == 1536


# ══════════════════════════════════════════════════════════════════════
#  OpenAIEmbeddingClient
# ══════════════════════════════════════════════════════════════════════


class TestOpenAIEmbeddingClient:
    """Test with mocked openai import."""

    def test_init_default_model(self) -> None:
        """Default model is text-embedding-3-small with 1536 dims."""
        openai_mod, _ = _make_openai_mock()

        with patch.dict(sys.modules, {"openai": openai_mod}):
            from kgn.embedding.client import OpenAIEmbeddingClient

            client = OpenAIEmbeddingClient(api_key="sk-test")

        assert client.model == "text-embedding-3-small"
        assert client.dimensions == 1536

    def test_init_custom_model(self) -> None:
        """Custom model uses its known dimension."""
        openai_mod, _ = _make_openai_mock()

        with patch.dict(sys.modules, {"openai": openai_mod}):
            from kgn.embedding.client import OpenAIEmbeddingClient

            client = OpenAIEmbeddingClient(
                api_key="sk-test",
                model="text-embedding-3-large",
            )

        assert client.model == "text-embedding-3-large"
        assert client.dimensions == 3072

    def test_init_unknown_model_defaults_1536(self) -> None:
        """Unknown model falls back to 1536 dims."""
        openai_mod, _ = _make_openai_mock()

        with patch.dict(sys.modules, {"openai": openai_mod}):
            from kgn.embedding.client import OpenAIEmbeddingClient

            client = OpenAIEmbeddingClient(
                api_key="sk-test",
                model="custom-model-v1",
            )

        assert client.model == "custom-model-v1"
        assert client.dimensions == 1536

    def test_embed_calls_openai_api(self) -> None:
        """embed() calls openai embeddings.create and returns vectors."""
        openai_mod, openai_client = _make_openai_mock()

        # Mock response data
        item1 = MagicMock()
        item1.embedding = [0.1] * 1536
        item2 = MagicMock()
        item2.embedding = [0.2] * 1536
        openai_client.embeddings.create.return_value = MagicMock(data=[item1, item2])

        with patch.dict(sys.modules, {"openai": openai_mod}):
            from kgn.embedding.client import OpenAIEmbeddingClient

            client = OpenAIEmbeddingClient(api_key="sk-test")
            result = client.embed(["hello", "world"])

        assert len(result) == 2
        assert len(result[0]) == 1536
        assert result[0][0] == 0.1
        assert result[1][0] == 0.2

        # Verify API call
        openai_client.embeddings.create.assert_called_once_with(
            input=["hello", "world"],
            model="text-embedding-3-small",
        )

    def test_init_respects_timeout_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """KGN_EMBEDDING_TIMEOUT env var configures the client timeout."""
        monkeypatch.setenv("KGN_EMBEDDING_TIMEOUT", "60.0")
        openai_mod, _ = _make_openai_mock()

        with patch.dict(sys.modules, {"openai": openai_mod}):
            from kgn.embedding.client import OpenAIEmbeddingClient

            OpenAIEmbeddingClient(api_key="sk-test")

        # OpenAI() should be called with timeout=60.0
        openai_mod.OpenAI.assert_called_once()
        call_kwargs = openai_mod.OpenAI.call_args
        assert call_kwargs.kwargs.get("timeout") == 60.0 or call_kwargs[1].get("timeout") == 60.0


class TestOpenAIImportError:
    """Test behavior when openai package is not available."""

    def test_import_error_raised(self) -> None:
        """ImportError raised when openai package missing."""
        # Temporarily remove openai from modules if present
        saved = sys.modules.get("openai")
        try:
            # Make openai import fail
            sys.modules["openai"] = None  # type: ignore[assignment]

            # Need to reimport to trigger the try/except
            # But since OpenAIEmbeddingClient does deferred import,
            # we just call the constructor
            with pytest.raises(ImportError, match="openai package is required"):
                # Force re-import by clearing cached class
                import importlib

                import kgn.embedding.client as _mod

                importlib.reload(_mod)
                _mod.OpenAIEmbeddingClient(api_key="sk-test")
        finally:
            if saved is not None:
                sys.modules["openai"] = saved
            else:
                sys.modules.pop("openai", None)
