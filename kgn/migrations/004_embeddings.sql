-- ============================================================
-- 004_embeddings.sql
-- Node embedding table + HNSW index (Phase 2)
-- ============================================================

-- pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- Node embeddings
-- ⚠️ Dimension must be updated and index rebuilt if model changes
-- Current: text-embedding-3-small (1536 dimensions)
-- ============================================================
CREATE TABLE IF NOT EXISTS node_embeddings (
    node_id    uuid PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
    project_id uuid NOT NULL REFERENCES projects(id),
    embedding  vector(1536),        -- text-embedding-3-small dimensions
    model      text NOT NULL,       -- Model name used
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- HNSW index (cosine similarity)
CREATE INDEX IF NOT EXISTS node_embeddings_hnsw
    ON node_embeddings USING hnsw (embedding vector_cosine_ops);
