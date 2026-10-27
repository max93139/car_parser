"""
Google Gemini 2.0 Flash AI Car Evaluation Service.
Provides specialized VAG / Audi automotive analysis for vehicle listings.
"""

from __future__ import annotations

import html
import logging
import os
import re
from typing import Any, Dict, Optional, Union
import httpx

from src.config import get_settings
from src.database.models import ListingModel
from src.models.listing import Listing

logger = logging.getLogger(__name__)

GEMINI_API_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
)
DEFAULT_MODEL_NAME = "gemini-2.0-flash"


def sanitize_markdown_to_telegram_html(text: str) -> str:
    """
    Converts standard Markdown produced by Gemini into Telegram-supported HTML tags.
    Telegram HTML only supports: <b>, <strong>, <i>, <em>, <u>, <ins>, <s>, <strike>,
    <del>, <span class="tg-spoiler">, <tg-spoiler>, <a>, <code>, <pre>, <blockquote>.
    """
    if not text:
        return ""

    # Remove markdown code block wrappers if model wrapped entire response
    text = re.sub(r"^```(?:html)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)

    # 1. Temporarily protect already valid HTML tags
    valid_tags_pattern = r"(</?(?:b|i|u|s|code|pre|a|blockquote)(?:\s+[^>]*)?>)"
    tokens = re.split(valid_tags_pattern, text, flags=re.IGNORECASE)

    processed_tokens = []
    for idx, token in enumerate(tokens):
        # Odd indexes are the matched valid HTML tags
        if idx % 2 == 1:
            processed_tokens.append(token)
            continue

        # For non-tag text, first escape raw HTML special characters
        escaped = html.escape(token, quote=False)

        # Convert markdown headers: ### Header or ## Header -> <b>Header</b>
        escaped = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", escaped, flags=re.MULTILINE)

        # Convert bold markdown: **text** or __text__ -> <b>text</b>
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped, flags=re.DOTALL)
        escaped = re.sub(r"__(.+?)__", r"<b>\1</b>", escaped, flags=re.DOTALL)

        # Convert italic markdown: *text* or _text_ -> <i>text</i>
        escaped = re.sub(r"(?<!\w)\*([^\*\n]+?)\*(?!\w)", r"<i>\1</i>", escaped)
        escaped = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<i>\1</i>", escaped)

        # Convert bullet points `* ` or `- ` at line start to emoji bullet
        escaped = re.sub(r"^[\*\-]\s+", "• ", escaped, flags=re.MULTILINE)

        processed_tokens.append(escaped)

    result = "".join(processed_tokens)

    # Collapse excessive blank lines
    result = re.sub(r"\n{3,}", "\n\n", result).strip()

    # Telegram hard limit guard (4096 chars)
    suffix = "...\n<i>[Скорочено через ліміт Telegram]</i>"
    if len(result) > 4000:
        result = result[: 4000 - len(suffix)] + suffix

    return result


def build_gemini_car_prompt(listing: Union[Listing, ListingModel, Dict[str, Any]]) -> str:
    """
    Constructs an expert VAG/Audi technical assessment prompt for Gemini 2.0 Flash.
    """
    def _get(attr: str, default: Any = None) -> Any:
        if isinstance(listing, dict):
            return listing.get(attr, default)
        return getattr(listing, attr, default) or default

    brand = _get("brand", "Audi")
    model = _get("model", "A6")
    generation = _get("generation", "C5")
    year = _get("year", "не вказано")
    price_val = _get("price")
    price_usd = _get("price_usd")
    currency = _get("currency", "USD")

    price_str = "не вказано"
    if price_usd:
        price_str = f"${float(price_usd):,}"
        if currency != "USD" and price_val:
            price_str += f" ({float(price_val):,} {currency})"
    elif price_val:
        price_str = f"{float(price_val):,} {currency}"

    mileage = _get("mileage")
    mileage_str = f"{int(mileage):,} км" if mileage else "не вказано"

    engine = _get("engine") or _get("engine_code") or "не вказано"
    fuel_type = _get("fuel_type", "інше")
    transmission = _get("transmission", "не вказано")
    drive_type = _get("drive_type", "не вказано")
    body_type = _get("body_type", "не вказано")
    location = _get("location") or _get("location_city") or "Україна"
    seller = _get("seller", "не вказано")
    description = (_get("description") or "").strip() or "Опис відсутній."

    prompt = f"""Ти — висококваліфікований український автопідбирач та технічний експерт по автомобілях VAG (Audi, Volkswagen) кінця 1990-х та 2000-х років (особливо Audi A6 C5, A4 B6/B7, A6 C6). Твій стаж — понад 15 років.

Зроби чесний, об'єктивний та глибокий експертний аналіз оголошення для потенційного покупця в Україні.

ДАНІ АВТОМОБІЛЯ З ОГОЛОШЕННЯ:
- Марка та модель: {brand} {model} {generation} ({year} р.в.)
- Кузов: {body_type}
- Заявлена ціна: {price_str}
- Заявлений пробіг: {mileage_str}
- Двигун: {engine} (тип пального: {fuel_type})
- Коробка передач: {transmission}
- Привід: {drive_type}
- Локація: {location}
- Продавець: {seller}
- Опис від продавця:
"{description}"

СТРУКТУРА ТВОЄЇ ВІДПОВІДІ (строго дотримуйся цих 5 розділів):

💰 <b>Оцінка ціни:</b>
(Проаналізуй співвідношення ціни до року, пробігу та комплектації. Чи це ринкова ціна, завищена, чи підозріло занижена ціна з ризиком «трупа»).

⚙️ <b>Технічні нюанси двигуна та КПП:</b>
(Опиши характерні вразливі місця конкретно цієї модифікації двигуна і коробки: наприклад, для 1.8T — тиск масла/маслоприймач, турбіна, ВКГ; для 2.4 V6 — підтікання з-під клапанних кришок/фазорегуляторів, жор масла; для 1.9 TDI — насос-форсунки, тандемний насос, маховик; для Multitronic — перегрів ЕБУ та знос конусів варіатора; для Quattro — надійність торсена; для підвіски — алюмінієва багатоважілька).

🕵️‍♂️ <b>Аналіз опису продавця:</b>
(Розбір тексту продавця: чи є маркери автобізнесмена/перекупа, кліше типу «сів і поїхав», «мотор шепче», спроби приховати дефекти, замовчування важливих ремонтів або ДТП).

🚦 <b>Підсумковий вердикт:</b>
(Обери ТІЛЬКИ ОДИН із статусів:
🟢 <b>Варто дивитися</b> — адекватна ціна та збалансований варіант.
🟡 <b>Потребує детальної діагностики</b> — є спірні моменти чи ризикові вузли.
🔴 <b>Ризикований варіант</b> — висока ймовірність значних фінансових вкладень або махінацій.
Коротко аргументуй у 1-2 реченнях).

🛠 <b>Чек-лист для огляду на СТО:</b>
(3-4 конкретні технічні точки перевірки саме для цієї комбінації авто: наприклад, комп'ютерна діагностика груп VCDS/Вася, замір тиску масла механічним манометром, огляд турбіни, стан кузова/покриття тощо).

ВАЖЛИВІ ПРАВИЛА:
1. Пиши виключно українською мовою.
2. Не використовуй Markdown заголовки (#) чи жирний текст (**). Використовуй тільки базові HTML-теги: <b>, <i>, <code>.
3. Будь конкретним і пиши без зайвої «води». Форматуй списки акуратно з крапками •.
"""
    return prompt.strip()


class GeminiCarAnalyzer:
    """
    Asynchronous client for Google Gemini 2.0 Flash AI car evaluation.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        model: str = DEFAULT_MODEL_NAME,
        timeout: float = 30.0,
    ) -> None:
        settings = get_settings()
        self.api_key = (
            api_key
            or getattr(settings, "GEMINI_API_KEY", None)
            or os.getenv("GEMINI_API_KEY", "")
        )
        self.model = model
        self.timeout = timeout
        self._injected_client = client
        self._client: Optional[httpx.AsyncClient] = client

    async def get_client(self) -> httpx.AsyncClient:
        """Lazy initialization of httpx.AsyncClient with connection pooling."""
        if self._injected_client is not None:
            return self._injected_client

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        """Closes network sockets gracefully."""
        if self._client is not None and not self._client.is_closed:
            if self._client is not self._injected_client:
                await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> GeminiCarAnalyzer:
        await self.get_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def analyze_listing(
        self,
        listing: Union[Listing, ListingModel, Dict[str, Any]],
    ) -> str:
        """
        Runs comprehensive AI evaluation on a vehicle listing.
        Returns user-ready HTML formatted message for Telegram.
        """
        clean_key = (self.api_key or "").strip()
        if not clean_key:
            return (
                "⚠️ <b>ШІ-аналіз недоступний: не налаштовано GEMINI_API_KEY</b>\n\n"
                "Для увімкнення експертного аналізу <b>Google Gemini 2.0 Flash</b>:\n"
                "1. Отримайте безкоштовний API-ключ на <a href=\"https://aistudio.google.com/\">Google AI Studio</a>.\n"
                "2. Додайте у файл <code>.env</code> рядок:\n"
                "<code>GEMINI_API_KEY=AIzaSy...</code>\n"
                "3. Перезапустіть бота."
            )

        prompt = build_gemini_car_prompt(listing)
        return await self._call_gemini_api(prompt, clean_key)

    async def _call_gemini_api(self, prompt: str, api_key: str) -> str:
        """
        Dispatches prompt to Google AI Studio Gemini 2.0 Flash REST endpoint.
        """
        client = await self.get_client()
        url = f"{GEMINI_API_ENDPOINT}?key={api_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.25,
                "maxOutputTokens": 2048,
            },
        }

        try:
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            # 1. Success response
            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return "⚠️ <b>Google Gemini 2.0 Flash не зміг сформувати оцінку для цього авто.</b>"

                parts = candidates[0].get("content", {}).get("parts", [])
                raw_text = "".join(part.get("text", "") for part in parts).strip()
                if not raw_text:
                    return "⚠️ <b>Отримано порожню відповідь від моделі аналізу.</b>"

                formatted_html = sanitize_markdown_to_telegram_html(raw_text)
                return f"🧠 <b>Експертний ШІ-аналіз (Gemini 2.0 Flash):</b>\n\n{formatted_html}"

            # 2. Rate Limit (HTTP 429)
            if response.status_code == 429:
                logger.warning("[ai_analyzer] Gemini API rate limit reached (HTTP 429)")
                return (
                    "⏳ <b>Перевищено ліміт запитів до Gemini API (429 Rate Limit)</b>\n\n"
                    "Google AI Studio тимчасово обмежив частоту запитів для вашого ключа.\n"
                    "Будь ласка, зачекайте 30–60 секунд і натисніть кнопку <b>[ 🧠 Аналіз ШІ ]</b> знову."
                )

            # 3. Authentication & Key Errors (HTTP 400 / 403)
            if response.status_code in (400, 403):
                logger.warning("[ai_analyzer] Gemini API auth error: %s %s", response.status_code, response.text)
                return (
                    "⚠️ <b>Помилка доступу до Google Gemini API</b>\n\n"
                    "Перевірте коректність <code>GEMINI_API_KEY</code> у вашому <code>.env</code> файлі. "
                    "Ключ можна створити в <a href=\"https://aistudio.google.com/\">Google AI Studio</a>."
                )

            # 4. Other non-200 responses
            logger.warning("[ai_analyzer] Gemini API unexpected response %s: %s", response.status_code, response.text)
            return (
                f"❌ <b>Помилка сервісу Gemini API ({response.status_code})</b>\n\n"
                "Сервіс тимчасово недоступний. Спробуйте пізніше."
            )

        except httpx.TimeoutException:
            logger.warning("[ai_analyzer] Gemini API request timed out after %.1fs", self.timeout)
            return (
                "⏱ <b>Таймаут запиту до Google Gemini API</b>\n\n"
                "Сервіс не встиг сформувати відповідь за відведений час. Спробуйте натиснути кнопку ще раз."
            )
        except httpx.RequestError as exc:
            logger.warning("[ai_analyzer] Network error connecting to Gemini API: %s", exc)
            return (
                "📡 <b>Мережева помилка під час запиту до Google Gemini</b>\n\n"
                "Не вдалося з'єднатися із сервером аналізу. Перевірте доступ до інтернету."
            )
        except Exception as exc:
            logger.exception("[ai_analyzer] Unexpected error during AI analysis: %s", exc)
            return "❌ <b>Сталася непередбачена помилка під час аналізу авто.</b>"
