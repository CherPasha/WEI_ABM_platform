-- Migration: add keyword_scanned_at column to companies table
-- Required for keyword scan resume/checkpoint support.
-- Run this in Supabase dashboard → SQL Editor

ALTER TABLE companies ADD COLUMN IF NOT EXISTS keyword_scanned_at TIMESTAMPTZ;
