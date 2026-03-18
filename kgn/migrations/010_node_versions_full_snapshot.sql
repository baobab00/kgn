-- ============================================================
-- Migration 010: Extend node_versions for full snapshot
-- Phase 12 / Step 1: Add type, status, file_path, tags,
-- confidence columns so _save_version() can capture the
-- complete node state before UPDATE.
-- ============================================================

ALTER TABLE node_versions ADD COLUMN IF NOT EXISTS type       VARCHAR(50);
ALTER TABLE node_versions ADD COLUMN IF NOT EXISTS status     VARCHAR(50);
ALTER TABLE node_versions ADD COLUMN IF NOT EXISTS file_path  TEXT;
ALTER TABLE node_versions ADD COLUMN IF NOT EXISTS tags       TEXT[] DEFAULT '{}';
ALTER TABLE node_versions ADD COLUMN IF NOT EXISTS confidence REAL;
