-- ============================================================
-- Migration 008: Node locks (Concurrent Access Guard)
-- Phase 10 / Step 4: Advisory locking on nodes to prevent
-- simultaneous edits by multiple agents.
-- ============================================================

-- Add lock columns to nodes table
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS locked_by uuid REFERENCES agents(id);
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS lock_expires_at timestamptz;

-- Index for efficient expired-lock queries
CREATE INDEX IF NOT EXISTS idx_nodes_lock_expires
    ON nodes (lock_expires_at)
    WHERE locked_by IS NOT NULL;
