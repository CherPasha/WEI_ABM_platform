"""E2E harness for the news stage (Yandex Search API + Playwright).

Usage:
    python -m scripts.news_e2e "Тинькофф" "Сбер" "Yandex"
    python -m scripts.news_e2e --check-db "Тинькофф"

For each company it reports:
  * Yandex search results count;
  * unique URLs after dedup;
  * Playwright pages scraped OK;
  * skipped pages with reasons (timeout / blocked / no_content / bad_proxy / ...).

With --check-db it additionally round-trips ONE synthetic row through Supabase
(insert -> select -> delete) to prove news_articles is writable via the same
client session_processor uses. The synthetic row is always deleted afterwards.

Requires real env: YANDEX_SEARCH_API_KEY + YANDEX_FOLDER_ID
(or legacy YANDEX_SEARCH_API_KEY_ID). --check-db also needs SUPABASE_URL/KEY.
"""
import sys
from collections import Counter

from app.config import settings
from app.services import yandex_search as ys


def report_company(name: str) -> None:
    raw = ys.search_yandex(name)
    results = [ys.extract_search_result(item, name) for item in raw]
    unique = ys._dedupe_by_url(results)
    urls = [r["url"] for r in unique]

    scraped, stats = ys._run_async(ys._scrape_all(urls)) or ({}, Counter())
    ok = stats.get("ok", 0)
    no_content = stats.get("no_content", 0)
    skipped = {k: v for k, v in stats.items() if k not in ("ok", "no_content")}

    print(f"\n=== {name} ===")
    print(f"  Yandex results        : {len(results)}")
    print(f"  Unique after dedup    : {len(unique)}")
    print(f"  Scraped with content  : {ok}")
    print(f"  Opened but no content : {no_content}")
    print(f"  Skipped (failed)      : {sum(skipped.values())}")
    for reason, count in sorted(skipped.items()):
        print(f"      - {reason}: {count}")


def check_db() -> None:
    """Insert/select/delete one synthetic news_articles row to verify writes."""
    from app.database import supabase

    marker = "e2e-news-check"
    row = {
        "article_url": "https://example.com/e2e-check",
        "search_term": marker,
        "title": "E2E check",
        "source_name": "example.com",
        "snippet": "synthetic row",
        "published_at": None,
        "raw_data": '{"e2e": true}',  # session_processor json.dumps()-es this field
    }
    inserted = supabase.table("news_articles").insert(row).execute()
    new_id = inserted.data[0]["id"]
    print(f"\n[DB] inserted news_articles id={new_id}")

    fetched = supabase.table("news_articles").select("*").eq("id", new_id).execute()
    print(f"[DB] read back rows: {len(fetched.data)}")

    supabase.table("news_articles").delete().eq("id", new_id).execute()
    print("[DB] cleaned up synthetic row -> OK")


def main(argv: list[str]) -> int:
    do_db = "--check-db" in argv
    names = [a for a in argv if not a.startswith("--")]

    if not settings.has_yandex:
        print("ERROR: set YANDEX_SEARCH_API_KEY and YANDEX_FOLDER_ID "
              "(or YANDEX_SEARCH_API_KEY_ID).")
        return 1
    if not names:
        print("Pass 1-3 company names, e.g.: python -m scripts.news_e2e 'Тинькофф'")
        return 1

    for name in names[:3]:
        report_company(name)

    if do_db:
        check_db()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
