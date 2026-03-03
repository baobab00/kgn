"""Embedding client factory — environment-based provider resolution.

The factory reads ``KGN_OPENAI_API_KEY`` (and optionally
``KGN_OPENAI_EMBED_MODEL``) from the environment to instantiate the
appropriate ``EmbeddingClient``.

Currently the only production provider is **OpenAI text-embedding-3-small**.
The ``EmbeddingClient`` Protocol guarantees that additional providers can
be added later without touching call-sites.
"""

from __future__ import annotations

import os

import structlog

from kgn.embedding.client import EmbeddingClient

log = structlog.get_logger("kgn.embedding.factory")

_DEFAULT_PROVIDER = "openai"


def create_embedding_client(
    provider: str | None = None,
) -> EmbeddingClient | None:
    """Create an ``EmbeddingClient`` from environment variables.

    Parameters
    ----------
    provider:
        Provider name.  ``"openai"`` (default) is the only supported value.
        Pass ``None`` to use the default.

    Returns
    -------
    EmbeddingClient | None
        A configured client, or ``None`` when the required credentials
        are missing (e.g. ``KGN_OPENAI_API_KEY`` not set).

    Notes
    -----
    - Returns ``None`` instead of raising so that callers can implement
      graceful degradation (R12).
    - The ``openai`` package is lazily imported inside
      ``OpenAIEmbeddingClient``, so it only needs to be installed when
      embedding features are actually used.
    """
    provider = (provider or _DEFAULT_PROVIDER).lower()

    if provider == "openai":
        return _create_openai_client()

    log.warning("unknown_embedding_provider", provider=provider)
    return None


def _create_openai_client() -> EmbeddingClient | None:
    """Instantiate an ``OpenAIEmbeddingClient`` from env vars."""
    api_key = os.environ.get("KGN_OPENAI_API_KEY", "").strip()
    if not api_key:
        log.info("embedding_disabled", reason="KGN_OPENAI_API_KEY not set")
        return None

    model = os.environ.get("KGN_OPENAI_EMBED_MODEL", "text-embedding-3-small").strip()

    try:
        from kgn.embedding.client import OpenAIEmbeddingClient

        return OpenAIEmbeddingClient(api_key=api_key, model=model)
    except ImportError:
        log.warning("embedding_disabled", reason="openai package not installed")
        return None
