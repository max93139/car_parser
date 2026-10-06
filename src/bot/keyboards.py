"""
Inline keyboard builders for Audi A6 C5 Telegram Bot configuration.
Generates interactive Telegram Bot API inline keyboards with status badges.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from src.database.models import UserFilterModel

ALL_ENGINES = ["1.8T", "2.4", "1.9 TDI"]
SUPPORTED_A6 = ["A6 C5", "A6 C6", "A6 C7", "A6 C4"]
SUPPORTED_A4 = ["A4 B6", "A4 B7", "A4 B8", "A4 B5"]


def format_filter_summary(uf: UserFilterModel) -> str:
    """
    Renders human-readable summary of current filter preferences for Audi A6 & A4.
    """
    # Models
    active_models = getattr(uf, "selected_models", None) or ["A6 C5"]
    models_str = ", ".join(f"Audi {m}" for m in active_models) if active_models else "Не обрано ⚠️"

    # Engines
    active_eng = uf.engines if uf.engines else ALL_ENGINES
    engines_str = ", ".join(active_eng) if active_eng else "Всі вимкнені ⚠️"

    # Price
    p_min = f"${uf.min_price:,}" if uf.min_price else "не обмежено"
    p_max = f"${uf.max_price:,}" if uf.max_price else "не обмежено"
    price_str = f"від {p_min} до {p_max}"

    # Years
    year_str = f"{uf.min_year or 1997} – {uf.max_year or 2018}"
    if uf.min_year == 1997 and uf.max_year == 2001:
        year_str += " (Дорестайл)"
    elif uf.min_year == 2001 and uf.max_year == 2005:
        year_str += " (Рестайл)"

    # Transmission
    trans_map = {
        "any": "Будь-яка (механіка або автомат)",
        "manual": "Тільки механіка 🕹",
        "automatic": "Тільки автомат ⚙️",
    }
    trans_str = trans_map.get(uf.transmission, "Будь-яка")

    # Drive & Body
    drive_map = {
        "any": "Будь-який",
        "quattro": "Тільки Quattro (4x4) ⚡️",
        "front": "Тільки передній 🚗",
    }
    drive_str = drive_map.get(getattr(uf, "drive_type", "any"), "Будь-який")

    body_map = {
        "any": "Будь-який",
        "sedan": "Седан 🏎",
        "avant": "Універсал (Avant) 🚙",
    }
    body_str = body_map.get(getattr(uf, "body_type", "any"), "Будь-який")

    # Mileage
    mil_str = f"до {uf.max_mileage:,} км" if uf.max_mileage else "не обмежено"

    # Condition
    cond_str = "Тільки цілі / на ходу (без ДТП) ✅" if uf.exclude_damaged else "Без обмежень"

    return (
        "⚙️ <b>Налаштування пошуку Audi (A6 / A4)</b>\n\n"
        f"🚘 <b>Моделі:</b> {models_str}\n"
        f"⛽ <b>Двигуни:</b> {engines_str}\n"
        f"💰 <b>Ціна:</b> {price_str}\n"
        f"📅 <b>Роки:</b> {year_str}\n"
        f"🕹 <b>КПП:</b> {trans_str}\n"
        f"⚡️ <b>Привід:</b> {drive_str}\n"
        f"🚙 <b>Кузов:</b> {body_str}\n"
        f"🛣 <b>Пробіг:</b> {mil_str}\n"
        f"🛠 <b>Стан:</b> {cond_str}\n\n"
        "<i>Натискайте кнопки нижче, щоб змінити параметри:</i>"
    )


def build_settings_keyboard(uf: UserFilterModel) -> Dict[str, Any]:
    """
    Builds the Telegram inline keyboard for interactive configuration.
    """
    active_models = getattr(uf, "selected_models", None) or ["A6 C5"]
    active_eng = uf.engines if uf.engines is not None else ALL_ENGINES

    # 0. Audi A6 models row
    a6_buttons = []
    for m in SUPPORTED_A6:
        is_on = m in active_models
        icon = "✅ " if is_on else ""
        a6_buttons.append({
            "text": f"{icon}{m}",
            "callback_data": f"toggle_model:{m.replace(' ', '')}",
        })

    # 1. Audi A4 models row
    a4_buttons = []
    for m in SUPPORTED_A4:
        is_on = m in active_models
        icon = "✅ " if is_on else ""
        a4_buttons.append({
            "text": f"{icon}{m}",
            "callback_data": f"toggle_model:{m.replace(' ', '')}",
        })

    # 2. Engines row
    eng_buttons = []
    for eng in ALL_ENGINES:
        is_on = eng in active_eng
        icon = "✅" if is_on else "❌"
        eng_buttons.append({
            "text": f"{icon} {eng}",
            "callback_data": f"toggle_eng:{eng.replace(' ', '')}",
        })

    # 2. Price row (inscribing / typing instead of preset buttons)
    price_buttons = [
        {"text": "✏️ Вказати ціну (вписати текстом)", "callback_data": "prompt_price"},
    ]
    if uf.min_price or uf.max_price:
        price_buttons.append({"text": "❌ Скинути ціну", "callback_data": "reset_price"})

    # 4. Transmission row
    tr = uf.transmission or "any"
    trans_buttons = [
        {"text": f"{'🔘 ' if tr == 'any' else ''}Будь-яка КПП", "callback_data": "set_trans:any"},
        {"text": f"{'🔘 ' if tr == 'manual' else ''}Механіка", "callback_data": "set_trans:manual"},
        {"text": f"{'🔘 ' if tr == 'automatic' else ''}Автомат", "callback_data": "set_trans:automatic"},
    ]

    # 4.1 Drive type row
    dr = getattr(uf, "drive_type", "any") or "any"
    drive_buttons = [
        {"text": f"{'🔘 ' if dr == 'any' else ''}Будь-який привід", "callback_data": "set_drive:any"},
        {"text": f"{'🔘 ' if dr == 'quattro' else ''}Quattro ⚡️", "callback_data": "set_drive:quattro"},
        {"text": f"{'🔘 ' if dr == 'front' else ''}Передній", "callback_data": "set_drive:front"},
    ]

    # 4.2 Body type row
    bd = getattr(uf, "body_type", "any") or "any"
    body_buttons = [
        {"text": f"{'🔘 ' if bd == 'any' else ''}Будь-який кузов", "callback_data": "set_body:any"},
        {"text": f"{'🔘 ' if bd == 'sedan' else ''}Седан 🏎", "callback_data": "set_body:sedan"},
        {"text": f"{'🔘 ' if bd == 'avant' else ''}Avant (універсал) 🚙", "callback_data": "set_body:avant"},
    ]

    # 5. Mileage row (typing number instead of preset buttons)
    mil_buttons = [
        {"text": "🛣 Вказати пробіг (вписати числом)", "callback_data": "prompt_mileage"},
    ]
    if uf.max_mileage:
        mil_buttons.append({"text": "❌ Скинути пробіг", "callback_data": "reset_mileage"})

    # 6. Years / Restyling row
    is_dorest = uf.min_year == 1997 and uf.max_year == 2001
    is_rest = uf.min_year == 2001 and uf.max_year == 2005
    is_all_years = not is_dorest and not is_rest
    years_buttons = [
        {"text": f"{'🔘 ' if is_all_years else ''}Всі 97-05", "callback_data": "set_year:all"},
        {"text": f"{'🔘 ' if is_dorest else ''}Дорест 97-01", "callback_data": "set_year:dorest"},
        {"text": f"{'🔘 ' if is_rest else ''}Рест 01-05", "callback_data": "set_year:rest"},
    ]

    # 7. Condition row
    cond_icon = "✅" if uf.exclude_damaged else "❌"
    cond_buttons = [
        {"text": f"{cond_icon} Без ДТП / на ходу", "callback_data": "toggle_cond"},
    ]

    # 8. Action buttons
    action_buttons = [
        {"text": "🔍 Знайти зараз за моїми фільтрами", "callback_data": "action_search"},
    ]
    reset_buttons = [
        {"text": "🔄 Скинути до стандарту", "callback_data": "action_reset"},
    ]

    return {
        "inline_keyboard": [
            a6_buttons,
            a4_buttons,
            eng_buttons,
            price_buttons,
            trans_buttons,
            drive_buttons,
            body_buttons,
            mil_buttons,
            years_buttons,
            cond_buttons,
            action_buttons,
            reset_buttons,
        ]
    }
