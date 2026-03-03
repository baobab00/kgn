"""Embedding pipeline — vector conversion and storage."""

from kgn.embedding.client import (
    MODEL_DIMENSIONS,
    EmbeddingClient,
    OpenAIEmbeddingClient,
)
from kgn.embedding.factory import create_embedding_client
from kgn.embedding.service import EmbeddingService

__all__ = [
    "MODEL_DIMENSIONS",
    "EmbeddingClient",
    "EmbeddingService",
    "OpenAIEmbeddingClient",
    "create_embedding_client",
]
