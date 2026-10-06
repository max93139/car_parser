"""
AUTO.RIA scraper for Audi A6 C5 (1997–2005) listings.
Extracts structured vehicle data, prices (USD/UAH/EUR), and high-resolution photo URLs.
"""

from __future__ import annotations

import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional
from bs4 import BeautifulSoup

from src.models.listing import RawListingPayload
from src.parsers.base import BaseParser

logger = logging.getLogger(__name__)


class AutoRiaParser(BaseParser):
    """
    Scraper for AUTO.RIA search results for Audi A6 C5 generation (1997-2005).
    """

    def __init__(
        self,
        base_url: str = "https://auto.ria.com/uk/search/",
        max_pages: int = 1,
        request_delay: float = 1.5,
        timeout: float = 15.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="auto_ria",
            base_url=base_url,
            request_delay=request_delay,
            timeout=timeout,
            **kwargs,
        )
        self.max_pages = max(1, max_pages)

    async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]:
        """
        Iterates through configured search result pages and yields RawListingPayloads.
        """
        self.stats.start()

        for page in range(1, self.max_pages + 1):
            params: Dict[str, Any] = {
                "categories.main.id": "1",  # Passenger cars
                "brand.id[0]": "6",         # Audi
                "model.id[0]": "49",        # A6
                "model.id[1]": "39",        # A4
                "year[0].gte": "1994",
                "year[0].lte": "2015",
                "order_by": "2",            # Newest first
                "page": str(page),
            }

            try:
                html = await self.fetch_html_with_retry(self.base_url, params=params)
                if not html:
                    logger.warning("[auto_ria] No HTML content returned for page %d", page)
                    if page == 1:
                        self.stats.finish(status="FAILED")
                        return
                    continue

                soup = BeautifulSoup(html, "html.parser")
                ticket_items = soup.select("section.ticket-item, div.ticket-item, a.product-card")

                if not ticket_items:
                    logger.info("[auto_ria] No tickets found on page %d", page)
                    break

                for ticket in ticket_items:
                    self.stats.record_fetched()
                    try:
                        payload = self._parse_ticket(ticket)
                        if payload:
                            self.stats.record_valid()
                            yield payload
                    except Exception as item_err:
                        self.stats.record_error()
                        logger.error("[auto_ria] Error parsing listing card: %s", item_err, exc_info=False)
                        continue

            except Exception as page_err:
                self.stats.record_error()
                logger.error("[auto_ria] Error fetching page %d: %s", page, page_err, exc_info=True)
                continue

        self.stats.finish()

    def _parse_ticket(self, ticket: Any) -> Optional[RawListingPayload]:
        """Extract RawListingPayload from a single ticket HTML element."""
        # 1. Source ID
        auto_id = ticket.get("data-auto-id") or ticket.get("data-id")
        link_elem = ticket.select_one("a.address, a.m-link-ticket")
        if not link_elem and ticket.name == "a" and ticket.get("href"):
            link_elem = ticket

        if not auto_id and link_elem and link_elem.get("href"):
            id_match = re.search(r"_(\d+)\.html", link_elem["href"])
            if id_match:
                auto_id = id_match.group(1)

        if not auto_id:
            return None

        # 2. Canonical URL & Title
        raw_url = link_elem["href"] if link_elem and link_elem.get("href") else f"https://auto.ria.com/uk/auto_audi_a6_{auto_id}.html"
        if raw_url.startswith("/"):
            raw_url = f"https://auto.ria.com{raw_url}"
        url = raw_url.split("?")[0].split("#")[0]
        
        title_elem = ticket.select_one("div[class*='titleS'], a.address, a.m-link-ticket")
        title = title_elem.get_text(strip=True) if title_elem else (link_elem.get_text(strip=True) if link_elem else "Audi")
        if not title or len(title) > 80:
            title = "Audi"

        # Detect model from title or link
        model_name = "A6"
        if re.search(r"\b[aа]4\b", title, re.IGNORECASE) or (link_elem and "audi_a4" in link_elem.get("href", "")):
            model_name = "A4"
        elif re.search(r"\b[aа]6\b", title, re.IGNORECASE) or (link_elem and "audi_a6" in link_elem.get("href", "")):
            model_name = "A6"

        # 3. Price & Currency
        price_val: Optional[float] = None
        currency: str = "USD"
        raw_price_str: Optional[str] = None

        usd_elem = ticket.select_one('[data-currency="USD"], span[class*="c-green"]')
        if usd_elem:
            raw_price_str = usd_elem.get_text(strip=True)
            currency = "USD"
            nums = re.sub(r"[^\d.]", "", raw_price_str.replace(" ", "").replace("\xa0", ""))
            if nums:
                price_val = float(nums)
        else:
            price_elem = ticket.select_one(".price-ticket, .size22")
            if price_elem:
                raw_price_str = price_elem.get_text(strip=True)
                if "грн" in raw_price_str.lower():
                    currency = "UAH"
                elif "€" in raw_price_str:
                    currency = "EUR"
                nums = re.sub(r"[^\d.]", "", raw_price_str.replace(" ", "").replace("\xa0", ""))
                if nums:
                    price_val = float(nums)

        # 4. Characteristics list
        char_items: List[str] = [
            li.get_text(" ", strip=True)
            for li in ticket.select("ul.characteristic li, div.item-char, div.grid-wrapper span, div.grid-wrapper div")
            if li.get_text(strip=True)
        ]
        raw_text_summary = " | ".join(char_items)

        # 5. Mileage
        raw_mileage: Optional[str] = None
        mileage_val: Optional[int] = None
        for c in char_items:
            if "тис" in c or "км" in c:
                raw_mileage = c
                match = re.search(r"(\d+(?:[\s.,]\d+)?)\s*тис", c)
                if match:
                    val_str = match.group(1).replace(" ", "").replace(",", ".")
                    try:
                        mileage_val = int(float(val_str) * 1000)
                    except ValueError:
                        pass
                else:
                    match_direct = re.search(r"(\d[\d\s]+)\s*км", c)
                    if match_direct:
                        val_str = match_direct.group(1).replace(" ", "")
                        try:
                            mileage_val = int(val_str)
                        except ValueError:
                            pass
                break

        # 6. Engine & Fuel
        raw_engine: Optional[str] = None
        raw_fuel: Optional[str] = None
        for c in char_items:
            c_low = c.lower()
            if any(k in c_low for k in ["л.", " л", "дизель", "бензин", "газ", "tdi"]):
                raw_engine = c
                if "2.39" in c or "2.4" in c:
                    raw_engine = f"2.4 {c}"
                elif "1.78" in c or "1.8" in c:
                    raw_engine = f"1.8T {c}"
                elif "1.89" in c or "1.9" in c:
                    raw_engine = f"1.9 TDI {c}"

                if "дизель" in c_low or "tdi" in c_low:
                    raw_fuel = "дизель"
                elif "газ" in c_low and "бензин" in c_low:
                    raw_fuel = "газ/бензин"
                elif "бензин" in c_low:
                    raw_fuel = "бензин"
                break

        # 7. Transmission
        raw_trans: Optional[str] = None
        for c in char_items:
            if any(t in c.lower() for t in ["механ", "автомат", "ручна", "тіптронік", "варіатор"]):
                raw_trans = c
                break

        # 8. Location / City
        raw_location: Optional[str] = None
        for c in char_items:
            if c != raw_mileage and c != raw_engine and c != raw_trans:
                # Typically city names or region
                if any(city in c for city in ["Київ", "Львів", "Одеса", "Дніпро", "Харків", "Рівне", "Луцьк", "Вінниця", "обл"]):
                    raw_location = c
                    break
        if not raw_location and len(char_items) >= 2:
            # Check 2nd item which is commonly city in AUTO.RIA layout
            raw_location = char_items[1]

        # 9. Year & Generation
        year_val: Optional[int] = None
        raw_year: Optional[str] = None
        year_match = re.search(r"\b(199\d|20[0-2]\d)\b", title) or re.search(r"\b(199\d|20[0-2]\d)\b", ticket.get_text())
        if year_match:
            year_val = int(year_match.group(1))
            raw_year = year_match.group(1)
            if str(year_val) not in title:
                title = f"{title} {year_val}"

        # Detect generation based on model and year / text
        gen_val = "C5" if model_name == "A6" else "B6"
        full_card_text = f"{title} {raw_text_summary}".upper()
        if model_name == "A6":
            if "C4" in full_card_text or (year_val and year_val < 1997):
                gen_val = "C4"
            elif "C5" in full_card_text or (year_val and 1997 <= year_val <= 2004):
                gen_val = "C5"
            elif "C6" in full_card_text or (year_val and 2004 <= year_val <= 2011):
                gen_val = "C6"
            elif "C7" in full_card_text or (year_val and 2011 <= year_val <= 2015):
                gen_val = "C7"
            if year_val and year_val > 2015:
                year_val = 2015
        elif model_name == "A4":
            if "B5" in full_card_text or (year_val and year_val < 2001):
                gen_val = "B5"
            elif "B6" in full_card_text or (year_val and 2001 <= year_val <= 2004):
                gen_val = "B6"
            elif "B7" in full_card_text or (year_val and 2004 <= year_val <= 2008):
                gen_val = "B7"
            elif "B8" in full_card_text or (year_val and 2008 <= year_val <= 2015):
                gen_val = "B8"

        # 10. Drive type & Body type
        raw_drive = "front"
        raw_body = "sedan"
        full_text_detect = f"{title} {raw_text_summary}".lower()

        if any(q in full_text_detect for q in ["quattro", "кватро", "квадро", "повний", "полный", "4x4", "4wd"]):
            raw_drive = "quattro"
        elif any(f in full_text_detect for f in ["передній", "передний", "fwd"]):
            raw_drive = "front"

        if any(b in full_text_detect for b in ["avant", "авант", "універсал", "универсал", "wagon"]):
            raw_body = "avant"
        elif any(s in full_text_detect for s in ["седан", "sedan"]):
            raw_body = "sedan"

        # 11. High-res photos
        image_urls: List[str] = []
        img_tags = ticket.select("picture img, div.ticket-photo img, .preview-gallery picture img, img[data-src], img[src]")
        for img in img_tags:
            src = img.get("data-src") or img.get("src")
            if src and not src.endswith("nophoto.svg") and not src.startswith("data:"):
                if src.startswith("//"):
                    src = f"https:{src}"
                # Convert small or medium thumbnail to full resolution (s.jpg / m.jpg -> f.jpg)
                src_hd = re.sub(r"[sSmM]\.jpg$", "f.jpg", src)
                if src_hd not in image_urls:
                    image_urls.append(src_hd)

        return RawListingPayload(
            source=self.name,
            source_id=str(auto_id),
            url=url,
            title=title,
            raw_text=raw_text_summary,
            description=raw_text_summary,
            raw_price=raw_price_str,
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
            brand="Audi",
            model=model_name,
            generation=gen_val,
            body_type=raw_body,
            drive_type=raw_drive,
            image_urls=image_urls[:10],
            images=image_urls[:10],
            extra_attributes={"page": 1, "card_source": "auto_ria_search"},
        )
