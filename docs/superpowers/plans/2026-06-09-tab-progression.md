# Tab Progression Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat tab nav in `project.html` with a numbered 5-step progression stepper (Upload → Stop Words → Keywords → Roles → Export), add yellow/green state colors driven by real backend state, consolidate all download buttons into the new Export tab, and persist keyword scan results to the database.

**Architecture:** Pure CSS radio-input tab mechanism is kept; state colors are applied via JS class manipulation on `<label>` elements. A `refreshTabStates()` function fetches current project state on load and after each action. Keyword scan bytes are base64-encoded and stored in a new `projects.keyword_scan_result` column so the Export tab can fetch them at any time.

**Tech Stack:** FastAPI, Jinja2 templates, vanilla JS, PicoCSS, Supabase (PostgreSQL via `supabase-py`), pytest

---

## File Map

| File | Change |
|------|--------|
| `app/templates/project.html` | All frontend changes — CSS, HTML structure, JS logic |
| `app/main.py` | 2 new GET endpoints; modify `_run_scan_task` to save to DB |
| DB migration SQL (run manually in Supabase SQL editor) | Add `keyword_scan_result` column |

---

### Task 1: DB Migration — Add keyword_scan_result column

**Files:**
- Modify: Supabase DB (run SQL in Supabase dashboard → SQL Editor)

- [ ] **Step 1: Run the migration SQL**

Open Supabase dashboard → SQL Editor and run:

```sql
ALTER TABLE projects ADD COLUMN IF NOT EXISTS keyword_scan_result bytea;
```

- [ ] **Step 2: Verify column exists**

In Supabase SQL Editor run:

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'projects' AND column_name = 'keyword_scan_result';
```

Expected: one row returned with `data_type = 'bytea'`.

- [ ] **Step 3: Commit migration note**

```bash
git add -A
git commit -m "chore: add keyword_scan_result column to projects table"
```

---

### Task 2: Backend — Persist scan result to DB + new endpoints

**Files:**
- Modify: `app/main.py` lines 802–811 (`_run_scan_task`), add 2 endpoints after line 848

- [ ] **Step 1: Write test for base64 round-trip**

Create `tests/test_keyword_scan_persistence.py`:

```python
import base64


def test_keyword_scan_bytes_roundtrip():
    """Verify base64 encode/decode preserves XLSX bytes exactly."""
    original = b"PK\x03\x04fake_xlsx_content_bytes_here"
    encoded = base64.b64encode(original).decode()
    assert isinstance(encoded, str)
    decoded = base64.b64decode(encoded)
    assert decoded == original
```

- [ ] **Step 2: Run test to verify it passes**

```bash
cd /path/to/WEI_ABM_platform
pytest tests/test_keyword_scan_persistence.py -v
```

Expected: PASS (this is a logic test, no DB needed).

- [ ] **Step 3: Add `import base64` to main.py**

In `app/main.py`, the imports block at the top — add `import base64` after `import io`:

```python
import base64
import io
```

- [ ] **Step 4: Modify `_run_scan_task` to save to DB**

Replace the existing `_run_scan_task` function (lines 802–811) with:

```python
def _run_scan_task(job_id: str, project_id: str) -> None:
    try:
        scan_result = scan_project_keywords(project_id)
        buffer = generate_keyword_xlsx(scan_result)
        data = buffer.getvalue()
        # Persist to DB so Export tab can fetch it later
        encoded = base64.b64encode(data).decode()
        supabase.table("projects").update(
            {"keyword_scan_result": encoded}
        ).eq("id", project_id).execute()
        _scan_jobs[job_id] = {"status": "done", "result": data, "error": None, "ts": time.time()}
    except ValueError as e:
        _scan_jobs[job_id] = {"status": "error", "result": None, "error": str(e), "ts": time.time()}
    except Exception as e:
        logging.getLogger(__name__).exception("Keyword scan failed for project %s", project_id)
        _scan_jobs[job_id] = {"status": "error", "result": None, "error": "Scan failed unexpectedly", "ts": time.time()}
```

- [ ] **Step 5: Add two new endpoints after the existing `keyword_scan_job_download` endpoint (after line 848)**

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


@app.get("/api/projects/{project_id}/keyword-scan/download")
async def keyword_scan_db_download(project_id: str):
    """Streams the saved keyword scan XLSX from the DB."""
    result = (
        supabase.table("projects")
        .select("keyword_scan_result")
        .eq("id", project_id)
        .execute()
    )
    if not result.data or result.data[0].get("keyword_scan_result") is None:
        raise HTTPException(status_code=404, detail="No keyword scan result saved for this project")
    encoded = result.data[0]["keyword_scan_result"]
    data = base64.b64decode(encoded)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=keyword_analysis_{project_id[:8]}.xlsx"
        },
    )
```

**Important — route ordering:** These two new endpoints have paths that differ in segment count from the existing `{job_id}/status` and `{job_id}/download` routes, so FastAPI will not confuse them regardless of order. No reordering needed.

- [ ] **Step 6: Verify manually**

Start the server:
```bash
uvicorn app.main:app --reload
```

Open a browser and navigate to a project. Run a keyword scan. Then check:

```
GET /api/projects/<project_id>/keyword-scan/status
```
Expected: `{"has_result": true}`

```
GET /api/projects/<project_id>/keyword-scan/download
```
Expected: XLSX file downloads.

- [ ] **Step 7: Commit**

```bash
git add app/main.py tests/test_keyword_scan_persistence.py
git commit -m "feat: persist keyword scan result to DB, add status and download endpoints"
```

---

### Task 3: Frontend — CSS stepper styles

**Files:**
- Modify: `app/templates/project.html` — `<style>` block (lines 8–84)

- [ ] **Step 1: Replace the entire Tabs CSS block**

Find this block in `<style>` (lines 52–83):

```css
        /* ── Tabs ── */
        .tabs > input[type="radio"] { display: none; }
        .tabs > div[id^="panel-"] { display: none; }

        #tab-upload:checked    ~ #panel-upload    { display: block; }
        #tab-keywords:checked  ~ #panel-keywords  { display: block; }
        #tab-stopwords:checked ~ #panel-stopwords { display: block; }
        #tab-roles:checked     ~ #panel-roles     { display: block; }

        .tab-nav {
            display: flex;
            border-bottom: 2px solid var(--pico-muted-border-color);
            margin-bottom: 1.5em;
            gap: 0;
        }
        .tab-nav label {
            padding: 0.6em 1.5em;
            cursor: pointer;
            font-weight: 500;
            border-bottom: 3px solid transparent;
            margin-bottom: -2px;
            color: var(--pico-muted-color);
        }
        .tab-nav label:hover { color: var(--pico-color); }

        #tab-upload:checked    ~ .tab-nav label[for="tab-upload"],
        #tab-keywords:checked  ~ .tab-nav label[for="tab-keywords"],
        #tab-stopwords:checked ~ .tab-nav label[for="tab-stopwords"],
        #tab-roles:checked     ~ .tab-nav label[for="tab-roles"] {
            border-bottom-color: var(--pico-primary);
            color: var(--pico-primary);
        }
```

Replace it with:

```css
        /* ── Tabs ── */
        .tabs > input[type="radio"] { display: none; }
        .tabs > div[id^="panel-"] { display: none; }

        #tab-upload:checked    ~ #panel-upload    { display: block; }
        #tab-stopwords:checked ~ #panel-stopwords { display: block; }
        #tab-keywords:checked  ~ #panel-keywords  { display: block; }
        #tab-roles:checked     ~ #panel-roles     { display: block; }
        #tab-export:checked    ~ #panel-export    { display: block; }

        /* ── Step bar ── */
        .tab-nav {
            display: flex;
            align-items: center;
            margin-bottom: 1.5em;
            gap: 0;
            border-bottom: none;
            flex-wrap: wrap;
        }
        .tab-nav label {
            display: flex;
            align-items: center;
            gap: 0.4em;
            padding: 0.5em 0.6em;
            cursor: pointer;
            font-weight: 500;
            color: var(--pico-muted-color);
        }
        .tab-nav label:hover { color: var(--pico-color); }
        .step-num {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 1.7em;
            height: 1.7em;
            border-radius: 50%;
            background: var(--pico-muted-border-color);
            color: var(--pico-muted-color);
            font-size: 0.82em;
            font-weight: 700;
            flex-shrink: 0;
        }
        .step-arrow {
            color: var(--pico-muted-color);
            font-size: 1.1em;
            padding: 0 0.15em;
            pointer-events: none;
            user-select: none;
        }

        /* Active tab — primary circle when no state class */
        #tab-upload:checked    ~ .tab-nav label[for="tab-upload"]:not(.tab-yellow):not(.tab-green) .step-num,
        #tab-stopwords:checked ~ .tab-nav label[for="tab-stopwords"]:not(.tab-yellow):not(.tab-green) .step-num,
        #tab-keywords:checked  ~ .tab-nav label[for="tab-keywords"]:not(.tab-yellow):not(.tab-green) .step-num,
        #tab-roles:checked     ~ .tab-nav label[for="tab-roles"]:not(.tab-yellow):not(.tab-green) .step-num,
        #tab-export:checked    ~ .tab-nav label[for="tab-export"] .step-num {
            background: var(--pico-primary);
            color: white;
        }
        #tab-upload:checked    ~ .tab-nav label[for="tab-upload"]:not(.tab-yellow):not(.tab-green),
        #tab-stopwords:checked ~ .tab-nav label[for="tab-stopwords"]:not(.tab-yellow):not(.tab-green),
        #tab-keywords:checked  ~ .tab-nav label[for="tab-keywords"]:not(.tab-yellow):not(.tab-green),
        #tab-roles:checked     ~ .tab-nav label[for="tab-roles"]:not(.tab-yellow):not(.tab-green),
        #tab-export:checked    ~ .tab-nav label[for="tab-export"] {
            color: var(--pico-primary);
            font-weight: 700;
        }

        /* Yellow state (file uploaded / words added) */
        .tab-nav label.tab-yellow .step-num {
            background: #ffc107;
            color: #5a3e00;
        }
        .tab-nav label.tab-yellow { color: #856404; }

        /* Green state (processing complete) */
        .tab-nav label.tab-green .step-num {
            background: #198754;
            color: white;
        }
        .tab-nav label.tab-green { color: #198754; }

        /* Export disabled download buttons */
        .export-btn-disabled {
            opacity: 0.4;
            pointer-events: none;
            cursor: not-allowed;
        }
```

- [ ] **Step 2: Verify the server still starts without template errors**

```bash
uvicorn app.main:app --reload
```

Navigate to any project page. Expected: page loads, tabs are visible (may look odd until HTML is updated in Task 4).

- [ ] **Step 3: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: add stepper CSS for tab progression bar"
```

---

### Task 4: Frontend — HTML restructure (radio inputs + tab nav)

**Files:**
- Modify: `app/templates/project.html` lines 93–104 (radio inputs and nav)

- [ ] **Step 1: Replace the radio inputs and nav block**

Find (lines 93–104):

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

Replace with:

```html
        <div class="tabs">
            <input type="radio" id="tab-upload"    name="tab" checked>
            <input type="radio" id="tab-stopwords" name="tab">
            <input type="radio" id="tab-keywords"  name="tab">
            <input type="radio" id="tab-roles"     name="tab">
            <input type="radio" id="tab-export"    name="tab">

            <nav class="tab-nav">
                <label for="tab-upload"    id="label-upload">   <span class="step-num">1</span> Upload</label>
                <span class="step-arrow">›</span>
                <label for="tab-stopwords" id="label-stopwords"><span class="step-num">2</span> Stop Words</label>
                <span class="step-arrow">›</span>
                <label for="tab-keywords"  id="label-keywords"> <span class="step-num">3</span> Keywords</label>
                <span class="step-arrow">›</span>
                <label for="tab-roles"     id="label-roles">    <span class="step-num">4</span> Roles</label>
                <span class="step-arrow">›</span>
                <label for="tab-export"    id="label-export">   <span class="step-num">5</span> Export</label>
            </nav>
```

- [ ] **Step 2: Reorder the panels to match the new tab order**

The panels currently appear in this order in the HTML: upload, keywords, stopwords, roles.

Find `<!-- ── Keywords panel ── -->` (around line 196) and `<!-- ── Stop Words panel ── -->` (around line 219). Swap the two `<div id="panel-keywords">` and `<div id="panel-stopwords">` blocks so the order becomes:

```
panel-upload
panel-stopwords   ← was keywords
panel-keywords    ← was stopwords
panel-roles
```

(The Export panel is added in Task 5.)

- [ ] **Step 3: Verify page loads and tabs switch correctly**

```bash
uvicorn app.main:app --reload
```

Click each tab — Upload, Stop Words, Keywords, Roles should each show their content. Export tab is not yet wired (panel doesn't exist yet — clicking it shows nothing, which is fine at this stage).

- [ ] **Step 4: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: reorder tabs to Upload>StopWords>Keywords>Roles>Export, add stepper nav"
```

---

### Task 5: Frontend — Add Export panel

**Files:**
- Modify: `app/templates/project.html` — add Export panel before `</div><!-- /tabs -->`

- [ ] **Step 1: Remove the Download Section article from panel-upload**

Find and delete this block inside `panel-upload` (around lines 159–166):

```html
                <!-- Download Section -->
                <article class="download-section" id="download-section">
                    <h3>Download Results</h3>
                    <div class="grid">
                        <a id="dl-postings" role="button" href="#">Download Postings (.xlsx)</a>
                        <a id="dl-news" role="button" class="secondary" href="#">Download News (.xlsx)</a>
                    </div>
                </article>
```

- [ ] **Step 2: Remove the Download Contacts button from panel-roles**

Find and delete this block inside `panel-roles` (around lines 308–313):

```html
                    <div style="margin-top:1em;">
                        <button id="dl-contacts-btn" onclick="downloadContacts()" class="secondary"
                                style="display:none;">
                            Download Contacts (.xlsx)
                        </button>
                    </div>
```

- [ ] **Step 3: Add the Export panel before `</div><!-- /tabs -->`**

Find `</div><!-- /tabs -->` and insert the Export panel immediately before it:

```html
            <!-- ── Export panel ── -->
            <div id="panel-export">
                <article>
                    <h3>Export Results</h3>
                    <p style="font-size:0.85em; color:var(--pico-muted-color); margin-bottom:1.2em;">
                        Download outputs from completed steps. Grayed-out buttons become available once the corresponding step is done.
                    </p>
                    <div style="display:flex; flex-direction:column; gap:1em;">

                        <div style="display:flex; align-items:center; gap:0.75em; flex-wrap:wrap;">
                            <a id="dl-export-postings" role="button" href="#" class="export-btn-disabled">
                                Download Postings (.xlsx)
                            </a>
                            <span id="dl-export-postings-hint" style="font-size:0.82em; color:var(--pico-muted-color);">Complete Upload step first</span>
                        </div>

                        <div style="display:flex; align-items:center; gap:0.75em; flex-wrap:wrap;">
                            <a id="dl-export-news" role="button" class="secondary export-btn-disabled" href="#">
                                Download News (.xlsx)
                            </a>
                            <span id="dl-export-news-hint" style="font-size:0.82em; color:var(--pico-muted-color);">Complete Upload step first</span>
                        </div>

                        <div style="display:flex; align-items:center; gap:0.75em; flex-wrap:wrap;">
                            <a id="dl-export-keywords" role="button" class="contrast export-btn-disabled" href="#">
                                Download Keyword Analysis (.xlsx)
                            </a>
                            <span id="dl-export-keywords-hint" style="font-size:0.82em; color:var(--pico-muted-color);">Run keyword scan first</span>
                        </div>

                        <div style="display:flex; align-items:center; gap:0.75em; flex-wrap:wrap;">
                            <a id="dl-export-contacts" role="button" class="secondary export-btn-disabled" href="#"
                               onclick="downloadContacts(); return false;">
                                Download Contacts (.xlsx)
                            </a>
                            <span id="dl-export-contacts-hint" style="font-size:0.82em; color:var(--pico-muted-color);">Complete contact scan first</span>
                        </div>

                    </div>
                </article>
            </div><!-- /panel-export -->
```

- [ ] **Step 4: Verify Export tab shows download buttons (all grayed out initially)**

```bash
uvicorn app.main:app --reload
```

Click the Export tab. Expected: 4 grayed-out download buttons with hint text.

- [ ] **Step 5: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: add Export tab with consolidated download buttons"
```

---

### Task 6: Frontend — refreshTabStates() and updateExportButtons()

**Files:**
- Modify: `app/templates/project.html` — `<script>` block

- [ ] **Step 1: Add `setTabState`, `updateExportButtons`, and `refreshTabStates` functions**

Add these three functions to the `<script>` block, before the `// Load on page load` comment at the bottom:

```javascript
        // ---- Tab State Management ----

        function setTabState(tabName, state) {
            const label = document.querySelector(`.tab-nav label[for="tab-${tabName}"]`);
            if (!label) return;
            label.classList.remove('tab-yellow', 'tab-green');
            if (state) label.classList.add(`tab-${state}`);
        }

        function updateExportButtons(sessions, scanStatus, contactScan) {
            const completedSession = sessions.find(s => s.status === 'completed');

            const postingsBtn  = document.getElementById('dl-export-postings');
            const postingsHint = document.getElementById('dl-export-postings-hint');
            const newsBtn      = document.getElementById('dl-export-news');
            const newsHint     = document.getElementById('dl-export-news-hint');
            const kwBtn        = document.getElementById('dl-export-keywords');
            const kwHint       = document.getElementById('dl-export-keywords-hint');
            const contactsBtn  = document.getElementById('dl-export-contacts');
            const contactsHint = document.getElementById('dl-export-contacts-hint');

            if (completedSession) {
                postingsBtn.href = `/api/sessions/${completedSession.id}/postings/download`;
                postingsBtn.classList.remove('export-btn-disabled');
                postingsHint.style.display = 'none';

                newsBtn.href = `/api/sessions/${completedSession.id}/news/download`;
                newsBtn.classList.remove('export-btn-disabled');
                newsHint.style.display = 'none';
            } else {
                postingsBtn.href = '#';
                postingsBtn.classList.add('export-btn-disabled');
                postingsHint.style.display = '';

                newsBtn.href = '#';
                newsBtn.classList.add('export-btn-disabled');
                newsHint.style.display = '';
            }

            if (scanStatus.has_result) {
                kwBtn.href = `/api/projects/${PROJECT_ID}/keyword-scan/download`;
                kwBtn.classList.remove('export-btn-disabled');
                kwHint.style.display = 'none';
            } else {
                kwBtn.href = '#';
                kwBtn.classList.add('export-btn-disabled');
                kwHint.style.display = '';
            }

            if (contactScan.status === 'completed') {
                contactsBtn.classList.remove('export-btn-disabled');
                contactsHint.style.display = 'none';
            } else {
                contactsBtn.classList.add('export-btn-disabled');
                contactsHint.style.display = '';
            }
        }

        async function refreshTabStates() {
            try {
                const [sessionsResp, stopWordsResp, scanStatusResp, projectResp, contactScanResp, kwGroupsResp] =
                    await Promise.all([
                        fetch(`/api/projects/${PROJECT_ID}/sessions`),
                        fetch(`/api/projects/${PROJECT_ID}/stop-words`),
                        fetch(`/api/projects/${PROJECT_ID}/keyword-scan/status`),
                        fetch(`/api/projects/${PROJECT_ID}/details`),
                        fetch(`/api/projects/${PROJECT_ID}/contact-scan/latest/status`),
                        fetch(`/api/projects/${PROJECT_ID}/keyword-groups`),
                    ]);

                const sessions    = await sessionsResp.json();
                const stopWords   = await stopWordsResp.json();
                const scanStatus  = await scanStatusResp.json();
                const project     = await projectResp.json();
                const contactScan = await contactScanResp.json();
                const kwGroups    = await kwGroupsResp.json();

                // Tab 1 — Upload
                const hasCompleted  = sessions.some(s => s.status === 'completed');
                const hasAnySession = sessions.length > 0;
                setTabState('upload', hasCompleted ? 'green' : hasAnySession ? 'yellow' : '');

                // Tab 2 — Stop Words
                setTabState('stopwords', stopWords.length > 0 ? 'yellow' : '');

                // Tab 3 — Keywords
                const hasKeywords = kwGroups.some(g => g.keywords && g.keywords.length > 0);
                setTabState('keywords', scanStatus.has_result ? 'green' : hasKeywords ? 'yellow' : '');

                // Tab 4 — Roles
                const hasRoles          = (project.target_roles || []).length > 0;
                const contactScanDone   = contactScan.status === 'completed';
                setTabState('roles', contactScanDone ? 'green' : hasRoles ? 'yellow' : '');

                // Update Export tab download buttons
                updateExportButtons(sessions, scanStatus, contactScan);
            } catch (err) {
                console.error('refreshTabStates error:', err);
            }
        }
```

- [ ] **Step 2: Call `refreshTabStates()` on page load**

Find the `// Load on page load` section at the bottom of `<script>`. It currently reads:

```javascript
        // Load on page load
        loadRoles();
        loadContactScanSettings();
        loadHistory();
        loadStopWords();
        loadKeywordGroups();
```

Add `refreshTabStates();` after `loadKeywordGroups();`:

```javascript
        // Load on page load
        loadRoles();
        loadContactScanSettings();
        loadHistory();
        loadStopWords();
        loadKeywordGroups();
        refreshTabStates();
```

- [ ] **Step 3: Verify tab states load correctly on page load**

```bash
uvicorn app.main:app --reload
```

Open a project page that has at least one completed session. Expected: Upload tab circle turns green. If keyword groups exist, Keywords tab turns yellow. Etc.

- [ ] **Step 4: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: add refreshTabStates() for live tab color state management"
```

---

### Task 7: Frontend — Wire refresh calls into action completions

**Files:**
- Modify: `app/templates/project.html` — `<script>` block (pollStatus, pollContactScan, page-load contact scan init)

- [ ] **Step 1: Update `pollStatus` to call `refreshTabStates` on completion**

Find the `if (data.status === "completed")` block inside `pollStatus` (around line 410):

```javascript
                if (data.status === "completed") {
                    clearInterval(pollInterval);
                    showDownloadLinks(sessionId);
                    loadHistory();
                }
```

Replace with:

```javascript
                if (data.status === "completed") {
                    clearInterval(pollInterval);
                    loadHistory();
                    refreshTabStates();
                }
```

- [ ] **Step 2: Remove the `showDownloadLinks` function**

Find and delete this function entirely (around lines 427–432):

```javascript
        // ---- Downloads ----
        function showDownloadLinks(sessionId) {
            const section = document.getElementById("download-section");
            section.style.display = "block";
            document.getElementById("dl-postings").href = `/api/sessions/${sessionId}/postings/download`;
            document.getElementById("dl-news").href = `/api/sessions/${sessionId}/news/download`;
        }
```

- [ ] **Step 3: Update `pollContactScan` to call `refreshTabStates` on completion**

Find the `if (data.status === "completed")` block inside `pollContactScan` (around lines 926–934):

```javascript
                if (data.status === "completed") {
                    clearInterval(contactScanPollInterval);
                    btn.disabled = false;
                    btn.removeAttribute("aria-busy");
                    statusEl.textContent =
                        `Completed — ${data.contacts_added || 0} contacts added`;
                    statusEl.style.color = "var(--pico-color-green-500)";
                    document.getElementById("dl-contacts-btn").style.display = "inline-block";
                }
```

Replace with:

```javascript
                if (data.status === "completed") {
                    clearInterval(contactScanPollInterval);
                    btn.disabled = false;
                    btn.removeAttribute("aria-busy");
                    statusEl.textContent =
                        `Completed — ${data.contacts_added || 0} contacts added`;
                    statusEl.style.color = "var(--pico-color-green-500)";
                    refreshTabStates();
                }
```

- [ ] **Step 4: Update page-load contact scan init block**

Find the block at the bottom of `<script>` (around lines 966–980):

```javascript
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

Replace with:

```javascript
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
                }
            })
            .catch(() => {});
```

- [ ] **Step 5: Wire `refreshTabStates` into stop-word and keyword/role mutations**

After each successful `addStopWord`, `deleteStopWord`, `importStopWordFile`, `addKeyword`, `deleteKeyword`, `importKeywordFile`, `addKeywordGroup`, `deleteKeywordGroup`, `addRole`, `removeRole`, `importRolesFile` — add a `refreshTabStates()` call.

For `addStopWord` (find the `if (resp.ok)` block):

```javascript
            if (resp.ok) {
                input.value = "";
                loadStopWords();
                refreshTabStates();
            }
```

For `deleteStopWord` (find the last `loadStopWords()` call inside it):

```javascript
            loadStopWords();
            refreshTabStates();
```

For `importStopWordFile` (find the `status.textContent = \`Done...` line and add after `loadStopWords()`):

```javascript
                    loadStopWords();
                    refreshTabStates();
```

For `addKeywordGroup` (find the `loadKeywordGroups()` call):

```javascript
            loadKeywordGroups();
            refreshTabStates();
```

For `deleteKeywordGroup` (find the `loadKeywordGroups()` call inside it):

```javascript
            loadKeywordGroups();
            refreshTabStates();
```

For `addKeyword` (find the `loadKeywordGroups()` call inside it):

```javascript
            loadKeywordGroups();
            refreshTabStates();
```

For `importKeywordFile` (find the `loadKeywordGroups()` call inside it):

```javascript
                    loadKeywordGroups();
                    refreshTabStates();
```

For `addRole` (find `renderRoles(); await saveRoles();`):

```javascript
            renderRoles();
            await saveRoles();
            refreshTabStates();
```

For `removeRole` (find `renderRoles(); await saveRoles();`):

```javascript
            renderRoles();
            await saveRoles();
            refreshTabStates();
```

For `importRolesFile` (find the `loadRoles()` call inside it):

```javascript
                    loadRoles();
                    refreshTabStates();
```

- [ ] **Step 6: Verify end-to-end tab state behavior**

```bash
uvicorn app.main:app --reload
```

Walk through each scenario:
- Add a stop word → Stop Words tab turns yellow
- Add a keyword group with a keyword → Keywords tab turns yellow
- Add a role → Roles tab turns yellow
- Complete a session upload → Upload tab turns green (after polling completes)
- Run a keyword scan → Keywords tab turns green
- Complete a contact scan → Roles tab turns green
- Export tab download buttons become available as steps complete

- [ ] **Step 7: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: wire refreshTabStates into all action completions and mutations"
```

---

### Task 8: Frontend — Keywords tab: decouple scan from download

**Files:**
- Modify: `app/templates/project.html` — `panel-keywords` HTML and `runKeywordScan` JS function

- [ ] **Step 1: Update the scan button label in panel-keywords**

Find (in `panel-keywords`, around line 211):

```html
                    <button id="scan-btn" onclick="runKeywordScan()" class="contrast" style="margin-top:0.5em;">
                        Scan Postings &amp; Download XLSX
                    </button>
```

Replace with:

```html
                    <button id="scan-btn" onclick="runKeywordScan()" class="contrast" style="margin-top:0.5em;">
                        Run Keyword Scan
                    </button>
```

- [ ] **Step 2: Replace the `runKeywordScan` function**

Find the entire `runKeywordScan` function in `<script>` (around lines 691–743) and replace it with:

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

- [ ] **Step 3: Verify scan behavior**

```bash
uvicorn app.main:app --reload
```

Go to the Keywords tab. Click "Run Keyword Scan". Expected:
- Button shows spinner while running
- No download triggers automatically
- After completion, Keywords tab circle turns green
- Export tab → Download Keyword Analysis button becomes active

- [ ] **Step 4: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: decouple keyword scan from download; scan saves to DB, download lives in Export tab"
```

---

## Self-Review

**Spec coverage:**
- [x] Tab order: Upload(1) → Stop Words(2) → Keywords(3) → Roles(4) → Export(5) — Task 4
- [x] Numbered circles with arrow separators — Tasks 3, 4
- [x] Yellow/green state colors — Tasks 3, 6
- [x] State reflects actual backend state on page load — Task 6
- [x] State refreshes on mutations (add word, add keyword, etc.) — Task 7
- [x] Export tab with conditional download buttons — Task 5
- [x] Download buttons removed from Upload and Roles panels — Task 5
- [x] Keywords scan decoupled from download — Task 8
- [x] Scan result persisted to DB — Task 2
- [x] New `/keyword-scan/status` endpoint — Task 2
- [x] New `/keyword-scan/download` endpoint — Task 2
- [x] DB migration — Task 1

**Placeholder scan:** None found.

**Type/name consistency:**
- `export-btn-disabled` CSS class used consistently in Tasks 3, 5, 6
- `refreshTabStates()` referenced in Tasks 6, 7, 8 — defined once in Task 6
- `setTabState(tabName, state)` defined in Task 6, not called directly from other tasks (called inside `refreshTabStates`)
- `updateExportButtons(sessions, scanStatus, contactScan)` defined in Task 6, called inside `refreshTabStates`
- Element IDs `dl-export-postings`, `dl-export-news`, `dl-export-keywords`, `dl-export-contacts` defined in Task 5, referenced in Task 6
- `label-upload`, `label-stopwords`, `label-keywords`, `label-roles`, `label-export` are `id` attributes added in Task 4 but not used by JS (CSS selectors use `[for="tab-X"]` instead) — no issue
