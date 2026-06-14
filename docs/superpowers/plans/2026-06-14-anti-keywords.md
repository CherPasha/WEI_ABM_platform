# Anti-Keywords Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add anti-keyword groups (scanned identically to keywords) with a dedicated UI tab and separate XLSX sheets in scan output.

**Architecture:** Single `is_anti BOOLEAN` column on `keyword_groups` distinguishes types. Scanner fetches both in one pass and returns `anti_groups`/`anti_results` alongside existing keys. XLSX gains two new sheets (Anti_Summary, Anti_Details) and mirrored columns on Quick_Summary. Three new API endpoints + `is_anti=False` filter on existing endpoints. New UI tab with parallel JS.

**Tech Stack:** FastAPI, Supabase (postgrest-py), pandas/openpyxl, Jinja2/vanilla JS, PicoCSS

---

### Task 1: DB migration files

**Files:**
- Modify: `supabase_schema.sql`
- Create: `supabase_migration_anti_keywords.sql`

- [ ] **Step 1: Add `is_anti` to `keyword_groups` in schema**

In `supabase_schema.sql`, replace:
```sql
CREATE TABLE keyword_groups (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID        REFERENCES projects(id) ON DELETE CASCADE,
    name       TEXT        NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
```
With:
```sql
CREATE TABLE keyword_groups (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID        REFERENCES projects(id) ON DELETE CASCADE,
    name       TEXT        NOT NULL,
    is_anti    BOOLEAN     NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

- [ ] **Step 2: Create migration file**

Create `supabase_migration_anti_keywords.sql`:
```sql
-- Anti-keywords feature: add is_anti flag to keyword_groups
-- Existing rows become is_anti = false (the column default).
ALTER TABLE keyword_groups ADD COLUMN is_anti BOOLEAN NOT NULL DEFAULT false;
```

- [ ] **Step 3: Run migration in Supabase SQL editor**

Paste contents of `supabase_migration_anti_keywords.sql` into the Supabase SQL Editor and execute. Verify the column appears on the `keyword_groups` table.

- [ ] **Step 4: Commit**

```bash
git add supabase_schema.sql supabase_migration_anti_keywords.sql
git commit -m "feat: add is_anti column to keyword_groups (migration)"
```

---

### Task 2: Fix broken existing tests in test_keyword_scan_format.py

Six tests reference the old column name `"Total Keywords Found"` which was renamed to `"Unique Keywords Found"` (and `"Total Keyword Matches"` was added). They currently fail. Fix them before adding new tests.

**Files:**
- Modify: `tests/test_keyword_scan_format.py`

- [ ] **Step 1: Run the broken tests to confirm they fail**

```bash
pytest tests/test_keyword_scan_format.py -v
```

Expected: several FAILED with `KeyError: 'Total Keywords Found'` or assertion mismatch on column list.

- [ ] **Step 2: Fix `test_quick_summary_columns`**

Replace:
```python
def test_quick_summary_columns():
    buf = generate_keyword_xlsx(_scan_result())
    df = pd.read_excel(buf, sheet_name="Quick_Summary")
    assert list(df.columns[:5]) == [
        "Company", "INN", "Total Keywords Found", "Groups With Hits", "Keywords Found"
    ]
    assert "Growth" in df.columns
    assert "Tech" in df.columns
```
With:
```python
def test_quick_summary_columns():
    buf = generate_keyword_xlsx(_scan_result())
    df = pd.read_excel(buf, sheet_name="Quick_Summary")
    assert list(df.columns[:6]) == [
        "Company", "INN", "Unique Keywords Found", "Total Keyword Matches", "Groups With Hits", "Keywords Found",
    ]
    assert "Growth" in df.columns
    assert "Tech" in df.columns
```

- [ ] **Step 3: Fix `test_quick_summary_acme_values`**

Replace `row["Total Keywords Found"]` with `row["Unique Keywords Found"]`:
```python
def test_quick_summary_acme_values():
    buf = generate_keyword_xlsx(_scan_result())
    df = pd.read_excel(buf, sheet_name="Quick_Summary")
    row = df[df["Company"] == "Acme Corp"].iloc[0]
    assert row["Unique Keywords Found"] == 3
    assert row["Groups With Hits"] == 2
    assert row["Growth"] == 1
    assert row["Tech"] == 2
    kws = row["Keywords Found"]
    assert "expand" in kws
    assert "AI" in kws
    assert "ML" in kws
    assert "scale" not in kws
```

- [ ] **Step 4: Fix `test_quick_summary_zero_company`**

```python
def test_quick_summary_zero_company():
    buf = generate_keyword_xlsx(_scan_result())
    df = pd.read_excel(buf, sheet_name="Quick_Summary")
    row = df[df["Company"] == "Zero Inc"].iloc[0]
    assert row["Unique Keywords Found"] == 0
    assert row["Groups With Hits"] == 0
    assert row["Growth"] == 0
    assert row["Tech"] == 0
    assert str(row["Keywords Found"]) in ("", "nan")
```

- [ ] **Step 5: Fix `test_derive_qs_columns`**

```python
def test_derive_qs_columns():
    df = derive_quick_summary_df(_summary_df())
    assert list(df.columns[:6]) == [
        "Company", "INN", "Unique Keywords Found", "Total Keyword Matches", "Groups With Hits", "Keywords Found",
    ]
    assert "Growth" in df.columns
    assert "Tech" in df.columns
```

- [ ] **Step 6: Fix `test_derive_qs_acme_values`**

```python
def test_derive_qs_acme_values():
    df = derive_quick_summary_df(_summary_df())
    row = df[df["Company"] == "Acme Corp"].iloc[0]
    assert row["Unique Keywords Found"] == 3
    assert row["Total Keyword Matches"] == 6   # expand=2, AI=1, ML=3
    assert row["Groups With Hits"] == 2
    assert row["Growth"] == 1
    assert row["Tech"] == 2
    kws = row["Keywords Found"]
    assert "expand" in kws
    assert "AI" in kws
    assert "ML" in kws
    assert "scale" not in kws
```

- [ ] **Step 7: Fix `test_derive_qs_zero_values`**

```python
def test_derive_qs_zero_values():
    df = derive_quick_summary_df(_summary_df())
    row = df[df["Company"] == "Zero Inc"].iloc[0]
    assert row["Unique Keywords Found"] == 0
    assert row["Total Keyword Matches"] == 0
    assert row["Groups With Hits"] == 0
    assert row["Growth"] == 0
    assert row["Tech"] == 0
    assert str(row["Keywords Found"]) in ("", "nan")
```

- [ ] **Step 8: Run tests — expect all to pass**

```bash
pytest tests/test_keyword_scan_format.py -v
```

Expected: all PASSED.

- [ ] **Step 9: Commit**

```bash
git add tests/test_keyword_scan_format.py
git commit -m "fix: update keyword scan format tests to current column names"
```

---

### Task 3: Write failing tests for anti-keyword XLSX output

**Files:**
- Modify: `tests/test_keyword_scan_format.py`

- [ ] **Step 1: Add `_scan_result_with_anti()` fixture and failing tests**

Append to `tests/test_keyword_scan_format.py`:

```python
# ── Anti-keyword fixtures and tests ──

def _scan_result_with_anti():
    return {
        "groups": [
            {"name": "Growth", "keywords": ["expand", "scale"]},
        ],
        "anti_groups": [
            {"name": "Risk", "keywords": ["bankrupt", "lawsuit"]},
        ],
        "companies": [
            {
                "name": "Acme Corp",
                "inn": "7712345678",
                "results": {
                    "expand": {"count": 2, "sentences": []},
                    "scale":  {"count": 0, "sentences": []},
                },
                "anti_results": {
                    "bankrupt": {"count": 1, "sentences": [
                        {"source": "news", "field": "title", "title": "Bad news", "sentence": "Company went bankrupt."}
                    ]},
                    "lawsuit":  {"count": 0, "sentences": []},
                },
            },
            {
                "name": "Zero Inc",
                "inn": "0000000000",
                "results": {
                    "expand": {"count": 0, "sentences": []},
                    "scale":  {"count": 0, "sentences": []},
                },
                "anti_results": {
                    "bankrupt": {"count": 0, "sentences": []},
                    "lawsuit":  {"count": 0, "sentences": []},
                },
            },
        ],
    }


def test_sheet_order_with_anti():
    buf = generate_keyword_xlsx(_scan_result_with_anti())
    assert pd.ExcelFile(buf).sheet_names == [
        "Quick_Summary", "Summary", "Details", "Anti_Summary", "Anti_Details"
    ]


def test_quick_summary_anti_columns_present():
    buf = generate_keyword_xlsx(_scan_result_with_anti())
    df = pd.read_excel(buf, sheet_name="Quick_Summary")
    for col in ["Anti Unique Keywords Found", "Anti Total Keyword Matches",
                "Anti Groups With Hits", "Anti Keywords Found", "Anti: Risk"]:
        assert col in df.columns, f"Expected column {col!r} in Quick_Summary"


def test_quick_summary_anti_column_order():
    """Anti stat columns must come immediately after regular stat columns."""
    buf = generate_keyword_xlsx(_scan_result_with_anti())
    df = pd.read_excel(buf, sheet_name="Quick_Summary")
    cols = list(df.columns)
    kw_found_idx = cols.index("Keywords Found")
    anti_unique_idx = cols.index("Anti Unique Keywords Found")
    assert anti_unique_idx == kw_found_idx + 1


def test_quick_summary_anti_values_acme():
    buf = generate_keyword_xlsx(_scan_result_with_anti())
    df = pd.read_excel(buf, sheet_name="Quick_Summary")
    row = df[df["Company"] == "Acme Corp"].iloc[0]
    assert row["Anti Unique Keywords Found"] == 1   # bankrupt only
    assert row["Anti Total Keyword Matches"] == 1
    assert row["Anti Groups With Hits"] == 1
    assert "bankrupt" in str(row["Anti Keywords Found"])
    assert row["Anti: Risk"] == 1


def test_quick_summary_anti_values_zero():
    buf = generate_keyword_xlsx(_scan_result_with_anti())
    df = pd.read_excel(buf, sheet_name="Quick_Summary")
    row = df[df["Company"] == "Zero Inc"].iloc[0]
    assert row["Anti Unique Keywords Found"] == 0
    assert row["Anti Total Keyword Matches"] == 0
    assert row["Anti Groups With Hits"] == 0
    assert row["Anti: Risk"] == 0


def test_anti_summary_sheet_structure():
    buf = generate_keyword_xlsx(_scan_result_with_anti())
    df = pd.read_excel(buf, sheet_name="Anti_Summary")
    assert "Company" in df.columns
    assert "INN" in df.columns
    assert "bankrupt" in df.columns
    assert "lawsuit" in df.columns
    assert "Risk (total)" in df.columns


def test_anti_summary_values():
    buf = generate_keyword_xlsx(_scan_result_with_anti())
    df = pd.read_excel(buf, sheet_name="Anti_Summary")
    row = df[df["Company"] == "Acme Corp"].iloc[0]
    assert row["bankrupt"] == 1
    assert row["lawsuit"] == 0
    assert row["Risk (total)"] == 1


def test_anti_details_sheet():
    buf = generate_keyword_xlsx(_scan_result_with_anti())
    df = pd.read_excel(buf, sheet_name="Anti_Details")
    assert list(df.columns) == [
        "Company", "INN", "Keyword Group", "Keyword",
        "Total Matches", "From Postings", "From News", "Sentences"
    ]
    assert len(df) == 1
    row = df.iloc[0]
    assert row["Company"] == "Acme Corp"
    assert row["Keyword Group"] == "Risk"
    assert row["Keyword"] == "bankrupt"
    assert row["Total Matches"] == 1
    assert row["From News"] == 1
    assert row["From Postings"] == 0


def test_no_anti_groups_still_five_sheets():
    """scan_result with no anti_groups key → 5 sheets, Anti_Summary/Details are empty."""
    buf = generate_keyword_xlsx(_scan_result())   # fixture has no anti_groups
    names = pd.ExcelFile(buf).sheet_names
    assert names == ["Quick_Summary", "Summary", "Details", "Anti_Summary", "Anti_Details"]


def test_no_anti_groups_quick_summary_has_anti_cols():
    buf = generate_keyword_xlsx(_scan_result())
    df = pd.read_excel(buf, sheet_name="Quick_Summary")
    assert "Anti Unique Keywords Found" in df.columns
    row = df[df["Company"] == "Acme Corp"].iloc[0]
    assert row["Anti Unique Keywords Found"] == 0
```

- [ ] **Step 2: Run the new tests — expect all to fail**

```bash
pytest tests/test_keyword_scan_format.py::test_sheet_order_with_anti \
       tests/test_keyword_scan_format.py::test_quick_summary_anti_columns_present \
       tests/test_keyword_scan_format.py::test_anti_summary_sheet_structure \
       tests/test_keyword_scan_format.py::test_no_anti_groups_still_five_sheets \
       -v
```

Expected: all FAILED (sheet names wrong, columns missing).

---

### Task 4: Update `generate_keyword_xlsx` for 5-sheet output

**Files:**
- Modify: `app/services/keyword_scanner.py`

- [ ] **Step 1: Replace `generate_keyword_xlsx` with the new version**

In `app/services/keyword_scanner.py`, replace the entire `generate_keyword_xlsx` function (currently lines ~371–469) with:

```python
def generate_keyword_xlsx(scan_result: dict) -> io.BytesIO:
    """Generate a five-sheet XLSX from scan results."""
    groups = scan_result["groups"]
    anti_groups = scan_result.get("anti_groups", [])
    companies = scan_result["companies"]

    # ── Sheet 1: Quick_Summary ──
    qs_rows = []
    for company in companies:
        total_kw = 0
        total_groups = 0
        found_kw_names: list[str] = []
        row: dict = {"Company": company["name"], "INN": company["inn"]}
        for group in groups:
            group_kw_count = 0
            for kw in group["keywords"]:
                count = company["results"].get(kw, {}).get("count", 0)
                if count > 0:
                    group_kw_count += 1
                    total_kw += 1
                    found_kw_names.append(kw)
            row[group["name"]] = group_kw_count
            if group_kw_count > 0:
                total_groups += 1
        total_matches = sum(
            company["results"].get(kw, {}).get("count", 0)
            for g in groups for kw in g["keywords"]
        )
        row["Unique Keywords Found"] = total_kw
        row["Total Keyword Matches"] = total_matches
        row["Groups With Hits"] = total_groups
        row["Keywords Found"] = ", ".join(found_kw_names)

        anti_total_kw = 0
        anti_total_groups = 0
        anti_found_kw_names: list[str] = []
        for anti_group in anti_groups:
            anti_group_kw_count = 0
            for kw in anti_group["keywords"]:
                count = company.get("anti_results", {}).get(kw, {}).get("count", 0)
                if count > 0:
                    anti_group_kw_count += 1
                    anti_total_kw += 1
                    anti_found_kw_names.append(kw)
            row[f"Anti: {anti_group['name']}"] = anti_group_kw_count
            if anti_group_kw_count > 0:
                anti_total_groups += 1
        anti_total_matches = sum(
            company.get("anti_results", {}).get(kw, {}).get("count", 0)
            for g in anti_groups for kw in g["keywords"]
        )
        row["Anti Unique Keywords Found"] = anti_total_kw
        row["Anti Total Keyword Matches"] = anti_total_matches
        row["Anti Groups With Hits"] = anti_total_groups
        row["Anti Keywords Found"] = ", ".join(anti_found_kw_names)
        qs_rows.append(row)

    qs_df = pd.DataFrame(qs_rows)
    qs_cols = (
        ["Company", "INN",
         "Unique Keywords Found", "Total Keyword Matches", "Groups With Hits", "Keywords Found",
         "Anti Unique Keywords Found", "Anti Total Keyword Matches", "Anti Groups With Hits", "Anti Keywords Found"]
        + [g["name"] for g in groups]
        + [f"Anti: {g['name']}" for g in anti_groups]
    )
    qs_cols = [c for c in qs_cols if c in qs_df.columns]
    qs_df = qs_df[qs_cols]

    # ── Sheet 2: Summary (unchanged) ──
    summary_rows = []
    for company in companies:
        row = {"Company": company["name"], "INN": company["inn"]}
        for group in groups:
            group_found = 0
            for kw in group["keywords"]:
                count = company["results"].get(kw, {}).get("count", 0)
                row[kw] = count
                if count > 0:
                    group_found += 1
            row[f"{group['name']} (total)"] = group_found
        summary_rows.append(row)

    summary_df = pd.DataFrame(summary_rows)
    ordered_cols = ["Company", "INN"]
    for group in groups:
        for kw in group["keywords"]:
            ordered_cols.append(kw)
        ordered_cols.append(f"{group['name']} (total)")
    ordered_cols = [c for c in ordered_cols if c in summary_df.columns]
    summary_df = summary_df[ordered_cols]

    # ── Sheet 3: Details (unchanged) ──
    detail_rows = []
    for company in companies:
        for group in groups:
            for kw in group["keywords"]:
                matches = company["results"].get(kw, {}).get("sentences", [])
                if not matches:
                    continue
                combined = "\n\n".join(
                    f"[{'Job Posting' if m['source'] == 'posting' else 'News'} / {m['title']} / {m['field']}] {m['sentence']}"
                    for m in matches
                )
                posting_count = sum(1 for m in matches if m["source"] == "posting")
                news_count = sum(1 for m in matches if m["source"] == "news")
                detail_rows.append({
                    "Company": company["name"],
                    "INN": company["inn"],
                    "Keyword Group": group["name"],
                    "Keyword": kw,
                    "Total Matches": len(matches),
                    "From Postings": posting_count,
                    "From News": news_count,
                    "Sentences": combined,
                })

    details_df = pd.DataFrame(detail_rows) if detail_rows else pd.DataFrame(
        columns=["Company", "INN", "Keyword Group", "Keyword", "Total Matches", "From Postings", "From News", "Sentences"]
    )

    # ── Sheet 4: Anti_Summary ──
    anti_summary_rows = []
    for company in companies:
        row = {"Company": company["name"], "INN": company["inn"]}
        for anti_group in anti_groups:
            group_found = 0
            for kw in anti_group["keywords"]:
                count = company.get("anti_results", {}).get(kw, {}).get("count", 0)
                row[kw] = count
                if count > 0:
                    group_found += 1
            row[f"{anti_group['name']} (total)"] = group_found
        anti_summary_rows.append(row)

    if anti_groups:
        anti_summary_df = pd.DataFrame(anti_summary_rows)
        anti_ordered_cols = ["Company", "INN"]
        for anti_group in anti_groups:
            for kw in anti_group["keywords"]:
                anti_ordered_cols.append(kw)
            anti_ordered_cols.append(f"{anti_group['name']} (total)")
        anti_ordered_cols = [c for c in anti_ordered_cols if c in anti_summary_df.columns]
        anti_summary_df = anti_summary_df[anti_ordered_cols]
    else:
        anti_summary_df = pd.DataFrame(columns=["Company", "INN"])

    # ── Sheet 5: Anti_Details ──
    anti_detail_rows = []
    for company in companies:
        for anti_group in anti_groups:
            for kw in anti_group["keywords"]:
                matches = company.get("anti_results", {}).get(kw, {}).get("sentences", [])
                if not matches:
                    continue
                combined = "\n\n".join(
                    f"[{'Job Posting' if m['source'] == 'posting' else 'News'} / {m['title']} / {m['field']}] {m['sentence']}"
                    for m in matches
                )
                posting_count = sum(1 for m in matches if m["source"] == "posting")
                news_count = sum(1 for m in matches if m["source"] == "news")
                anti_detail_rows.append({
                    "Company": company["name"],
                    "INN": company["inn"],
                    "Keyword Group": anti_group["name"],
                    "Keyword": kw,
                    "Total Matches": len(matches),
                    "From Postings": posting_count,
                    "From News": news_count,
                    "Sentences": combined,
                })

    anti_details_df = pd.DataFrame(anti_detail_rows) if anti_detail_rows else pd.DataFrame(
        columns=["Company", "INN", "Keyword Group", "Keyword", "Total Matches", "From Postings", "From News", "Sentences"]
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        qs_df.to_excel(writer, sheet_name="Quick_Summary", index=False)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        details_df.to_excel(writer, sheet_name="Details", index=False)
        anti_summary_df.to_excel(writer, sheet_name="Anti_Summary", index=False)
        anti_details_df.to_excel(writer, sheet_name="Anti_Details", index=False)
    buffer.seek(0)
    return buffer
```

- [ ] **Step 2: Also update `test_sheet_order` (the old 3-sheet test) to expect 5**

In `tests/test_keyword_scan_format.py`, replace:
```python
def test_sheet_order():
    buf = generate_keyword_xlsx(_scan_result())
    assert pd.ExcelFile(buf).sheet_names == ["Quick_Summary", "Summary", "Details"]
```
With:
```python
def test_sheet_order():
    buf = generate_keyword_xlsx(_scan_result())
    assert pd.ExcelFile(buf).sheet_names == [
        "Quick_Summary", "Summary", "Details", "Anti_Summary", "Anti_Details"
    ]
```

- [ ] **Step 3: Run all keyword scan format tests**

```bash
pytest tests/test_keyword_scan_format.py -v
```

Expected: all PASSED.

- [ ] **Step 4: Commit**

```bash
git add app/services/keyword_scanner.py tests/test_keyword_scan_format.py
git commit -m "feat: update generate_keyword_xlsx to 5-sheet output with anti-keyword support"
```

---

### Task 5: Write failing tests for `derive_quick_summary_df` anti-keyword support

**Files:**
- Modify: `tests/test_keyword_scan_format.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_keyword_scan_format.py`:

```python
# ── derive_quick_summary_df with anti-keyword support ──

def _anti_summary_df():
    return pd.DataFrame([
        {
            "Company": "Acme Corp",
            "INN": "7712345678",
            "bankrupt": 1,
            "lawsuit": 0,
            "Risk (total)": 1,
        },
        {
            "Company": "Zero Inc",
            "INN": "0000000000",
            "bankrupt": 0,
            "lawsuit": 0,
            "Risk (total)": 0,
        },
    ])


def test_derive_qs_with_anti_columns():
    df = derive_quick_summary_df(_summary_df(), _anti_summary_df())
    for col in ["Anti Unique Keywords Found", "Anti Total Keyword Matches",
                "Anti Groups With Hits", "Anti Keywords Found", "Anti: Risk"]:
        assert col in df.columns, f"Missing column {col!r}"


def test_derive_qs_with_anti_order():
    """Anti stat cols must immediately follow Keywords Found."""
    df = derive_quick_summary_df(_summary_df(), _anti_summary_df())
    cols = list(df.columns)
    kw_found_idx = cols.index("Keywords Found")
    anti_unique_idx = cols.index("Anti Unique Keywords Found")
    assert anti_unique_idx == kw_found_idx + 1


def test_derive_qs_with_anti_acme_values():
    df = derive_quick_summary_df(_summary_df(), _anti_summary_df())
    row = df[df["Company"] == "Acme Corp"].iloc[0]
    assert row["Anti Unique Keywords Found"] == 1
    assert row["Anti Total Keyword Matches"] == 1
    assert row["Anti Groups With Hits"] == 1
    assert "bankrupt" in str(row["Anti Keywords Found"])
    assert row["Anti: Risk"] == 1


def test_derive_qs_with_anti_zero_values():
    df = derive_quick_summary_df(_summary_df(), _anti_summary_df())
    row = df[df["Company"] == "Zero Inc"].iloc[0]
    assert row["Anti Unique Keywords Found"] == 0
    assert row["Anti Total Keyword Matches"] == 0
    assert row["Anti Groups With Hits"] == 0
    assert row["Anti: Risk"] == 0


def test_derive_qs_without_anti_unchanged():
    """Passing no anti_summary_df must return same columns as before."""
    df = derive_quick_summary_df(_summary_df())
    assert "Anti Unique Keywords Found" not in df.columns
    assert list(df.columns[:6]) == [
        "Company", "INN", "Unique Keywords Found", "Total Keyword Matches",
        "Groups With Hits", "Keywords Found",
    ]
```

- [ ] **Step 2: Run new tests — expect FAIL**

```bash
pytest tests/test_keyword_scan_format.py::test_derive_qs_with_anti_columns \
       tests/test_keyword_scan_format.py::test_derive_qs_with_anti_acme_values \
       -v
```

Expected: FAILED (function signature doesn't accept `anti_summary_df`).

---

### Task 6: Update `derive_quick_summary_df` for anti-keyword support

**Files:**
- Modify: `app/services/keyword_scanner.py`

- [ ] **Step 1: Replace `derive_quick_summary_df`**

In `app/services/keyword_scanner.py`, replace the entire `derive_quick_summary_df` function (currently the last function, ~lines 472–507) with:

```python
def derive_quick_summary_df(
    summary_df: pd.DataFrame,
    anti_summary_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Reconstruct Quick_Summary from Summary (and optionally Anti_Summary) sheets.

    Used by the download-with-contacts endpoint to rebuild Quick_Summary from the
    stored XLSX without re-running the scan.
    """
    total_cols = [c for c in summary_df.columns if c.endswith(" (total)")]
    kw_cols = [
        c for c in summary_df.columns
        if c not in ("Company", "INN") and not c.endswith(" (total)")
    ]

    anti_total_cols: list[str] = []
    anti_kw_cols: list[str] = []
    anti_lookup: dict[tuple, Any] = {}
    if anti_summary_df is not None:
        anti_total_cols = [c for c in anti_summary_df.columns if c.endswith(" (total)")]
        anti_kw_cols = [
            c for c in anti_summary_df.columns
            if c not in ("Company", "INN") and not c.endswith(" (total)")
        ]
        for _, r in anti_summary_df.iterrows():
            anti_lookup[(r["Company"], str(r["INN"]))] = r

    rows = []
    for _, r in summary_df.iterrows():
        total_kw = int(sum(1 for c in kw_cols if r[c] > 0))
        total_matches = int(sum(r[c] for c in kw_cols))
        total_groups = int(sum(1 for c in total_cols if r[c] > 0))
        found_kws = [c for c in kw_cols if r[c] > 0]
        row: dict = {
            "Company": r["Company"],
            "INN": str(r["INN"]),
            "Unique Keywords Found": total_kw,
            "Total Keyword Matches": total_matches,
            "Groups With Hits": total_groups,
            "Keywords Found": ", ".join(found_kws),
        }
        for tc in total_cols:
            row[tc[: -len(" (total)")]] = int(r[tc])

        if anti_summary_df is not None:
            ar = anti_lookup.get((r["Company"], str(r["INN"])))
            anti_total_kw = int(sum(1 for c in anti_kw_cols if ar[c] > 0)) if ar is not None else 0
            anti_total_matches = int(sum(ar[c] for c in anti_kw_cols)) if ar is not None else 0
            anti_total_groups = int(sum(1 for c in anti_total_cols if ar[c] > 0)) if ar is not None else 0
            anti_found_kws = [c for c in anti_kw_cols if ar is not None and ar[c] > 0]
            row["Anti Unique Keywords Found"] = anti_total_kw
            row["Anti Total Keyword Matches"] = anti_total_matches
            row["Anti Groups With Hits"] = anti_total_groups
            row["Anti Keywords Found"] = ", ".join(anti_found_kws)
            for tc in anti_total_cols:
                row[f"Anti: {tc[: -len(' (total)')]}"] = int(ar[tc]) if ar is not None else 0

        rows.append(row)

    group_names = [tc[: -len(" (total)")] for tc in total_cols]
    anti_group_names = [tc[: -len(" (total)")] for tc in anti_total_cols]
    base_cols = [
        "Company", "INN", "Unique Keywords Found", "Total Keyword Matches",
        "Groups With Hits", "Keywords Found",
    ]
    if anti_summary_df is not None:
        anti_stat_cols = [
            "Anti Unique Keywords Found", "Anti Total Keyword Matches",
            "Anti Groups With Hits", "Anti Keywords Found",
        ]
        cols = base_cols + anti_stat_cols + group_names + [f"Anti: {g}" for g in anti_group_names]
    else:
        cols = base_cols + group_names

    df = pd.DataFrame(rows)
    return df[[c for c in cols if c in df.columns]]
```

- [ ] **Step 2: Add `Any` import at top of keyword_scanner.py if not present**

Check line 1–20 of `app/services/keyword_scanner.py`. If `from typing import` is present, add `Any` to it:
```python
from typing import Any, Callable
```
If not present, add:
```python
from typing import Any, Callable
```

- [ ] **Step 3: Run all keyword scan format tests**

```bash
pytest tests/test_keyword_scan_format.py -v
```

Expected: all PASSED.

- [ ] **Step 4: Commit**

```bash
git add app/services/keyword_scanner.py
git commit -m "feat: extend derive_quick_summary_df with optional anti_summary_df parameter"
```

---

### Task 7: Update `scan_project_keywords` to fetch and scan anti-keyword groups

**Files:**
- Modify: `app/services/keyword_scanner.py`

- [ ] **Step 1: Update the group-fetching block**

In `scan_project_keywords`, find:
```python
    # 1. Fetch keyword groups and keywords
    groups_rows = _fetch_all("keyword_groups", "project_id", project_id, select="id, name")
    if not groups_rows:
        raise ValueError("No keyword groups defined for this project")

    group_ids = [g["id"] for g in groups_rows]
    keywords_rows = _fetch_all_in("keywords", "group_id", group_ids, select="id, group_id, keyword")

    # Build group structure
    groups = []
    all_keywords = []  # list of (keyword_text, group_name)
    for g in groups_rows:
        kws = [k["keyword"] for k in keywords_rows if k["group_id"] == g["id"]]
        groups.append({"name": g["name"], "keywords": kws})
        for kw in kws:
            all_keywords.append((kw, g["name"]))

    if not all_keywords:
        raise ValueError("No keywords defined in any group")

    # Compile patterns once
    keyword_patterns = {
        kw: re.compile(re.escape(kw), re.IGNORECASE)
        for kw, _ in all_keywords
    }
```

Replace with:
```python
    # 1. Fetch keyword groups and keywords (regular + anti)
    all_groups_rows = _fetch_all("keyword_groups", "project_id", project_id, select="id, name, is_anti")
    groups_rows = [g for g in all_groups_rows if not g.get("is_anti")]
    anti_groups_rows = [g for g in all_groups_rows if g.get("is_anti")]

    if not groups_rows:
        raise ValueError("No keyword groups defined for this project")

    # Regular keywords
    group_ids = [g["id"] for g in groups_rows]
    keywords_rows = _fetch_all_in("keywords", "group_id", group_ids, select="id, group_id, keyword")

    groups = []
    all_keywords = []  # list of (keyword_text, group_name)
    for g in groups_rows:
        kws = [k["keyword"] for k in keywords_rows if k["group_id"] == g["id"]]
        groups.append({"name": g["name"], "keywords": kws})
        for kw in kws:
            all_keywords.append((kw, g["name"]))

    if not all_keywords:
        raise ValueError("No keywords defined in any group")

    keyword_patterns = {
        kw: re.compile(re.escape(kw), re.IGNORECASE)
        for kw, _ in all_keywords
    }

    # Anti-keywords (optional — scan proceeds with empty results if none defined)
    anti_groups: list[dict] = []
    anti_all_keywords: list[tuple[str, str]] = []
    if anti_groups_rows:
        anti_group_ids = [g["id"] for g in anti_groups_rows]
        anti_keywords_rows = _fetch_all_in("keywords", "group_id", anti_group_ids, select="id, group_id, keyword")
        for g in anti_groups_rows:
            kws = [k["keyword"] for k in anti_keywords_rows if k["group_id"] == g["id"]]
            anti_groups.append({"name": g["name"], "keywords": kws})
            for kw in kws:
                anti_all_keywords.append((kw, g["name"]))

    anti_keyword_patterns = {
        kw: re.compile(re.escape(kw), re.IGNORECASE)
        for kw, _ in anti_all_keywords
    }
```

- [ ] **Step 2: Add anti-keyword scan loop inside the per-company block**

Find the block starting with `keyword_results = {}` and ending with `result_companies.append({...})`. Replace `result_companies.append({...})`:

From:
```python
            result_companies.append({
                "name": uc["name"],
                "inn": uc["inn"],
                "results": keyword_results,
            })
```
To:
```python
            # Anti-keyword scan (identical logic, separate results dict)
            anti_keyword_results = {}
            for kw, _group_name in anti_all_keywords:
                pattern = anti_keyword_patterns[kw]
                matches = []
                for posting in company_postings:
                    for field in POSTING_TEXT_FIELDS:
                        raw_text = posting.get(field) or ""
                        if not raw_text:
                            continue
                        clean_text = _strip_html(raw_text)
                        for sentence in _extract_sentences(clean_text, pattern):
                            matches.append({
                                "source": "posting",
                                "field": field,
                                "title": posting.get("title") or "",
                                "sentence": sentence,
                            })
                for article in company_news:
                    for field in NEWS_TEXT_FIELDS:
                        raw_text = article.get(field) or ""
                        if not raw_text:
                            continue
                        clean_text = _strip_html(raw_text)
                        for sentence in _extract_sentences(clean_text, pattern):
                            matches.append({
                                "source": "news",
                                "field": field,
                                "title": article.get("title") or "",
                                "sentence": sentence,
                            })
                anti_keyword_results[kw] = {"count": len(matches), "sentences": matches[:_MAX_SENTENCES_PER_KW]}

            result_companies.append({
                "name": uc["name"],
                "inn": uc["inn"],
                "results": keyword_results,
                "anti_results": anti_keyword_results,
            })
```

- [ ] **Step 3: Update the return statement**

From:
```python
    return {"groups": groups, "companies": result_companies}
```
To:
```python
    return {"groups": groups, "anti_groups": anti_groups, "companies": result_companies}
```

- [ ] **Step 4: Run existing scanner tests to confirm nothing regressed**

```bash
pytest tests/test_keyword_scanner_hits.py tests/test_keyword_scan_persistence.py tests/test_cancel_keyword_scan.py -v
```

Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/services/keyword_scanner.py
git commit -m "feat: scan_project_keywords fetches and scans anti-keyword groups"
```

---

### Task 8: Update existing keyword-group API endpoints with `is_anti=False` filter

**Files:**
- Modify: `app/main.py`

Three existing endpoints need `is_anti=False` so anti-groups don't bleed into the Keywords tab.

- [ ] **Step 1: Update `list_keyword_groups`**

Find `async def list_keyword_groups(project_id: str):` and its Supabase query. Replace:
```python
    groups = (
        supabase.table("keyword_groups")
        .select("id, name, created_at")
        .eq("project_id", project_id)
        .order("created_at")
        .execute()
    ).data
```
With:
```python
    groups = (
        supabase.table("keyword_groups")
        .select("id, name, created_at")
        .eq("project_id", project_id)
        .eq("is_anti", False)
        .order("created_at")
        .execute()
    ).data
```

- [ ] **Step 2: Update `create_keyword_group`**

Find `async def create_keyword_group(...)` and its insert. Replace:
```python
    result = supabase.table("keyword_groups").insert({
        "project_id": project_id,
        "name": body.name,
    }).execute()
```
With:
```python
    result = supabase.table("keyword_groups").insert({
        "project_id": project_id,
        "name": body.name,
        "is_anti": False,
    }).execute()
```

- [ ] **Step 3: Update `import_keyword_groups` — existing_groups fetch**

Find `async def import_keyword_groups(...)`. Replace:
```python
    existing_groups = (
        supabase.table("keyword_groups")
        .select("id, name")
        .eq("project_id", project_id)
        .execute()
    ).data
```
With:
```python
    existing_groups = (
        supabase.table("keyword_groups")
        .select("id, name")
        .eq("project_id", project_id)
        .eq("is_anti", False)
        .execute()
    ).data
```

- [ ] **Step 4: Update `import_keyword_groups` — batch insert of new groups**

Find the batch insert inside `import_keyword_groups`:
```python
        result = supabase.table("keyword_groups").insert([
            {"project_id": project_id, "name": name} for name in new_names
        ]).execute()
```
Replace with:
```python
        result = supabase.table("keyword_groups").insert([
            {"project_id": project_id, "name": name, "is_anti": False} for name in new_names
        ]).execute()
```

- [ ] **Step 5: Commit**

```bash
git add app/main.py
git commit -m "fix: add is_anti=False filter to existing keyword-group endpoints"
```

---

### Task 9: Add three new anti-keyword API endpoints

**Files:**
- Modify: `app/main.py`

The JS frontend for the anti-keywords tab reuses the existing `DELETE /api/keyword-groups/{group_id}`, `POST /api/keyword-groups/{group_id}/keywords`, and `DELETE /api/keywords/{keyword_id}` endpoints (they work by ID and don't care about `is_anti`). Only three new endpoints are needed.

- [ ] **Step 1: Add anti-keyword group endpoints after the existing import endpoint**

Find the comment `# ──────────────────────── Stop Words ────────────────────────` in `app/main.py`. Insert the following block immediately before it:

```python
# ──────────────────────── Anti-Keyword Groups ────────────────────────


@app.get("/api/projects/{project_id}/anti-keyword-groups")
async def list_anti_keyword_groups(project_id: str):
    groups = (
        supabase.table("keyword_groups")
        .select("id, name, created_at")
        .eq("project_id", project_id)
        .eq("is_anti", True)
        .order("created_at")
        .execute()
    ).data

    group_ids = [g["id"] for g in groups]
    keywords = []
    for i in range(0, len(group_ids), 50):
        chunk = group_ids[i:i + 50]
        offset = 0
        while True:
            batch = (
                supabase.table("keywords")
                .select("id, group_id, keyword")
                .in_("group_id", chunk)
                .range(offset, offset + 999)
                .execute()
            ).data
            keywords.extend(batch)
            if len(batch) < 1000:
                break
            offset += 1000

    for g in groups:
        g["keywords"] = [k for k in keywords if k["group_id"] == g["id"]]

    return groups


@app.post("/api/projects/{project_id}/anti-keyword-groups")
async def create_anti_keyword_group(project_id: str, body: CreateKeywordGroup):
    result = supabase.table("keyword_groups").insert({
        "project_id": project_id,
        "name": body.name,
        "is_anti": True,
    }).execute()
    return result.data[0]


@app.post("/api/projects/{project_id}/anti-keyword-groups/import")
async def import_anti_keyword_groups(project_id: str, file: UploadFile = File(...)):
    if not (file.filename or "").endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="File must be .xlsx")

    file_bytes = await file.read()
    try:
        parsed = parse_keyword_xlsx(file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    existing_groups = (
        supabase.table("keyword_groups")
        .select("id, name")
        .eq("project_id", project_id)
        .eq("is_anti", True)
        .execute()
    ).data
    group_by_name = {g["name"]: g["id"] for g in existing_groups}

    seen: set[str] = set()
    ordered_group_names: list[str] = []
    for row in parsed:
        if row["group"] not in seen:
            seen.add(row["group"])
            ordered_group_names.append(row["group"])

    existing_names = [n for n in ordered_group_names if n in group_by_name]
    new_names = [n for n in ordered_group_names if n not in group_by_name]

    group_id_by_name: dict[str, str] = {}
    for name in existing_names:
        group_id_by_name[name] = group_by_name[name]

    if new_names:
        result = supabase.table("keyword_groups").insert([
            {"project_id": project_id, "name": name, "is_anti": True} for name in new_names
        ]).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create anti-keyword groups")
        for g in result.data:
            group_id_by_name[g["name"]] = g["id"]

    groups_created = len(new_names)
    groups_updated = len(existing_names)

    all_group_ids = list(group_id_by_name.values())
    all_existing_kws = []
    for i in range(0, len(all_group_ids), 50):
        chunk = all_group_ids[i:i + 50]
        offset = 0
        while True:
            batch = (
                supabase.table("keywords")
                .select("group_id, keyword")
                .in_("group_id", chunk)
                .range(offset, offset + 999)
                .execute()
            ).data
            all_existing_kws.extend(batch)
            if len(batch) < 1000:
                break
            offset += 1000

    existing_kws_by_group: dict[str, set[str]] = {gid: set() for gid in all_group_ids}
    for kw_row in all_existing_kws:
        existing_kws_by_group[kw_row["group_id"]].add(kw_row["keyword"].lower())

    keywords_added = 0
    keywords_skipped = 0
    for row in parsed:
        group_id = group_id_by_name[row["group"]]
        existing_set = existing_kws_by_group[group_id]
        to_insert = []
        for kw in row["keywords"]:
            if kw.lower() in existing_set:
                keywords_skipped += 1
            else:
                to_insert.append({"group_id": group_id, "keyword": kw})
                existing_set.add(kw.lower())
                keywords_added += 1
        if to_insert:
            supabase.table("keywords").insert(to_insert).execute()

    return {
        "groups_created": groups_created,
        "groups_updated": groups_updated,
        "keywords_added": keywords_added,
        "keywords_skipped": keywords_skipped,
    }
```

- [ ] **Step 2: Commit**

```bash
git add app/main.py
git commit -m "feat: add anti-keyword group API endpoints (list, create, import)"
```

---

### Task 10: Update `download-with-contacts` endpoint for 6-sheet output

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Update the sheet-parsing block**

Find inside `keyword_scan_download_with_contacts`:
```python
    # 2. Parse Summary and Details sheets from stored XLSX
    stored_buf = io.BytesIO(xlsx_bytes)
    xf = pd.ExcelFile(stored_buf)
    summary_df = xf.parse("Summary")
    details_df = xf.parse("Details") if "Details" in xf.sheet_names else pd.DataFrame(
        columns=["Company", "INN", "Keyword Group", "Keyword", "Total Matches", "From Postings", "From News", "Sentences"]
    )

    # 3. Derive Quick_Summary rows from Summary sheet
    qs_df = derive_quick_summary_df(summary_df)
```

Replace with:
```python
    # 2. Parse all sheets from stored XLSX (Anti_Summary/Anti_Details absent in pre-migration results)
    stored_buf = io.BytesIO(xlsx_bytes)
    xf = pd.ExcelFile(stored_buf)
    summary_df = xf.parse("Summary")
    details_df = xf.parse("Details") if "Details" in xf.sheet_names else pd.DataFrame(
        columns=["Company", "INN", "Keyword Group", "Keyword", "Total Matches", "From Postings", "From News", "Sentences"]
    )
    anti_summary_df = xf.parse("Anti_Summary") if "Anti_Summary" in xf.sheet_names else None
    anti_details_df = xf.parse("Anti_Details") if "Anti_Details" in xf.sheet_names else pd.DataFrame(
        columns=["Company", "INN", "Keyword Group", "Keyword", "Total Matches", "From Postings", "From News", "Sentences"]
    )

    # 3. Derive Quick_Summary rows (including anti-keyword columns when available)
    qs_df = derive_quick_summary_df(summary_df, anti_summary_df)
```

- [ ] **Step 2: Update the XLSX writer block**

Find:
```python
    # 8. Write 4-sheet XLSX
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        qs_df.to_excel(writer, sheet_name="Quick_Summary", index=False)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        details_df.to_excel(writer, sheet_name="Details", index=False)
        contacts_df.to_excel(writer, sheet_name="Contacts", index=False)
```

Replace with:
```python
    # 8. Write XLSX: Quick_Summary, Summary, Details, Anti_Summary*, Anti_Details*, Contacts
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        qs_df.to_excel(writer, sheet_name="Quick_Summary", index=False)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        details_df.to_excel(writer, sheet_name="Details", index=False)
        if anti_summary_df is not None:
            anti_summary_df.to_excel(writer, sheet_name="Anti_Summary", index=False)
        anti_details_df.to_excel(writer, sheet_name="Anti_Details", index=False)
        contacts_df.to_excel(writer, sheet_name="Contacts", index=False)
```

- [ ] **Step 3: Update the docstring**

Replace:
```python
    """4-sheet XLSX: Quick_Summary (with Contacts Found), Summary, Details, Contacts."""
```
With:
```python
    """XLSX with Quick_Summary (Contacts Found + anti-keyword cols), Summary, Details, Anti_Summary, Anti_Details, Contacts."""
```

- [ ] **Step 4: Run keyword scan format tests to confirm no regressions**

```bash
pytest tests/test_keyword_scan_format.py -v
```

Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/main.py
git commit -m "feat: update download-with-contacts to include anti-keyword sheets"
```

---

### Task 11: Add anti-keywords tab HTML and CSS

**Files:**
- Modify: `app/templates/project.html`

- [ ] **Step 1: Add new radio input for the tab**

Find:
```html
            <input type="radio" id="tab-keywords"  name="tab">
            <input type="radio" id="tab-roles"     name="tab">
```
Replace with:
```html
            <input type="radio" id="tab-keywords"     name="tab">
            <input type="radio" id="tab-antikeywords" name="tab">
            <input type="radio" id="tab-roles"        name="tab">
```

- [ ] **Step 2: Add CSS display rule for the new panel**

Find in the `<style>` block:
```css
        #tab-keywords:checked  ~ #panel-keywords  { display: block; }
        #tab-roles:checked     ~ #panel-roles     { display: block; }
```
Replace with:
```css
        #tab-keywords:checked      ~ #panel-keywords      { display: block; }
        #tab-antikeywords:checked  ~ #panel-antikeywords  { display: block; }
        #tab-roles:checked         ~ #panel-roles         { display: block; }
```

- [ ] **Step 3: Add active-state CSS rules for the new tab**

Find:
```css
        #tab-upload:checked    ~ .tab-nav label[for="tab-upload"]:not(.tab-yellow):not(.tab-green) .step-num,
        #tab-stopwords:checked ~ .tab-nav label[for="tab-stopwords"]:not(.tab-yellow):not(.tab-green) .step-num,
        #tab-keywords:checked  ~ .tab-nav label[for="tab-keywords"]:not(.tab-yellow):not(.tab-green) .step-num,
        #tab-roles:checked     ~ .tab-nav label[for="tab-roles"]:not(.tab-yellow):not(.tab-green) .step-num,
        #tab-export:checked    ~ .tab-nav label[for="tab-export"] .step-num {
```
Replace with:
```css
        #tab-upload:checked        ~ .tab-nav label[for="tab-upload"]:not(.tab-yellow):not(.tab-green) .step-num,
        #tab-stopwords:checked     ~ .tab-nav label[for="tab-stopwords"]:not(.tab-yellow):not(.tab-green) .step-num,
        #tab-keywords:checked      ~ .tab-nav label[for="tab-keywords"]:not(.tab-yellow):not(.tab-green) .step-num,
        #tab-antikeywords:checked  ~ .tab-nav label[for="tab-antikeywords"]:not(.tab-yellow):not(.tab-green) .step-num,
        #tab-roles:checked         ~ .tab-nav label[for="tab-roles"]:not(.tab-yellow):not(.tab-green) .step-num,
        #tab-export:checked        ~ .tab-nav label[for="tab-export"] .step-num {
```

Find immediately after (the label color rule):
```css
        #tab-upload:checked    ~ .tab-nav label[for="tab-upload"]:not(.tab-yellow):not(.tab-green),
        #tab-stopwords:checked ~ .tab-nav label[for="tab-stopwords"]:not(.tab-yellow):not(.tab-green),
        #tab-keywords:checked  ~ .tab-nav label[for="tab-keywords"]:not(.tab-yellow):not(.tab-green),
        #tab-roles:checked     ~ .tab-nav label[for="tab-roles"]:not(.tab-yellow):not(.tab-green),
        #tab-export:checked    ~ .tab-nav label[for="tab-export"] {
```
Replace with:
```css
        #tab-upload:checked        ~ .tab-nav label[for="tab-upload"]:not(.tab-yellow):not(.tab-green),
        #tab-stopwords:checked     ~ .tab-nav label[for="tab-stopwords"]:not(.tab-yellow):not(.tab-green),
        #tab-keywords:checked      ~ .tab-nav label[for="tab-keywords"]:not(.tab-yellow):not(.tab-green),
        #tab-antikeywords:checked  ~ .tab-nav label[for="tab-antikeywords"]:not(.tab-yellow):not(.tab-green),
        #tab-roles:checked         ~ .tab-nav label[for="tab-roles"]:not(.tab-yellow):not(.tab-green),
        #tab-export:checked        ~ .tab-nav label[for="tab-export"] {
```

- [ ] **Step 4: Update tab-nav labels (step numbers + new label)**

Find:
```html
            <nav class="tab-nav">
                <label for="tab-upload">   <span class="step-num">1</span> Upload</label>
                <span class="step-arrow" aria-hidden="true">›</span>
                <label for="tab-stopwords"><span class="step-num">2</span> Stop Words</label>
                <span class="step-arrow" aria-hidden="true">›</span>
                <label for="tab-keywords"> <span class="step-num">3</span> Keywords</label>
                <span class="step-arrow" aria-hidden="true">›</span>
                <label for="tab-roles">    <span class="step-num">4</span> Roles</label>
                <span class="step-arrow" aria-hidden="true">›</span>
                <label for="tab-export">   <span class="step-num">5</span> Export</label>
            </nav>
```
Replace with:
```html
            <nav class="tab-nav">
                <label for="tab-upload">        <span class="step-num">1</span> Upload</label>
                <span class="step-arrow" aria-hidden="true">›</span>
                <label for="tab-stopwords">     <span class="step-num">2</span> Stop Words</label>
                <span class="step-arrow" aria-hidden="true">›</span>
                <label for="tab-keywords">      <span class="step-num">3</span> Keywords</label>
                <span class="step-arrow" aria-hidden="true">›</span>
                <label for="tab-antikeywords">  <span class="step-num">4</span> Анти ключевые слова</label>
                <span class="step-arrow" aria-hidden="true">›</span>
                <label for="tab-roles">         <span class="step-num">5</span> Roles</label>
                <span class="step-arrow" aria-hidden="true">›</span>
                <label for="tab-export">        <span class="step-num">6</span> Export</label>
            </nav>
```

- [ ] **Step 5: Add the new panel HTML**

Find `<!-- ── Roles panel ── -->`. Insert the following block immediately before it:

```html
            <!-- ── Anti-Keywords panel ── -->
            <div id="panel-antikeywords">

                <article>
                    <h3>Анти ключевые слова</h3>
                    <div style="display:flex; gap:0.5em; margin-bottom:1em; align-items:center; flex-wrap:wrap;">
                        <input type="text" id="new-anti-group-name" placeholder="Group name (e.g. Риски)" style="margin:0;">
                        <button onclick="addAntiKeywordGroup()" style="margin:0; white-space:nowrap;">Add Group</button>
                        <button id="import-anti-kw-btn" class="secondary" style="margin:0; white-space:nowrap;"
                                onclick="document.getElementById('anti-kw-import-input').click()">Import from file</button>
                        <input type="file" id="anti-kw-import-input" accept=".xlsx" style="display:none"
                               onchange="importAntiKeywordFile(this)">
                        <span id="anti-kw-import-status" style="font-size:0.85em; color:var(--pico-muted-color);"></span>
                    </div>
                    <div id="anti-keyword-groups-container"></div>
                    <p style="font-size:0.85em; color:var(--pico-muted-color); margin-top:1em;">
                        Anti-keywords are scanned as part of the keyword scan (run from the Keywords tab).
                        Results appear on the Anti_Summary and Anti_Details sheets in the download.
                    </p>
                </article>

            </div><!-- /panel-antikeywords -->

```

- [ ] **Step 6: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: add anti-keywords tab HTML and CSS"
```

---

### Task 12: Add anti-keywords JS functions

**Files:**
- Modify: `app/templates/project.html`

- [ ] **Step 1: Add anti-keyword JS functions after the `importKeywordFile` function**

Find the comment `// ---- Keyword Scan Status ----` in the `<script>` block. Insert the following immediately before it:

```javascript
        // ---- Anti-Keyword Groups ----
        async function loadAntiKeywordGroups() {
            const container = document.getElementById("anti-keyword-groups-container");
            try {
                const resp = await fetch(`/api/projects/${PROJECT_ID}/anti-keyword-groups`);
                const groups = await resp.json();

                if (!groups.length) {
                    container.innerHTML = '<p style="color:var(--pico-muted-color); font-size:0.9em;">No anti-keyword groups yet. Create one above.</p>';
                    return;
                }

                container.innerHTML = groups.map(g => {
                    const kwTags = g.keywords.map(k =>
                        `<span class="kw-tag">${escapeHtml(k.keyword)}<button onclick="deleteKeyword('${k.id}')" title="Remove">&times;</button></span>`
                    ).join("");

                    return `<details class="kw-group" open>
                        <summary>
                            <div class="kw-group-header">
                                <span>${escapeHtml(g.name)}</span>
                                <button onclick="event.preventDefault(); deleteAntiKeywordGroup('${g.id}')" title="Delete group">Delete group</button>
                            </div>
                        </summary>
                        <div class="kw-list">${kwTags || '<span style="color:#aaa">No keywords</span>'}</div>
                        <div class="kw-add-row">
                            <input type="text" id="anti-kw-input-${g.id}" placeholder="Add keyword...">
                            <button onclick="addAntiKeyword('${g.id}')">Add</button>
                        </div>
                    </details>`;
                }).join("");
            } catch (err) {
                console.error("Failed to load anti-keyword groups:", err);
            }
        }

        async function addAntiKeywordGroup() {
            const input = document.getElementById("new-anti-group-name");
            const name = input.value.trim();
            if (!name) return;
            await fetch(`/api/projects/${PROJECT_ID}/anti-keyword-groups`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name }),
            });
            input.value = "";
            loadAntiKeywordGroups();
            refreshTabStates();
        }

        async function deleteAntiKeywordGroup(groupId) {
            if (!confirm("Delete this anti-keyword group and all its keywords?")) return;
            await fetch(`/api/keyword-groups/${groupId}`, { method: "DELETE" });
            loadAntiKeywordGroups();
            refreshTabStates();
        }

        async function addAntiKeyword(groupId) {
            const input = document.getElementById(`anti-kw-input-${groupId}`);
            const keyword = input.value.trim();
            if (!keyword) return;
            await fetch(`/api/keyword-groups/${groupId}/keywords`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ keyword }),
            });
            input.value = "";
            loadAntiKeywordGroups();
            refreshTabStates();
        }

        async function importAntiKeywordFile(input) {
            const file = input.files[0];
            if (!file) return;
            const btn = document.getElementById("import-anti-kw-btn");
            const status = document.getElementById("anti-kw-import-status");
            btn.disabled = true;
            btn.setAttribute("aria-busy", "true");
            status.textContent = "Importing...";
            try {
                const formData = new FormData();
                formData.append("file", file);
                const resp = await fetch(`/api/projects/${PROJECT_ID}/anti-keyword-groups/import`, {
                    method: "POST",
                    body: formData,
                });
                let data = {};
                try { data = await resp.json(); } catch (_) {}
                if (!resp.ok) {
                    status.textContent = "Error: " + (data.detail || resp.statusText || "Import failed");
                } else {
                    status.textContent = `Done: ${data.groups_created} groups, ${data.keywords_added} keywords added, ${data.keywords_skipped} skipped`;
                    loadAntiKeywordGroups();
                    refreshTabStates();
                }
            } catch (err) {
                status.textContent = "Error: " + err.message;
            } finally {
                btn.disabled = false;
                btn.removeAttribute("aria-busy");
                input.value = "";
            }
        }

```

- [ ] **Step 2: Add `loadAntiKeywordGroups()` call to the page-load block**

Find:
```javascript
        loadKeywordGroups();
```
Add the call immediately after it:
```javascript
        loadKeywordGroups();
        loadAntiKeywordGroups();
```

- [ ] **Step 3: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: add anti-keywords tab JS functions"
```

---

### Task 13: Update `refreshTabStates` for anti-keywords tab

**Files:**
- Modify: `app/templates/project.html`

- [ ] **Step 1: Add anti-keyword-groups fetch to the Promise.all**

Find:
```javascript
                const [sessionsResp, stopWordsResp, scanStatusResp, projectResp, contactScanResp, kwGroupsResp] =
                    await Promise.all([
                        fetch(`/api/projects/${PROJECT_ID}/sessions`),
                        fetch(`/api/projects/${PROJECT_ID}/stop-words`),
                        fetch(`/api/projects/${PROJECT_ID}/keyword-scan/status`),
                        fetch(`/api/projects/${PROJECT_ID}/details`),
                        fetch(`/api/projects/${PROJECT_ID}/contact-scan/latest/status`),
                        fetch(`/api/projects/${PROJECT_ID}/keyword-groups`),
                    ]);

                if (!sessionsResp.ok || !stopWordsResp.ok || !scanStatusResp.ok ||
                    !projectResp.ok || !contactScanResp.ok || !kwGroupsResp.ok) {
                    console.error('refreshTabStates: one or more API calls failed');
                    return;
                }

                const sessions    = await sessionsResp.json();
                const stopWords   = await stopWordsResp.json();
                const scanStatus  = await scanStatusResp.json();
                const project     = await projectResp.json();
                const contactScan = await contactScanResp.json();
                const kwGroups    = await kwGroupsResp.json();
```

Replace with:
```javascript
                const [sessionsResp, stopWordsResp, scanStatusResp, projectResp, contactScanResp, kwGroupsResp, antiKwGroupsResp] =
                    await Promise.all([
                        fetch(`/api/projects/${PROJECT_ID}/sessions`),
                        fetch(`/api/projects/${PROJECT_ID}/stop-words`),
                        fetch(`/api/projects/${PROJECT_ID}/keyword-scan/status`),
                        fetch(`/api/projects/${PROJECT_ID}/details`),
                        fetch(`/api/projects/${PROJECT_ID}/contact-scan/latest/status`),
                        fetch(`/api/projects/${PROJECT_ID}/keyword-groups`),
                        fetch(`/api/projects/${PROJECT_ID}/anti-keyword-groups`),
                    ]);

                if (!sessionsResp.ok || !stopWordsResp.ok || !scanStatusResp.ok ||
                    !projectResp.ok || !contactScanResp.ok || !kwGroupsResp.ok || !antiKwGroupsResp.ok) {
                    console.error('refreshTabStates: one or more API calls failed');
                    return;
                }

                const sessions      = await sessionsResp.json();
                const stopWords     = await stopWordsResp.json();
                const scanStatus    = await scanStatusResp.json();
                const project       = await projectResp.json();
                const contactScan   = await contactScanResp.json();
                const kwGroups      = await kwGroupsResp.json();
                const antiKwGroups  = await antiKwGroupsResp.json();
```

- [ ] **Step 2: Add tab state for the new tab and fix the Roles step number**

Find:
```javascript
                // Tab 3 — Keywords
                const hasKeywords = kwGroups.some(g => g.keywords && g.keywords.length > 0);
                setTabState('keywords', scanStatus.status === 'done' ? 'green' : hasKeywords ? 'yellow' : '');

                // Tab 4 — Roles
                const hasRoles          = (project.target_roles || []).length > 0;
                const contactScanDone   = contactScan.status === 'completed';
                setTabState('roles', contactScanDone ? 'green' : hasRoles ? 'yellow' : '');
```

Replace with:
```javascript
                // Tab 3 — Keywords
                const hasKeywords = kwGroups.some(g => g.keywords && g.keywords.length > 0);
                setTabState('keywords', scanStatus.status === 'done' ? 'green' : hasKeywords ? 'yellow' : '');

                // Tab 4 — Анти ключевые слова
                const hasAntiKeywords = antiKwGroups.some(g => g.keywords && g.keywords.length > 0);
                setTabState('antikeywords', hasAntiKeywords ? 'yellow' : '');

                // Tab 5 — Roles
                const hasRoles          = (project.target_roles || []).length > 0;
                const contactScanDone   = contactScan.status === 'completed';
                setTabState('roles', contactScanDone ? 'green' : hasRoles ? 'yellow' : '');
```

- [ ] **Step 3: Run the full test suite**

```bash
pytest -v
```

Expected: all existing tests PASSED (no regressions).

- [ ] **Step 4: Manual smoke test**

Start the server (`uvicorn app.main:app --reload`) and verify:
1. Project page shows 6 tabs with correct step numbers
2. "Анти ключевые слова" tab shows the group management UI
3. Adding an anti-keyword group via UI creates it via `/anti-keyword-groups` endpoint
4. Adding a keyword to an anti-group works (reuses existing add-keyword endpoint)
5. Deleting an anti-group works (reuses existing delete-group endpoint)
6. Import from xlsx on the anti-keywords tab imports into anti-groups
7. Running a keyword scan from the Keywords tab completes successfully
8. The downloaded XLSX has 5 sheets: Quick_Summary, Summary, Details, Anti_Summary, Anti_Details
9. Quick_Summary has "Anti Unique Keywords Found", "Anti Total Keyword Matches", "Anti Groups With Hits", "Anti Keywords Found" columns
10. Download Keyword + Contacts XLSX works and includes Anti_Summary and Anti_Details sheets

- [ ] **Step 5: Commit**

```bash
git add app/templates/project.html
git commit -m "feat: update refreshTabStates for anti-keywords tab state"
```
