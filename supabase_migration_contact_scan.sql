-- Migration: Add contact_scans table and related columns
-- Execute in Supabase SQL Editor for existing databases

-- 1. Add settings columns to projects
ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS contact_scan_use_roles    BOOLEAN NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS contact_scan_keyword_only BOOLEAN NOT NULL DEFAULT false;

-- 2. Create contact_scans table
CREATE TABLE IF NOT EXISTS contact_scans (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status              TEXT        NOT NULL DEFAULT 'running',
    use_roles           BOOLEAN     NOT NULL,
    keyword_only        BOOLEAN     NOT NULL,
    total_companies     INTEGER     NOT NULL DEFAULT 0,
    hunter_done         INTEGER     NOT NULL DEFAULT 0,
    enrichment_done     INTEGER     NOT NULL DEFAULT 0,
    total_verification  INTEGER     NOT NULL DEFAULT 0,
    verification_done   INTEGER     NOT NULL DEFAULT 0,
    contacts_added      INTEGER     NOT NULL DEFAULT 0,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_contact_scans_project ON contact_scans(project_id);

-- 3. Add contact_scan_id to contacts
ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS contact_scan_id UUID REFERENCES contact_scans(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_contacts_scan ON contacts(contact_scan_id);

-- 4. Add keyword hit columns to companies
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS keyword_hit_count   INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS keyword_group_count INTEGER NOT NULL DEFAULT 0;
