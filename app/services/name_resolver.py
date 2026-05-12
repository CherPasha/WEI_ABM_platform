import time
import logging
import httpx
from google import genai
from app.config import settings

logger = logging.getLogger(__name__)

COMPANY_PROMPT = (
    "Ты опытный аналитик российского рынка. "
    "Я дам тебе юридическое название фирмы. "
    "Ты должен найти название компании, связанной с этим юридическим лицом. "
    "Ответ давай без форматирования и лишних комментариев, только название компании."
)

MODEL = "models/gemini-2.5-flash"
NAME_UNAVAILABLE = "Название не доступно"


def _get_client() -> genai.Client:
    return genai.Client(api_key=settings.GEMINI_API_KEY)


def find_company_real_name(client: genai.Client, company_name: str) -> str:
    """Use Gemini to resolve a legal entity name to a real company name.

    Retries up to 5 times on API errors with exponential backoff.
    Returns NAME_UNAVAILABLE if all retries fail, so the caller falls back to the legal name.
    """
    max_retries = 5
    retry_delay = 15  # seconds; doubles each attempt

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=COMPANY_PROMPT + company_name,
            )
            result = response.text.strip()
            if len(result) > 30:
                return NAME_UNAVAILABLE
            return result

        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
            wait = 5 * (attempt + 1)
            logger.warning(
                "Network error for '%s', retrying in %ds (attempt %d/%d): %s",
                company_name, wait, attempt + 1, max_retries, e,
            )
            time.sleep(wait)

        except Exception as e:
            error_msg = str(e).lower()

            if "safety" in error_msg or "blocked" in error_msg:
                logger.warning("Content blocked by safety filters for: %s", company_name)
                return NAME_UNAVAILABLE

            is_quota = "resource" in error_msg and "exhausted" in error_msg or "429" in error_msg
            wait = retry_delay * (2 ** attempt)
            logger.warning(
                "%s for '%s'. Waiting %ds before retry (attempt %d/%d).",
                "Quota exceeded" if is_quota else "API error",
                company_name, wait, attempt + 1, max_retries,
            )
            time.sleep(wait)

    logger.error("All %d retries exhausted for '%s'. Falling back to legal name.", max_retries, company_name)
    return NAME_UNAVAILABLE
