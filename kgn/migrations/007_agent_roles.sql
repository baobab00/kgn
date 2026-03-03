-- ============================================================
-- Migration 007: Agent Roles
-- Convert agents.role from free text to agent_role enum type.
-- Backward-compatible: existing 'worker' values map to 'worker'.
-- ============================================================

-- 1. Create agent_role enum type
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'agent_role') THEN
        CREATE TYPE agent_role AS ENUM (
            'genesis',
            'worker',
            'reviewer',
            'indexer',
            'admin'
        );
    END IF;
END
$$;

-- 2. Convert existing role column from text to enum
-- Step A: Add temporary column with the new enum type
ALTER TABLE agents ADD COLUMN IF NOT EXISTS role_enum agent_role;

-- Step B: Migrate existing data (map known values, default others to 'admin')
UPDATE agents SET role_enum = CASE
    WHEN role IN ('genesis', 'worker', 'reviewer', 'indexer', 'admin') THEN role::agent_role
    ELSE 'admin'::agent_role
END
WHERE role_enum IS NULL;

-- Step C: Drop old text column (only if role_enum was successfully populated)
-- We use a DO block to make this idempotent
DO $$
BEGIN
    -- Check if old text column still exists and new column has data
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agents' AND column_name = 'role'
        AND data_type = 'text'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agents' AND column_name = 'role_enum'
    ) THEN
        ALTER TABLE agents DROP COLUMN role;
        ALTER TABLE agents RENAME COLUMN role_enum TO role;
        ALTER TABLE agents ALTER COLUMN role SET NOT NULL;
        ALTER TABLE agents ALTER COLUMN role SET DEFAULT 'admin'::agent_role;
    END IF;
END
$$;
