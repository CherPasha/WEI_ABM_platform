import io
import pytest
import openpyxl

from app.services.keyword_parser import parse_keyword_xlsx


def _make_xlsx(rows: list) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_basic_rows():
    data = _make_xlsx([
        ("ABM", '"ABM маркетинг", "Account based marketing"'),
        ("Конкуренты", '"конкурент", "аналог"'),
    ])
    result = parse_keyword_xlsx(data)
    assert result == [
        {"group": "ABM", "keywords": ["ABM маркетинг", "Account based marketing"]},
        {"group": "Конкуренты", "keywords": ["конкурент", "аналог"]},
    ]


def test_skips_empty_group_name():
    data = _make_xlsx([
        ("ABM", '"kw1"'),
        ("", '"kw2"'),
        (None, '"kw3"'),
    ])
    result = parse_keyword_xlsx(data)
    assert len(result) == 1
    assert result[0]["group"] == "ABM"


def test_empty_keywords_cell():
    data = _make_xlsx([
        ("ABM", ""),
    ])
    result = parse_keyword_xlsx(data)
    assert result == [{"group": "ABM", "keywords": []}]


def test_fewer_than_two_columns_raises():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ABM"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="at least 2 columns"):
        parse_keyword_xlsx(buf.getvalue())


def test_single_keyword_no_comma():
    data = _make_xlsx([
        ("Group", '"только один"'),
    ])
    result = parse_keyword_xlsx(data)
    assert result == [{"group": "Group", "keywords": ["только один"]}]
