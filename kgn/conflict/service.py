"""Conflict detection service.

Scans embedded nodes for high cosine similarity and manages
CONTRADICTS edge lifecycle (PENDING → APPROVED / DISMISSED).
"""

from __future__ import annotations

import itertools
import os
import uuid

import structlog

from kgn.db.repository import ConflictCandidate, KgnRepository
from kgn.models.enums import NodeType

log = structlog.get_logger("kgn.conflict")

# Default node types to scan for contradictions
DEFAULT_SCAN_TYPES: list[NodeType] = [NodeType.SPEC, NodeType.DECISION]

# Default similarity threshold (above this → candidate)
DEFAULT_THRESHOLD: float = 0.92

# Node count warning threshold for O(n²) scan
SCAN_WARN_THRESHOLD: int = int(os.getenv("KGN_CONFLICT_SCAN_WARN_THRESHOLD", "500"))


class ConflictService:
    """Detect and manage node contradictions via vector similarity."""

    def __init__(self, repo: KgnRepository) -> None:
        self._repo = repo

    def scan(
        self,
        project_id: uuid.UUID,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        node_types: list[NodeType] | None = None,
    ) -> list[ConflictCandidate]:
        """Find node pairs whose similarity exceeds *threshold*.

        Algorithm:
        1. Fetch embedded nodes of the target types.
        2. For each pair, compute cosine similarity.
        3. If similarity > threshold AND no existing APPROVED/DISMISSED
           CONTRADICTS edge → include as candidate.
        4. If a PENDING edge already exists, include with status="PENDING".
        5. Otherwise status="NEW".

        Returns candidates sorted by descending similarity.
        """
        types = node_types or DEFAULT_SCAN_TYPES
        nodes = self._repo.get_embedded_node_ids_by_types(project_id, types)

        if len(nodes) < 2:
            return []

        if len(nodes) > SCAN_WARN_THRESHOLD:
            log.warning(
                "scan_large_node_set",
                node_count=len(nodes),
                threshold=SCAN_WARN_THRESHOLD,
            )

        candidates: list[ConflictCandidate] = []

        for a, b in itertools.combinations(nodes, 2):
            sim = self._repo.compute_cosine_similarity(a["id"], b["id"])
            if sim is None or sim <= threshold:
                continue

            # Check for existing CONTRADICTS edge
            existing = self._repo.find_contradicts_edge(project_id, a["id"], b["id"])
            if existing is not None:
                edge_status = existing["status"]
                if edge_status in ("APPROVED", "DISMISSED"):
                    # Already handled — skip
                    continue
                # PENDING — include with current status
                candidates.append(
                    ConflictCandidate(
                        node_a_id=a["id"],
                        node_a_title=a["title"],
                        node_b_id=b["id"],
                        node_b_title=b["title"],
                        similarity=sim,
                        status="PENDING",
                    )
                )
            else:
                candidates.append(
                    ConflictCandidate(
                        node_a_id=a["id"],
                        node_a_title=a["title"],
                        node_b_id=b["id"],
                        node_b_title=b["title"],
                        similarity=sim,
                        status="NEW",
                    )
                )

        # Sort by descending similarity
        candidates.sort(key=lambda c: c.similarity, reverse=True)
        return candidates

    def approve(
        self,
        project_id: uuid.UUID,
        node_a_id: uuid.UUID,
        node_b_id: uuid.UUID,
        *,
        note: str = "",
        created_by: uuid.UUID | None = None,
    ) -> int:
        """Approve a contradiction — create/update CONTRADICTS edge as APPROVED.

        If CONTRADICTS edge already exists (any direction), update its status.
        Otherwise insert a new edge with status=APPROVED.

        Returns the edge id.
        """
        existing = self._repo.find_contradicts_edge(project_id, node_a_id, node_b_id)
        if existing is not None:
            self._repo.update_edge_status(existing["id"], "APPROVED")
            return existing["id"]

        return self._repo.insert_contradicts_edge(
            project_id,
            node_a_id,
            node_b_id,
            "APPROVED",
            note=note,
            created_by=created_by,
        )

    def dismiss(
        self,
        project_id: uuid.UUID,
        node_a_id: uuid.UUID,
        node_b_id: uuid.UUID,
        *,
        note: str = "",
        created_by: uuid.UUID | None = None,
    ) -> int:
        """Dismiss a contradiction — create/update CONTRADICTS edge as DISMISSED.

        Dismissed edges persist so future scans skip this pair.

        Returns the edge id.
        """
        existing = self._repo.find_contradicts_edge(project_id, node_a_id, node_b_id)
        if existing is not None:
            self._repo.update_edge_status(existing["id"], "DISMISSED")
            return existing["id"]

        return self._repo.insert_contradicts_edge(
            project_id,
            node_a_id,
            node_b_id,
            "DISMISSED",
            note=note,
            created_by=created_by,
        )
