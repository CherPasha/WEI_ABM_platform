-- Anti-keywords feature: add is_anti flag to keyword_groups
-- Existing rows become is_anti = false (the column default).
ALTER TABLE keyword_groups ADD COLUMN is_anti BOOLEAN NOT NULL DEFAULT false;
