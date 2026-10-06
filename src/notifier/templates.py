"""
Telegram notification templates and HTML message formatting.
Generates structured Ukrainian captions for Audi A6 C5 listings,
incorporating specs, source badges, review warnings, price drop alerts,
and enforcing strict 1024 character limits for media group albums.
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, Optional, Union

from src.models.listing import Listing

SOURCE_BADGE_MAP: Dict[str, str] = {
    "auto_ria": "AUTO.RIA",
    "olx": "OLX",
    "rst": "RST.ua",
    "telegram": "Telegram",
    "instagram": "Instagram",
}

# Approximate exchange rates for dual currency display
USD_TO_UAH_RATE = 41.5
EUR_TO_USD_RATE = 1.08


def format_source_badge(source: Optional[str]) -> str:
    """Returns a standardized display name for marketplace sources."""
    if not source:
        return "Marketplace"
    src_key = source.lower().strip()
    return SOURCE_BADGE_MAP.get(src_key, source.replace("_", " ").title())


def format_price(
    price: Optional[float],
    currency: Optional[str] = "USD",
    price_usd: Optional[float] = None,
) -> str:
    """
    Formats price with dual-currency conversion (USD and UAH).
    Examples:
      - "$4,500 (~186,750 грн)"
      - "185,000 грн (~$4,458)"
      - "4,000 € (~$4,320)"
      - "Договірна" if not provided
    """
    if price is None and price_usd is None:
        return "Договірна"

    curr = (currency or "USD").upper().strip()
    if curr in ("$", "US"):
        curr = "USD"
    elif curr in ("€",):
        curr = "EUR"
    elif curr in ("ГРН", "ГРН."):
        curr = "UAH"

    # Base price calculation
    val = price if price is not None else price_usd
    if val is None:
        return "Договірна"

    if curr == "USD":
        uah_approx = int(round(val * USD_TO_UAH_RATE))
        return f"${val:,.0f} (~{uah_approx:,} грн)".replace(",", " ")

    if curr == "UAH":
        usd_approx = int(round(val / USD_TO_UAH_RATE))
        return f"{val:,.0f} грн (~${usd_approx:,})".replace(",", " ")

    if curr == "EUR":
        usd_approx = int(round(val * EUR_TO_USD_RATE))
        return f"{val:,.0f} € (~${usd_approx:,})".replace(",", " ")

    return f"{val:,.0f} {curr}".replace(",", " ")


def format_mileage(mileage: Optional[int]) -> str:
    """Formats odometer reading with kilometer units."""
    if mileage is not None and mileage >= 0:
        return f"{mileage:,} км".replace(",", " ")
    return "Не вказано"


def format_price_drop_badge(
    old_price_usd: float,
    new_price_usd: float,
    diff_usd: Optional[float] = None,
) -> str:
    """
    Generates a high-visibility price drop banner.
    Highlights original price, new price, dollar savings, and discount percentage.
    """
    diff = diff_usd if diff_usd is not None else (new_price_usd - old_price_usd)
    abs_diff = abs(diff)
    discount_pct = (abs_diff / old_price_usd * 100) if old_price_usd > 0 else 0

    diff_str = f"-${abs_diff:,.0f}".replace(",", " ")
    old_str = f"${old_price_usd:,.0f}".replace(",", " ")
    new_str = f"${new_price_usd:,.0f}".replace(",", " ")

    return (
        f"📉 <b>ЦІНУ ЗНИЖЕНО!</b> ({diff_str}, -{discount_pct:.0f}%)\n"
        f"💵 Стара ціна: {old_str} ➔ Нова: <b>{new_str}</b>"
    )


def format_market_badge(price_usd: Optional[float], avg_price_usd: Optional[float]) -> Optional[str]:
    """Generates hot deal banner if price is >= 15% below market average."""
    if not price_usd or not avg_price_usd or avg_price_usd <= 0:
        return None
    diff = avg_price_usd - price_usd
    pct = (diff / avg_price_usd) * 100
    if pct >= 15:
        return f"🔥 <b>НИЗ РИНКУ!</b> (-${diff:,.0f}, на {pct:.0f}% дешевше ринку)"
    return None


def format_dealer_badge(ad_count: int) -> Optional[str]:
    """Badges sellers based on total active/historical listings count."""
    if ad_count >= 3:
        return f"⚠️ <b>ПЕРЕКУП / АВТОМАЙДАНЧИК</b> (оголошень продавця: {ad_count})"
    elif ad_count == 1:
        return "👤 <b>Приватний власник</b>"
    return None


def format_needs_review_badge(reason: Optional[str] = None) -> str:
    """Generates an explicit warning badge for listings requiring manual review."""
    if reason:
        clean_reason = html.escape(reason.strip())
        return f"⚠️ <b>ПОТРЕБУЄ ПЕРЕВІРКИ</b> ({clean_reason})"
    return "⚠️ <b>ПОТРЕБУЄ ПЕРЕВІРКИ</b>"


def truncate_caption(caption: str, max_length: int = 1024) -> str:
    """
    Safely truncates caption ensuring it does not exceed max_length characters
    while preserving valid structure.
    """
    if len(caption) <= max_length:
        return caption

    # Ensure hard limit
    return caption[:max_length]


def format_listing_caption(
    listing: Union[Listing, Dict[str, Any]],
    price_drop_info: Optional[Dict[str, Any]] = None,
    max_length: int = 1024,
) -> str:
    """
    Renders clean, HTML-formatted Ukrainian caption for Audi A6 C5 listing.

    Features:
      - Emojis and structured parameters: title, price, year, engine, gearbox, mileage, city, source badge, direct link.
      - Price drop notification banner if price_drop_info is present.
      - Needs review warning badge if listing status is 'NEEDS_REVIEW'.
      - Strict caption length guard (<=1024 characters for media group album compatibility).
    """
    # 1. Extract values
    if isinstance(listing, dict):
        title = listing.get("title") or "Audi A6 C5"
        year = listing.get("year")
        price = listing.get("price")
        currency = listing.get("currency") or "USD"
        price_usd = listing.get("price_usd")
        engine = listing.get("engine") or listing.get("engine_code") or "Не вказано"
        transmission = listing.get("transmission") or "Не вказано"
        mileage = listing.get("mileage")
        location = listing.get("location_city") or listing.get("location") or "Україна"
        source = listing.get("source") or "Marketplace"
        url = listing.get("url") or ""
        description = listing.get("description") or listing.get("raw_text")
        status = listing.get("status") or "PASS"
        review_reasons = listing.get("review_reasons") or []
    else:
        title = listing.title or "Audi A6 C5"
        year = listing.year
        price = listing.price
        currency = listing.currency or "USD"
        price_usd = listing.price_usd
        engine = listing.engine or listing.engine_code or "Не вказано"
        transmission = listing.transmission or "Не вказано"
        mileage = listing.mileage
        location = listing.location_city or listing.location or "Україна"
        source = listing.source or "Marketplace"
        url = listing.url or ""
        description = listing.description
        status = getattr(listing, "status", "PASS")
        review_reasons = getattr(listing, "review_reasons", [])

    # 2. Escape HTML dynamic content
    safe_title = html.escape(str(title).strip())
    safe_engine = html.escape(str(engine).strip())
    safe_trans = html.escape(str(transmission).strip())
    safe_location = html.escape(str(location).strip())
    safe_source = html.escape(format_source_badge(str(source)))
    safe_url = html.escape(str(url).strip())

    formatted_price = format_price(price=price, currency=currency, price_usd=price_usd)
    formatted_mileage = format_mileage(mileage)

    # 3. Build Header
    year_str = f" ({year})" if year else ""
    header_lines = [f"🚗 <b>{safe_title}</b>{year_str}"]

    # Price drop banner if applicable
    if price_drop_info and price_drop_info.get("is_price_drop"):
        old_p = price_drop_info.get("old_price_usd", 0.0)
        new_p = price_drop_info.get("new_price_usd", price_usd or 0.0)
        diff_p = price_drop_info.get("diff_usd")
        header_lines.append(format_price_drop_badge(old_p, new_p, diff_p))

    # Market low price badge if applicable
    avg_p = price_drop_info.get("avg_price_usd") if price_drop_info else None
    market_badge = format_market_badge(price_usd, avg_p)
    if market_badge:
        header_lines.append(market_badge)

    # Dealer / private owner badge
    dealer_count = price_drop_info.get("seller_ad_count") if price_drop_info else None
    if dealer_count is not None:
        dealer_badge = format_dealer_badge(dealer_count)
        if dealer_badge:
            header_lines.append(dealer_badge)

    # Review needed badge if applicable
    if str(status).upper() == "NEEDS_REVIEW":
        reason_txt = ", ".join(review_reasons) if review_reasons else None
        header_lines.append(format_needs_review_badge(reason_txt))

    header = "\n".join(header_lines) + "\n\n"

    # Drive & Body string
    drive_type = getattr(listing, "drive_type", None) or (listing.get("drive_type") if isinstance(listing, dict) else None)
    body_type = getattr(listing, "body_type", None) or (listing.get("body_type") if isinstance(listing, dict) else None)
    
    drive_label = "Quattro (4x4) ⚡️" if drive_type == "quattro" else ("Передній" if drive_type == "front" else None)
    body_label = "Універсал (Avant) 🚙" if body_type == "avant" else ("Седан 🏎" if body_type == "sedan" else None)

    # 4. Build Specs Body
    body_lines = [
        f"💰 <b>Ціна:</b> {formatted_price}",
    ]
    if year:
        body_lines.append(f"📅 <b>Рік:</b> {year}")
    body_lines.append(f"⚙️ <b>Двигун:</b> {safe_engine}")
    body_lines.append(f"🕹️ <b>КПП:</b> {safe_trans}")
    if drive_label:
        body_lines.append(f"⚡️ <b>Привід:</b> {drive_label}")
    if body_label:
        body_lines.append(f"🚙 <b>Кузов:</b> {body_label}")
    body_lines.extend([
        f"🛣️ <b>Пробіг:</b> {formatted_mileage}",
        f"📍 <b>Місто:</b> {safe_location}",
        f"🏷️ <b>Джерело:</b> {safe_source}",
        f"🔗 <a href=\"{safe_url}\">Посилання на оголошення</a>",
    ])
    body = "\n".join(body_lines)

    # 5. Build Description Block with Strict Length Budget
    fixed_content = header + body
    remaining_budget = max_length - len(fixed_content)

    desc_block = ""
    if description and remaining_budget > 40:
        desc_prefix = "\n\n📝 <b>Опис:</b>\n"
        max_desc_len = remaining_budget - len(desc_prefix) - 5  # Room for "..."
        if max_desc_len > 20:
            clean_desc = description.strip()
            if len(clean_desc) > max_desc_len:
                # Slice raw text first, then escape to prevent cutting HTML tags
                clean_desc = clean_desc[:max_desc_len].rstrip() + "..."
            desc_block = f"{desc_prefix}{html.escape(clean_desc)}"

    full_caption = fixed_content + desc_block

    # 6. Safety check: ensure caption strictly fits max_length
    if len(full_caption) > max_length:
        if desc_block:
            # Drop description block completely if it causes an overflow
            full_caption = fixed_content
        if len(full_caption) > max_length:
            # Extreme fallback: truncate title safely
            excess = len(full_caption) - max_length + 3
            truncated_title = safe_title[:-excess] + "..." if len(safe_title) > excess else safe_title[:20]
            header_lines[0] = f"🚗 <b>{truncated_title}</b>{year_str}"
            header = "\n".join(header_lines) + "\n\n"
            full_caption = (header + body)[:max_length]

    return full_caption
