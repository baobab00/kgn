-- ============================================================
-- 003_init_indexes.sql
-- Index creation (Phase 1 scope)
-- ============================================================

-- Node indexes
CREATE INDEX IF NOT EXISTS idx_nodes_project_type
    ON nodes(project_id, type);

CREATE INDEX IF NOT EXISTS idx_nodes_project_status
    ON nodes(project_id, status);

CREATE INDEX IF NOT EXISTS idx_nodes_content_hash
    ON nodes(project_id, content_hash)
    WHERE content_hash IS NOT NULL;

-- Edge indexes
CREATE INDEX IF NOT EXISTS idx_edges_from
    ON edges(project_id, from_node_id);

CREATE INDEX IF NOT EXISTS idx_edges_to
    ON edges(project_id, to_node_id);

CREATE INDEX IF NOT EXISTS idx_edges_type
    ON edges(project_id, type);

-- Activity log indexes
CREATE INDEX IF NOT EXISTS idx_activities_project_time
    ON agent_activities(project_id, created_at);

CREATE INDEX IF NOT EXISTS idx_activities_node
    ON agent_activities(target_node_id, created_at)
    WHERE target_node_id IS NOT NULL;
