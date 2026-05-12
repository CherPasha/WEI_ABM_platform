import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    HUNTER_API_KEY: str = os.getenv("HUNTER_API_KEY", "")
    YANDEX_SEARCH_API_KEY: str = os.getenv("YANDEX_SEARCH_API_KEY", "")
    YANDEX_SEARCH_API_KEY_ID: str = os.getenv("YANDEX_SEARCH_API_KEY_ID", "")


settings = Settings()
