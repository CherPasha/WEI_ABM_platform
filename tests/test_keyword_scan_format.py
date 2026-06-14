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
