"""
Contextual negative filtering rules to eliminate non-car listings.

Rejects:
- Trade-in target offers ("обмен на Audi" where sold car is not Audi)
- Buyer wanted inquiries ("куплю Audi", "шукаю Audi")
- Dismantlers, wreckers and scrap ("разборка", "розбірка", "шрот", "на запчасти", "донор")
- Standalone spare part titles ("турбина 1.8T Audi", "фары Audi A6 C5")
- Commercial services ("пригон авто", "автоподбор", "растаможка", "аренда")
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


# 1. Directional Trade-in Target: detects someone selling another vehicle and offering trade for Audi/A6/C5
RE_NEG_TRADE_TARGET = re.compile(
    r"\b(?:обмен\w*|обмін\w*|поменя\w*|помін\w*|розглян\w+\s+обмін|рассмотр\w+\s+обмен|цікавит\w+\s+обмін|интересу\w+\s+обмен)"
    r"\b.{1,50}?\bна\s+(?:авто\w*\s+|машин\w*\s+)?(?:audi|ауд[иі]|[aа]6|[cс]5)\b",
    re.IGNORECASE,
)

# 2. Buyer Inquiries: wanted ads seeking to buy Audi
RE_NEG_BUYER = re.compile(
    r"^(?:куплю|шукаю|придбаю|купим|ищу)\b|"
    r"\b(?:куплю|шукаю|придбаю|купим|ищу)\s+(?:audi|ауд[иі]|[aа]6|[cс]5|авто|машину|документ\w*|кузов)\b",
    re.IGNORECASE,
)

# 3. Dismantling, Wreckers & Scrap
RE_NEG_DISMANTLING = re.compile(
    r"\b(?:"
    r"разборк[аиуеы]|розбірк[аиуе]|розборк[аиуе]|авторазборк\w*|авторозбірк\w*|"
    r"шрот|автошрот|"
    r"на\s+запчаст[иі]|по\s+запчаст(?:ям|ях|инах)|в\s+разбор|под\s+разбор|на\s+розборку|"
    r"донор[ауе]?|на\s+донора|под\s+донора|"
    r"без\s+двигател[яей]|без\s+мотор[а]|без\s+двигун[а]|без\s+кпп|"
    r"кузов\s+с\s+документами|голый\s+кузов|техпаспорт\s+отдельно|только\s+кузов|"
    r"разбира(?:ю|ется|ем)|розбира(?:ю|ється|ємо)"
    r")\b",
    re.IGNORECASE,
)

# 4. Standalone Spare Part Titles
RE_NEG_PARTS_TITLE = re.compile(
    r"^(?:продам\s+|продаю\s+|продаж\s+|продається\s+)?"
    r"(?:оригинальн\w+\s+|оригінальн\w+\s+|нов\w+\s+|б[/-]?у\s+)?"
    r"(?:запчаст\w*|запчастин\w*|детал\w*|фар[аыие]|фонар\w*|ліхтар\w*|капот\w*|крыл\w+|крил\w+|бампер\w*|"
    r"двигател\w*|мотор\w*|двигун\w*|кпп|акпп|мкпп|коробк\w+\s+передач|"
    r"турбин\w*|турбін\w*|тнвд|форсунк\w*|диск\w*|титан\w*|салон\w*|сидень\w*|сидінн\w*|"
    r"руль|керм\w*|приборк\w*|щиток\s+приборов|подлокотник\w*|радиатор\w*|радіатор\w*)"
    r"\b.{0,45}?\b(?:audi|ауд[иі]|[aа]6|[cс]5|passat)\b",
    re.IGNORECASE,
)

# 5. Commercial Services & Non-Car Listings
RE_NEG_SERVICES = re.compile(
    r"\b(?:"
    r"пригон\s+(?:авто|под\s+заказ|з\s+європи|из\s+европы)|автоподбор|підбір\s+авто|"
    r"растаможк\w*|розмитненн\w*|помощь\s+в\s+растаможке|"
    r"аренда\s+авто|авто\s+под\s+выкуп|оренда\s+авто|під\s+викуп|такси"
    r")\b",
    re.IGNORECASE,
)


def evaluate_negative_rules(
    clean_title: str,
    clean_description: str,
) -> Optional[str]:
    """
    Evaluates title and description against all negative patterns.
    Returns rejection reason code if negative trigger found, or None if clean.
    """
    full_text = f"{clean_title} {clean_description}".strip()

    # 1. Parts in title (evaluated specifically against title)
    if clean_title and RE_NEG_PARTS_TITLE.search(clean_title):
        return "NEGATIVE_PARTS_TITLE"

    # 2. Buyer inquiry (evaluated against title and full text)
    if RE_NEG_BUYER.search(clean_title) or RE_NEG_BUYER.search(full_text):
        return "NEGATIVE_BUYER_INQUIRY"

    # 3. Directional trade-in target
    if RE_NEG_TRADE_TARGET.search(full_text):
        return "NEGATIVE_TRADE_IN_TARGET"

    # 4. Dismantling, wreckers, scrap
    if RE_NEG_DISMANTLING.search(full_text):
        return "NEGATIVE_DISMANTLING"

    # 5. Commercial services
    if RE_NEG_SERVICES.search(full_text):
        return "NEGATIVE_COMMERCIAL_SERVICE"

    return None
