"""
Instagram public account scraper with graceful degradation on login wall.
Scrapes dealership posts, extracting photos, car specifications, and dealer contacts.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

from src.models.listing import RawListingPayload
from src.parsers.base import BaseParser

logger = logging.getLogger(__name__)


class InstagramParser(BaseParser):
    """
    Public scraper for automotive dealership Instagram accounts.
    Gracefully degrades when encountering authentication walls or bot challenges.
    """

    def __init__(
        self,
        accounts: Optional[List[str]] = None,
        session_cookie: Optional[str] = None,
        max_posts_per_account: int = 12,
        request_delay: float = 2.0,
        timeout: float = 15.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="instagram",
            base_url="https://www.instagram.com/",
            request_delay=request_delay,
            timeout=timeout,
            **kwargs,
        )
        self.accounts = accounts or []
        self.session_cookie = session_cookie
        self.max_posts = max(1, max_posts_per_account)

        self.default_headers.update({
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.instagram.com/",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        })
        if session_cookie:
            cookie_val = session_cookie if session_cookie.startswith("sessionid=") else f"sessionid={session_cookie};"
            self.default_headers["Cookie"] = cookie_val

    async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]:
        """
        Polls configured Instagram accounts and yields RawListingPayloads.
        Degrades gracefully if authentication or anti-scraping walls are triggered.
        """
        self.stats.start()

        if not self.accounts:
            logger.info("[instagram] No accounts configured for Instagram parser.")
            self.stats.finish(status="SUCCESS")
            return

        for account in self.accounts:
            username = account.lstrip("@").strip()
            if not username:
                continue

            endpoint = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"

            try:
                response = await self.fetch_response_with_retry(endpoint)
                if response is None:
                    logger.warning("[instagram] No response received for @%s", username)
                    self.stats.status = "PARTIAL"
                    continue

                # Check for login wall or rate limiting status
                if response.status_code in (401, 403, 429, 302):
                    logger.warning(
                        "[instagram] Access restricted (HTTP %d) for @%s. Gracefully degrading parser.",
                        response.status_code, username
                    )
                    self.stats.status = "DEGRADED"
                    continue

                # Check HTML response for login wall challenge
                content_text = response.text
                if "/accounts/login/" in content_text or "Login • Instagram" in content_text or "<!DOCTYPE html>" in content_text:
                    logger.warning(
                        "[instagram] Login wall detected for @%s. Gracefully degrading parser.",
                        username
                    )
                    self.stats.status = "DEGRADED"
                    continue

                try:
                    data = response.json()
                except Exception as json_err:
                    logger.warning("[instagram] Non-JSON response for @%s: %s", username, json_err)
                    self.stats.status = "DEGRADED"
                    continue

                user_data = data.get("data", {}).get("user", {})
                if not user_data:
                    logger.info("[instagram] User profile data empty for @%s", username)
                    continue

                edges = user_data.get("edge_owner_to_timeline_media", {}).get("edges", [])
                for edge in edges[:self.max_posts]:
                    self.stats.record_fetched()
                    try:
                        payload = self._parse_edge_node(username, edge.get("node", {}))
                        if payload:
                            self.stats.record_valid()
                            yield payload
                    except Exception as node_err:
                        self.stats.record_error()
                        logger.error("[instagram] Error parsing post node from @%s: %s", username, node_err, exc_info=False)
                        continue

            except Exception as acc_err:
                self.stats.record_error()
                logger.error("[instagram] Unexpected error scraping @%s: %s", username, acc_err, exc_info=True)
                self.stats.status = "PARTIAL"
                continue

        if self.stats.status != "DEGRADED":
            self.stats.finish()

    def parse_caption_specs(self, text: str) -> Dict[str, Any]:
        """Extract structured car specs from Instagram post caption."""
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

        # Year
        year_match = re.search(r"\b(199[7-9]|200[0-5])\b", text)
        if year_match:
            specs["year"] = int(year_match.group(1))
            specs["raw_year"] = year_match.group(1)

        # Price
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
            p_fallback = re.search(r"(\d{3,5})\s*(\$|USD)", text, re.IGNORECASE)
            if p_fallback:
                try:
                    specs["price"] = float(p_fallback.group(1))
                    specs["raw_price"] = f"{p_fallback.group(1)} $"
                    specs["currency"] = "USD"
                except ValueError:
                    pass

        # Mileage
        mileage_match = re.search(r"(?:Пробіг|Пробег)?[:\s-]*(\d+(?:[\s.,]\d+)?)\s*тис", text, re.IGNORECASE)
        if mileage_match:
            specs["raw_mileage"] = mileage_match.group(0).strip()
            val_str = mileage_match.group(1).replace(" ", "").replace(",", ".")
            try:
                specs["mileage"] = int(float(val_str) * 1000)
            except ValueError:
                pass

        # Engine & Fuel
        engine_match = re.search(r"\b(1\.[89]\s*(?:tdi|дизель|турбо|т|t|turbo)?|2\.4\s*(?:v6|газ/бензин|газ-бензин|бензин)?)\b", text, re.IGNORECASE)
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

        # Transmission
        if "механ" in text.lower() or "ручна" in text.lower():
            specs["transmission"] = "механіка"
            specs["raw_transmission"] = "механіка"
        elif "автомат" in text.lower() or "тіптронік" in text.lower():
            specs["transmission"] = "автомат"
            specs["raw_transmission"] = "автомат"

        # Location
        loc_match = re.search(r"(?:м\.|місто|город)?[:\s-]*\b(Київ|Львів|Одеса|Дніпро|Харків|Тернопіль|Рівне|Луцьк|Вінниця|Івано-Франківськ|Хмельницький|Чернівці|Житомир|Полтава|Черкаси|Суми|Запоріжжя|Миколаїв|Ужгород)\b", text, re.IGNORECASE)
        if loc_match:
            specs["location"] = loc_match.group(1)
            specs["raw_location"] = loc_match.group(1)

        # Phone
        phone_match = re.search(r"(?:\+?380|0)\d{9}", re.sub(r"[\s()-]", "", text))
        if phone_match:
            specs["seller_phone"] = phone_match.group(0)

        return specs

    def _parse_edge_node(self, username: str, node: Dict[str, Any]) -> Optional[RawListingPayload]:
        """Convert a single Instagram media node into RawListingPayload."""
        shortcode = node.get("shortcode")
        if not shortcode:
            return None

        post_url = f"https://www.instagram.com/p/{shortcode}/"
        caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
        caption = caption_edges[0]["node"]["text"] if caption_edges else ""

        title = caption.split("\n")[0][:100] if caption else f"Instagram post by @{username}"

        # Photos: carousel or single
        photos: List[str] = []
        carousel = node.get("edge_sidecar_to_children", {}).get("edges", [])
        if carousel:
            for child in carousel:
                disp = child.get("node", {}).get("display_url")
                if disp and disp not in photos:
                    photos.append(disp)
        elif node.get("display_url"):
            photos.append(node["display_url"])

        specs = self.parse_caption_specs(caption)

        # Published date
        published_dt = None
        taken_timestamp = node.get("taken_at_timestamp")
        if taken_timestamp:
            try:
                published_dt = datetime.fromtimestamp(taken_timestamp, tz=timezone.utc)
            except Exception:
                pass

        return RawListingPayload(
            source=self.name,
            source_id=f"ig_{shortcode}",
            url=post_url,
            title=title,
            raw_text=caption,
            description=caption,
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
            seller=f"@{username}",
            seller_name=f"@{username}",
            seller_phone=specs["seller_phone"],
            image_urls=photos[:10],
            images=photos[:10],
            published_at=published_dt,
            extra_attributes={"username": username, "shortcode": shortcode},
        )
