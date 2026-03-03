-- ============================================================
-- 006_task_queue.sql
-- Phase 3: Task queue schema
-- ============================================================

-- task_state ENUM
-- NOTE: BLOCKED state will be activated in Phase 6 / Step 3 (dependency chain).
--       Currently unused — enum value only declared.
DO $$ BEGIN
    CREATE TYPE task_state AS ENUM (
        'READY', 'IN_PROGRESS', 'BLOCKED', 'DONE', 'FAILED'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- task_queue table
CREATE TABLE IF NOT EXISTS task_queue (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       uuid NOT NULL REFERENCES projects(id),
    task_node_id     uuid NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    priority         int  NOT NULL DEFAULT 100,
    state            task_state NOT NULL DEFAULT 'READY',
    leased_by        uuid REFERENCES agents(id),
    lease_expires_at timestamptz,
    attempts         int NOT NULL DEFAULT 0,
    max_attempts     int NOT NULL DEFAULT 3,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Optimized READY task lookup (partial index)
CREATE INDEX IF NOT EXISTS idx_taskq_ready
    ON task_queue(project_id, state, priority, created_at)
    WHERE state = 'READY';

-- Add task_queue_id FK to agent_activities
ALTER TABLE agent_activities
    ADD COLUMN IF NOT EXISTS task_queue_id uuid REFERENCES task_queue(id);
