# Roles xlsx Import — Design Spec

**Date:** 2026-06-09  
**Status:** Approved

## Summary

Add the ability to upload roles from a single-column xlsx file on the project detail page. Merge imported roles with existing ones (case-insensitive dedup). Remove the roles input field from the Create Project form on the home screen.

---

## Backend

### `app/services/keyword_parser.py`

Add `parse_roles_xlsx(file) -> list[str]`:
- Reads column A of the first sheet using `openpyxl`
- Strips whitespace from each cell value
- Filters out empty rows
- Returns a list of role name strings
- Mirrors the existing `parse_stop_word_xlsx()` function in the same file

### `app/main.py`

Add endpoint `POST /api/projects/{project_id}/roles/import`:
- Accepts a multipart file upload (`.xlsx` only)
- Validates file extension; returns 400 if not `.xlsx`
- Calls `parse_roles_xlsx()` to extract role strings
- Fetches project's current `target_roles` from Supabase
- Merges: compares case-insensitively, adds only roles not already present
- Saves the merged list via `supabase.table("projects").update({"target_roles": merged}).eq("id", project_id).execute()`
- Returns `{"added": N, "skipped": N}`

---

## Frontend

### `app/templates/projects.html` — Home screen

Remove the roles input field from the Create Project form. The form will only ask for the project name. The `target_roles` field in the `POST /api/projects` payload will default to `[]`.

### `app/templates/project.html` — Project detail page

In the existing roles section (which already has a text input + "Add" button + role tags):
- Add a file input (`<input type="file" accept=".xlsx">`) with a label "Import from xlsx"
- On `change` event, POST the selected file to `/api/projects/{project_id}/roles/import` using `FormData`
- On success, reload roles display and show inline feedback: e.g. "12 added, 3 skipped"
- On error, show an error message
- Reset the file input after each upload

---

## Data Flow

1. User opens a project detail page and navigates to the roles section
2. User clicks "Import from xlsx" and selects a single-column `.xlsx` file
3. Browser POSTs file to `POST /api/projects/{project_id}/roles/import`
4. Backend parses column A, fetches existing roles, merges (case-insensitive), saves
5. Backend responds with `{"added": N, "skipped": N}`
6. Frontend reloads the roles tags and displays the feedback message

---

## Error Handling

- Non-xlsx file selected: backend returns 400; frontend shows "Only .xlsx files are supported"
- Empty file / no valid rows in column A: endpoint returns `{"added": 0, "skipped": 0}`
- Project not found: endpoint returns 404

---

## Testing

- Unit test for `parse_roles_xlsx()`: single column with values, empty rows, whitespace
- Unit test for merge logic: new roles added, duplicates skipped (case-insensitive)
- Integration test for `POST /api/projects/{project_id}/roles/import`: valid file, invalid extension, empty file
