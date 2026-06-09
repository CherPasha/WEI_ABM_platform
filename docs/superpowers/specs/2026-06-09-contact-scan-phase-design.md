# Contact Scanning as a Separate Phase — Design Spec

**Date:** 2026-06-09
**Status:** Approved

## Summary

Move contact discovery (Hunter.io domain search, LLM role enrichment, email verification) out of the session processing pipeline into a new manually-triggered, per-project contact scan. Add a "Roles" tab in the project detail page that hosts roles management, scan settings, launch button, and inline progress. Replace the per-session contacts export with a two-sheet project-level xlsx export. Store keyword hit counts on companies so the scan can filter to only keyword-matched companies.

---

## Goals

- Session processing ends at news (`finding_news` → `completed`). No contact stages.
- Contacts are found by a separate per-project contact scan, triggered manually from the UI.
- Scan settings: (1) use LLM role enrichment or not; (2) scan all companies or only those with ≥1 keyword hit.
- On re-run: always scan all target companies, dedup on email — no existing contacts deleted.
- Old session-produced contacts (pre-migration) remain in DB, untouched and not exported.
- New contacts are linked to a `contact_scan_id`, not `session_id`.
- Export is a two-sheet xlsx at project level, replacing the per-session contacts download.

---

## 1. Database Schema Changes

### 1a. `projects` table — add two columns

```sql
ALTER TABLE projects
  ADD COLUMN contact_scan_use_roles BOOLEAN NOT NULL DEFAULT true,
  ADD COLUMN contact_scan_keyword_only BOOLEAN NOT NULL DEFAULT false;
```

### 1b. New `contact_scans` table

```sql
CREATE TABLE contact_scans (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  status            TEXT NOT NULL DEFAULT 'running',
    -- 'running' | 'completed' | 'failed'
  use_roles         BOOLEAN NOT NULL,
  keyword_only      BOOLEAN NOT NULL,
  total_companies   INT NOT NULL DEFAULT 0,
  hunter_done       INT NOT NULL DEFAULT 0,
  enrichment_done   INT NOT NULL DEFAULT 0,
  total_verification INT NOT NULL DEFAULT 0,
  verification_done INT NOT NULL DEFAULT 0,
  contacts_added    INT NOT NULL DEFAULT 0,
  error_message     TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`use_roles` and `keyword_only` are snapshotted from the project settings at the moment the scan starts, so the record is self-describing.

### 1c. `contacts` table — add `contact_scan_id`

```sql
ALTER TABLE contacts
  ADD COLUMN contact_scan_id UUID REFERENCES contact_scans(id) ON DELETE CASCADE;
```

`session_id` remains nullable on the table. Existing rows keep their `session_id`. New contacts from the scan have `contact_scan_id` set and `session_id` null.

### 1d. `companies` table — add keyword hit columns

```sql
ALTER TABLE companies
  ADD COLUMN keyword_hit_count INT NOT NULL DEFAULT 0,
  ADD COLUMN keyword_group_count INT NOT NULL DEFAULT 0;
```

Updated by the keyword scanner each time it runs. The contact scan reads these to apply the `keyword_only` filter.

### 1e. `sessions` table — dead columns (no migration)

`run_contacts`, `run_enrichment`, `run_verification` become dead columns. They are kept to avoid breaking old session rows. No new code reads or writes them.

---

## 2. Backend

### 2a. `app/services/session_processor.py`

Remove the three contact stages:
- `finding_contacts` (Hunter.io domain search)
- `enriching_contacts` (LLM role enrichment)
- `verifying_emails` (Hunter.io verification)

Pipeline becomes: `parsing` → `resolving_names` → `finding_postings` → `finding_news` → `completed`.

The `process_session()` and `resume_session()` functions no longer reference `contacts_done`, `enrichment_done`, `verification_done`, `total_verification`, `run_contacts`, `run_enrichment`, or `run_verification`.

### 2b. New `app/services/contact_scanner.py`

Single public function `run_contact_scan(scan_id: str)`. Extracted from the removed contact stages of `session_processor.py`, adapted for per-project scope.

**Logic:**

```
1. Fetch scan record (use_roles, keyword_only, project_id)
2. Fetch all companies across all sessions for this project
   - If keyword_only: filter WHERE keyword_hit_count > 0
3. Set total_companies on scan record
4. For each company:
   a. Run Hunter.io domain search → new contacts (dedup against existing emails for this company)
   b. Save new Hunter contacts with contact_scan_id set
   c. Increment hunter_done
   d. If use_roles and project.target_roles is non-empty:
      - Run LLM enrichment (find_people_by_roles) → candidate people
      - Generate emails from detected pattern
      - Dedup against existing contacts for this company
      - Save new enriched contacts with contact_scan_id set
      e. Increment enrichment_done
5. Set total_verification = count of new contacts with emails
6. For each new contact with an email:
   a. Run Hunter.io email verification
   b. Update contact verification fields
   c. Increment verification_done
7. Update contacts_added = total new contacts saved
8. Set status = 'completed'
```

On any unhandled exception: set status = 'failed', save error_message.

**Deduplication rule:** A contact is a duplicate if a contact with the same `email` (case-insensitive) already exists for the same `company_id`, regardless of which scan produced it. This prevents re-adding old session-produced contacts as well.

### 2c. `app/services/keyword_scanner.py`

After computing keyword hits per company (existing logic), write results back:

```python
supabase.table("companies").update({
    "keyword_hit_count": hit_count,
    "keyword_group_count": group_count,
}).eq("id", company_id).execute()
```

Called for every company in the scan, including those with 0 hits (to reset from prior scans).

### 2d. `app/main.py` — new and changed endpoints

**New endpoints:**

`POST /api/projects/{project_id}/contact-scan/start`
- Reads current `contact_scan_use_roles` and `contact_scan_keyword_only` from project
- Creates a `contact_scans` row with those values and status `'running'`
- Launches `run_contact_scan(scan_id)` as a `BackgroundTask`
- Returns `{"scan_id": "..."}`
- If a scan is already `running` for this project: returns 409 Conflict

`GET /api/projects/{project_id}/contact-scan/latest/status`
- Returns the most recent `contact_scans` row for the project ordered by `created_at DESC`
- Returns all progress fields: `status`, `use_roles`, `keyword_only`, `total_companies`, `hunter_done`, `enrichment_done`, `total_verification`, `verification_done`, `contacts_added`, `error_message`
- Returns `{"status": "none"}` if no scan has ever been run

`PUT /api/projects/{project_id}/contact-scan/settings`
- Body: `{"use_roles": bool, "keyword_only": bool}`
- Updates `contact_scan_use_roles` and `contact_scan_keyword_only` on the project
- Returns the updated project row

**Changed endpoint:**

`GET /api/projects/{project_id}/contacts/download` *(replaces `GET /api/sessions/{session_id}/contacts/download`)*
- Fetches all contacts WHERE `contact_scan_id` IS NOT NULL AND company belongs to this project (via contacts → companies → sessions → project_id). Includes contacts from all past scans (contacts accumulate across runs via dedup).
- Filters to contacts where `email_status` IS NULL OR `email_status` IN ('valid', 'accept_all')
- Builds a two-sheet xlsx (see Section 3)
- Returns as `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`

**Removed endpoint:**

`GET /api/sessions/{session_id}/contacts/download` — removed. No replacement for session-level export.

### 2e. `app/models.py`

Add:
```python
class ContactScanSettings(BaseModel):
    use_roles: bool
    keyword_only: bool
```

---

## 3. Two-Sheet xlsx Export

### Sheet 1 — "Companies"

One row per company that has at least one contact with `contact_scan_id IS NOT NULL` (from any scan, all scans accumulate).

| Column | Source |
|--------|--------|
| Company | `companies.legal_name` |
| INN | `companies.inn` |
| Session | `sessions.filename` (via company → session) |
| Contacts Found | COUNT of contacts for this company |
| Keywords Found | `companies.keyword_hit_count` |
| Keyword Groups Found | `companies.keyword_group_count` |

Sorted by Contacts Found descending.

### Sheet 2 — "Contacts"

One row per contact. Same columns as the current per-session contacts download:

`company_name`, `email`, `confidence`, `first_name`, `last_name`, `position`, `position_raw`, `seniority`, `department`, `linkedin`, `phone_number`, `source`, `email_status`, `email_score`, `email_regexp`, `email_gibberish`, `email_disposable`, `email_webmail`, `email_mx_records`, `email_smtp_server`, `email_smtp_check`, `email_accept_all`, `email_block`, `email_verified_at`

Sorted by company_name, then last_name.

---

## 4. Frontend (`app/templates/project.html`)

### 4a. New "Roles" tab

Add after the Stop Words tab in the tab bar:

```html
<input type="radio" id="tab-roles" name="tab">
<label for="tab-roles">Roles</label>
```

CSS: add `#tab-roles:checked ~ #panel-roles { display: block; }` and active label style.

**Tab panel content (`#panel-roles`):**

```
[ Roles management section ]
  — existing roles tags (moved from Upload tab)
  — Add input + Add button
  — Import from xlsx button (already built)

[ Contact Scan Settings ]
  ☑ Use roles for enrichment
  ☐ Keyword companies only

[ Launch Contact Scan button ]  (disabled while scan is running)

[ Progress section ] (hidden until first scan)
  Hunter.io:    [progress bar]  N / total
  Enrichment:   [progress bar]  N / total  (hidden if use_roles=false)
  Verification: [progress bar]  N / total
  Status: Completed / Failed: {error}

[ Download Contacts button ]  (enabled only when status = 'completed')
```

### 4b. Settings toggles behavior

Both checkboxes call `PUT /api/projects/{project_id}/contact-scan/settings` on `change`, immediately saving to the server (same fire-and-forget pattern as `saveRoles()`).

Settings are loaded on page load from `GET /api/projects/{project_id}/details` (the `contact_scan_use_roles` and `contact_scan_keyword_only` fields from the project row). The `project_details` endpoint in `main.py` must be updated to include these two columns in its Supabase `select()` call.

### 4c. Scan launch and polling

On "Launch Contact Scan" click:
1. POST to `/api/projects/{project_id}/contact-scan/start`
2. Show progress section, disable launch button, set `aria-busy`
3. Poll `/api/projects/{project_id}/contact-scan/latest/status` every 3s
4. Update progress bars on each poll
5. On `status = 'completed'`: stop polling, enable Download button, re-enable launch button
6. On `status = 'failed'`: stop polling, show error message, re-enable launch button

On page load: call `GET /api/projects/{project_id}/contact-scan/latest/status`. If `status = 'running'`, start polling immediately. If `status = 'completed'`, show completed state and enable Download button.

### 4d. Upload tab cleanup

Remove from the upload form:
- `run_contacts` checkbox and label
- `run_enrichment` checkbox and label
- `run_verification` checkbox and label

Remove from the session history progress display:
- "Contacts (Hunter.io)" progress bar/column
- "Contact Enrichment" progress bar/column
- "Email Verification" progress bar/column

Session history now shows: Resolving Names, Job Postings, News.

The roles section in the Upload tab is removed (moved to the new Roles tab).

---

## 5. Error Handling

| Scenario | Behavior |
|----------|----------|
| Scan launched while another is running | `POST /start` returns 409; frontend shows "Scan already in progress" |
| No sessions / no companies in project | `total_companies = 0`, scan immediately sets `status = 'completed'` |
| `keyword_only = true` but no keyword scan run yet | All companies have `keyword_hit_count = 0`; scan runs on 0 companies; completes immediately with `contacts_added = 0` |
| Hunter.io API error on one company | Log and skip that company; continue with rest; do not fail the whole scan |
| LLM error on one company | Log and skip enrichment for that company; Hunter contacts still saved |
| Download requested with no completed scan | Return 404 |

---

## 6. Testing

- Unit test `run_contact_scan`: mock supabase and Hunter/LLM calls, verify progress counters increment, verify dedup logic, verify keyword_only filter
- Unit test keyword_scanner update: verify keyword_hit_count and keyword_group_count written to companies
- Integration test `POST /contact-scan/start`: 409 on double-start
- Integration test `GET /contacts/download`: two-sheet structure, correct column names, only contact_scan contacts included
- Frontend: verify upload form no longer has contact checkboxes; verify Roles tab renders settings and progress
