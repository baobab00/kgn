-- ============================================================
-- 002_init_tables.sql
-- Table creation (Phase 1 scope)
-- ============================================================

-- ============================================================
-- Projects
-- ============================================================
CREATE TABLE IF NOT EXISTS projects (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL UNIQUE,
    description text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ============================================================
-- Agents (normalized)
-- ============================================================
CREATE TABLE IF NOT EXISTS agents (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES projects(id),
    agent_key   text NOT NULL,
    role        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, agent_key)
);

-- ============================================================
-- Nodes
-- ============================================================
CREATE TABLE IF NOT EXISTS nodes (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id         uuid NOT NULL REFERENCES projects(id),
    type               node_type NOT NULL,
    status             node_status NOT NULL DEFAULT 'ACTIVE',
    title              text NOT NULL,
    body_md            text NOT NULL DEFAULT '',

    -- File association
    file_path          text,
    content_hash       text,

    -- Meta
    tags               text[] NOT NULL DEFAULT '{}',
    confidence         numeric(4,3)
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),

    -- Audit
    created_by         uuid REFERENCES agents(id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- ============================================================
-- Node version history
-- ============================================================
CREATE TABLE IF NOT EXISTS node_versions (
    id          bigserial PRIMARY KEY,
    node_id     uuid NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    version     int  NOT NULL,
    title       text NOT NULL,
    body_md     text NOT NULL,
    content_hash text,
    updated_by  uuid REFERENCES agents(id),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (node_id, version)
);

-- ============================================================
-- Edges
-- ============================================================
CREATE TABLE IF NOT EXISTS edges (
    id           bigserial PRIMARY KEY,
    project_id   uuid NOT NULL REFERENCES projects(id),
    from_node_id uuid NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    to_node_id   uuid NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    type         edge_type NOT NULL,
    note         text NOT NULL DEFAULT '',
    created_by   uuid REFERENCES agents(id),
    created_at   timestamptz NOT NULL DEFAULT now(),

    UNIQUE (project_id, from_node_id, to_node_id, type)
);

-- ============================================================
-- KGN ingest log
-- ============================================================
CREATE TABLE IF NOT EXISTS kgn_ingest_log (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   uuid NOT NULL REFERENCES projects(id),
    file_path    text NOT NULL,
    content_hash text NOT NULL,
    ingested_by  uuid REFERENCES agents(id),
    status       text NOT NULL CHECK (status IN ('SUCCESS', 'FAILED', 'SKIPPED')),
    error_detail jsonb,
    ingested_at  timestamptz NOT NULL DEFAULT now()
);

-- ============================================================
-- Agent activity log (append-only)
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_activities (
    id                   bigserial PRIMARY KEY,
    project_id           uuid NOT NULL REFERENCES projects(id),
    agent_id             uuid NOT NULL REFERENCES agents(id),
    activity_type        activity_type NOT NULL,
    target_node_id       uuid REFERENCES nodes(id),
    message              text NOT NULL DEFAULT '',
    context_snapshot     jsonb NOT NULL DEFAULT '{}',
    created_at           timestamptz NOT NULL DEFAULT now()
);

-- ============================================================
-- Append-only trigger (block UPDATE/DELETE)
-- ============================================================
CREATE OR REPLACE FUNCTION prevent_activity_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'agent_activities is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_activities_immutable ON agent_activities;
CREATE TRIGGER trg_activities_immutable
    BEFORE UPDATE OR DELETE ON agent_activities
    FOR EACH ROW EXECUTE FUNCTION prevent_activity_mutation();
