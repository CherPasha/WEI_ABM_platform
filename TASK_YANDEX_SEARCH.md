# Задача: Реализация сервиса поиска новостей через Yandex Search API + Playwright

## Описание приложения

**ABM Platform** — бэкенд на FastAPI (Python), который обрабатывает список компаний из загруженного Excel-файла через pipeline обогащения данных. Данные хранятся в Supabase (Postgres). Фронтенд — простые HTML-шаблоны с vanilla JS, без фреймворков.

### Технологический стек

| Компонент | Описание |
|-----------|---------|
| **FastAPI** | REST API — `app/main.py` |
| **Supabase** | База данных через `supabase-py` — `app/database.py` |
| **Gemini 2.5 Flash** | ИИ для разрешения названий компаний и обогащения контактов |
| **hh.ru API** | Поиск вакансий — `app/services/postings_finder.py` |
| **Yandex Search API** | Поиск новостей (новая реализация) — `app/services/yandex_search.py` |
| **Hunter.io API** | Поиск контактов — `app/services/hunter_service.py` |
| **Config** | Переменные окружения через `app/config.py` → класс `Settings` |

---

## Pipeline обработки

При загрузке файла создаётся `session`, после чего фоновая задача последовательно выполняет следующие этапы (`app/services/session_processor.py`):

```
1. Parse         → читаем компании из xlsx/csv                   (company_parser.py)
2. Resolve names → Gemini: юридическое имя → бренд              (name_resolver.py)
3. Job postings  → поиск вакансий по названию на hh.ru          (postings_finder.py)
4. News          → Yandex Search API + парсинг страниц          (yandex_search.py)  ← этот этап
5. Contacts      → поиск контактов через Hunter.io              (hunter_service.py)
6. Enrichment    → Gemini: поиск руководителей, генерация email (contact_enrichment.py)
```

Каждый этап обновляет счётчик `sessions.{stage}_done` в Supabase для отслеживания прогресса. Сессии можно возобновить с места остановки.

---

## Паттерн кода

Каждый сервисный файл использует структуру из трёх функций. За образец берите `postings_finder.py`:

```python
def find_{thing}_by_search_term(term: str) -> list[dict]:
    """Raw API call. Handles pagination, retries, rate limiting."""
    ...

def extract_{thing}_fields(item: dict, term: str) -> dict:
    """Maps raw API response fields to DB column names."""
    ...

def find_all_{things}_for_company(known_names: list[str]) -> list[dict]:
    """Iterates over all known name variants, deduplicates results."""
    ...
```

---

## Описание задачи

### Цель

Реализовать двухэтапный сервис поиска новостей для компании:

1. **Этап 1 — Yandex Search API:** по названию компании получить список URL статей из поисковой выдачи Яндекса
2. **Этап 2 — Playwright + прокси:** открыть каждую страницу через Playwright и спарсить содержимое (заголовок, текст, дата публикации, источник)

### Файл для создания

```
app/services/yandex_search.py
```

### Архитектура сервиса

Сервис состоит из двух логических блоков:

**Блок 1 — Yandex Search API (получение ссылок)**

```python
def search_yandex(query: str) -> list[dict]:
    """
    Вызывает Yandex Search API с поисковым запросом.
    Возвращает список сырых результатов поиска — каждый содержит url, title, snippet.
    Обрабатывает rate limiting (429) с экспоненциальным backoff.
    """
    ...

def extract_search_result(item: dict, search_term: str) -> dict:
    """
    Маппит поле ответа Yandex Search API в промежуточный dict:
    { "url", "title", "snippet", "search_term" }
    """
    ...
```

**Блок 2 — Playwright парсер страниц (получение содержимого)**

```python
async def scrape_article(url: str, proxy: str) -> dict:
    """
    Открывает страницу через Playwright с указанным прокси.
    Возвращает: { "title", "source_name", "snippet", "published_at", "raw_html" (опционально) }
    При ошибке возвращает пустой dict.
    """
    ...
```

**Оркестратор**

```python
def find_all_news_for_company(known_names: list[str]) -> list[dict]:
    """
    1. Для каждого known_name вызывает search_yandex()
    2. Дедуплицирует результаты по URL
    3. Для каждого уникального URL вызывает scrape_article()
    4. Возвращает финальный список статей в формате БД
    """
    ...
```

### Ожидаемый формат вывода

Финальный список, который `find_all_news_for_company` возвращает в `session_processor.py`:

```python
{
    "article_url":  str,   # URL статьи из поиска Яндекса
    "search_term":  str,   # название компании, по которому нашли статью
    "title":        str,   # заголовок статьи (из парсинга страницы)
    "source_name":  str,   # домен или название издания
    "snippet":      str,   # краткий текст / лид статьи
    "published_at": str,   # дата публикации (если удалось найти на странице)
    "raw_data":     dict,  # сырые данные из Yandex Search API (для отладки)
}
```

### Учётные данные

Оба ключа уже доступны через `app/config.py`:

```python
from app.config import settings

settings.YANDEX_SEARCH_API_KEY       # API key string
settings.YANDEX_SEARCH_API_KEY_ID    # Folder / key ID
```

Параметры прокси уточните у команды — они будут переданы в Playwright при открытии страниц.

### Требования

- Обрабатывать **rate limiting (429)** Yandex Search API с повторными попытками и экспоненциальным backoff (см. `postings_finder.py` как образец)
- **Дедуплицировать** URL до запуска Playwright — не открывать одну страницу дважды
- Парсинг страниц через **Playwright** с **прокси** — каждая страница открывается в отдельном browser context с ротацией прокси
- Если страница недоступна или парсинг упал — **пропускать** эту статью, логировать предупреждение, не бросать исключение
- Использовать `logging.getLogger(__name__)` для логирования — никаких вызовов `print()`
- Для HTTP-запросов к Yandex API использовать `requests`; для парсинга страниц — `playwright`

### Точка интеграции

`session_processor.py` вызывает `find_all_news_for_company(known_names)` на этапе новостей (около строки 172). После готовности нового сервиса:

- Обновить импорт в `session_processor.py`: заменить `from app.services.news_finder import find_all_news_for_company` на `from app.services.yandex_search import find_all_news_for_company`
- Старый `news_finder.py` можно удалить или оставить как архив

Перед подключением согласуйте с командой список прокси и формат их передачи в сервис.
