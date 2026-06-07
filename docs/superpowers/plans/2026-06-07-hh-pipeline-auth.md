# HH Pipeline OAuth Authentication — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace unauthenticated HH API requests with OAuth `client_credentials` flow so the pipeline works with the updated API.

**Architecture:** Add three settings to the `Settings` class in `app/config.py`. Add `get_app_token()` to `postings_finder.py`. Thread the token through the internal `find_postings_by_search_term` function. The public signature of `find_all_postings_for_company` is unchanged.

**Tech Stack:** Python 3.11, `requests`, `unittest.mock` for tests, `pytest`

---

## File Map

| File | Change |
|---|---|
| `app/config.py` | Add `HH_CLIENT_ID`, `HH_CLIENT_SECRET`, `HH_USER_AGENT` to `Settings` class |
| `app/services/postings_finder.py` | Add `get_app_token()`, add `token` param to `find_postings_by_search_term`, update `find_all_postings_for_company` |
| `tests/test_postings_finder.py` | New file — all tests for the above |

---

## Task 1: Add HH settings to `app/config.py`

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_postings_finder.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_postings_finder.py`:

```python
import app.config


def test_settings_has_hh_fields():
    assert hasattr(app.config.Settings, "HH_CLIENT_ID")
    assert hasattr(app.config.Settings, "HH_CLIENT_SECRET")
    assert hasattr(app.config.Settings, "HH_USER_AGENT")
    assert "WEI-Group-Vacancy-Analysis" in app.config.Settings.HH_USER_AGENT
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_postings_finder.py::test_settings_has_hh_fields -v
```

Expected: `FAILED — AttributeError: type object 'Settings' has no attribute 'HH_CLIENT_ID'`

- [ ] **Step 3: Add the three settings to `Settings` in `app/config.py`**

Insert after the `PLAYWRIGHT_CONCURRENCY` line (before the `@property` block):

```python
    # ── HH.ru API (поиск вакансий) ──
    HH_CLIENT_ID: str = os.getenv("HH_CLIENT_ID", "")
    HH_CLIENT_SECRET: str = os.getenv("HH_CLIENT_SECRET", "")
    HH_USER_AGENT: str = os.getenv("HH_USER_AGENT", "WEI-Group-Vacancy-Analysis/0.1 (https://weigroup.ru)")
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_postings_finder.py::test_settings_has_hh_fields -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_postings_finder.py
git commit -m "feat: add HH OAuth credentials to Settings"
```

---

## Task 2: Add `get_app_token()` to `postings_finder.py`

**Files:**
- Modify: `app/services/postings_finder.py`
- Test: `tests/test_postings_finder.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_postings_finder.py`:

```python
import pytest
from unittest.mock import patch, MagicMock


def test_get_app_token_returns_token():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"access_token": "test_token_123"}
    mock_resp.raise_for_status.return_value = None

    with patch("app.services.postings_finder.requests.post", return_value=mock_resp) as mock_post:
        from app.services.postings_finder import get_app_token
        token = get_app_token()

    assert token == "test_token_123"
    call_args = mock_post.call_args
    assert call_args[0][0] == "https://api.hh.ru/token"
    assert call_args[1]["data"]["grant_type"] == "client_credentials"


def test_get_app_token_raises_if_no_token_in_response():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {}
    mock_resp.raise_for_status.return_value = None

    with patch("app.services.postings_finder.requests.post", return_value=mock_resp):
        from app.services.postings_finder import get_app_token
        with pytest.raises(RuntimeError, match="access_token"):
            get_app_token()
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_postings_finder.py::test_get_app_token_returns_token tests/test_postings_finder.py::test_get_app_token_raises_if_no_token_in_response -v
```

Expected: `ImportError` or `AttributeError` — `get_app_token` does not exist yet.

- [ ] **Step 3: Add `get_app_token()` to `app/services/postings_finder.py`**

Add this import at the top of the file (after the existing imports):

```python
from app.config import settings
```

Add this function before `find_postings_by_search_term`:

```python
def get_app_token() -> str:
    """Obtain a client_credentials OAuth token from HH.ru."""
    response = requests.post(
        "https://api.hh.ru/token",
        data={
            "grant_type": "client_credentials",
            "client_id": settings.HH_CLIENT_ID,
            "client_secret": settings.HH_CLIENT_SECRET,
        },
        headers={
            "User-Agent": settings.HH_USER_AGENT,
            "HH-User-Agent": settings.HH_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=45,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError(f"HH did not return access_token: {response.json()}")
    return token
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_postings_finder.py::test_get_app_token_returns_token tests/test_postings_finder.py::test_get_app_token_raises_if_no_token_in_response -v
```

Expected: both `PASSED`

- [ ] **Step 5: Commit**

```bash
git add app/services/postings_finder.py tests/test_postings_finder.py
git commit -m "feat: add get_app_token() with client_credentials flow"
```

---

## Task 3: Thread token through `find_postings_by_search_term`

**Files:**
- Modify: `app/services/postings_finder.py`
- Test: `tests/test_postings_finder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_postings_finder.py`:

```python
def test_find_postings_by_search_term_sends_auth_header():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"items": [{"id": "1", "name": "Dev"}], "pages": 1}

    with patch("app.services.postings_finder.requests.get", return_value=mock_resp) as mock_get:
        from app.services.postings_finder import find_postings_by_search_term
        result = find_postings_by_search_term("python developer", token="my_bearer_token")

    assert len(result) == 1
    call_headers = mock_get.call_args[1]["headers"]
    assert call_headers["Authorization"] == "Bearer my_bearer_token"
    assert "HH-User-Agent" in call_headers
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_postings_finder.py::test_find_postings_by_search_term_sends_auth_header -v
```

Expected: `FAILED — TypeError: find_postings_by_search_term() got an unexpected keyword argument 'token'`

- [ ] **Step 3: Update `find_postings_by_search_term` in `app/services/postings_finder.py`**

Change the function signature and add headers to the `requests.get` call. The full updated function:

```python
def find_postings_by_search_term(search_term: str, token: str = "") -> list[dict]:
    """Search hh.ru for job postings matching a search term. Returns raw API items."""
    page_counter = 0
    listings = []
    rate_limit_retries = 3
    headers = {
        "User-Agent": settings.HH_USER_AGENT,
        "HH-User-Agent": settings.HH_USER_AGENT,
        "Authorization": f"Bearer {token}",
    }

    while page_counter < MAX_PAGES:
        params = {
            "text": search_term,
            "per_page": 100,
            "page": page_counter,
        }
        try:
            response = requests.get(BASE_URL, params=params, headers=headers, timeout=30)

            if response.status_code == 200:
                data = response.json()
                items = data.get("items", [])
                if not items:
                    break
                listings.extend(items)
                pages_total = data.get("pages", 1)
                if page_counter >= pages_total - 1:
                    break
                page_counter += 1
                time.sleep(3)

            elif response.status_code in (429, 503):
                if rate_limit_retries > 0:
                    wait = 60 * (4 - rate_limit_retries)
                    logger.warning(
                        "hh.ru rate limited (status %d) for '%s'. Waiting %ds (%d retries left).",
                        response.status_code, search_term, wait, rate_limit_retries,
                    )
                    time.sleep(wait)
                    rate_limit_retries -= 1
                else:
                    logger.warning("hh.ru rate limit retries exhausted for '%s'. Stopping.", search_term)
                    break

            else:
                logger.warning(
                    "hh.ru returned status %d for term '%s'. Stopping.",
                    response.status_code, search_term,
                )
                break

        except Exception as e:
            logger.error("Error fetching postings for '%s': %s", search_term, e)
            break

    return listings
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_postings_finder.py::test_find_postings_by_search_term_sends_auth_header -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add app/services/postings_finder.py tests/test_postings_finder.py
git commit -m "feat: add Bearer token header to HH vacancy search requests"
```

---

## Task 4: Fetch token once in `find_all_postings_for_company`

**Files:**
- Modify: `app/services/postings_finder.py`
- Test: `tests/test_postings_finder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_postings_finder.py`:

```python
def test_find_all_postings_for_company_fetches_token_once_and_passes_it():
    with patch("app.services.postings_finder.get_app_token", return_value="shared_tok") as mock_token, \
         patch("app.services.postings_finder.find_postings_by_search_term", return_value=[]) as mock_find:
        from app.services.postings_finder import find_all_postings_for_company
        find_all_postings_for_company(["Acme Ltd", "Acme"])

    mock_token.assert_called_once()
    assert mock_find.call_count == 2
    for call in mock_find.call_args_list:
        assert call[1].get("token") == "shared_tok"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_postings_finder.py::test_find_all_postings_for_company_fetches_token_once_and_passes_it -v
```

Expected: `FAILED — AssertionError: mock_token.assert_called_once() — get_app_token was never called`

- [ ] **Step 3: Update `find_all_postings_for_company` in `app/services/postings_finder.py`**

Replace the existing function with:

```python
def find_all_postings_for_company(known_names: list[str]) -> list[dict]:
    """Search hh.ru for all known names of a company, deduplicate by hh_id."""
    token = get_app_token()
    seen_ids = set()
    all_postings = []

    for name in known_names:
        if not name or name == "Название не доступно":
            continue
        raw_items = find_postings_by_search_term(name, token=token)
        for item in raw_items:
            hh_id = str(item.get("id", ""))
            if hh_id not in seen_ids:
                seen_ids.add(hh_id)
                all_postings.append(extract_posting_fields(item, name))

    return all_postings
```

- [ ] **Step 4: Run the full test suite to verify everything passes**

```
pytest tests/test_postings_finder.py -v
```

Expected: all 6 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add app/services/postings_finder.py tests/test_postings_finder.py
git commit -m "feat: fetch OAuth token once per company lookup in HH pipeline"
```
