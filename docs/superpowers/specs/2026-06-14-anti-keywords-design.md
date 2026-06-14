# Anti-Keywords Feature Design

**Date:** 2026-06-14
**Status:** Approved

## Overview

Add a second keyword category — anti-keywords — that is scanned identically to regular keywords but tracked separately. Anti-keywords signal negative fit; the analyst interprets the scores. The UI gets a new tab, the scanner runs both types in one job, and the XLSX output gains mirrored anti-keyword columns on Quick_Summary plus two new sheets (Anti_Summary, Anti_Details).

---

## Database

Single migration — no new tables:

```sql
ALTER TABLE keyword_groups ADD COLUMN is_anti BOOLEAN NOT NULL DEFAULT false;
```

All existing rows become `is_anti = false`. Anti-keyword groups are inserted with `is_anti = true`. The `keywords` table is unchanged — it links to `keyword_groups` by `group_id` regardless of type.

---

## UI — New Tab

A new tab **"Анти ключевые слова"** is inserted between Keywords and Roles. Tab step numbers shift:

| Step | Tab |
|------|-----|
| 1 | Upload |
| 2 | Stop Words |
| 3 | Keywords |
| 4 | Анти ключевые слова *(new)* |
| 5 | Roles |
| 6 | Export |

The panel is a direct copy of `#panel-keywords` layout:
- Same "Add Group" input + button
- Same "Import from file" button (calls `/anti-keyword-groups/import`)
- Same group accordion with keyword tags and add/delete controls

**No "Run Keyword Scan" button on this tab.** The scan is triggered from the Keywords tab only; the anti-keyword scan runs automatically as part of the same job.

JS for the anti-keywords tab is a parallel copy of the keywords tab JS (`loadKeywordGroups` → `loadAntiKeywordGroups`, `addKeywordGroup` → `addAntiKeywordGroup`, etc.) pointing at `/anti-keyword-groups/` endpoints.

CSS: add `#tab-antikeywords:checked ~ #panel-antikeywords { display: block; }` and the corresponding active-state selectors alongside the existing tab rules.

---

## API Endpoints

New endpoints alongside existing keyword group routes in `app/main.py`. All internally filter/insert with `is_anti=True`:

| Method | Path | Action |
|--------|------|--------|
| `GET` | `/api/projects/{id}/anti-keyword-groups` | List all anti-groups with their keywords |
| `POST` | `/api/projects/{id}/anti-keyword-groups` | Create new anti-group |
| `DELETE` | `/api/projects/{id}/anti-keyword-groups/{group_id}` | Delete anti-group |
| `POST` | `/api/projects/{id}/anti-keyword-groups/{group_id}/keywords` | Add keyword to anti-group |
| `DELETE` | `/api/projects/{id}/anti-keyword-groups/{group_id}/keywords/{keyword_id}` | Delete keyword from anti-group |
| `POST` | `/api/projects/{id}/anti-keyword-groups/import` | Bulk import from xlsx |

No new service files — all added to `app/main.py`. The import endpoint reuses `parse_keyword_xlsx` from `app/services/keyword_parser.py`.

**Existing keyword group endpoints must be updated** to add `is_anti=False` to their Supabase queries. Without this filter, `GET /keyword-groups` would return anti-groups after the migration, polluting the Keywords tab.

---

## Scanner Changes (`app/services/keyword_scanner.py`)

`scan_project_keywords()` fetches anti-keyword groups separately (filtered by `is_anti=True`) and runs the identical matching loop against postings and news.

### Return structure

```python
{
    "groups": [...],         # regular keyword groups (is_anti=False)
    "anti_groups": [...],    # anti-keyword groups (is_anti=True)
    "companies": [
        {
            "name": str,
            "inn": str,
            "results": {kw: {"count": int, "sentences": [...]}},       # regular
            "anti_results": {kw: {"count": int, "sentences": [...]}},  # anti
        }
    ]
}
```

### Behaviour rules

- Stop-word filtering applies to both passes identically.
- `ValueError("No keyword groups defined")` is raised only if **regular** keyword groups are missing. Anti-keyword groups are optional — scan proceeds with empty `anti_results` if none are defined.
- `keyword_hit_count` / `keyword_group_count` written back to the `companies` table track **regular keywords only** and are not changed by this feature. Anti-keyword hits are output-only.

---

## XLSX Output (`generate_keyword_xlsx`)

Sheet order: **Quick_Summary → Summary → Details → Anti_Summary → Anti_Details**

### Quick_Summary columns

| # | Column |
|---|--------|
| 1 | Company |
| 2 | INN |
| 3 | Unique Keywords Found |
| 4 | Total Keyword Matches |
| 5 | Groups With Hits |
| 6 | Keywords Found |
| 7 | Anti Unique Keywords Found |
| 8 | Anti Total Keyword Matches |
| 9 | Anti Groups With Hits |
| 10 | Anti Keywords Found |
| 11… | `{Group Name}` (one col per regular group) |
| n… | `Anti: {Group Name}` (one col per anti-group) |

### Summary — unchanged

Existing format: Company, INN, per-keyword hit counts, per-group totals. Regular keywords only.

### Details — unchanged

Existing format: Company, INN, Keyword Group, Keyword, Total Matches, From Postings, From News, Sentences. Regular keywords only.

### Anti_Summary

Same structure as Summary but sourced from `anti_results` and anti-groups.

### Anti_Details

Same structure as Details but sourced from `anti_results`. "Keyword Group" and "Keyword" columns refer to anti-groups.

---

## `download-with-contacts` Endpoint

`GET /api/projects/{id}/keyword-scan/download-with-contacts` currently:
1. Loads the stored XLSX from `projects.keyword_scan_result`
2. Parses the Summary sheet via `derive_quick_summary_df` to reconstruct per-company stats
3. Builds a new Quick_Summary with a Contacts Found column inserted
4. Returns a 4-sheet XLSX: Quick_Summary → Summary → Details → Contacts

After this change the stored XLSX has 5 sheets and Quick_Summary includes anti-keyword columns. The endpoint must be updated to:
1. Parse both **Summary** and **Anti_Summary** sheets
2. Merge anti-keyword stats into the rebuilt Quick_Summary (preserving anti-keyword columns and per-group cols)
3. Return a 6-sheet XLSX: Quick_Summary → Summary → Details → Anti_Summary → Anti_Details → Contacts

`derive_quick_summary_df` must be extended to accept an optional `anti_summary_df` argument and produce the full merged column set when provided.

---

## Files Changed

| File | Change |
|------|--------|
| `supabase_schema.sql` | Add `is_anti` column to `keyword_groups` definition |
| `supabase_migration_anti_keywords.sql` | New migration file with `ALTER TABLE` statement |
| `app/main.py` | 6 new anti-keyword endpoints; existing keyword group endpoints gain `is_anti=False` filter on list/create; update `download-with-contacts` endpoint |
| `app/services/keyword_scanner.py` | Fetch anti-groups; run anti-keyword scan loop; update return dict; update `generate_keyword_xlsx` for 5 sheets; extend `derive_quick_summary_df` |
| `app/templates/project.html` | New tab radio/panel; step numbers updated; parallel JS functions for anti-keywords tab |
