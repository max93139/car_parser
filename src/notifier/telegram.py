"""
Telegram Notification Engine for Audi A6 C5 Monitoring Service.
Provides asynchronous Telegram Bot API integration supporting:
- Media group albums (sendMediaGroup) for 2 to 10 photos
- Single photo alerts (sendPhoto) for 1 photo
- Text message alerts (sendMessage) for 0 photos or fallback
- Inter-message rate limiting (>=1.2s delay)
- Automatic HTTP 429 / FloodWait exponential backoff
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Any, Dict, List, Optional, Union
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.database.models import ListingModel
from src.models.listing import Listing
from src.notifier.templates import format_listing_caption

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Production-grade Telegram Bot API client optimized for car media groups,
    HTML templating, rate limiting, and automated FloodWait backoff.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        max_photos: int = 10,
        rate_limit_delay: float = 1.2,
        max_retries: int = 3,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        settings = get_settings()
        self.bot_token = bot_token or settings.telegram_bot.bot_token or ""
        self.chat_id = str(chat_id or settings.telegram_bot.chat_id or "")
        self.max_photos = min(max(max_photos, 1), 10)
        self.rate_limit_delay = max(rate_limit_delay, 1.2)
        self.max_retries = max(max_retries, 1)

        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._injected_client = client
        self._client: Optional[httpx.AsyncClient] = client

        # Rate limiter synchronization state
        self._lock = asyncio.Lock()
        self._last_send_time: float = 0.0

    async def get_client(self) -> httpx.AsyncClient:
        """Lazy initialization of httpx.AsyncClient with connection pooling."""
        if self._injected_client is not None:
            return self._injected_client

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=10.0),
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        """Closes network sockets gracefully."""
        if self._client is not None and not self._client.is_closed:
            if self._client is not self._injected_client:
                await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> TelegramNotifier:
        await self.get_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def wait_rate_limit(self) -> None:
        """
        Enforces minimum inter-message delay (>=1.2s) between Telegram dispatches.
        Thread-safe and coroutine-safe using asyncio.Lock.
        """
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_send_time
            if elapsed < self.rate_limit_delay:
                sleep_time = self.rate_limit_delay - elapsed
                await asyncio.sleep(sleep_time)
            self._last_send_time = time.monotonic()

    def calculate_floodwait_backoff(self, retry_after: float, multiplier: float = 1.5) -> float:
        """Calculates backoff duration for Telegram FloodWait / 429 responses."""
        return float(retry_after) * multiplier

    def parse_retry_after(
        self,
        response: Optional[httpx.Response] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Extracts retry_after delay from response JSON parameters or HTTP headers.
        """
        if data and isinstance(data, dict):
            params = data.get("parameters", {})
            if "retry_after" in params:
                try:
                    return int(params["retry_after"])
                except (ValueError, TypeError):
                    pass

        if response is not None and hasattr(response, "headers"):
            retry_hdr = response.headers.get("Retry-After")
            if retry_hdr:
                try:
                    return int(retry_hdr)
                except ValueError:
                    pass

        return 10

    async def _post_api(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        max_retries: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Executes Telegram Bot API POST request with rate limiting and 429 FloodWait backoff.
        """
        if not self.bot_token or not self.chat_id:
            logger.warning("[TelegramNotifier] Bot token or chat ID is empty. Skipping API dispatch.")
            return None

        client = await self.get_client()
        url = f"{self.api_url}/{endpoint}"
        retries = max_retries or self.max_retries

        for attempt in range(1, retries + 1):
            # Throttle dispatch according to rate limit policy
            await self.wait_rate_limit()

            try:
                response = await client.post(url, json=payload)
                try:
                    data = response.json()
                except Exception:
                    data = {}

                # 1. Success
                if response.status_code == 200 and data.get("ok"):
                    return data

                # 2. Rate limit / FloodWait (HTTP 429)
                if response.status_code == 429 or data.get("error_code") == 429:
                    retry_after = self.parse_retry_after(response, data)
                    backoff = self.calculate_floodwait_backoff(retry_after)
                    logger.warning(
                        "[TelegramNotifier] 429 FloodWait on %s. Sleeping %.1fs (attempt %d/%d)",
                        endpoint, backoff, attempt, retries
                    )
                    await asyncio.sleep(backoff)
                    continue

                # 3. Client error (e.g. 400 Bad Request due to hotlinked image fetch error)
                if response.status_code == 400:
                    description = data.get("description", "")
                    logger.warning(
                        "[TelegramNotifier] HTTP 400 on %s: %s (attempt %d/%d)",
                        endpoint, description, attempt, retries
                    )
                    # Non-retryable error on same endpoint/payload; return None for fallback
                    return None

                # 4. Other non-200 responses
                logger.warning(
                    "[TelegramNotifier] HTTP %d on %s: %s (attempt %d/%d)",
                    response.status_code, endpoint, data, attempt, retries
                )
                await asyncio.sleep(1.0 * attempt)

            except (httpx.RequestError, httpx.TimeoutException) as exc:
                logger.warning(
                    "[TelegramNotifier] Network exception %s on %s. Backoff %.1fs (attempt %d/%d)",
                    type(exc).__name__, endpoint, 2.0 * attempt, attempt, retries
                )
                await asyncio.sleep(2.0 * attempt)
            except Exception as e:
                logger.error("[TelegramNotifier] Unexpected error on %s: %s", endpoint, e, exc_info=True)
                break

        logger.error("[TelegramNotifier] Exhausted %d retries for %s", retries, endpoint)
        return None

    async def send_media_group(self, photos: List[str], caption: str) -> bool:
        """
        Dispatches a Telegram media group (photo album) containing between 2 and 10 photos.
        Caption is assigned exclusively to the first photo.
        """
        valid_photos = [p for p in photos if p and isinstance(p, str) and p.startswith("http")][:self.max_photos]
        if len(valid_photos) < 2:
            return False

        media: List[Dict[str, Any]] = []
        for idx, photo_url in enumerate(valid_photos):
            item: Dict[str, Any] = {
                "type": "photo",
                "media": photo_url,
            }
            if idx == 0:
                item["caption"] = caption
                item["parse_mode"] = "HTML"
            media.append(item)

        payload = {
            "chat_id": self.chat_id,
            "media": media,
        }
        result = await self._post_api("sendMediaGroup", payload)
        return result is not None and result.get("ok", False)

    async def send_photo(self, photo: str, caption: str) -> bool:
        """
        Dispatches a single photo message with an HTML caption.
        """
        payload = {
            "chat_id": self.chat_id,
            "photo": photo,
            "caption": caption,
            "parse_mode": "HTML",
        }
        result = await self._post_api("sendPhoto", payload)
        return result is not None and result.get("ok", False)

    async def send_message(self, text: str) -> bool:
        """
        Dispatches a text-only HTML message (supports up to 4096 characters).
        """
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        result = await self._post_api("sendMessage", payload)
        return result is not None and result.get("ok", False)

    async def send_listing_alert(
        self,
        listing: Union[Listing, Dict[str, Any], ListingModel],
        price_drop_info: Optional[Dict[str, Any]] = None,
        session: Optional[AsyncSession] = None,
    ) -> bool:
        """
        Main alert dispatcher:
        - Uses sendMediaGroup if listing has 2 to 10 photos.
        - Falls back to sendPhoto if listing has 1 photo (or if sendMediaGroup fails).
        - Falls back to sendMessage if listing has 0 photos (or if photo delivery fails).
        - Automatically updates DB state if session is provided.
        """
        # 1. Resolve photo URLs
        raw_images = getattr(listing, "images", None)
        if raw_images is None and isinstance(listing, dict):
            raw_images = listing.get("images", [])
        if raw_images is None:
            raw_images = []

        photos = [p for p in raw_images if isinstance(p, str) and p.startswith("http")][:self.max_photos]

        # 2. Render caption with 1024 char hard limit
        caption = format_listing_caption(listing, price_drop_info=price_drop_info, max_length=1024)

        success = False

        # 3. Strategy execution with fallback hierarchy
        if len(photos) >= 2:
            success = await self.send_media_group(photos, caption)
            if not success:
                logger.warning("[TelegramNotifier] sendMediaGroup failed. Falling back to sendPhoto.")
                success = await self.send_photo(photos[0], caption)
                if not success:
                    logger.warning("[TelegramNotifier] sendPhoto fallback failed. Falling back to sendMessage.")
                    text_caption = format_listing_caption(listing, price_drop_info=price_drop_info, max_length=4000)
                    success = await self.send_message(text_caption)

        elif len(photos) == 1:
            success = await self.send_photo(photos[0], caption)
            if not success:
                logger.warning("[TelegramNotifier] sendPhoto failed. Falling back to sendMessage.")
                text_caption = format_listing_caption(listing, price_drop_info=price_drop_info, max_length=4000)
                success = await self.send_message(text_caption)

        else:
            text_caption = format_listing_caption(listing, price_drop_info=price_drop_info, max_length=4000)
            success = await self.send_message(text_caption)

        # 4. State synchronization if successful
        if success:
            now = datetime.now(timezone.utc)
            if isinstance(listing, Listing):
                listing.is_sent_to_telegram = True
                listing.telegram_sent_at = now
            elif isinstance(listing, ListingModel):
                listing.is_sent_to_telegram = True
                listing.telegram_sent_at = now
                listing.status = "SENT"

            if session is not None:
                listing_id = getattr(listing, "id", None)
                if listing_id is not None:
                    stmt = select(ListingModel).where(ListingModel.id == listing_id)
                    db_item = (await session.execute(stmt)).scalar_one_or_none()
                    if db_item:
                        db_item.is_sent_to_telegram = True
                        db_item.telegram_sent_at = now
                        db_item.status = "SENT"
                        await session.flush()

        return success

    # Alias for API symmetry with survey reports
    send_listing_notification = send_listing_alert
