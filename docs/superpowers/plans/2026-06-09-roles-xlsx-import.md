# Roles xlsx Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add xlsx import for target roles on the project detail page and remove the roles input from the Create Project form on the home screen.

**Architecture:** Add `parse_roles_xlsx()` to the existing `keyword_parser` service (identical shape to `parse_stop_word_xlsx`), expose it via a new `POST /api/projects/{project_id}/roles/import` endpoint that merges with existing roles (case-insensitive dedup), then update both HTML templates.

**Tech Stack:** Python/FastAPI, openpyxl, Jinja2 HTML templates, vanilla JS fetch API

---

## File Map

| File | Change |
|------|--------|
| `app/services/keyword_parser.py` | Add `parse_roles_xlsx()` |
| `tests/test_keyword_parser.py` | Add tests for `parse_roles_xlsx()` |
| `app/main.py` | Add import + `POST /api/projects/{project_id}/roles/import` |
| `app/templates/projects.html` | Remove roles input from Create Project form |
| `app/templates/project.html` | Add "Import from xlsx" button + JS to roles section |

---

### Task 1: Add `parse_roles_xlsx()` with tests

**Files:**
- Modify: `app/services/keyword_parser.py`
- Modify: `tests/test_keyword_parser.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_keyword_parser.py`, update the import on line 5:

```python
from app.services.keyword_parser import parse_keyword_xlsx, parse_stop_word_xlsx, parse_roles_xlsx
```

Then append these tests at the end of the file:

```python
def test_parse_roles_basic():
    data = _make_single_col_xlsx(["CEO", "CTO", "HR Director"])
    result = parse_roles_xlsx(data)
    assert result == ["CEO", "CTO", "HR Director"]


def test_parse_roles_skips_empty():
    data = _make_single_col_xlsx(["CEO", "", None, "CFO"])
    result = parse_roles_xlsx(data)
    assert result == ["CEO", "CFO"]


def test_parse_roles_strips_whitespace():
    data = _make_single_col_xlsx(["  CEO  ", "CTO"])
    result = parse_roles_xlsx(data)
    assert result == ["CEO", "CTO"]


def test_parse_roles_empty_file():
    data = _make_single_col_xlsx([])
    result = parse_roles_xlsx(data)
    assert result == []


def test_parse_roles_ignores_extra_columns():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["CEO", "extra column"])
    buf = io.BytesIO()
    wb.save(buf)
    result = parse_roles_xlsx(buf.getvalue())
    assert result == ["CEO"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/pashache/Desktop/projects/WEI group/ABM/WEI_ABM_platform"
pytest tests/test_keyword_parser.py -k "parse_roles" -v
```

Expected: `ERROR` — `ImportError: cannot import name 'parse_roles_xlsx'`

- [ ] **Step 3: Add `parse_roles_xlsx()` to `app/services/keyword_parser.py`**

Append after the last line of `app/services/keyword_parser.py` (currently line 62):

```python


def parse_roles_xlsx(file_bytes: bytes) -> list[str]:
    """
    Parse an Excel file with one column of role names, one role per row.

    Returns list of non-empty stripped strings.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    results = []
    for row in ws.iter_rows(values_only=True):
        if not row:
            continue
        raw_role = row[0]
        role = str(raw_role).strip() if raw_role is not None else ""
        if role:
            results.append(role)
    wb.close()
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_keyword_parser.py -k "parse_roles" -v
```

Expected: 5 tests PASSED

- [ ] **Step 5: Run full parser test suite to check for regressions**

```bash
pytest tests/test_keyword_parser.py -v
```

Expected: all tests PASSED

- [ ] **Step 6: Commit**

```bash
git add app/services/keyword_parser.py tests/test_keyword_parser.py
git commit -m "feat: add parse_roles_xlsx to keyword_parser"
```

---

### Task 2: Add `/roles/import` endpoint

**Files:**
- Modify: `app/main.py` (line 18 — import, then add endpoint after `update_project` at line 103)

- [ ] **Step 1: Update the import on line 18 of `app/main.py`**

Replace:
```python
from app.services.keyword_parser import parse_keyword_xlsx, parse_stop_word_xlsx
```
With:
```python
from app.services.keyword_parser import parse_keyword_xlsx, parse_stop_word_xlsx, parse_roles_xlsx
```

- [ ] **Step 2: Add the endpoint after `update_project` (after line 103)**

Insert this block immediately after the closing of `update_project` (after `return result.data[0]`):

```python

@app.post("/api/projects/{project_id}/roles/import")
async def import_roles(project_id: str, file: UploadFile = File(...)):
    if not (file.filename or "").endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="File must be .xlsx")

    file_bytes = await file.read()
    try:
        parsed = parse_roles_xlsx(file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = supabase.table("projects").select("target_roles").eq("id", project_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Project not found")

    existing = result.data[0].get("target_roles") or []
    existing_lower = {r.lower() for r in existing}

    roles_added = 0
    roles_skipped = 0
    merged = list(existing)
    for role in parsed:
        if role.lower() in existing_lower:
            roles_skipped += 1
        else:
            merged.append(role)
            existing_lower.add(role.lower())
            roles_added += 1

    supabase.table("projects").update({"target_roles": merged}).eq("id", project_id).execute()

    return {"added": roles_added, "skipped": roles_skipped}
```

- [ ] **Step 3: Verify the server starts without errors**

```bash
cd "/Users/pashache/Desktop/projects/WEI group/ABM/WEI_ABM_platform"
uvicorn app.main:app --port 8000
```

Expected: server starts, no import or syntax errors. Stop with Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "feat: add POST /api/projects/{project_id}/roles/import endpoint"
```

---

### Task 3: Remove roles input from Create Project form

**Files:**
- Modify: `app/templates/projects.html`

- [ ] **Step 1: Remove the roles `<input>` element**

In `app/templates/projects.html`, remove line 44:
```html
                <input type="text" id="project-roles" placeholder="Target roles for contact enrichment (comma-separated), e.g.: CEO, CTO, HR Director" style="margin:0; font-size:0.9em;">
```

The form block should now read:
```html
            <form id="create-form">
                <div style="display:flex; gap:1em; align-items:end; margin-bottom:0.5em;">
                    <input type="text" id="project-name" placeholder="New project name" required style="margin:0;">
                    <button type="submit" style="margin:0; white-space:nowrap;">Create Project</button>
                </div>
            </form>
```

- [ ] **Step 2: Remove roles JS from the submit handler**

In `app/templates/projects.html`, replace the submit event listener (lines 84–104) with:

```javascript
        document.getElementById("create-form").addEventListener("submit", async (e) => {
            e.preventDefault();
            const input = document.getElementById("project-name");
            const name = input.value.trim();
            if (!name) return;

            await fetch("/api/projects", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name }),
            });
            input.value = "";
            loadProjects();
        });
```

- [ ] **Step 3: Commit**

```bash
git add app/templates/projects.html
git commit -m "feat: remove target roles input from create project form"
```

---

### Task 4: Add xlsx import button to roles section in `project.html`

**Files:**
- Modify: `app/templates/project.html`

- [ ] **Step 1: Add file input and import button to the roles section**

In `app/templates/project.html`, replace lines 109–112:
```html
                    <div style="display:flex; gap:0.5em; align-items:center; margin-top:0.5em;">
                        <input type="text" id="new-role-input" placeholder="Add role, e.g. CEO, CTO" style="margin:0; padding:0.3em 0.5em; font-size:0.9em;">
                        <button onclick="addRole()" style="margin:0; padding:0.3em 0.8em; font-size:0.85em; white-space:nowrap;">Add</button>
                    </div>
```

With:
```html
                    <div style="display:flex; gap:0.5em; align-items:center; margin-top:0.5em; flex-wrap:wrap;">
                        <input type="text" id="new-role-input" placeholder="Add role, e.g. CEO, CTO" style="margin:0; padding:0.3em 0.5em; font-size:0.9em;">
                        <button onclick="addRole()" style="margin:0; padding:0.3em 0.8em; font-size:0.85em; white-space:nowrap;">Add</button>
                        <button id="import-roles-btn" class="secondary" style="margin:0; padding:0.3em 0.8em; font-size:0.85em; white-space:nowrap;"
                                onclick="document.getElementById('roles-import-input').click()">Import from xlsx</button>
                        <input type="file" id="roles-import-input" accept=".xlsx" style="display:none"
                               onchange="importRolesFile(this)">
                        <span id="roles-import-status" style="font-size:0.85em; color:var(--pico-muted-color);"></span>
                    </div>
```

- [ ] **Step 2: Add `importRolesFile()` JS function**

In `app/templates/project.html`, add the following function after the `removeRole` function (after line 789, before the `// Load on page load` comment):

```javascript
        async function importRolesFile(input) {
            const file = input.files[0];
            if (!file) return;
            const btn = document.getElementById("import-roles-btn");
            const status = document.getElementById("roles-import-status");
            btn.disabled = true;
            status.textContent = "Importing...";
            try {
                const formData = new FormData();
                formData.append("file", file);
                const resp = await fetch(`/api/projects/${PROJECT_ID}/roles/import`, {
                    method: "POST",
                    body: formData,
                });
                let data = {};
                try { data = await resp.json(); } catch (_) {}
                if (!resp.ok) {
                    status.textContent = "Error: " + (data.detail || resp.statusText || "Import failed");
                } else {
                    status.textContent = `Done: ${data.added} added, ${data.skipped} skipped`;
                    await loadRoles();
                }
            } catch (err) {
                status.textContent = "Error: " + err.message;
            } finally {
                btn.disabled = false;
                input.value = "";
            }
        }
```

- [ ] **Step 3: End-to-end verification**

1. Start the server: `uvicorn app.main:app --reload --port 8000`
2. Open `http://localhost:8000` — confirm the Create Project form shows only the name input (no roles field)
3. Create a project and open it
4. In the "Target Roles" section, click "Import from xlsx"
5. Select `/Users/pashache/Desktop/projects/WEI group/ABM/WEI_ABM_platform/analysis 08.06/roles.xlsx`
6. Verify: status shows `Done: 12 added, 0 skipped`; all 12 roles appear as tags
7. Import the same file again — status should show `Done: 0 added, 12 skipped`
8. Stop server with Ctrl+C

- [ ] **Step 4: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: add import roles from xlsx to project detail page"
```
