import io
import pandas as pd
from app.services.keyword_scanner import generate_keyword_xlsx


def _scan_result():
    return {
        "groups": [
            {"name": "Growth", "keywords": ["expand", "scale"]},
            {"name": "Tech",   "keywords": ["AI", "ML"]},
        ],
        "companies": [
            {
                "name": "Acme Corp",
                "inn":  "7712345678",
                "results": {
                    "expand": {"count": 2, "sentences": []},
                    "scale":  {"count": 0, "sentences": []},
                    "AI":     {"count": 1, "sentences": []},
                    "ML":     {"count": 3, "sentences": []},
                },
            },
            {
                "name": "Zero Inc",
                "inn":  "0000000000",
                "results": {
                    "expand": {"count": 0, "sentences": []},
                    "scale":  {"count": 0, "sentences": []},
                    "AI":     {"count": 0, "sentences": []},
                    "ML":     {"count": 0, "sentences": []},
                },
            },
        ],
    }


def test_sheet_order():
    buf = generate_keyword_xlsx(_scan_result())
    assert pd.ExcelFile(buf).sheet_names == ["Quick_Summary", "Summary", "Details"]


def test_quick_summary_columns():
    buf = generate_keyword_xlsx(_scan_result())
    df = pd.read_excel(buf, sheet_name="Quick_Summary")
    assert list(df.columns[:6]) == [
        "Company", "INN", "Unique Keywords Found", "Total Keyword Matches", "Groups With Hits", "Keywords Found",
    ]
    assert "Growth" in df.columns
    assert "Tech" in df.columns


def test_quick_summary_acme_values():
    buf = generate_keyword_xlsx(_scan_result())
    df = pd.read_excel(buf, sheet_name="Quick_Summary")
    row = df[df["Company"] == "Acme Corp"].iloc[0]
    # expand=2, AI=1, ML=3 matched; scale=0 did not
    assert row["Unique Keywords Found"] == 3
    assert row["Groups With Hits"] == 2
    assert row["Growth"] == 1   # only expand in Growth
    assert row["Tech"] == 2     # AI and ML in Tech
    kws = row["Keywords Found"]
    assert "expand" in kws
    assert "AI" in kws
    assert "ML" in kws
    assert "scale" not in kws


def test_quick_summary_zero_company():
    buf = generate_keyword_xlsx(_scan_result())
    df = pd.read_excel(buf, sheet_name="Quick_Summary")
    row = df[df["Company"] == "Zero Inc"].iloc[0]
    assert row["Unique Keywords Found"] == 0
    assert row["Groups With Hits"] == 0
    assert row["Growth"] == 0
    assert row["Tech"] == 0
    # Keywords Found is empty string or NaN when no matches
    assert str(row["Keywords Found"]) in ("", "nan")


from app.services.keyword_scanner import derive_quick_summary_df


def _summary_df():
    return pd.DataFrame([
        {
            "Company": "Acme Corp",
            "INN": "7712345678",
            "expand": 2,
            "scale": 0,
            "Growth (total)": 1,
            "AI": 1,
            "ML": 3,
            "Tech (total)": 2,
        },
        {
            "Company": "Zero Inc",
            "INN": "0000000000",
            "expand": 0,
            "scale": 0,
            "Growth (total)": 0,
            "AI": 0,
            "ML": 0,
            "Tech (total)": 0,
        },
    ])


def test_derive_qs_columns():
    df = derive_quick_summary_df(_summary_df())
    assert list(df.columns[:6]) == [
        "Company", "INN", "Unique Keywords Found", "Total Keyword Matches", "Groups With Hits", "Keywords Found",
    ]
    assert "Growth" in df.columns
    assert "Tech" in df.columns


def test_derive_qs_acme_values():
    df = derive_quick_summary_df(_summary_df())
    row = df[df["Company"] == "Acme Corp"].iloc[0]
    assert row["Unique Keywords Found"] == 3   # expand, AI, ML
    assert row["Total Keyword Matches"] == 6   # expand=2, AI=1, ML=3
    assert row["Groups With Hits"] == 2
    assert row["Growth"] == 1
    assert row["Tech"] == 2
    kws = row["Keywords Found"]
    assert "expand" in kws
    assert "AI" in kws
    assert "ML" in kws
    assert "scale" not in kws


def test_derive_qs_zero_values():
    df = derive_quick_summary_df(_summary_df())
    row = df[df["Company"] == "Zero Inc"].iloc[0]
    assert row["Unique Keywords Found"] == 0
    assert row["Total Keyword Matches"] == 0
    assert row["Groups With Hits"] == 0
    assert row["Growth"] == 0
    assert row["Tech"] == 0
    assert str(row["Keywords Found"]) in ("", "nan")


def test_contacts_found_column_injection():
    """Verify the Contacts Found column injection logic used in the endpoint."""
    import pandas as pd
    summary = pd.DataFrame([
        {"Company": "Acme Corp", "INN": "7712345678", "expand": 2, "scale": 0, "Growth (total)": 1},
        {"Company": "Zero Inc",  "INN": "0000000000", "expand": 0, "scale": 0, "Growth (total)": 0},
    ])
    qs_df = derive_quick_summary_df(summary)
    inn_to_count = {"7712345678": 5}
    qs_df.insert(5, "Contacts Found", qs_df["INN"].apply(lambda inn: inn_to_count.get(str(inn), 0)))
    acme = qs_df[qs_df["Company"] == "Acme Corp"].iloc[0]
    zero = qs_df[qs_df["Company"] == "Zero Inc"].iloc[0]
    assert acme["Contacts Found"] == 5
    assert zero["Contacts Found"] == 0
    # Contacts Found must be column 6 (index 5)
    assert list(qs_df.columns).index("Contacts Found") == 5


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
