"""
Engine validation rules for Audi A6 C5.

Strict Whitelist:
- 1.8T (150/180 hp): 1.8L 20V Turbocharged Petrol / LPG
- 2.4 (136-170 hp): 2.4L 30V V6 Naturally Aspirated Petrol / LPG
- 1.9 TDI (110/115/130 hp): 1.9L 8V Turbo Diesel

Strict Blacklist:
- 2.5 TDI: Infamous camshaft and VP44 pump failures
- 2.7T Biturbo: Dual-turbo high maintenance
- 2.8 V6 NA: High fuel consumption
- 3.0 V6 NA: Cylinder wall scoring risk
- 4.2 V8 NA: S6 / Allroad high running costs
- 2.0 ALT NA: Severe oil consumption
- 1.8 ADR / NA: Non-turbo 125hp underpowered engine
- Other engines (1.6, 2.6, 3.2, etc.)
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


# ==============================================================================
# Whitelist Engine Patterns
# ==============================================================================

# 1.8T: Turbocharged 1.8 petrol
RE_TARGET_18T = re.compile(
    r"\b1\.8\s*(?:[tт]|(?<!\bбез\s)(?<!\bне\s)(?:turbo|турбо|турбіна|турбован\w*|турбирован\w*)|"
    r"150\s*(?:л\.?с|квт|кw|hp)|180\s*(?:л\.?с|квт|кw|hp))\b|"
    r"\b1\.8\b.{0,25}?\b(?<!\bбез\s)(?<!\bне\s)(?:turbo|турбо|турбін\w*|турбован\w*|турбирован\w*|"
    r"150\s*л|180\s*л|110\s*к|132\s*к)\b|"
    r"\b(?:aeb|ajl|apu|ark|anb|awt)\b",
    re.IGNORECASE,
)

# 2.4: V6 Naturally Aspirated Petrol / LPG
RE_TARGET_24 = re.compile(
    r"\b2\.4(?!\d)(?:\s*(?:v6|бензин|газ|гбо|газ[/-]бензин|газ\s*бензин))?\b|"
    r"\b(?:aga|alf|aps|arj|aml|ajg|apz|amm|bdv|alw|arn|asm)\b",
    re.IGNORECASE,
)

# 1.9 TDI: 8V Turbo Diesel
RE_TARGET_19TDI = re.compile(
    r"\b1\.9\s*(?:tdi|тди|тді|дизель|diesel|насос[ -]форсунк[аи]|pd)\b|"
    r"\b1\.9(?!\d)(?:\s*(?:81\s*(?:квт|kw)|96\s*(?:квт|kw)|130\s*(?:л\.?с|hp)|"
    r"110\s*(?:л\.?с|hp)|115\s*(?:л\.?с|hp)))?\b|"
    r"\b(?:afn|avg|ajm|avf|awx)\b",
    re.IGNORECASE,
)

# Ambiguous 1.8 detection (1.8 present without explicit turbo or NA indicators)
RE_GENERIC_18 = re.compile(r"\b1\.8(?!\d)\b", re.IGNORECASE)


# ==============================================================================
# Blacklist Engine Patterns
# ==============================================================================

RE_REJECTED_ENGINES = {
    "REJECTED_ENGINE_2.5_TDI": re.compile(
        r"\b2\.5\s*(?:tdi|тди|тді|дизель|diesel|v6)?\b",
        re.IGNORECASE,
    ),
    "REJECTED_ENGINE_2.7T": re.compile(
        r"\b2\.7\s*(?:[tт]|biturbo|битурбо|turbo|турбо)\b",
        re.IGNORECASE,
    ),
    "REJECTED_ENGINE_2.8": re.compile(
        r"\b2\.8(?!\d)\b",
        re.IGNORECASE,
    ),
    "REJECTED_ENGINE_3.0": re.compile(
        r"\b3\.0(?!\d)\b",
        re.IGNORECASE,
    ),
    "REJECTED_ENGINE_4.2": re.compile(
        r"\b4\.2(?!\d)\b|\bs6\b|\brs6\b",
        re.IGNORECASE,
    ),
    "REJECTED_ENGINE_2.0": re.compile(
        r"\b2\.0(?!\d)(?:\s*(?:alt|бензин|бенз))?\b",
        re.IGNORECASE,
    ),
    "REJECTED_ENGINE_1.8_NON_TURBO": re.compile(
        r"\b1\.8\b.{0,35}?\b(?:125\s*(?:л\.?с|квт|hp)|92\s*(?:квт|kw)|"
        r"атмосфер\w*|(?:без|не)\s+(?:турб\w+|turbo)|простой|простий|adr|agn|afy)\b|"
        r"\b(?:125\s*(?:л\.?с|квт|hp)|92\s*(?:квт|kw)|атмосфер\w*|(?:без|не)\s+(?:турб\w+|turbo)|простой|простий|adr|agn|afy)"
        r"\b.{0,35}?\b1\.8\b",
        re.IGNORECASE,
    ),
    "REJECTED_ENGINE_OTHER": re.compile(
        r"\b(?:1\.6|2\.6|3\.2(?:\s*fsi)?)\b",
        re.IGNORECASE,
    ),
}


def check_engine_blacklist(
    full_text: str,
) -> Optional[str]:
    """
    Checks if text matches any blacklisted engine.
    Returns the rejection reason code, or None if clean.
    """
    for reason_code, pattern in RE_REJECTED_ENGINES.items():
        if pattern.search(full_text):
            return reason_code
    return None


def identify_target_engine(
    full_text: str,
    structured_engine: Optional[str] = None,
    structured_fuel: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Evaluates text and structured fields to detect target engine.
    Returns (normalized_engine, ambiguity_reason).
    - normalized_engine: "1.8T", "2.4", or "1.9_TDI" if definitively identified.
    - ambiguity_reason: Reason code if ambiguous (e.g. "AMBIGUOUS_1.8_CHECK_TURBO", "ENGINE_NOT_IDENTIFIED").
    """
    # 1. First, check for 1.8 NA disqualifier (125hp / ADR)
    if RE_REJECTED_ENGINES["REJECTED_ENGINE_1.8_NON_TURBO"].search(full_text):
        return None, "REJECTED_ENGINE_1.8_NON_TURBO"

    # 2. Check 1.8T Whitelist
    is_18t = bool(RE_TARGET_18T.search(full_text))
    if not is_18t and structured_engine:
        if "1.8" in structured_engine and ("t" in structured_engine.lower() or "турбо" in structured_engine.lower()):
            is_18t = True

    # 3. Check 2.4 Whitelist
    is_24 = bool(RE_TARGET_24.search(full_text))
    if not is_24 and structured_engine and "2.4" in structured_engine:
        is_24 = True

    # 4. Check 1.9 TDI Whitelist
    is_19tdi = bool(RE_TARGET_19TDI.search(full_text))
    if not is_19tdi and structured_engine and "1.9" in structured_engine:
        is_19tdi = True

    # Check matches count
    matches = []
    if is_18t:
        matches.append("1.8T")
    if is_24:
        matches.append("2.4")
    if is_19tdi:
        matches.append("1.9_TDI")

    if len(matches) == 1:
        return matches[0], None

    if len(matches) > 1:
        # Multiple matched (rare, e.g. text mentions "1.8T or 2.4")
        return None, "AMBIGUOUS_MULTIPLE_ENGINES"

    # 5. Check for ambiguous 1.8 without turbo or NA indicator
    if RE_GENERIC_18.search(full_text) or (structured_engine and "1.8" in structured_engine):
        return None, "AMBIGUOUS_1.8_CHECK_TURBO"

    # 6. No engine identified
    return None, "ENGINE_NOT_IDENTIFIED"
