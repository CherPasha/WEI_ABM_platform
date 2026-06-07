import io
import re

import openpyxl


def parse_keyword_xlsx(file_bytes: bytes) -> list[dict]:
    """
    Parse an Excel file with two columns:
      col 0 — keyword group name (str)
      col 1 — keywords as quoted comma-separated string, e.g. "kw1", "kw2"

    Returns list of {"group": str, "keywords": list[str]}.
    Raises ValueError if file has fewer than 2 columns.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active

    if ws.max_column is not None and ws.max_column < 2:
        raise ValueError("File must have at least 2 columns")

    results = []
    for row in ws.iter_rows(values_only=True):
        raw_group = row[0]
        group_name = str(raw_group).strip() if raw_group is not None else ""
        if not group_name:
            continue

        raw_kw = row[1]
        kw_cell = str(raw_kw) if raw_kw is not None else ""
        keywords = re.findall(r'"([^"]+)"', kw_cell)

        results.append({"group": group_name, "keywords": keywords})

    wb.close()
    return results


def parse_stop_word_xlsx(file_bytes: bytes) -> list[str]:
    """
    Parse an Excel file with one column of stop words, one word per row.

    Returns list of non-empty stripped strings.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    results = []
    for row in ws.iter_rows(values_only=True):
        if not row:
            continue
        raw_word = row[0]
        word = str(raw_word).strip() if raw_word is not None else ""
        if word:
            results.append(word)
    wb.close()
    return results
