"""
Telegram Bot Long-Polling Listener service.
Polls updates from Telegram Bot API via getUpdates and routes to handlers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional
import httpx

from src.bot.handlers import BotHandler
from src.config import get_settings

logger = logging.getLogger(__name__)


class BotListener:
    """
    Lightweight, robust async long-polling listener for Telegram Bot API.
    Does not require external bot frameworks, uses httpx.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        poll_timeout: int = 25,
    ) -> None:
        settings = get_settings()
        self.bot_token = bot_token or settings.telegram_bot.bot_token or ""
        self.poll_timeout = poll_timeout
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._running = False
        self._offset: int = 0

    async def run(self) -> None:
        """
        Runs the long-polling event loop until stopped.
        """
        if not self.bot_token:
            logger.error("[bot] TELEGRAM_BOT_TOKEN is missing. Cannot start bot listener.")
            return

        self._running = True
        logger.info("[bot] Starting Audi A6 C5 Telegram Bot Listener...")

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.poll_timeout + 10.0, connect=10.0)) as client:
            handler = BotHandler(self.bot_token, client)

            while self._running:
                try:
                    params: Dict[str, Any] = {
                        "offset": self._offset,
                        "timeout": self.poll_timeout,
                        "allowed_updates": ["message", "callback_query"],
                    }

                    resp = await client.get(f"{self.api_url}/getUpdates", params=params)

                    if resp.status_code != 200:
                        logger.warning("[bot] getUpdates HTTP %d: %s", resp.status_code, resp.text)
                        await asyncio.sleep(2.0)
                        continue

                    data = resp.json()
                    if not data.get("ok"):
                        logger.warning("[bot] getUpdates response not ok: %s", data)
                        await asyncio.sleep(2.0)
                        continue

                    updates: List[Dict[str, Any]] = data.get("result", [])

                    for upd in updates:
                        upd_id = upd.get("update_id", 0)
                        self._offset = max(self._offset, upd_id + 1)

                        # 1. Message update
                        if "message" in upd:
                            msg = upd["message"]
                            chat_id = str(msg.get("chat", {}).get("id", ""))
                            text = (msg.get("text") or "").strip()

                            if text:
                                await handler.handle_text_message(chat_id, text)

                        # 2. Callback query update
                        elif "callback_query" in upd:
                            cb = upd["callback_query"]
                            cb_id = cb.get("id", "")
                            chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
                            msg_id = cb.get("message", {}).get("message_id", 0)
                            data_str = cb.get("data", "")

                            await handler.handle_callback_query(cb_id, chat_id, msg_id, data_str)

                except asyncio.CancelledError:
                    logger.info("[bot] Bot listener received cancellation signal.")
                    break
                except httpx.RequestError as req_err:
                    logger.warning("[bot] Network error during long-polling: %s. Retrying in 3s...", req_err)
                    await asyncio.sleep(3.0)
                except Exception as exc:
                    logger.error("[bot] Unexpected error in polling loop: %s", exc, exc_info=True)
                    await asyncio.sleep(2.0)

        logger.info("[bot] Telegram Bot Listener stopped.")

    def stop(self) -> None:
        """Stops the polling loop."""
        self._running = False
