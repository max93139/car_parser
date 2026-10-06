"""
Unit tests for UserFilterModel, filter matching logic, keyboard generation, and bot handlers.
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.bot.keyboards import (
    ALL_ENGINES,
    build_settings_keyboard,
    format_filter_summary,
)
from src.database.models import Base, ListingModel, UserFilterModel
from src.database.repository import (
    find_matching_listings,
    get_or_create_user_filter,
    reset_user_filter,
    update_user_filter,
)
from src.filtering.user_filter import matches_user_filter
from src.models.listing import Listing


@pytest.fixture
def sample_listing():
    return Listing(
        source="auto_ria",
        source_id="test_101",
        url="https://auto.ria.com/test_101.html",
        canonical_url="https://auto.ria.com/test_101.html",
        title="Audi A6 C5 1.9 TDI 2002",
        description="Гарний стан, мотор 1.9 tdi працює ідеально, рідний пробіг.",
        brand="Audi",
        model="A6",
        generation="C5",
        year=2002,
        price=4500.0,
        currency="USD",
        mileage=240000,
        engine="1.9 TDI",
        engine_code="1.9TDI",
        fuel_type="дизель",
        transmission="механіка",
        location="Львів",
        images=["https://img.test/1.jpg"],
        first_seen_at=datetime.now(timezone.utc),
    )


def test_matches_user_filter_defaults(sample_listing):
    uf = UserFilterModel(
        chat_id="123",
        engines=["1.8T", "2.4", "1.9 TDI"],
        min_price=None,
        max_price=None,
        min_year=1997,
        max_year=2005,
        max_mileage=None,
        transmission="any",
        exclude_damaged=True,
    )
    assert matches_user_filter(sample_listing, uf) is True


def test_matches_user_filter_engine_exclusion(sample_listing):
    uf = UserFilterModel(
        chat_id="123",
        engines=["1.8T", "2.4"],  # 1.9 TDI not selected
        min_price=None,
        max_price=None,
        min_year=1997,
        max_year=2005,
        transmission="any",
        exclude_damaged=True,
    )
    assert matches_user_filter(sample_listing, uf) is False


def test_matches_user_filter_price_range(sample_listing):
    # Within range
    uf_ok = UserFilterModel(chat_id="123", min_price=3000, max_price=5000)
    assert matches_user_filter(sample_listing, uf_ok) is True

    # Below min price
    uf_high = UserFilterModel(chat_id="123", min_price=5000, max_price=8000)
    assert matches_user_filter(sample_listing, uf_high) is False

    # Above max price
    uf_low = UserFilterModel(chat_id="123", min_price=2000, max_price=4000)
    assert matches_user_filter(sample_listing, uf_low) is False


def test_matches_user_filter_mileage(sample_listing):
    # Listing mileage is 240,000
    uf_pass = UserFilterModel(chat_id="123", max_mileage=250000)
    assert matches_user_filter(sample_listing, uf_pass) is True

    uf_fail = UserFilterModel(chat_id="123", max_mileage=200000)
    assert matches_user_filter(sample_listing, uf_fail) is False


def test_matches_user_filter_transmission(sample_listing):
    # Listing is manual
    uf_manual = UserFilterModel(chat_id="123", transmission="manual")
    assert matches_user_filter(sample_listing, uf_manual) is True

    uf_auto = UserFilterModel(chat_id="123", transmission="automatic")
    assert matches_user_filter(sample_listing, uf_auto) is False


def test_matches_user_filter_damaged_keyword():
    damaged_listing = Listing(
        source="auto_ria",
        source_id="test_dmg",
        url="https://auto.ria.com/test_dmg.html",
        canonical_url="https://auto.ria.com/test_dmg.html",
        title="Audi A6 C5 1.9 TDI 2002 бита після дтп",
        description="Продам на запчастини після ДТП, передок розбитий.",
        brand="Audi",
        model="A6",
        generation="C5",
        year=2002,
        price=1500.0,
        currency="USD",
        first_seen_at=datetime.now(timezone.utc),
    )
    uf_exclude = UserFilterModel(chat_id="123", exclude_damaged=True)
    assert matches_user_filter(damaged_listing, uf_exclude) is False

    uf_allow = UserFilterModel(chat_id="123", exclude_damaged=False)
    assert matches_user_filter(damaged_listing, uf_allow) is True


def test_keyboards_generation():
    uf = UserFilterModel(
        chat_id="456",
        engines=["1.9 TDI"],
        min_price=3000,
        max_price=5000,
        min_year=2001,
        max_year=2005,
        max_mileage=250000,
        transmission="manual",
        exclude_damaged=True,
    )
    summary = format_filter_summary(uf)
    assert "1.9 TDI" in summary
    assert "$3,000" in summary
    assert "$5,000" in summary
    assert "Рестайл" in summary
    assert "механіка" in summary

    kb = build_settings_keyboard(uf)
    assert "inline_keyboard" in kb
    rows = kb["inline_keyboard"]
    assert len(rows) >= 8

    # First row is engines
    eng_row = rows[0]
    texts = [b["text"] for b in eng_row]
    assert "❌ 1.8T" in texts
    assert "❌ 2.4" in texts
    assert "✅ 1.9 TDI" in texts

    # Second row is price
    price_row = rows[1]
    assert "Вказати ціну" in price_row[0]["text"]

    # Fourth row is mileage
    mil_row = rows[3]
    assert "Вказати пробіг" in mil_row[0]["text"]


def test_parse_mileage_input():
    from src.bot.price_parser import parse_mileage_input

    # Thousands suffix
    assert parse_mileage_input("250 тис") == (True, 250000)
    assert parse_mileage_input("до 280 тыс") == (True, 280000)
    assert parse_mileage_input("300k") == (True, 300000)
    assert parse_mileage_input("240к") == (True, 240000)

    # Explicit keyword
    assert parse_mileage_input("пробіг 250000") == (True, 250000)
    assert parse_mileage_input("до 280 000 км") == (True, 280000)

    # Plain digits
    assert parse_mileage_input("250000") == (True, 250000)
    assert parse_mileage_input("250") == (True, 250000)

    # Reset
    assert parse_mileage_input("0") == (True, None)
    assert parse_mileage_input("скинути") == (True, None)
    assert parse_mileage_input("будь-який") == (True, None)


def test_parse_price_input():
    from src.bot.price_parser import parse_price_input

    # Range
    assert parse_price_input("3500-5500") == (3500, 5500)
    assert parse_price_input("3000 - 6000$") == (3000, 6000)
    assert parse_price_input("3500 5500") == (3500, 5500)
    assert parse_price_input("3.5k - 5k") == (3500, 5000)

    # Upper bound
    assert parse_price_input("до 5000") == (None, 5000)
    assert parse_price_input("5000$") == (None, 5000)
    assert parse_price_input("4800") == (None, 4800)

    # Lower bound
    assert parse_price_input("від 3000") == (3000, None)
    assert parse_price_input("> 3500") == (3500, None)

    # Reset
    assert parse_price_input("0") == (None, None)
    assert parse_price_input("скинути") == (None, None)
    assert parse_price_input("будь-яка") == (None, None)


@pytest.mark.asyncio
async def test_repository_user_filters_crud():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as session:
        # 1. Get or create
        uf = await get_or_create_user_filter(session, "chat_999")
        assert uf.chat_id == "chat_999"
        assert "1.8T" in uf.engines
        assert uf.transmission == "any"

        # 2. Update
        uf_updated = await update_user_filter(
            session,
            "chat_999",
            min_price=3500,
            max_price=6000,
            transmission="manual",
            engines=["1.8T"],
        )
        assert uf_updated.min_price == 3500
        assert uf_updated.max_price == 6000
        assert uf_updated.transmission == "manual"
        assert uf_updated.engines == ["1.8T"]

        # 3. Reset
        uf_reset = await reset_user_filter(session, "chat_999")
        assert uf_reset.min_price is None
        assert uf_reset.max_price is None
        assert uf_reset.transmission == "any"
        assert len(uf_reset.engines) == 3

    await engine.dispose()
