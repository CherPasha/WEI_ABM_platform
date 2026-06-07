# HH Pipeline: OAuth Authentication

**Date:** 2026-06-07
**Scope:** Add `client_credentials` OAuth authentication to the existing HH vacancy pipeline. Inputs and outputs of all public functions remain unchanged.

---

## Background

The HH.ru API now requires authenticated requests. The current pipeline (`app/services/postings_finder.py`) makes unauthenticated GET requests which are being rejected. The standalone script `hh_vacancies_simple_export_ed02.py` already implements the correct `client_credentials` flow and serves as the reference implementation.

---

## Architecture

No new files. Changes are confined to two existing files:

- `app/config.py` — add three new env-var-backed settings
- `app/services/postings_finder.py` — add `get_app_token()`, thread token into requests

---

## Changes

### `app/config.py`

Add three settings to the `Settings` class, following the existing `os.getenv` pattern:

```python
HH_CLIENT_ID: str = os.getenv("HH_CLIENT_ID", "")
HH_CLIENT_SECRET: str = os.getenv("HH_CLIENT_SECRET", "")
HH_USER_AGENT: str = os.getenv("HH_USER_AGENT", "WEI-Group-Vacancy-Analysis/0.1 (https://weigroup.ru)")
```

All three have safe defaults consistent with how other keys are declared in `Settings`.

### `app/services/postings_finder.py`

**Add `get_app_token() -> str`**

Posts to `https://api.hh.ru/token` with `grant_type=client_credentials`. Raises `RuntimeError` if the response does not contain `access_token`. Uses `HH_USER_AGENT` for both `User-Agent` and `HH-User-Agent` headers (required by HH API policy).

**Update `find_postings_by_search_term(search_term, token)`**

Add `token: str` parameter. All `requests.get` calls include:

```python
headers={"Authorization": f"Bearer {token}",
         "User-Agent": HH_USER_AGENT,
         "HH-User-Agent": HH_USER_AGENT}
```

Existing pagination, rate-limit retry, and error-handling logic is untouched.

**Update `find_all_postings_for_company(known_names)`**

Public signature unchanged: `(known_names: list[str]) -> list[dict]`.

Token is fetched once at the top of the function and passed to every `find_postings_by_search_term` call:

```python
def find_all_postings_for_company(known_names: list[str]) -> list[dict]:
    token = get_app_token()
    ...
    raw_items = find_postings_by_search_term(name, token=token)
```

---

## Credentials

| Variable | Location | Notes |
|---|---|---|
| `HH_CLIENT_ID` | `.env` | Required |
| `HH_CLIENT_SECRET` | `.env` | Required |
| `HH_USER_AGENT` | `.env` (optional) | Defaults to `WEI-Group-Vacancy-Analysis/0.1` |

`.env.example` updated with placeholder values. `.env` already populated with production credentials from `hh_vacancies_simple_export_ed02.py`.

---

## What does NOT change

- `extract_posting_fields` — field set unchanged
- `find_all_postings_for_company` public signature — unchanged
- Pagination logic, rate-limit retries, error handling — unchanged
- DB schema, keyword scanner, session processor — no changes needed
