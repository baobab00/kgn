-- ============================================================
-- 005_edge_status.sql
-- Add status column to edges table (Phase 2 — conflict detection)
-- ============================================================

-- edge_status enum: PENDING → new CONTRADICTS, APPROVED → confirmed, DISMISSED → ignored
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'edge_status') THEN
        CREATE TYPE edge_status AS ENUM ('PENDING', 'APPROVED', 'DISMISSED');
    END IF;
END$$;

-- Add status column (existing edges retroactively set to APPROVED)
ALTER TABLE edges
    ADD COLUMN IF NOT EXISTS status edge_status NOT NULL DEFAULT 'APPROVED';

-- We would like CONTRADICTS edges to default to PENDING,
-- but only one column default is possible, so the service/repository controls this at INSERT time.
-- All existing edges become APPROVED.
