"""
OLX Ukraine scraper for Audi A6 passenger cars.
Supports dual-mode extraction: embedded __PRERENDERED_STATE__ JSON state
with graceful fallback to HTML DOM parsing.
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional
from bs4 import BeautifulSoup

from src.models.listing import RawListingPayload
from src.parsers.base import BaseParser

logger = logging.getLogger(__name__)


class OlxParser(BaseParser):
    """
    Scraper for OLX Ukraine passenger car listings for Audi A6 (1997-2005).
    """

    def __init__(
        self,
        base_url: str = "https://www.olx.ua/d/uk/transport/legkovye-avtomobili/audi/a6/",
        max_pages: int = 1,
        request_delay: float = 2.0,
        timeout: float = 20.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="olx",
            base_url=base_url,
            request_delay=request_delay,
            timeout=timeout,
            **kwargs,
        )
        self.max_pages = max(1, max_pages)

    async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]:
        """
        Scrapes OLX search results, first attempting JSON state extraction,
        falling back to DOM parsing if needed.
        """
        self.stats.start()

        for page in range(1, self.max_pages + 1):
            params: Dict[str, Any] = {
                "search[filter_float_year:from]": "1997",
                "search[filter_float_year:to]": "2005",
                "search[order]": "created_at:desc",
            }
            if page > 1:
                params["page"] = str(page)

            try:
                html = await self.fetch_html_with_retry(self.base_url, params=params)
                if not html:
                    logger.warning("[olx] No HTML returned for page %d", page)
                    if page == 1:
                        self.stats.finish(status="FAILED")
                        return
                    continue

                # Strategy 1: Attempt JSON State Extraction
                extracted_any = False
                async for payload in self._parse_json_state(html):
                    extracted_any = True
                    self.stats.record_fetched()
                    self.stats.record_valid()
                    yield payload

                # Strategy 2: If JSON State did not yield items, fallback to DOM
                if not extracted_any:
                    logger.info("[olx] JSON state not present or yielded 0 items, using DOM fallback on page %d", page)
                    async for payload in self._parse_dom(html):
                        self.stats.record_fetched()
                        self.stats.record_valid()
                        yield payload

            except Exception as page_err:
                self.stats.record_error()
                logger.error("[olx] Error fetching page %d: %s", page, page_err, exc_info=True)
                continue

        self.stats.finish()

    async def _parse_json_state(self, html: str) -> AsyncGenerator[RawListingPayload, None]:
        """Extract listings from window.__PRERENDERED_STATE__ or script tag."""
        patterns = [
            r"window\.__PRERENDERED_STATE__\s*=\s*(\"\{.*?\}\"|\{.*?\});",
            r"<script id=\"olx-init-config\"[^>]*>(\{.*?\})</script>",
            r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});",
        ]

        data: Optional[Dict[str, Any]] = None
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                raw_json = match.group(1).strip()
                try:
                    if raw_json.startswith('"') and raw_json.endswith('"'):
                        raw_json = json.loads(raw_json)  # unescape stringified JSON
                    parsed = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
                    if isinstance(parsed, dict):
                        data = parsed
                        break
                except Exception as json_err:
                    logger.debug("[olx] Pattern match failed to parse as JSON: %s", json_err)

        if not data:
            return

        ads: List[Dict[str, Any]] = []
        # Navigate standard OLX nested dictionary structures
        if "listing" in data and isinstance(data["listing"], dict):
            sub = data["listing"]
            if "listing" in sub and isinstance(sub["listing"], dict):
                ads = sub["listing"].get("ads", [])
            elif "ads" in sub:
                ads = sub.get("ads", [])
        elif "ads" in data:
            ads = data.get("ads", [])

        for ad in ads:
            try:
                raw_id = ad.get("id")
                if raw_id is None or str(raw_id).strip() in ("", "None"):
                    continue
                ad_id = str(raw_id).strip()

                url = ad.get("url", "")
                if url and not url.startswith("http"):
                    url = f"https://www.olx.ua{url}"
                clean_url = url.split("?")[0] if url else f"https://www.olx.ua/d/uk/obyavlenie/{ad_id}.html"

                title = ad.get("title", "Audi A6")
                description = ad.get("description", "")

                price_info = ad.get("price", {})
                price_val: Optional[float] = None
                raw_price: Optional[str] = None
                currency = "USD"
                if price_info:
                    val = price_info.get("value")
                    curr = price_info.get("currency", "USD")
                    currency = curr
                    if val is not None:
                        try:
                            price_val = float(val)
                            raw_price = f"{val} {curr}"
                        except (ValueError, TypeError):
                            raw_price = str(val)

                # Params map
                raw_params = ad.get("params") or []
                params_map: Dict[str, str] = {}
                for p in raw_params:
                    if not isinstance(p, dict):
                        continue
                    k = p.get("key")
                    v_dict = p.get("value", {})
                    v_label = v_dict.get("label") if isinstance(v_dict, dict) else str(v_dict)
                    if k and v_label:
                        params_map[k] = v_label

                raw_year = params_map.get("year")
                year_val = int(raw_year) if raw_year and raw_year.isdigit() else None
                if not year_val:
                    year_match = re.search(r"\b(199[7-9]|200[0-5])\b", title)
                    if year_match:
                        year_val = int(year_match.group(1))
                        raw_year = str(year_val)

                raw_mileage = params_map.get("milage") or params_map.get("mileage")
                mileage_val: Optional[int] = None
                if raw_mileage:
                    cleaned_m = re.sub(r"[^\d]", "", raw_mileage)
                    if cleaned_m:
                        mileage_val = int(cleaned_m)

                raw_engine = params_map.get("engine_capacity")
                raw_fuel = params_map.get("fuel_type")
                raw_trans = params_map.get("transmission")

                # Photos formatting
                photos: List[str] = []
                for p in (ad.get("photos") or []):
                    if not isinstance(p, dict):
                        continue
                    link = p.get("link", "")
                    if link:
                        formatted_link = link.replace("{width}x{height}", "1000x700")
                        photos.append(formatted_link)

                # Location
                location_name: Optional[str] = None
                loc_dict = ad.get("location", {})
                if isinstance(loc_dict, dict):
                    city_info = loc_dict.get("city", {})
                    location_name = city_info.get("name") if isinstance(city_info, dict) else str(loc_dict.get("city"))

                # Publication timestamp
                published_dt: Optional[datetime] = None
                created_time = ad.get("created_time")
                if created_time:
                    try:
                        published_dt = datetime.fromisoformat(created_time)
                    except Exception:
                        pass

                yield RawListingPayload(
                    source=self.name,
                    source_id=ad_id,
                    url=clean_url,
                    title=title,
                    description=description,
                    raw_text=description,
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
                    raw_location=location_name,
                    location=location_name,
                    image_urls=photos[:10],
                    images=photos[:10],
                    published_at=published_dt,
                    extra_attributes={"extraction_mode": "prerendered_json"},
                )
            except Exception as ad_err:
                self.stats.record_error()
                logger.error("[olx] Error parsing ad from JSON state: %s", ad_err, exc_info=False)
                continue

    async def _parse_dom(self, html: str) -> AsyncGenerator[RawListingPayload, None]:
        """DOM parsing fallback for OLX card markup."""
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select('div[data-cy="l-card"]')

        for card in cards:
            try:
                link = card.select_one('a[href*="/d/uk/obyavlenie/"], a[href*="/d/obyavlenie/"], a.css-z3gu2d')
                if not link or not link.get("href"):
                    continue

                href = link["href"]
                full_url = href if href.startswith("http") else f"https://www.olx.ua{href}"
                clean_url = full_url.split("?")[0]

                id_match = re.search(r"-ID([A-Za-z0-9]+)\.html", clean_url)
                source_id = id_match.group(1) if id_match else clean_url

                title_elem = card.select_one("h6, .css-16v5mdi")
                title = title_elem.get_text(strip=True) if title_elem else link.get_text(strip=True)

                price_elem = card.select_one('[data-testid="ad-price"], .css-10b0gli')
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

                loc_elem = card.select_one('[data-testid="location-date"], .css-veheph')
                raw_loc = loc_elem.get_text(strip=True) if loc_elem else None
                loc_name = raw_loc.split("-")[0].strip() if raw_loc else None

                photos: List[str] = []
                img = card.select_one("img[src]")
                if img and img.get("src") and "http" in img["src"]:
                    photos.append(img["src"])

                # Attempt to extract year from title
                year_val: Optional[int] = None
                raw_year: Optional[str] = None
                year_match = re.search(r"\b(199[7-9]|200[0-5])\b", title)
                if year_match:
                    year_val = int(year_match.group(1))
                    raw_year = year_match.group(1)

                yield RawListingPayload(
                    source=self.name,
                    source_id=str(source_id),
                    url=clean_url,
                    title=title,
                    description=title,
                    raw_text=title,
                    raw_price=raw_price,
                    price=price_val,
                    currency=currency,
                    raw_year=raw_year,
                    year=year_val,
                    raw_location=loc_name,
                    location=loc_name,
                    image_urls=photos[:10],
                    images=photos[:10],
                    extra_attributes={"extraction_mode": "dom_fallback"},
                )
            except Exception as card_err:
                self.stats.record_error()
                logger.error("[olx] Error parsing DOM card: %s", card_err, exc_info=False)
                continue
