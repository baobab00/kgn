-- ============================================================
-- Migration 009: Conflict resolution activity types
-- Phase 10 / Step 5: Add CONFLICT_DETECTED and CONFLICT_RESOLVED
-- to the activity_type enum for concurrent edit tracking.
-- ============================================================

ALTER TYPE activity_type ADD VALUE IF NOT EXISTS 'CONFLICT_DETECTED';
ALTER TYPE activity_type ADD VALUE IF NOT EXISTS 'CONFLICT_RESOLVED';
