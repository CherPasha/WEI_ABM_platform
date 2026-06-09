-- Migration: add keyword_scan_result column to projects table
-- Run this in Supabase dashboard → SQL Editor

ALTER TABLE projects ADD COLUMN IF NOT EXISTS keyword_scan_result bytea;
