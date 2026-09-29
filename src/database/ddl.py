"""
Database DDL utilities: table creation, schema initialization, and source seeding.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.database.connection import get_async_engine, get_session_factory
from src.database.models import Base, SourceModel

logger = logging.getLogger(__name__)

DEFAULT_SOURCES = [
    {
        "id": "auto_ria",
        "name": "AUTO.RIA",
        "base_url": "https://auto.ria.com",
        "source_type": "WEB_SCRAPER",
        "is_active": True,
        "rate_limit_per_min": 20,
        "scrape_interval_minutes": 15,
        "config": {"brand": "Audi", "model": "A6", "generation": "C5"},
    },
    {
        "id": "olx",
        "name": "OLX Ukraine",
        "base_url": "https://www.olx.ua",
        "source_type": "WEB_SCRAPER",
        "is_active": True,
        "rate_limit_per_min": 25,
        "scrape_interval_minutes": 15,
        "config": {"category": "passenger_cars", "q": "Audi A6 C5"},
    },
    {
        "id": "rst",
        "name": "RST.ua",
        "base_url": "https://rst.ua",
        "source_type": "WEB_SCRAPER",
        "is_active": True,
        "rate_limit_per_min": 20,
        "scrape_interval_minutes": 20,
        "config": {"make": "audi", "model": "a6"},
    },
    {
        "id": "telegram",
        "name": "Telegram Channels",
        "base_url": "https://t.me",
        "source_type": "TELEGRAM_CHANNEL",
        "is_active": True,
        "rate_limit_per_min": 60,
        "scrape_interval_minutes": 5,
        "config": {"channels": ["@autobazar_ua", "@autopoisk_ua", "@audi_club_ua"]},
    },
    {
        "id": "instagram",
        "name": "Instagram Accounts",
        "base_url": "https://www.instagram.com",
        "source_type": "INSTAGRAM_PROFILE",
        "is_active": True,
        "rate_limit_per_min": 15,
        "scrape_interval_minutes": 30,
        "config": {"accounts": ["autopodbor_ua", "avto_germany_ua", "audi_hub_ua"]},
    },
]


async def seed_default_sources(session: AsyncSession) -> None:
    """
    Inserts default sources if they do not already exist in the database.
    """
    for src in DEFAULT_SOURCES:
        stmt = select(SourceModel).where(SourceModel.id == src["id"])
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if not existing:
            source_obj = SourceModel(
                id=src["id"],
                name=src["name"],
                base_url=src["base_url"],
                source_type=src["source_type"],
                is_active=src["is_active"],
                rate_limit_per_min=src["rate_limit_per_min"],
                scrape_interval_minutes=src["scrape_interval_minutes"],
                config=src["config"],
            )
            session.add(source_obj)
    await session.flush()


async def init_db(engine: Optional[AsyncEngine] = None, seed_sources: bool = True) -> None:
    """
    Idempotent schema creation: creates tables, indexes, and seeds default sources.
    """
    active_engine = engine or get_async_engine()

    async with active_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if seed_sources:
        factory = get_session_factory(active_engine)
        async with factory() as session:
            async with session.begin():
                await seed_default_sources(session)

    logger.info("Database schema initialized successfully.")


async def drop_db(engine: Optional[AsyncEngine] = None) -> None:
    """
    Drops all registered tables. Use with caution (primarily for clean testing).
    """
    active_engine = engine or get_async_engine()
    async with active_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    logger.info("Database schema dropped.")
