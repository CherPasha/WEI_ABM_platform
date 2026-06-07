# Keyword Group Import from XLSX — Design Spec

**Date:** 2026-06-05
**Status:** Approved

---

## Overview

Add the ability to import keyword groups and keywords into a project from an Excel file structured like `WEI. ABM. Маркеры 05.06.2026.xlsx`, instead of entering them manually one by one.

**Merge behavior:** additive. New groups are created; if a group with the same name already exists, new keywords are added to it. Duplicate keywords (case-insensitive) are skipped.

---

## File Format

| Column A (group name) | Column B (keywords)                                               |
|-----------------------|-------------------------------------------------------------------|
| ABM                   | "ABM маркетинг", "Account based marketing", "ABM стратегия"      |
| Конкуренты            | "конкурент", "аналог", "альтернатива"                             |

- First two columns are used regardless of header names
- Keywords in column B are comma-space-separated, each wrapped in double quotes
- Rows with an empty group name are skipped

---

## Architecture

### New file: `app/services/keyword_parser.py`

Single function:

```python
def parse_keyword_xlsx(file_bytes: bytes) -> list[dict]:
    """
    Returns list of { "group": str, "keywords": list[str] }
    Raises ValueError on structural errors (< 2 columns, unreadable file).
    """
```

**Parsing logic:**
1. `pd.read_excel(BytesIO(file_bytes))` — uses existing pandas dependency
2. Assert at least 2 columns, else raise `ValueError`
3. For each row: take col[0] as group name, col[1] as keyword string
4. Skip rows where group name is empty/NaN
5. Parse keywords: `re.findall(r'"([^"]+)"', keyword_string)` — extracts all quoted tokens
6. Return list of `{group, keywords}` dicts

---

### New endpoint: `POST /api/projects/{project_id}/keyword-groups/import`

In `app/main.py`, alongside existing keyword-group routes (~line 348).

**Request:** `multipart/form-data` with field `file` (`.xlsx` or `.xls`)

**Logic:**
1. Validate file extension → 400 if not xlsx/xls
2. Call `parse_keyword_xlsx(file_bytes)` → 400 on `ValueError` with message
3. Fetch all existing groups for project from Supabase
4. For each parsed row:
   - Find existing group by name (case-sensitive match) or create new one
   - Fetch existing keywords for that group
   - Insert keywords not already present (case-insensitive dedup)
5. Return 200 JSON:
   ```json
   { "groups_created": 2, "groups_updated": 1, "keywords_added": 15, "keywords_skipped": 3 }
   ```

**Error responses:**
- `400 {"detail": "File must be .xlsx or .xls"}`
- `400 {"detail": "File must have at least 2 columns"}`

---

### Frontend changes: `app/templates/project.html`

**In the Keyword Analysis section** (around line 151–161), add next to the "Add Group" button:

```html
<button onclick="document.getElementById('kw-import-input').click()">Import from file</button>
<input type="file" id="kw-import-input" accept=".xlsx,.xls" style="display:none">
<span id="kw-import-status"></span>
```

**JS handler (new function `importKeywordFile()`):**
1. Triggered on `change` event of `#kw-import-input`
2. Disables button, shows "Importing..."
3. POSTs `FormData` with file to `/api/projects/{projectId}/keyword-groups/import`
4. On success: shows "Added {keywords_added} keywords in {groups_created + groups_updated} groups", calls `loadKeywordGroups()`
5. On error: shows error detail from response
6. Resets file input so same file can be re-imported

---

## Data Flow

```
User selects .xlsx
    ↓ JS FormData POST
/api/projects/{id}/keyword-groups/import
    ↓ parse_keyword_xlsx()
list[{group, keywords}]
    ↓ merge logic (fetch existing → create/update groups → insert missing keywords)
Supabase: keyword_groups + keywords tables
    ↓ return summary JSON
JS: show status message + loadKeywordGroups()
UI refreshed with new groups/keywords
```

---

## What Is NOT Changing

- Existing manual entry (add group / add keyword) is unchanged
- No changes to DB schema — uses existing `keyword_groups` and `keywords` tables
- No changes to keyword scanning logic
- No preview/confirm step — import is immediate and non-destructive (merge only)

---

## Files Touched

| File | Change |
|------|--------|
| `app/services/keyword_parser.py` | New file — xlsx parsing function |
| `app/main.py` | New endpoint + import of `keyword_parser` |
| `app/templates/project.html` | Import button + JS handler |
