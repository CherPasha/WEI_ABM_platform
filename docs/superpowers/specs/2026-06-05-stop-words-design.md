# Stop Words — Design Spec

**Date:** 2026-06-05
**Status:** Approved

---

## Overview

Add a project-level stop words list. If any stop word appears anywhere in a publication (job posting or news article), that publication is excluded entirely from keyword match counting in the scan. Stop words apply across all keyword groups equally.

---

## Database

Already created in Supabase:

```sql
CREATE TABLE stop_words (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    word TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_stop_words_project ON stop_words(project_id);
```

---

## API Endpoints

New endpoints in `app/main.py`, grouped near the keyword endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/projects/{project_id}/stop-words` | List all stop words: `[{id, word, created_at}]` |
| POST | `/api/projects/{project_id}/stop-words` | Add one stop word. Body: `{word: str}`. Case-insensitive dedup. |
| DELETE | `/api/stop-words/{word_id}` | Delete one stop word by id |
| POST | `/api/projects/{project_id}/stop-words/import` | Bulk import from xlsx |

**Import xlsx format** — one column, one stop word per row (no quotes, no group column):
```
Column A
конкурент
тендер
вакансия
```

Parser reads all non-empty values from column 0. Extension validation: `.xlsx` only (same constraint as keyword import). Merge behavior: skip words already present (case-insensitive).

Import response: `{words_added: int, words_skipped: int}`

---

## Scanner Filtering (`app/services/keyword_scanner.py`)

In `scan_project_keywords()`, after fetching keyword groups/keywords, add:

1. Fetch all stop words for the project from Supabase
2. Compile each word to a case-insensitive regex: `re.compile(re.escape(w), re.IGNORECASE)`
3. Define helper `_publication_has_stop_word(text_fields: list[str], stop_patterns) -> bool` that returns `True` if any pattern matches any field
4. For each posting and news article, before checking it against keywords:
   - Collect its relevant text fields (same fields already used for keyword matching)
   - Call the helper — if `True`, skip this publication entirely
5. If no stop words are configured, skip the filter step entirely (no behavior change)

**Text fields checked:**
- Postings: `title`, `snippet_requirement`, `snippet_responsibility`
- News: `title`, `snippet`, `full_text`

---

## Frontend (`app/templates/project.html`)

New **"Stop Words"** section placed just above the Keyword Analysis section.

**HTML structure:**
```html
<article>
  <h3>Stop Words</h3>
  <p style="font-size:0.85em; color:var(--pico-muted-color); margin-bottom:0.75em;">
    Publications containing any stop word are excluded from keyword counts.
  </p>
  <div style="display:flex; gap:0.5em; margin-bottom:1em; align-items:center; flex-wrap:wrap;">
    <input type="text" id="new-stop-word" placeholder="Stop word..." style="margin:0;">
    <button onclick="addStopWord()" style="margin:0; white-space:nowrap;">Add Stop Word</button>
    <button id="import-sw-btn" class="secondary" style="margin:0; white-space:nowrap;"
            onclick="document.getElementById('sw-import-input').click()">Import from file</button>
    <input type="file" id="sw-import-input" accept=".xlsx" style="display:none"
           onchange="importStopWordFile(this)">
    <span id="sw-import-status" style="font-size:0.85em; color:var(--pico-muted-color);"></span>
  </div>
  <div id="stop-words-container"></div>
</article>
```

**JS functions:**

- `loadStopWords()` — `GET /api/projects/{PROJECT_ID}/stop-words`, renders flat tag cloud in `#stop-words-container`
- `addStopWord()` — reads `#new-stop-word`, `POST /api/projects/.../stop-words`, clears input, calls `loadStopWords()`
- `deleteStopWord(id)` — `DELETE /api/stop-words/{id}`, calls `loadStopWords()`
- `importStopWordFile(input)` — multipart POST to `/api/projects/.../stop-words/import`, shows status, resets input, calls `loadStopWords()`

Tag rendering (inline, same style as keyword tags):
```javascript
// Each stop word renders as:
`<span style="...tag styles...">${escapeHtml(w.word)} <span onclick="deleteStopWord('${w.id}')" ...>×</span></span>`
```

`loadStopWords()` is called on page load alongside `loadKeywordGroups()`.

---

## Files Touched

| File | Change |
|------|--------|
| `supabase_schema.sql` | Add `stop_words` table definition (already created in DB) |
| `app/main.py` | 4 new endpoints + `CreateStopWord` Pydantic model |
| `app/services/keyword_scanner.py` | Fetch stop words, filter publications before keyword matching |
| `app/services/keyword_parser.py` | New `parse_stop_word_xlsx(file_bytes)` function (single-column parser) |
| `app/templates/project.html` | New Stop Words section + 4 JS functions |
| `tests/test_keyword_parser.py` | Tests for `parse_stop_word_xlsx` |

---

## What Is NOT Changing

- Keyword groups, keywords, and their CRUD — untouched
- Scan Excel output format — no new columns; excluded publications simply produce lower counts
- The `keyword_parser.py` `parse_keyword_xlsx` function — unchanged
- No changes to DB schema beyond the already-created `stop_words` table
