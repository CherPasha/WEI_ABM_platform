# Session Keyword Scan — Jupyter Notebook Design Spec

**Date:** 2026-06-08
**Status:** Approved

---

## Overview

A one-time local analysis notebook that connects to Supabase, downloads all companies, job postings, and news articles for a **specific session**, then scans them against a user-provided keyword methodology and stop words list — both loaded from local xlsx files. Output is a local xlsx file in the identical two-sheet format produced by the platform's regular keyword scan.

The notebook is entirely read-only against Supabase and has no effect on the running server.

---

## Folder Structure

```
analysis 08.06/          ← gitignored
  scan_session.ipynb     ← the notebook
  keywords.xlsx          ← user places methodology file here
  stop_words.xlsx        ← user places stop words file here
  results.xlsx           ← written by the notebook
```

`.gitignore` entry: `analysis 08.06/`

---

## Input File Formats

Both formats match the existing platform parsers exactly.

**keywords.xlsx** — one row per keyword:
| Column A (group name) | Column B (keyword) |
|-----------------------|--------------------|
| ABM                   | ABM маркетинг      |
| ABM                   | Account based marketing |
| Конкуренты            | конкурент          |

**stop_words.xlsx** — one column, one word per row:
| Column A  |
|-----------|
| тендер    |
| вакансия  |

---

## Notebook Cells

### Cell 1 — Config
```python
SESSION_ID    = "your-session-uuid-here"
KEYWORDS_FILE = "keywords.xlsx"
STOP_WORDS_FILE = "stop_words.xlsx"
OUTPUT_FILE   = "results.xlsx"
```
Edit these values before running any other cell.

### Cell 2 — Imports
```python
import sys, os, re, subprocess

# Find project root via git — robust regardless of where Jupyter was started
PROJECT_ROOT = subprocess.check_output(
    ["git", "rev-parse", "--show-toplevel"]
).decode().strip()
sys.path.insert(0, PROJECT_ROOT)

# Set CWD to the notebook folder so relative file paths in Cell 1 work
os.chdir(os.path.join(PROJECT_ROOT, "analysis 08.06"))

from app.database import supabase
from app.services.keyword_parser import parse_keyword_xlsx, parse_stop_word_xlsx
from app.services.keyword_scanner import (
    _fetch_all, _fetch_all_in,          # private helpers — OK for one-off use
    _strip_html, _extract_sentences,
    POSTING_TEXT_FIELDS, NEWS_TEXT_FIELDS,
    generate_keyword_xlsx,
)
```

Loads `.env` via `app.database` (same credentials the server uses). No writes.

### Cell 3 — Fetch session data from Supabase
1. `SELECT id, legal_name, inn, known_names FROM companies WHERE session_id = SESSION_ID`
2. Deduplicate companies by INN (fallback: `lower(legal_name)`) — same logic as `scan_project_keywords`
3. Collect all `company_id`s from the deduped set
4. `SELECT company_id, title, snippet_requirement, snippet_responsibility FROM postings WHERE company_id IN (...)`
5. `SELECT company_id, title, snippet, full_text FROM news_articles WHERE company_id IN (...)`
6. Print: company count (raw + after dedup), posting count, news article count

All queries are `SELECT` only.

### Cell 4 — Parse methodology files
1. `parse_keyword_xlsx(open(KEYWORDS_FILE, "rb").read())` → `[{"group": str, "keywords": [str]}]`
2. `parse_stop_word_xlsx(open(STOP_WORDS_FILE, "rb").read())` → `[str]`
3. Compile keyword patterns: `{kw: re.compile(re.escape(kw), re.IGNORECASE)}`
4. Compile stop patterns: `[re.compile(re.escape(w), re.IGNORECASE) for w in stop_words]`
5. Print: group count, total keyword count, stop word count

### Cell 5 — Scan
For each unique company:
1. Collect its postings and news articles
2. If stop patterns exist: filter out any publication where any stop pattern matches any text field
3. For each keyword × each remaining publication: extract matching sentences from relevant text fields
4. Build `{"count": int, "sentences": [...]}` per keyword per company

Text fields checked:
- Postings: `title`, `snippet_requirement`, `snippet_responsibility`
- News: `title`, `snippet`, `full_text`

Sentence extraction and HTML stripping reuse `_extract_sentences` and `_strip_html` from the existing scanner.

Print: number of companies with at least one keyword match.

### Cell 6 — Save output
```python
buf = generate_keyword_xlsx(scan_result)
with open(OUTPUT_FILE, "wb") as f:
    f.write(buf.read())
print(f"Saved → {OUTPUT_FILE}")
```

Output is the same two-sheet xlsx as the platform's regular scan:
- **Summary** — one row per company, one column per keyword, group totals
- **Details** — one row per company × keyword with all matching sentences

---

## Data Flow

```
Cell 1: SESSION_ID + file paths
      ↓
Cell 2: sys.path → .env → supabase client + imported helpers
      ↓
Cell 3: Supabase SELECT → companies (deduped) + postings + news
      ↓
Cell 4: keywords.xlsx + stop_words.xlsx → compiled patterns
      ↓
Cell 5: stop filter → keyword match → scan_result dict
      ↓
Cell 6: generate_keyword_xlsx → results.xlsx
```

---

## How to Run

1. Place `keywords.xlsx` and `stop_words.xlsx` into the `analysis 08.06/` folder
2. Activate the project venv: `source .venv/bin/activate`
3. Start Jupyter from anywhere in the repo: `jupyter notebook` or `jupyter lab`
4. Open `analysis 08.06/scan_session.ipynb`
5. Edit Cell 1 — set `SESSION_ID` to the target session UUID
6. Run all cells top to bottom (Kernel → Restart & Run All)
7. `results.xlsx` appears in `analysis 08.06/`

---

## What Is NOT Changing

- No changes to `app/` source code
- No writes to Supabase
- No new API endpoints
- No changes to the running server

---

## Files Touched

| File | Change |
|------|--------|
| `analysis 08.06/scan_session.ipynb` | New file — the notebook |
| `.gitignore` | Add `analysis 08.06/` entry |
