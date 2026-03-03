-- ============================================================
-- 001_init_enums.sql
-- Extensions + ENUM type definitions
-- ============================================================

-- UUID generation
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- ENUM types
-- ============================================================

-- Node types
DO $$ BEGIN
    CREATE TYPE node_type AS ENUM (
        'GOAL', 'ARCH', 'SPEC', 'LOGIC', 'DECISION',
        'ISSUE', 'TASK', 'CONSTRAINT', 'ASSUMPTION', 'SUMMARY'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Node statuses
DO $$ BEGIN
    CREATE TYPE node_status AS ENUM (
        'ACTIVE', 'DEPRECATED', 'SUPERSEDED', 'ARCHIVED'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Edge types
DO $$ BEGIN
    CREATE TYPE edge_type AS ENUM (
        'DEPENDS_ON', 'IMPLEMENTS', 'RESOLVES', 'SUPERSEDES',
        'DERIVED_FROM', 'CONTRADICTS', 'CONSTRAINED_BY'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Agent activity types
DO $$ BEGIN
    CREATE TYPE activity_type AS ENUM (
        'NODE_CREATED', 'NODE_UPDATED', 'NODE_STATUS_CHANGED',
        'EDGE_CREATED', 'CONTEXT_ASSEMBLED', 'TASK_CHECKOUT',
        'TASK_COMPLETED', 'TASK_FAILED', 'KGN_INGESTED'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
