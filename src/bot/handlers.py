"""
Command and callback query handlers for the interactive Telegram Bot.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.keyboards import (
    ALL_ENGINES,
    build_main_reply_keyboard,
    build_settings_keyboard,
    format_filter_summary,
)
from src.bot.price_parser import parse_mileage_input, parse_price_input
from src.database.connection import get_session_factory
from src.database.models import UserFilterModel
from src.database.repository import (
    find_matching_listings,
    get_market_overview,
    get_or_create_user_filter,
    reset_user_filter,
    update_user_filter,
)
from src.notifier.templates import format_listing_caption, format_market_overview

logger = logging.getLogger(__name__)


class BotHandler:
    """
    Handles user commands and interactive inline button callbacks.
    """

    def __init__(self, bot_token: str, client: httpx.AsyncClient) -> None:
        self.bot_token = bot_token
        self.client = client
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.user_states: Dict[str, str] = {}

    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "HTML",
    ) -> Optional[Dict[str, Any]]:
        """Sends a text message via Telegram Bot API with automatic plain-text fallback."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        resp = await self.client.post(f"{self.api_url}/sendMessage", json=payload)
        if resp.status_code == 200:
            return resp.json().get("result")

        # Fallback: if HTML parsing failed, retry as plain text without tags
        if resp.status_code == 400 and parse_mode:
            import re
            plain_text = re.sub(r"<[^>]+>", "", text)
            payload["text"] = plain_text
            payload.pop("parse_mode", None)
            retry_resp = await self.client.post(f"{self.api_url}/sendMessage", json=payload)
            if retry_resp.status_code == 200:
                return retry_resp.json().get("result")

        logger.warning("[bot] sendMessage failed: %s %s", resp.status_code, resp.text)
        return None

    async def edit_message_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "HTML",
    ) -> bool:
        """Edits existing message text and inline keyboard."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        resp = await self.client.post(f"{self.api_url}/editMessageText", json=payload)
        return resp.status_code == 200

    async def answer_callback(
        self,
        callback_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> bool:
        """Acknowledges callback query to stop loading spinner on button."""
        payload: Dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
            payload["show_alert"] = show_alert

        resp = await self.client.post(f"{self.api_url}/answerCallbackQuery", json=payload)
        return resp.status_code == 200

    async def send_photo(
        self,
        chat_id: str,
        photo_url: str,
        caption: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "HTML",
    ) -> bool:
        """Sends a single photo with caption, optional inline markup, and automatic plain-text fallback."""
        try:
            safe_caption = caption[:1024] if len(caption) > 1024 else caption
            payload: Dict[str, Any] = {
                "chat_id": chat_id,
                "photo": photo_url,
                "caption": safe_caption,
                "parse_mode": parse_mode,
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup

            resp = await self.client.post(f"{self.api_url}/sendPhoto", json=payload)
            if resp.status_code == 200:
                return True

            # If HTML parsing failed on Telegram side, retry as plain text
            if resp.status_code == 400 and parse_mode:
                import re
                plain_caption = re.sub(r"<[^>]+>", "", caption)[:1024]
                payload["caption"] = plain_caption
                payload.pop("parse_mode", None)
                retry_resp = await self.client.post(f"{self.api_url}/sendPhoto", json=payload)
                if retry_resp.status_code == 200:
                    return True

            logger.warning("[bot] sendPhoto failed: %s %s (url: %s)", resp.status_code, resp.text, photo_url)
            return False
        except Exception as exc:
            logger.warning("[bot] sendPhoto exception: %s (url: %s)", exc, photo_url)
            return False

    async def handle_start(self, chat_id: str) -> None:
        """Handles /start command."""
        welcome_text = (
            "👋 <b>Вітаю в моніторингу авто Audi!</b>\n\n"
            "Цей бот автоматично знаходить найкращі пропозиції Audi A6 та A4 "
            "по всій Україні (AUTO.RIA, OLX, RST, Telegram-канали та Instagram).\n\n"
            "Використовуйте кнопки постійного меню внизу екрана для швидкого керування ботом!"
        )
        await self.send_message(
            chat_id,
            welcome_text,
            reply_markup=build_main_reply_keyboard(),
        )
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                uf = await get_or_create_user_filter(session, chat_id)
                summary = format_filter_summary(uf)
                kb = build_settings_keyboard(uf)
                await self.send_message(chat_id, summary, reply_markup=kb)

    async def handle_settings(self, chat_id: str) -> None:
        """Handles /settings command or settings button."""
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                uf = await get_or_create_user_filter(session, chat_id)
                summary = format_filter_summary(uf)
                kb = build_settings_keyboard(uf)
                await self.send_message(chat_id, summary, reply_markup=kb)

    async def handle_help(self, chat_id: str) -> None:
        """Handles /help command or help button."""
        help_text = (
            "ℹ️ <b>Як користуватися ботом Audi Monitor:</b>\n\n"
            "🔍 <b>Знайти авто зараз</b> — швидкий пошук останніх оголошень за вашими фільтрами.\n"
            "⚙️ <b>Налаштування фільтрів</b> — налаштувати моделі (A6 C4-C7, A4 B5-B8), мотори, КПП, Quattro, кузов, бюджет і пробіг.\n"
            "📊 <b>Статистика ринку</b> — поточний огляд цін та кількості авто в базі.\n"
            "🚀 <b>Boost пошук</b> (/boost) — прискорений пошук найсвіжіших пропозицій.\n"
            "🔄 <b>Скинути фільтри</b> — скинути параметри пошуку до початкових.\n\n"
            "💡 <i>Ви також можете надіслати в чат бажану ціну (наприклад: <code>3500-5000</code>) або пробіг (<code>до 250 тис</code>)!</i>"
        )
        await self.send_message(
            chat_id,
            help_text,
            reply_markup=build_main_reply_keyboard(),
        )

    async def handle_market_stats(self, chat_id: str) -> None:
        """Handles market statistics display."""
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                stats = await get_market_overview(session)
                text = format_market_overview(stats)
                await self.send_message(
                    chat_id,
                    text,
                    reply_markup=build_main_reply_keyboard(),
                )

    async def handle_reset(self, chat_id: str) -> None:
        """Handles filter reset from reply keyboard or command."""
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                uf = await reset_user_filter(session, chat_id)
                summary = format_filter_summary(uf)
                kb = build_settings_keyboard(uf)
                await self.send_message(
                    chat_id,
                    "🔄 <b>Фільтри скинуто до початкових!</b>",
                    reply_markup=build_main_reply_keyboard(),
                )
                await self.send_message(chat_id, summary, reply_markup=kb)

    async def execute_search(self, chat_id: str, is_boost: bool = False) -> None:
        """Executes search for matching listings according to user filters."""
        limit = 10 if is_boost else 5
        if is_boost:
            await self.send_message(
                chat_id,
                "🚀 <b>Режим BOOST активовано!</b>\nШукаю розширений список найактуальніших пропозицій за вашими критеріями...",
                reply_markup=build_main_reply_keyboard(),
            )

        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                uf = await get_or_create_user_filter(session, chat_id)
                listings = await find_matching_listings(session, uf, limit=limit)
                if not listings:
                    await self.send_message(
                        chat_id,
                        "🔍 <b>За вашими критеріями зараз немає збережених нових оголошень.</b>\n\n"
                        "💡 <i>Спробуйте розширити параметри у налаштуваннях або зачекайте — "
                        "щойно з'явиться відповідне авто, бот миттєво надішле його вам!</i>",
                        reply_markup=build_main_reply_keyboard(),
                    )
                    return

                header_text = (
                    f"🚀 <b>[BOOST] Знайдено {len(listings)} найактуальніших пропозицій за вашими фільтрами:</b>"
                    if is_boost
                    else f"🔍 <b>Знайдено {len(listings)} останніх пропозицій за вашими фільтрами:</b>"
                )
                await self.send_message(
                    chat_id,
                    header_text,
                    reply_markup=build_main_reply_keyboard(),
                )
                for item in listings:
                    try:
                        caption = format_listing_caption(item)
                        images = getattr(item, "images", []) or []
                        if isinstance(images, str):
                            try:
                                import json
                                images = json.loads(images)
                            except Exception:
                                images = [images] if images.startswith("http") else []
                        if not isinstance(images, list):
                            images = []

                        first_photo = next(
                            (img for img in images if isinstance(img, str) and img.startswith("http")),
                            None,
                        )

                        item_url = getattr(item, "url", None)
                        card_markup = None
                        if item_url and isinstance(item_url, str) and item_url.startswith("http"):
                            card_markup = {
                                "inline_keyboard": [
                                    [{"text": "🚗 Відкрити оголошення", "url": item_url}]
                                ]
                            }

                        sent = False
                        if first_photo:
                            sent = await self.send_photo(
                                chat_id,
                                first_photo,
                                caption,
                                reply_markup=card_markup,
                            )
                            if not sent:
                                logger.warning(
                                    "[bot] send_photo failed for listing %s, falling back to sendMessage",
                                    getattr(item, "id", None),
                                )
                        if not sent:
                            await self.send_message(
                                chat_id,
                                caption,
                                reply_markup=card_markup,
                            )
                    except Exception as exc:
                        logger.exception(
                            "[bot] Error rendering/sending listing %s: %s",
                            getattr(item, "id", None),
                            exc,
                        )

    async def handle_callback_query(
        self,
        callback_id: str,
        chat_id: str,
        message_id: int,
        data: str,
    ) -> None:
        """Handles inline keyboard button presses."""
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                uf = await get_or_create_user_filter(session, chat_id)
                alert_text: Optional[str] = None

                # 0. Toggle Model & Generation (e.g. A6 C5, A6 C6, A4 B6)
                if data.startswith("toggle_model:"):
                    mod_raw = data.split(":", 1)[1]
                    mod_map = {
                        "A6C5": "A6 C5",
                        "A6C6": "A6 C6",
                        "A6C7": "A6 C7",
                        "A6C4": "A6 C4",
                        "A4B6": "A4 B6",
                        "A4B7": "A4 B7",
                        "A4B8": "A4 B8",
                        "A4B5": "A4 B5",
                    }
                    target_model = mod_map.get(mod_raw, mod_raw)
                    current_models = list(getattr(uf, "selected_models", None) or ["A6 C5"])

                    if target_model in current_models:
                        if len(current_models) == 1:
                            alert_text = "Має бути обрана щонайменше одна модель!"
                        else:
                            current_models.remove(target_model)
                    else:
                        current_models.append(target_model)

                    if not alert_text:
                        uf.selected_models = current_models

                # 1. Toggle engine
                elif data.startswith("toggle_eng:"):
                    eng_clean = data.split(":", 1)[1]
                    # Map back to standard representation
                    eng_map = {"1.8T": "1.8T", "2.4": "2.4", "1.9TDI": "1.9 TDI"}
                    target_eng = eng_map.get(eng_clean, eng_clean)

                    current_engines = list(uf.engines or ALL_ENGINES)
                    if target_eng in current_engines:
                        if len(current_engines) == 1:
                            alert_text = "Має бути обрано щонайменше один двигун!"
                        else:
                            current_engines.remove(target_eng)
                    else:
                        current_engines.append(target_eng)

                    if not alert_text:
                        uf.engines = current_engines

                # 2. Price prompt & reset
                elif data == "prompt_price":
                    self.user_states[chat_id] = "awaiting_price"
                    await self.answer_callback(callback_id)
                    await self.send_message(
                        chat_id,
                        "✍️ <b>Введіть бажану ціну у повідомленні</b>:\n\n"
                        "Наприклад:\n"
                        "• Діапазон: <code>3500-5500</code> або <code>3500 5500</code>\n"
                        "• Тільки максимальна: <code>до 5000</code> або <code>5000</code>\n"
                        "• Тільки мінімальна: <code>від 3000</code>\n"
                        "• Зняти обмеження: <code>0</code>\n\n"
                        "<i>Просто надішліть повідомлення з ціною сюди в чат 👇</i>",
                    )
                    return

                elif data == "reset_price":
                    uf.min_price = None
                    uf.max_price = None
                    alert_text = "Обмеження за ціною знято!"

                # 4. Transmission
                elif data.startswith("set_trans:"):
                    tr_val = data.split(":", 1)[1]
                    uf.transmission = tr_val

                # 4.1 Drive type
                elif data.startswith("set_drive:"):
                    dr_val = data.split(":", 1)[1]
                    uf.drive_type = dr_val

                # 4.2 Body type
                elif data.startswith("set_body:"):
                    bd_val = data.split(":", 1)[1]
                    uf.body_type = bd_val

                # 5. Mileage prompt & reset
                elif data == "prompt_mileage":
                    self.user_states[chat_id] = "awaiting_mileage"
                    await self.answer_callback(callback_id)
                    await self.send_message(
                        chat_id,
                        "✍️ <b>Введіть максимальний пробіг у повідомленні</b>:\n\n"
                        "Наприклад:\n"
                        "• <code>250 тис</code> або <code>250000</code>\n"
                        "• <code>до 280 000 км</code> або <code>280к</code>\n"
                        "• Зняти обмеження: <code>0</code> або <code>скинути</code>\n\n"
                        "<i>Просто надішліть число сюди в чат 👇</i>",
                    )
                    return

                elif data == "reset_mileage":
                    uf.max_mileage = None
                    alert_text = "Обмеження за пробігом знято!"

                # 6. Year range
                elif data.startswith("set_year:"):
                    yr_type = data.split(":", 1)[1]
                    if yr_type == "dorest":
                        uf.min_year = 1997
                        uf.max_year = 2001
                    elif yr_type == "rest":
                        uf.min_year = 2001
                        uf.max_year = 2005
                    else:
                        uf.min_year = 1997
                        uf.max_year = 2005

                # 7. Condition toggle
                elif data == "toggle_cond":
                    uf.exclude_damaged = not uf.exclude_damaged

                # 8. Reset action
                elif data == "action_reset":
                    uf = await reset_user_filter(session, chat_id)
                    alert_text = "Налаштування скинуто до початкових!"

                # 9. Search action
                elif data == "action_search":
                    await self.answer_callback(callback_id, "Шукаю авто за вашими критеріями...")
                    await self.execute_search(chat_id)
                    return

                await session.flush()
                await self.answer_callback(callback_id, alert_text)

                # Re-render menu
                summary = format_filter_summary(uf)
                kb = build_settings_keyboard(uf)
                await self.edit_message_text(chat_id, message_id, summary, reply_markup=kb)

    async def handle_text_message(self, chat_id: str, text: str) -> None:
        """Handles incoming text messages, commands, and persistent menu buttons."""
        clean_text = text.strip()

        # 0. Navigation and Persistent Menu Commands
        if clean_text in ("/start",):
            await self.handle_start(chat_id)
            return

        if clean_text in ("/help", "ℹ️ Допомога", "ℹ️ Про бота / Допомога", "допомога", "help"):
            await self.handle_help(chat_id)
            return

        if clean_text in ("/settings", "/filters", "/filter", "⚙️ Налаштування фільтрів", "налаштування", "фільтри"):
            await self.handle_settings(chat_id)
            return

        if clean_text in ("/search", "/find", "🔍 Знайти авто зараз", "знайти авто", "пошук"):
            await self.execute_search(chat_id)
            return

        if clean_text in ("/boost", "🚀 Boost пошук", "boost", "🚀 /boost", "/boost пошук", "boost пошук"):
            await self.execute_search(chat_id, is_boost=True)
            return

        if clean_text in ("/stats", "/market", "📊 Статистика ринку", "статистика"):
            await self.handle_market_stats(chat_id)
            return

        if clean_text in ("/reset", "🔄 Скинути фільтри", "скинути фільтри"):
            await self.handle_reset(chat_id)
            return

        state = self.user_states.get(chat_id)

        # 1. Check if user is inputting mileage or text explicitly contains mileage keywords
        is_mileage_context = (
            state == "awaiting_mileage"
            or any(k in clean_text.lower() for k in ["пробіг", "пробег", "км"])
        )

        if is_mileage_context:
            parsed_mil = parse_mileage_input(clean_text)
            if parsed_mil is not None:
                self.user_states.pop(chat_id, None)
                _, mil_val = parsed_mil
                factory = get_session_factory()
                async with factory() as session:
                    async with session.begin():
                        uf = await get_or_create_user_filter(session, chat_id)
                        uf.max_mileage = mil_val
                        await session.flush()

                        summary = format_filter_summary(uf)
                        kb = build_settings_keyboard(uf)

                        msg = (
                            f"✅ <b>Встановлено макс. пробіг: до {mil_val:,} км!</b>"
                            if mil_val
                            else "✅ <b>Обмеження за пробігом знято!</b>"
                        )
                        await self.send_message(
                            chat_id,
                            f"{msg}\n\n{summary}",
                            reply_markup=kb,
                        )
                return

        # 2. Try parsing price
        parsed_price = parse_price_input(clean_text)
        if parsed_price is not None:
            self.user_states.pop(chat_id, None)
            min_p, max_p = parsed_price
            factory = get_session_factory()
            async with factory() as session:
                async with session.begin():
                    uf = await get_or_create_user_filter(session, chat_id)
                    uf.min_price = min_p
                    uf.max_price = max_p
                    await session.flush()

                    summary = format_filter_summary(uf)
                    kb = build_settings_keyboard(uf)

                    if min_p is None and max_p is None:
                        msg = "✅ <b>Обмеження за ціною знято!</b>"
                    elif min_p is not None and max_p is not None:
                        msg = f"✅ <b>Встановлено ціну: від ${min_p:,} до ${max_p:,}!</b>"
                    elif max_p is not None:
                        msg = f"✅ <b>Встановлено макс. ціну: до ${max_p:,}!</b>"
                    else:
                        msg = f"✅ <b>Встановлено мін. ціну: від ${min_p:,}!</b>"

                    await self.send_message(
                        chat_id,
                        f"{msg}\n\n{summary}",
                        reply_markup=kb,
                    )
            return

        # 3. Fallback: check if large number represents mileage (e.g. >= 50,000)
        parsed_mil = parse_mileage_input(clean_text)
        if parsed_mil is not None and parsed_mil[1] and parsed_mil[1] >= 50000:
            self.user_states.pop(chat_id, None)
            _, mil_val = parsed_mil
            factory = get_session_factory()
            async with factory() as session:
                async with session.begin():
                    uf = await get_or_create_user_filter(session, chat_id)
                    uf.max_mileage = mil_val
                    await session.flush()

                    summary = format_filter_summary(uf)
                    kb = build_settings_keyboard(uf)
                    msg = f"✅ <b>Встановлено макс. пробіг: до {mil_val:,} км!</b>"
                    await self.send_message(
                        chat_id,
                        f"{msg}\n\n{summary}",
                        reply_markup=kb,
                    )
            return

        # Unknown message fallback
        await self.send_message(
            chat_id,
            "Не вдалося розпізнати значення.\n"
            "• Щоб вказати ціну: наприклад <code>3500-5000</code> або <code>до 5000</code>.\n"
            "• Щоб вказати пробіг: наприклад <code>250 тис</code> або <code>до 280000</code>.\n\n"
            "Скористайтеся кнопками меню внизу або надішліть /settings.",
            reply_markup=build_main_reply_keyboard(),
        )
