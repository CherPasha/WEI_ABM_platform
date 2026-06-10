# Keyword Scan Persistence & Progress Tracking — Design Spec

**Date:** 2026-06-11  
**Status:** Approved

---

## Problem

The keyword scan runs as a FastAPI `BackgroundTask` (a thread inside the Uvicorn process). Scan status is stored in `_scan_jobs`, an in-memory dict keyed by `job_id`. This creates three gaps:

1. **Status blindness after browser close** — `job_id` is only known to the client that started the scan. Reloading the page loses all visibility into whether a scan is running, how far along it is, or whether it errored.
2. **No progress transparency** — the UI shows only running/done/error, not how many companies have been processed or how long it has been running.
3. **Scan dies on restart** — if the Amvera container restarts mid-scan (deploy, OOM), the background thread is killed with no trace and no way to resume.

Scans can take 20+ hours. All three gaps must be closed.

---

## Solution Overview

Replace the in-memory job store with Supabase-backed scan state. Add a per-company checkpoint marker so a scan can resume from where it left off after a restart. Surface rich progress in the UI.

---

## Schema Changes

### New table: `keyword_scans`

One row per project. Upserted on each scan start.

```sql
create table keyword_scans (
  id            uuid primary key default gen_random_uuid(),
  project_id    uuid not null unique references projects(id) on delete cascade,
  status        text not null,           -- 'running' | 'done' | 'error'
  started_at    timestamptz not null,
  updated_at    timestamptz not null,
  companies_done   int not null default 0,
  companies_total  int not null default 0,
  error         text
);
```

### New column on `companies`: `keyword_scanned_at TIMESTAMPTZ`

Set to `NOW()` when a company's INN group finishes processing. Combined with `keyword_hit_count` in the same DB write (no extra round trip). Null means not yet processed in the current scan.

Resume logic: skip companies where `keyword_scanned_at >= scan.started_at`.

The existing `projects.keyword_scan_result` (base64 XLSX blob) is unchanged — it remains how completed results are stored and downloaded.

---

## Backend Changes

### `scan_project_keywords` (`keyword_scanner.py`)

Signature change:

```python
def scan_project_keywords(
    project_id: str,
    scan_started_at: datetime,
    on_total_known: Callable[[int], None],
    on_company_done: Callable[[int], None],
) -> dict:
```

- **`scan_started_at`**: used as the resume checkpoint threshold.
- **`on_total_known(total)`**: called once after deduplication, before the processing loop. Used to write `companies_total` to DB.
- **`on_company_done(done_so_far)`**: called after each INN group completes. Used to write `companies_done` to DB.

**Resume logic** (inserted after deduplication):

```python
unique_companies = [
    c for c in unique_companies
    if not _any_scanned_after(c["company_ids"], scan_started_at)
]
already_done = companies_total - len(unique_companies)
```

Where `_any_scanned_after(company_ids, scan_started_at)` fetches the `keyword_scanned_at` values for those IDs from the `companies` table and returns `True` if any is `>= scan_started_at`. This is a single `in_` query across all IDs for the INN group.

**Per-company DB write** (merged into existing `keyword_hit_count` update):

```python
supabase.table("companies").update({
    "keyword_hit_count": hit_count,
    "keyword_group_count": hit_groups,
    "keyword_scanned_at": datetime.utcnow().isoformat(),
}).eq("id", cid).execute()
```

### `_run_scan_task` (`main.py`)

Replaces the current in-memory job store entirely.

**Fresh start path** (called from `keyword_scan_start` endpoint):

1. Upsert `keyword_scans`: `status='running'`, `started_at=NOW()`, `companies_done=0`, `companies_total=0`, `error=NULL`, `updated_at=NOW()`.
2. Call `scan_project_keywords` with callbacks that write `companies_total` / `companies_done` to the DB row after each update.
3. On success: upsert `status='done'`, `updated_at=NOW()`.
4. On `ValueError`: upsert `status='error'`, `error=str(e)`.
5. On unexpected exception: upsert `status='error'`, `error='Scan failed unexpectedly'`.

**Resume path** (called from startup recovery — same function, different trigger):

Same as fresh start except `started_at` is read from the existing DB row and passed through. The upsert on step 1 only updates `status` and `updated_at`, leaving `started_at`, `companies_total`, and `companies_done` intact. The `on_company_done` callbacks will increment `companies_done` from its current value automatically.

To distinguish fresh vs. resume: `_run_scan_task` accepts an optional `started_at: datetime | None` parameter. If `None` → fresh start (generate `NOW()`). If provided → resume (use as-is).

### `_scan_jobs` dict

Removed entirely. The `job_id` concept is eliminated.

---

## Startup Recovery

```python
@app.on_event("startup")
async def recover_interrupted_scans():
    result = supabase.table("keyword_scans").select("project_id, started_at").eq("status", "running").execute()
    loop = asyncio.get_event_loop()
    for row in result.data:
        started_at = datetime.fromisoformat(row["started_at"])
        loop.run_in_executor(None, _run_scan_task, row["project_id"], started_at)
```

Any scan that was `running` when the process exited is automatically re-enqueued on the next startup. The checkpoint (`keyword_scanned_at >= started_at`) ensures already-processed companies are skipped.

---

## API Changes

### Removed endpoints

- `GET /api/projects/{project_id}/keyword-scan/{job_id}/status`
- `GET /api/projects/{project_id}/keyword-scan/{job_id}/download`

### Modified endpoints

**`POST /api/projects/{project_id}/keyword-scan/start`**

- Reads `keyword_scans` for this project. If `status='running'`, returns HTTP 409 with `{"error": "Scan already running"}`.
- Otherwise upserts a fresh row and enqueues `_run_scan_task`.
- Returns `{}` (no `job_id`).

**`GET /api/projects/{project_id}/keyword-scan/status`**

Previously only checked whether `keyword_scan_result` was non-null. Now returns:

```json
{
  "status": "running" | "done" | "error" | "none",
  "started_at": "2026-06-11T10:00:00Z",
  "companies_done": 847,
  "companies_total": 2000,
  "error": null
}
```

`"none"` when no `keyword_scans` row exists for the project.

**`GET /api/projects/{project_id}/keyword-scan/download`** — unchanged.  
**`GET /api/projects/{project_id}/keyword-scan/download-with-contacts`** — unchanged.

---

## Frontend Changes

The Keywords tab receives a **scan status panel** rendered server-side (Jinja2) and updated client-side via polling.

### Status panel states

| DB status | Display |
|---|---|
| `none` | "No scan has been run yet." + Start Scan button |
| `running` | Spinner · "Started [formatted date/time]" · "[X / Y companies]" · progress bar (X/Y%) |
| `done` | "Last scan completed [formatted date/time]" · Download button · Download with Contacts button |
| `error` | "Scan failed: [error message]" · Start Scan button (retry) |

### Polling

- On page load: fetch `/keyword-scan/status`. If `running`, start polling every 5 seconds.
- On each poll response: update counters and progress bar in-place (no page reload).
- Polling stops when status becomes `done` or `error`.
- The Start Scan button is disabled while `status='running'`.

### No breaking change to existing download buttons

The existing "Download" and "Download with Contacts" buttons remain. Their behaviour is unchanged — they only appear when a result exists (i.e. when `status='done'`).

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| `keyword_scans` DB write fails during scan | Log warning, continue scan (progress may be stale in UI but scan completes) |
| `keyword_scanned_at` write fails for a company | Log warning, continue — company will be re-processed on next resume |
| Startup recovery fails to read `keyword_scans` | Log error, skip recovery — scan must be manually restarted |
| Scan started while another is running | HTTP 409 returned; UI shows current scan state |

---

## Out of Scope

- Scan history / audit log (only latest state is tracked)
- Manual resume from UI (recovery is automatic on startup)
- Progress within a single company (granularity is per-INN-group)
