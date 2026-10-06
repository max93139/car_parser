"""
Parser for flexible user-typed price inputs.
Handles ranges (3500-5500, 3k-5k), upper limits (до 5000, 4800$),
lower limits (від 3000), and resets (0, скинути, будь-яка).
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


def parse_price_input(text: str) -> Optional[Tuple[Optional[int], Optional[int]]]:
    """
    Parses user typed text into (min_price, max_price).
    Returns None if text does not contain recognized price patterns.
    """
    clean = text.strip().lower()
    if clean in ("0", "скинути", "скинь", "будь-яка", "будь яка", "люба", "все", "reset", "none"):
        return (None, None)

    # Normalize 'k' / 'к' (e.g. 3.5k -> 3500, 4k -> 4000)
    def repl_k(m: re.Match) -> str:
        num = float(m.group(1))
        return str(int(num * 1000))

    clean = re.sub(r"(\d+(?:\.\d+)?)\s*[kк]\b", repl_k, clean)

    # 1. Range with dash/slash/to (e.g. "3500-5500", "3000 - 6000$", "3000..5000")
    range_match = re.search(r"(\d{3,6})\s*(?:-|—|–|\.\.|\/|to|до)\s*(\d{3,6})", clean)
    if range_match:
        p1 = int(range_match.group(1))
        p2 = int(range_match.group(2))
        return (min(p1, p2), max(p1, p2))

    # 2. Two numbers separated by space (e.g. "3500 5500")
    two_nums = re.findall(r"\b(\d{3,6})\b", clean)
    if len(two_nums) == 2:
        p1 = int(two_nums[0])
        p2 = int(two_nums[1])
        return (min(p1, p2), max(p1, p2))

    # 3. "Від X" / "от X" / "> X"
    from_match = re.search(r"(?:від|от|>|\+)\s*(\d{3,6})", clean)
    if from_match:
        return (int(from_match.group(1)), None)

    # 4. "До X" / "< X"
    to_match = re.search(r"(?:до|<)\s*(\d{3,6})", clean)
    if to_match:
        return (None, int(to_match.group(1)))

    # 5. Single number (e.g. "5000", "4500$", "4800 usd") -> treat as max_price
    if len(two_nums) == 1:
        val = int(two_nums[0])
        return (None, val)

    return None


def parse_mileage_input(text: str) -> Optional[Tuple[bool, Optional[int]]]:
    """
    Parses user typed text into max_mileage.
    Returns (True, int_mileage) or (True, None) for reset, or None if not recognized.
    """
    clean = text.strip().lower()
    if clean in ("0", "скинути", "скинь", "будь-який", "будь який", "любий", "все", "reset", "none"):
        return (True, None)

    # 1. Matches with thousands suffix: e.g. "250 тис", "280 тыс", "до 250к", "300k"
    m_tis = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:тис|тыс|тис\.|тыс\.|[kк])\b", clean)
    if m_tis:
        num = float(m_tis.group(1).replace(",", "."))
        return (True, int(num * 1000))

    # 2. Matches with explicit context: "пробіг 250000", "до 280 000 км", "240000 км"
    m_km = re.search(r"(?:пробіг|пробег|км)?[:\s]*(\d[\d\s]{4,7})\s*(?:км)?", clean)
    if m_km and ("пробіг" in clean or "пробег" in clean or "км" in clean):
        digits = re.sub(r"\s+", "", m_km.group(1))
        if digits.isdigit():
            val = int(digits)
            if 10000 <= val <= 1000000:
                return (True, val)

    # 3. Plain digits (e.g. "250000" or short "250" when user enters in thousands)
    digits = re.sub(r"[^\d]", "", clean)
    if digits:
        val = int(digits)
        if 50 <= val <= 999:
            return (True, val * 1000)
        elif 10000 <= val <= 1000000:
            return (True, val)

    return None
