import json
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    HUNTER_API_KEY: str = os.getenv("HUNTER_API_KEY", "")
    YANDEX_SEARCH_API_KEY: str = os.getenv("YANDEX_SEARCH_API_KEY", "")
    # NOTE: despite the name, this must contain the Yandex Cloud folderId, not the API key id.
    YANDEX_SEARCH_API_KEY_ID: str = os.getenv("YANDEX_SEARCH_API_KEY_ID", "")
    YANDEX_FOLDER_ID: str = os.getenv("YANDEX_FOLDER_ID", "")  # clearer alias for the same value

    # News stage: Playwright scraping
    PLAYWRIGHT_PROXIES: str = os.getenv("PLAYWRIGHT_PROXIES", "")
    PLAYWRIGHT_TIMEOUT_MS: int = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "20000"))
    PLAYWRIGHT_CONCURRENCY: int = int(os.getenv("PLAYWRIGHT_CONCURRENCY", "4"))

    # ── HH.ru API (поиск вакансий) ──
    HH_CLIENT_ID: str = os.getenv("HH_CLIENT_ID", "")
    HH_CLIENT_SECRET: str = os.getenv("HH_CLIENT_SECRET", "")
    HH_USER_AGENT: str = os.getenv("HH_USER_AGENT", "WEI-Group-Vacancy-Analysis/0.1 (https://weigroup.ru)")

    @property
    def yandex_folder_id(self) -> str:
        """Yandex Cloud folderId. Prefers YANDEX_FOLDER_ID, falls back to the
        legacy YANDEX_SEARCH_API_KEY_ID which in practice already holds it."""
        return self.YANDEX_FOLDER_ID or self.YANDEX_SEARCH_API_KEY_ID

    @property
    def has_yandex(self) -> bool:
        return bool(self.YANDEX_SEARCH_API_KEY and self.yandex_folder_id)

    @property
    def proxy_list(self) -> list[str]:
        """Parse PLAYWRIGHT_PROXIES: JSON array or comma-separated string."""
        raw = self.PLAYWRIGHT_PROXIES.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                return [p.strip() for p in json.loads(raw) if p and p.strip()]
            except json.JSONDecodeError:
                return []
        return [p.strip() for p in raw.split(",") if p.strip()]


settings = Settings()
