"""Embedding service — node body_md → vector conversion + DB storage."""

from __future__ import annotations

import uuid

import structlog
from rich.console import Console

from kgn.db.repository import KgnRepository
from kgn.embedding.client import MODEL_DIMENSIONS, EmbeddingClient

log = structlog.get_logger("kgn.embedding.service")
console = Console()

# Schema dimension (must match migrations/004_embeddings.sql)
SCHEMA_DIMENSIONS = 1536


class EmbeddingService:
    """Convert node ``body_md`` to embedding vectors and store in DB.

    All API calls go through the injected ``EmbeddingClient`` (R8).
    """

    def __init__(
        self,
        repo: KgnRepository,
        client: EmbeddingClient,
        *,
        batch_size: int = 100,
    ) -> None:
        self._repo = repo
        self._client = client
        self._batch_size = batch_size

        # Dimension mismatch warning
        expected_dim = MODEL_DIMENSIONS.get(client.model)
        if expected_dim is not None and expected_dim != SCHEMA_DIMENSIONS:
            console.print(
                f"[bold yellow]Warning:[/bold yellow] Model '{client.model}' produces "
                f"{expected_dim}-dim vectors, but DB schema expects {SCHEMA_DIMENSIONS}-dim. "
                f"Migration 004_embeddings.sql may need updating.",
            )
        elif expected_dim is None:
            console.print(
                f"[bold yellow]Warning:[/bold yellow] Unknown model '{client.model}'. "
                f"Ensure its output dimension matches DB schema ({SCHEMA_DIMENSIONS}).",
            )

    def embed_node(
        self,
        node_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> None:
        """Embed a single node and store the vector.

        Reads the node's ``body_md`` from DB, calls the embedding client,
        and upserts the result into ``node_embeddings``.

        Raises:
            ValueError: If the node does not exist.
        """
        row = self._repo.get_node_text_for_embedding(node_id)
        if row is None:
            msg = f"Node {node_id} not found"
            raise ValueError(msg)

        text = self._build_text(row["title"], row["body_md"])
        vectors = self._client.embed([text])
        self._repo.upsert_embedding(
            node_id=node_id,
            project_id=project_id,
            embedding=vectors[0],
            model=self._client.model,
        )

    def embed_batch(
        self,
        project_id: uuid.UUID,
        *,
        force: bool = False,
    ) -> int:
        """Embed all un-embedded nodes in a project.

        Args:
            project_id: Target project.
            force: If True, re-embed all nodes (not just un-embedded).

        Returns:
            Number of nodes embedded.
        """
        if force:
            rows = self._repo.get_nodes_text_for_embedding(
                project_id=project_id,
            )
        else:
            rows = self._repo.get_unembedded_nodes(project_id)

        if not rows:
            return 0

        count = 0
        for batch_start in range(0, len(rows), self._batch_size):
            batch = rows[batch_start : batch_start + self._batch_size]
            texts = [self._build_text(r["title"], r["body_md"]) for r in batch]
            vectors = self._client.embed(texts)

            for row, vec in zip(batch, vectors, strict=True):
                self._repo.upsert_embedding(
                    node_id=row["id"],
                    project_id=project_id,
                    embedding=vec,
                    model=self._client.model,
                )
                count += 1

        return count

    def embed_nodes(
        self,
        node_ids: list[uuid.UUID],
        project_id: uuid.UUID,
    ) -> int:
        """Embed specific nodes by their IDs.

        Args:
            node_ids: List of node UUIDs to embed.
            project_id: Target project.

        Returns:
            Number of nodes successfully embedded.
        """
        if not node_ids:
            return 0

        rows = self._repo.get_nodes_text_for_embedding(node_ids=node_ids)

        if not rows:
            return 0

        count = 0
        for batch_start in range(0, len(rows), self._batch_size):
            batch = rows[batch_start : batch_start + self._batch_size]
            texts = [self._build_text(r["title"], r["body_md"]) for r in batch]
            vectors = self._client.embed(texts)

            for row, vec in zip(batch, vectors, strict=True):
                self._repo.upsert_embedding(
                    node_id=row["id"],
                    project_id=project_id,
                    embedding=vec,
                    model=self._client.model,
                )
                count += 1

        return count

    @staticmethod
    def _build_text(title: str, body_md: str) -> str:
        """Combine title and body into embeddable text."""
        if body_md.strip():
            return f"{title}\n\n{body_md}"
        return title
