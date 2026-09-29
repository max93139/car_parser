"""
Model and generation validation rules for Audi A6 C5 / 4B (1997-2005).

Enforces:
- Strict Audi brand verification
- Strict A6 model verification (rejection of A4, A8, A3, TT, Q-series, 80, 100)
- Strict C5 / Typ 4B generation verification and rejection of C4, C6, C7, C8
- Disambiguation of transition years: 1997, 2004, 2005
- Recognition of Ukrainian / Russian vernacular slang ("горбатая", "капля", "черепаха")
"""

from __future__ import annotations

import re
from typing import Optional, Tuple, List


# Audi brand patterns
RE_AUDI_BRAND = re.compile(r"\b(?:audi|ауд[иі])\b", re.IGNORECASE)

# Positive Audi A6 model pattern
RE_AUDI_A6 = re.compile(r"\b(?:audi\s*)?[aа]6\b|\bауд[иі]\s*а6\b", re.IGNORECASE)

# Vernacular / chassis synonyms for generation C5
RE_C5_GENERATION = re.compile(
    r"\b(?:[cс]5|4[bв]|горбат\w*|капл\w*|черепах\w*|черепас\w*)\b",
    re.IGNORECASE,
)

# Conflicting Audi models to reject
RE_WRONG_MODEL_A4 = re.compile(r"\b(?:audi\s*)?[aа]4\b|\ba4\b|\bа4\b", re.IGNORECASE)
RE_WRONG_MODEL_A8 = re.compile(r"\b(?:audi\s*)?[aа]8\b|\ba8\b|\bа8\b", re.IGNORECASE)
RE_WRONG_MODEL_OTHER = re.compile(
    r"\b(?:audi\s*)?(?:[aа][12357]|q[3578]|tt|80|100|s4|s8|rs4)\b",
    re.IGNORECASE,
)

# Conflicting Audi generations to reject
RE_GENERATION_C4 = re.compile(r"\b(?:[cс]4|4[aа]|typ\s*4a)\b", re.IGNORECASE)
RE_GENERATION_C6 = re.compile(r"\b(?:[cс]6|4[fф]|typ\s*4f)\b", re.IGNORECASE)
RE_GENERATION_C7 = re.compile(r"\b(?:[cс]7|typ\s*4g)\b", re.IGNORECASE)
RE_GENERATION_C8 = re.compile(r"\b(?:[cс]8|typ\s*4k)\b", re.IGNORECASE)

# Non-Audi makes starting ad title (unless sold car is Audi)
RE_NON_AUDI_TITLE_START = re.compile(
    r"^(?:bmw|passat|volkswagen|vw|mercedes|opel|skoda|ford|renault|peugeot)\b",
    re.IGNORECASE,
)

# Body styles
RE_BODY_AVANT = re.compile(
    r"\b(?:avant|авант|универсал|універсал|комби|station\s*wagon|wagon)\b",
    re.IGNORECASE,
)
RE_BODY_SEDAN = re.compile(r"\b(?:sedan|седан|лимузин)\b", re.IGNORECASE)

# Year extractor from text
RE_YEAR = re.compile(r"\b(199\d|200\d|201\d|202\d)\b")


def extract_year(
    structured_year: Optional[int],
    text: str,
) -> Optional[int]:
    """
    Returns structured year if valid, or extracts 4-digit year from text.
    """
    if structured_year and 1980 <= structured_year <= 2030:
        return structured_year

    match = RE_YEAR.search(text)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return None


def validate_brand_and_model(
    structured_brand: Optional[str],
    structured_model: Optional[str],
    title: str,
    full_text: str,
) -> Tuple[bool, Optional[str]]:
    """
    Validates that the vehicle is an Audi A6.
    Returns (is_valid, rejection_reason).
    """
    # 1. Structured Brand Check
    if structured_brand:
        b = structured_brand.strip().lower()
        if b not in ("audi", "ауди", "ауді"):
            return False, "WRONG_BRAND"

    # 2. Structured Model Check
    if structured_model:
        m = structured_model.strip().upper()
        if m in ("A4", "А4"):
            return False, "WRONG_MODEL_A4"
        if m in ("A8", "А8"):
            return False, "WRONG_MODEL_A8"
        if m not in ("A6", "А6", "ALLROAD", "UNKNOWN"):
            return False, f"WRONG_MODEL_{m}"

    # 3. Non-Audi make heading the title
    if RE_NON_AUDI_TITLE_START.search(title):
        # Unless Audi A6 is explicitly stated as the sold car
        if not (RE_AUDI_A6.search(title) and not ("на" in title.lower())):
            return False, "NOT_AUDI_A6"

    # 4. Check for conflicting model mentions in title when A6 is absent
    has_a6_in_title = bool(RE_AUDI_A6.search(title))
    has_c5_in_title = bool(RE_C5_GENERATION.search(title))

    if not has_a6_in_title and not has_c5_in_title:
        if RE_WRONG_MODEL_A4.search(title):
            return False, "WRONG_MODEL_A4"
        if RE_WRONG_MODEL_A8.search(title):
            return False, "WRONG_MODEL_A8"
        if RE_WRONG_MODEL_OTHER.search(title):
            return False, "WRONG_MODEL"

    # 5. Check if title or full text has A6 or C5 indicator
    has_a6 = bool(RE_AUDI_A6.search(full_text))
    has_c5 = bool(RE_C5_GENERATION.search(full_text))

    if not has_a6 and not has_c5:
        # Check structured model
        if structured_model and structured_model.strip().upper() in ("A6", "А6"):
            pass
        else:
            return False, "NOT_AUDI_A6"

    # 6. Title mentions A4 or A8 as primary entity
    if not has_a6_in_title:
        if RE_WRONG_MODEL_A4.search(title):
            return False, "WRONG_MODEL_A4"
        if RE_WRONG_MODEL_A8.search(title):
            return False, "WRONG_MODEL_A8"

    return True, None


def check_conflicting_generations(
    full_text: str,
) -> Optional[str]:
    """
    Checks if text explicitly specifies a conflicting generation (C4, C6, C7, C8).
    """
    if RE_GENERATION_C4.search(full_text):
        return "GENERATION_C4"
    if RE_GENERATION_C6.search(full_text):
        return "GENERATION_C6"
    if RE_GENERATION_C7.search(full_text):
        return "GENERATION_C7"
    if RE_GENERATION_C8.search(full_text):
        return "GENERATION_C8"
    return None


def is_c5_explicit(full_text: str) -> bool:
    """
    Returns True if C5, 4B, or vernacular slang is explicitly present.
    """
    return bool(RE_C5_GENERATION.search(full_text))


def is_avant_body(
    body_type: Optional[str],
    full_text: str,
) -> bool:
    """
    Returns True if body is Avant / station wagon.
    """
    if body_type:
        bt = body_type.lower()
        if "avant" in bt or "универсал" in bt or "універсал" in bt or "wagon" in bt:
            return True
    return bool(RE_BODY_AVANT.search(full_text))


def is_sedan_body(
    body_type: Optional[str],
    full_text: str,
) -> bool:
    """
    Returns True if body is sedan.
    """
    if body_type:
        bt = body_type.lower()
        if "sedan" in bt or "седан" in bt:
            return True
    return bool(RE_BODY_SEDAN.search(full_text))
