"""
Telegram notification package for Audi A6 C5 Monitoring Service.
Exports TelegramNotifier client and message templating utilities.
"""

from src.notifier.telegram import TelegramNotifier
from src.notifier.templates import (
    format_listing_caption,
    format_mileage,
    format_needs_review_badge,
    format_price,
    format_price_drop_badge,
    format_source_badge,
    truncate_caption,
)

__all__ = [
    "TelegramNotifier",
    "format_listing_caption",
    "format_price",
    "format_mileage",
    "format_price_drop_badge",
    "format_needs_review_badge",
    "format_source_badge",
    "truncate_caption",
]
