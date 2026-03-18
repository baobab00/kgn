"""Single source of truth for all SQL queries.

Every database interaction goes through ``KgnRepository``, which receives a
:class:`psycopg.Connection` and is therefore fully testable within a transaction
that rolls back cleanly after each test.

All SQL lives here intentionally — not in service layers — so that schema
migrations have a single, searchable impact surface.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from psycopg import Connection
from psycopg.rows import dict_row

from kgn.errors import KgnError, KgnErrorCode
from kgn.models.edge import EdgeRecord
from kgn.models.enums import ActivityType, EdgeType, NodeStatus, NodeType
from kgn.models.node import NodeRecord

# ── Helper dataclass ───────────────────────────────────────────────────


@dataclass
class UpsertResult:
    """Return value of :meth:`KgnRepository.upsert_node`."""

    node_id: uuid.UUID
    status: str  # "CREATED" | "UPDATED" | "SKIPPED"


@dataclass
class SubgraphNode:
    """Lightweight node representation returned by ``extract_subgraph``."""

    id: uuid.UUID
    type: str
    status: str
    title: str
    body_md: str
    depth: int
    tags: list[str] = field(default_factory=list)


@dataclass
class SimilarNode:
    """Result of a vector similarity search."""

    id: uuid.UUID
    type: str
    title: str
    similarity: float  # 1 - cosine_distance
    depth: int = 0  # reserved for subgraph use


@dataclass
class ConflictCandidate:
    """A pair of nodes that may contradict each other."""

    node_a_id: uuid.UUID
    node_a_title: str
    node_b_id: uuid.UUID
    node_b_title: str
    similarity: float
    status: str  # "NEW" | "PENDING" | "APPROVED" | "DISMISSED"


@dataclass
class TaskQueueItem:
    """Task queue item."""

    id: uuid.UUID
    project_id: uuid.UUID
    task_node_id: uuid.UUID
    priority: int
    # NOTE: BLOCKED state activated in Phase 6 / Step 3 (dependency chain).
    state: str  # READY | IN_PROGRESS | BLOCKED | DONE | FAILED
    leased_by: uuid.UUID | None
    lease_expires_at: datetime | None
    attempts: int
    max_attempts: int
    created_at: datetime
    updated_at: datetime


# ── Repository ─────────────────────────────────────────────────────────


class KgnRepository:
    """All SQL operations in one place."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    # ── Transaction helpers ─────────────────────────────────────────────

    def savepoint(self, name: str) -> None:
        """Create a SAVEPOINT with the given name."""
        self._conn.execute(f"SAVEPOINT {name}")

    def release_savepoint(self, name: str) -> None:
        """Release (commit) a SAVEPOINT."""
        self._conn.execute(f"RELEASE SAVEPOINT {name}")

    def rollback_to_savepoint(self, name: str) -> None:
        """Roll back to a SAVEPOINT (without releasing it)."""
        self._conn.execute(f"ROLLBACK TO SAVEPOINT {name}")

    # ── Internal helpers ───────────────────────────────────────────────

    def _dict_fetchone(self, query: str, params: tuple | list = ()) -> dict | None:
        """Execute a query and return a single row as dict."""
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def _dict_fetchall(self, query: str, params: tuple | list = ()) -> list[dict]:
        """Execute a query and return all rows as dicts."""
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchall()

    # ── Project ────────────────────────────────────────────────────────

    def get_project_by_name(self, name: str) -> uuid.UUID | None:
        """Look up a project UUID by name. Returns None if not found."""
        row = self._conn.execute(
            "SELECT id FROM projects WHERE name = %s",
            (name,),
        ).fetchone()
        if row:
            return row[0]
        return None

    def get_or_create_project(self, name: str) -> uuid.UUID:
        """Return existing project UUID or create a new one."""
        row = self._conn.execute(
            "SELECT id FROM projects WHERE name = %s",
            (name,),
        ).fetchone()
        if row:
            return row[0]

        row = self._conn.execute(
            "INSERT INTO projects (name) VALUES (%s) RETURNING id",
            (name,),
        ).fetchone()
        if row is None:
            raise KgnError(
                code=KgnErrorCode.INTERNAL_ERROR,
                message="INSERT projects RETURNING yielded no row",
            )
        return row[0]

    # ── Agent ──────────────────────────────────────────────────────────

    def get_or_create_agent(
        self,
        project_id: uuid.UUID,
        agent_key: str,
        role: str = "admin",
    ) -> uuid.UUID:
        """Return existing agent UUID or create a new one.

        If the agent already exists and *role* differs from the stored value,
        the stored role is updated to match.

        Default role is ``admin`` for backward compatibility (R16).
        """
        row = self._conn.execute(
            "SELECT id, role FROM agents WHERE project_id = %s AND agent_key = %s",
            (project_id, agent_key),
        ).fetchone()
        if row:
            agent_id, current_role = row
            if str(current_role) != role:
                self._conn.execute(
                    "UPDATE agents SET role = %s WHERE id = %s",
                    (role, agent_id),
                )
            return agent_id

        row = self._conn.execute(
            "INSERT INTO agents (project_id, agent_key, role) VALUES (%s, %s, %s) RETURNING id",
            (project_id, agent_key, role),
        ).fetchone()
        if row is None:
            raise KgnError(
                code=KgnErrorCode.INTERNAL_ERROR,
                message="INSERT agents RETURNING yielded no row",
            )
        return row[0]

    def get_agent_role(self, agent_id: uuid.UUID) -> str | None:
        """Return the role string for the given agent, or None if not found."""
        row = self._conn.execute(
            "SELECT role FROM agents WHERE id = %s",
            (agent_id,),
        ).fetchone()
        if row:
            return str(row[0])
        return None

    def set_agent_role(self, agent_id: uuid.UUID, role: str) -> bool:
        """Update an agent's role. Returns True if the agent was found."""
        cur = self._conn.execute(
            "UPDATE agents SET role = %s WHERE id = %s",
            (role, agent_id),
        )
        return cur.rowcount > 0

    def list_agents(self, project_id: uuid.UUID) -> list[dict]:
        """Return all agents for a project as dicts."""
        return self._dict_fetchall(
            "SELECT id, agent_key, role, created_at FROM agents "
            "WHERE project_id = %s ORDER BY created_at",
            (project_id,),
        )

    def get_agent_by_key(
        self,
        project_id: uuid.UUID,
        agent_key: str,
    ) -> dict | None:
        """Look up agent by project + key. Returns dict or None."""
        return self._dict_fetchone(
            "SELECT id, agent_key, role, created_at FROM agents "
            "WHERE project_id = %s AND agent_key = %s",
            (project_id, agent_key),
        )

    # ── Node ───────────────────────────────────────────────────────────

    def upsert_node(self, node: NodeRecord) -> UpsertResult:
        """Insert or update a node, enforcing V7 and V8.

        Flow:
        1. V8 — duplicate ``content_hash`` → SKIPPED
        2. V7 — ``supersedes`` target must exist
        3. Existing UUID → save version, UPDATE
        4. New UUID → INSERT

        Returns:
            UpsertResult with the final ``node_id`` and ``status``.
        """
        # V8: content_hash duplicate check
        if node.content_hash:
            existing = self.find_node_by_content_hash(node.project_id, node.content_hash)
            if existing is not None:
                return UpsertResult(node_id=existing.id, status="SKIPPED")

        # Check if node already exists (UPDATE path)
        existing_node = self.get_node_by_id(node.id)

        if existing_node is not None:
            # Save current state to node_versions before updating
            self._save_version(existing_node)
            self._update_node(node)
            # Log activity
            if node.created_by:
                self.log_activity(
                    project_id=node.project_id,
                    agent_id=node.created_by,
                    activity_type=ActivityType.NODE_UPDATED,
                    target_node_id=node.id,
                    message=f"Node updated: {node.title}",
                )
            return UpsertResult(node_id=node.id, status="UPDATED")

        # INSERT path
        self._insert_node(node)
        # Log activity
        if node.created_by:
            self.log_activity(
                project_id=node.project_id,
                agent_id=node.created_by,
                activity_type=ActivityType.NODE_CREATED,
                target_node_id=node.id,
                message=f"Node created: {node.title}",
            )
        return UpsertResult(node_id=node.id, status="CREATED")

    def _insert_node(self, node: NodeRecord) -> None:
        """Raw INSERT into nodes table."""
        self._conn.execute(
            """
            INSERT INTO nodes
                (id, project_id, type, status, title, body_md,
                 file_path, content_hash, tags, confidence,
                 created_by, created_at, updated_at)
            VALUES
                (%s, %s, %s, %s, %s, %s,
                 %s, %s, %s, %s,
                 %s, COALESCE(%s, now()), now())
            """,
            (
                node.id,
                node.project_id,
                node.type.value,
                node.status.value,
                node.title,
                node.body_md,
                node.file_path,
                node.content_hash,
                node.tags,
                node.confidence,
                node.created_by,
                node.created_at,
            ),
        )

    def _update_node(self, node: NodeRecord) -> None:
        """Raw UPDATE on nodes table."""
        self._conn.execute(
            """
            UPDATE nodes
            SET type         = %s,
                status       = %s,
                title        = %s,
                body_md      = %s,
                file_path    = %s,
                content_hash = %s,
                tags         = %s,
                confidence   = %s,
                updated_at   = now()
            WHERE id = %s
            """,
            (
                node.type.value,
                node.status.value,
                node.title,
                node.body_md,
                node.file_path,
                node.content_hash,
                node.tags,
                node.confidence,
                node.id,
            ),
        )

    def _save_version(self, node: NodeRecord) -> None:
        """Save old state to ``node_versions`` before an UPDATE.

        Captures the complete node snapshot including type, status,
        file_path, tags, and confidence (Phase 12 / Step 2).
        """
        # Get next version number
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM node_versions WHERE node_id = %s",
            (node.id,),
        ).fetchone()
        if row is None:
            msg = f"Failed to compute next version for node {node.id}"
            raise KgnError(KgnErrorCode.INTERNAL_ERROR, msg)
        next_version = row[0]

        self._conn.execute(
            """
            INSERT INTO node_versions
                (node_id, version, title, body_md, content_hash,
                 updated_by, updated_at,
                 type, status, file_path, tags, confidence)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                node.id,
                next_version,
                node.title,
                node.body_md,
                node.content_hash,
                node.created_by,
                node.updated_at or datetime.now().astimezone(),
                node.type,
                node.status,
                node.file_path,
                node.tags,
                node.confidence,
            ),
        )

    def get_node_by_id(self, node_id: uuid.UUID) -> NodeRecord | None:
        """Fetch a single node by UUID."""
        row = self._dict_fetchone(
            "SELECT id, project_id, type, status, title, body_md, "
            "file_path, content_hash, tags, confidence, "
            "created_by, created_at, updated_at "
            "FROM nodes WHERE id = %s",
            (node_id,),
        )
        if row is None:
            return None
        return _row_to_node(row)

    def find_node_by_content_hash(
        self,
        project_id: uuid.UUID,
        content_hash: str,
    ) -> NodeRecord | None:
        """V8: Find a node with the same content_hash in the same project."""
        row = self._dict_fetchone(
            "SELECT id, project_id, type, status, title, body_md, "
            "file_path, content_hash, tags, confidence, "
            "created_by, created_at, updated_at "
            "FROM nodes WHERE project_id = %s AND content_hash = %s LIMIT 1",
            (project_id, content_hash),
        )
        if row is None:
            return None
        return _row_to_node(row)

    def check_node_exists(self, node_id: uuid.UUID) -> bool:
        """V7: Check whether a node exists in DB."""
        row = self._conn.execute(
            "SELECT 1 FROM nodes WHERE id = %s",
            (node_id,),
        ).fetchone()
        return row is not None

    def search_nodes(
        self,
        project_id: uuid.UUID,
        *,
        node_type: NodeType | None = None,
        status: NodeStatus | None = None,
        exclude_archived: bool = True,
    ) -> list[NodeRecord]:
        """Search nodes with optional type/status filters."""
        clauses = ["project_id = %s"]
        params: list[object] = [project_id]

        if node_type is not None:
            clauses.append("type = %s")
            params.append(node_type.value)
        if status is not None:
            clauses.append("status = %s")
            params.append(status.value)
        elif exclude_archived:
            clauses.append("status != %s")
            params.append(NodeStatus.ARCHIVED.value)

        where = " AND ".join(clauses)
        rows = self._dict_fetchall(
            f"SELECT id, project_id, type, status, title, body_md, "  # noqa: S608
            f"file_path, content_hash, tags, confidence, "
            f"created_by, created_at, updated_at "
            f"FROM nodes WHERE {where} ORDER BY created_at",
            params,
        )
        return [_row_to_node(r) for r in rows]

    # ── Edge ───────────────────────────────────────────────────────────

    def insert_edge(self, edge: EdgeRecord) -> int:
        """Insert an edge. Returns the edge id.

        Uses ON CONFLICT DO NOTHING for idempotency.
        """
        row = self._conn.execute(
            """
            INSERT INTO edges
                (project_id, from_node_id, to_node_id, type, note, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (project_id, from_node_id, to_node_id, type)
                DO NOTHING
            RETURNING id
            """,
            (
                edge.project_id,
                edge.from_node_id,
                edge.to_node_id,
                edge.type.value,
                edge.note,
                edge.created_by,
            ),
        ).fetchone()

        if row is None:
            # Duplicate — fetch existing id
            existing = self._conn.execute(
                "SELECT id FROM edges WHERE project_id = %s "
                "AND from_node_id = %s AND to_node_id = %s AND type = %s",
                (edge.project_id, edge.from_node_id, edge.to_node_id, edge.type.value),
            ).fetchone()
            if existing is None:
                raise KgnError(
                    code=KgnErrorCode.INTERNAL_ERROR,
                    message="Duplicate edge not found after ON CONFLICT",
                )
            return existing[0]

        # Log activity
        if edge.created_by:
            self.log_activity(
                project_id=edge.project_id,
                agent_id=edge.created_by,
                activity_type=ActivityType.EDGE_CREATED,
                target_node_id=edge.from_node_id,
                message=f"Edge {edge.type.value}: {edge.from_node_id} → {edge.to_node_id}",
            )

        return row[0]

    def get_edges_from(self, node_id: uuid.UUID) -> list[EdgeRecord]:
        """Get all outgoing edges from a node."""
        rows = self._dict_fetchall(
            "SELECT id, project_id, from_node_id, to_node_id, type, note, "
            "created_by, created_at "
            "FROM edges WHERE from_node_id = %s ORDER BY created_at",
            (node_id,),
        )
        return [_row_to_edge(r) for r in rows]

    def get_edges_to(self, node_id: uuid.UUID) -> list[EdgeRecord]:
        """Get all incoming edges to a node."""
        rows = self._dict_fetchall(
            "SELECT id, project_id, from_node_id, to_node_id, type, note, "
            "created_by, created_at "
            "FROM edges WHERE to_node_id = %s ORDER BY created_at",
            (node_id,),
        )
        return [_row_to_edge(r) for r in rows]

    def search_edges(
        self,
        project_id: uuid.UUID,
    ) -> list[EdgeRecord]:
        """Return all edges in a project, ordered by created_at."""
        rows = self._dict_fetchall(
            "SELECT id, project_id, from_node_id, to_node_id, type, note, "
            "created_by, created_at "
            "FROM edges WHERE project_id = %s ORDER BY created_at",
            (project_id,),
        )
        return [_row_to_edge(r) for r in rows]

    # ── Subgraph ───────────────────────────────────────────────────────

    def extract_subgraph(
        self,
        root_id: uuid.UUID,
        project_id: uuid.UUID,
        depth: int = 2,
        edge_types: list[EdgeType] | None = None,
    ) -> list[SubgraphNode]:
        """BFS traversal from ``root_id`` up to ``depth`` hops.

        Returns a list of SubgraphNode with ``depth`` field indicating
        the hop distance from root.
        """
        visited: dict[uuid.UUID, int] = {root_id: 0}
        frontier: list[uuid.UUID] = [root_id]

        for current_depth in range(depth):
            if not frontier:
                break
            next_frontier: list[uuid.UUID] = []
            for nid in frontier:
                edges = self._get_adjacent_ids(nid, project_id, edge_types)
                for adj_id in edges:
                    if adj_id not in visited:
                        visited[adj_id] = current_depth + 1
                        next_frontier.append(adj_id)
            frontier = next_frontier

        # Fetch node details (batch)
        if not visited:
            return []

        nodes_map = self.get_nodes_by_ids(set(visited.keys()))
        result: list[SubgraphNode] = []
        for nid, d in visited.items():
            node = nodes_map.get(nid)
            if node is not None:
                result.append(
                    SubgraphNode(
                        id=node.id,
                        type=node.type.value,
                        status=node.status.value,
                        title=node.title,
                        body_md=node.body_md,
                        depth=d,
                        tags=node.tags or [],
                    )
                )
        return result

    def _get_adjacent_ids(
        self,
        node_id: uuid.UUID,
        project_id: uuid.UUID,
        edge_types: list[EdgeType] | None,
    ) -> list[uuid.UUID]:
        """Get IDs of adjacent nodes (both directions)."""
        type_clause = ""
        params: list[object] = [node_id, node_id, node_id, project_id]
        if edge_types:
            placeholders = ", ".join(["%s"] * len(edge_types))
            type_clause = f" AND e.type IN ({placeholders})"
            params.extend(et.value for et in edge_types)

        rows = self._conn.execute(
            f"SELECT CASE WHEN e.from_node_id = %s THEN e.to_node_id "  # noqa: S608
            f"ELSE e.from_node_id END AS adj_id "
            f"FROM edges e "
            f"WHERE (e.from_node_id = %s OR e.to_node_id = %s) "
            f"AND e.project_id = %s{type_clause}",
            params,
        ).fetchall()
        return [r[0] for r in rows]

    # ── Ingest log ─────────────────────────────────────────────────────

    def log_ingest(
        self,
        project_id: uuid.UUID,
        file_path: str,
        content_hash: str,
        status: str,
        *,
        error_detail: dict | None = None,
        ingested_by: uuid.UUID | None = None,
    ) -> None:
        """Record a file ingest attempt."""
        self._conn.execute(
            """
            INSERT INTO kgn_ingest_log
                (project_id, file_path, content_hash, status, error_detail, ingested_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                project_id,
                file_path,
                content_hash,
                status,
                json.dumps(error_detail) if error_detail else None,
                ingested_by,
            ),
        )

    # ── Activity log ───────────────────────────────────────────────────

    def log_activity(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        activity_type: ActivityType | str,
        target_node_id: uuid.UUID | None = None,
        message: str = "",
        task_queue_id: uuid.UUID | None = None,
        context_snapshot: dict | None = None,
    ) -> None:
        """Insert an append-only activity log entry."""
        at_value = activity_type.value if isinstance(activity_type, ActivityType) else activity_type
        snapshot = json.dumps(context_snapshot) if context_snapshot else "{}"
        self._conn.execute(
            """
            INSERT INTO agent_activities
                (project_id, agent_id, activity_type, target_node_id,
                 message, task_queue_id, context_snapshot)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                project_id,
                agent_id,
                at_value,
                target_node_id,
                message,
                task_queue_id,
                snapshot,
            ),
        )

    def get_task_activities(
        self,
        task_queue_id: uuid.UUID,
    ) -> list[dict]:
        """Return activity log entries for a specific task queue item.

        Results are ordered by ``created_at ASC``.
        """
        rows = self._conn.execute(
            """
            SELECT a.activity_type, a.message, a.created_at,
                   ag.agent_key
            FROM agent_activities a
            JOIN agents ag ON ag.id = a.agent_id
            WHERE a.task_queue_id = %s
            ORDER BY a.created_at ASC, a.id ASC
            """,
            (task_queue_id,),
        ).fetchall()
        return [
            {
                "activity_type": r[0],
                "message": r[1],
                "created_at": r[2],
                "agent_key": r[3],
            }
            for r in rows
        ]

    # ── Observability (Phase 10 / Step 6) ──────────────────────────────

    def get_agent_timeline(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        *,
        limit: int = 50,
    ) -> list[dict]:
        """Return activity timeline, optionally filtered by agent.

        Results ordered by ``created_at DESC`` (most recent first).
        """
        conditions = ["a.project_id = %s"]
        params: list[object] = [project_id]
        if agent_id is not None:
            conditions.append("a.agent_id = %s")
            params.append(agent_id)
        params.append(limit)
        where = " AND ".join(conditions)
        return self._dict_fetchall(
            f"SELECT a.id, a.agent_id, ag.agent_key, ag.role AS agent_role, "  # noqa: S608
            f"a.activity_type, a.target_node_id, a.message, "
            f"a.task_queue_id, a.created_at "
            f"FROM agent_activities a "
            f"JOIN agents ag ON ag.id = a.agent_id "
            f"WHERE {where} "
            f"ORDER BY a.created_at DESC, a.id DESC "
            f"LIMIT %s",
            params,
        )

    def get_agent_task_stats(self, project_id: uuid.UUID) -> list[dict]:
        """Return per-agent task statistics.

        For each agent: total tasks leased, DONE count, FAILED count,
        average duration (checkout→completion) in seconds.
        """
        return self._dict_fetchall(
            """
            SELECT
                ag.id              AS agent_id,
                ag.agent_key,
                ag.role,
                COUNT(tq.id)       AS total_tasks,
                COUNT(tq.id) FILTER (WHERE tq.state = 'DONE')   AS done_count,
                COUNT(tq.id) FILTER (WHERE tq.state = 'FAILED') AS failed_count,
                COALESCE(
                    EXTRACT(EPOCH FROM AVG(tq.updated_at - tq.created_at)
                        FILTER (WHERE tq.state = 'DONE')),
                    0
                ) AS avg_duration_sec
            FROM agents ag
            LEFT JOIN task_queue tq ON tq.leased_by = ag.id AND tq.project_id = ag.project_id
            WHERE ag.project_id = %s
            GROUP BY ag.id, ag.agent_key, ag.role
            ORDER BY ag.agent_key
            """,
            (project_id,),
        )

    def get_task_durations(self, project_id: uuid.UUID) -> list[dict]:
        """Return individual task durations for bottleneck analysis.

        Only includes DONE and FAILED tasks that have been leased.
        """
        return self._dict_fetchall(
            """
            SELECT
                tq.id              AS task_queue_id,
                tq.task_node_id,
                n.title            AS task_title,
                tq.state,
                tq.priority,
                ag.agent_key       AS leased_by_key,
                tq.created_at,
                tq.updated_at,
                EXTRACT(EPOCH FROM (tq.updated_at - tq.created_at)) AS duration_sec
            FROM task_queue tq
            JOIN nodes n ON n.id = tq.task_node_id
            LEFT JOIN agents ag ON ag.id = tq.leased_by
            WHERE tq.project_id = %s
              AND tq.state IN ('DONE', 'FAILED')
            ORDER BY duration_sec DESC
            """,
            (project_id,),
        )

    def get_project_activity_summary(self, project_id: uuid.UUID) -> list[dict]:
        """Return activity counts grouped by type for a project."""
        return self._dict_fetchall(
            """
            SELECT activity_type, COUNT(*) AS count
            FROM agent_activities
            WHERE project_id = %s
            GROUP BY activity_type
            ORDER BY count DESC
            """,
            (project_id,),
        )

    # ── Statistics ─────────────────────────────────────────────────────

    def count_nodes(self, project_id: uuid.UUID) -> dict[str, int]:
        """Count nodes per type for a project."""
        rows = self._conn.execute(
            "SELECT type::text, COUNT(*) FROM nodes WHERE project_id = %s GROUP BY type",
            (project_id,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def count_edges(self, project_id: uuid.UUID) -> dict[str, int]:
        """Count edges per type for a project."""
        rows = self._conn.execute(
            "SELECT type::text, COUNT(*) FROM edges WHERE project_id = %s GROUP BY type",
            (project_id,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def count_orphan_nodes(self, project_id: uuid.UUID) -> int:
        """Count nodes with no edges (neither outgoing nor incoming)."""
        row = self._conn.execute(
            """
            SELECT COUNT(*) FROM nodes n
            WHERE n.project_id = %s
              AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.from_node_id = n.id)
              AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.to_node_id = n.id)
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            raise KgnError(code=KgnErrorCode.INTERNAL_ERROR, message="COUNT query returned no rows")
        return row[0]

    def last_ingest_at(self, project_id: uuid.UUID) -> datetime | None:
        """Return the timestamp of the most recent ingest for a project."""
        row = self._conn.execute(
            "SELECT MAX(ingested_at) FROM kgn_ingest_log WHERE project_id = %s",
            (project_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return row[0]

    # ── Health metrics ─────────────────────────────────────────────────

    def count_active_nodes(self, project_id: uuid.UUID) -> int:
        """Count ACTIVE nodes for a project."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE project_id = %s AND status = %s",
            (project_id, NodeStatus.ACTIVE.value),
        ).fetchone()
        if row is None:
            raise KgnError(code=KgnErrorCode.INTERNAL_ERROR, message="COUNT query returned no rows")
        return row[0]

    def count_active_orphan_nodes(self, project_id: uuid.UUID) -> int:
        """Count ACTIVE nodes with no edges (neither direction)."""
        row = self._conn.execute(
            """
            SELECT COUNT(*) FROM nodes n
            WHERE n.project_id = %s
              AND n.status = %s
              AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.from_node_id = n.id)
              AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.to_node_id = n.id)
            """,
            (project_id, NodeStatus.ACTIVE.value),
        ).fetchone()
        if row is None:
            raise KgnError(code=KgnErrorCode.INTERNAL_ERROR, message="COUNT query returned no rows")
        return row[0]

    def count_contradicts_edges(self, project_id: uuid.UUID) -> int:
        """Count CONTRADICTS edges for a project."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM edges WHERE project_id = %s AND type = %s",
            (project_id, EdgeType.CONTRADICTS.value),
        ).fetchone()
        if row is None:
            raise KgnError(code=KgnErrorCode.INTERNAL_ERROR, message="COUNT query returned no rows")
        return row[0]

    def count_pending_contradicts(self, project_id: uuid.UUID) -> int:
        """Count CONTRADICTS edges with PENDING status for a project."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM edges "
            "WHERE project_id = %s AND type = 'CONTRADICTS' AND status = 'PENDING'",
            (project_id,),
        ).fetchone()
        if row is None:
            raise KgnError(code=KgnErrorCode.INTERNAL_ERROR, message="COUNT query returned no rows")
        return row[0]

    def count_spec_nodes(self, project_id: uuid.UUID) -> int:
        """Count SPEC-type nodes (all statuses except ARCHIVED) for a project."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM nodes "
            "WHERE project_id = %s AND type = 'SPEC' AND status != 'ARCHIVED'",
            (project_id,),
        ).fetchone()
        if row is None:
            raise KgnError(code=KgnErrorCode.INTERNAL_ERROR, message="COUNT query returned no rows")
        return row[0]

    def count_superseded_stale(self, project_id: uuid.UUID) -> int:
        """Count SUPERSEDED nodes that lack a SUPERSEDES edge pointing to them."""
        row = self._conn.execute(
            """
            SELECT COUNT(*) FROM nodes n
            WHERE n.project_id = %s
              AND n.status = %s
              AND NOT EXISTS (
                  SELECT 1 FROM edges e
                  WHERE e.type = %s AND e.to_node_id = n.id
              )
            """,
            (
                project_id,
                NodeStatus.SUPERSEDED.value,
                EdgeType.SUPERSEDES.value,
            ),
        ).fetchone()
        if row is None:
            raise KgnError(code=KgnErrorCode.INTERNAL_ERROR, message="COUNT query returned no rows")
        return row[0]

    def count_wip_tasks(self, project_id: uuid.UUID) -> int:
        """Count task_queue items currently IN_PROGRESS."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM task_queue WHERE project_id = %s AND state = 'IN_PROGRESS'",
            (project_id,),
        ).fetchone()
        if row is None:
            raise KgnError(code=KgnErrorCode.INTERNAL_ERROR, message="COUNT query returned no rows")
        return row[0]

    def count_open_assumptions(self, project_id: uuid.UUID) -> int:
        """Count ASSUMPTION-type ACTIVE nodes."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE project_id = %s AND type = %s AND status = %s",
            (project_id, NodeType.ASSUMPTION.value, NodeStatus.ACTIVE.value),
        ).fetchone()
        if row is None:
            raise KgnError(code=KgnErrorCode.INTERNAL_ERROR, message="COUNT query returned no rows")
        return row[0]

    def get_edges_for_subgraph(
        self,
        node_ids: set[uuid.UUID],
        project_id: uuid.UUID,
    ) -> list[EdgeRecord]:
        """Fetch all edges where both endpoints are in *node_ids*."""
        if not node_ids:
            return []
        placeholders = ", ".join(["%s"] * len(node_ids))
        id_list = list(node_ids)
        rows = self._dict_fetchall(
            f"SELECT id, project_id, from_node_id, to_node_id, type, note, "  # noqa: S608
            f"created_by, created_at "
            f"FROM edges "
            f"WHERE project_id = %s "
            f"AND from_node_id IN ({placeholders}) "
            f"AND to_node_id IN ({placeholders})",
            [project_id, *id_list, *id_list],
        )
        return [_row_to_edge(r) for r in rows]

    # ── Embedding ──────────────────────────────────────────────────────

    def upsert_embedding(
        self,
        node_id: uuid.UUID,
        project_id: uuid.UUID,
        embedding: list[float],
        model: str,
    ) -> None:
        """Insert or update an embedding vector for a node."""
        self._conn.execute(
            """
            INSERT INTO node_embeddings (node_id, project_id, embedding, model, updated_at)
            VALUES (%s, %s, %s::vector, %s, now())
            ON CONFLICT (node_id) DO UPDATE
            SET embedding  = EXCLUDED.embedding,
                model      = EXCLUDED.model,
                updated_at = now()
            """,
            (node_id, project_id, str(embedding), model),
        )

    def get_node_text_for_embedding(
        self,
        node_id: uuid.UUID,
    ) -> dict | None:
        """Return a single node's text data for embedding.

        Returns a dict with keys ``title``, ``body_md``, or ``None``
        if the node does not exist.
        """
        return self._dict_fetchone(
            "SELECT body_md, title FROM nodes WHERE id = %s",
            (node_id,),
        )

    def get_nodes_text_for_embedding(
        self,
        node_ids: list[uuid.UUID] | None = None,
        project_id: uuid.UUID | None = None,
        *,
        exclude_archived: bool = True,
    ) -> list[dict]:
        """Return text data for multiple nodes for embedding.

        When *node_ids* is given, fetch those specific nodes.
        When *project_id* is given (without *node_ids*), fetch all
        non-archived nodes in the project.

        Returns a list of dicts with keys: ``id``, ``title``, ``body_md``.
        """
        conditions: list[str] = []
        params: list = []

        if node_ids:
            placeholders = ", ".join(["%s"] * len(node_ids))
            conditions.append(f"id IN ({placeholders})")
            params.extend(node_ids)
        elif project_id is not None:
            conditions.append("project_id = %s")
            params.append(project_id)
        else:
            msg = "Either node_ids or project_id must be provided"
            raise ValueError(msg)

        if exclude_archived:
            conditions.append("status != 'ARCHIVED'")

        where = " AND ".join(conditions)
        return self._dict_fetchall(
            f"SELECT id, title, body_md FROM nodes WHERE {where} "  # noqa: S608
            f"ORDER BY created_at",
            params,
        )

    def get_unembedded_nodes(
        self,
        project_id: uuid.UUID,
    ) -> list[dict]:
        """Return nodes that have no entry in ``node_embeddings``.

        Returns a list of dicts with keys: ``id``, ``title``, ``body_md``.
        """
        return self._dict_fetchall(
            """
            SELECT n.id, n.title, n.body_md
            FROM nodes n
            LEFT JOIN node_embeddings ne ON ne.node_id = n.id
            WHERE n.project_id = %s
              AND n.status != 'ARCHIVED'
              AND ne.node_id IS NULL
            ORDER BY n.created_at
            """,
            (project_id,),
        )

    def has_embedding(self, node_id: uuid.UUID) -> bool:
        """Check whether a node has an embedding."""
        row = self._conn.execute(
            "SELECT 1 FROM node_embeddings WHERE node_id = %s",
            (node_id,),
        ).fetchone()
        return row is not None

    def get_node_embedding(self, node_id: uuid.UUID) -> list[float] | None:
        """Return the embedding vector for a node, or None."""
        row = self._conn.execute(
            "SELECT embedding::text FROM node_embeddings WHERE node_id = %s",
            (node_id,),
        ).fetchone()
        if row is None:
            return None
        # pgvector returns text like '[0.1,0.2,...]'
        text = row[0]
        return [float(x) for x in text.strip("[]").split(",")]

    def search_similar_nodes(
        self,
        query_embedding: list[float],
        project_id: uuid.UUID,
        *,
        top_k: int = 5,
        node_type: NodeType | None = None,
        exclude_archived: bool = True,
        exclude_ids: set[uuid.UUID] | None = None,
    ) -> list[SimilarNode]:
        """pgvector cosine-similarity Top-K search.

        Similarity is computed as ``1 - cosine_distance``.
        Results are sorted by descending similarity.
        """
        conditions = ["ne.project_id = %s"]
        params: list = [str(query_embedding), project_id]

        if exclude_archived:
            conditions.append("n.status != 'ARCHIVED'")

        if node_type is not None:
            conditions.append("n.type = %s")
            params.append(node_type.value)

        if exclude_ids:
            placeholders = ", ".join(["%s"] * len(exclude_ids))
            conditions.append(f"n.id NOT IN ({placeholders})")
            params.extend(list(exclude_ids))

        where_clause = " AND ".join(conditions)
        params.append(str(query_embedding))
        params.append(top_k)

        rows = self._dict_fetchall(
            f"SELECT "  # noqa: S608
            f"    n.id, n.type::text AS type, n.title, "
            f"    1 - (ne.embedding <=> %s::vector) AS similarity "
            f"FROM node_embeddings ne "
            f"JOIN nodes n ON n.id = ne.node_id "
            f"WHERE {where_clause} "
            f"ORDER BY ne.embedding <=> %s::vector "
            f"LIMIT %s",
            params,
        )
        return [
            SimilarNode(
                id=r["id"],
                type=r["type"],
                title=r["title"],
                similarity=float(r["similarity"]),
            )
            for r in rows
        ]

    # ── Conflict detection ─────────────────────────────────────────────

    def get_embedded_node_ids_by_types(
        self,
        project_id: uuid.UUID,
        node_types: list[NodeType],
    ) -> list[dict]:
        """Return embedded, non-ARCHIVED nodes of the given types.

        Returns a list of dicts with keys: ``id``, ``title``.
        """
        if not node_types:
            return []
        placeholders = ", ".join(["%s"] * len(node_types))
        params: list[object] = [project_id, *[nt.value for nt in node_types]]
        return self._dict_fetchall(
            f"SELECT n.id, n.title "  # noqa: S608
            f"FROM nodes n "
            f"JOIN node_embeddings ne ON ne.node_id = n.id "
            f"WHERE n.project_id = %s "
            f"  AND n.status != 'ARCHIVED' "
            f"  AND n.type IN ({placeholders}) "
            f"ORDER BY n.created_at",
            params,
        )

    def compute_cosine_similarity(
        self,
        node_a_id: uuid.UUID,
        node_b_id: uuid.UUID,
    ) -> float | None:
        """Compute cosine similarity between two node embeddings.

        Returns ``1 - cosine_distance``, or None if either node has no embedding.
        """
        row = self._conn.execute(
            """
            SELECT 1 - (a.embedding <=> b.embedding) AS similarity
            FROM node_embeddings a, node_embeddings b
            WHERE a.node_id = %s AND b.node_id = %s
            """,
            (node_a_id, node_b_id),
        ).fetchone()
        if row is None:
            return None
        return float(row[0])

    def find_contradicts_edge(
        self,
        project_id: uuid.UUID,
        node_a_id: uuid.UUID,
        node_b_id: uuid.UUID,
    ) -> dict | None:
        """Find existing CONTRADICTS edge between two nodes (either direction).

        Returns a dict with ``id``, ``from_node_id``, ``to_node_id``, ``status``
        or None.
        """
        row = self._dict_fetchone(
            """
            SELECT id, from_node_id, to_node_id, status::text AS status
            FROM edges
            WHERE project_id = %s
              AND type = 'CONTRADICTS'
              AND (
                  (from_node_id = %s AND to_node_id = %s)
                  OR (from_node_id = %s AND to_node_id = %s)
              )
            """,
            (project_id, node_a_id, node_b_id, node_b_id, node_a_id),
        )
        return row

    def insert_contradicts_edge(
        self,
        project_id: uuid.UUID,
        from_node_id: uuid.UUID,
        to_node_id: uuid.UUID,
        status: str,
        *,
        note: str = "",
        created_by: uuid.UUID | None = None,
    ) -> int:
        """Insert a CONTRADICTS edge with explicit status.

        Returns the edge id.
        """
        row = self._conn.execute(
            """
            INSERT INTO edges
                (project_id, from_node_id, to_node_id, type, note, created_by, status)
            VALUES (%s, %s, %s, 'CONTRADICTS', %s, %s, %s::edge_status)
            ON CONFLICT (project_id, from_node_id, to_node_id, type)
                DO NOTHING
            RETURNING id
            """,
            (project_id, from_node_id, to_node_id, note, created_by, status),
        ).fetchone()

        if row is None:
            existing = self._conn.execute(
                "SELECT id FROM edges WHERE project_id = %s "
                "AND from_node_id = %s AND to_node_id = %s AND type = 'CONTRADICTS'",
                (project_id, from_node_id, to_node_id),
            ).fetchone()
            if existing is None:
                raise KgnError(
                    code=KgnErrorCode.INTERNAL_ERROR,
                    message="Duplicate CONTRADICTS edge not found after ON CONFLICT",
                )
            return existing[0]
        return row[0]

    def update_edge_status(self, edge_id: int, status: str) -> None:
        """Update the status of an edge."""
        self._conn.execute(
            "UPDATE edges SET status = %s::edge_status WHERE id = %s",
            (status, edge_id),
        )

    def get_contradicts_edges(
        self,
        project_id: uuid.UUID,
        *,
        status_filter: str | None = None,
    ) -> list[dict]:
        """List CONTRADICTS edges, optionally filtered by status.

        Returns dicts with keys: ``id``, ``from_node_id``, ``to_node_id``,
        ``from_title``, ``to_title``, ``status``, ``note``.
        """
        conditions = ["e.project_id = %s", "e.type = 'CONTRADICTS'"]
        params: list[object] = [project_id]
        if status_filter:
            conditions.append("e.status = %s::edge_status")
            params.append(status_filter)
        where = " AND ".join(conditions)
        return self._dict_fetchall(
            f"SELECT e.id, e.from_node_id, e.to_node_id, "  # noqa: S608
            f"    nf.title AS from_title, nt.title AS to_title, "
            f"    e.status::text AS status, e.note "
            f"FROM edges e "
            f"JOIN nodes nf ON nf.id = e.from_node_id "
            f"JOIN nodes nt ON nt.id = e.to_node_id "
            f"WHERE {where} "
            f"ORDER BY e.created_at",
            params,
        )

    def get_edge_by_id(self, edge_id: int) -> dict | None:
        """Fetch a single edge by its numeric id."""
        return self._dict_fetchone(
            "SELECT id, project_id, from_node_id, to_node_id, "
            "type::text AS type, status::text AS status, note, "
            "created_by, created_at "
            "FROM edges WHERE id = %s",
            (edge_id,),
        )

    # ── Task Queue ─────────────────────────────────────────────────────

    def enqueue_task(
        self,
        project_id: uuid.UUID,
        task_node_id: uuid.UUID,
        *,
        priority: int = 100,
        state: str = "READY",
    ) -> uuid.UUID:
        """Insert a TASK node into task_queue.

        Validates that *task_node_id* refers to a TASK-type node belonging
        to *project_id*.  Raises ``ValueError`` otherwise.

        Args:
            state: Initial queue state — ``"READY"`` (default) or
                ``"BLOCKED"`` when dependencies are unmet.

        Returns the generated ``task_queue.id``.
        """
        row = self._conn.execute(
            "SELECT type::text FROM nodes WHERE id = %s AND project_id = %s",
            (task_node_id, project_id),
        ).fetchone()
        if row is None:
            msg = f"Node {task_node_id} not found in project {project_id}"
            raise ValueError(msg)
        if row[0] != NodeType.TASK:
            msg = f"Node {task_node_id} is type {row[0]}, expected TASK"
            raise ValueError(msg)

        result = self._conn.execute(
            "INSERT INTO task_queue (project_id, task_node_id, priority, state) "
            "VALUES (%s, %s, %s, %s::task_state) RETURNING id",
            (project_id, task_node_id, priority, state),
        ).fetchone()
        if result is None:
            raise KgnError(
                code=KgnErrorCode.INTERNAL_ERROR,
                message="INSERT task_queue RETURNING yielded no row",
            )
        return result[0]

    def checkout_task(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        *,
        lease_duration_sec: int = 600,
        role_filter: str | None = None,
    ) -> TaskQueueItem | None:
        """Consume the highest-priority READY task (FIFO within same priority).

        Uses ``FOR UPDATE SKIP LOCKED`` for concurrency safety.

        Args:
            project_id: The project scope.
            agent_id: The agent consuming the task.
            lease_duration_sec: Lease duration in seconds.
            role_filter: If provided, only checkout tasks whose node
                         tags contain ``role:<role_filter>``.

        Returns the consumed :class:`TaskQueueItem` or ``None`` if no READY
        tasks exist.

        State transition: READY → IN_PROGRESS, sets *leased_by* and
        *lease_expires_at*.
        """
        if role_filter is not None:
            # Join with nodes table to filter by role tag
            role_tag = f"role:{role_filter}"
            row = self._dict_fetchone(
                "UPDATE task_queue "
                "SET state            = 'IN_PROGRESS', "
                "    leased_by        = %s, "
                "    lease_expires_at = now() + make_interval(secs => %s), "
                "    attempts         = attempts + 1, "
                "    updated_at       = now() "
                "WHERE id = ( "
                "    SELECT tq.id FROM task_queue tq "
                "    JOIN nodes n ON n.id = tq.task_node_id "
                "    WHERE tq.project_id = %s "
                "      AND tq.state = 'READY' "
                "      AND %s = ANY(n.tags) "
                "    ORDER BY tq.priority ASC, tq.created_at ASC "
                "    LIMIT 1 "
                "    FOR UPDATE OF tq SKIP LOCKED "
                ") "
                "RETURNING *",
                (agent_id, lease_duration_sec, project_id, role_tag),
            )
        else:
            row = self._dict_fetchone(
                "UPDATE task_queue "
                "SET state            = 'IN_PROGRESS', "
                "    leased_by        = %s, "
                "    lease_expires_at = now() + make_interval(secs => %s), "
                "    attempts         = attempts + 1, "
                "    updated_at       = now() "
                "WHERE id = ( "
                "    SELECT id FROM task_queue "
                "    WHERE project_id = %s "
                "      AND state = 'READY' "
                "    ORDER BY priority ASC, created_at ASC "
                "    LIMIT 1 "
                "    FOR UPDATE SKIP LOCKED "
                ") "
                "RETURNING *",
                (agent_id, lease_duration_sec, project_id),
            )
        if row is None:
            return None
        return _row_to_task(row)

    def complete_task(self, task_id: uuid.UUID) -> None:
        """Mark a task as DONE.

        State transition: IN_PROGRESS → DONE.
        Raises ``ValueError`` if the task is not currently IN_PROGRESS.
        """
        row = self._conn.execute(
            "UPDATE task_queue "
            "SET state = 'DONE', updated_at = now() "
            "WHERE id = %s AND state = 'IN_PROGRESS' "
            "RETURNING id",
            (task_id,),
        ).fetchone()
        if row is None:
            msg = f"Task {task_id} is not IN_PROGRESS or does not exist"
            raise ValueError(msg)

    def fail_task(
        self,
        task_id: uuid.UUID,
        *,
        reason: str = "",
    ) -> None:
        """Mark a task as FAILED.

        State transition: IN_PROGRESS → FAILED (always).
        Increments ``attempts``.  Retry is only possible via
        :meth:`requeue_expired`.
        """
        row = self._conn.execute(
            "UPDATE task_queue "
            "SET state = 'FAILED', updated_at = now() "
            "WHERE id = %s AND state = 'IN_PROGRESS' "
            "RETURNING id",
            (task_id,),
        ).fetchone()
        if row is None:
            msg = f"Task {task_id} is not IN_PROGRESS or does not exist"
            raise ValueError(msg)

    def requeue_expired(self, project_id: uuid.UUID) -> int:
        """Requeue expired IN_PROGRESS tasks back to READY.

        Conditions: ``state = 'IN_PROGRESS'`` AND ``lease_expires_at < now()``
        AND ``attempts < max_attempts``.

        Returns the number of recovered tasks.
        """
        cur = self._conn.execute(
            "UPDATE task_queue "
            "SET state            = 'READY', "
            "    leased_by        = NULL, "
            "    lease_expires_at = NULL, "
            "    updated_at       = now() "
            "WHERE project_id = %s "
            "  AND state = 'IN_PROGRESS' "
            "  AND lease_expires_at < now() "
            "  AND attempts < max_attempts",
            (project_id,),
        )
        return cur.rowcount

    def get_task_status(self, task_id: uuid.UUID) -> TaskQueueItem | None:
        """Fetch the current state of a single task."""
        row = self._dict_fetchone(
            "SELECT * FROM task_queue WHERE id = %s",
            (task_id,),
        )
        if row is None:
            return None
        return _row_to_task(row)

    def list_tasks(
        self,
        project_id: uuid.UUID,
        *,
        state: str | None = None,
    ) -> list[TaskQueueItem]:
        """List tasks for a project, optionally filtered by state.

        Ordered by ``priority ASC, created_at ASC``.
        """
        conditions = ["project_id = %s"]
        params: list[object] = [project_id]
        if state is not None:
            conditions.append("state = %s::task_state")
            params.append(state)
        where = " AND ".join(conditions)
        rows = self._dict_fetchall(
            f"SELECT * FROM task_queue WHERE {where} "  # noqa: S608
            f"ORDER BY priority ASC, created_at ASC",
            params,
        )
        return [_row_to_task(r) for r in rows]

    def unblock_task(self, task_queue_id: uuid.UUID) -> bool:
        """Transition a single task from BLOCKED → READY.

        Returns ``True`` if the transition happened, ``False`` otherwise
        (e.g. task was not BLOCKED).
        """
        row = self._conn.execute(
            "UPDATE task_queue "
            "SET state = 'READY', updated_at = now() "
            "WHERE id = %s AND state = 'BLOCKED' "
            "RETURNING id",
            (task_queue_id,),
        ).fetchone()
        return row is not None

    def find_blocked_dependents(
        self,
        completed_node_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> list[TaskQueueItem]:
        """Find BLOCKED tasks that depend on *completed_node_id*.

        Looks for ``DEPENDS_ON`` edges where ``from_node_id`` points to a
        TASK node that has a BLOCKED entry in ``task_queue``.

        The edge direction convention is:
            from_node_id --DEPENDS_ON--> to_node_id
        i.e. the *from* node depends on *to* node.
        So we search for edges where ``to_node_id = completed_node_id``.
        """
        rows = self._dict_fetchall(
            "SELECT tq.* "
            "FROM edges e "
            "JOIN task_queue tq ON tq.task_node_id = e.from_node_id "
            "WHERE e.to_node_id = %s "
            "  AND e.type = 'DEPENDS_ON' "
            "  AND e.project_id = %s "
            "  AND tq.state = 'BLOCKED' "
            "ORDER BY tq.priority ASC, tq.created_at ASC",
            (completed_node_id, project_id),
        )
        return [_row_to_task(r) for r in rows]

    def find_ready_dependents(
        self,
        completed_node_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> list[TaskQueueItem]:
        """Find READY/IN_PROGRESS tasks that depend on *completed_node_id*.

        Same edge convention as :meth:`find_blocked_dependents`, but looks
        for tasks in READY or IN_PROGRESS state.
        """
        rows = self._dict_fetchall(
            "SELECT tq.* "
            "FROM edges e "
            "JOIN task_queue tq ON tq.task_node_id = e.from_node_id "
            "WHERE e.to_node_id = %s "
            "  AND e.type = 'DEPENDS_ON' "
            "  AND e.project_id = %s "
            "  AND tq.state IN ('READY', 'IN_PROGRESS') "
            "ORDER BY tq.priority ASC, tq.created_at ASC",
            (completed_node_id, project_id),
        )
        return [_row_to_task(r) for r in rows]

    def get_dependency_edges(
        self,
        task_node_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> list[EdgeRecord]:
        """Get outgoing DEPENDS_ON edges from a task node.

        Returns edges where ``from_node_id = task_node_id`` and
        ``type = 'DEPENDS_ON'``.
        """
        rows = self._dict_fetchall(
            "SELECT id, project_id, from_node_id, to_node_id, type, note, "
            "created_by, created_at "
            "FROM edges "
            "WHERE from_node_id = %s "
            "  AND type = 'DEPENDS_ON' "
            "  AND project_id = %s "
            "ORDER BY created_at",
            (task_node_id, project_id),
        )
        return [_row_to_edge(r) for r in rows]

    def get_task_by_node_id(
        self,
        task_node_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> TaskQueueItem | None:
        """Find a task_queue entry by its associated node ID."""
        row = self._dict_fetchone(
            "SELECT * FROM task_queue "
            "WHERE task_node_id = %s AND project_id = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (task_node_id, project_id),
        )
        if row is None:
            return None
        return _row_to_task(row)

    # ── Batch query helpers (R-014 / R-028) ────────────────────────

    def get_nodes_by_ids(
        self,
        node_ids: set[uuid.UUID],
    ) -> dict[uuid.UUID, NodeRecord]:
        """Fetch multiple nodes by their UUIDs in a single query.

        Returns a dict mapping ``node_id → NodeRecord``.
        """
        if not node_ids:
            return {}
        id_list = list(node_ids)
        placeholders = ", ".join(["%s"] * len(id_list))
        rows = self._dict_fetchall(
            f"SELECT id, project_id, type, status, title, body_md, "  # noqa: S608
            f"file_path, content_hash, tags, confidence, "
            f"created_by, created_at, updated_at "
            f"FROM nodes WHERE id IN ({placeholders})",
            tuple(id_list),
        )
        return {r["id"]: _row_to_node(r) for r in rows}

    def get_tasks_by_node_ids(
        self,
        node_ids: set[uuid.UUID],
        project_id: uuid.UUID,
    ) -> dict[uuid.UUID, TaskQueueItem]:
        """Fetch task_queue entries for multiple node IDs in one query.

        Returns a dict mapping ``task_node_id → TaskQueueItem`` (latest).
        """
        if not node_ids:
            return {}
        id_list = list(node_ids)
        placeholders = ", ".join(["%s"] * len(id_list))
        rows = self._dict_fetchall(
            f"SELECT DISTINCT ON (task_node_id) * "  # noqa: S608
            f"FROM task_queue "
            f"WHERE task_node_id IN ({placeholders}) AND project_id = %s "
            f"ORDER BY task_node_id, created_at DESC",
            (*id_list, project_id),
        )
        return {r["task_node_id"]: _row_to_task(r) for r in rows}

    def get_all_dependency_edges(
        self,
        project_id: uuid.UUID,
    ) -> list[EdgeRecord]:
        """Fetch ALL DEPENDS_ON edges for a project in one query.

        Used by ``_has_cycle()`` to avoid N+1 queries (R-015).
        """
        rows = self._dict_fetchall(
            "SELECT id, project_id, from_node_id, to_node_id, type, note, "
            "created_by, created_at "
            "FROM edges "
            "WHERE type = 'DEPENDS_ON' AND project_id = %s "
            "ORDER BY created_at",
            (project_id,),
        )
        return [_row_to_edge(r) for r in rows]

    # ── Conflict resolution (Phase 10 / Step 5) ──────────────────

    def get_latest_node_version(self, node_id: uuid.UUID) -> dict | None:
        """Get the most recent version entry for a node.

        Returns dict with ``version``, ``updated_by``, ``updated_at``,
        ``title``, ``body_md``, ``content_hash``, ``type``, ``status``,
        ``file_path``, ``tags``, ``confidence`` or None if no versions exist.
        """
        return self._dict_fetchone(
            "SELECT version, updated_by, updated_at, title, body_md, content_hash, "
            "type, status, file_path, tags, confidence "
            "FROM node_versions "
            "WHERE node_id = %s "
            "ORDER BY version DESC LIMIT 1",
            (node_id,),
        )

    def get_node_version(
        self,
        node_id: uuid.UUID,
        version: int,
    ) -> dict | None:
        """Get a specific version entry for a node."""
        return self._dict_fetchone(
            "SELECT version, updated_by, updated_at, title, body_md, content_hash, "
            "type, status, file_path, tags, confidence "
            "FROM node_versions "
            "WHERE node_id = %s AND version = %s",
            (node_id, version),
        )

    def get_node_version_count(self, node_id: uuid.UUID) -> int:
        """Return the number of version entries for a node."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM node_versions WHERE node_id = %s",
            (node_id,),
        ).fetchone()
        return row[0] if row else 0

    def update_node_status(
        self,
        node_id: uuid.UUID,
        status: str,
    ) -> bool:
        """Update only the status of a node. Returns True if updated."""
        row = self._conn.execute(
            "UPDATE nodes SET status = %s, updated_at = now() WHERE id = %s RETURNING id",
            (status, node_id),
        ).fetchone()
        return row is not None

    # ── Node locking (Phase 10 / Step 4) ───────────────────────────

    def acquire_node_lock(
        self,
        node_id: uuid.UUID,
        agent_id: uuid.UUID,
        duration_sec: int = 300,
    ) -> dict | None:
        """Attempt to acquire an advisory lock on a node.

        Succeeds if:
        - The node is currently unlocked (locked_by IS NULL)
        - The existing lock has expired (lock_expires_at < now())
        - The same agent already holds the lock (refresh)

        Returns the updated row dict on success, or None if another
        agent holds a non-expired lock.
        """
        row = self._dict_fetchone(
            "UPDATE nodes "
            "SET locked_by       = %s, "
            "    lock_expires_at = now() + make_interval(secs => %s) "
            "WHERE id = %s "
            "  AND (locked_by IS NULL "
            "       OR locked_by = %s "
            "       OR lock_expires_at < now()) "
            "RETURNING id, locked_by, lock_expires_at",
            (agent_id, duration_sec, node_id, agent_id),
        )
        return row

    def release_node_lock(
        self,
        node_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> bool:
        """Release a lock held by *agent_id*.

        Returns True if the lock was actually released.
        """
        row = self._conn.execute(
            "UPDATE nodes "
            "SET locked_by = NULL, lock_expires_at = NULL "
            "WHERE id = %s AND locked_by = %s "
            "RETURNING id",
            (node_id, agent_id),
        ).fetchone()
        return row is not None

    def release_all_node_locks(self, agent_id: uuid.UUID) -> int:
        """Release all locks held by an agent.

        Returns the number of locks released.
        """
        result = self._conn.execute(
            "UPDATE nodes "
            "SET locked_by = NULL, lock_expires_at = NULL "
            "WHERE locked_by = %s "
            "RETURNING id",
            (agent_id,),
        )
        return len(result.fetchall())

    def get_node_lock(self, node_id: uuid.UUID) -> dict | None:
        """Get lock information for a node.

        Returns a dict with ``locked_by``, ``lock_expires_at``,
        and ``is_expired`` flag, or None if unlocked.
        """
        row = self._dict_fetchone(
            "SELECT locked_by, lock_expires_at, "
            "       (lock_expires_at < now()) AS is_expired "
            "FROM nodes "
            "WHERE id = %s AND locked_by IS NOT NULL",
            (node_id,),
        )
        return row

    def cleanup_expired_locks(self) -> int:
        """Release all expired locks across all nodes.

        Returns the number of locks cleaned up.
        """
        result = self._conn.execute(
            "UPDATE nodes "
            "SET locked_by = NULL, lock_expires_at = NULL "
            "WHERE locked_by IS NOT NULL "
            "  AND lock_expires_at < now() "
            "RETURNING id",
        )
        return len(result.fetchall())


# ── Row mappers ────────────────────────────────────────────────────────


def _row_to_node(row: dict) -> NodeRecord:
    """Convert a dict_row to NodeRecord."""
    return NodeRecord(
        id=row["id"],
        project_id=row["project_id"],
        type=NodeType(row["type"]),
        status=NodeStatus(row["status"]),
        title=row["title"],
        body_md=row["body_md"],
        file_path=row.get("file_path"),
        content_hash=row.get("content_hash"),
        tags=row.get("tags") or [],
        confidence=float(row["confidence"]) if row.get("confidence") is not None else None,
        created_by=row.get("created_by"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _row_to_edge(row: dict) -> EdgeRecord:
    """Convert a dict_row to EdgeRecord."""
    return EdgeRecord(
        id=row["id"],
        project_id=row["project_id"],
        from_node_id=row["from_node_id"],
        to_node_id=row["to_node_id"],
        type=EdgeType(row["type"]),
        note=row.get("note", ""),
        created_by=row.get("created_by"),
        created_at=row.get("created_at"),
    )


def _row_to_task(row: dict) -> TaskQueueItem:
    """Convert a dict_row to TaskQueueItem."""
    return TaskQueueItem(
        id=row["id"],
        project_id=row["project_id"],
        task_node_id=row["task_node_id"],
        priority=row["priority"],
        state=str(row["state"]),
        leased_by=row.get("leased_by"),
        lease_expires_at=row.get("lease_expires_at"),
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
