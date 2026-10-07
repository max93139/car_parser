"""
Unit tests for UserFilterModel, filter matching logic, keyboard generation, and bot handlers.
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.bot.keyboards import (
    ALL_ENGINES,
    build_main_reply_keyboard,
    build_settings_keyboard,
    format_filter_summary,
)
from src.database.models import Base, ListingModel, UserFilterModel
from src.database.repository import (
    find_matching_listings,
    get_market_overview,
    get_or_create_user_filter,
    reset_user_filter,
    update_user_filter,
)
from src.filtering.user_filter import matches_user_filter
from src.models.listing import Listing
from src.notifier.templates import (
    format_listing_caption,
    format_market_badge,
    format_market_overview,
    format_price,
    format_price_drop_badge,
)


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

    # Models rows are first two rows, then engines
    eng_row = next(r for r in rows if any("1.8T" in b["text"] for b in r))
    texts = [b["text"] for b in eng_row]
    assert "❌ 1.8T" in texts
    assert "❌ 2.4" in texts
    assert "✅ 1.9 TDI" in texts

    # Price row
    price_row = next(r for r in rows if any("Вказати ціну" in b["text"] for b in r))
    assert "Вказати ціну" in price_row[0]["text"]

    # Mileage row
    mil_row = next(r for r in rows if any("Вказати пробіг" in b["text"] for b in r))
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


def test_build_main_reply_keyboard():
    kb = build_main_reply_keyboard()
    assert kb["resize_keyboard"] is True
    assert kb["is_persistent"] is True
    keyboard_rows = kb["keyboard"]
    all_texts = [btn["text"] for row in keyboard_rows for btn in row]
    assert "🔍 Знайти авто зараз" in all_texts
    assert "⚙️ Налаштування фільтрів" in all_texts
    assert "📊 Статистика ринку" in all_texts
    assert "🔄 Скинути фільтри" in all_texts
    assert "🚀 Boost пошук" in all_texts
    assert "ℹ️ Допомога" in all_texts


def test_format_price_decimal_and_numeric_compatibility():
    # Decimal USD
    res_usd = format_price(Decimal("4500.00"), "USD")
    assert "$4 500 (~186 750 грн)" in res_usd

    # Decimal UAH
    res_uah = format_price(Decimal("200000"), "UAH")
    assert "200 000 грн" in res_uah

    # None and invalid handling
    assert format_price(None, "USD") == "Договірна"
    assert format_price("invalid", "USD") == "Договірна"

    # Price drop badge with Decimal
    drop_badge = format_price_drop_badge(Decimal("5000"), Decimal("4500"), Decimal("-500"))
    assert "ЦІНУ ЗНИЖЕНО" in drop_badge
    assert "-$500" in drop_badge

    # Market badge with Decimal
    market_badge = format_market_badge(Decimal("3000"), Decimal("4000"))
    assert market_badge is not None
    assert "НИЗ РИНКУ" in market_badge


@pytest.mark.asyncio
async def test_get_market_overview_and_formatting():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        # Add sample listings
        from src.database.models import SourceModel
        source = SourceModel(
            id="auto_ria",
            name="AUTO.RIA",
            base_url="https://auto.ria.com",
            source_type="html",
        )
        session.add(source)
        await session.flush()

        listing1 = ListingModel(
            source_id="auto_ria",
            source_listing_id="ria_1",
            url="https://auto.ria.com/1",
            canonical_url="https://auto.ria.com/1",
            title="Audi A6 2000",
            brand="Audi",
            model="A6",
            generation="C5",
            year=2000,
            price=Decimal("4000.00"),
            price_usd=Decimal("4000.00"),
            currency="USD",
            content_fingerprint="fp1",
            fuzzy_fingerprint="ffp1",
            images=["https://img.test/1.jpg"],
        )
        listing2 = ListingModel(
            source_id="auto_ria",
            source_listing_id="ria_2",
            url="https://auto.ria.com/2",
            canonical_url="https://auto.ria.com/2",
            title="Audi A4 2006",
            brand="Audi",
            model="A4",
            generation="B7",
            year=2006,
            price=Decimal("6000.00"),
            price_usd=Decimal("6000.00"),
            currency="USD",
            content_fingerprint="fp2",
            fuzzy_fingerprint="ffp2",
            images=[],
        )
        session.add_all([listing1, listing2])
        await session.flush()

        # Query stats
        stats = await get_market_overview(session)
        assert stats["total"] == 2
        assert stats["avg_price"] == 5000.0
        assert stats["min_price"] == 4000.0
        assert stats["max_price"] == 6000.0

        # Check caption format with Decimal on ListingModel
        caption = format_listing_caption(listing1)
        assert "Audi A6 2000" in caption
        assert "$4 000" in caption

        # Format stats message
        msg = format_market_overview(stats)
        assert "Аналітика та статистика ринку Audi" in msg
        assert "2 шт." in msg
        assert "$5 000" in msg

    await engine.dispose()


@pytest.mark.asyncio
async def test_bot_handler_menu_and_boost_dispatch():
    from unittest.mock import AsyncMock, patch
    from src.bot.handlers import BotHandler
    import httpx

    client = AsyncMock(spec=httpx.AsyncClient)
    handler = BotHandler("fake_token", client)
    handler.send_message = AsyncMock(return_value={"message_id": 1})
    handler.send_photo = AsyncMock(return_value=True)

    # Test handle_help
    await handler.handle_help("123")
    handler.send_message.assert_called()
    call_args = handler.send_message.call_args[0]
    assert "Як користуватися ботом" in call_args[1]

    # Test menu texts in handle_text_message
    with patch.object(handler, "execute_search", new_callable=AsyncMock) as mock_search, \
         patch.object(handler, "handle_settings", new_callable=AsyncMock) as mock_settings, \
         patch.object(handler, "handle_market_stats", new_callable=AsyncMock) as mock_stats, \
         patch.object(handler, "handle_reset", new_callable=AsyncMock) as mock_reset, \
         patch.object(handler, "handle_help", new_callable=AsyncMock) as mock_help:

        # 1. Search button
        await handler.handle_text_message("123", "🔍 Знайти авто зараз")
        mock_search.assert_called_once_with("123")

        # 2. Boost command and button
        mock_search.reset_mock()
        await handler.handle_text_message("123", "/boost")
        mock_search.assert_called_once_with("123", is_boost=True)

        mock_search.reset_mock()
        await handler.handle_text_message("123", "🚀 Boost пошук")
        mock_search.assert_called_once_with("123", is_boost=True)

        # 3. Settings button
        await handler.handle_text_message("123", "⚙️ Налаштування фільтрів")
        mock_settings.assert_called_once_with("123")

        # 4. Market stats button
        await handler.handle_text_message("123", "📊 Статистика ринку")
        mock_stats.assert_called_once_with("123")

        # 5. Reset button
        await handler.handle_text_message("123", "🔄 Скинути фільтри")
        mock_reset.assert_called_once_with("123")

        # 6. Help button
        await handler.handle_text_message("123", "ℹ️ Допомога")
        mock_help.assert_called_once_with("123")


@pytest.mark.asyncio
async def test_bot_handler_send_fallback_and_card_markup():
    from unittest.mock import AsyncMock, MagicMock
    from src.bot.handlers import BotHandler
    import httpx

    # Test 1: send_message retries as plain text when HTML parsing fails (HTTP 400)
    client = AsyncMock(spec=httpx.AsyncClient)
    resp_fail = MagicMock(spec=httpx.Response)
    resp_fail.status_code = 400
    resp_fail.text = '{"ok":false,"description":"Bad Request: can\'t parse entities"}'

    resp_ok = MagicMock(spec=httpx.Response)
    resp_ok.status_code = 200
    resp_ok.json.return_value = {"ok": True, "result": {"message_id": 999}}

    client.post.side_effect = [resp_fail, resp_ok]

    handler = BotHandler("fake_token", client)
    res = await handler.send_message("123", "<b>Hello <i>world</i></b>")
    assert res == {"message_id": 999}
    assert client.post.call_count == 2
    # Verify second call had parse_mode removed and HTML tags stripped
    second_payload = client.post.call_args_list[1][1]["json"]
    assert "parse_mode" not in second_payload
    assert second_payload["text"] == "Hello world"

    # Test 2: send_photo retries as plain text when caption parse fails (HTTP 400)
    client.reset_mock()
    client.post.side_effect = [resp_fail, resp_ok]
    photo_res = await handler.send_photo("123", "https://img.test/a.jpg", "<b>Car</b> caption")
    assert photo_res is True
    assert client.post.call_count == 2
    photo_retry_payload = client.post.call_args_list[1][1]["json"]
    assert "parse_mode" not in photo_retry_payload
    assert photo_retry_payload["caption"] == "Car caption"

    # Test 3: format_listing_caption handles empty/invalid url safely
    no_url_listing = {
        "title": "Audi A6",
        "price": Decimal("3000"),
        "url": "",
    }
    cap_no_url = format_listing_caption(no_url_listing)
    assert "Посилання відсутнє" in cap_no_url

    # Test 4: format_market_overview escapes dangerous tags in model and source names
    dirty_stats = {
        "total": 5,
        "models": [("A6 <script>", 3)],
        "sources": [("<untrusted_source>", 2)],
    }
    overview = format_market_overview(dirty_stats)
    assert "<script>" not in overview
    assert "&lt;script&gt;" in overview
    assert "<untrusted_source>" not in overview
    assert "&lt;Untrusted Source&gt;" in overview
