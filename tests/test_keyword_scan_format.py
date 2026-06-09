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
    assert list(df.columns[:5]) == [
        "Company", "INN", "Total Keywords Found", "Groups With Hits", "Keywords Found"
    ]
    assert "Growth" in df.columns
    assert "Tech" in df.columns


def test_quick_summary_acme_values():
    buf = generate_keyword_xlsx(_scan_result())
    df = pd.read_excel(buf, sheet_name="Quick_Summary")
    row = df[df["Company"] == "Acme Corp"].iloc[0]
    # expand=2, AI=1, ML=3 matched; scale=0 did not
    assert row["Total Keywords Found"] == 3
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
    assert row["Total Keywords Found"] == 0
    assert row["Groups With Hits"] == 0
    assert row["Growth"] == 0
    assert row["Tech"] == 0
    # Keywords Found is empty string or NaN when no matches
    assert str(row["Keywords Found"]) in ("", "nan")
