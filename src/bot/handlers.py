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
    build_settings_keyboard,
    format_filter_summary,
)
from src.database.connection import get_session_factory
from src.database.models import UserFilterModel
from src.database.repository import (
    find_matching_listings,
    get_or_create_user_filter,
    reset_user_filter,
    update_user_filter,
)
from src.notifier.templates import format_listing_caption

logger = logging.getLogger(__name__)


class BotHandler:
    """
    Handles user commands and interactive inline button callbacks.
    """

    def __init__(self, bot_token: str, client: httpx.AsyncClient) -> None:
        self.bot_token = bot_token
        self.client = client
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "HTML",
    ) -> Optional[Dict[str, Any]]:
        """Sends a text message via Telegram Bot API."""
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
        parse_mode: str = "HTML",
    ) -> bool:
        """Sends a single photo with caption."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": parse_mode,
        }
        resp = await self.client.post(f"{self.api_url}/sendPhoto", json=payload)
        return resp.status_code == 200

    async def handle_start(self, chat_id: str) -> None:
        """Handles /start command."""
        welcome_text = (
            "👋 <b>Вітаю в моніторингу Audi A6 C5!</b>\n\n"
            "Цей бот автоматично знаходить найкращі пропозиції Audi A6 C5 "
            "по всій Україні (AUTO.RIA, OLX, RST, Telegram-канали та Instagram).\n\n"
            "Ви можете налаштувати пошук під власні критерії (мотори, ціну, пробіг, КПП).\n\n"
            "Натисніть /settings, щоб відкрити панель налаштувань!"
        )
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                uf = await get_or_create_user_filter(session, chat_id)
                summary = format_filter_summary(uf)
                kb = build_settings_keyboard(uf)
                await self.send_message(chat_id, f"{welcome_text}\n\n{summary}", reply_markup=kb)

    async def handle_settings(self, chat_id: str) -> None:
        """Handles /settings command."""
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                uf = await get_or_create_user_filter(session, chat_id)
                summary = format_filter_summary(uf)
                kb = build_settings_keyboard(uf)
                await self.send_message(chat_id, summary, reply_markup=kb)

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

                # 1. Toggle engine
                if data.startswith("toggle_eng:"):
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

                # 2. Min price
                elif data.startswith("set_pmin:"):
                    val_str = data.split(":", 1)[1]
                    uf.min_price = None if val_str == "none" else int(val_str)

                # 3. Max price
                elif data.startswith("set_pmax:"):
                    val_str = data.split(":", 1)[1]
                    uf.max_price = None if val_str == "none" else int(val_str)

                # 4. Transmission
                elif data.startswith("set_trans:"):
                    tr_val = data.split(":", 1)[1]
                    uf.transmission = tr_val

                # 5. Mileage
                elif data.startswith("set_mil:"):
                    mil_val = data.split(":", 1)[1]
                    uf.max_mileage = None if mil_val == "none" else int(mil_val)

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
                    listings = await find_matching_listings(session, uf, limit=5)
                    if not listings:
                        await self.send_message(
                            chat_id,
                            "🔍 <b>За вашими критеріями зараз немає збережених нових оголошень.</b>\n"
                            "Як тільки з'явиться підходяща машина — бот миттєво надішле її вам!",
                        )
                    else:
                        await self.send_message(
                            chat_id,
                            f"🔍 <b>Знайдено {len(listings)} останніх пропозицій за вашими фільтрами:</b>",
                        )
                        for item in listings:
                            caption = format_listing_caption(item)
                            images = item.images if isinstance(item.images, list) else []
                            if images and images[0].startswith("http"):
                                sent = await self.send_photo(chat_id, images[0], caption)
                                if not sent:
                                    await self.send_message(chat_id, caption)
                            else:
                                await self.send_message(chat_id, caption)
                    return

                await session.flush()
                await self.answer_callback(callback_id, alert_text)

                # Re-render menu
                summary = format_filter_summary(uf)
                kb = build_settings_keyboard(uf)
                await self.edit_message_text(chat_id, message_id, summary, reply_markup=kb)
