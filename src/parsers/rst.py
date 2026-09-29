"""
RST.ua scraper for Audi A6 listings.
Handles Ukrainian and legacy Windows-1251 / UTF-8 encodings, parses structured
specifications from description summaries, and converts thumbnail photos to high-res URLs.
"""

from __future__ import annotations

import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional
from bs4 import BeautifulSoup
import httpx

from src.models.listing import RawListingPayload
from src.parsers.base import BaseParser

logger = logging.getLogger(__name__)


class RstParser(BaseParser):
    """
    Scraper for RST.ua Audi A6 classifieds.
    """

    def __init__(
        self,
        base_url: str = "https://rst.ua/ukr/oldcars/audi/a6/",
        max_pages: int = 1,
        request_delay: float = 1.2,
        timeout: float = 15.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="rst",
            base_url=base_url,
            request_delay=request_delay,
            timeout=timeout,
            **kwargs,
        )
        self.max_pages = max(1, max_pages)

    @staticmethod
    def decode_response_bytes(content: bytes, content_type_header: Optional[str] = None) -> str:
        """
        Robustly decodes RST page bytes handling UTF-8, Windows-1251, and CP1251.
        """
        # 1. Check content-type header for explicit charset
        if content_type_header:
            match = re.search(r"charset=([a-zA-Z0-9_-]+)", content_type_header, re.IGNORECASE)
            if match:
                charset = match.group(1).lower()
                if charset in ("windows-1251", "cp1251", "1251"):
                    try:
                        return content.decode("windows-1251")
                    except UnicodeDecodeError:
                        pass
                elif charset in ("utf-8", "utf8"):
                    try:
                        return content.decode("utf-8")
                    except UnicodeDecodeError:
                        pass

        # 2. Try UTF-8 first (modern RST /ukr/ pages)
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            pass

        # 3. Try Windows-1251 (legacy RST pages)
        try:
            return content.decode("windows-1251")
        except UnicodeDecodeError:
            pass

        # 4. Fallback with replacement
        return content.decode("utf-8", errors="replace")

    async def fetch_html_with_charset(self, url: str) -> Optional[str]:
        """Fetch RST response with adaptive charset handling."""
        response = await self.fetch_response_with_retry(url)
        if response is None or response.status_code != 200:
            return None
        content_type = response.headers.get("content-type", "")
        return self.decode_response_bytes(response.content, content_type)

    async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]:
        """
        Scrapes RST search results and yields RawListingPayloads.
        """
        self.stats.start()

        for page in range(1, self.max_pages + 1):
            page_url = self.base_url if page == 1 else f"{self.base_url.rstrip('/')}/{page}.html"

            try:
                html = await self.fetch_html_with_charset(page_url)
                if not html:
                    logger.warning("[rst] No HTML returned for %s", page_url)
                    if page == 1:
                        self.stats.finish(status="FAILED")
                        return
                    continue

                soup = BeautifulSoup(html, "html.parser")
                cards = soup.select(".rst-ocb-i")

                if not cards:
                    logger.info("[rst] No cards found on page %d", page)
                    break

                for card in cards:
                    self.stats.record_fetched()
                    try:
                        payload = self._parse_card(card)
                        if payload:
                            self.stats.record_valid()
                            yield payload
                    except Exception as card_err:
                        self.stats.record_error()
                        logger.error("[rst] Error parsing listing card: %s", card_err, exc_info=False)
                        continue

            except Exception as page_err:
                self.stats.record_error()
                logger.error("[rst] Error fetching %s: %s", page_url, page_err, exc_info=True)
                continue

        self.stats.finish()

    def _parse_card(self, card: Any) -> Optional[RawListingPayload]:
        """Extract RawListingPayload from a single RST listing card element."""
        link = card.select_one("a.rst-ocb-i-a, h3.rst-ocb-i-h a")
        if not link or not link.get("href"):
            return None

        href = link["href"]
        full_url = f"https://rst.ua{href}" if href.startswith("/") else href
        clean_url = full_url.split("?")[0].split("#")[0]

        id_match = re.search(r"audi_a6_(\d+)\.html", clean_url)
        if not id_match:
            return None
        source_id = id_match.group(1)

        title_elem = card.select_one("h3.rst-ocb-i-h, .rst-ocb-i-h")
        title = title_elem.get_text(strip=True) if title_elem else link.get_text(strip=True)

        price_elem = card.select_one(".rst-ocb-i-d-s-p, .rst-uix-price")
        raw_price = price_elem.get_text(strip=True) if price_elem else None
        price_val: Optional[float] = None
        currency = "USD"
        if raw_price:
            if "грн" in raw_price.lower():
                currency = "UAH"
            elif "€" in raw_price:
                currency = "EUR"
            nums = re.sub(r"[^\d.]", "", raw_price.replace(" ", ""))
            if nums:
                try:
                    price_val = float(nums)
                except ValueError:
                    pass

        desc_elem = card.select_one(".rst-ocb-i-d-d")
        raw_text = desc_elem.get_text(" ", strip=True) if desc_elem else ""

        # Extract parameters from title and description
        year_val: Optional[int] = None
        raw_year: Optional[str] = None
        year_match = re.search(r"\b(199[7-9]|200[0-5])\b", f"{title} {raw_text}")
        if year_match:
            year_val = int(year_match.group(1))
            raw_year = year_match.group(1)

        # Engine & Fuel
        raw_engine: Optional[str] = None
        raw_fuel: Optional[str] = None
        engine_match = re.search(r"\b(1\.[89]\s*(?:tdi|дизель|турбо|т|t)?|2\.[4578]\s*(?:газ-бензин|газ/бензин|бензин|дизель|tdi)?)\b", raw_text, re.IGNORECASE)
        if engine_match:
            raw_engine = engine_match.group(0).strip()
            if "дизель" in raw_text.lower() or "tdi" in raw_text.lower():
                raw_fuel = "дизель"
            elif "газ" in raw_text.lower() and "бензин" in raw_text.lower():
                raw_fuel = "газ/бензин"
            elif "бензин" in raw_text.lower():
                raw_fuel = "бензин"

        # Mileage
        mileage_val: Optional[int] = None
        raw_mileage: Optional[str] = None
        mileage_match = re.search(r"(\d+(?:[\s.,]\d+)?)\s*тис\.?\s*км", raw_text, re.IGNORECASE)
        if mileage_match:
            raw_mileage = mileage_match.group(0)
            val_str = mileage_match.group(1).replace(" ", "").replace(",", ".")
            try:
                mileage_val = int(float(val_str) * 1000)
            except ValueError:
                pass

        # Transmission
        raw_trans: Optional[str] = None
        if "механ" in raw_text.lower() or "ручна" in raw_text.lower():
            raw_trans = "механіка"
        elif "автомат" in raw_text.lower() or "кпп автомат" in raw_text.lower():
            raw_trans = "автомат"

        # Location / City
        raw_location: Optional[str] = None
        cities = ["Київ", "Львів", "Одеса", "Дніпро", "Харків", "Рівне", "Луцьк", "Вінниця", "Тернопіль", "Івано-Франківськ", "Хмельницький", "Чернівці", "Житомир", "Полтава", "Черкаси", "Суми", "Запоріжжя", "Миколаїв", "Ужгород"]
        for city in cities:
            if re.search(rf"\b{city}\b", raw_text, re.IGNORECASE):
                raw_location = city
                break

        # Photos
        photos: List[str] = []
        img = card.select_one("img.rst-ocb-i-d-l-i, img")
        if img and img.get("src"):
            src = img["src"]
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = f"https://rst.ua{src}"
            # Convert thumbnail suffix (_1.jpg, _s.jpg, _m.jpg) to high-res (_0.jpg)
            src_full = re.sub(r"_[sSmMtT1]\.jpg$", "_0.jpg", src)
            photos.append(src_full)

        return RawListingPayload(
            source=self.name,
            source_id=source_id,
            url=clean_url,
            title=title,
            description=raw_text,
            raw_text=raw_text,
            raw_price=raw_price,
            price=price_val,
            currency=currency,
            raw_year=raw_year,
            year=year_val,
            raw_mileage=raw_mileage,
            mileage=mileage_val,
            raw_engine=raw_engine,
            engine=raw_engine,
            raw_fuel=raw_fuel,
            fuel_type=raw_fuel,
            raw_transmission=raw_trans,
            transmission=raw_trans,
            raw_location=raw_location,
            location=raw_location,
            image_urls=photos[:10],
            images=photos[:10],
            extra_attributes={"card_source": "rst_ua"},
        )
