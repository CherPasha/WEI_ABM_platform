# Keyword Scan Persistence & Progress Tracking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the in-memory keyword scan job store with Supabase-backed state, add per-company resume checkpoints, and surface live progress in the UI.

**Architecture:** A new `keyword_scans` table (one row per project) owns all scan state. A `keyword_scanned_at` column on `companies` acts as a per-company checkpoint — on server restart, any scan marked `running` is automatically re-enqueued and resumes from the last completed company. The frontend polls `/keyword-scan/status` every 5 seconds while a scan is active instead of tying state to the button click.

**Tech Stack:** FastAPI, Supabase (supabase-py), Python 3.11, PicoCSS + vanilla JS (existing stack)

**Spec:** `docs/superpowers/specs/2026-06-11-keyword-scan-persistence-design.md`

---

## File Map

| File | Change |
|---|---|
| `app/services/keyword_scanner.py` | Add `_is_checkpointed`, update `scan_project_keywords` signature, resume logic, `keyword_scanned_at` write |
| `app/main.py` | Add `_upsert_keyword_scan`, rewrite `_run_scan_task`, update endpoints, add startup recovery, remove `_scan_jobs` |
| `app/templates/project.html` | Add scan status panel, replace `runKeywordScan()`, update `refreshTabStates()` |
| `tests/test_keyword_scan_persistence.py` | Replace trivial byte test with real unit tests |

---

## Task 1: DB Migration

**Files:**
- No code files — run SQL in Supabase dashboard

- [ ] **Step 1: Run the migration SQL in Supabase**

Open your Supabase project → SQL Editor → New query. Paste and run:

```sql
-- 1. Scan state table (one row per project)
create table keyword_scans (
  id               uuid primary key default gen_random_uuid(),
  project_id       uuid not null unique references projects(id) on delete cascade,
  status           text not null,
  started_at       timestamptz not null,
  updated_at       timestamptz not null,
  companies_done   int not null default 0,
  companies_total  int not null default 0,
  error            text
);

-- 2. Per-company checkpoint marker
alter table companies add column keyword_scanned_at timestamptz;
```

- [ ] **Step 2: Verify**

In the Supabase Table Editor, confirm `keyword_scans` appears as a new table and `companies` has a `keyword_scanned_at` column.

---

## Task 2: Add `_is_checkpointed` helper + tests

**Files:**
- Modify: `app/services/keyword_scanner.py`
- Modify: `tests/test_keyword_scan_persistence.py`

- [ ] **Step 1: Replace the trivial test in `tests/test_keyword_scan_persistence.py` with real tests**

Replace the entire file contents:

```python
from datetime import datetime, timezone

import pytest

from app.services.keyword_scanner import _is_checkpointed


def test_is_checkpointed_true_when_one_id_scanned_after_threshold():
    threshold = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    scanned_map = {
        "id1": "2026-06-11T11:00:00+00:00",  # after threshold
        "id2": None,
    }
    assert _is_checkpointed(["id1", "id2"], threshold, scanned_map) is True


def test_is_checkpointed_false_when_all_ids_null():
    threshold = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    scanned_map = {"id1": None, "id2": None}
    assert _is_checkpointed(["id1", "id2"], threshold, scanned_map) is False


def test_is_checkpointed_false_when_scanned_before_threshold():
    threshold = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    scanned_map = {"id1": "2026-06-11T09:00:00+00:00"}  # before threshold
    assert _is_checkpointed(["id1"], threshold, scanned_map) is False


def test_is_checkpointed_false_when_empty_company_ids():
    threshold = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    assert _is_checkpointed([], threshold, {}) is False


def test_is_checkpointed_true_when_scanned_exactly_at_threshold():
    threshold = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    scanned_map = {"id1": "2026-06-11T10:00:00+00:00"}  # equal = checkpointed
    assert _is_checkpointed(["id1"], threshold, scanned_map) is True
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd "/Users/pashache/Desktop/projects/WEI group/ABM/WEI_ABM_platform"
python -m pytest tests/test_keyword_scan_persistence.py -v
```

Expected: `ImportError` or `AttributeError` — `_is_checkpointed` does not exist yet.

- [ ] **Step 3: Add `_is_checkpointed` to `app/services/keyword_scanner.py`**

Add these two lines to the imports at the top of the file (after `import re`):

```python
from datetime import datetime, timezone
from typing import Callable
```

Add this function after the `_extract_sentences` function (around line 27):

```python
def _is_checkpointed(
    company_ids: list[str],
    threshold: datetime,
    scanned_map: dict,
) -> bool:
    """Return True if any company row has keyword_scanned_at >= threshold."""
    cmp_threshold = threshold if threshold.tzinfo else threshold.replace(tzinfo=timezone.utc)
    for cid in company_ids:
        ts_str = scanned_map.get(cid)
        if ts_str is None:
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cmp_threshold:
                return True
        except (ValueError, TypeError):
            continue
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_keyword_scan_persistence.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_keyword_scan_persistence.py app/services/keyword_scanner.py
git commit -m "feat: add _is_checkpointed helper for keyword scan resume"
```

---

## Task 3: Update `scan_project_keywords` with callbacks, resume filtering, and `keyword_scanned_at` write

**Files:**
- Modify: `app/services/keyword_scanner.py`
- Modify: `tests/test_keyword_scan_persistence.py`

- [ ] **Step 1: Add callback + resume tests to `tests/test_keyword_scan_persistence.py`**

Append to the end of the file:

```python
from unittest.mock import patch, MagicMock


def _make_fetch_all_side_effect():
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


def _make_fetch_in_side_effect(checkpoint_map=None):
    """checkpoint_map: {company_id: keyword_scanned_at_str | None}"""
    if checkpoint_map is None:
        checkpoint_map = {"c1": None, "c2": None}

    def fake(table, column, values, select="*"):
        if table == "keywords":
            return [{"id": "k1", "group_id": "g1", "keyword": "AI"}]
        if table == "companies" and column == "session_id":
            return [
                {"id": "c1", "legal_name": "Acme", "inn": "001", "known_names": []},
                {"id": "c2", "legal_name": "Beta", "inn": "002", "known_names": []},
            ]
        if table == "companies" and column == "id" and "keyword_scanned_at" in select:
            return [{"id": cid, "keyword_scanned_at": checkpoint_map.get(cid)} for cid in values]
        if table == "postings":
            return []
        if table == "news_articles":
            return []
        return []
    return fake


@patch("app.services.keyword_scanner.time.sleep", return_value=None)
@patch("app.services.keyword_scanner.supabase")
@patch("app.services.keyword_scanner._fetch_all_in")
@patch("app.services.keyword_scanner._fetch_all")
def test_on_total_known_and_on_company_done_called_for_fresh_scan(
    mock_fetch_all, mock_fetch_in, mock_supa, mock_sleep
):
    """Fresh scan: on_total_known(2) and on_company_done called twice (1, 2)."""
    from app.services.keyword_scanner import scan_project_keywords

    mock_fetch_all.side_effect = _make_fetch_all_side_effect()
    mock_fetch_in.side_effect = _make_fetch_in_side_effect()
    mock_supa.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    started_at = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    total_known = []
    done_counts = []

    scan_project_keywords(
        "proj1",
        started_at,
        on_total_known=lambda t: total_known.append(t),
        on_company_done=lambda d: done_counts.append(d),
    )

    assert total_known == [2]
    assert done_counts == [1, 2]


@patch("app.services.keyword_scanner.time.sleep", return_value=None)
@patch("app.services.keyword_scanner.supabase")
@patch("app.services.keyword_scanner._fetch_all_in")
@patch("app.services.keyword_scanner._fetch_all")
def test_resume_skips_checkpointed_company(
    mock_fetch_all, mock_fetch_in, mock_supa, mock_sleep
):
    """Resume: c1 already checkpointed, only c2 processed; done_count starts at 1."""
    from app.services.keyword_scanner import scan_project_keywords

    mock_fetch_all.side_effect = _make_fetch_all_side_effect()
    # c1 was processed after scan started (10:00), c2 not yet
    mock_fetch_in.side_effect = _make_fetch_in_side_effect({
        "c1": "2026-06-11T11:00:00+00:00",
        "c2": None,
    })
    mock_supa.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    started_at = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    total_known = []
    done_counts = []

    scan_project_keywords(
        "proj1",
        started_at,
        on_total_known=lambda t: total_known.append(t),
        on_company_done=lambda d: done_counts.append(d),
    )

    assert total_known == [2]       # always the full deduped total
    assert done_counts == [2]       # only one company processed, already_done=1 → count starts at 2
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_keyword_scan_persistence.py::test_on_total_known_and_on_company_done_called_for_fresh_scan tests/test_keyword_scan_persistence.py::test_resume_skips_checkpointed_company -v
```

Expected: `TypeError` — `scan_project_keywords` does not accept the new arguments yet.

- [ ] **Step 3: Update `scan_project_keywords` signature in `app/services/keyword_scanner.py`**

Replace the current signature:

```python
def scan_project_keywords(project_id: str) -> dict:
```

With:

```python
def scan_project_keywords(
    project_id: str,
    scan_started_at: datetime,
    on_total_known: Callable[[int], None],
    on_company_done: Callable[[int], None],
) -> dict:
```

- [ ] **Step 4: Add checkpoint fetch + resume filter after the deduplication block**

The deduplication block ends at the line `unique_companies = list(dedup.values())` (around line 175). Insert the following block immediately after it:

```python
    # 4b. Fetch keyword_scanned_at for all company IDs (one batch query for resume detection)
    all_cids_for_checkpoint = [cid for uc in unique_companies for cid in uc["company_ids"]]
    if all_cids_for_checkpoint:
        checkpoint_rows = _fetch_all_in(
            "companies", "id", all_cids_for_checkpoint,
            select="id, keyword_scanned_at"
        )
        scanned_map = {r["id"]: r.get("keyword_scanned_at") for r in checkpoint_rows}
    else:
        scanned_map = {}

    companies_total_unique = len(unique_companies)
    unprocessed = [
        uc for uc in unique_companies
        if not _is_checkpointed(uc["company_ids"], scan_started_at, scanned_map)
    ]
    already_done = companies_total_unique - len(unprocessed)
    on_total_known(companies_total_unique)
```

- [ ] **Step 5: Replace `unique_companies` with `unprocessed` in the processing loop and add `done_count` tracking**

Find the line:
```python
    result_companies = []
    for batch_start in range(0, len(unique_companies), _COMPANY_BATCH):
```

Replace with:
```python
    result_companies = []
    done_count = already_done
    for batch_start in range(0, len(unprocessed), _COMPANY_BATCH):
```

Find the line (inside the batch loop):
```python
        batch = unique_companies[batch_start:batch_start + _COMPANY_BATCH]
        batch_cids = [cid for uc in batch for cid in uc["company_ids"]]
```

Replace with:
```python
        batch = unprocessed[batch_start:batch_start + _COMPANY_BATCH]
        batch_cids = [cid for uc in batch for cid in uc["company_ids"]]
```

Find the `result_companies.append` block at the end of the `for uc in batch:` loop:

```python
            result_companies.append({
                "name": uc["name"],
                "inn": uc["inn"],
                "results": keyword_results,
            })
```

Replace it with:

```python
            result_companies.append({
                "name": uc["name"],
                "inn": uc["inn"],
                "results": keyword_results,
            })
            done_count += 1
            on_company_done(done_count)
```

- [ ] **Step 6: Add `keyword_scanned_at` to the per-company DB write**

Find this block inside the `for cid in uc["company_ids"]:` loop:

```python
                    supabase.table("companies").update({
                        "keyword_hit_count": hit_count,
                        "keyword_group_count": hit_groups,
                    }).eq("id", cid).execute()
```

Replace with:

```python
                    supabase.table("companies").update({
                        "keyword_hit_count": hit_count,
                        "keyword_group_count": hit_groups,
                        "keyword_scanned_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", cid).execute()
```

- [ ] **Step 7: Run all keyword scanner tests**

```bash
python -m pytest tests/test_keyword_scan_persistence.py tests/test_keyword_scanner_hits.py tests/test_keyword_scan_format.py -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add app/services/keyword_scanner.py tests/test_keyword_scan_persistence.py
git commit -m "feat: add callbacks and resume checkpoint to scan_project_keywords"
```

---

## Task 4: Rewrite `_run_scan_task` in `main.py`

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Add missing imports at the top of `app/main.py`**

After the line `import uuid`, add:

```python
import asyncio
from datetime import datetime, timezone
```

- [ ] **Step 2: Remove `_scan_jobs` and `_SCAN_JOB_TTL`**

Find and delete these three lines (around line 799):

```python
# In-memory job store: job_id -> {status, result, error, ts}
_scan_jobs: dict[str, dict] = {}
_SCAN_JOB_TTL = 7200  # seconds before an unclaimed job is discarded (120 min)
```

- [ ] **Step 3: Add `_upsert_keyword_scan` helper**

Directly below the `# ──────────────────────── Keyword Scan ────────────────────────` comment (which is now followed by a blank line), add:

```python
def _upsert_keyword_scan(project_id: str, fields: dict) -> None:
    """Insert or update the keyword_scans row for this project."""
    existing = (
        supabase.table("keyword_scans")
        .select("id")
        .eq("project_id", project_id)
        .execute()
    )
    if existing.data:
        supabase.table("keyword_scans").update(fields).eq("project_id", project_id).execute()
    else:
        supabase.table("keyword_scans").insert({"project_id": project_id, **fields}).execute()
```

- [ ] **Step 4: Replace `_run_scan_task` entirely**

Find and delete the entire current `_run_scan_task` function (lines 804–825). Replace it with:

```python
def _run_scan_task(project_id: str, started_at: datetime | None = None) -> None:
    _logger = logging.getLogger(__name__)
    now = datetime.now(timezone.utc)
    is_resume = started_at is not None

    if not is_resume:
        started_at = now
        _upsert_keyword_scan(project_id, {
            "status": "running",
            "started_at": started_at.isoformat(),
            "updated_at": now.isoformat(),
            "companies_done": 0,
            "companies_total": 0,
            "error": None,
        })
    else:
        _upsert_keyword_scan(project_id, {
            "status": "running",
            "updated_at": now.isoformat(),
        })

    def _on_total_known(total: int) -> None:
        try:
            _upsert_keyword_scan(project_id, {
                "companies_total": total,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            _logger.warning("Failed to update companies_total for project %s", project_id)

    def _on_company_done(done: int) -> None:
        try:
            _upsert_keyword_scan(project_id, {
                "companies_done": done,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            _logger.warning("Failed to update companies_done for project %s", project_id)

    try:
        scan_result = scan_project_keywords(
            project_id,
            started_at,
            _on_total_known,
            _on_company_done,
        )
        buffer = generate_keyword_xlsx(scan_result)
        data = buffer.getvalue()
        try:
            encoded = base64.b64encode(data).decode()
            supabase.table("projects").update(
                {"keyword_scan_result": encoded}
            ).eq("id", project_id).execute()
        except Exception:
            _logger.exception(
                "Failed to persist keyword scan to DB for project %s", project_id
            )
        _upsert_keyword_scan(project_id, {
            "status": "done",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except ValueError as e:
        _upsert_keyword_scan(project_id, {
            "status": "error",
            "error": str(e),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        _logger.exception("Keyword scan failed for project %s", project_id)
        try:
            _upsert_keyword_scan(project_id, {
                "status": "error",
                "error": "Scan failed unexpectedly",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            _logger.exception("Failed to write error status for project %s", project_id)
```

- [ ] **Step 5: Verify the app still imports without error**

```bash
cd "/Users/pashache/Desktop/projects/WEI group/ABM/WEI_ABM_platform"
python -c "from app.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add app/main.py
git commit -m "feat: rewrite _run_scan_task with DB-backed state and resume support"
```

---

## Task 5: Update API endpoints in `main.py`

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Replace `keyword_scan_start` endpoint**

Find and replace the entire `keyword_scan_start` function:

```python
@app.post("/api/projects/{project_id}/keyword-scan/start")
async def keyword_scan_start(project_id: str, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    _scan_jobs[job_id] = {"status": "running", "result": None, "error": None, "ts": time.time()}
    background_tasks.add_task(_run_scan_task, job_id, project_id)
    return {"job_id": job_id}
```

With:

```python
@app.post("/api/projects/{project_id}/keyword-scan/start")
async def keyword_scan_start(project_id: str, background_tasks: BackgroundTasks):
    result = (
        supabase.table("keyword_scans")
        .select("status")
        .eq("project_id", project_id)
        .execute()
    )
    if result.data and result.data[0].get("status") == "running":
        raise HTTPException(status_code=409, detail="Scan already running")
    background_tasks.add_task(_run_scan_task, project_id)
    return {}
```

- [ ] **Step 2: Replace `keyword_scan_db_status` endpoint**

Find and replace the entire `keyword_scan_db_status` function:

```python
@app.get("/api/projects/{project_id}/keyword-scan/status")
async def keyword_scan_db_status(project_id: str):
    """Returns whether a saved keyword scan result exists in the DB."""
    result = (
        supabase.table("projects")
        .select("keyword_scan_result")
        .eq("id", project_id)
        .execute()
    )
    if not result.data:
        return {"has_result": False}
    return {"has_result": result.data[0].get("keyword_scan_result") is not None}
```

With:

```python
@app.get("/api/projects/{project_id}/keyword-scan/status")
async def keyword_scan_db_status(project_id: str):
    """Returns current keyword scan state from keyword_scans table."""
    result = (
        supabase.table("keyword_scans")
        .select("status, started_at, companies_done, companies_total, error")
        .eq("project_id", project_id)
        .execute()
    )
    if not result.data:
        return {
            "status": "none",
            "started_at": None,
            "companies_done": 0,
            "companies_total": 0,
            "error": None,
        }
    row = result.data[0]
    return {
        "status": row["status"],
        "started_at": row.get("started_at"),
        "companies_done": row.get("companies_done", 0),
        "companies_total": row.get("companies_total", 0),
        "error": row.get("error"),
    }
```

- [ ] **Step 3: Remove the two job-id-based endpoints**

Find and delete the entire `keyword_scan_job_status` function:

```python
@app.get("/api/projects/{project_id}/keyword-scan/{job_id}/status")
async def keyword_scan_job_status(project_id: str, job_id: str):
    now = time.time()
    stale = [jid for jid, j in list(_scan_jobs.items()) if now - j["ts"] > _SCAN_JOB_TTL]
    for jid in stale:
        _scan_jobs.pop(jid, None)

    job = _scan_jobs.get(job_id)
    if not job:
        return {"status": "not_found"}
    return {"status": job["status"], "error": job.get("error")}
```

Find and delete the entire `keyword_scan_job_download` function:

```python
@app.get("/api/projects/{project_id}/keyword-scan/{job_id}/download")
async def keyword_scan_job_download(project_id: str, job_id: str):
    job = _scan_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail=f"Job not ready: {job['status']}")
    data = job.pop("result")
    _scan_jobs.pop(job_id, None)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=keyword_analysis_{project_id[:8]}.xlsx"},
    )
```

- [ ] **Step 4: Verify the app still imports**

```bash
python -c "from app.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add app/main.py
git commit -m "feat: replace job-id endpoints with DB-backed keyword scan status API"
```

---

## Task 6: Add startup recovery

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Add startup recovery handler**

Find the existing startup handler at the top of `main.py`:

```python
@app.on_event("startup")
async def _log_config():
```

Add a second startup handler immediately after the closing of `_log_config` (i.e., after its function body ends). The new handler goes right before the first route definition:

```python
@app.on_event("startup")
async def recover_keyword_scans() -> None:
    """Re-enqueue any scan that was running when the server last stopped."""
    try:
        result = (
            supabase.table("keyword_scans")
            .select("project_id, started_at")
            .eq("status", "running")
            .execute()
        )
        loop = asyncio.get_event_loop()
        for row in (result.data or []):
            started_at = datetime.fromisoformat(row["started_at"])
            loop.run_in_executor(None, _run_scan_task, row["project_id"], started_at)
            logging.getLogger(__name__).info(
                "Recovering interrupted keyword scan for project %s", row["project_id"]
            )
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to recover interrupted keyword scans on startup"
        )
```

- [ ] **Step 2: Verify the app still imports**

```bash
python -c "from app.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat: auto-resume interrupted keyword scans on server startup"
```

---

## Task 7: Update frontend

**Files:**
- Modify: `app/templates/project.html`

- [ ] **Step 1: Add scan status panel HTML inside `#panel-keywords`**

Find this block inside `#panel-keywords`:

```html
                    <button id="scan-btn" onclick="runKeywordScan()" class="contrast" style="margin-top:0.5em;">
                        Run Keyword Scan
                    </button>
```

Replace with:

```html
                    <!-- Keyword scan status panel -->
                    <div id="kw-scan-status" style="display:none; margin-bottom:0.75em;">
                        <p id="kw-scan-status-text" style="font-size:0.9em; margin:0 0 0.4em;"></p>
                        <progress id="kw-scan-progress-bar" value="0" max="100" style="width:100%; display:none;"></progress>
                    </div>

                    <button id="scan-btn" onclick="runKeywordScan()" class="contrast" style="margin-top:0.5em;">
                        Run Keyword Scan
                    </button>
```

- [ ] **Step 2: Replace `runKeywordScan()` in the `<script>` block**

Find the entire `runKeywordScan` function (lines 783–822 of the template):

```javascript
        async function runKeywordScan() {
            const btn = document.getElementById("scan-btn");
            btn.setAttribute("aria-busy", "true");
            btn.disabled = true;
            try {
                const startResp = await fetch(`/api/projects/${PROJECT_ID}/keyword-scan/start`, {method: "POST"});
                if (!startResp.ok) {
                    const err = await startResp.json().catch(() => ({}));
                    alert(err.detail || err.error || "Failed to start scan");
                    return;
                }
                const {job_id} = await startResp.json();

                while (true) {
                    await new Promise(r => setTimeout(r, 2000));
                    const statusResp = await fetch(`/api/projects/${PROJECT_ID}/keyword-scan/${job_id}/status`);
                    if (!statusResp.ok) {
                        throw new Error(`Server error ${statusResp.status} while checking scan status — the scan may have caused the server to run out of memory`);
                    }
                    const status = await statusResp.json();
                    if (status.status === "done") break;
                    if (status.status === "error") {
                        alert("Scan failed: " + (status.error || "Unknown error"));
                        return;
                    }
                    if (status.status === "not_found") {
                        alert("Scan job expired — please try again");
                        return;
                    }
                }

                // Scan complete — update tab states and Export tab buttons
                refreshTabStates();
            } catch (err) {
                alert("Scan failed: " + err.message);
            } finally {
                btn.removeAttribute("aria-busy");
                btn.disabled = false;
            }
        }
```

Replace it with:

```javascript
        // ---- Keyword Scan Status ----
        let kwScanPollInterval = null;

        function updateKeywordScanPanel(data) {
            const panel = document.getElementById("kw-scan-status");
            const text  = document.getElementById("kw-scan-status-text");
            const bar   = document.getElementById("kw-scan-progress-bar");
            const btn   = document.getElementById("scan-btn");

            if (data.status === "none") {
                panel.style.display = "none";
                btn.disabled = false;
                btn.removeAttribute("aria-busy");
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
            } else if (data.status === "done") {
                const started = data.started_at ? new Date(data.started_at).toLocaleString() : "";
                text.textContent = `Last scan completed \u2014 started ${started}`;
                bar.style.display = "none";
                btn.disabled = false;
                btn.removeAttribute("aria-busy");
            } else if (data.status === "error") {
                text.style.color = "var(--pico-color-red-500)";
                text.textContent = `Scan failed: ${data.error || "Unknown error"}`;
                bar.style.display = "none";
                btn.disabled = false;
                btn.removeAttribute("aria-busy");
            }
        }

        async function loadKeywordScanStatus() {
            try {
                const resp = await fetch(`/api/projects/${PROJECT_ID}/keyword-scan/status`);
                const data = await resp.json();
                updateKeywordScanPanel(data);
                return data.status;
            } catch (err) {
                console.error("Failed to load keyword scan status:", err);
                return "none";
            }
        }

        function startKeywordScanPolling() {
            if (kwScanPollInterval) clearInterval(kwScanPollInterval);
            kwScanPollInterval = setInterval(async () => {
                const status = await loadKeywordScanStatus();
                if (status === "done" || status === "error") {
                    clearInterval(kwScanPollInterval);
                    kwScanPollInterval = null;
                    refreshTabStates();
                }
            }, 5000);
        }

        async function runKeywordScan() {
            const btn = document.getElementById("scan-btn");
            btn.disabled = true;
            btn.setAttribute("aria-busy", "true");
            try {
                const resp = await fetch(`/api/projects/${PROJECT_ID}/keyword-scan/start`, { method: "POST" });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    alert(err.detail || err.error || "Failed to start scan");
                    btn.disabled = false;
                    btn.removeAttribute("aria-busy");
                    return;
                }
                await loadKeywordScanStatus();
                startKeywordScanPolling();
            } catch (err) {
                alert("Scan failed: " + err.message);
                btn.disabled = false;
                btn.removeAttribute("aria-busy");
            }
        }
```

- [ ] **Step 3: Update `refreshTabStates()` to use the new status field**

Find this line inside `refreshTabStates()`:

```javascript
                setTabState('keywords', scanStatus.has_result ? 'green' : hasKeywords ? 'yellow' : '');
```

Replace with:

```javascript
                setTabState('keywords', scanStatus.status === 'done' ? 'green' : hasKeywords ? 'yellow' : '');
```

Find these two occurrences (both in `updateExportButtons`):

```javascript
            if (scanStatus.has_result) {
```

Replace both with:

```javascript
            if (scanStatus.status === 'done') {
```

- [ ] **Step 4: Add page-load initialization for keyword scan**

Find this block near the bottom of the `<script>` tag:

```javascript
        // Initialize contact scan state on page load
        fetch(`/api/projects/${PROJECT_ID}/contact-scan/latest/status`)
```

Add the following block immediately before it:

```javascript
        // Initialize keyword scan state on page load
        loadKeywordScanStatus().then(status => {
            if (status === "running") startKeywordScanPolling();
        });

```

- [ ] **Step 5: Run the existing test suite to confirm nothing is broken**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: add keyword scan status panel with live progress polling"
```

---

## Task 8: Smoke test end-to-end

- [ ] **Step 1: Start the dev server**

```bash
uvicorn app.main:app --reload --port 8000
```

- [ ] **Step 2: Verify the status endpoint returns `"none"` for a project with no scan**

```bash
curl -s "http://localhost:8000/api/projects/<your-project-id>/keyword-scan/status" | python -m json.tool
```

Expected:
```json
{
    "status": "none",
    "started_at": null,
    "companies_done": 0,
    "companies_total": 0,
    "error": null
}
```

- [ ] **Step 3: Verify the UI**

Open `http://localhost:8000/projects/<your-project-id>` → Keywords tab. Confirm:
- No status panel visible before a scan starts
- "Run Keyword Scan" button is enabled
- After clicking "Run Keyword Scan", the status panel appears with "Scan running" and a progress counter
- Progress counter updates every 5 seconds without page reload
- After scan completes, panel shows "Last scan completed — started …" and button re-enables

- [ ] **Step 4: Verify restart recovery**

Start a scan, then stop the uvicorn process (`Ctrl+C`) before it finishes. Check the `keyword_scans` table in Supabase — `status` should be `running`. Restart the server. Confirm in the logs that it logs "Recovering interrupted keyword scan for project …" and the scan resumes from where it left off.
