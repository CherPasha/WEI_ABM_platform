# Cross-Project Session Import — Design Spec

**Date:** 2026-06-12
**Status:** Approved

---

## Overview

Allow a project to reference companies (with their postings, news, and contacts pipeline) from another project's session, without duplicating the heavy data (postings/news). Project 2 can run its own keyword scan, stop words, contact scan, and downloads against the referenced companies, independently of Project 1's keywords and roles.

---

## Data Model

### `sessions` table — new columns

| Column | Type | Default | Notes |
|---|---|---|---|
| `type` | TEXT | `'normal'` | `'normal'` or `'imported'` |
| `source_session_id` | UUID NULLABLE | `null` | FK → `sessions.id` SET NULL on delete |
| `source_project_name` | TEXT NULLABLE | `null` | Snapshot of source project name at import time |
| `source_session_filename` | TEXT NULLABLE | `null` | Snapshot of source filename at import time |

`source_session_id` uses SET NULL (not CASCADE) on delete. This means if the source session is deleted, the ghost session survives but `source_session_id` becomes `null` — the broken reference signal.

### `companies` table — new column

| Column | Type | Default | Notes |
|---|---|---|---|
| `source_company_id` | UUID NULLABLE | `null` | FK → `companies.id` SET NULL on delete |

Ghost companies hold only metadata (INN, legal_name, known_names, website_url, ceo_name, kpp, ogrn, revenue). The fields `keyword_scanned_at`, `keyword_hit_count`, and `keyword_group_count` are NOT copied — they reset fresh for Project 2.

When the source company is deleted (cascading from its session), `source_company_id` becomes `null`. Services treat this as "data unavailable for this company."

No other schema changes. Contacts, postings, news, keyword groups, and stop words are unchanged.

---

## Import Flow

### New API endpoint

**`POST /api/projects/{project_id}/sessions/import`**

Request body:
```json
{ "source_session_id": "<uuid>" }
```

Backend steps:
1. Validate source session exists, belongs to a different project, and has `status = 'completed'`.
2. Reject if this source session is already imported into this project (duplicate guard).
3. Snapshot `source_project_name` (from source project) and `source_session_filename` (from source session).
4. Insert imported session: `type='imported'`, `status='completed'`, `source_session_id`, `source_project_name`, `source_session_filename`, `total_companies = count of source companies`.
5. Batch-copy company metadata rows from source session into the new imported session, setting `source_company_id` on each row. Fields copied: `legal_name`, `inn`, `kpp`, `ogrn`, `website_url`, `ceo_name`, `revenue`, `known_names`. Fields NOT copied: `keyword_scanned_at`, `keyword_hit_count`, `keyword_group_count`.

### New helper endpoint

**`GET /api/projects/{project_id}/sessions/completed?importing_into={target_project_id}`**

Returns only `status='completed'` sessions for a given project. Excludes any sessions already imported into `importing_into` project (prevents duplicate import attempts). Used to populate the session picker in the import modal.

### UI — Upload tab

- Add an "Import from project" button alongside the existing file upload area.
- Opens a two-step modal:
  - **Step 1:** Dropdown of all other projects (from existing `GET /api/projects`).
  - **Step 2:** Dropdown of completed sessions in the selected project (from new endpoint above), defaulting to the most recent.
- On confirm: calls `POST /api/projects/{project_id}/sessions/import`, then refreshes the sessions list.

---

## Service Layer Changes

### keyword_scanner.py

After loading all companies for the project, split into two buckets:

- **Native companies** (`source_company_id` is null): fetch postings/news by `company.id` as today.
- **Ghost companies** (`source_company_id` is set): fetch postings/news by `source_company_id` instead.

Keyword scan results are attributed back to the ghost company's `legal_name` and `inn` (which are identical copies). INN deduplication, hit counts, and output rows remain consistent within Project 2.

If `source_company_id` is null on a ghost company (source deleted), that company is skipped during the scan and a warning is logged.

### contact_scanner.py

Ghost companies are included in the project's session list naturally — no structural change. The scanner creates contacts against the ghost `company_id`, making them fully owned by Project 2. Contacts survive even if the source session is later deleted.

Verify that the scanner does not skip sessions with `type='imported'`.

### Download endpoints (postings + news)

`GET /api/sessions/{session_id}/postings/download` and `.../news/download` currently query by `session_id`.

Change for imported sessions:
- If `type='imported'` and `source_session_id` is set → proxy query to `source_session_id`.
- If `type='imported'` and `source_session_id` is null (broken reference) → return HTTP 410 with message "Source session has been deleted."

### Project and session deletion guard

**`DELETE /api/projects/{project_id}`** and **`DELETE /api/sessions/{session_id}`**:

Before deleting, check whether any other session has `source_session_id` pointing into the sessions being deleted. If dependents are found, return HTTP 409 with a list of dependent project names. The frontend shows a confirmation modal (see Warning System section). A second confirmed delete request (`?force=true`) proceeds with deletion — no FK blocks, the broken reference state is handled gracefully.

**New endpoints:**

- `GET /api/sessions/{session_id}/dependents` — returns `[{ project_name, session_filename }]` for any sessions that import from this session.
- `GET /api/projects/{project_id}/dependents` — returns `[{ project_name, session_filename, source_session_filename }]` for any sessions across all projects that import from any session belonging to this project. Used before project-level deletion.

---

## Warning System

### Broken reference (Project 2's side)

The existing `GET /api/projects/{project_id}/sessions` response will include `type`, `source_session_id`, `source_project_name`, and `source_session_filename`.

A broken reference is detected when: `type = 'imported'` AND `source_session_id` is `null`.

Two UI locations:

1. **Project page banner** — shown if any imported session has a broken reference:
   > "One or more imported sessions have lost their source data. Keyword scans will skip those companies. Downloads from those sessions are unavailable."

2. **Sessions history table row** — the affected row shows a red "Source deleted" badge and the text *"[source_project_name] · [source_session_filename]"* (snapshots ensure name/filename display even after deletion).

### Deletion guard (Project 1's side)

When user initiates delete on a **session**: frontend calls `GET /api/sessions/{session_id}/dependents`.

When user initiates delete on a **project**: frontend calls `GET /api/projects/{project_id}/dependents`.

If either returns dependents, show a confirmation modal:
> "Project [Y] imports data from this session ([filename]). Deleting will break its imported session — those companies will be skipped in keyword scans and downloads will stop working. Are you sure?"

User must explicitly confirm. Delete proceeds with `?force=true`. Broken reference state is handled gracefully on Project 2's side.

---

## Sessions History Table — Imported Row Rendering

| Column | Imported session rendering |
|---|---|
| Filename / label | "Imported from [source_project_name]" |
| Status badge | Green "Reference active" / Red "Source deleted" |
| Companies count | Same count as source (set at import time) |
| Pipeline stage columns | Hidden; replaced by source label |
| Resume button | Hidden |
| Cancel button | Hidden |
| Download postings/news | Shown; disabled with tooltip if broken reference |
| Delete button | Shown — removes ghost session and ghost companies from Project 2 only; does not touch source |

---

## What Is NOT Imported

- Keyword groups and keywords from the source project
- Stop words from the source project
- Target roles from the source project
- Contacts from the source project's contact scans

All of the above are configured independently in Project 2.

---

## Edge Cases

- **Duplicate import prevention:** importing the same source session into the same project twice is rejected with HTTP 409.
- **Self-import prevention:** a project cannot import from itself.
- **Source session not completed:** only `status='completed'` sessions are eligible as import sources.
- **Ghost company with null source_company_id during keyword scan:** logged as a warning, skipped silently — does not abort the scan.
- **Contact download for ghost companies:** works normally since contacts are stored against ghost company_ids.
