"""Embedding client — LLM API abstraction (R8).

All embedding API calls MUST go through the ``EmbeddingClient`` protocol.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

import structlog

log = structlog.get_logger("kgn.embedding.client")

# Known model → dimension mapping
MODEL_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


@runtime_checkable
class EmbeddingClient(Protocol):
    """Protocol for embedding API abstraction (R8)."""

    @property
    def model(self) -> str:
        """Return the model name used by this client."""
        ...

    @property
    def dimensions(self) -> int:
        """Return the vector dimension produced by this client."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts and return their vectors.

        Args:
            texts: Non-empty list of text strings.

        Returns:
            List of embedding vectors (each a list of floats).
            Length and order matches *texts*.
        """
        ...


class OpenAIEmbeddingClient:
    """OpenAI text-embedding-3-small implementation.

    Requires the ``openai`` package and a valid API key.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            msg = "openai package is required: uv add openai"
            raise ImportError(msg) from exc

        self._client = OpenAI(
            api_key=api_key,
            timeout=float(os.getenv("KGN_EMBEDDING_TIMEOUT", "30.0")),
        )
        self._model = model
        self._dimensions = MODEL_DIMENSIONS.get(model, 1536)

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Call OpenAI embeddings API."""
        response = self._client.embeddings.create(
            input=texts,
            model=self._model,
        )
        return [item.embedding for item in response.data]
