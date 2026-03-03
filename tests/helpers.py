"""Shared test helpers for KGN test suite.

This module contains reusable mocks, constants, and E2E helper functions
that are imported by both ``conftest.py`` and individual test modules.
"""

from __future__ import annotations

import asyncio
import random

from kgn.embedding.client import EmbeddingClient

# ── Constants ──────────────────────────────────────────────────────────

EMBEDDING_DIMS = 1536


# ── E2E MCP Helpers ────────────────────────────────────────────────────


def call_tool(server, tool_name: str, **kwargs) -> str:
    """Invoke a registered FastMCP tool by name (sync wrapper).

    This is the canonical helper for E2E tests.  Previously duplicated
    as ``_call_tool`` in multiple test_e2e_* files (consolidated in P7/Step 1).
    """

    async def _run():
        return await server.call_tool(tool_name, kwargs)

    raw = asyncio.run(_run())
    content_list = raw[0] if isinstance(raw, tuple) else raw
    if content_list and hasattr(content_list[0], "text"):
        return content_list[0].text
    return str(content_list)


def make_kgn(
    *,
    node_id: str = "new:e2e-test",
    node_type: str = "SPEC",
    title: str = "E2E Spec",
    project_id: str,
    agent_id: str = "mcp",
    body: str = "## Content\n\nE2E test body.",
    tags: str = '["e2e"]',
) -> str:
    """Build a minimal .kgn string for testing.

    Consolidated from ``_make_kgn`` duplicated in test_e2e_phase4/phase5.
    """
    return (
        "---\n"
        'kgn_version: "0.1"\n'
        f'id: "{node_id}"\n'
        f"type: {node_type}\n"
        f'title: "{title}"\n'
        "status: ACTIVE\n"
        f'project_id: "{project_id}"\n'
        f'agent_id: "{agent_id}"\n'
        'created_at: "2026-03-01T00:00:00+09:00"\n'
        f"tags: {tags}\n"
        "confidence: 0.9\n"
        "---\n\n"
        f"{body}\n"
    )


def make_kge(
    *,
    from_id: str,
    to_id: str,
    edge_type: str = "DEPENDS_ON",
    project_id: str,
    agent_id: str = "mcp",
    note: str = "e2e edge",
) -> str:
    """Build a minimal .kge string for testing.

    Consolidated from ``_make_kge`` duplicated in test_e2e_phase4/phase5.
    """
    return (
        "---\n"
        'kgn_version: "0.1"\n'
        f'project_id: "{project_id}"\n'
        f'agent_id: "{agent_id}"\n'
        'created_at: "2026-03-01T00:00:00+09:00"\n'
        "edges:\n"
        f'  - from: "{from_id}"\n'
        f'    to: "{to_id}"\n'
        f"    type: {edge_type}\n"
        f'    note: "{note}"\n'
        "---\n"
    )


def assert_error_shape(data: dict) -> None:
    """Assert that an error response has the standard 4-field shape.

    Consolidated from ``_assert_error_shape`` in test_e2e_phase5.
    """
    assert "error" in data, f"Missing 'error' field: {data}"
    assert "code" in data, f"Missing 'code' field: {data}"
    assert "detail" in data, f"Missing 'detail' field: {data}"
    assert "recoverable" in data, f"Missing 'recoverable' field: {data}"
    assert isinstance(data["recoverable"], bool), "recoverable must be bool"
    assert data["code"].startswith("KGN-"), f"Code must start with KGN-: {data['code']}"


# ── Shared Mock ────────────────────────────────────────────────────────


class MockEmbeddingClient:
    """Deterministic mock that satisfies the ``EmbeddingClient`` protocol.

    Features:
    - Text-hash-based deterministic vectors — same text always yields the
      same normalised vector, different texts yield different vectors.  This
      makes similarity search tests reliable.
    - ``call_count`` / ``last_texts`` tracking for interaction assertions.
    - Configurable ``model`` and ``dimensions``.
    """

    def __init__(
        self,
        *,
        model: str = "text-embedding-3-small",
        dimensions: int = EMBEDDING_DIMS,
        seed: int = 42,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self._seed = seed
        self.call_count: int = 0
        self.last_texts: list[str] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        self.last_texts = texts
        results: list[list[float]] = []
        for text in texts:
            rng = random.Random(hash(text) ^ self._seed)
            vec = [rng.gauss(0, 0.1) for _ in range(self._dimensions)]
            norm = sum(x * x for x in vec) ** 0.5
            if norm > 0:
                vec = [x / norm for x in vec]
            results.append(vec)
        return results


assert isinstance(MockEmbeddingClient(), EmbeddingClient)
