# Tab Progression Bar — Design Spec

**Date:** 2026-06-09  
**Status:** Approved

---

## Overview

Redesign the project page tab navigation into a numbered 5-step progression bar. Tab colors reflect the real backend state on page load and update live as actions complete. All download buttons are consolidated into a new Export tab (step 5).

---

## Tab Order & Numbers

| # | Tab | Old name |
|---|-----|----------|
| 1 | Upload | Upload |
| 2 | Stop Words | Stop Words (was step 3) |
| 3 | Keywords | Keywords (was step 2) |
| 4 | Roles | Roles |
| 5 | Export | _(new)_ |

---

## Visual Structure

The tab nav is a horizontal stepper with numbered circles and arrow separators:

```
① Upload  ›  ② Stop Words  ›  ③ Keywords  ›  ④ Roles  ›  ⑤ Export
```

- Each tab is a `<label>` wrapping a `<span class="step-num">N</span>` circle and a text label.
- Arrow separators are `<span class="step-arrow">›</span>` — decorative, not interactive.
- The active tab shows a filled primary-color circle.
- State colors replace the old bottom-border underline:
  - Default: gray circle, muted text
  - `.tab-yellow`: amber circle background, dark amber text
  - `.tab-green`: green circle background, dark green text
- No bottom border on the tab bar.

---

## State Logic

`refreshTabStates()` runs on page load and after each significant action. It makes parallel API calls and applies CSS classes to the `<label>` elements.

### Tab 1 — Upload
- **Yellow**: at least one session exists for the project
- **Green**: at least one session has `status === "completed"`

### Tab 2 — Stop Words
- **Yellow**: `GET /api/projects/{id}/stop-words` returns ≥ 1 word
- **Green**: _(no scan — stays yellow once words are added)_

### Tab 3 — Keywords
- **Yellow**: at least one keyword group with at least one keyword exists
- **Green**: `GET /api/projects/{id}/keyword-scan/status` returns `has_result: true`

### Tab 4 — Roles
- **Yellow**: `target_roles` array has ≥ 1 role
- **Green**: latest contact scan has `status === "completed"`

### Tab 5 — Export
- Always accessible, no color state.

`refreshTabStates()` is triggered after: page load, upload complete, keyword scan complete, contact scan complete, stop words added/removed, keywords added/removed, roles added/removed.

---

## Export Tab Content

Download buttons shown for all available exports. Unavailable ones are rendered grayed-out (not hidden) with a short explanation label.

| Button | Enabled when |
|--------|-------------|
| Download Postings (.xlsx) | At least one completed session exists (links to most recent) |
| Download News (.xlsx) | Same condition as Postings |
| Download Keyword Analysis (.xlsx) | `keyword_scan_result` is saved in DB |
| Download Contacts (.xlsx) | Latest contact scan is `completed` |

---

## Backend Changes

### 1. DB Migration
Add column to `projects` table:
```sql
ALTER TABLE projects ADD COLUMN keyword_scan_result bytea;
```

### 2. Keyword Scan — Save to DB
After `generate_keyword_xlsx()` completes in `_run_scan_task`, write the resulting bytes to `projects.keyword_scan_result` in Supabase. Continue to keep the in-memory job result for the immediate status polling during the current scan.

### 3. New Endpoint: `GET /api/projects/{project_id}/keyword-scan/status`
Returns `{"has_result": true}` or `{"has_result": false}` based on whether `keyword_scan_result` is non-null in the DB. Used by `refreshTabStates()` and the Export tab to know if a download is available.

### 4. New Endpoint: `GET /api/projects/{project_id}/keyword-scan/download`
Reads `keyword_scan_result` from DB and streams as an XLSX file. Returns 404 if no result saved yet. Used by the Export tab download button.

### 5. Keywords Tab — Button Change
"Scan Postings & Download XLSX" → "Run Keyword Scan"  
Clicking starts the scan (as today) but does not auto-download. After completion, `refreshTabStates()` is called to update tab 3 to green and enable the Export tab download button.

### 6. Download Buttons — Move to Export
Remove from their current locations:
- Upload panel: "Download Postings (.xlsx)" and "Download News (.xlsx)"
- Roles panel: "Download Contacts (.xlsx)"

These three buttons (plus the new keyword download) all live in the Export tab.

---

## Files Changed

- `app/templates/project.html` — all frontend changes
- `app/main.py` — 2 new endpoints, save scan result to DB in `_run_scan_task`
- DB migration SQL — `ALTER TABLE projects ADD COLUMN keyword_scan_result bytea`
