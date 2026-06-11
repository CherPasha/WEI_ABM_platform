# Process Kill Button — Design Spec

**Date:** 2026-06-11  
**Status:** Approved

---

## Problem

The three long-running background processes — session scan (upload pipeline), contact scan, and keyword scan — have no way to be stopped once started. If a user starts a scan by mistake, or wants to change keywords/roles before scanning, they must wait for it to finish or restart the server.

---

## Solution Overview

Add a Stop button to each process's UI panel. The button:
1. Shows a confirmation dialog before acting (failsafe against accidental clicks)
2. Calls a cancel endpoint that sets the process status to `'cancelling'`
3. The background thread detects the flag between company iterations, runs cleanup, and sets status to `'cancelled'`
4. All partial results are discarded (clean slate)

For the session scan, the company list (the uploaded file contents) is preserved — only the scan output (resolved names, postings, news) is wiped. A cancelled session can be re-run using the existing Resume mechanism (counters are reset to 0, so resume restarts from name resolution).

---

## DB Status Additions

No new columns. Two new status values are added to each table:

| Table | New values |
|---|---|
| `sessions` | `'cancelling'`, `'cancelled'` |
| `contact_scans` | `'cancelling'`, `'cancelled'` |
| `keyword_scans` | `'cancelling'`, `'cancelled'` |

No Supabase migrations required — status columns are free-text.

---

## Backend

### Cancellation pattern (same for all three)

1. **Cancel endpoint** sets status to `'cancelling'` in the DB and returns `{}` immediately.
2. **Background thread** checks for `'cancelling'` at the top of each per-company iteration.
3. When detected: raises a private `_CancelledError`.
4. The entry-point function catches `_CancelledError`, runs cleanup, sets status to `'cancelled'`.

All other exceptions (`ValueError`, unexpected) are handled by the existing error paths and are unaffected.

---

### Session scan (`app/services/session_processor.py`)

**New helper:**
```python
def _is_cancelling(session_id: str) -> bool:
    """Return True if the session has been flagged for cancellation."""
    result = _supabase_call_with_retry(
        lambda: supabase.table("sessions").select("status").eq("id", session_id).execute()
    )
    return bool(result.data) and result.data[0].get("status") == "cancelling"
```

**Keep existing `_session_exists()` checks unchanged** — they handle the deletion case (early return, no cleanup needed since cascade deletes data). Add `_is_cancelling()` as a separate check at those same locations AND at the top of each per-company loop. When `_is_cancelling()` returns `True`, raise `_SessionCancelledError` to trigger the cleanup path.

**Cancellation check locations:**
- Between stages (existing `_session_exists()` locations — now `_is_cancelling()`)
- Top of each per-company `for i, company in enumerate(db_companies):` loop

**Cleanup (run on `_CancelledError`):**
```python
# 1. Delete scan output
supabase.table("postings").delete().eq("session_id", session_id).execute()
supabase.table("news_articles").delete().eq("session_id", session_id).execute()

# 2. Reset known_names to [legal_name] for all companies (one update per company —
#    Supabase does not support bulk conditional updates, so loop is unavoidable;
#    use the existing _fetch_all_companies helper and batch the reads)
companies = _fetch_all_companies(session_id)  # already paginates
for c in companies:
    supabase.table("companies").update(
        {"known_names": [c["legal_name"]]}
    ).eq("id", c["id"]).execute()

# 3. Reset progress counters and set status
_update_session(session_id, status="cancelled", names_done=0, postings_done=0, news_done=0)
```

**Post-cancellation state:** `status='cancelled'`, company rows intact, no postings/news. The existing Resume button already works for cancelled sessions — it checks counters and restarts from name resolution (stage 2).

---

### Contact scan (`app/services/contact_scanner.py`)

**New check inside `run_contact_scan`:** At the top of the per-company loop, after fetching the scan row for its settings, add:

```python
def _is_cancelling_scan(scan_id: str) -> bool:
    result = _supabase_call_with_retry(
        lambda: supabase.table("contact_scans").select("status").eq("id", scan_id).execute()
    )
    return bool(result.data) and result.data[0].get("status") == "cancelling"
```

Check at the top of `for company in companies:` loop.

**Cleanup (run on `_CancelledError`):**
```python
# Delete all contacts added in this scan
supabase.table("contacts").delete().eq("contact_scan_id", scan_id).execute()
_update_scan(scan_id, status="cancelled")
```

---

### Keyword scan (`app/main.py` + `app/services/keyword_scanner.py`)

**`scan_project_keywords` gains a 5th parameter:**
```python
def scan_project_keywords(
    project_id: str,
    scan_started_at: datetime,
    on_total_known: Callable[[int], None],
    on_company_done: Callable[[int], None],
    is_cancelled: Callable[[], bool],   # NEW
) -> dict:
```

Check at the top of `for uc in batch:` inner loop (where `time.sleep(0)` already is):
```python
if is_cancelled():
    raise _KeywordScanCancelledError()
```

**`_run_scan_task` provides the check as a closure:**
```python
def _is_scan_cancelled() -> bool:
    try:
        row = supabase.table("keyword_scans").select("status").eq("project_id", project_id).execute()
        return bool(row.data) and row.data[0].get("status") == "cancelling"
    except Exception:
        return False
```

**Cleanup (run on `_KeywordScanCancelledError`):**
```python
# Clear stored result
supabase.table("projects").update({"keyword_scan_result": None}).eq("id", project_id).execute()
# Mark cancelled (keyword_scanned_at needs no reset — fresh scans use new started_at as threshold)
_upsert_keyword_scan(project_id, {
    "status": "cancelled",
    "updated_at": datetime.now(timezone.utc).isoformat(),
})
```

---

## API Endpoints

### `POST /api/sessions/{session_id}/cancel`
- If `sessions.status` is not `'running'`/`'resolving_names'`/`'finding_postings'`/`'finding_news'`/`'resuming'` → HTTP 409
- Set `sessions.status = 'cancelling'`
- Return `{}`

### `POST /api/projects/{project_id}/contact-scan/cancel`
- Fetch latest `contact_scans` row for this project
- If status is not `'running'` → HTTP 409
- Set `contact_scans.status = 'cancelling'`
- Return `{}`

### `POST /api/projects/{project_id}/keyword-scan/cancel`
- If `keyword_scans.status` is not `'running'` → HTTP 409
- Set `keyword_scans.status = 'cancelling'`
- Return `{}`

---

## Frontend

### Confirmation dialogs

Every Stop button shows a `confirm()` before calling the endpoint:

| Process | Dialog text |
|---|---|
| Session scan | `"Stop this scan? The company list is kept but all names, postings, and news found so far will be discarded."` |
| Contact scan | `"Stop the contact scan? All contacts found so far will be deleted."` |
| Keyword scan | `"Stop the keyword scan? All progress will be discarded."` |

### Session scan (Upload tab)

**Stop button** added to `#progress-section` below the stage table.

| Status | UI |
|---|---|
| `resolving_names` / `finding_postings` / `finding_news` / `resuming` | Red "Stop Scan" button enabled |
| `cancelling` | Button disabled, label "Stopping…" with `aria-busy` |
| `cancelled` | Progress section hides; history table shows `"Stopped"` badge in red; Resume button available (resets to stage 2) |

`ACTIVE_STATUSES` in JS updated to include `'cancelling'` (keeps polling). Polling stops and progress section hides on `'cancelled'`.

History table: `isActive` updated to include `'cancelling'`. Cancelled sessions show a `"Stopped ✕"` badge and a Resume link.

### Contact scan (Roles tab)

**Stop button** added inside the `#contact-scan-progress` div, below the stage table.

| Status | UI |
|---|---|
| `running` | Red "Stop Contact Scan" button enabled |
| `cancelling` | Button disabled, "Stopping…" with `aria-busy` |
| `cancelled` | Progress cleared; status text shows "Contact scan stopped — results discarded" in muted color; Launch Contact Scan button re-enabled |

`pollContactScan()` updated to handle `'cancelling'` (keep polling) and `'cancelled'` (stop polling, show stopped state).

### Keyword scan (Keywords tab)

**Stop button** added inside `#kw-scan-status` panel, next to the progress text.

| Status | UI |
|---|---|
| `running` | Red "Stop Scan" button in status panel |
| `cancelling` | Button disabled, panel text "Stopping…" |
| `cancelled` | Panel shows "Scan stopped — results discarded"; Run Keyword Scan button re-enabled |

`updateKeywordScanPanel()` updated with `'cancelling'` and `'cancelled'` states. `loadKeywordScanStatus()` returns `'cancelling'` as an active status (polling continues).

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Cancel called when process not running | HTTP 409 — UI shows no change |
| Cleanup DB call fails | Log warning, continue cleanup steps, still set `status='cancelled'` |
| Thread finishes naturally before seeing `'cancelling'` | Status is set to `'done'`/`'completed'` by the normal path — cancel has no effect, UI shows completed |
| Server restarts while `status='cancelling'` | Keyword scan: startup recovery re-enqueues it; thread immediately sees `'cancelling'` and runs cleanup. Session/contact scans: status stays `'cancelling'` until user manually re-triggers or the process naturally completes on next restart |

---

## Out of Scope

- Partial-result preservation (user chose discard/clean-slate)
- Undo / restore after cancellation
- Batch cancellation of multiple running processes at once
