"""Embedding client — LLM API abstraction (R8).

All embedding API calls MUST go through the ``EmbeddingClient`` protocol.
"""

from __future__ import annotations

import os
import time
from typing import Protocol, runtime_checkable

import structlog

from kgn.errors import KgnError, KgnErrorCode

log = structlog.get_logger("kgn.embedding.client")

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds, doubles each retry

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
        """Call OpenAI embeddings API with retry and error handling."""
        if not texts:
            return []

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = self._client.embeddings.create(
                    input=texts,
                    model=self._model,
                )
                return [item.embedding for item in response.data]
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                exc_name = type(exc).__name__

                # Timeout-class errors
                if "timeout" in exc_name.lower() or "Timeout" in str(type(exc).__mro__):
                    if attempt < _MAX_RETRIES - 1:
                        delay = _RETRY_BASE_DELAY * (2**attempt)
                        log.warning(
                            "embedding_timeout_retry",
                            attempt=attempt + 1,
                            delay=delay,
                            error=str(exc),
                        )
                        time.sleep(delay)
                        continue
                    raise KgnError(
                        KgnErrorCode.EMBEDDING_API_TIMEOUT,
                        f"Embedding API timeout after {_MAX_RETRIES} retries: {exc}",
                    ) from exc

                # Auth / permission errors — not recoverable, fail fast
                if "auth" in exc_name.lower() or "permission" in exc_name.lower():
                    raise KgnError(
                        KgnErrorCode.EMBEDDING_API_FAILED,
                        f"Embedding API authentication error: {exc}",
                    ) from exc

                # Rate limit (429) or transient server errors — retry
                is_rate = "ratelimit" in exc_name.lower() or "rate" in str(exc).lower()
                if (is_rate or "503" in str(exc)) and attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    log.warning(
                        "embedding_rate_limit_retry",
                        attempt=attempt + 1,
                        delay=delay,
                        error=str(exc),
                    )
                    time.sleep(delay)
                    continue

                # Rate limit retries exhausted or other errors — fail immediately
                raise KgnError(
                    KgnErrorCode.EMBEDDING_API_FAILED,
                    f"Embedding API call failed: {exc}",
                ) from exc

        # Should not reach here, but safety net
        raise KgnError(
            KgnErrorCode.EMBEDDING_API_FAILED,
            f"Embedding API failed after {_MAX_RETRIES} retries: {last_exc}",
        )
