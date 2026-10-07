"""
User-specific filter evaluator for Audi A6 C5 monitoring.
Evaluates listings against personalized price, engine, mileage, transmission,
and condition preferences saved in the user's profile.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional, Union

from src.database.models import ListingModel, UserFilterModel
from src.models.listing import Listing, RawListingPayload

logger = logging.getLogger(__name__)

DAMAGED_KEYWORDS = [
    "дтп",
    "після дтп",
    "после дтп",
    "битий",
    "бита",
    "битое",
    "битый",
    "не на ходу",
    "не на ходу",
    "не заводиться",
    "не заводится",
    "під відновлення",
    "под восстановление",
    "пошкоджений",
    "пошкодження",
    "на запчастини",
    "на запчасти",
    "донор",
    "розбірка",
    "разборка",
]


def matches_user_filter(
    listing: Union[Listing, RawListingPayload, ListingModel, Any],
    user_filter: Optional[UserFilterModel] = None,
) -> bool:
    """
    Checks if a listing satisfies all custom preferences of the user.
    Returns True if matches, False otherwise.
    """
    if user_filter is None:
        return True

    # 0. Model & Generation check
    selected_models = getattr(user_filter, "selected_models", None)
    if selected_models:
        l_model = (getattr(listing, "model", None) or "").strip().upper()
        l_gen = (getattr(listing, "generation", None) or "").strip().upper()
        l_title = (getattr(listing, "title", None) or "").strip().upper()
        l_combo = f"{l_model} {l_gen}".strip()

        matched_any_model = False
        for sm in selected_models:
            sm_clean = sm.strip().upper()
            if sm_clean in l_combo or l_combo in sm_clean or sm_clean in l_title:
                matched_any_model = True
                break
            parts = sm_clean.split()
            if len(parts) == 2 and parts[0] in l_title and parts[1] in l_title:
                matched_any_model = True
                break
        if not matched_any_model:
            return False

    # 1. Price check
    price = getattr(listing, "price_usd", None)
    if price is None:
        price = getattr(listing, "price", None)
    if price is not None:
        try:
            price_val = float(price)
            if user_filter.min_price is not None and price_val < user_filter.min_price:
                return False
            if user_filter.max_price is not None and price_val > user_filter.max_price:
                return False
        except (ValueError, TypeError):
            pass

    # 2. Year check
    year = getattr(listing, "year", None)
    if year is not None:
        try:
            year_val = int(year)
            if user_filter.min_year is not None and year_val < user_filter.min_year:
                return False
            if user_filter.max_year is not None and year_val > user_filter.max_year:
                return False
        except (ValueError, TypeError):
            pass

    # 3. Mileage check
    mileage = getattr(listing, "mileage", None)
    if mileage is not None and user_filter.max_mileage is not None:
        try:
            mil_val = int(mileage)
            if mil_val > user_filter.max_mileage:
                return False
        except (ValueError, TypeError):
            pass

    # 4. Transmission check
    trans = (getattr(listing, "transmission", None) or "").lower()
    if user_filter.transmission == "manual":
        if trans and not any(m in trans for m in ["механ", "manual", "ручн"]):
            return False
    elif user_filter.transmission == "automatic":
        if trans and not any(a in trans for a in ["автомат", "auto", "типтрон", "варіат"]):
            return False

    # 4.1 Drive type check (Quattro vs Front)
    target_drive = getattr(user_filter, "drive_type", "any") or "any"
    if target_drive != "any":
        l_drive = (getattr(listing, "drive_type", None) or "").lower()
        title_l = (getattr(listing, "title", None) or "").lower()
        desc_l = (getattr(listing, "description", None) or getattr(listing, "raw_text", None) or "").lower()
        full_d = f"{l_drive} {title_l} {desc_l}"
        is_quattro = any(q in full_d for q in ["quattro", "кватро", "квадро", "повний", "полный", "4x4", "4wd"])
        if target_drive == "quattro" and not is_quattro:
            return False
        elif target_drive == "front" and is_quattro:
            return False

    # 4.2 Body type check (Sedan vs Avant)
    target_body = getattr(user_filter, "body_type", "any") or "any"
    if target_body != "any":
        l_body = (getattr(listing, "body_type", None) or "").lower()
        title_b = (getattr(listing, "title", None) or "").lower()
        desc_b = (getattr(listing, "description", None) or getattr(listing, "raw_text", None) or "").lower()
        full_b = f"{l_body} {title_b} {desc_b}"
        is_avant = any(av in full_b for av in ["avant", "авант", "універсал", "универсал", "wagon"])
        if target_body == "avant" and not is_avant:
            return False
        elif target_body == "sedan" and is_avant:
            return False

    # 5. Engine check
    active_engines = user_filter.engines or ["1.8T", "2.4", "1.9 TDI"]
    raw_engine = (getattr(listing, "engine", None) or getattr(listing, "engine_code", None) or "").lower()
    title = (getattr(listing, "title", None) or "").lower()
    desc = (getattr(listing, "description", None) or getattr(listing, "raw_text", None) or "").lower()
    full_text = f"{raw_engine} {title} {desc}"

    engine_matched = False
    for eng in active_engines:
        if eng == "1.8T":
            if "1.8" in full_text and any(t in full_text for t in ["1.8t", "1.8 turbo", "1.8 турбо", "1.8 т", "1.8т", "1.8t"]):
                engine_matched = True
                break
            if "1.8" in raw_engine:
                engine_matched = True
                break
        elif eng == "2.4":
            if "2.4" in full_text or "2.39" in full_text:
                engine_matched = True
                break
        elif eng == "1.9 TDI":
            if ("1.9" in full_text or "1.89" in full_text) and any(d in full_text for d in ["tdi", "тди", "дизель", "дизель"]):
                engine_matched = True
                break
            if "1.9" in raw_engine:
                engine_matched = True
                break

    if not engine_matched and active_engines:
        return False

    # 6. Condition / Damaged check
    if user_filter.exclude_damaged:
        text_to_check = f"{title} {desc}"
        for kw in DAMAGED_KEYWORDS:
            if re.search(rf"\b{re.escape(kw)}\b", text_to_check, re.IGNORECASE):
                return False

    return True
