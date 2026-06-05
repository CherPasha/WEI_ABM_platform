# News stage — Yandex Search API + Playwright

The news stage (`app/services/yandex_search.py`) searches the open web for each
resolved company name via the **Yandex Search API**, deduplicates the result
URLs, opens the unique pages with **Playwright (Chromium)**, and returns rows
ready for the `news_articles` table.

The stage is best-effort: external pages may be down, block automation, return
a captcha/403, change layout, or omit a publication date. Such cases are logged
as warnings and skipped — they never break the pipeline.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `YANDEX_SEARCH_API_KEY` | yes | Yandex Cloud API key, sent as `Authorization: Api-Key <key>`. |
| `YANDEX_SEARCH_API_KEY_ID` | yes* | **Must contain the Yandex Cloud `folderId`**, not the API key id. The name is legacy — the value put here is sent as `folderId`. |
| `YANDEX_FOLDER_ID` | yes* | Clearer alias for the folderId. If set, it **takes priority** over `YANDEX_SEARCH_API_KEY_ID`. Use this for new setups. |
| `PLAYWRIGHT_PROXIES` | no | Proxy list (see below). Empty = direct connection. |
| `PLAYWRIGHT_TIMEOUT_MS` | no | Per-page navigation timeout (default `20000`). |
| `PLAYWRIGHT_CONCURRENCY` | no | Max pages opened in parallel (default `4`). |

> \* The folderId can come from **either** `YANDEX_FOLDER_ID` (preferred) **or**
> the legacy `YANDEX_SEARCH_API_KEY_ID` — at least one must be set.
>
> ⚠️ **`YANDEX_SEARCH_API_KEY_ID` фактически должен содержать `folderId` Yandex
> Cloud** (идентификатор каталога), а не ID API-ключа. Чтобы избежать путаницы,
> в новых конфигурациях задавайте `YANDEX_FOLDER_ID`.
>
> The news stage is automatically skipped (warning logged, empty result) if the
> API key or the folderId is missing.

## Getting the Yandex Search API credentials

1. Create / open a folder in the [Yandex Cloud console](https://console.yandex.cloud).
2. Copy the **folder ID** → `YANDEX_SEARCH_API_KEY_ID`.
3. Create a service account, grant it the `search-api.executor` role.
4. Create an **API key** for that service account → `YANDEX_SEARCH_API_KEY`.

The stage uses the synchronous endpoint
`POST https://searchapi.api.cloud.yandex.net/v2/web/search` with
`responseFormat=FORMAT_XML`; the XML payload is base64-decoded and parsed.

## Billing / API call volume

Yandex Search API returns about **10 documents per page**. To collect the
top-50 results the stage paginates, so **one company at top-50 can cost up to 5
search API requests**, depending on Yandex Search API pagination (fewer if the
query returns less than 50 results). Multiply by the number of `known_names`
per company (usually the legal name + the resolved real name → up to 2), i.e.
**up to ~10 search requests per company** in the worst case.

Plan your Yandex Cloud quota/budget accordingly. Pages are scraped with
Playwright and do **not** consume Yandex API quota.

## Proxies

`PLAYWRIGHT_PROXIES` accepts either a comma-separated string or a JSON array.
Proxies are rotated round-robin across the scraped pages. Supported forms:

```
http://user:pass@host:port
socks5://user:pass@host:port
http://host:port
```

Examples:

```env
PLAYWRIGHT_PROXIES=http://user:pass@10.0.0.1:8080,http://10.0.0.2:8080
PLAYWRIGHT_PROXIES=["socks5://user:pass@host:1080","http://host:3128"]
```

If a proxy is bad or a page fails to open, the error is logged and the next
page continues.

## Installing Playwright

The Python package is already in `requirements.txt`. The Chromium browser
binary must be installed once per environment:

```bash
pip install -r requirements.txt
playwright install chromium
```

## Limitations / what is NOT guaranteed

- 100% successful scraping of every found page is **not** guaranteed.
- Pages with captcha / 403 / timeouts / non-standard markup are skipped.
- Freshness filter is best-effort: a page is dropped only when a date is found
  **and** it is clearly older than 5 years. Pages without a date are kept
  (they may be company sites or relevant pages).
- Heavy resources (images/fonts/media) are blocked to save proxy traffic.

## Testing

Without real credentials you can still verify the offline logic:

```python
from app.services import yandex_search as ys

# XML parsing + mapping
docs = ys._parse_docs(open("tests/fixtures/yandex.xml").read())
print(ys.extract_search_result(docs[0], "Acme"))

# URL normalization / dedup
print(ys._normalize_url("https://Example.com/News/?utm_source=x&id=1"))
```

End-to-end (needs `YANDEX_SEARCH_API_KEY*` set and `playwright install chromium`):

```python
from app.services.yandex_search import find_all_news_for_company
rows = find_all_news_for_company(["Тинькофф", "Yandex"])
for r in rows[:5]:
    print(r["article_url"], r["title"])
```

Each returned row matches the `news_articles` schema:
`article_url, search_term, title, source_name, snippet, published_at, raw_data`.
