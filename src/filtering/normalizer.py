"""
Text and homoglyph normalization for car listing evaluation.

Provides multi-pass Unicode sanitization, decimal point standardization,
whitespace collapsing, and Cyrillic/Latin dual-script token harmonization.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional


# Mapping of visually identical Cyrillic characters to Latin equivalents
CYRILLIC_TO_LATIN_HOMOGLYPHS = {
    # Uppercase
    "\u0410": "A",  # Cyrillic А
    "\u0412": "B",  # Cyrillic В
    "\u0421": "C",  # Cyrillic С
    "\u0415": "E",  # Cyrillic Е
    "\u041A": "K",  # Cyrillic К
    "\u041C": "M",  # Cyrillic М
    "\u041D": "H",  # Cyrillic Н
    "\u041E": "O",  # Cyrillic О
    "\u0420": "P",  # Cyrillic Р
    "\u0422": "T",  # Cyrillic Т
    "\u0423": "Y",  # Cyrillic У
    "\u0425": "X",  # Cyrillic Х
    "\u0406": "I",  # Ukrainian Cyrillic І
    # Lowercase
    "\u0430": "a",  # Cyrillic а
    "\u0432": "b",  # Cyrillic в
    "\u0441": "c",  # Cyrillic с
    "\u0435": "e",  # Cyrillic е
    "\u043A": "k",  # Cyrillic к
    "\u043C": "m",  # Cyrillic м
    "\u043D": "h",  # Cyrillic н
    "\u043E": "o",  # Cyrillic о
    "\u0440": "p",  # Cyrillic р
    "\u0442": "t",  # Cyrillic т
    "\u0443": "y",  # Cyrillic у
    "\u0445": "x",  # Cyrillic х
    "\u0456": "i",  # Ukrainian Cyrillic і
}

# Regex to standardize comma decimals (e.g. "1,8" -> "1.8", "1,9" -> "1.9", "2,4" -> "2.4")
RE_DECIMAL_COMMA = re.compile(r"(\d+),(\d+)")

# Regex for unprintable and zero-width characters
RE_UNPRINTABLE = re.compile(r"[\u200B\uFEFF\u200E\u200F\u202A-\u202E]")

# Regex to collapse multiple whitespace
RE_WHITESPACE = re.compile(r"\s+")

# Regex targeting specific automotive identifier tokens for homoglyph substitution
# e.g., Cyrillic А6, С5, 4В, 1.8Т, 2.7Т
RE_HOMOGLYPH_TOKENS = re.compile(
    r"\b(?:[аАaA][6]|[сСcC][4-8]|4[вВbB]|1\.8[тТtT]|2\.7[тТtT])\b",
    re.IGNORECASE,
)


def normalize_text(text: Optional[str]) -> str:
    """
    Pass 0: Basic sanitization.
    - Applies Unicode NFKC normalization.
    - Strips unprintable and zero-width Unicode characters.
    - Replaces non-breaking spaces with standard space.
    - Converts comma decimals (e.g., '1,8' -> '1.8').
    - Collapses consecutive whitespace characters.
    """
    if not text:
        return ""

    # NFKC compatibility decomposition & canonical composition
    normalized = unicodedata.normalize("NFKC", str(text))

    # Strip unprintable and zero-width chars
    normalized = RE_UNPRINTABLE.sub("", normalized)

    # Standardize spaces
    normalized = normalized.replace("\u00A0", " ")

    # Standardize comma decimals: 1,8 -> 1.8, 2,4 -> 2.4
    normalized = RE_DECIMAL_COMMA.sub(r"\1.\2", normalized)

    # Collapse whitespace
    normalized = RE_WHITESPACE.sub(" ", normalized).strip()

    return normalized


def to_track_a(text: Optional[str]) -> str:
    """
    Track A: Semantic text for negative filters and natural language evaluation.
    Preserves pristine Cyrillic / Ukrainian words (lowercased) without corrupting word roots.
    """
    return normalize_text(text).lower()


def replace_homoglyphs(text: str) -> str:
    """
    Replaces all visual Cyrillic lookalike characters with Latin equivalents.
    Caution: Only use on isolated tokens or identifiers, as it alters Cyrillic vocabulary.
    """
    if not text:
        return ""
    return "".join(CYRILLIC_TO_LATIN_HOMOGLYPHS.get(ch, ch) for ch in text)


def normalize_automotive_identifiers(text: str) -> str:
    """
    Replaces homoglyphs specifically in known automotive tokens (e.g. Cyrillic А6 -> A6,
    С5 -> C5, 4В -> 4B, 1.8Т -> 1.8T) while keeping surrounding words intact.
    """
    if not text:
        return ""

    def _replace_token(match: re.Match[str]) -> str:
        return replace_homoglyphs(match.group(0))

    return RE_HOMOGLYPH_TOKENS.sub(_replace_token, text)
