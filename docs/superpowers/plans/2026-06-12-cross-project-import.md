# Cross-Project Session Import — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow Project 2 to import a completed session from Project 1 as a reference — ghost companies hold only metadata, all postings/news are fetched from the source at query time.

**Architecture:** Two new columns on `sessions` (`type`, `source_session_id`, `source_project_name`, `source_session_filename`) and one on `companies` (`source_company_id`) form the reference chain. The keyword scanner resolves each company's effective ID before fetching postings/news. Download endpoints proxy to the source session. Contact scan results are stored against ghost company IDs (owned by Project 2). No postings/news rows are ever copied.

**Tech Stack:** FastAPI, Supabase PostgreSQL via REST SDK, Jinja2, Pico CSS 2, vanilla JS, pytest + unittest.mock.

---

## File Map

| File | Change |
|---|---|
| Supabase SQL (run in dashboard) | Add 5 new columns |
| `app/models.py` | Add `ImportSession` model |
| `app/main.py` | Add 5 new endpoints; modify 3 existing endpoints |
| `app/services/keyword_scanner.py` | Add `source_company_id` to select + effective ID routing |
| `app/templates/project.html` | Import modal, imported row rendering, warning banner, session delete guard |
| `app/templates/projects.html` | Project delete guard |
| `tests/test_cross_project_import.py` | Create — backend unit tests |

---

## Task 1: Database Schema Migration

**Files:**
- Run SQL in Supabase dashboard

- [ ] **Step 1: Run the migration SQL in Supabase SQL editor**

```sql
ALTER TABLE sessions
  ADD COLUMN IF NOT EXISTS type TEXT DEFAULT 'normal',
  ADD COLUMN IF NOT EXISTS source_session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS source_project_name TEXT,
  ADD COLUMN IF NOT EXISTS source_session_filename TEXT;

ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS source_company_id UUID REFERENCES companies(id) ON DELETE SET NULL;
```

- [ ] **Step 2: Verify columns exist**

In the Supabase dashboard Table Editor, check that `sessions` has `type`, `source_session_id`, `source_project_name`, `source_session_filename`, and `companies` has `source_company_id`. All nullable except `type` (defaults to `'normal'`).

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: add schema columns for cross-project session import"
```

---

## Task 2: Add `ImportSession` Model + Update `list_sessions` Select

**Files:**
- Modify: `app/models.py`
- Modify: `app/main.py:290-302`

- [ ] **Step 1: Add `ImportSession` to models.py**

Read `app/models.py`, then add after the last model:

```python
class ImportSession(BaseModel):
    source_session_id: str
```

- [ ] **Step 2: Update `list_sessions` to include new session fields**

In `app/main.py`, find the `list_sessions` function (around line 290). Replace:

```python
        .select("id, filename, status, total_companies, names_done, postings_done, news_done, created_at")
```

with:

```python
        .select("id, filename, status, total_companies, names_done, postings_done, news_done, created_at, type, source_session_id, source_project_name, source_session_filename")
```

- [ ] **Step 3: Add `ImportSession` to the import line in main.py**

Find the models import line near the top of `app/main.py`:

```python
from app.models import CreateKeywordGroup, RenameKeywordGroup, CreateKeyword, CreateStopWord, UpdateProject, ContactScanSettings
```

Replace with:

```python
from app.models import CreateKeywordGroup, RenameKeywordGroup, CreateKeyword, CreateStopWord, UpdateProject, ContactScanSettings, ImportSession
```

- [ ] **Step 4: Commit**

```bash
git add app/models.py app/main.py
git commit -m "feat: add ImportSession model and expose import fields in list_sessions"
```

---

## Task 3: Import Endpoint + Completed Sessions Endpoint

**Files:**
- Modify: `app/main.py` — add 2 new endpoints in the Sessions section (~line 287)
- Create: `tests/test_cross_project_import.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cross_project_import.py`:

```python
from unittest.mock import patch, MagicMock, call
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _mock_sb():
    """Return a fresh MagicMock that chains .table().select().eq()... naturally."""
    m = MagicMock()
    # Make every method return the same mock so chains work
    m.table.return_value = m
    m.select.return_value = m
    m.eq.return_value = m
    m.neq.return_value = m
    m.in_.return_value = m
    m.order.return_value = m
    m.range.return_value = m
    m.insert.return_value = m
    m.update.return_value = m
    m.delete.return_value = m
    m.not_ = m
    m.execute.return_value = MagicMock(data=[])
    return m


# ──────────────── import endpoint ────────────────

def test_import_session_success():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"id": "src-sess", "project_id": "proj-1", "filename": "a.xlsx", "status": "completed", "total_companies": 2}]),  # source session lookup
        MagicMock(data=[]),                                                               # duplicate guard
        MagicMock(data=[{"name": "Project One"}]),                                        # source project name
        MagicMock(data=[{"id": "new-sess", "type": "imported", "project_id": "proj-2"}]), # session insert
        MagicMock(data=[{"id": "c1", "legal_name": "A", "inn": "1", "kpp": None, "ogrn": None, "website_url": None, "ceo_name": None, "revenue": None, "known_names": []},
                        {"id": "c2", "legal_name": "B", "inn": "2", "kpp": None, "ogrn": None, "website_url": None, "ceo_name": None, "revenue": None, "known_names": []}]),  # companies page 1
        MagicMock(data=[]),                                                               # companies insert
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.post("/api/projects/proj-2/sessions/import",
                           json={"source_session_id": "src-sess"})
    assert resp.status_code == 200
    assert resp.json()["type"] == "imported"


def test_import_session_same_project_rejected():
    sb = _mock_sb()
    sb.execute.return_value = MagicMock(data=[{"id": "src-sess", "project_id": "proj-1", "filename": "a.xlsx", "status": "completed", "total_companies": 2}])

    with patch("app.main.supabase", sb):
        resp = client.post("/api/projects/proj-1/sessions/import",
                           json={"source_session_id": "src-sess"})
    assert resp.status_code == 400
    assert "same project" in resp.json()["detail"].lower()


def test_import_session_not_completed_rejected():
    sb = _mock_sb()
    sb.execute.return_value = MagicMock(data=[{"id": "src-sess", "project_id": "proj-1", "filename": "a.xlsx", "status": "processing", "total_companies": 0}])

    with patch("app.main.supabase", sb):
        resp = client.post("/api/projects/proj-2/sessions/import",
                           json={"source_session_id": "src-sess"})
    assert resp.status_code == 400
    assert "completed" in resp.json()["detail"].lower()


def test_import_session_duplicate_rejected():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"id": "src-sess", "project_id": "proj-1", "filename": "a.xlsx", "status": "completed", "total_companies": 1}]),  # source session
        MagicMock(data=[{"id": "existing"}]),  # duplicate guard — already imported
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.post("/api/projects/proj-2/sessions/import",
                           json={"source_session_id": "src-sess"})
    assert resp.status_code == 409


def test_import_session_source_not_found():
    sb = _mock_sb()
    sb.execute.return_value = MagicMock(data=[])

    with patch("app.main.supabase", sb):
        resp = client.post("/api/projects/proj-2/sessions/import",
                           json={"source_session_id": "nonexistent"})
    assert resp.status_code == 404


# ──────────────── completed sessions endpoint ────────────────

def test_completed_sessions_lists_only_completed():
    sb = _mock_sb()
    sb.execute.return_value = MagicMock(data=[
        {"id": "s1", "filename": "f.xlsx", "created_at": "2026-01-01", "total_companies": 5}
    ])

    with patch("app.main.supabase", sb):
        resp = client.get("/api/projects/proj-1/sessions/completed")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "s1"


def test_completed_sessions_excludes_already_imported():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[
            {"id": "s1", "filename": "f.xlsx", "created_at": "2026-01-01", "total_companies": 5},
            {"id": "s2", "filename": "g.xlsx", "created_at": "2026-01-02", "total_companies": 3},
        ]),  # completed sessions in source project
        MagicMock(data=[{"source_session_id": "s1"}]),  # already imported in target project
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.get("/api/projects/proj-1/sessions/completed?importing_into=proj-2")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert "s1" not in ids
    assert "s2" in ids
```

- [ ] **Step 2: Run tests — verify they all fail**

```bash
cd "/Users/pashache/Desktop/projects/WEI group/ABM/WEI_ABM_platform"
python -m pytest tests/test_cross_project_import.py -v 2>&1 | head -40
```

Expected: all tests FAIL with `404` or import errors.

- [ ] **Step 3: Add the two new endpoints to app/main.py**

In `app/main.py`, in the `# ── Upload (scoped to project) ──` section (around line 255), add these two endpoints **before** the existing `/sessions/upload` endpoint:

```python
@app.get("/api/projects/{project_id}/sessions/completed")
async def list_completed_sessions(project_id: str, importing_into: Optional[str] = None):
    result = (
        supabase.table("sessions")
        .select("id, filename, created_at, total_companies")
        .eq("project_id", project_id)
        .eq("status", "completed")
        .order("created_at", desc=True)
        .execute()
    )
    sessions = result.data or []

    if importing_into:
        already = (
            supabase.table("sessions")
            .select("source_session_id")
            .eq("project_id", importing_into)
            .eq("type", "imported")
            .execute()
        )
        imported_ids = {r["source_session_id"] for r in (already.data or []) if r.get("source_session_id")}
        sessions = [s for s in sessions if s["id"] not in imported_ids]

    return sessions


@app.post("/api/projects/{project_id}/sessions/import")
async def import_session(project_id: str, body: ImportSession):
    # 1. Validate source session
    src_result = (
        supabase.table("sessions")
        .select("id, project_id, filename, status, total_companies")
        .eq("id", body.source_session_id)
        .execute()
    )
    if not src_result.data:
        raise HTTPException(status_code=404, detail="Source session not found")
    src = src_result.data[0]

    if src["project_id"] == project_id:
        raise HTTPException(status_code=400, detail="Cannot import from the same project")

    if src["status"] != "completed":
        raise HTTPException(status_code=400, detail="Source session must be completed")

    # 2. Duplicate guard
    dup = (
        supabase.table("sessions")
        .select("id")
        .eq("project_id", project_id)
        .eq("source_session_id", body.source_session_id)
        .execute()
    )
    if dup.data:
        raise HTTPException(status_code=409, detail="This session is already imported into this project")

    # 3. Snapshot source project name
    proj_result = (
        supabase.table("projects")
        .select("name")
        .eq("id", src["project_id"])
        .execute()
    )
    source_project_name = proj_result.data[0]["name"] if proj_result.data else "Unknown"

    # 4. Create imported session
    imported = supabase.table("sessions").insert({
        "project_id": project_id,
        "filename": src["filename"],
        "status": "completed",
        "type": "imported",
        "source_session_id": body.source_session_id,
        "source_project_name": source_project_name,
        "source_session_filename": src["filename"],
        "total_companies": src["total_companies"],
    }).execute()
    imported_session_id = imported.data[0]["id"]

    # 5. Batch-copy company metadata (no postings/news)
    all_source_companies: list[dict] = []
    offset = 0
    while True:
        rows = (
            supabase.table("companies")
            .select("id, legal_name, inn, kpp, ogrn, website_url, ceo_name, revenue, known_names")
            .eq("session_id", body.source_session_id)
            .range(offset, offset + 999)
            .execute()
        ).data
        all_source_companies.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000

    batch_size = 200
    for i in range(0, len(all_source_companies), batch_size):
        batch = all_source_companies[i:i + batch_size]
        supabase.table("companies").insert([
            {
                "session_id": imported_session_id,
                "source_company_id": c["id"],
                "legal_name": c.get("legal_name"),
                "inn": c.get("inn"),
                "kpp": c.get("kpp"),
                "ogrn": c.get("ogrn"),
                "website_url": c.get("website_url"),
                "ceo_name": c.get("ceo_name"),
                "revenue": c.get("revenue"),
                "known_names": c.get("known_names"),
            }
            for c in batch
        ]).execute()

    return imported.data[0]
```

Also add `Optional` to the imports at the top of `main.py` if not already present. Find the existing `from typing import` line and ensure `Optional` is included.

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_cross_project_import.py::test_import_session_success tests/test_cross_project_import.py::test_import_session_same_project_rejected tests/test_cross_project_import.py::test_import_session_not_completed_rejected tests/test_cross_project_import.py::test_import_session_duplicate_rejected tests/test_cross_project_import.py::test_import_session_source_not_found tests/test_cross_project_import.py::test_completed_sessions_lists_only_completed tests/test_cross_project_import.py::test_completed_sessions_excludes_already_imported -v
```

Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/models.py tests/test_cross_project_import.py
git commit -m "feat: add import session and completed sessions endpoints"
```

---

## Task 4: Dependents Endpoints + Deletion Guard

**Files:**
- Modify: `app/main.py` — 2 new GET endpoints, modify 2 DELETE endpoints
- Modify: `tests/test_cross_project_import.py` — add tests

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cross_project_import.py`:

```python
# ──────────────── dependents endpoints ────────────────

def test_session_dependents_returns_list():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"id": "imp-sess", "project_id": "proj-2", "source_project_name": "P2", "source_session_filename": "a.xlsx"}]),  # dependents query
        MagicMock(data=[{"name": "Project Two"}]),  # project name lookup
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.get("/api/sessions/src-sess/dependents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["project_name"] == "Project Two"


def test_session_dependents_returns_empty():
    sb = _mock_sb()
    sb.execute.return_value = MagicMock(data=[])

    with patch("app.main.supabase", sb):
        resp = client.get("/api/sessions/src-sess/dependents")
    assert resp.status_code == 200
    assert resp.json() == []


def test_delete_session_blocked_when_dependents():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"project_id": "proj-2"}]),  # dependents exist
        MagicMock(data=[{"name": "Project Two"}]),   # project name
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.delete("/api/sessions/src-sess")
    assert resp.status_code == 409


def test_delete_session_force_bypasses_guard():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"id": "src-sess"}]),  # delete result
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.delete("/api/sessions/src-sess?force=true")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_delete_project_blocked_when_dependents():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"id": "s1"}]),              # project sessions
        MagicMock(data=[{"project_id": "proj-2"}]),  # dependents for s1
        MagicMock(data=[{"name": "Project Two"}]),   # dependent project name
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.delete("/api/projects/proj-1")
    assert resp.status_code == 409
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_cross_project_import.py::test_session_dependents_returns_list tests/test_cross_project_import.py::test_session_dependents_returns_empty tests/test_cross_project_import.py::test_delete_session_blocked_when_dependents tests/test_cross_project_import.py::test_delete_session_force_bypasses_guard tests/test_cross_project_import.py::test_delete_project_blocked_when_dependents -v 2>&1 | tail -20
```

Expected: FAIL.

- [ ] **Step 3: Add dependents endpoints to app/main.py**

In `app/main.py`, in the `# ── Sessions (scoped to project) ──` section, add these two endpoints after `list_sessions`:

```python
@app.get("/api/sessions/{session_id}/dependents")
async def session_dependents(session_id: str):
    result = (
        supabase.table("sessions")
        .select("id, project_id, source_project_name, source_session_filename")
        .eq("source_session_id", session_id)
        .execute()
    )
    dependents = []
    for row in (result.data or []):
        proj = supabase.table("projects").select("name").eq("id", row["project_id"]).execute()
        project_name = proj.data[0]["name"] if proj.data else row.get("source_project_name", "Unknown")
        dependents.append({
            "project_name": project_name,
            "session_filename": row.get("source_session_filename", ""),
        })
    return dependents


@app.get("/api/projects/{project_id}/dependents")
async def project_dependents(project_id: str):
    sessions_result = (
        supabase.table("sessions")
        .select("id, filename")
        .eq("project_id", project_id)
        .execute()
    )
    session_ids = [s["id"] for s in (sessions_result.data or [])]
    if not session_ids:
        return []

    all_dependents = []
    for sid in session_ids:
        src_filename = next((s["filename"] for s in (sessions_result.data or []) if s["id"] == sid), "")
        deps = (
            supabase.table("sessions")
            .select("project_id, source_session_filename")
            .eq("source_session_id", sid)
            .execute()
        )
        for row in (deps.data or []):
            proj = supabase.table("projects").select("name").eq("id", row["project_id"]).execute()
            project_name = proj.data[0]["name"] if proj.data else "Unknown"
            all_dependents.append({
                "project_name": project_name,
                "source_session_filename": src_filename,
                "session_filename": row.get("source_session_filename", ""),
            })
    return all_dependents
```

- [ ] **Step 4: Modify `delete_session` to check dependents**

Find the existing `delete_session` endpoint in `app/main.py` (around line 370):

```python
@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    result = supabase.table("sessions").delete().eq("id", session_id).execute()
    if not result.data:
        return {"error": "Session not found"}
    return {"status": "ok"}
```

Replace with:

```python
@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, force: bool = False):
    if not force:
        deps = (
            supabase.table("sessions")
            .select("project_id")
            .eq("source_session_id", session_id)
            .execute()
        )
        if deps.data:
            project_ids = list({r["project_id"] for r in deps.data})
            names = []
            for pid in project_ids:
                p = supabase.table("projects").select("name").eq("id", pid).execute()
                if p.data:
                    names.append(p.data[0]["name"])
            raise HTTPException(
                status_code=409,
                detail={"message": "Session is imported by other projects", "projects": names},
            )
    result = supabase.table("sessions").delete().eq("id", session_id).execute()
    if not result.data:
        return {"error": "Session not found"}
    return {"status": "ok"}
```

- [ ] **Step 5: Modify `delete_project` to check dependents**

Find the existing `delete_project` endpoint in `app/main.py`:

```python
@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    result = supabase.table("projects").delete().eq("id", project_id).execute()
    if not result.data:
        return {"error": "Project not found"}
    return {"status": "ok"}
```

Replace with:

```python
@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, force: bool = False):
    if not force:
        sessions = supabase.table("sessions").select("id").eq("project_id", project_id).execute()
        session_ids = [s["id"] for s in (sessions.data or [])]
        dep_project_ids: set[str] = set()
        for sid in session_ids:
            deps = (
                supabase.table("sessions")
                .select("project_id")
                .eq("source_session_id", sid)
                .execute()
            )
            for row in (deps.data or []):
                if row["project_id"] != project_id:
                    dep_project_ids.add(row["project_id"])
        if dep_project_ids:
            names = []
            for pid in dep_project_ids:
                p = supabase.table("projects").select("name").eq("id", pid).execute()
                if p.data:
                    names.append(p.data[0]["name"])
            raise HTTPException(
                status_code=409,
                detail={"message": "Project sessions are imported by other projects", "projects": names},
            )
    result = supabase.table("projects").delete().eq("id", project_id).execute()
    if not result.data:
        return {"error": "Project not found"}
    return {"status": "ok"}
```

- [ ] **Step 6: Run tests — verify they pass**

```bash
python -m pytest tests/test_cross_project_import.py -v 2>&1 | tail -20
```

Expected: all 12 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/main.py tests/test_cross_project_import.py
git commit -m "feat: add dependents endpoints and deletion guard for imported sessions"
```

---

## Task 5: Download Proxy for Imported Sessions

**Files:**
- Modify: `app/main.py` — `download_postings` and `download_news` functions
- Modify: `tests/test_cross_project_import.py` — add tests

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cross_project_import.py`:

```python
# ──────────────── download proxy ────────────────

def test_download_postings_proxies_for_imported_session():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"type": "imported", "source_session_id": "src-sess"}]),  # session type check
        # _query_all_rows calls: page 1 returns data, page 2 returns empty
        MagicMock(data=[{"id": "p1", "company_id": "c1", "title": "Dev", "session_id": "src-sess"}]),
        MagicMock(data=[]),
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.get("/api/sessions/imp-sess/postings/download")
    assert resp.status_code == 200
    assert "spreadsheet" in resp.headers["content-type"]


def test_download_postings_returns_410_for_broken_reference():
    sb = _mock_sb()
    sb.execute.return_value = MagicMock(data=[{"type": "imported", "source_session_id": None}])

    with patch("app.main.supabase", sb):
        resp = client.get("/api/sessions/imp-sess/postings/download")
    assert resp.status_code == 410


def test_download_news_proxies_for_imported_session():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"type": "imported", "source_session_id": "src-sess"}]),  # session type check
        MagicMock(data=[{"id": "n1", "company_id": "c1", "title": "News", "session_id": "src-sess", "url": "http://x.com", "snippet": "x", "full_text": "x", "published_at": None}]),
        MagicMock(data=[]),  # pagination done
        MagicMock(data=[{"id": "c1", "legal_name": "A"}]),  # companies for news
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.get("/api/sessions/imp-sess/news/download")
    assert resp.status_code == 200
    assert "spreadsheet" in resp.headers["content-type"]
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_cross_project_import.py::test_download_postings_proxies_for_imported_session tests/test_cross_project_import.py::test_download_postings_returns_410_for_broken_reference tests/test_cross_project_import.py::test_download_news_proxies_for_imported_session -v 2>&1 | tail -15
```

Expected: FAIL.

- [ ] **Step 3: Modify `download_postings` in app/main.py**

Find `download_postings` (around line 404). Add a proxy block at the start of the function body, before the `rows = _query_all_rows(...)` call:

```python
@app.get("/api/sessions/{session_id}/postings/download")
async def download_postings(session_id: str):
    # Proxy imported sessions to their source
    sess = supabase.table("sessions").select("type, source_session_id").eq("id", session_id).execute()
    if sess.data and sess.data[0].get("type") == "imported":
        src_id = sess.data[0].get("source_session_id")
        if not src_id:
            raise HTTPException(status_code=410, detail="Source session has been deleted")
        session_id = src_id

    rows = _query_all_rows("postings", session_id)
    # ... rest of function unchanged
```

- [ ] **Step 4: Modify `download_news` in app/main.py**

Find `download_news` (around line 426). Add the same proxy block at the start:

```python
@app.get("/api/sessions/{session_id}/news/download")
async def download_news(session_id: str):
    # Proxy imported sessions to their source
    sess = supabase.table("sessions").select("type, source_session_id").eq("id", session_id).execute()
    if sess.data and sess.data[0].get("type") == "imported":
        src_id = sess.data[0].get("source_session_id")
        if not src_id:
            raise HTTPException(status_code=410, detail="Source session has been deleted")
        session_id = src_id

    rows = _query_all_rows("news_articles", session_id)
    # ... rest of function unchanged
```

- [ ] **Step 5: Run all tests**

```bash
python -m pytest tests/test_cross_project_import.py -v 2>&1 | tail -25
```

Expected: all 15 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_cross_project_import.py
git commit -m "feat: proxy postings/news downloads for imported sessions"
```

---

## Task 6: Keyword Scanner — Ghost Company Support

**Files:**
- Modify: `app/services/keyword_scanner.py:193-270`
- Modify: `tests/test_cross_project_import.py` — add tests

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cross_project_import.py`:

```python
# ──────────────── keyword scanner ────────────────

def test_keyword_scanner_uses_source_company_id_for_ghost():
    """Ghost company (source_company_id set) fetches postings by source ID, not its own."""
    from app.services.keyword_scanner import scan_project_keywords
    from datetime import datetime, timezone

    sb = _mock_sb()

    ghost_id = "ghost-c1"
    source_id = "source-c1"

    call_seq = iter([
        # keyword_groups
        MagicMock(data=[{"id": "g1", "name": "Tech"}]),
        # keywords
        MagicMock(data=[{"id": "k1", "group_id": "g1", "keyword": "Python"}]),
        # stop_words
        MagicMock(data=[]),
        # sessions for project
        MagicMock(data=[{"id": "imported-sess"}]),
        # companies for session — ghost company with source_company_id set
        MagicMock(data=[{"id": ghost_id, "legal_name": "Acme", "inn": "123", "known_names": ["Acme"], "source_company_id": source_id}]),
        # keyword_scanned_at checkpoint
        MagicMock(data=[{"id": ghost_id, "keyword_scanned_at": None}]),
        # postings fetched by source_id (effective) — has a Python match
        MagicMock(data=[{"company_id": source_id, "title": "Python Developer", "snippet_requirement": "Python", "snippet_responsibility": ""}]),
        # news fetched by source_id (effective)
        MagicMock(data=[]),
        # update company (checkpoint write) — we don't care about result
        MagicMock(data=[{"id": ghost_id}]),
    ])
    sb.execute.side_effect = lambda: next(call_seq)

    with patch("app.services.keyword_scanner.supabase", sb):
        result = scan_project_keywords(
            "proj-2",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            lambda n: None,
            lambda n: None,
        )

    companies = result["companies"]
    assert len(companies) == 1
    assert companies[0]["inn"] == "123"
    assert companies[0]["results"]["Python"]["count"] > 0
```

- [ ] **Step 2: Run test — verify it fails**

```bash
python -m pytest tests/test_cross_project_import.py::test_keyword_scanner_uses_source_company_id_for_ghost -v 2>&1 | tail -15
```

Expected: FAIL (count is 0 because ghost company ID has no postings).

- [ ] **Step 3: Apply the three-part change to scan_project_keywords in keyword_scanner.py**

Read `app/services/keyword_scanner.py` lines 193–270 first, then make these three replacements:

**Change 1** — line 196, add `source_company_id` to select:

```python
    all_companies = _fetch_all_in(
        "companies", "session_id", session_ids,
        select="id, legal_name, inn, known_names, source_company_id"
    )
```

**Change 2** — lines 200–208, add `effective_ids` tracking to dedup:

```python
    dedup: dict[str, dict] = {}
    for c in all_companies:
        inn = (c.get("inn") or "").strip()
        legal_name = c.get("legal_name", "")
        key = f"inn:{inn}" if inn else f"name:{legal_name.strip().lower()}"

        if key not in dedup:
            dedup[key] = {"name": legal_name, "inn": inn, "company_ids": [], "effective_ids": {}}
        cid = c["id"]
        dedup[key]["company_ids"].append(cid)
        # Ghost companies (source_company_id set) fetch postings/news by source ID
        dedup[key]["effective_ids"][cid] = c.get("source_company_id") or cid
```

**Change 3** — lines 243–269, use effective IDs for postings/news fetching:

```python
        batch_cids = [cid for uc in batch for cid in uc["company_ids"]]
        # Use effective IDs (source_company_id for ghost companies) when querying postings/news
        effective_fetch_ids = list({uc["effective_ids"][cid] for uc in batch for cid in uc["company_ids"]})

        batch_postings = _fetch_all_in(
            "postings", "company_id", effective_fetch_ids,
            select="company_id, title, snippet_requirement, snippet_responsibility"
        )
        postings_by_company: dict[str, list] = {}
        for p in batch_postings:
            postings_by_company.setdefault(p["company_id"], []).append(p)

        batch_news = _fetch_all_in(
            "news_articles", "company_id", effective_fetch_ids,
            select="company_id, title, snippet, full_text"
        )
        news_by_company: dict[str, list] = {}
        for a in batch_news:
            news_by_company.setdefault(a["company_id"], []).append(a)

        # 6. For each company x keyword, search postings and news
        for uc in batch:
            time.sleep(0)
            if is_cancelled():
                raise ScanCancelledError()
            company_postings = []
            company_news = []
            for cid in uc["company_ids"]:
                effective_id = uc["effective_ids"][cid]
                company_postings.extend(postings_by_company.get(effective_id, []))
                company_news.extend(news_by_company.get(effective_id, []))
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/test_cross_project_import.py -v 2>&1 | tail -25
```

Expected: all 16 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/keyword_scanner.py tests/test_cross_project_import.py
git commit -m "feat: route keyword scanner postings/news through source_company_id for ghost companies"
```

---

## Task 7: Frontend — Import Button + Modal

**Files:**
- Modify: `app/templates/project.html`

- [ ] **Step 1: Add the import button and modal HTML to project.html**

In `project.html`, find the closing `</article>` of the Upload Section (after the `<p id="error-msg"></p>` line, around line 193). Add this block immediately after it:

```html
                <!-- Import from project -->
                <article>
                    <h3>Import from Another Project</h3>
                    <p style="font-size:0.85em; color:var(--pico-muted-color); margin-bottom:0.75em;">
                        Reference companies (with their postings and news) from a completed session in another project. Keywords and roles are not imported.
                    </p>
                    <button class="secondary" onclick="openImportModal()">Import from project</button>
                </article>

                <!-- Import modal -->
                <dialog id="import-modal">
                    <article>
                        <header>
                            <button aria-label="Close" rel="prev" onclick="closeImportModal()"></button>
                            <h3>Import from Another Project</h3>
                        </header>
                        <div id="import-step-1">
                            <label for="import-project-select">Select source project:</label>
                            <select id="import-project-select" onchange="loadImportSessions()">
                                <option value="">-- Choose a project --</option>
                            </select>
                        </div>
                        <div id="import-step-2" style="display:none; margin-top:1em;">
                            <label for="import-session-select">Select session:</label>
                            <select id="import-session-select"></select>
                        </div>
                        <p id="import-modal-error" style="color:var(--pico-color-red-500); display:none; margin-top:0.75em;"></p>
                        <footer>
                            <button class="secondary" onclick="closeImportModal()">Cancel</button>
                            <button id="import-confirm-btn" onclick="confirmImport()" disabled>Import</button>
                        </footer>
                    </article>
                </dialog>
```

- [ ] **Step 2: Add the import modal JavaScript to project.html**

Find the `// Load on page load` comment near the bottom of the `<script>` block. Add these functions before it:

```javascript
        // ---- Import from project ----
        async function openImportModal() {
            const errorEl = document.getElementById("import-modal-error");
            const step2   = document.getElementById("import-step-2");
            const confirmBtn = document.getElementById("import-confirm-btn");
            errorEl.style.display = "none";
            step2.style.display = "none";
            confirmBtn.disabled = true;

            const projectSelect = document.getElementById("import-project-select");
            projectSelect.value = "";

            try {
                const resp = await fetch("/api/projects");
                const projects = await resp.json();
                const others = projects.filter(p => p.id !== PROJECT_ID);
                projectSelect.innerHTML = '<option value="">-- Choose a project --</option>' +
                    others.map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("");
            } catch (err) {
                projectSelect.innerHTML = '<option value="">Failed to load projects</option>';
            }

            document.getElementById("import-modal").showModal();
        }

        function closeImportModal() {
            document.getElementById("import-modal").close();
        }

        async function loadImportSessions() {
            const sourceProjectId = document.getElementById("import-project-select").value;
            const step2      = document.getElementById("import-step-2");
            const confirmBtn = document.getElementById("import-confirm-btn");

            if (!sourceProjectId) {
                step2.style.display = "none";
                confirmBtn.disabled = true;
                return;
            }

            try {
                const resp = await fetch(`/api/projects/${sourceProjectId}/sessions/completed?importing_into=${PROJECT_ID}`);
                const sessions = await resp.json();
                const select = document.getElementById("import-session-select");

                if (!sessions.length) {
                    select.innerHTML = '<option value="">No eligible sessions in this project</option>';
                    confirmBtn.disabled = true;
                } else {
                    select.innerHTML = sessions.map(s => {
                        const date = s.created_at ? new Date(s.created_at).toLocaleDateString() : "";
                        return `<option value="${s.id}">${escapeHtml(s.filename)} — ${s.total_companies || 0} companies (${date})</option>`;
                    }).join("");
                    confirmBtn.disabled = false;
                }
                step2.style.display = "";
            } catch (err) {
                console.error("Failed to load sessions:", err);
            }
        }

        async function confirmImport() {
            const sessionId  = document.getElementById("import-session-select").value;
            if (!sessionId) return;

            const btn     = document.getElementById("import-confirm-btn");
            const errorEl = document.getElementById("import-modal-error");
            btn.disabled  = true;
            btn.setAttribute("aria-busy", "true");
            errorEl.style.display = "none";

            try {
                const resp = await fetch(`/api/projects/${PROJECT_ID}/sessions/import`, {
                    method:  "POST",
                    headers: { "Content-Type": "application/json" },
                    body:    JSON.stringify({ source_session_id: sessionId }),
                });
                if (!resp.ok) {
                    const data = await resp.json().catch(() => ({}));
                    errorEl.textContent = data.detail || "Import failed";
                    errorEl.style.display = "block";
                    btn.disabled = false;
                    btn.removeAttribute("aria-busy");
                    return;
                }
                closeImportModal();
                loadHistory();
                refreshTabStates();
            } catch (err) {
                errorEl.textContent = "Error: " + err.message;
                errorEl.style.display = "block";
                btn.disabled = false;
                btn.removeAttribute("aria-busy");
            }
        }
```

- [ ] **Step 3: Smoke test manually**

Start the server (`uvicorn app.main:app --reload`), open a project, and verify the "Import from project" button appears and opens a modal with a project dropdown.

- [ ] **Step 4: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: add import from project button and modal to Upload tab"
```

---

## Task 8: Frontend — Imported Session Row Rendering

**Files:**
- Modify: `app/templates/project.html` — `loadHistory` function

- [ ] **Step 1: Modify `loadHistory` to render imported sessions differently**

In `project.html`, find the `loadHistory` function. Find this line inside the `tbody.innerHTML = sessions.map(s => {` block:

```javascript
                tbody.innerHTML = sessions.map(s => {
                    const date = s.created_at ? new Date(s.created_at).toLocaleString() : "-";
                    const total = s.total_companies || 0;
```

After the `const total` line, add an early-return branch for imported sessions. Insert this block before any other existing logic:

```javascript
                    // ---- Imported session row ----
                    if (s.type === "imported") {
                        const isBroken = !s.source_session_id;
                        const badge = isBroken
                            ? `<span class="status-badge status-failed">Source deleted</span>`
                            : `<span class="status-badge status-completed">Reference active</span>`;
                        const sourceLabel = escapeHtml(s.source_project_name || "Unknown") +
                            " &middot; " + escapeHtml(s.source_session_filename || s.filename);
                        const dlLinks = !isBroken
                            ? `<a href="/api/sessions/${s.id}/postings/download">Postings</a> | <a href="/api/sessions/${s.id}/news/download">News</a> | `
                            : `<span style="color:var(--pico-muted-color); font-size:0.85em;" title="Source deleted">Downloads unavailable</span> | `;
                        const actions = dlLinks + `<a href="#" onclick="deleteSession('${s.id}'); return false;" style="color:var(--pico-color-red-500)">Delete</a>`;
                        return `<tr style="background: #f8f9fa;">
                            <td><em>${sourceLabel}</em> ${badge}<br><span style="font-size:0.8em; color:var(--pico-muted-color);">${s.total_companies || 0} companies</span></td>
                            <td colspan="3" style="color:var(--pico-muted-color); font-size:0.85em;">—</td>
                            <td>${date}</td>
                            <td>${actions}</td>
                        </tr>`;
                    }
                    // ---- end imported session row ----
```

- [ ] **Step 2: Fix `updateExportButtons` to skip broken-reference imported sessions**

In `project.html`, find `updateExportButtons`. Find this line:

```javascript
            const completedSession = sessions.find(s => s.status === 'completed');
```

Replace with:

```javascript
            const completedSession = sessions.find(s => s.status === 'completed' && (s.type !== 'imported' || s.source_session_id));
```

- [ ] **Step 3: Smoke test manually**

Import a session from another project and verify the history table shows the imported row with the correct label, badge, and download links.

- [ ] **Step 4: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: render imported sessions distinctly in history table"
```

---

## Task 9: Frontend — Warning Banner for Broken References

**Files:**
- Modify: `app/templates/project.html`

- [ ] **Step 1: Add warning banner HTML to project.html**

In `project.html`, find the `<div id="panel-upload">` opening tag. Add this banner immediately after it (before the `<!-- Upload Section -->` comment):

```html
            <!-- Broken reference warning banner -->
            <div id="broken-reference-banner" style="display:none; margin-bottom:1em;">
                <p style="background:#fff3cd; border:1px solid #ffc107; border-radius:6px; padding:0.75em 1em; margin:0; font-size:0.9em;">
                    <strong>Warning:</strong> One or more imported sessions have lost their source data.
                    Keyword scans will skip those companies and downloads from those sessions are unavailable.
                </p>
            </div>
```

- [ ] **Step 2: Add `checkBrokenReferences` call inside `loadHistory`**

In `project.html`, find the end of `loadHistory` — the `} catch (err) {` block that closes the try. Before that closing `} catch`, add a call to check broken references. Find this section at the end of the `try` block in `loadHistory`:

```javascript
            } catch (err) {
                console.error("History load error:", err);
            }
```

Insert a call just before the `} catch`:

```javascript
                checkBrokenReferences(sessions);
```

Then add the helper function near the other helper functions in the script block (e.g., right after `loadHistory`):

```javascript
        function checkBrokenReferences(sessions) {
            const hasBroken = sessions.some(s => s.type === "imported" && !s.source_session_id);
            document.getElementById("broken-reference-banner").style.display = hasBroken ? "block" : "none";
        }
```

- [ ] **Step 3: Smoke test manually**

To test the banner: in Supabase, set a ghost session's `source_session_id` to NULL manually, then reload the project page. Verify the yellow banner appears.

- [ ] **Step 4: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: add broken reference warning banner to Upload tab"
```

---

## Task 10: Frontend — Deletion Guard

**Files:**
- Modify: `app/templates/project.html` — `deleteSession` function
- Modify: `app/templates/projects.html` — `deleteProject` function

- [ ] **Step 1: Modify `deleteSession` in project.html to check dependents**

Find the existing `deleteSession` function:

```javascript
        async function deleteSession(sessionId) {
            if (!confirm("Delete this file and all its data?")) return;
            await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
            loadHistory();
        }
```

Replace with:

```javascript
        async function deleteSession(sessionId) {
            try {
                const depsResp = await fetch(`/api/sessions/${sessionId}/dependents`);
                const deps = await depsResp.json();

                if (deps.length) {
                    const names = deps.map(d => d.project_name).join(", ");
                    if (!confirm(`This session is imported by: ${names}.\n\nDeleting will break their imported sessions — those companies will be skipped in keyword scans and downloads will stop working.\n\nContinue?`)) return;
                    await fetch(`/api/sessions/${sessionId}?force=true`, { method: "DELETE" });
                } else {
                    if (!confirm("Delete this file and all its data?")) return;
                    await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
                }
            } catch (err) {
                console.error("Failed to check dependents:", err);
                if (!confirm("Delete this file and all its data?")) return;
                await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
            }
            loadHistory();
        }
```

- [ ] **Step 2: Modify `deleteProject` in projects.html to check dependents and pass project name**

In `projects.html`, find the card render code that calls `deleteProject`:

```javascript
                    return `<article class="project-card" onclick="window.location='/projects/${p.id}'">
                        <div class="project-header">
                            <h4 style="margin:0;">${escapeHtml(p.name)}</h4>
                            <a href="#" class="delete" onclick="deleteProject(event, '${p.id}')">Delete</a>
                        </div>
```

Replace the `onclick` to pass the project name:

```javascript
                            <a href="#" class="delete" onclick="deleteProject(event, '${p.id}', ${JSON.stringify(p.name)})">Delete</a>
```

Then find the existing `deleteProject` function:

```javascript
        async function deleteProject(e, projectId) {
            e.stopPropagation();
            e.preventDefault();
            if (!confirm("Delete this project and ALL its files/data?")) return;
            await fetch(`/api/projects/${projectId}`, { method: "DELETE" });
            loadProjects();
        }
```

Replace with:

```javascript
        async function deleteProject(e, projectId, projectName) {
            e.stopPropagation();
            e.preventDefault();
            try {
                const depsResp = await fetch(`/api/projects/${projectId}/dependents`);
                const deps = await depsResp.json();

                if (deps.length) {
                    const depNames = [...new Set(deps.map(d => d.project_name))].join(", ");
                    if (!confirm(`Project "${projectName}" is imported by: ${depNames}.\n\nDeleting will break their imported sessions — those companies will be skipped in keyword scans and downloads will stop working.\n\nContinue?`)) return;
                    await fetch(`/api/projects/${projectId}?force=true`, { method: "DELETE" });
                } else {
                    if (!confirm(`Delete project "${projectName}" and ALL its files/data?`)) return;
                    await fetch(`/api/projects/${projectId}`, { method: "DELETE" });
                }
            } catch (err) {
                console.error("Failed to check dependents:", err);
                if (!confirm(`Delete project "${projectName}" and ALL its files/data?`)) return;
                await fetch(`/api/projects/${projectId}`, { method: "DELETE" });
            }
            loadProjects();
        }
```

- [ ] **Step 3: Smoke test manually**

1. Import a session from Project 1 into Project 2.
2. Go to the project list and delete Project 1 — verify the warning dialog appears with Project 2's name.
3. Cancel, then go to Project 2's Upload tab and delete the imported session — verify normal "Delete this file" dialog appears (imported session has no dependents).
4. Go back and force-delete Project 1 — verify it succeeds and the broken reference banner appears in Project 2.

- [ ] **Step 4: Commit**

```bash
git add app/templates/project.html app/templates/projects.html
git commit -m "feat: add deletion guard for projects and sessions with dependents"
```

---

## Contact Scanner — Verification (no code changes needed)

The contact scanner in `app/services/contact_scanner.py` requires no modifications. Confirm this by checking:

- `_fetch_companies` queries `sessions` by `project_id` with no filter on `type` → imported sessions are included naturally.
- Ghost companies have `website_url`, `known_names`, `legal_name` copied from source → Hunter.io and LLM enrichment run correctly against them.
- Contacts are stored with `company_id = ghost_company_id` → they are owned by Project 2 and survive source deletion.

If any of these checks fail after running a contact scan on a project with imported sessions, re-read `app/services/contact_scanner.py` and add the necessary guards.

---

## Done

All backend endpoints, service logic, and frontend interactions for cross-project session import are complete. Run the full test suite one final time before marking done:

```bash
python -m pytest tests/ -v 2>&1 | tail -30
```
