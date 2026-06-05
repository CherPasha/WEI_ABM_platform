"""Minimal LLM client over the OpenAI API.

Mirrors the ``client.models.generate_content(model=, contents=)`` call shape and
returns an object with a ``.text`` attribute. This lets name_resolver and
contact_enrichment stay untouched — they call the client the same way regardless
of the provider behind it.
"""
from openai import OpenAI

from app.config import settings


class _Response:
    def __init__(self, text: str):
        self.text = text


class _Models:
    def __init__(self, client: OpenAI):
        self._client = client

    def generate_content(self, model: str, contents: str) -> _Response:
        # Tolerate Gemini-style "models/<name>" ids — OpenAI wants a bare name.
        model = model.replace("models/", "")
        resp = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": contents}],
        )
        return _Response((resp.choices[0].message.content or "").strip())


class LLMClient:
    """Wraps OpenAI behind a ``.models.generate_content`` API."""

    def __init__(self):
        self._client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.models = _Models(self._client)
