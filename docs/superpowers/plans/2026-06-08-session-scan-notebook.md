# Session Keyword Scan Notebook — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a Jupyter notebook in `analysis 08.06/` that connects to Supabase, fetches companies/postings/news for one session, scans them against user-supplied keyword and stop-word xlsx files, and writes a two-sheet results xlsx — all read-only against the database.

**Architecture:** Single notebook (`scan_session.ipynb`) with a config cell at the top and five execution cells below. Imports reuse `parse_keyword_xlsx`, `parse_stop_word_xlsx`, `generate_keyword_xlsx`, and the private fetch/scan helpers from the existing `app/` package. The `analysis 08.06/` folder is gitignored so xlsx inputs and outputs are never committed.

**Tech Stack:** Python 3.11, Jupyter, supabase-py, openpyxl, pandas — all already in the venv.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `.gitignore` | Ignore `analysis 08.06/` |
| Create | `analysis 08.06/scan_session.ipynb` | The analysis notebook |

---

## Task 1: Add `analysis 08.06/` to .gitignore

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add the gitignore entry**

Open `.gitignore`. After the last line (`data_trial/`), append:

```
analysis 08.06/
```

The file's tail should look like:
```
# Большие датасеты и файлы с данными
*.xlsx
datasets/
kontur_data/
hunter_emails/
data_trial/
analysis 08.06/
```

- [ ] **Step 2: Verify git treats the folder as ignored**

```bash
mkdir -p "analysis 08.06"
touch "analysis 08.06/test.txt"
git status
```

Expected: `analysis 08.06/` does NOT appear under "Untracked files". Then:

```bash
rm "analysis 08.06/test.txt"
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore analysis 08.06/ folder"
```

---

## Task 2: Create the notebook

**Files:**
- Create: `analysis 08.06/scan_session.ipynb`

- [ ] **Step 1: Write the notebook file**

Create `analysis 08.06/scan_session.ipynb` with the following content (valid nbformat 4 JSON):

```json
{
 "cells": [
  {
   "cell_type": "markdown",
   "id": "md-intro",
   "metadata": {},
   "source": [
    "# Session Keyword Scan\n",
    "\n",
    "Local analysis notebook — read-only against Supabase.\n",
    "Edit **Cell 1** (Config) then run **Kernel → Restart & Run All**."
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "id": "cell-config",
   "metadata": {},
   "outputs": [],
   "source": [
    "# ── CONFIG — edit these before running ───────────────────────────────────────\n",
    "SESSION_ID      = \"your-session-uuid-here\"\n",
    "KEYWORDS_FILE   = \"keywords.xlsx\"\n",
    "STOP_WORDS_FILE = \"stop_words.xlsx\"\n",
    "OUTPUT_FILE     = \"results.xlsx\"\n",
    "# ─────────────────────────────────────────────────────────────────────────────"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "id": "cell-imports",
   "metadata": {},
   "outputs": [],
   "source": [
    "import sys\n",
    "import os\n",
    "import re\n",
    "import subprocess\n",
    "\n",
    "# Locate project root via git — works regardless of where Jupyter was started\n",
    "PROJECT_ROOT = subprocess.check_output(\n",
    "    [\"git\", \"rev-parse\", \"--show-toplevel\"]\n",
    ").decode().strip()\n",
    "sys.path.insert(0, PROJECT_ROOT)\n",
    "\n",
    "# Set CWD to the notebook folder so relative file paths in Cell 1 work\n",
    "os.chdir(os.path.join(PROJECT_ROOT, \"analysis 08.06\"))\n",
    "\n",
    "from app.database import supabase\n",
    "from app.services.keyword_parser import parse_keyword_xlsx, parse_stop_word_xlsx\n",
    "from app.services.keyword_scanner import (\n",
    "    _fetch_all,        # fetch rows WHERE column = value (paginates)\n",
    "    _fetch_all_in,     # fetch rows WHERE column IN (values) (paginates)\n",
    "    _strip_html,\n",
    "    _extract_sentences,\n",
    "    POSTING_TEXT_FIELDS,\n",
    "    NEWS_TEXT_FIELDS,\n",
    "    generate_keyword_xlsx,\n",
    ")\n",
    "\n",
    "print(\"Imports OK\")"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "id": "cell-fetch",
   "metadata": {},
   "outputs": [],
   "source": [
    "# ── Fetch companies for the session ──────────────────────────────────────────\n",
    "raw_companies = _fetch_all(\n",
    "    \"companies\", \"session_id\", SESSION_ID,\n",
    "    select=\"id, legal_name, inn, known_names\"\n",
    ")\n",
    "print(f\"Raw companies fetched: {len(raw_companies)}\")\n",
    "\n",
    "# Deduplicate by INN (fallback: lower(legal_name))\n",
    "dedup: dict[str, dict] = {}\n",
    "for c in raw_companies:\n",
    "    inn = (c.get(\"inn\") or \"\").strip()\n",
    "    key = f\"inn:{inn}\" if inn else f\"name:{c.get('legal_name', '').strip().lower()}\"\n",
    "    if key not in dedup:\n",
    "        dedup[key] = {\"name\": c[\"legal_name\"], \"inn\": inn, \"company_ids\": []}\n",
    "    dedup[key][\"company_ids\"].append(c[\"id\"])\n",
    "\n",
    "unique_companies = list(dedup.values())\n",
    "print(f\"Unique companies after INN dedup: {len(unique_companies)}\")\n",
    "\n",
    "# ── Fetch postings and news ───────────────────────────────────────────────────\n",
    "all_company_ids = [cid for uc in unique_companies for cid in uc[\"company_ids\"]]\n",
    "\n",
    "all_postings = _fetch_all_in(\n",
    "    \"postings\", \"company_id\", all_company_ids,\n",
    "    select=\"company_id, title, snippet_requirement, snippet_responsibility\"\n",
    ")\n",
    "all_news = _fetch_all_in(\n",
    "    \"news_articles\", \"company_id\", all_company_ids,\n",
    "    select=\"company_id, title, snippet, full_text\"\n",
    ")\n",
    "\n",
    "print(f\"Postings fetched:      {len(all_postings)}\")\n",
    "print(f\"News articles fetched: {len(all_news)}\")"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "id": "cell-parse",
   "metadata": {},
   "outputs": [],
   "source": [
    "# ── Keywords ──────────────────────────────────────────────────────────────────\n",
    "with open(KEYWORDS_FILE, \"rb\") as f:\n",
    "    keyword_groups = parse_keyword_xlsx(f.read())  # [{\"group\": str, \"keywords\": [str]}]\n",
    "\n",
    "groups = [{\"name\": g[\"group\"], \"keywords\": g[\"keywords\"]} for g in keyword_groups]\n",
    "all_keywords = [(kw, g[\"group\"]) for g in keyword_groups for kw in g[\"keywords\"]]\n",
    "keyword_patterns = {kw: re.compile(re.escape(kw), re.IGNORECASE) for kw, _ in all_keywords}\n",
    "\n",
    "print(f\"Keyword groups: {len(groups)}\")\n",
    "print(f\"Total keywords: {len(all_keywords)}\")\n",
    "for g in groups:\n",
    "    print(f\"  {g['name']}: {len(g['keywords'])} keywords\")\n",
    "\n",
    "# ── Stop words ────────────────────────────────────────────────────────────────\n",
    "with open(STOP_WORDS_FILE, \"rb\") as f:\n",
    "    stop_words = parse_stop_word_xlsx(f.read())\n",
    "\n",
    "stop_patterns = [re.compile(re.escape(w), re.IGNORECASE) for w in stop_words]\n",
    "print(f\"\\nStop words loaded: {len(stop_words)}\")"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "id": "cell-scan",
   "metadata": {},
   "outputs": [],
   "source": [
    "def _is_stopped(pub: dict, fields: list) -> bool:\n",
    "    \"\"\"Return True if any stop pattern matches any text field of this publication.\"\"\"\n",
    "    for field in fields:\n",
    "        text = _strip_html(pub.get(field) or \"\")\n",
    "        if not text:\n",
    "            continue\n",
    "        if any(p.search(text) for p in stop_patterns):\n",
    "            return True\n",
    "    return False\n",
    "\n",
    "# Index publications by company_id\n",
    "postings_by_company: dict[str, list] = {}\n",
    "for p in all_postings:\n",
    "    postings_by_company.setdefault(p[\"company_id\"], []).append(p)\n",
    "\n",
    "news_by_company: dict[str, list] = {}\n",
    "for a in all_news:\n",
    "    news_by_company.setdefault(a[\"company_id\"], []).append(a)\n",
    "\n",
    "# Scan each company\n",
    "result_companies = []\n",
    "for uc in unique_companies:\n",
    "    company_postings = []\n",
    "    company_news = []\n",
    "    for cid in uc[\"company_ids\"]:\n",
    "        company_postings.extend(postings_by_company.get(cid, []))\n",
    "        company_news.extend(news_by_company.get(cid, []))\n",
    "\n",
    "    if stop_patterns:\n",
    "        company_postings = [p for p in company_postings if not _is_stopped(p, POSTING_TEXT_FIELDS)]\n",
    "        company_news = [a for a in company_news if not _is_stopped(a, NEWS_TEXT_FIELDS)]\n",
    "\n",
    "    keyword_results: dict[str, dict] = {}\n",
    "    for kw, _ in all_keywords:\n",
    "        pattern = keyword_patterns[kw]\n",
    "        matches = []\n",
    "\n",
    "        for posting in company_postings:\n",
    "            for field in POSTING_TEXT_FIELDS:\n",
    "                raw_text = _strip_html(posting.get(field) or \"\")\n",
    "                for sentence in _extract_sentences(raw_text, pattern):\n",
    "                    matches.append({\n",
    "                        \"source\": \"posting\",\n",
    "                        \"field\": field,\n",
    "                        \"title\": posting.get(\"title\") or \"\",\n",
    "                        \"sentence\": sentence,\n",
    "                    })\n",
    "\n",
    "        for article in company_news:\n",
    "            for field in NEWS_TEXT_FIELDS:\n",
    "                raw_text = _strip_html(article.get(field) or \"\")\n",
    "                for sentence in _extract_sentences(raw_text, pattern):\n",
    "                    matches.append({\n",
    "                        \"source\": \"news\",\n",
    "                        \"field\": field,\n",
    "                        \"title\": article.get(\"title\") or \"\",\n",
    "                        \"sentence\": sentence,\n",
    "                    })\n",
    "\n",
    "        keyword_results[kw] = {\"count\": len(matches), \"sentences\": matches}\n",
    "\n",
    "    result_companies.append({\n",
    "        \"name\": uc[\"name\"],\n",
    "        \"inn\": uc[\"inn\"],\n",
    "        \"results\": keyword_results,\n",
    "    })\n",
    "\n",
    "scan_result = {\"groups\": groups, \"companies\": result_companies}\n",
    "\n",
    "matched = sum(\n",
    "    1 for c in result_companies\n",
    "    if any(v[\"count\"] > 0 for v in c[\"results\"].values())\n",
    ")\n",
    "print(f\"Scan complete: {matched}/{len(result_companies)} companies had at least one keyword match\")"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "id": "cell-save",
   "metadata": {},
   "outputs": [],
   "source": [
    "buf = generate_keyword_xlsx(scan_result)\n",
    "with open(OUTPUT_FILE, \"wb\") as f:\n",
    "    f.write(buf.read())\n",
    "print(f\"Saved \\u2192 {os.path.abspath(OUTPUT_FILE)}\")"
   ]
  }
 ],
 "metadata": {
  "kernelspec": {
   "display_name": "Python 3",
   "language": "python",
   "name": "python3"
  },
  "language_info": {
   "name": "python",
   "version": "3.11.0"
  }
 },
 "nbformat": 4,
 "nbformat_minor": 5
}
```

- [ ] **Step 2: Verify the notebook file is valid JSON**

```bash
python3 -c "import json; json.load(open('analysis 08.06/scan_session.ipynb')); print('Valid JSON')"
```

Expected output: `Valid JSON`

- [ ] **Step 3: Verify git ignores the folder**

```bash
git status
```

Expected: `analysis 08.06/` does NOT appear in the output (it's gitignored).

- [ ] **Step 4: Commit the notebook**

```bash
git add docs/superpowers/plans/2026-06-08-session-scan-notebook.md
git commit -m "feat: add session keyword scan Jupyter notebook"
```

Note: `analysis 08.06/scan_session.ipynb` is intentionally not staged — it's gitignored.

---

## Task 3: Smoke test (manual)

Run this after placing your `keywords.xlsx` and `stop_words.xlsx` in `analysis 08.06/`.

- [ ] **Step 1: Place input files**

Copy your methodology xlsx files into `analysis 08.06/`:
```
analysis 08.06/keywords.xlsx
analysis 08.06/stop_words.xlsx
```

- [ ] **Step 2: Open Jupyter**

```bash
cd "/Users/pashache/Desktop/projects/WEI group/ABM/WEI_ABM_platform"
source .venv/bin/activate
jupyter notebook
```

Open `analysis 08.06/scan_session.ipynb` in the browser.

- [ ] **Step 3: Set SESSION_ID in Cell 1**

Edit Cell 1: replace `"your-session-uuid-here"` with the actual session UUID from Supabase.

- [ ] **Step 4: Run all cells**

Kernel → Restart & Run All.

Expected cell outputs:
- Cell 2: `Imports OK`
- Cell 3: company/posting/news counts (non-zero if session has data)
- Cell 4: keyword group and stop word counts
- Cell 5: `Scan complete: N/M companies had at least one keyword match`
- Cell 6: `Saved → /…/analysis 08.06/results.xlsx`

- [ ] **Step 5: Verify output file**

Open `analysis 08.06/results.xlsx`. Confirm:
- Sheet "Summary" — one row per company, keyword columns, group total columns
- Sheet "Details" — rows for companies with matches, with sentence excerpts
