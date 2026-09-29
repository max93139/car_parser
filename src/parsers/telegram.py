"""
Telegram MTProto channel scraper using Telethon.
Monitors configured automotive channels, groups album messages by grouped_id,
parses structured vehicle specs from message text, and handles FloodWait backoff.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

from src.models.listing import RawListingPayload
from src.parsers.base import BaseParser

logger = logging.getLogger(__name__)

try:
    from telethon import TelegramClient
    from telethon.errors import FloodWaitError
    from telethon.sessions import StringSession
    from telethon.tl.types import MessageMediaPhoto
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False
    TelegramClient = None  # type: ignore

    class FloodWaitError(Exception):  # type: ignore
        def __init__(self, seconds: int = 0, *args: Any):
            super().__init__(*args)
            self.seconds = seconds

    class StringSession:  # type: ignore
        def __init__(self, session: Optional[str] = None):
            self.session = session

    class MessageMediaPhoto:  # type: ignore
        pass


class TelegramChannelParser(BaseParser):
    """
    Scraper for public Telegram automotive trading channels via MTProto (Telethon).
    """

    def __init__(
        self,
        api_id: Optional[int] = None,
        api_hash: Optional[str] = None,
        session_string: Optional[str] = None,
        channels: Optional[List[str]] = None,
        max_messages_per_channel: int = 30,
        client: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="telegram",
            base_url="https://t.me/",
            request_delay=0.5,
            **kwargs,
        )
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_string = session_string
        self.channels = channels or []
        self.max_messages = max(1, max_messages_per_channel)
        self.client: Optional[Any] = client
        self._owns_client = client is None

    async def _init_client(self) -> Optional[Any]:
        """Initialize or return the Telethon MTProto client."""
        if self.client is not None:
            return self.client

        if not HAS_TELETHON or TelegramClient is None:
            logger.warning("[telegram] Telethon library is not installed. MTProto unavailable.")
            return None

        if not self.api_id or not self.api_hash or not self.session_string:
            logger.warning("[telegram] Missing Telethon credentials (api_id, api_hash, or session_string).")
            return None

        try:
            session = StringSession(self.session_string)
            self.client = TelegramClient(session, self.api_id, self.api_hash)
            await self.client.connect()
            if not await self.client.is_user_authorized():
                logger.error("[telegram] Telethon StringSession is not authorized or expired.")
                await self.client.disconnect()
                self.client = None
                return None
            return self.client
        except Exception as conn_err:
            logger.error("[telegram] Failed to connect Telethon client: %s", conn_err)
            self.client = None
            return None

    async def close(self) -> None:
        """Disconnect and release Telethon client session."""
        if self.client is not None and self._owns_client:
            try:
                if hasattr(self.client, "disconnect") and callable(self.client.disconnect):
                    res = self.client.disconnect()
                    if asyncio.iscoroutine(res):
                        await res
            except Exception as close_err:
                logger.warning("[telegram] Error closing client: %s", close_err)
            finally:
                self.client = None
        await super().close()

    async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]:
        """
        Polls configured Telegram channels, groups album messages,
        and yields RawListingPayloads.
        """
        self.stats.start()

        # If credentials missing or client cannot be initialized -> Graceful degradation
        client = await self._init_client()
        if client is None:
            logger.warning("[telegram] Telegram parser degrading gracefully due to missing/unauthorized client.")
            self.stats.finish(status="DEGRADED")
            return

        self.stats.status = "RUNNING"

        for channel in self.channels:
            clean_channel = channel.lstrip("@").strip()
            if not clean_channel:
                continue

            try:
                entity = await client.get_entity(channel)

                grouped_albums: Dict[int, List[Any]] = {}
                standalone_messages: List[Any] = []

                # Fetch messages using iter_messages
                async for msg in client.iter_messages(entity, limit=self.max_messages):
                    self.stats.record_fetched()
                    grouped_id = getattr(msg, "grouped_id", None)
                    if grouped_id:
                        grouped_albums.setdefault(grouped_id, []).append(msg)
                    else:
                        standalone_messages.append(msg)

                # Process standalone ads
                for msg in standalone_messages:
                    try:
                        payload = self._parse_single_message(clean_channel, msg)
                        if payload:
                            self.stats.record_valid()
                            yield payload
                    except Exception as msg_err:
                        self.stats.record_error()
                        logger.error("[telegram] Error parsing message %s in %s: %s", getattr(msg, "id", None), clean_channel, msg_err)
                        continue

                # Process grouped album ads
                for grp_id, msgs in grouped_albums.items():
                    try:
                        payload = self._parse_album_messages(clean_channel, grp_id, msgs)
                        if payload:
                            self.stats.record_valid()
                            yield payload
                    except Exception as album_err:
                        self.stats.record_error()
                        logger.error("[telegram] Error parsing album %s in %s: %s", grp_id, clean_channel, album_err)
                        continue

            except FloodWaitError as fwe:
                seconds = getattr(fwe, "seconds", 10)
                logger.warning("[telegram] FloodWaitError on %s: sleeping %d seconds", channel, seconds)
                await asyncio.sleep(seconds)
            except Exception as ch_err:
                self.stats.record_error()
                logger.error("[telegram] Error monitoring channel %s: %s", channel, ch_err, exc_info=True)
                continue

        self.stats.finish()

    def parse_message_specs(self, text: str) -> Dict[str, Any]:
        """
        Helper extracting structured specs from raw Ukrainian/Russian message text.
        """
        specs: Dict[str, Any] = {
            "year": None,
            "raw_year": None,
            "price": None,
            "raw_price": None,
            "currency": "USD",
            "mileage": None,
            "raw_mileage": None,
            "engine": None,
            "raw_engine": None,
            "fuel_type": None,
            "raw_fuel": None,
            "transmission": None,
            "raw_transmission": None,
            "location": None,
            "raw_location": None,
            "seller_phone": None,
        }
        if not text:
            return specs

        lines = [line.strip() for line in text.split("\n") if line.strip()]

        # 1. Year
        year_match = re.search(r"(?:Рік|Год|Year)?[:\s-]*\b(199[7-9]|200[0-5])\b", text, re.IGNORECASE)
        if year_match:
            specs["year"] = int(year_match.group(1))
            specs["raw_year"] = year_match.group(1)

        # 2. Price
        price_match = re.search(r"(?:Ціна|Цена|Price)?[:\s-]*(\d[\d\s]{2,})\s*(\$|USD|грн|UAH|EUR|€)", text, re.IGNORECASE)
        if price_match:
            raw_nums = re.sub(r"[^\d.]", "", price_match.group(1))
            curr_sym = price_match.group(2)
            try:
                specs["price"] = float(raw_nums)
                specs["raw_price"] = f"{price_match.group(1).strip()} {curr_sym}"
                if "грн" in curr_sym.lower() or "uah" in curr_sym.lower():
                    specs["currency"] = "UAH"
                elif "eur" in curr_sym.lower() or "€" in curr_sym:
                    specs["currency"] = "EUR"
                else:
                    specs["currency"] = "USD"
            except ValueError:
                pass
        else:
            # Fallback price pattern e.g. "4800$"
            p_fallback = re.search(r"(\d{3,5})\s*(\$|USD)", text, re.IGNORECASE)
            if p_fallback:
                try:
                    specs["price"] = float(p_fallback.group(1))
                    specs["raw_price"] = f"{p_fallback.group(1)} $"
                    specs["currency"] = "USD"
                except ValueError:
                    pass

        # 3. Mileage
        mileage_match = re.search(r"(?:Пробіг|Пробег)?[:\s-]*(\d+(?:[\s.,]\d+)?)\s*тис", text, re.IGNORECASE)
        if mileage_match:
            specs["raw_mileage"] = mileage_match.group(0).strip()
            val_str = mileage_match.group(1).replace(" ", "").replace(",", ".")
            try:
                specs["mileage"] = int(float(val_str) * 1000)
            except ValueError:
                pass

        # 4. Engine & Fuel
        engine_match = re.search(r"(?:Двигун|Двигатель|Мотор)?[:\s-]*\b(1\.[89]\s*(?:tdi|дизель|турбо|т|t|turbo)?|2\.4\s*(?:v6|газ/бензин|газ-бензин|бензин)?)\b", text, re.IGNORECASE)
        if engine_match:
            specs["raw_engine"] = engine_match.group(1).strip()
            specs["engine"] = specs["raw_engine"]

        if "дизель" in text.lower() or "tdi" in text.lower():
            specs["fuel_type"] = "дизель"
            specs["raw_fuel"] = "дизель"
        elif "газ" in text.lower() and "бензин" in text.lower():
            specs["fuel_type"] = "газ/бензин"
            specs["raw_fuel"] = "газ/бензин"
        elif "бензин" in text.lower():
            specs["fuel_type"] = "бензин"
            specs["raw_fuel"] = "бензин"

        # 5. Transmission
        if "механ" in text.lower() or "6-ступка" in text.lower() or "5-ступ" in text.lower():
            specs["transmission"] = "механіка"
            specs["raw_transmission"] = "механіка"
        elif "автомат" in text.lower() or "тіптронік" in text.lower():
            specs["transmission"] = "автомат"
            specs["raw_transmission"] = "автомат"

        # 6. Location
        loc_match = re.search(r"(?:Місто|Город|Локація)?[:\s-]*\b(Київ|Львів|Одеса|Дніпро|Харків|Тернопіль|Рівне|Луцьк|Вінниця|Івано-Франківськ|Хмельницький|Чернівці|Житомир|Полтава|Черкаси|Суми|Запоріжжя|Миколаїв|Ужгород)\b", text, re.IGNORECASE)
        if loc_match:
            specs["location"] = loc_match.group(1)
            specs["raw_location"] = loc_match.group(1)

        # 7. Phone
        phone_match = re.search(r"(?:\+?380|0)\d{9}", re.sub(r"[\s()-]", "", text))
        if phone_match:
            specs["seller_phone"] = phone_match.group(0)

        return specs

    def _extract_images_from_msg(self, msg: Any) -> List[str]:
        """Helper to extract direct photo URLs or identifiers from Telethon message."""
        if isinstance(msg, dict):
            return list(msg.get("photo_urls", []))
        if hasattr(msg, "photo_urls") and msg.photo_urls:
            return list(msg.photo_urls)

        photos: List[str] = []
        # Telethon Message object
        if hasattr(msg, "media") and msg.media:
            if isinstance(msg.media, MessageMediaPhoto) or type(msg.media).__name__ == "MessageMediaPhoto":
                # For MTProto messages, we construct canonical t.me link or internal ref
                photo_id = getattr(getattr(msg.media, "photo", None), "id", None)
                if photo_id:
                    photos.append(f"telegram_photo_{photo_id}")
        return photos

    def _parse_single_message(self, channel: str, msg: Any) -> Optional[RawListingPayload]:
        """Convert a standalone message into RawListingPayload."""
        msg_id = msg.get("id") if isinstance(msg, dict) else getattr(msg, "id", None)
        text = msg.get("message", "") if isinstance(msg, dict) else getattr(msg, "message", "") or ""
        date = msg.get("date") if isinstance(msg, dict) else getattr(msg, "date", None)

        if not text and not self._extract_images_from_msg(msg):
            return None

        # Clean text
        text_clean = text.strip()
        lines = [l for l in text_clean.split("\n") if l.strip()]
        title = lines[0][:100] if lines else f"Telegram post from @{channel}"

        specs = self.parse_message_specs(text_clean)
        post_url = f"https://t.me/{channel}/{msg_id}"
        photos = self._extract_images_from_msg(msg)

        # Parse date if string
        published_dt = None
        if isinstance(date, datetime):
            published_dt = date
        elif isinstance(date, str):
            try:
                published_dt = datetime.fromisoformat(date)
            except Exception:
                pass

        return RawListingPayload(
            source=self.name,
            source_id=f"{channel}_{msg_id}",
            url=post_url,
            title=title,
            raw_text=text_clean,
            description=text_clean,
            raw_price=specs["raw_price"],
            price=specs["price"],
            currency=specs["currency"],
            raw_year=specs["raw_year"],
            year=specs["year"],
            raw_mileage=specs["raw_mileage"],
            mileage=specs["mileage"],
            raw_engine=specs["raw_engine"],
            engine=specs["engine"],
            raw_fuel=specs["raw_fuel"],
            fuel_type=specs["fuel_type"],
            raw_transmission=specs["raw_transmission"],
            transmission=specs["transmission"],
            raw_location=specs["raw_location"],
            location=specs["location"],
            seller_phone=specs["seller_phone"],
            image_urls=photos[:10],
            images=photos[:10],
            published_at=published_dt,
            extra_attributes={"channel": channel, "message_id": msg_id, "is_album": False},
        )

    def _parse_album_messages(self, channel: str, grouped_id: int, msgs: List[Any]) -> Optional[RawListingPayload]:
        """Convert a list of messages belonging to the same album into a single RawListingPayload."""
        if not msgs:
            return None

        # Find primary message with caption
        def get_text(m: Any) -> str:
            return (m.get("message", "") if isinstance(m, dict) else getattr(m, "message", "") or "").strip()

        caption_msg = next((m for m in msgs if get_text(m)), msgs[0])
        text = get_text(caption_msg)
        lines = [l for l in text.split("\n") if l.strip()]
        title = lines[0][:100] if lines else f"Telegram album from @{channel}"

        # Collect photos across all messages in album
        all_photos: List[str] = []
        for m in msgs:
            all_photos.extend(self._extract_images_from_msg(m))

        # Distinct preserve order
        seen = set()
        dedup_photos = [p for p in all_photos if not (p in seen or seen.add(p))]

        def get_id(m: Any) -> int:
            return m.get("id") if isinstance(m, dict) else getattr(m, "id", 0)

        min_id = min(get_id(m) for m in msgs)
        post_url = f"https://t.me/{channel}/{min_id}"
        specs = self.parse_message_specs(text)

        date = caption_msg.get("date") if isinstance(caption_msg, dict) else getattr(caption_msg, "date", None)
        published_dt = None
        if isinstance(date, datetime):
            published_dt = date
        elif isinstance(date, str):
            try:
                published_dt = datetime.fromisoformat(date)
            except Exception:
                pass

        return RawListingPayload(
            source=self.name,
            source_id=f"{channel}_{min_id}",
            url=post_url,
            title=title,
            raw_text=text,
            description=text,
            raw_price=specs["raw_price"],
            price=specs["price"],
            currency=specs["currency"],
            raw_year=specs["raw_year"],
            year=specs["year"],
            raw_mileage=specs["raw_mileage"],
            mileage=specs["mileage"],
            raw_engine=specs["raw_engine"],
            engine=specs["engine"],
            raw_fuel=specs["raw_fuel"],
            fuel_type=specs["fuel_type"],
            raw_transmission=specs["raw_transmission"],
            transmission=specs["transmission"],
            raw_location=specs["raw_location"],
            location=specs["location"],
            seller_phone=specs["seller_phone"],
            image_urls=dedup_photos[:10],
            images=dedup_photos[:10],
            published_at=published_dt,
            extra_attributes={
                "channel": channel,
                "grouped_id": grouped_id,
                "album_message_count": len(msgs),
                "is_album": True,
            },
        )
