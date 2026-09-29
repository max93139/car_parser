"""
Shared Pytest fixtures for Audi A6 C5 Monitoring Service test suites.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.config import Settings
from src.database.connection import (
    close_db_engine,
    get_async_engine,
    get_session_factory,
)
from src.database.ddl import drop_db, init_db
from src.database.models import Base
from src.models.listing import Listing, RawListingPayload, SourceType

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_loader() -> Callable[[str], str]:
    """Loads text content from tests/fixtures/<path>."""
    def _loader(relative_path: str) -> str:
        target = FIXTURES_DIR / relative_path
        if not target.is_file():
            raise FileNotFoundError(f"Fixture not found at {target}")
        return target.read_text(encoding="utf-8")
    return _loader


@pytest.fixture(scope="session")
def json_fixture_loader(fixture_loader: Callable[[str], Any]) -> Callable[[str], Any]:
    """Loads and deserializes JSON from tests/fixtures/<path>."""
    def _loader(relative_path: str) -> Any:
        content = fixture_loader(relative_path)
        return json.loads(content)
    return _loader


@pytest_asyncio.fixture
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """
    Creates an isolated in-memory SQLite database engine with foreign keys and seeded sources.
    """
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine, seed_sources=True)

    yield engine

    await drop_db(engine)
    await close_db_engine(engine)


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """
    Provides an active AsyncSession connected to the test database with auto-rollback on error.
    """
    factory = get_session_factory(test_engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture
async def async_db_session(db_session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """Alias for db_session for compatibility across test suites."""
    yield db_session


@pytest.fixture
def sample_listing() -> Listing:
    """Standard valid Pydantic Listing instance."""
    return Listing(
        source="auto_ria",
        source_id="36482145",
        url="https://auto.ria.com/uk/car/audi/a6/auto_audi_a6_36482145.html?utm_source=telegram",
        title="Audi A6 C5 1.8 Turbo 2001",
        description="Продам Audi A6 C5 1.8 Turbo бензин. Механіка, повний привід, гарний стан.",
        brand="Audi",
        model="A6",
        generation="C5",
        year=2001,
        body_type="sedan",
        price=4800.0,
        currency="USD",
        mileage=285000,
        engine="1.8 Turbo",
        engine_code="1.8T",
        fuel_type="petrol",
        transmission="manual",
        drive_type="quattro",
        location="Київ",
        seller="Максим",
        seller_phone="097 987 65 43",
        images=[
            "https://cdn.riastatic.com/photo1.jpg",
            "https://cdn.riastatic.com/photo2.jpg",
        ],
        published_at=datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_raw_payload() -> RawListingPayload:
    """Sample raw scraper payload."""
    return RawListingPayload(
        source="auto_ria",
        source_id="36482145",
        url="https://auto.ria.com/uk/car/audi/a6/auto_audi_a6_36482145.html?utm_source=telegram&ref=feed",
        title="Audi A6 C5 1.8 T 2001",
        raw_text="Audi A6 C5 в хорошому стані. Двигун 1.8 турбо бензин, механіка, клімат.",
        raw_price="4 500 $",
        price=4500.0,
        currency="USD",
        raw_year="2001",
        year=2001,
        raw_mileage="284 тис. км",
        mileage=284000,
        raw_engine="1.8 T",
        engine="1.8T",
        raw_fuel="Бензин",
        fuel_type="petrol",
        raw_transmission="Механіка",
        transmission="manual",
        raw_location="Київ",
        location="Київ",
        seller_name="Олександр",
        seller_phone="+38 (050) 123-45-67",
        image_urls=[
            "https://cdn.riastatic.com/photos/1.jpg",
            "https://cdn.riastatic.com/photos/2.jpg",
        ],
        published_at=datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_valid_listing_18t() -> Listing:
    """Standard valid 1999 Audi A6 C5 1.8T Listing."""
    return Listing(
        source="auto_ria",
        source_id="36482145",
        url="https://auto.ria.com/uk/auto_audi_a6_36482145.html",
        title="Audi A6 C5 1.8T 1999",
        brand="Audi",
        model="A6",
        generation="C5",
        year=1999,
        price=4200.0,
        currency="USD",
        mileage=280000,
        engine="1.8 Turbo",
        engine_code="1.8T",
        fuel_type="petrol",
        transmission="manual",
        location="Київ",
        seller="Олександр",
        seller_phone="+380671112233",
        images=["https://cdn4.riastatic.com/photosnew/auto/photo/audi_a6__36482145-12345f.jpg"],
    )


@pytest.fixture
def sample_valid_listing_24() -> Listing:
    """Standard valid 2002 Audi A6 C5 2.4 Listing."""
    return Listing(
        source="olx",
        source_id="829104812",
        url="https://www.olx.ua/d/uk/obyavlenie/audi-a6-c5-2-4-2002-ID829104812.html",
        title="Audi A6 4B 2.4 V6 2002",
        brand="Audi",
        model="A6",
        generation="C5",
        year=2002,
        price=4800.0,
        currency="USD",
        mileage=310000,
        engine="2.4 V6",
        engine_code="2.4",
        fuel_type="gas_petrol",
        transmission="automatic",
        location="Львів",
        seller="Андрій",
        seller_phone="+380501234567",
        images=["https://ireland.apollo.olxcdn.com/v1/files/sample_olx/image"],
    )


@pytest.fixture
def sample_valid_listing_19tdi() -> Listing:
    """Standard valid 2003 Audi A6 C5 1.9 TDI Listing."""
    return Listing(
        source="rst",
        source_id="14238911",
        url="https://rst.ua/ukr/oldcars/audi/a6/audi_a6_14238911.html",
        title="Audi A6 Avant 2002 1.9 TDI",
        brand="Audi",
        model="A6",
        generation="C5",
        year=2003,
        price=4600.0,
        currency="USD",
        mileage=315000,
        engine="1.9 TDI 96kW AWX",
        engine_code="1.9_TDI",
        fuel_type="diesel",
        transmission="manual",
        location="Рівне",
        seller="Сергій",
        seller_phone="+380987654321",
        images=["https://img.rst.ua/oldcars/audi/a6/audi_a6_14238911_0.jpg"],
    )
