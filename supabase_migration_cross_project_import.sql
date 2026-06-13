-- Migration: Add cross-project session import columns
-- Execute in Supabase SQL Editor for existing databases

-- 1. Add import fields to sessions
ALTER TABLE sessions
  ADD COLUMN IF NOT EXISTS type                    TEXT DEFAULT 'normal',
  ADD COLUMN IF NOT EXISTS source_session_id       UUID REFERENCES sessions(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS source_project_name     TEXT,
  ADD COLUMN IF NOT EXISTS source_session_filename TEXT;

-- 2. Add source reference to companies
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS source_company_id UUID REFERENCES companies(id) ON DELETE SET NULL;
