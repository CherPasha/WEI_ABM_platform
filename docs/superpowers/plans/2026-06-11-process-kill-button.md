# Process Kill Button — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Stop button (with confirmation dialog) to each of the three long-running scan processes — session scan, contact scan, keyword scan — that immediately signals cancellation, discards all partial results, and resets the process to a clean restartable state.

**Architecture:** DB-flag polling pattern: a cancel endpoint sets status to `'cancelling'`; the background thread detects it between company iterations, raises a private `CancelledError`, and the outer handler runs cleanup (delete partial data, reset counters) before setting `'cancelled'`. No new DB columns needed — all three tables already have a free-text `status` field.

**Tech Stack:** FastAPI, Supabase (supabase-py), Python 3.11, PicoCSS + vanilla JS (existing stack)

**Spec:** `docs/superpowers/specs/2026-06-11-process-kill-button-design.md`

---

## File Map

| File | Change |
|---|---|
| `app/services/session_processor.py` | Add `_SessionCancelledError`, `_is_cancelling()`, cancellation checks + cleanup in `process_session` and `resume_session` |
| `app/services/contact_scanner.py` | Add `_ContactScanCancelledError`, `_is_cancelling_scan()`, cancellation check + cleanup in `run_contact_scan` |
| `app/services/keyword_scanner.py` | Add `ScanCancelledError` (public), `is_cancelled` parameter to `scan_project_keywords`, check in inner loop |
| `app/main.py` | Add `_is_scan_cancelled` closure + `except ScanCancelledError` handler in `_run_scan_task`; add 3 cancel endpoints |
| `app/templates/project.html` | Stop buttons for all 3 processes with confirm dialogs; updated polling handlers |
| `tests/test_cancel_session.py` | New: unit tests for `_is_cancelling` |
| `tests/test_cancel_contact_scan.py` | New: unit tests for `_is_cancelling_scan` |
| `tests/test_cancel_keyword_scan.py` | New: unit tests for `ScanCancelledError` raised on cancellation |

---

## Task 1: Session scan cancellation

**Files:**
- Modify: `app/services/session_processor.py`
- Create: `tests/test_cancel_session.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cancel_session.py`:

```python
from unittest.mock import patch, MagicMock


@patch("app.services.session_processor._supabase_call_with_retry")
def test_is_cancelling_true_when_status_cancelling(mock_retry):
    from app.services.session_processor import _is_cancelling
    mock_retry.side_effect = lambda fn: MagicMock(data=[{"status": "cancelling"}])
    assert _is_cancelling("sess-1") is True


@patch("app.services.session_processor._supabase_call_with_retry")
def test_is_cancelling_false_when_status_running(mock_retry):
    from app.services.session_processor import _is_cancelling
    mock_retry.side_effect = lambda fn: MagicMock(data=[{"status": "running"}])
    assert _is_cancelling("sess-1") is False


@patch("app.services.session_processor._supabase_call_with_retry")
def test_is_cancelling_false_when_no_row(mock_retry):
    from app.services.session_processor import _is_cancelling
    mock_retry.side_effect = lambda fn: MagicMock(data=[])
    assert _is_cancelling("sess-1") is False
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd "/Users/pashache/Desktop/projects/WEI group/ABM/WEI_ABM_platform"
python -m pytest tests/test_cancel_session.py -v
```

Expected: `ImportError` — `_is_cancelling` does not exist yet.

- [ ] **Step 3: Add `_SessionCancelledError` and `_is_cancelling` to `session_processor.py`**

After the `_session_exists` function (around line 79), add:

```python
class _SessionCancelledError(Exception):
    """Raised when a session scan is flagged for cancellation."""


def _is_cancelling(session_id: str) -> bool:
    """Return True if the session has been flagged for cancellation."""
    result = _supabase_call_with_retry(
        lambda: supabase.table("sessions").select("status").eq("id", session_id).execute()
    )
    return bool(result.data) and result.data[0].get("status") == "cancelling"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_cancel_session.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Add cancellation checks to `process_session`**

In `process_session`, the two existing `_session_exists()` blocks (before postings and before news) currently do:

```python
        if not _session_exists(session_id):
            logger.warning("Session %s was deleted during processing, aborting", session_id)
            return
```

Replace BOTH with the combined check:

```python
        if not _session_exists(session_id):
            logger.warning("Session %s was deleted during processing, aborting", session_id)
            return
        if _is_cancelling(session_id):
            raise _SessionCancelledError()
```

Also add a per-company check inside the names resolution loop. Find:

```python
        for i, company in enumerate(db_companies):
            legal_name = company.get("legal_name", "")
            if not legal_name:
                continue
```

Add after the `if not legal_name: continue` line:

```python
            if _is_cancelling(session_id):
                raise _SessionCancelledError()
```

Add the same check at the top of the postings per-company loop. Find:

```python
            for i, company in enumerate(db_companies):
                try:
                    known_names = company.get("known_names") or []
                    postings = find_all_postings_for_company(known_names)
```

Add before the `try:`:

```python
                if _is_cancelling(session_id):
                    raise _SessionCancelledError()
```

Add the same check at the top of the news per-company loop (same pattern, just the news `for i, company` block).

- [ ] **Step 6: Add cleanup handler to `process_session`**

The current except clause catches generic `Exception`. Change the structure so `_SessionCancelledError` is caught first. Find:

```python
    except Exception as e:
        logger.exception("Session %s failed: %s", session_id, e)
        _update_session(session_id, status="failed", error_message=str(e)[:500])
```

Replace with:

```python
    except _SessionCancelledError:
        logger.info("Session %s cancelled, cleaning up", session_id)
        try:
            supabase.table("postings").delete().eq("session_id", session_id).execute()
        except Exception:
            logger.warning("Failed to delete postings for cancelled session %s", session_id)
        try:
            supabase.table("news_articles").delete().eq("session_id", session_id).execute()
        except Exception:
            logger.warning("Failed to delete news_articles for cancelled session %s", session_id)
        try:
            companies = _fetch_all_companies(session_id)
            for c in companies:
                supabase.table("companies").update(
                    {"known_names": [c["legal_name"]]}
                ).eq("id", c["id"]).execute()
        except Exception:
            logger.warning("Failed to reset known_names for cancelled session %s", session_id)
        _update_session(session_id, status="cancelled", names_done=0, postings_done=0, news_done=0)
    except Exception as e:
        logger.exception("Session %s failed: %s", session_id, e)
        _update_session(session_id, status="failed", error_message=str(e)[:500])
```

- [ ] **Step 7: Apply the same cancellation checks to `resume_session`**

`resume_session` also has three per-company loops (names, postings, news). Add `if _is_cancelling(session_id): raise _SessionCancelledError()` at the top of each per-company loop body (same pattern as Step 5).

Also change `resume_session`'s except clause the same way as in Step 6.

- [ ] **Step 8: Run all tests**

```bash
python -m pytest tests/ -v 2>&1 | tail -5
```

Expected: same pass count as before + 3 new passing tests.

- [ ] **Step 9: Commit**

```bash
git add app/services/session_processor.py tests/test_cancel_session.py
git commit -m "feat: add cancellation support to session scan"
```

---

## Task 2: Contact scan cancellation

**Files:**
- Modify: `app/services/contact_scanner.py`
- Create: `tests/test_cancel_contact_scan.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cancel_contact_scan.py`:

```python
from unittest.mock import patch, MagicMock


@patch("app.services.contact_scanner._supabase_call_with_retry")
def test_is_cancelling_scan_true_when_status_cancelling(mock_retry):
    from app.services.contact_scanner import _is_cancelling_scan
    mock_retry.side_effect = lambda fn: MagicMock(data=[{"status": "cancelling"}])
    assert _is_cancelling_scan("scan-1") is True


@patch("app.services.contact_scanner._supabase_call_with_retry")
def test_is_cancelling_scan_false_when_status_running(mock_retry):
    from app.services.contact_scanner import _is_cancelling_scan
    mock_retry.side_effect = lambda fn: MagicMock(data=[{"status": "running"}])
    assert _is_cancelling_scan("scan-1") is False


@patch("app.services.contact_scanner._supabase_call_with_retry")
def test_is_cancelling_scan_false_when_no_row(mock_retry):
    from app.services.contact_scanner import _is_cancelling_scan
    mock_retry.side_effect = lambda fn: MagicMock(data=[])
    assert _is_cancelling_scan("scan-1") is False
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_cancel_contact_scan.py -v
```

Expected: `ImportError` — `_is_cancelling_scan` does not exist yet.

- [ ] **Step 3: Add `_ContactScanCancelledError` and `_is_cancelling_scan` to `contact_scanner.py`**

After `_update_scan` (around line 31), add:

```python
class _ContactScanCancelledError(Exception):
    """Raised when a contact scan is flagged for cancellation."""


def _is_cancelling_scan(scan_id: str) -> bool:
    """Return True if the contact scan has been flagged for cancellation."""
    result = _supabase_call_with_retry(
        lambda: supabase.table("contact_scans").select("status").eq("id", scan_id).execute()
    )
    return bool(result.data) and result.data[0].get("status") == "cancelling"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_cancel_contact_scan.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Add cancellation check to `run_contact_scan`**

Inside `run_contact_scan`, find the per-company loop:

```python
        for i, company in enumerate(companies):
            company_id: str = company["id"]
```

Add before `company_id:`:

```python
            if _is_cancelling_scan(scan_id):
                raise _ContactScanCancelledError()
```

- [ ] **Step 6: Add cleanup handler to `run_contact_scan`**

Find the existing except clause in `run_contact_scan`:

```python
    except Exception as e:
        logger.exception("Contact scan %s failed: %s", scan_id, e)
        _update_scan(scan_id, status="failed", error_message=str(e)[:500])
```

Replace with:

```python
    except _ContactScanCancelledError:
        logger.info("Contact scan %s cancelled, cleaning up", scan_id)
        try:
            supabase.table("contacts").delete().eq("contact_scan_id", scan_id).execute()
        except Exception:
            logger.warning("Failed to delete contacts for cancelled scan %s", scan_id)
        _update_scan(scan_id, status="cancelled")
    except Exception as e:
        logger.exception("Contact scan %s failed: %s", scan_id, e)
        _update_scan(scan_id, status="failed", error_message=str(e)[:500])
```

- [ ] **Step 7: Run all tests**

```bash
python -m pytest tests/ -v 2>&1 | tail -5
```

Expected: same pass count as before + 3 new passing tests.

- [ ] **Step 8: Commit**

```bash
git add app/services/contact_scanner.py tests/test_cancel_contact_scan.py
git commit -m "feat: add cancellation support to contact scan"
```

---

## Task 3: Keyword scan cancellation

**Files:**
- Modify: `app/services/keyword_scanner.py`
- Create: `tests/test_cancel_keyword_scan.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cancel_keyword_scan.py`:

```python
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


def _make_fetch_all():
    def fake(table, column, value, select="*"):
        if table == "keyword_groups":
            return [{"id": "g1", "name": "Tech"}]
        if table == "keywords":
            return [{"id": "k1", "group_id": "g1", "keyword": "AI"}]
        if table == "stop_words":
            return []
        if table == "sessions":
            return [{"id": "s1"}]
        return []
    return fake


def _make_fetch_in():
    def fake(table, column, values, select="*"):
        if table == "keywords":
            return [{"id": "k1", "group_id": "g1", "keyword": "AI"}]
        if table == "companies" and column == "session_id":
            return [
                {"id": "c1", "legal_name": "Acme", "inn": "001", "known_names": []},
                {"id": "c2", "legal_name": "Beta", "inn": "002", "known_names": []},
            ]
        if table == "companies" and column == "id" and "keyword_scanned_at" in select:
            return [{"id": cid, "keyword_scanned_at": None} for cid in values]
        if table in ("postings", "news_articles"):
            return []
        return []
    return fake


@patch("app.services.keyword_scanner.time.sleep", return_value=None)
@patch("app.services.keyword_scanner.supabase")
@patch("app.services.keyword_scanner._fetch_all_in")
@patch("app.services.keyword_scanner._fetch_all")
def test_scan_raises_scan_cancelled_error_when_is_cancelled_true(
    mock_fetch_all, mock_fetch_in, mock_supa, mock_sleep
):
    """scan_project_keywords raises ScanCancelledError when is_cancelled() returns True."""
    from app.services.keyword_scanner import scan_project_keywords, ScanCancelledError

    mock_fetch_all.side_effect = _make_fetch_all()
    mock_fetch_in.side_effect = _make_fetch_in()
    mock_supa.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    started_at = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ScanCancelledError):
        scan_project_keywords(
            "proj1",
            started_at,
            on_total_known=lambda t: None,
            on_company_done=lambda d: None,
            is_cancelled=lambda: True,  # always cancelled
        )


@patch("app.services.keyword_scanner.time.sleep", return_value=None)
@patch("app.services.keyword_scanner.supabase")
@patch("app.services.keyword_scanner._fetch_all_in")
@patch("app.services.keyword_scanner._fetch_all")
def test_scan_completes_when_not_cancelled(
    mock_fetch_all, mock_fetch_in, mock_supa, mock_sleep
):
    """scan_project_keywords completes normally when is_cancelled() always returns False."""
    from app.services.keyword_scanner import scan_project_keywords, ScanCancelledError

    mock_fetch_all.side_effect = _make_fetch_all()
    mock_fetch_in.side_effect = _make_fetch_in()
    mock_supa.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    started_at = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)

    result = scan_project_keywords(
        "proj1",
        started_at,
        on_total_known=lambda t: None,
        on_company_done=lambda d: None,
        is_cancelled=lambda: False,
    )
    assert "companies" in result
    assert len(result["companies"]) == 2
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_cancel_keyword_scan.py -v
```

Expected: `TypeError` — `scan_project_keywords` does not accept `is_cancelled` yet.

- [ ] **Step 3: Add `ScanCancelledError` to `keyword_scanner.py`**

At the top of the file, after the existing imports, add:

```python
class ScanCancelledError(Exception):
    """Raised when a keyword scan is flagged for cancellation by the user."""
```

- [ ] **Step 4: Add `is_cancelled` parameter to `scan_project_keywords`**

Replace the current signature:

```python
def scan_project_keywords(
    project_id: str,
    scan_started_at: datetime,
    on_total_known: Callable[[int], None],
    on_company_done: Callable[[int], None],
) -> dict:
```

With:

```python
def scan_project_keywords(
    project_id: str,
    scan_started_at: datetime,
    on_total_known: Callable[[int], None],
    on_company_done: Callable[[int], None],
    is_cancelled: Callable[[], bool] = lambda: False,
) -> dict:
```

- [ ] **Step 5: Add cancellation check inside the inner company loop**

Find this block inside `for uc in batch:`:

```python
        for uc in batch:
            time.sleep(0)  # yield GIL once per company so the event loop can serve status polls
            company_postings = []
```

Add after `time.sleep(0)`:

```python
            if is_cancelled():
                raise ScanCancelledError()
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
python -m pytest tests/test_cancel_keyword_scan.py tests/test_keyword_scan_persistence.py tests/test_keyword_scanner_hits.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/services/keyword_scanner.py tests/test_cancel_keyword_scan.py
git commit -m "feat: add ScanCancelledError and is_cancelled parameter to scan_project_keywords"
```

---

## Task 4: Cancel API endpoints + `_run_scan_task` update

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Update `_run_scan_task` to pass `is_cancelled` and handle `ScanCancelledError`**

First add the import. Find the existing import line:

```python
from app.services.keyword_scanner import scan_project_keywords, generate_keyword_xlsx, derive_quick_summary_df
```

Replace with:

```python
from app.services.keyword_scanner import scan_project_keywords, generate_keyword_xlsx, derive_quick_summary_df, ScanCancelledError
```

Then in `_run_scan_task`, find the call to `scan_project_keywords`:

```python
        scan_result = scan_project_keywords(
            project_id,
            started_at,
            _on_total_known,
            _on_company_done,
        )
```

Add the closure and fifth argument:

```python
        def _is_scan_cancelled() -> bool:
            try:
                row = (
                    supabase.table("keyword_scans")
                    .select("status")
                    .eq("project_id", project_id)
                    .execute()
                )
                return bool(row.data) and row.data[0].get("status") == "cancelling"
            except Exception:
                return False

        scan_result = scan_project_keywords(
            project_id,
            started_at,
            _on_total_known,
            _on_company_done,
            _is_scan_cancelled,
        )
```

Then add the `ScanCancelledError` handler. Find the existing except block at the end of `_run_scan_task`:

```python
    except ValueError as e:
        _upsert_keyword_scan(project_id, {
```

Add a new except clause BEFORE `except ValueError`:

```python
    except ScanCancelledError:
        _logger.info("Keyword scan cancelled for project %s, cleaning up", project_id)
        try:
            supabase.table("projects").update(
                {"keyword_scan_result": None}
            ).eq("id", project_id).execute()
        except Exception:
            _logger.warning("Failed to clear keyword_scan_result for project %s", project_id)
        try:
            _upsert_keyword_scan(project_id, {
                "status": "cancelled",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            _logger.warning("Failed to set cancelled status for project %s", project_id)
    except ValueError as e:
```

- [ ] **Step 2: Add the three cancel endpoints**

Add these three endpoints to `app/main.py` in the Keyword Scan section (after the existing keyword scan endpoints) and in the appropriate sections for sessions and contact scans.

**Session cancel endpoint** — add after the `resume_session_endpoint` function (around line 300):

```python
@app.post("/api/sessions/{session_id}/cancel")
async def cancel_session(session_id: str):
    result = (
        supabase.table("sessions")
        .select("status")
        .eq("id", session_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Session not found")
    active = {"resolving_names", "finding_postings", "finding_news", "resuming", "parsing"}
    if result.data[0].get("status") not in active:
        raise HTTPException(status_code=409, detail="Session is not running")
    supabase.table("sessions").update({"status": "cancelling"}).eq("id", session_id).execute()
    return {}
```

**Contact scan cancel endpoint** — add after `contact_scan_latest_status` (around line 225):

```python
@app.post("/api/projects/{project_id}/contact-scan/cancel")
async def contact_scan_cancel(project_id: str):
    result = (
        supabase.table("contact_scans")
        .select("id, status")
        .eq("project_id", project_id)
        .eq("status", "running")
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=409, detail="No running contact scan found")
    scan_id = result.data[0]["id"]
    supabase.table("contact_scans").update({"status": "cancelling"}).eq("id", scan_id).execute()
    return {}
```

**Keyword scan cancel endpoint** — add after `keyword_scan_start` (in the Keyword Scan section):

```python
@app.post("/api/projects/{project_id}/keyword-scan/cancel")
async def keyword_scan_cancel(project_id: str):
    result = (
        supabase.table("keyword_scans")
        .select("status")
        .eq("project_id", project_id)
        .execute()
    )
    if not result.data or result.data[0].get("status") != "running":
        raise HTTPException(status_code=409, detail="No running keyword scan found")
    supabase.table("keyword_scans").update({"status": "cancelling"}).eq("project_id", project_id).execute()
    return {}
```

- [ ] **Step 3: Verify the app imports without error**

```bash
cd "/Users/pashache/Desktop/projects/WEI group/ABM/WEI_ABM_platform"
python -c "from app.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/ -v 2>&1 | tail -5
```

Expected: same pass count as before (no regressions).

- [ ] **Step 5: Commit**

```bash
git add app/main.py
git commit -m "feat: add cancel endpoints and ScanCancelledError handling to keyword scan"
```

---

## Task 5: Frontend — keyword scan Stop button

**Files:**
- Modify: `app/templates/project.html`

- [ ] **Step 1: Add Stop button to `#kw-scan-status` panel**

Find the existing status panel HTML inside `#panel-keywords`:

```html
                    <!-- Keyword scan status panel -->
                    <div id="kw-scan-status" style="display:none; margin-bottom:0.75em;">
                        <p id="kw-scan-status-text" style="font-size:0.9em; margin:0 0 0.4em;"></p>
                        <progress id="kw-scan-progress-bar" value="0" max="100" style="width:100%; display:none;"></progress>
                    </div>
```

Replace with:

```html
                    <!-- Keyword scan status panel -->
                    <div id="kw-scan-status" style="display:none; margin-bottom:0.75em;">
                        <p id="kw-scan-status-text" style="font-size:0.9em; margin:0 0 0.4em;"></p>
                        <progress id="kw-scan-progress-bar" value="0" max="100" style="width:100%; display:none;"></progress>
                        <button id="kw-stop-btn" onclick="stopKeywordScan()" class="secondary"
                                style="margin-top:0.4em; display:none; font-size:0.85em; padding:0.3em 0.8em; color:var(--pico-color-red-500);">
                            Stop Scan
                        </button>
                    </div>
```

- [ ] **Step 2: Update `updateKeywordScanPanel()` to handle `cancelling` and `cancelled` states, and show/hide the Stop button**

Find the `updateKeywordScanPanel` function. Replace it entirely:

```javascript
        function updateKeywordScanPanel(data) {
            const panel = document.getElementById("kw-scan-status");
            const text  = document.getElementById("kw-scan-status-text");
            const bar   = document.getElementById("kw-scan-progress-bar");
            const btn   = document.getElementById("scan-btn");
            const stopBtn = document.getElementById("kw-stop-btn");

            if (data.status === "none") {
                panel.style.display = "none";
                btn.disabled = false;
                btn.removeAttribute("aria-busy");
                stopBtn.style.display = "none";
                return;
            }

            panel.style.display = "block";
            text.style.color = "";

            if (data.status === "running") {
                const done  = data.companies_done  || 0;
                const total = data.companies_total || 0;
                const started = data.started_at ? new Date(data.started_at).toLocaleString() : "";
                const progress = total > 0 ? `${done} / ${total} companies` : "starting\u2026";
                text.textContent = `Scan running \u2014 started ${started} \u00b7 ${progress}`;
                bar.style.display = total > 0 ? "" : "none";
                bar.value = total > 0 ? Math.round((done / total) * 100) : 0;
                btn.disabled = true;
                btn.setAttribute("aria-busy", "true");
                stopBtn.style.display = "";
                stopBtn.disabled = false;
                stopBtn.textContent = "Stop Scan";
                stopBtn.removeAttribute("aria-busy");
            } else if (data.status === "cancelling") {
                text.textContent = "Stopping\u2026";
                bar.style.display = "none";
                btn.disabled = true;
                btn.setAttribute("aria-busy", "true");
                stopBtn.style.display = "";
                stopBtn.disabled = true;
                stopBtn.setAttribute("aria-busy", "true");
                stopBtn.textContent = "Stopping\u2026";
            } else if (data.status === "done") {
                const started = data.started_at ? new Date(data.started_at).toLocaleString() : "";
                text.textContent = `Last scan completed \u2014 started ${started}`;
                bar.style.display = "none";
                btn.disabled = false;
                btn.removeAttribute("aria-busy");
                stopBtn.style.display = "none";
            } else if (data.status === "error") {
                text.style.color = "var(--pico-color-red-500)";
                text.textContent = `Scan failed: ${data.error || "Unknown error"}`;
                bar.style.display = "none";
                btn.disabled = false;
                btn.removeAttribute("aria-busy");
                stopBtn.style.display = "none";
            } else if (data.status === "cancelled") {
                text.style.color = "var(--pico-muted-color)";
                text.textContent = "Scan stopped \u2014 results discarded";
                bar.style.display = "none";
                btn.disabled = false;
                btn.removeAttribute("aria-busy");
                stopBtn.style.display = "none";
            }
        }
```

- [ ] **Step 3: Update `startKeywordScanPolling` to keep polling on `cancelling`**

Find:

```javascript
        function startKeywordScanPolling() {
            if (kwScanPollInterval) clearInterval(kwScanPollInterval);
            kwScanPollInterval = setInterval(async () => {
                const status = await loadKeywordScanStatus();
                if (status === "done" || status === "error") {
```

Replace:

```javascript
        function startKeywordScanPolling() {
            if (kwScanPollInterval) clearInterval(kwScanPollInterval);
            kwScanPollInterval = setInterval(async () => {
                const status = await loadKeywordScanStatus();
                if (status === "done" || status === "error" || status === "cancelled") {
```

- [ ] **Step 4: Add `stopKeywordScan()` function**

Add this function immediately after `runKeywordScan()`:

```javascript
        async function stopKeywordScan() {
            if (!confirm("Stop the keyword scan? All progress will be discarded.")) return;
            const stopBtn = document.getElementById("kw-stop-btn");
            stopBtn.disabled = true;
            stopBtn.setAttribute("aria-busy", "true");
            try {
                await fetch(`/api/projects/${PROJECT_ID}/keyword-scan/cancel`, { method: "POST" });
            } catch (err) {
                console.error("Failed to cancel keyword scan:", err);
                stopBtn.disabled = false;
                stopBtn.removeAttribute("aria-busy");
            }
        }
```

- [ ] **Step 5: Update `refreshTabStates` to treat `cancelled` as no result**

Find inside `refreshTabStates()`:

```javascript
                setTabState('keywords', scanStatus.status === 'done' ? 'green' : hasKeywords ? 'yellow' : '');
```

This already handles `cancelled` correctly (not `'done'` → falls through to `hasKeywords`). No change needed.

- [ ] **Step 6: Verify the app imports**

```bash
python -c "from app.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: add Stop button to keyword scan status panel"
```

---

## Task 6: Frontend — contact scan Stop button

**Files:**
- Modify: `app/templates/project.html`

- [ ] **Step 1: Add Stop button to `#contact-scan-progress`**

Find the contact scan progress div, specifically the `#scan-contacts-status` paragraph:

```html
                        <p id="scan-contacts-status" style="font-size:0.9em; margin-top:0.5em;"></p>
```

Add a Stop button immediately after it:

```html
                        <p id="scan-contacts-status" style="font-size:0.9em; margin-top:0.5em;"></p>
                        <button id="contact-stop-btn" onclick="stopContactScan()"
                                style="display:none; font-size:0.85em; padding:0.3em 0.8em; color:var(--pico-color-red-500); margin-top:0.3em;"
                                class="secondary">
                            Stop Contact Scan
                        </button>
```

- [ ] **Step 2: Update `pollContactScan()` to handle `cancelling` and `cancelled`**

Find the `pollContactScan` function. Inside it, find the block that handles running/completed/failed:

```javascript
                if (data.status === "completed") {
                    clearInterval(contactScanPollInterval);
                    btn.disabled = false;
                    btn.removeAttribute("aria-busy");
                    statusEl.textContent =
                        `Completed — ${data.contacts_added || 0} contacts added`;
                    statusEl.style.color = "var(--pico-color-green-500)";
                    refreshTabStates();
                } else if (data.status === "failed") {
                    clearInterval(contactScanPollInterval);
                    btn.disabled = false;
                    btn.removeAttribute("aria-busy");
                    statusEl.textContent = "Failed: " + (data.error_message || "Unknown error");
                    statusEl.style.color = "var(--pico-color-red-500)";
                } else {
                    // still running
                    btn.disabled = true;
                    btn.setAttribute("aria-busy", "true");
                }
```

Replace with:

```javascript
                const stopBtn = document.getElementById("contact-stop-btn");
                if (data.status === "completed") {
                    clearInterval(contactScanPollInterval);
                    btn.disabled = false;
                    btn.removeAttribute("aria-busy");
                    statusEl.textContent = `Completed — ${data.contacts_added || 0} contacts added`;
                    statusEl.style.color = "var(--pico-color-green-500)";
                    if (stopBtn) { stopBtn.style.display = "none"; }
                    refreshTabStates();
                } else if (data.status === "failed") {
                    clearInterval(contactScanPollInterval);
                    btn.disabled = false;
                    btn.removeAttribute("aria-busy");
                    statusEl.textContent = "Failed: " + (data.error_message || "Unknown error");
                    statusEl.style.color = "var(--pico-color-red-500)";
                    if (stopBtn) { stopBtn.style.display = "none"; }
                } else if (data.status === "cancelled") {
                    clearInterval(contactScanPollInterval);
                    btn.disabled = false;
                    btn.removeAttribute("aria-busy");
                    statusEl.textContent = "Contact scan stopped \u2014 results discarded";
                    statusEl.style.color = "var(--pico-muted-color)";
                    if (stopBtn) { stopBtn.style.display = "none"; }
                } else if (data.status === "cancelling") {
                    btn.disabled = true;
                    btn.setAttribute("aria-busy", "true");
                    statusEl.textContent = "Stopping\u2026";
                    if (stopBtn) { stopBtn.disabled = true; stopBtn.setAttribute("aria-busy", "true"); stopBtn.textContent = "Stopping\u2026"; }
                } else {
                    // still running
                    btn.disabled = true;
                    btn.setAttribute("aria-busy", "true");
                    if (stopBtn) { stopBtn.style.display = ""; stopBtn.disabled = false; stopBtn.removeAttribute("aria-busy"); stopBtn.textContent = "Stop Contact Scan"; }
                }
```

- [ ] **Step 3: Add `stopContactScan()` function**

Add immediately after the `launchContactScan` function (after its closing `}`):

```javascript
        async function stopContactScan() {
            if (!confirm("Stop the contact scan? All contacts found so far will be deleted.")) return;
            const stopBtn = document.getElementById("contact-stop-btn");
            if (stopBtn) { stopBtn.disabled = true; stopBtn.setAttribute("aria-busy", "true"); }
            try {
                await fetch(`/api/projects/${PROJECT_ID}/contact-scan/cancel`, { method: "POST" });
            } catch (err) {
                console.error("Failed to cancel contact scan:", err);
                if (stopBtn) { stopBtn.disabled = false; stopBtn.removeAttribute("aria-busy"); }
            }
        }
```

- [ ] **Step 4: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: add Stop button to contact scan progress panel"
```

---

## Task 7: Frontend — session scan Stop button

**Files:**
- Modify: `app/templates/project.html`

- [ ] **Step 1: Add Stop button to `#progress-section`**

Find the `#progress-section` article, specifically the progress table. The table closes with `</table>`. Add a Stop button immediately after the closing `</table>` tag:

```html
                    </table>
                    <button id="session-stop-btn" onclick="stopActiveSession()"
                            style="display:none; margin-top:0.5em; font-size:0.85em; padding:0.3em 0.8em; color:var(--pico-color-red-500);"
                            class="secondary">
                        Stop Scan
                    </button>
```

- [ ] **Step 2: Update `ACTIVE_STATUSES` to include `'cancelling'`**

Find:

```javascript
        const ACTIVE_STATUSES = new Set(["resolving_names", "finding_postings", "finding_news", "resuming"]);
```

Replace with:

```javascript
        const ACTIVE_STATUSES = new Set(["resolving_names", "finding_postings", "finding_news", "resuming", "parsing", "cancelling"]);
```

- [ ] **Step 3: Update `pollStatus()` to show/hide Stop button and handle `cancelled`**

Find inside `pollStatus`:

```javascript
                if (data.status === "completed") {
                    clearInterval(pollInterval);
                    loadHistory();
                    refreshTabStates();
                } else if (data.status === "failed") {
                    clearInterval(pollInterval);
                    const errorEl = document.getElementById("error-msg");
                    errorEl.textContent = "Processing failed: " + (data.error_message || "Unknown error");
                    errorEl.style.display = "block";
                    loadHistory();
                }
```

Replace with:

```javascript
                const stopBtn = document.getElementById("session-stop-btn");
                if (ACTIVE_STATUSES.has(data.status) && data.status !== "cancelling") {
                    if (stopBtn) { stopBtn.style.display = ""; stopBtn.disabled = false; stopBtn.removeAttribute("aria-busy"); stopBtn.textContent = "Stop Scan"; }
                } else if (data.status === "cancelling") {
                    if (stopBtn) { stopBtn.style.display = ""; stopBtn.disabled = true; stopBtn.setAttribute("aria-busy", "true"); stopBtn.textContent = "Stopping\u2026"; }
                } else {
                    if (stopBtn) { stopBtn.style.display = "none"; }
                }

                if (data.status === "completed") {
                    clearInterval(pollInterval);
                    document.getElementById("progress-section").style.display = "none";
                    loadHistory();
                    refreshTabStates();
                } else if (data.status === "failed") {
                    clearInterval(pollInterval);
                    const errorEl = document.getElementById("error-msg");
                    errorEl.textContent = "Processing failed: " + (data.error_message || "Unknown error");
                    errorEl.style.display = "block";
                    loadHistory();
                } else if (data.status === "cancelled") {
                    clearInterval(pollInterval);
                    document.getElementById("progress-section").style.display = "none";
                    loadHistory();
                }
```

- [ ] **Step 4: Update `loadHistory()` to show `cancelled` sessions properly**

Find inside `loadHistory()` the `isActive` definition:

```javascript
                    const isActive = ["uploading", "parsing", "resolving_names", "finding_postings", "finding_news", "resuming"].includes(s.status);
```

Replace with:

```javascript
                    const isActive = ["uploading", "parsing", "resolving_names", "finding_postings", "finding_news", "resuming", "cancelling"].includes(s.status);
```

Find:

```javascript
                    const canResume = !isComplete && !isActive && total > 0;
```

Replace with:

```javascript
                    const canResume = !isComplete && !isActive && total > 0 && s.status !== "uploading";
```

(This already works for `cancelled` since it's not in `isActive` and not `completed`.)

Find the `isFailed` check:

```javascript
                    const isFailed = s.status === "failed";
                    const filename = isFailed
                        ? `<span style="color:var(--pico-color-red-500)" title="Failed">${s.filename} \u2715</span>`
                        : s.filename;
```

Replace with:

```javascript
                    const isFailed = s.status === "failed";
                    const isCancelled = s.status === "cancelled";
                    const filename = isFailed
                        ? `<span style="color:var(--pico-color-red-500)" title="Failed">${s.filename} \u2715</span>`
                        : isCancelled
                            ? `<span style="color:var(--pico-muted-color)" title="Stopped">${s.filename} \u25a0</span>`
                            : s.filename;
```

- [ ] **Step 5: Add `stopActiveSession()` and `stopSessionById()` functions**

Add immediately after the `resumeSession` function (after its closing `}`):

```javascript
        async function stopActiveSession() {
            if (!currentSessionId) return;
            await stopSessionById(currentSessionId);
        }

        async function stopSessionById(sessionId) {
            if (!confirm("Stop this scan? The company list is kept but all names, postings, and news found so far will be discarded.")) return;
            const stopBtn = document.getElementById("session-stop-btn");
            if (stopBtn) { stopBtn.disabled = true; stopBtn.setAttribute("aria-busy", "true"); }
            try {
                await fetch(`/api/sessions/${sessionId}/cancel`, { method: "POST" });
                loadHistory();
            } catch (err) {
                console.error("Failed to cancel session scan:", err);
                if (stopBtn) { stopBtn.disabled = false; stopBtn.removeAttribute("aria-busy"); }
            }
        }
```

- [ ] **Step 6: Add Stop link to history table rows for active sessions**

Inside `loadHistory()`, find the `actions` construction:

```javascript
                    const dlLinks = isComplete
                        ? `<a href="/api/sessions/${s.id}/postings/download">Postings</a> | <a href="/api/sessions/${s.id}/news/download">News</a> | `
                        : "";
                    const resumeLink = canResume
                        ? `<a href="#" onclick="resumeSession('${s.id}'); return false;">Resume</a> | `
                        : "";
                    const actions = dlLinks + resumeLink + `<a href="#" onclick="deleteSession('${s.id}'); return false;" style="color:var(--pico-color-red-500)">Delete</a>`;
```

Replace with:

```javascript
                    const dlLinks = isComplete
                        ? `<a href="/api/sessions/${s.id}/postings/download">Postings</a> | <a href="/api/sessions/${s.id}/news/download">News</a> | `
                        : "";
                    const resumeLink = canResume
                        ? `<a href="#" onclick="resumeSession('${s.id}'); return false;">Resume</a> | `
                        : "";
                    const stopLink = isActive && s.status !== "cancelling"
                        ? `<a href="#" onclick="stopSessionById('${s.id}'); return false;" style="color:var(--pico-color-red-500)">Stop</a> | `
                        : isActive && s.status === "cancelling"
                            ? `<span style="color:var(--pico-muted-color); font-size:0.85em;">Stopping…</span> | `
                            : "";
                    const actions = dlLinks + resumeLink + stopLink + `<a href="#" onclick="deleteSession('${s.id}'); return false;" style="color:var(--pico-color-red-500)">Delete</a>`;
```

- [ ] **Step 7: Run all tests**

```bash
python -m pytest tests/ -v 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: add Stop button to session scan progress panel and history table"
```

---

## Task 8: Smoke test

- [ ] **Step 1: Start the server**

```bash
uvicorn app.main:app --reload --port 8000
```

- [ ] **Step 2: Verify cancel endpoints exist**

```bash
curl -s -X POST "http://localhost:8000/api/projects/nonexistent/keyword-scan/cancel" | python -m json.tool
```

Expected: `{"detail": "No running keyword scan found"}` (409 response body).

- [ ] **Step 3: Manual UI test — keyword scan**

1. Go to Keywords tab → click "Run Keyword Scan"
2. While running, click "Stop Scan"
3. Confirm dialog appears — click OK
4. Panel shows "Stopping…" briefly
5. Panel shows "Scan stopped — results discarded"
6. "Run Keyword Scan" button re-enables
7. Check Supabase `keyword_scans` table: `status='cancelled'`, `keyword_scan_result` on `projects` is null

- [ ] **Step 4: Manual UI test — contact scan**

1. Go to Roles tab → click "Launch Contact Scan"
2. While running, click "Stop Contact Scan"
3. Confirm dialog → OK
4. Panel shows "Stopping…" → "Contact scan stopped — results discarded"
5. Launch button re-enables
6. Check `contacts` table: no contacts with the cancelled scan's `contact_scan_id`

- [ ] **Step 5: Manual UI test — session scan**

1. Go to Upload tab → upload a file
2. While processing (names/postings/news stage), click "Stop Scan"
3. Confirm dialog → OK
4. Progress section hides
5. History shows the file with a "■ Stopped" badge
6. Resume button appears — clicking it restarts from name resolution
7. Check DB: `postings` and `news_articles` tables have no rows for that session; `known_names` reset to `[legal_name]`
