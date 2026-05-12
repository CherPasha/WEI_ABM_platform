import io
import re
import json
import pandas as pd


# Mapping from Russian xlsx column names to database column names
COLUMN_MAP = {
    "Наименование": "legal_name",
    "ИНН": "inn",
    "КПП": "kpp",
    "ОГРН": "ogrn",
    "Дата регистрации": "registration_date",
    "Адрес": "address",
    "Регион регистрации": "region",
    "Ссылка на сайт": "website_url",
    "Выручка": "revenue",
    "Количество сотрудников": "employee_count",
    "ФИО руководителя": "ceo_name",
    "Должность руководителя": "ceo_position",
    "Номер телефона": "phone",
    "Электронная почта": "email",
    "Основной вид деятельности": "main_activity",

}


def is_valid_url(value: str) -> bool:
    """Check if a value looks like a real URL (not a random number)."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value:
        return False
    # Must contain a dot and not be purely numeric
    if re.match(r"^\d+$", value):
        return False
    if "." not in value:
        return False
    return True


def clean_url(value: str) -> str:
    """Strip protocol and trailing slashes for a cleaner URL."""
    value = value.strip()
    for prefix in ("https://", "http://", "www."):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
    return value.rstrip("/")


def parse_xlsx_to_companies(file_bytes: bytes, filename: str = "") -> list[dict]:
    """Parse an xlsx/xls/csv file into a list of company dicts ready for Supabase insert."""
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(file_bytes))
    else:
        df = pd.read_excel(io.BytesIO(file_bytes))

    companies = []
    for _, row in df.iterrows():
        company = {}

        # Map known columns
        for ru_col, db_col in COLUMN_MAP.items():
            if ru_col in df.columns:
                val = row[ru_col]
                if pd.isna(val):
                    company[db_col] = None
                else:
                    company[db_col] = str(val)
            else:
                company[db_col] = None

        # Validate and clean website URL
        if company.get("website_url") and not is_valid_url(company["website_url"]):
            company["website_url"] = None

        # Initialize known_names with the legal name
        if company.get("legal_name"):
            company["known_names"] = [company["legal_name"]]
        else:
            company["known_names"] = []

        # Store all columns as raw_data JSON
        raw = {}
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                raw[col] = None
            else:
                raw[col] = str(val) if not isinstance(val, (int, float)) else val
        company["raw_data"] = json.dumps(raw, ensure_ascii=False)

        companies.append(company)

    return companies
