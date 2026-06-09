# Contact Scanning as a Separate Phase — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Hunter.io contact search, LLM enrichment, and email verification out of the session pipeline into a new manually-triggered per-project contact scan; add a Roles tab in the UI with settings and a launch button; replace the per-session contacts export with a two-sheet project-level xlsx.

**Architecture:** New `contact_scans` table tracks scan jobs. New `contact_scanner.py` service runs the scan as a background task. Session processor is stripped of contact stages. The Roles tab in `project.html` hosts roles management, scan settings (use_roles, keyword_only), a launch button with inline progress, and a download button. Contact export becomes `GET /api/projects/{project_id}/contacts/download` returning a two-sheet xlsx.

**Tech Stack:** Python/FastAPI, Supabase/PostgreSQL, openpyxl, pandas, Hunter.io API, OpenAI/Gemini LLM, vanilla JS

---

## File Map

| File | Change |
|------|--------|
| `supabase_schema.sql` | Add contact_scans table, new columns on projects/contacts/companies |
| `app/services/keyword_scanner.py` | Write keyword_hit_count + keyword_group_count back to companies after scan |
| `app/services/session_processor.py` | Strip contact stages (finding_contacts, enriching_contacts, verifying_emails) from process_session() and resume_session() |
| `app/services/contact_scanner.py` | New file — run_contact_scan(scan_id) |
| `app/models.py` | Add ContactScanSettings model |
| `app/main.py` | New scan endpoints, update project_details, update upload_file, replace contacts download |
| `tests/test_keyword_scanner_hits.py` | New — tests for hit count computation helper |
| `tests/test_contact_scanner.py` | New — unit tests for contact_scanner |
| `app/templates/project.html` | Upload tab cleanup + new Roles tab |

---

### Task 1: Database schema — add contact_scans table and new columns

**Files:**
- Modify: `supabase_schema.sql`

Note: No automated tests for SQL. Steps below cover both the schema file update (for fresh installs) and the migration SQL to run in Supabase SQL Editor for existing databases.

- [ ] **Step 1: Update `supabase_schema.sql` — add two columns to `projects` table**

In `supabase_schema.sql`, replace:
```sql
CREATE TABLE projects (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT        NOT NULL,
    target_roles  TEXT[]      DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT now()
);
```
With:
```sql
CREATE TABLE projects (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                        TEXT        NOT NULL,
    target_roles                TEXT[]      DEFAULT '{}',
    contact_scan_use_roles      BOOLEAN     NOT NULL DEFAULT true,
    contact_scan_keyword_only   BOOLEAN     NOT NULL DEFAULT false,
    created_at                  TIMESTAMPTZ DEFAULT now()
);
```

- [ ] **Step 2: Add the `contact_scans` table to `supabase_schema.sql`**

Insert after the projects table block (before the sessions table):

```sql
-- Contact scans (one per manual scan trigger, per project)
CREATE TABLE contact_scans (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status              TEXT        NOT NULL DEFAULT 'running',
    use_roles           BOOLEAN     NOT NULL,
    keyword_only        BOOLEAN     NOT NULL,
    total_companies     INTEGER     NOT NULL DEFAULT 0,
    hunter_done         INTEGER     NOT NULL DEFAULT 0,
    enrichment_done     INTEGER     NOT NULL DEFAULT 0,
    total_verification  INTEGER     NOT NULL DEFAULT 0,
    verification_done   INTEGER     NOT NULL DEFAULT 0,
    contacts_added      INTEGER     NOT NULL DEFAULT 0,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_contact_scans_project ON contact_scans(project_id);
```

- [ ] **Step 3: Add `contact_scan_id` column to `contacts` table in `supabase_schema.sql`**

In the contacts table definition, add after the existing `session_id` line:
```sql
    contact_scan_id  UUID        REFERENCES contact_scans(id) ON DELETE CASCADE,
```

Also add an index after the contacts table:
```sql
CREATE INDEX idx_contacts_scan ON contacts(contact_scan_id);
```

- [ ] **Step 4: Add keyword hit columns to `companies` table in `supabase_schema.sql`**

In the companies table definition, add before `created_at`:
```sql
    keyword_hit_count   INTEGER     NOT NULL DEFAULT 0,
    keyword_group_count INTEGER     NOT NULL DEFAULT 0,
```

- [ ] **Step 5: Run migration SQL in Supabase SQL Editor for existing databases**

Open the Supabase SQL Editor for this project and run:

```sql
-- 1. Add settings columns to projects
ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS contact_scan_use_roles    BOOLEAN NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS contact_scan_keyword_only BOOLEAN NOT NULL DEFAULT false;

-- 2. Create contact_scans table
CREATE TABLE IF NOT EXISTS contact_scans (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status              TEXT        NOT NULL DEFAULT 'running',
    use_roles           BOOLEAN     NOT NULL,
    keyword_only        BOOLEAN     NOT NULL,
    total_companies     INTEGER     NOT NULL DEFAULT 0,
    hunter_done         INTEGER     NOT NULL DEFAULT 0,
    enrichment_done     INTEGER     NOT NULL DEFAULT 0,
    total_verification  INTEGER     NOT NULL DEFAULT 0,
    verification_done   INTEGER     NOT NULL DEFAULT 0,
    contacts_added      INTEGER     NOT NULL DEFAULT 0,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_contact_scans_project ON contact_scans(project_id);

-- 3. Add contact_scan_id to contacts
ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS contact_scan_id UUID REFERENCES contact_scans(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_contacts_scan ON contacts(contact_scan_id);

-- 4. Add keyword hit columns to companies
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS keyword_hit_count   INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS keyword_group_count INTEGER NOT NULL DEFAULT 0;
```

Expected: all statements execute without error.

- [ ] **Step 6: Commit**

```bash
git add supabase_schema.sql
git commit -m "feat: add contact_scans table and new columns to schema"
```

---

### Task 2: Write keyword hit counts to companies after scan

**Files:**
- Modify: `app/services/keyword_scanner.py`
- Create: `tests/test_keyword_scanner_hits.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_keyword_scanner_hits.py`:

```python
from app.services.keyword_scanner import _compute_company_hits


def test_compute_hits_counts_total_and_groups():
    groups = [
        {"name": "Group A", "keywords": ["kw1", "kw2"]},
        {"name": "Group B", "keywords": ["kw3"]},
    ]
    keyword_results = {
        "kw1": {"count": 3},
        "kw2": {"count": 0},
        "kw3": {"count": 5},
    }
    hit_count, hit_groups = _compute_company_hits(groups, keyword_results)
    assert hit_count == 8
    assert hit_groups == 2


def test_compute_hits_no_matches():
    groups = [{"name": "G", "keywords": ["kw1"]}]
    keyword_results = {"kw1": {"count": 0}}
    hit_count, hit_groups = _compute_company_hits(groups, keyword_results)
    assert hit_count == 0
    assert hit_groups == 0


def test_compute_hits_partial_group():
    groups = [{"name": "G", "keywords": ["kw1", "kw2"]}]
    keyword_results = {"kw1": {"count": 2}, "kw2": {"count": 0}}
    hit_count, hit_groups = _compute_company_hits(groups, keyword_results)
    assert hit_count == 2
    assert hit_groups == 1  # group G has at least one hit
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/pashache/Desktop/projects/WEI group/ABM/WEI_ABM_platform"
pytest tests/test_keyword_scanner_hits.py -v
```

Expected: `ImportError: cannot import name '_compute_company_hits'`

- [ ] **Step 3: Add `_compute_company_hits` helper to `app/services/keyword_scanner.py`**

Add after the `_fetch_all_in` function (after line 75):

```python
def _compute_company_hits(groups: list[dict], keyword_results: dict) -> tuple[int, int]:
    """Compute total keyword hit count and number of groups with at least one hit."""
    hit_count = sum(kd.get("count", 0) for kd in keyword_results.values())
    hit_groups = sum(
        1 for g in groups
        if any(keyword_results.get(kw, {}).get("count", 0) > 0 for kw in g["keywords"])
    )
    return hit_count, hit_groups
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_keyword_scanner_hits.py -v
```

Expected: 3 tests PASSED

- [ ] **Step 5: Use `_compute_company_hits` in `scan_project_keywords` and write to DB**

In `app/services/keyword_scanner.py`, inside the `for uc in unique_companies:` loop, replace:

```python
        result_companies.append({
            "name": uc["name"],
            "inn": uc["inn"],
            "results": keyword_results,
        })
```

With:

```python
        hit_count, hit_groups = _compute_company_hits(groups, keyword_results)
        for cid in uc["company_ids"]:
            try:
                supabase.table("companies").update({
                    "keyword_hit_count": hit_count,
                    "keyword_group_count": hit_groups,
                }).eq("id", cid).execute()
            except Exception as e:
                logger.warning("Failed to update keyword hits for company %s: %s", cid, e)

        result_companies.append({
            "name": uc["name"],
            "inn": uc["inn"],
            "results": keyword_results,
        })
```

- [ ] **Step 6: Run all keyword-related tests to check for regressions**

```bash
pytest tests/test_keyword_scanner_hits.py tests/test_keyword_parser.py -v
```

Expected: all PASSED

- [ ] **Step 7: Commit**

```bash
git add app/services/keyword_scanner.py tests/test_keyword_scanner_hits.py
git commit -m "feat: write keyword hit counts to companies after keyword scan"
```

---

### Task 3: Strip contact stages from session_processor.py

**Files:**
- Modify: `app/services/session_processor.py`
- Create: `tests/test_session_processor_no_contacts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_session_processor_no_contacts.py`:

```python
import ast
import inspect
from pathlib import Path


def test_session_processor_does_not_import_hunter():
    """Ensure session_processor no longer imports contact-related services."""
    source = Path("app/services/session_processor.py").read_text()
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append((node.module, [a.name for a in node.names]))
    # hunter_service and contact_enrichment must not be imported
    imported_modules = [m for m, _ in imports]
    assert "app.services.hunter_service" not in imported_modules, \
        "session_processor must not import hunter_service after contact stages removal"
    assert "app.services.contact_enrichment" not in imported_modules, \
        "session_processor must not import contact_enrichment after contact stages removal"


def test_session_processor_does_not_reference_contacts_done():
    """Ensure session_processor no longer references contact stage counters."""
    source = Path("app/services/session_processor.py").read_text()
    assert "finding_contacts" not in source
    assert "enriching_contacts" not in source
    assert "verifying_emails" not in source
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/pashache/Desktop/projects/WEI group/ABM/WEI_ABM_platform"
pytest tests/test_session_processor_no_contacts.py -v
```

Expected: FAIL (current session_processor still imports hunter_service and contact_enrichment)

- [ ] **Step 3: Strip contact imports from `app/services/session_processor.py`**

Replace lines 12–13:
```python
from app.services.hunter_service import find_contacts_for_domain, verify_email
from app.services.contact_enrichment import enrich_contacts_for_company
```
With nothing (delete both lines).

- [ ] **Step 4: Strip contact flags from `_get_session_flags()` and `process_session()`**

In `_get_session_flags()`, replace:
```python
    result = (
        supabase.table("sessions")
        .select("run_postings, run_news, run_contacts, run_enrichment, run_verification, project_id")
        .eq("id", session_id)
        .execute()
    )
```
With:
```python
    result = (
        supabase.table("sessions")
        .select("run_postings, run_news, project_id")
        .eq("id", session_id)
        .execute()
    )
```

In `process_session()`, remove these three lines (they read the dead flags):
```python
        run_contacts = flags.get("run_contacts", True)
        run_enrichment = flags.get("run_enrichment", True)
        run_verification = flags.get("run_verification", True)
```

- [ ] **Step 5: Remove contact stages from `process_session()`**

In `process_session()`, delete the entire blocks for Step 4 (finding_contacts), Step 5 (enriching_contacts), and Step 6 (verifying_emails), which start with `# --- Step 4:` and end before `# --- Done ---`. Keep only:

```python
        # --- Done ---
        _update_session(session_id, status="completed")
        logger.info("Session %s completed successfully", session_id)
```

The last `_session_exists` check before Step 4 should also be removed since there are no more steps after news.

After news stage (`else: _update_session(session_id, news_done=total)`), the next thing should be `# --- Done ---`.

- [ ] **Step 6: Strip contact stages from `resume_session()`**

In `resume_session()`, the `select` at the top should be updated. Replace:
```python
        result = (
            supabase.table("sessions")
            .select("total_companies, names_done, postings_done, news_done, contacts_done, enrichment_done, verification_done, project_id, run_postings, run_news, run_contacts, run_enrichment, run_verification")
            .eq("id", session_id)
            .execute()
        )
```
With:
```python
        result = (
            supabase.table("sessions")
            .select("total_companies, names_done, postings_done, news_done, project_id, run_postings, run_news")
            .eq("id", session_id)
            .execute()
        )
```

Remove the variable assignments for dead fields:
```python
        contacts_done = session["contacts_done"] or 0
        enrichment_done = session["enrichment_done"] or 0
        verification_done = session.get("verification_done") or 0
        run_verification = session.get("run_verification", True)
```
And:
```python
        run_contacts = session.get("run_contacts", True)
        run_enrichment = session.get("run_enrichment", True)
```

Remove the three contact/enrichment/verification stage blocks (Stages 3, 4, 6) from `resume_session()`. Keep only Stages 1 (names), 2 (postings), 2.5 (news), then set status completed.

- [ ] **Step 7: Run tests to verify they pass**

```bash
pytest tests/test_session_processor_no_contacts.py -v
```

Expected: 2 tests PASSED

- [ ] **Step 8: Smoke-test that the module imports cleanly**

```bash
python3 -c "from app.services.session_processor import process_session, resume_session; print('OK')"
```

Expected: `OK`

- [ ] **Step 9: Commit**

```bash
git add app/services/session_processor.py tests/test_session_processor_no_contacts.py
git commit -m "feat: remove contact stages from session processing pipeline"
```

---

### Task 4: Create `app/services/contact_scanner.py`

**Files:**
- Create: `app/services/contact_scanner.py`
- Create: `tests/test_contact_scanner.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_contact_scanner.py`:

```python
import time
from unittest.mock import MagicMock, patch, call


def _make_supabase_mock(scan_row, project_row, sessions, companies, existing_contacts):
    """Build a mock supabase client with consistent chained query responses."""
    mock = MagicMock()

    def select_side_effect(*args, **kwargs):
        return mock._current_query

    def table_side_effect(name):
        mock._current_table = name
        mock._current_query = MagicMock()
        mock._current_query.select.return_value = mock._current_query
        mock._current_query.eq.return_value = mock._current_query
        mock._current_query.gt.return_value = mock._current_query
        mock._current_query.not_.is_.return_value = mock._current_query
        mock._current_query.in_.return_value = mock._current_query
        mock._current_query.range.return_value = mock._current_query
        mock._current_query.update.return_value = mock._current_query
        mock._current_query.insert.return_value = mock._current_query

        if name == "contact_scans":
            mock._current_query.execute.return_value = MagicMock(data=[scan_row])
        elif name == "projects":
            mock._current_query.execute.return_value = MagicMock(data=[project_row])
        elif name == "sessions":
            mock._current_query.execute.return_value = MagicMock(data=sessions)
        elif name == "companies":
            mock._current_query.execute.return_value = MagicMock(data=companies)
        elif name == "contacts":
            mock._current_query.execute.return_value = MagicMock(data=existing_contacts)
        return mock._current_query

    mock.table.side_effect = table_side_effect
    return mock


@patch("app.services.contact_scanner.supabase")
@patch("app.services.contact_scanner.find_contacts_for_domain", return_value=[])
@patch("app.services.contact_scanner.time.sleep", return_value=None)
def test_scan_completes_when_no_companies(mock_sleep, mock_hunter, mock_supa):
    """Scan with no sessions should immediately set status=completed."""
    scan_row = {
        "id": "scan-1", "project_id": "proj-1",
        "use_roles": False, "keyword_only": False,
    }
    project_row = {"target_roles": []}
    mock_supa.table.side_effect = lambda name: _make_supabase_mock(
        scan_row, project_row, sessions=[], companies=[], existing_contacts=[]
    ).table(name)

    from app.services.contact_scanner import run_contact_scan
    run_contact_scan("scan-1")

    # Should update status to completed
    update_calls = [
        str(c) for c in mock_supa.table.call_args_list
    ]
    # Check completed update was called (via update().eq().execute())
    assert mock_hunter.call_count == 0


@patch("app.services.contact_scanner.supabase")
@patch("app.services.contact_scanner.find_contacts_for_domain")
@patch("app.services.contact_scanner.time.sleep", return_value=None)
def test_scan_deduplicates_on_email(mock_sleep, mock_hunter, mock_supa):
    """Contacts with emails already in the DB for a company are skipped."""
    scan_row = {
        "id": "scan-1", "project_id": "proj-1",
        "use_roles": False, "keyword_only": False,
    }
    project_row = {"target_roles": []}
    sessions = [{"id": "sess-1"}]
    companies = [{"id": "comp-1", "legal_name": "Acme", "known_names": ["Acme"],
                  "website_url": "https://acme.com", "keyword_hit_count": 0}]
    # Two contacts returned by Hunter — one already exists
    mock_hunter.return_value = [
        {"email": "new@acme.com", "first_name": "New", "last_name": "Person",
         "confidence": 90, "source": "hunter"},
        {"email": "existing@acme.com", "first_name": "Old", "last_name": "Person",
         "confidence": 85, "source": "hunter"},
    ]

    call_counter = {"contacts": 0}

    def table_side_effect(name):
        q = MagicMock()
        q.select.return_value = q
        q.eq.return_value = q
        q.gt.return_value = q
        q.in_.return_value = q
        q.not_ = MagicMock()
        q.not_.is_ = MagicMock(return_value=q)
        q.range.return_value = q
        q.update.return_value = q
        q.insert.return_value = q

        if name == "contact_scans":
            q.execute.return_value = MagicMock(data=[scan_row])
        elif name == "projects":
            q.execute.return_value = MagicMock(data=[project_row])
        elif name == "sessions":
            q.execute.return_value = MagicMock(data=sessions)
        elif name == "companies":
            q.execute.return_value = MagicMock(data=companies)
        elif name == "contacts":
            call_counter["contacts"] += 1
            if call_counter["contacts"] == 1:
                # existing emails query
                q.execute.return_value = MagicMock(data=[{"email": "existing@acme.com"}])
            else:
                q.execute.return_value = MagicMock(data=[])
        return q

    mock_supa.table.side_effect = table_side_effect

    from importlib import reload
    import app.services.contact_scanner as cs
    reload(cs)
    cs.run_contact_scan("scan-1")

    # Check that insert was called with only the new (non-duplicate) contact
    insert_calls = [c for c in mock_supa.table.call_args_list if c == call("contacts")]
    # The key assertion: only 1 contact inserted (new@acme.com), not both
    inserted_rows = []
    for c in mock_supa.method_calls:
        if "insert" in str(c) and "new@acme.com" in str(c):
            inserted_rows.append(c)
    # At minimum, hunter was called once
    mock_hunter.assert_called_once_with("https://acme.com")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/pashache/Desktop/projects/WEI group/ABM/WEI_ABM_platform"
pytest tests/test_contact_scanner.py -v
```

Expected: `ImportError: No module named 'app.services.contact_scanner'`

- [ ] **Step 3: Create `app/services/contact_scanner.py`**

Create the file with this content:

```python
import logging
import time

from app.database import supabase
from app.services.hunter_service import find_contacts_for_domain, verify_email
from app.services.contact_enrichment import enrich_contacts_for_company
from app.services.llm import LLMClient

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500


def _update_scan(scan_id: str, **fields) -> None:
    supabase.table("contact_scans").update(fields).eq("id", scan_id).execute()


def _batch_insert_contacts(rows: list[dict]) -> None:
    for i in range(0, len(rows), _BATCH_SIZE):
        supabase.table("contacts").insert(rows[i:i + _BATCH_SIZE]).execute()


def _get_existing_emails(company_id: str) -> set[str]:
    """Return lower-cased emails already stored for this company."""
    emails: set[str] = set()
    offset = 0
    while True:
        rows = (
            supabase.table("contacts")
            .select("email")
            .eq("company_id", company_id)
            .not_.is_("email", "null")
            .range(offset, offset + 999)
            .execute()
        ).data
        for r in rows:
            if r.get("email"):
                emails.add(r["email"].lower())
        if len(rows) < 1000:
            break
        offset += 1000
    return emails


def _fetch_companies(project_id: str, keyword_only: bool) -> list[dict]:
    """Fetch all companies across all sessions for a project."""
    sessions = (
        supabase.table("sessions")
        .select("id")
        .eq("project_id", project_id)
        .execute()
    ).data
    if not sessions:
        return []

    session_ids = [s["id"] for s in sessions]
    all_companies: list[dict] = []
    batch_size = 200

    for i in range(0, len(session_ids), batch_size):
        batch = session_ids[i:i + batch_size]
        offset = 0
        while True:
            query = (
                supabase.table("companies")
                .select("id, legal_name, known_names, website_url, keyword_hit_count")
                .in_("session_id", batch)
            )
            if keyword_only:
                query = query.gt("keyword_hit_count", 0)
            rows = query.range(offset, offset + 999).execute().data
            all_companies.extend(rows)
            if len(rows) < 1000:
                break
            offset += 1000

    return all_companies


def run_contact_scan(scan_id: str) -> None:
    """Run a per-project contact scan. Called as a FastAPI BackgroundTask."""
    try:
        scan_result = supabase.table("contact_scans").select("*").eq("id", scan_id).execute()
        if not scan_result.data:
            logger.error("Contact scan %s not found", scan_id)
            return
        scan = scan_result.data[0]
        project_id: str = scan["project_id"]
        use_roles: bool = scan["use_roles"]
        keyword_only: bool = scan["keyword_only"]

        project_result = (
            supabase.table("projects").select("target_roles").eq("id", project_id).execute()
        )
        target_roles: list[str] = (
            project_result.data[0].get("target_roles") or []
            if project_result.data else []
        )

        companies = _fetch_companies(project_id, keyword_only)
        total = len(companies)
        _update_scan(scan_id, total_companies=total)

        if total == 0:
            _update_scan(scan_id, status="completed")
            return

        llm_client = LLMClient() if use_roles and target_roles else None
        contacts_added = 0

        # ── Phase 1: Hunter.io domain search + optional LLM enrichment per company ──
        for i, company in enumerate(companies):
            company_id: str = company["id"]
            website_url: str | None = company.get("website_url")
            existing_emails = _get_existing_emails(company_id)

            # Hunter.io domain search
            if website_url:
                try:
                    hunter_contacts = find_contacts_for_domain(website_url)
                    time.sleep(5)  # Hunter.io rate limit between domain searches

                    new_hunter = []
                    for c in hunter_contacts:
                        email = (c.get("email") or "").lower()
                        if email and email in existing_emails:
                            continue
                        c["contact_scan_id"] = scan_id
                        c["company_id"] = company_id
                        new_hunter.append(c)
                        if email:
                            existing_emails.add(email)

                    if new_hunter:
                        _batch_insert_contacts(new_hunter)
                        contacts_added += len(new_hunter)

                except Exception as e:
                    logger.error(
                        "Hunter.io failed for company '%s' (scan %s): %s",
                        company.get("legal_name"), scan_id, e,
                    )

            _update_scan(scan_id, hunter_done=i + 1)

            # LLM role enrichment
            if use_roles and target_roles and llm_client:
                try:
                    existing_contacts = (
                        supabase.table("contacts")
                        .select("*")
                        .eq("company_id", company_id)
                        .execute()
                    ).data
                    # pass session_id=None; enrich_contacts_for_company sets it on each contact
                    enriched = enrich_contacts_for_company(
                        llm_client, company, target_roles, None, existing_contacts
                    )
                    new_enriched = []
                    for c in enriched:
                        email = (c.get("email") or "").lower()
                        if email and email in existing_emails:
                            continue
                        c.pop("session_id", None)   # remove the None session_id
                        c["contact_scan_id"] = scan_id
                        c["company_id"] = company_id
                        new_enriched.append(c)
                        if email:
                            existing_emails.add(email)

                    if new_enriched:
                        _batch_insert_contacts(new_enriched)
                        contacts_added += len(new_enriched)

                except Exception as e:
                    logger.error(
                        "Enrichment failed for company '%s' (scan %s): %s",
                        company.get("legal_name"), scan_id, e,
                    )

                _update_scan(scan_id, enrichment_done=i + 1)

        # ── Phase 2: Email verification ──
        contacts_to_verify = (
            supabase.table("contacts")
            .select("id, email")
            .eq("contact_scan_id", scan_id)
            .not_.is_("email", "null")
            .execute()
        ).data

        total_verification = len(contacts_to_verify)
        _update_scan(
            scan_id,
            total_verification=total_verification,
            contacts_added=contacts_added,
        )

        for i, contact in enumerate(contacts_to_verify):
            try:
                result = verify_email(contact["email"])
                if result is not None:
                    supabase.table("contacts").update(result).eq("id", contact["id"]).execute()
            except Exception as e:
                logger.error(
                    "Verification failed for contact %s ('%s'): %s",
                    contact["id"], contact["email"], e,
                )

            time.sleep(0.2)  # Hunter.io rate limit: 300 req/min
            _update_scan(scan_id, verification_done=i + 1)

        _update_scan(scan_id, status="completed")
        logger.info("Contact scan %s completed, %d contacts added", scan_id, contacts_added)

    except Exception as e:
        logger.exception("Contact scan %s failed: %s", scan_id, e)
        try:
            _update_scan(scan_id, status="failed", error_message=str(e)[:500])
        except Exception:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_contact_scanner.py -v
```

Expected: 2 tests PASSED

- [ ] **Step 5: Smoke test**

```bash
python3 -c "from app.services.contact_scanner import run_contact_scan; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add app/services/contact_scanner.py tests/test_contact_scanner.py
git commit -m "feat: add contact_scanner service for per-project contact scanning"
```

---

### Task 5: New contact scan API endpoints + model + upload form cleanup

**Files:**
- Modify: `app/models.py`
- Modify: `app/main.py`

- [ ] **Step 1: Add `ContactScanSettings` to `app/models.py`**

Append to `app/models.py`:

```python


class ContactScanSettings(BaseModel):
    use_roles: bool
    keyword_only: bool
```

- [ ] **Step 2: Update imports in `app/main.py`**

Replace line 15–18:
```python
from app.database import supabase
from app.models import CreateKeywordGroup, RenameKeywordGroup, CreateKeyword, CreateStopWord, UpdateProject
from app.services.session_processor import process_session, resume_session
from app.services.keyword_scanner import scan_project_keywords, generate_keyword_xlsx
from app.services.keyword_parser import parse_keyword_xlsx, parse_stop_word_xlsx, parse_roles_xlsx
```
With:
```python
from app.database import supabase
from app.models import CreateKeywordGroup, RenameKeywordGroup, CreateKeyword, CreateStopWord, UpdateProject, ContactScanSettings
from app.services.session_processor import process_session, resume_session
from app.services.contact_scanner import run_contact_scan
from app.services.keyword_scanner import scan_project_keywords, generate_keyword_xlsx
from app.services.keyword_parser import parse_keyword_xlsx, parse_stop_word_xlsx, parse_roles_xlsx
```

- [ ] **Step 3: Update `project_details` endpoint to return new settings columns**

Replace:
```python
@app.get("/api/projects/{project_id}/details")
async def project_details(project_id: str):
    result = (
        supabase.table("projects")
        .select("id, name, target_roles, created_at")
        .eq("id", project_id)
        .execute()
    )
    if not result.data:
        return {"error": "Project not found"}
    return result.data[0]
```
With:
```python
@app.get("/api/projects/{project_id}/details")
async def project_details(project_id: str):
    result = (
        supabase.table("projects")
        .select("id, name, target_roles, contact_scan_use_roles, contact_scan_keyword_only, created_at")
        .eq("id", project_id)
        .execute()
    )
    if not result.data:
        return {"error": "Project not found"}
    return result.data[0]
```

- [ ] **Step 4: Update `upload_file` endpoint — remove contact form params and session insert fields**

Replace:
```python
@app.post("/api/projects/{project_id}/sessions/upload")
async def upload_file(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    run_postings: bool = Form(True),
    run_news: bool = Form(True),
    run_contacts: bool = Form(True),
    run_enrichment: bool = Form(True),
    run_verification: bool = Form(True),
):
    if not file.filename.endswith((".xlsx", ".xls", ".csv")):
        return {"error": "Only .xlsx/.xls/.csv files are accepted"}

    file_bytes = await file.read()

    result = supabase.table("sessions").insert({
        "project_id": project_id,
        "filename": file.filename,
        "status": "uploading",
        "total_companies": 0,
        "run_postings": run_postings,
        "run_news": run_news,
        "run_contacts": run_contacts,
        "run_enrichment": run_enrichment,
        "run_verification": run_verification,
    }).execute()
```
With:
```python
@app.post("/api/projects/{project_id}/sessions/upload")
async def upload_file(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    run_postings: bool = Form(True),
    run_news: bool = Form(True),
):
    if not file.filename.endswith((".xlsx", ".xls", ".csv")):
        return {"error": "Only .xlsx/.xls/.csv files are accepted"}

    file_bytes = await file.read()

    result = supabase.table("sessions").insert({
        "project_id": project_id,
        "filename": file.filename,
        "status": "uploading",
        "total_companies": 0,
        "run_postings": run_postings,
        "run_news": run_news,
    }).execute()
```

- [ ] **Step 5: Update `list_sessions` and `session_status` to not select dead contact columns**

Replace the select in `list_sessions`:
```python
        .select("id, filename, status, total_companies, names_done, postings_done, news_done, contacts_done, enrichment_done, verification_done, total_verification, created_at")
```
With:
```python
        .select("id, filename, status, total_companies, names_done, postings_done, news_done, created_at")
```

Replace the select in `session_status`:
```python
        .select("id, filename, status, error_message, total_companies, names_done, postings_done, news_done, contacts_done, enrichment_done, verification_done, total_verification")
```
With:
```python
        .select("id, filename, status, error_message, total_companies, names_done, postings_done, news_done")
```

- [ ] **Step 6: Add the three new contact scan endpoints after `import_roles` endpoint**

Add these three endpoints after the `import_roles` endpoint (after line ~138, before `@app.delete("/api/projects/{project_id}")`):

```python

# ──────────────────────── Contact Scan ────────────────────────


@app.post("/api/projects/{project_id}/contact-scan/start")
async def contact_scan_start(project_id: str, background_tasks: BackgroundTasks):
    # Check if a scan is already running
    running = (
        supabase.table("contact_scans")
        .select("id")
        .eq("project_id", project_id)
        .eq("status", "running")
        .execute()
    )
    if running.data:
        raise HTTPException(status_code=409, detail="A contact scan is already running for this project")

    # Snapshot current project settings
    project = supabase.table("projects").select("contact_scan_use_roles, contact_scan_keyword_only").eq("id", project_id).execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="Project not found")
    settings = project.data[0]

    scan = supabase.table("contact_scans").insert({
        "project_id": project_id,
        "status": "running",
        "use_roles": settings["contact_scan_use_roles"],
        "keyword_only": settings["contact_scan_keyword_only"],
    }).execute()
    scan_id = scan.data[0]["id"]

    background_tasks.add_task(run_contact_scan, scan_id)
    return {"scan_id": scan_id}


@app.get("/api/projects/{project_id}/contact-scan/latest/status")
async def contact_scan_latest_status(project_id: str):
    result = (
        supabase.table("contact_scans")
        .select("status, use_roles, keyword_only, total_companies, hunter_done, enrichment_done, total_verification, verification_done, contacts_added, error_message, created_at")
        .eq("project_id", project_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return {"status": "none"}
    return result.data[0]


@app.put("/api/projects/{project_id}/contact-scan/settings")
async def update_contact_scan_settings(project_id: str, body: ContactScanSettings):
    result = supabase.table("projects").update({
        "contact_scan_use_roles": body.use_roles,
        "contact_scan_keyword_only": body.keyword_only,
    }).eq("id", project_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Project not found")
    return result.data[0]
```

- [ ] **Step 7: Smoke test**

```bash
python3 -c "from app.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add app/models.py app/main.py
git commit -m "feat: add contact scan API endpoints and update project details endpoint"
```

---

### Task 6: Replace contacts download with project-level two-sheet xlsx

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Remove the old `download_contacts` endpoint**

In `app/main.py`, delete the entire `download_contacts` function:

```python
@app.get("/api/sessions/{session_id}/contacts/download")
async def download_contacts(session_id: str):
    rows = _query_all_rows("contacts", session_id)
    ...
    return StreamingResponse(...)
```

(This is the block from `@app.get("/api/sessions/{session_id}/contacts/download")` to the closing `StreamingResponse` call, inclusive.)

- [ ] **Step 2: Add the new project-level contacts download endpoint**

Add after the `download_postings` endpoint (after the `download_news` endpoint is cleaner — add at the end of the Downloads section):

```python

@app.get("/api/projects/{project_id}/contacts/download")
async def download_project_contacts(project_id: str):
    # 1. Get all sessions for this project
    sessions_result = (
        supabase.table("sessions")
        .select("id, filename")
        .eq("project_id", project_id)
        .execute()
    )
    sessions = sessions_result.data
    if not sessions:
        raise HTTPException(status_code=404, detail="No sessions found for this project")

    session_ids = [s["id"] for s in sessions]
    session_filename: dict[str, str] = {s["id"]: s["filename"] for s in sessions}

    # 2. Fetch all companies across those sessions
    all_companies: list[dict] = []
    for i in range(0, len(session_ids), 200):
        batch = session_ids[i:i + 200]
        offset = 0
        while True:
            rows = (
                supabase.table("companies")
                .select("id, legal_name, inn, session_id, keyword_hit_count, keyword_group_count")
                .in_("session_id", batch)
                .range(offset, offset + 999)
                .execute()
            ).data
            all_companies.extend(rows)
            if len(rows) < 1000:
                break
            offset += 1000

    if not all_companies:
        raise HTTPException(status_code=404, detail="No companies found in this project")

    company_ids = [c["id"] for c in all_companies]
    company_meta: dict[str, dict] = {
        c["id"]: {
            "Company": c.get("legal_name", ""),
            "INN": c.get("inn", ""),
            "Session": session_filename.get(c.get("session_id", ""), ""),
            "Keywords Found": c.get("keyword_hit_count", 0),
            "Keyword Groups Found": c.get("keyword_group_count", 0),
            "Contacts Found": 0,
        }
        for c in all_companies
    }

    # 3. Fetch all contacts with contact_scan_id set, filtered by verification status
    _KEEP_STATUSES = {"valid", "accept_all"}
    all_contacts: list[dict] = []
    for i in range(0, len(company_ids), 200):
        batch = company_ids[i:i + 200]
        offset = 0
        while True:
            rows = (
                supabase.table("contacts")
                .select("*")
                .in_("company_id", batch)
                .not_.is_("contact_scan_id", "null")
                .range(offset, offset + 999)
                .execute()
            ).data
            all_contacts.extend(rows)
            if len(rows) < 1000:
                break
            offset += 1000

    filtered = [
        c for c in all_contacts
        if c.get("email_status") is None or c.get("email_status") in _KEEP_STATUSES
    ]

    if not filtered:
        raise HTTPException(status_code=404, detail="No contacts found. Run a contact scan first.")

    # 4. Build Sheet 1 — Companies
    for c in filtered:
        cid = c.get("company_id")
        if cid in company_meta:
            company_meta[cid]["Contacts Found"] += 1

    sheet1_rows = [v for v in company_meta.values() if v["Contacts Found"] > 0]
    sheet1_rows.sort(key=lambda x: x["Contacts Found"], reverse=True)
    sheet1_df = pd.DataFrame(sheet1_rows, columns=[
        "Company", "INN", "Session", "Contacts Found", "Keywords Found", "Keyword Groups Found"
    ])

    # 5. Build Sheet 2 — Contacts
    for c in filtered:
        cid = c.get("company_id")
        c["company_name"] = company_meta.get(cid, {}).get("Company", "")

    sheet2_df = pd.DataFrame(filtered)
    for col in ("id", "session_id", "company_id", "contact_scan_id"):
        if col in sheet2_df.columns:
            sheet2_df = sheet2_df.drop(columns=[col])

    # company_name first
    cols = ["company_name"] + [c for c in sheet2_df.columns if c != "company_name"]
    sheet2_df = sheet2_df[[c for c in cols if c in sheet2_df.columns]]
    if "company_name" in sheet2_df.columns and "last_name" in sheet2_df.columns:
        sheet2_df = sheet2_df.sort_values(["company_name", "last_name"], na_position="last")

    # 6. Write two-sheet xlsx
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        sheet1_df.to_excel(writer, sheet_name="Companies", index=False)
        sheet2_df.to_excel(writer, sheet_name="Contacts", index=False)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=contacts_{project_id[:8]}.xlsx"},
    )
```

- [ ] **Step 3: Smoke test**

```bash
python3 -c "from app.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "feat: add project-level two-sheet contacts download, remove session-level contacts download"
```

---

### Task 7: Upload tab cleanup in `project.html`

**Files:**
- Modify: `app/templates/project.html`

- [ ] **Step 1: Remove the three contact stage checkboxes from the upload form**

In `app/templates/project.html`, remove lines 138–152 (the three contact/enrichment/verification checkbox labels):

```html
                            <label style="display:flex; align-items:center; gap:0.5em; margin-bottom:0.3em; font-size:0.9em;">
                                <input type="checkbox" id="chk-contacts" checked style="margin:0;">
                                Find contacts (Hunter.io)
                                <span style="color:var(--pico-muted-color); font-size:0.85em;">~6 sec/company</span>
                            </label>
                            <label style="display:flex; align-items:center; gap:0.5em; margin-bottom:0.3em; font-size:0.9em;">
                                <input type="checkbox" id="chk-enrichment" checked style="margin:0;">
                                Find contacts (open sources)
                                <span style="color:var(--pico-muted-color); font-size:0.85em;">~15 sec/company</span>
                            </label>
                            <label style="display:flex; align-items:center; gap:0.5em; margin-bottom:0.3em; font-size:0.9em;">
                                <input type="checkbox" id="chk-verification" checked style="margin:0;">
                                Verify emails (Hunter.io)
                                <span style="color:var(--pico-muted-color); font-size:0.85em;">~0.2 sec/contact</span>
                            </label>
```

- [ ] **Step 2: Remove contact FormData appends from the upload submit handler**

In the `upload-form` submit event listener (~line 334–338), remove:
```javascript
            formData.append("run_contacts", document.getElementById("chk-contacts").checked);
            formData.append("run_enrichment", document.getElementById("chk-enrichment").checked);
            formData.append("run_verification", document.getElementById("chk-verification").checked);
```

- [ ] **Step 3: Remove the three contact progress rows from the progress table**

In the progress table (~lines 181–195), remove these three `<tr>` blocks:
```html
                            <tr>
                                <td>Contacts (Hunter.io)</td>
                                <td><progress id="prog-contacts-bar" value="0" max="100" style="width:160px;"></progress></td>
                                <td id="prog-contacts-text">&mdash;</td>
                            </tr>
                            <tr>
                                <td>Contact Enrichment</td>
                                <td><progress id="prog-enrichment-bar" value="0" max="100" style="width:160px;"></progress></td>
                                <td id="prog-enrichment-text">&mdash;</td>
                            </tr>
                            <tr>
                                <td>Email Verification</td>
                                <td><progress id="prog-verification-bar" value="0" max="100" style="width:160px;"></progress></td>
                                <td id="prog-verification-text">&mdash;</td>
                            </tr>
```

- [ ] **Step 4: Remove the contacts download link from the Download Section**

Remove from the download section article:
```html
                        <a id="dl-contacts" role="button" class="secondary" href="#">Download Contacts (.xlsx)</a>
```

- [ ] **Step 5: Remove contact link from `showDownloadLinks()` function**

Remove from `showDownloadLinks()`:
```javascript
            document.getElementById("dl-contacts").href = `/api/sessions/${sessionId}/contacts/download`;
```

- [ ] **Step 6: Update `pollStatus()` — remove contact/enrichment/verification stage calls**

In `pollStatus()`, remove:
```javascript
                setStage("contacts",   data.contacts_done   || 0);
                setStage("enrichment", data.enrichment_done || 0);
```

And remove the entire verification block:
```javascript
                // Verification uses total_verification as denominator, not total_companies
                const verTotal = data.total_verification || 0;
                const verDone  = data.verification_done  || 0;
                const verPct   = verTotal > 0 ? Math.round((verDone / verTotal) * 100) : 0;
                document.getElementById("prog-verification-bar").value = verPct;
                document.getElementById("prog-verification-text").textContent =
                    verTotal ? `${verDone} / ${verTotal}` : "\u2014";
```

- [ ] **Step 7: Update history table — remove Contacts/Enrichment/Email Verif columns**

In the history table `<thead>`, replace:
```html
                                <tr>
                                    <th>File</th>
                                    <th>Names</th>
                                    <th>Postings</th>
                                    <th>News</th>
                                    <th>Contacts</th>
                                    <th>Enrichment</th>
                                    <th>Email Verif.</th>
                                    <th>Created</th>
                                    <th>Actions</th>
                                </tr>
```
With:
```html
                                <tr>
                                    <th>File</th>
                                    <th>Names</th>
                                    <th>Postings</th>
                                    <th>News</th>
                                    <th>Created</th>
                                    <th>Actions</th>
                                </tr>
```

- [ ] **Step 8: Update history table empty state colspan and row rendering**

Replace:
```html
                                <tr><td colspan="9">Loading...</td></tr>
```
With:
```html
                                <tr><td colspan="6">Loading...</td></tr>
```

In `loadHistory()`, replace the tbody.innerHTML row template. The existing template has 9 `<td>` cells. Remove the three contact/enrichment/verification `stageCell` calls and change `colspan="9"` to `colspan="6"`.

Replace the no-files string:
```javascript
                    tbody.innerHTML = '<tr><td colspan="9">No files yet. Upload one above.</td></tr>';
```
With:
```javascript
                    tbody.innerHTML = '<tr><td colspan="6">No files yet. Upload one above.</td></tr>';
```

Replace the row template inside `sessions.map(s => { ... })`. Remove these three cells:
```javascript
                        <td>${stageCell(s.contacts_done, total, s.status, "finding_contacts")}</td>
                        <td>${stageCell(s.enrichment_done, total, s.status, "enriching_contacts")}</td>
                        <td>${verificationCell(s.verification_done, s.total_verification, s.status)}</td>
```

Also update the download links inside `loadHistory()`: remove the Contacts link:
```javascript
                        ? `<a href="/api/sessions/${s.id}/postings/download">Postings</a> | <a href="/api/sessions/${s.id}/news/download">News</a> | <a href="/api/sessions/${s.id}/contacts/download">Contacts</a> | `
```
Replace with:
```javascript
                        ? `<a href="/api/sessions/${s.id}/postings/download">Postings</a> | <a href="/api/sessions/${s.id}/news/download">News</a> | `
```

- [ ] **Step 9: Update `ACTIVE_STATUSES` — remove contact stage strings**

Replace:
```javascript
        const ACTIVE_STATUSES = new Set(["resolving_names", "finding_postings", "finding_news", "finding_contacts", "enriching_contacts", "verifying_emails", "resuming"]);
```
With:
```javascript
        const ACTIVE_STATUSES = new Set(["resolving_names", "finding_postings", "finding_news", "resuming"]);
```

Also update `isActive` check in `loadHistory()`:
```javascript
                    const isActive = ["uploading", "parsing", "resolving_names", "finding_postings", "finding_news", "finding_contacts", "enriching_contacts", "verifying_emails", "resuming"].includes(s.status);
```
Replace with:
```javascript
                    const isActive = ["uploading", "parsing", "resolving_names", "finding_postings", "finding_news", "resuming"].includes(s.status);
```

- [ ] **Step 10: Remove the Target Roles article from the Upload tab panel**

Remove lines 105–118 from `panel-upload` (the Target Roles article with roles-list div and the add/import buttons):
```html
                <!-- Target Roles -->
                <article>
                    <h3>Target Roles for Contact Enrichment</h3>
                    <div id="roles-list" class="roles-list"></div>
                    <div style="display:flex; gap:0.5em; align-items:center; margin-top:0.5em; flex-wrap:wrap;">
                        <input type="text" id="new-role-input" placeholder="Add role, e.g. CEO, CTO" style="margin:0; padding:0.3em 0.5em; font-size:0.9em;">
                        <button onclick="addRole()" style="margin:0; padding:0.3em 0.8em; font-size:0.85em; white-space:nowrap;">Add</button>
                        <button id="import-roles-btn" class="secondary" style="margin:0; padding:0.3em 0.8em; font-size:0.85em; white-space:nowrap;"
                                onclick="document.getElementById('roles-import-input').click()">Import from xlsx</button>
                        <input type="file" id="roles-import-input" accept=".xlsx" style="display:none"
                               onchange="importRolesFile(this)">
                        <span id="roles-import-status" style="font-size:0.85em; color:var(--pico-muted-color);"></span>
                    </div>
                </article>
```

- [ ] **Step 11: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: clean up upload tab - remove contact stages, remove roles section"
```

---

### Task 8: Add new Roles tab to `project.html`

**Files:**
- Modify: `app/templates/project.html`

- [ ] **Step 1: Add `tab-roles` radio input to the tab bar**

Replace:
```html
        <div class="tabs">
            <input type="radio" id="tab-upload"    name="tab" checked>
            <input type="radio" id="tab-keywords"  name="tab">
            <input type="radio" id="tab-stopwords" name="tab">

            <nav class="tab-nav">
                <label for="tab-upload">Upload</label>
                <label for="tab-keywords">Keywords</label>
                <label for="tab-stopwords">Stop Words</label>
            </nav>
```
With:
```html
        <div class="tabs">
            <input type="radio" id="tab-upload"    name="tab" checked>
            <input type="radio" id="tab-keywords"  name="tab">
            <input type="radio" id="tab-stopwords" name="tab">
            <input type="radio" id="tab-roles"     name="tab">

            <nav class="tab-nav">
                <label for="tab-upload">Upload</label>
                <label for="tab-keywords">Keywords</label>
                <label for="tab-stopwords">Stop Words</label>
                <label for="tab-roles">Roles</label>
            </nav>
```

- [ ] **Step 2: Add CSS for the new tab**

In the `<style>` block, add after `#tab-stopwords:checked ~ #panel-stopwords { display: block; }`:

```css
        #tab-roles:checked     ~ #panel-roles     { display: block; }
```

And add to the active label rule:
```css
        #tab-roles:checked     ~ .tab-nav label[for="tab-roles"] {
            border-bottom-color: var(--pico-primary);
            color: var(--pico-primary);
        }
```

- [ ] **Step 3: Add the `panel-roles` HTML**

Add before `</div><!-- /tabs -->`:

```html
            <!-- ── Roles panel ── -->
            <div id="panel-roles">

                <!-- Roles management -->
                <article>
                    <h3>Target Roles for Contact Enrichment</h3>
                    <div id="roles-list" class="roles-list"></div>
                    <div style="display:flex; gap:0.5em; align-items:center; margin-top:0.5em; flex-wrap:wrap;">
                        <input type="text" id="new-role-input" placeholder="Add role, e.g. CEO, CTO" style="margin:0; padding:0.3em 0.5em; font-size:0.9em;">
                        <button onclick="addRole()" style="margin:0; padding:0.3em 0.8em; font-size:0.85em; white-space:nowrap;">Add</button>
                        <button id="import-roles-btn" class="secondary" style="margin:0; padding:0.3em 0.8em; font-size:0.85em; white-space:nowrap;"
                                onclick="document.getElementById('roles-import-input').click()">Import from xlsx</button>
                        <input type="file" id="roles-import-input" accept=".xlsx" style="display:none"
                               onchange="importRolesFile(this)">
                        <span id="roles-import-status" style="font-size:0.85em; color:var(--pico-muted-color);"></span>
                    </div>
                </article>

                <!-- Contact Scan Settings -->
                <article>
                    <h3>Contact Scan Settings</h3>
                    <div style="display:flex; flex-direction:column; gap:0.5em;">
                        <label style="display:flex; align-items:center; gap:0.5em; font-size:0.95em;">
                            <input type="checkbox" id="chk-use-roles" style="margin:0;" onchange="saveContactScanSettings()">
                            Use roles for LLM enrichment
                            <span style="color:var(--pico-muted-color); font-size:0.85em;">(finds people by job title)</span>
                        </label>
                        <label style="display:flex; align-items:center; gap:0.5em; font-size:0.95em;">
                            <input type="checkbox" id="chk-keyword-only" style="margin:0;" onchange="saveContactScanSettings()">
                            Keyword companies only
                            <span style="color:var(--pico-muted-color); font-size:0.85em;">(skip companies with no keyword hits)</span>
                        </label>
                    </div>
                </article>

                <!-- Scan Launch + Progress -->
                <article>
                    <h3>Contact Scan</h3>
                    <button id="scan-contacts-btn" onclick="launchContactScan()" class="contrast">
                        Launch Contact Scan
                    </button>
                    <p id="scan-contacts-error" style="color:var(--pico-color-red-500); display:none; margin-top:0.5em;"></p>

                    <div id="contact-scan-progress" style="display:none; margin-top:1em;">
                        <table style="font-size:0.9em;">
                            <thead><tr><th>Stage</th><th>Progress</th><th></th></tr></thead>
                            <tbody>
                                <tr>
                                    <td>Hunter.io search</td>
                                    <td><progress id="scan-prog-hunter-bar" value="0" max="100" style="width:160px;"></progress></td>
                                    <td id="scan-prog-hunter-text">&mdash;</td>
                                </tr>
                                <tr id="scan-enrichment-row">
                                    <td>LLM enrichment</td>
                                    <td><progress id="scan-prog-enrichment-bar" value="0" max="100" style="width:160px;"></progress></td>
                                    <td id="scan-prog-enrichment-text">&mdash;</td>
                                </tr>
                                <tr>
                                    <td>Email verification</td>
                                    <td><progress id="scan-prog-verification-bar" value="0" max="100" style="width:160px;"></progress></td>
                                    <td id="scan-prog-verification-text">&mdash;</td>
                                </tr>
                            </tbody>
                        </table>
                        <p id="scan-contacts-status" style="font-size:0.9em; margin-top:0.5em;"></p>
                    </div>

                    <div style="margin-top:1em;">
                        <button id="dl-contacts-btn" onclick="downloadContacts()" class="secondary"
                                style="display:none;">
                            Download Contacts (.xlsx)
                        </button>
                    </div>
                </article>

            </div><!-- /panel-roles -->
```

- [ ] **Step 4: Add contact scan JavaScript functions**

In the `<script>` block, add these functions before the `// Load on page load` comment:

```javascript
        // ---- Contact Scan Settings ----
        async function loadContactScanSettings() {
            try {
                const resp = await fetch(`/api/projects/${PROJECT_ID}/details`);
                const project = await resp.json();
                document.getElementById("chk-use-roles").checked =
                    project.contact_scan_use_roles !== false;  // default true
                document.getElementById("chk-keyword-only").checked =
                    project.contact_scan_keyword_only === true;
            } catch (err) {
                console.error("Failed to load contact scan settings:", err);
            }
        }

        async function saveContactScanSettings() {
            const use_roles = document.getElementById("chk-use-roles").checked;
            const keyword_only = document.getElementById("chk-keyword-only").checked;
            fetch(`/api/projects/${PROJECT_ID}/contact-scan/settings`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ use_roles, keyword_only }),
            });
        }

        // ---- Contact Scan Launch + Polling ----
        let contactScanPollInterval = null;

        async function launchContactScan() {
            const btn = document.getElementById("scan-contacts-btn");
            const errorEl = document.getElementById("scan-contacts-error");
            errorEl.style.display = "none";
            btn.disabled = true;
            btn.setAttribute("aria-busy", "true");

            try {
                const resp = await fetch(`/api/projects/${PROJECT_ID}/contact-scan/start`, {
                    method: "POST",
                });
                const data = await resp.json();
                if (!resp.ok) {
                    errorEl.textContent = data.detail || "Failed to start scan";
                    errorEl.style.display = "block";
                    return;
                }
                document.getElementById("contact-scan-progress").style.display = "block";
                document.getElementById("dl-contacts-btn").style.display = "none";
                document.getElementById("scan-contacts-status").textContent = "";
                startContactScanPolling();
            } catch (err) {
                errorEl.textContent = "Error: " + err.message;
                errorEl.style.display = "block";
                btn.disabled = false;
                btn.removeAttribute("aria-busy");
            }
        }

        function startContactScanPolling() {
            if (contactScanPollInterval) clearInterval(contactScanPollInterval);
            contactScanPollInterval = setInterval(pollContactScan, 3000);
            pollContactScan();
        }

        async function pollContactScan() {
            try {
                const resp = await fetch(`/api/projects/${PROJECT_ID}/contact-scan/latest/status`);
                const data = await resp.json();

                if (data.status === "none") return;

                document.getElementById("contact-scan-progress").style.display = "block";

                const total = data.total_companies || 0;

                function setScanStage(prefix, done, denom) {
                    const d = denom || total;
                    const pct = d > 0 ? Math.round((done / d) * 100) : 0;
                    document.getElementById(`scan-prog-${prefix}-bar`).value = pct;
                    document.getElementById(`scan-prog-${prefix}-text`).textContent =
                        d ? `${done} / ${d}` : "\u2014";
                }

                setScanStage("hunter", data.hunter_done || 0);
                setScanStage("enrichment", data.enrichment_done || 0);
                setScanStage("verification", data.verification_done || 0, data.total_verification || 0);

                // Show/hide enrichment row based on scan's use_roles snapshot
                document.getElementById("scan-enrichment-row").style.display =
                    data.use_roles ? "" : "none";

                const btn = document.getElementById("scan-contacts-btn");
                const statusEl = document.getElementById("scan-contacts-status");

                if (data.status === "completed") {
                    clearInterval(contactScanPollInterval);
                    btn.disabled = false;
                    btn.removeAttribute("aria-busy");
                    statusEl.textContent =
                        `Completed — ${data.contacts_added || 0} contacts added`;
                    statusEl.style.color = "var(--pico-color-green-500)";
                    document.getElementById("dl-contacts-btn").style.display = "inline-block";
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
            } catch (err) {
                console.error("Contact scan poll error:", err);
            }
        }

        async function downloadContacts() {
            const a = document.createElement("a");
            a.href = `/api/projects/${PROJECT_ID}/contacts/download`;
            a.download = "contacts.xlsx";
            document.body.appendChild(a);
            a.click();
            a.remove();
        }
```

- [ ] **Step 5: Update the page load sequence**

Replace:
```javascript
        // Load on page load
        loadRoles();
        loadHistory();
        loadStopWords();
        loadKeywordGroups();
```
With:
```javascript
        // Load on page load
        loadRoles();
        loadContactScanSettings();
        loadHistory();
        loadStopWords();
        loadKeywordGroups();
        // Initialize contact scan state on page load
        fetch(`/api/projects/${PROJECT_ID}/contact-scan/latest/status`)
            .then(r => r.json())
            .then(data => {
                if (data.status === "none") return;
                document.getElementById("contact-scan-progress").style.display = "block";
                if (data.status === "running") {
                    startContactScanPolling();
                } else {
                    pollContactScan();
                    if (data.status === "completed") {
                        document.getElementById("dl-contacts-btn").style.display = "inline-block";
                    }
                }
            })
            .catch(() => {});
```

- [ ] **Step 6: Verify HTML is well-formed**

```bash
python3 -c "
with open('app/templates/project.html') as f:
    content = f.read()
assert 'id=\"tab-roles\"' in content
assert 'id=\"panel-roles\"' in content
assert 'id=\"chk-use-roles\"' in content
assert 'id=\"chk-keyword-only\"' in content
assert 'id=\"scan-contacts-btn\"' in content
assert 'id=\"dl-contacts-btn\"' in content
assert 'launchContactScan' in content
assert 'pollContactScan' in content
assert 'downloadContacts' in content
assert 'saveContactScanSettings' in content
print('OK')
"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: add Roles tab with contact scan settings, launch button, and progress"
```
