"""
Multi-source parsers package for Audi A6 C5 monitoring.
Exports BaseParser, ParserRunStats, and concrete scrapers for AUTO.RIA, OLX, RST.ua, Telegram, and Instagram.
"""

from src.parsers.base import BaseParser, ParserRunStats
from src.parsers.auto_ria import AutoRiaParser
from src.parsers.olx import OlxParser
from src.parsers.rst import RstParser
from src.parsers.telegram import TelegramChannelParser
from src.parsers.instagram import InstagramParser

__all__ = [
    "BaseParser",
    "ParserRunStats",
    "AutoRiaParser",
    "OlxParser",
    "RstParser",
    "TelegramChannelParser",
    "InstagramParser",
]
