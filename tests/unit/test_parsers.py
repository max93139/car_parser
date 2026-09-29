"""
Unit tests for Milestone M3: Multi-Source Parsers.
Tests BaseParser ABC, AutoRiaParser, OlxParser, RstParser,
TelegramChannelParser, InstagramParser, and Error Isolation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from src.models.listing import RawListingPayload
from src.parsers.base import BaseParser, ParserRunStats, USER_AGENTS
from src.parsers.auto_ria import AutoRiaParser
from src.parsers.olx import OlxParser
from src.parsers.rst import RstParser
from src.parsers.telegram import TelegramChannelParser, FloodWaitError
from src.parsers.instagram import InstagramParser

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


# ==============================================================================
# 1. BaseParser ABC & ParserRunStats Tests
# ==============================================================================

class DummyParser(BaseParser):
    """Concrete implementation for testing BaseParser ABC contracts."""
    def __init__(self, listings_to_yield: Optional[List[RawListingPayload]] = None, **kwargs):
        super().__init__(name="dummy", base_url="https://dummy.example.com", **kwargs)
        self._listings = listings_to_yield or []

    async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]:
        self.stats.start()
        for item in self._listings:
            self.stats.record_fetched()
            self.stats.record_valid()
            yield item
        self.stats.finish()


class TestBaseParserAndStats:
    """Tests for BaseParser ABC and ParserRunStats telemetry."""

    def test_parser_run_stats_lifecycle_and_aliases(self):
        stats = ParserRunStats(source="auto_ria")
        assert stats.source == "auto_ria"
        assert stats.source_name == "auto_ria"
        assert stats.status == "PENDING"
        assert stats.scanned == 0
        assert stats.items_fetched == 0
        assert stats.new == 0
        assert stats.items_valid == 0
        assert stats.errors == 0
        assert stats.errors_count == 0

        stats.start()
        assert stats.status == "RUNNING"
        assert stats.started_at is not None

        stats.record_fetched(5)
        assert stats.scanned == 5
        assert stats.items_fetched == 5

        stats.record_valid(3)
        assert stats.new == 3
        assert stats.items_valid == 3

        stats.record_error(2)
        assert stats.errors == 2
        assert stats.errors_count == 2

        stats.finish()
        assert stats.status == "PARTIAL"  # Had both valid and errors
        assert stats.finished_at is not None
        assert stats.duration_seconds >= 0.0

    def test_parser_run_stats_success_and_failure_states(self):
        # Pure success
        s1 = ParserRunStats(source="test")
        s1.start()
        s1.record_fetched(2)
        s1.record_valid(2)
        s1.finish()
        assert s1.status == "SUCCESS"

        # Pure failure
        s2 = ParserRunStats(source="test")
        s2.start()
        s2.record_error(3)
        s2.finish()
        assert s2.status == "FAILED"

    def test_user_agent_rotation(self):
        parser = DummyParser()
        seen_agents = set()
        for _ in range(30):
            headers = parser.rotate_headers()
            ua = headers["User-Agent"]
            assert ua in USER_AGENTS
            seen_agents.add(ua)
        # Should have picked more than 1 agent over 30 rotations
        assert len(seen_agents) > 1

    @pytest.mark.asyncio
    async def test_lazy_client_initialization_and_close(self):
        parser = DummyParser()
        assert parser._client is None

        client = await parser.get_client()
        assert isinstance(client, httpx.AsyncClient)
        assert client.is_closed is False

        await parser.close()
        assert parser._client is None

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        async with DummyParser() as parser:
            client = await parser.get_client()
            assert client.is_closed is False
        assert parser._client is None

    @pytest.mark.asyncio
    async def test_fetch_response_with_retry_on_429(self):
        # Mock client that returns 429 on 1st call and 200 on 2nd
        resp_429 = httpx.Response(status_code=429, headers={"Retry-After": "0"})
        resp_200 = httpx.Response(status_code=200, text="Success")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=[resp_429, resp_200])

        parser = DummyParser(client=mock_client, request_delay=0.01)
        resp = await parser.fetch_response_with_retry("https://dummy.example.com/test")

        assert resp is not None
        assert resp.status_code == 200
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_response_with_retry_on_network_timeout(self):
        # Mock client that raises TimeoutException then succeeds
        resp_200 = httpx.Response(status_code=200, text="Recovered")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=[httpx.TimeoutException("Timeout"), resp_200])

        parser = DummyParser(client=mock_client, request_delay=0.01)
        resp = await parser.fetch_response_with_retry("https://dummy.example.com/test")

        assert resp is not None
        assert resp.status_code == 200
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_response_non_retryable_client_error(self):
        resp_404 = httpx.Response(status_code=404)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=resp_404)

        parser = DummyParser(client=mock_client, request_delay=0.01)
        resp = await parser.fetch_response_with_retry("https://dummy.example.com/notfound")

        assert resp is not None
        assert resp.status_code == 404
        assert mock_client.get.call_count == 1  # Did not retry 404


# ==============================================================================
# 2. AUTO.RIA Parser Tests
# ==============================================================================

class TestAutoRiaParser:
    """Unit tests for AutoRiaParser."""

    @pytest.mark.asyncio
    async def test_parse_sample_search_page(self, fixture_loader):
        html_content = fixture_loader("auto_ria/sample_search_page.html")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text=html_content))

        parser = AutoRiaParser(client=mock_client, max_pages=1, request_delay=0)
        listings: List[RawListingPayload] = []

        async for item in parser.fetch_new_listings():
            listings.append(item)

        assert len(listings) == 4
        assert parser.stats.items_fetched == 4
        assert parser.stats.items_valid == 4
        assert parser.stats.status == "SUCCESS"

        # Item 1: 1.8T 1999 Manual
        ad1 = listings[0]
        assert ad1.source == "auto_ria"
        assert ad1.source_id == "36482145"
        assert ad1.url == "https://auto.ria.com/uk/auto_audi_a6_36482145.html"
        assert "Audi A6 C5 1.8T 1999" in ad1.title
        assert ad1.price == 4200.0
        assert ad1.currency == "USD"
        assert ad1.year == 1999
        assert ad1.mileage == 280000
        assert "1.8 л" in ad1.engine
        assert "Механіка" in ad1.transmission
        assert ad1.location == "Київ"
        assert len(ad1.image_urls) == 1
        assert ad1.image_urls[0].endswith("f.jpg")  # Converted to full res

        # Item 2: 2.4 V6 2002 Automatic Gas/Petrol
        ad2 = listings[1]
        assert ad2.source_id == "36482146"
        assert ad2.price == 4800.0
        assert ad2.year == 2002
        assert ad2.mileage == 310000
        assert "2.4 л" in ad2.engine
        assert ad2.fuel_type == "газ/бензин"
        assert ad2.transmission == "Автомат"
        assert ad2.location == "Львів"

        # Item 3: 2.5 TDI 2001
        ad3 = listings[2]
        assert ad3.source_id == "36482147"
        assert ad3.price == 3700.0
        assert "2.5 л" in ad3.engine

        # Item 4: Dismantler ad (parsed by scraper, downstream filter rejects)
        ad4 = listings[3]
        assert ad4.source_id == "36482148"
        assert "Розбірка" in ad4.title

    @pytest.mark.asyncio
    async def test_handles_empty_search_page(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text="<html><body></body></html>"))

        parser = AutoRiaParser(client=mock_client, max_pages=1, request_delay=0)
        items = [item async for item in parser.fetch_new_listings()]
        assert len(items) == 0
        assert parser.stats.items_valid == 0

    @pytest.mark.asyncio
    async def test_card_parsing_error_containment(self):
        # HTML with 1 good card, 1 corrupted card (no ID or link), 1 good card
        corrupt_html = """
        <section class="ticket-item" data-auto-id="101">
            <a class="address" href="/auto_101.html">Audi A6 2001</a>
            <span data-currency="USD">4 000 $</span>
        </section>
        <section class="ticket-item">
            <!-- completely empty, no data-auto-id and no link -->
        </section>
        <section class="ticket-item" data-auto-id="102">
            <a class="address" href="/auto_102.html">Audi A6 2002</a>
            <span data-currency="USD">4 500 $</span>
        </section>
        """
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text=corrupt_html))

        parser = AutoRiaParser(client=mock_client, max_pages=1, request_delay=0)
        items = [item async for item in parser.fetch_new_listings()]
        assert len(items) == 2
        assert items[0].source_id == "101"
        assert items[1].source_id == "102"


# ==============================================================================
# 3. OLX Parser Tests
# ==============================================================================

class TestOlxParser:
    """Unit tests for OlxParser (JSON state and DOM fallback)."""

    @pytest.mark.asyncio
    async def test_parse_prerendered_state_json(self, json_fixture_loader):
        data = json_fixture_loader("olx/sample_prerendered_state.json")
        html_with_json = f"""
        <html>
        <head>
            <script>
                window.__PRERENDERED_STATE__ = {json.dumps(data)};
            </script>
        </head>
        <body><div id="root"></div></body>
        </html>
        """
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text=html_with_json))

        parser = OlxParser(client=mock_client, max_pages=1, request_delay=0)
        listings: List[RawListingPayload] = []

        async for item in parser.fetch_new_listings():
            listings.append(item)

        assert len(listings) == 3
        assert parser.stats.items_valid == 3

        # Ad 1: 1.9 TDI 2003 AWX
        ad1 = listings[0]
        assert ad1.source == "olx"
        assert ad1.source_id == "829104812"
        assert ad1.year == 2003
        assert ad1.price == 4500.0
        assert ad1.currency == "USD"
        assert ad1.engine == "1.9"
        assert ad1.fuel_type == "Дизель"
        assert ad1.mileage == 320000
        assert ad1.transmission == "Механічна"
        assert ad1.location == "Луцьк"
        assert len(ad1.image_urls) == 2
        assert "1000x700" in ad1.image_urls[0]
        assert "{width}" not in ad1.image_urls[0]

        # Ad 2: 1.8 Turbo 2000
        ad2 = listings[1]
        assert ad2.source_id == "829104813"
        assert ad2.year == 2000
        assert ad2.price == 3900.0
        assert ad2.location == "Вінниця"

    @pytest.mark.asyncio
    async def test_dom_fallback_when_json_state_missing(self, fixture_loader):
        dom_html = fixture_loader("olx/sample_listing_card.html")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text=dom_html))

        parser = OlxParser(client=mock_client, max_pages=1, request_delay=0)
        listings: List[RawListingPayload] = []

        async for item in parser.fetch_new_listings():
            listings.append(item)

        assert len(listings) == 1
        ad = listings[0]
        assert ad.source == "olx"
        assert ad.source_id == "830112233"
        assert "Audi A6 C5 1.8T 2001" in ad.title
        assert ad.price == 4200.0
        assert ad.currency == "USD"
        assert "Київ" in ad.location
        assert len(ad.image_urls) == 1


# ==============================================================================
# 4. RST.ua Parser Tests
# ==============================================================================

class TestRstParser:
    """Unit tests for RstParser."""

    @pytest.mark.asyncio
    async def test_parse_sample_rst_search(self, fixture_loader):
        html_content = fixture_loader("rst/sample_rst_search.html")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text=html_content, headers={"content-type": "text/html; charset=utf-8"}))

        parser = RstParser(client=mock_client, max_pages=1, request_delay=0)
        listings: List[RawListingPayload] = []

        async for item in parser.fetch_new_listings():
            listings.append(item)

        assert len(listings) == 3

        # Ad 1: Audi A6 C5 2001 2.4 gas/petrol
        ad1 = listings[0]
        assert ad1.source == "rst"
        assert ad1.source_id == "14238910"
        assert ad1.url == "https://rst.ua/ukr/oldcars/audi/a6/audi_a6_14238910.html"
        assert ad1.year == 2001
        assert ad1.price == 4100.0
        assert ad1.currency == "USD"
        assert "2.4 газ-бензин" in ad1.engine
        assert ad1.mileage == 295000
        assert ad1.location == "Київ"
        assert len(ad1.image_urls) == 1
        assert ad1.image_urls[0].endswith("_0.jpg")  # Converted from _1.jpg

        # Ad 2: Audi A6 Avant 2002 1.9 TDI
        ad2 = listings[1]
        assert ad2.source_id == "14238911"
        assert ad2.price == 4600.0
        assert "1.9 дизель" in ad2.engine
        assert ad2.mileage == 310000
        assert ad2.location == "Рівне"

    def test_charset_decoding_windows_1251_and_utf8(self):
        text = "Ауді А6 С5 1.8Т"
        # Test UTF-8 bytes
        utf8_bytes = text.encode("utf-8")
        decoded_utf8 = RstParser.decode_response_bytes(utf8_bytes, "text/html; charset=utf-8")
        assert decoded_utf8 == text

        # Test Windows-1251 bytes
        win1251_text = "Ауди А6 С5"
        win_bytes = win1251_text.encode("windows-1251")
        decoded_win = RstParser.decode_response_bytes(win_bytes, "text/html; charset=windows-1251")
        assert decoded_win == win1251_text

    @pytest.mark.asyncio
    async def test_rst_card_without_photos(self):
        no_photo_html = """
        <div class="rst-ocb-i">
            <a class="rst-ocb-i-a" href="/ukr/oldcars/audi/a6/audi_a6_99999.html">
                <h3 class="rst-ocb-i-h">Audi A6 C5 2000</h3>
                <span class="rst-ocb-i-d-s-p">3 900 $</span>
                <div class="rst-ocb-i-d-d">1.8 бензин, седан, 250 тис.км</div>
            </a>
        </div>
        """
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text=no_photo_html))

        parser = RstParser(client=mock_client, max_pages=1, request_delay=0)
        items = [item async for item in parser.fetch_new_listings()]
        assert len(items) == 1
        assert items[0].image_urls == []


# ==============================================================================
# 5. Telegram Channel Parser Tests
# ==============================================================================

class TestTelegramChannelParser:
    """Unit tests for TelegramChannelParser."""

    @pytest.mark.asyncio
    async def test_degrades_gracefully_when_unconfigured(self):
        # No credentials or client injected
        parser = TelegramChannelParser(api_id=None, api_hash=None, session_string=None, channels=["@autobazar"])
        items = [item async for item in parser.fetch_new_listings()]
        assert len(items) == 0
        assert parser.stats.status == "DEGRADED"

    def test_parse_message_specs_ukrainian(self):
        parser = TelegramChannelParser(channels=[])
        text = (
            "🔥 Audi A6 C5 2002 1.8 Turbo\n"
            "Рік: 2002\n"
            "Ціна: 4 300 $\n"
            "Двигун: 1.8 Turbo (газ/бензин)\n"
            "Пробіг: 275 тис. км\n"
            "Коробка: Механіка\n"
            "Місто: Львів\n"
            "Тел: +380671234567\n"
        )
        specs = parser.parse_message_specs(text)
        assert specs["year"] == 2002
        assert specs["price"] == 4300.0
        assert specs["currency"] == "USD"
        assert "1.8" in specs["engine"]
        assert specs["fuel_type"] == "газ/бензин"
        assert specs["mileage"] == 275000
        assert specs["transmission"] == "механіка"
        assert specs["location"] == "Львів"
        assert specs["seller_phone"] == "+380671234567"

    @pytest.mark.asyncio
    async def test_parse_standalone_and_album_posts_with_mock_client(self, json_fixture_loader):
        posts_data = json_fixture_loader("telegram/sample_channel_posts.json")

        # Create mock Telethon client
        mock_client = MagicMock()
        mock_client.is_connected = MagicMock(return_value=True)

        async def mock_iter_messages(entity, limit=30):
            for p in posts_data:
                msg_obj = MagicMock()
                msg_obj.id = p["id"]
                msg_obj.message = p["message"]
                msg_obj.date = datetime.fromisoformat(p["date"])
                msg_obj.grouped_id = p["grouped_id"]
                msg_obj.photo_urls = p.get("photo_urls", [])
                yield msg_obj

        mock_client.iter_messages = mock_iter_messages
        mock_client.get_entity = AsyncMock(return_value="entity")

        parser = TelegramChannelParser(channels=["@autobazar_ukraine"], client=mock_client)
        listings: List[RawListingPayload] = []

        async for item in parser.fetch_new_listings():
            listings.append(item)

        # 4 messages in fixture:
        # - 10421 + 10422 (grouped album with grouped_id 9988776611) -> 1 ad
        # - 10425 (standalone ad) -> 1 ad
        # - 10426 (standalone buyer ad without photos) -> 1 ad
        assert len(listings) == 3

        # Album ad
        album_ad = next(item for item in listings if item.extra_attributes.get("is_album"))
        assert album_ad.source_id == "autobazar_ukraine_10421"
        assert "Audi A6 C5 2002 1.8T" in album_ad.title
        assert album_ad.price == 4300.0
        assert len(album_ad.image_urls) == 3

        # Standalone ad 10425
        single_ad = next(item for item in listings if item.source_id == "autobazar_ukraine_10425")
        assert "Audi A6 1.9 TDI 2003" in single_ad.title
        assert single_ad.price == 4800.0
        assert single_ad.location == "Тернопіль"


# ==============================================================================
# 6. Instagram Parser Tests
# ==============================================================================

class TestInstagramParser:
    """Unit tests for InstagramParser."""

    @pytest.mark.asyncio
    async def test_parse_profile_json_response(self, json_fixture_loader):
        data = json_fixture_loader("instagram/sample_profile_response.json")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, json=data))

        parser = InstagramParser(accounts=["@autopodbor_ua"], client=mock_client, request_delay=0)
        listings: List[RawListingPayload] = []

        async for item in parser.fetch_new_listings():
            listings.append(item)

        assert len(listings) == 3

        # Post 1: Audi A6 C5 2001 2.4 gas/petrol
        p1 = listings[0]
        assert p1.source == "instagram"
        assert p1.source_id == "ig_C89X12345"
        assert p1.url == "https://www.instagram.com/p/C89X12345/"
        assert p1.year == 2001
        assert p1.price == 4200.0
        assert p1.location == "Київ"
        assert p1.seller == "@autopodbor_ua"
        assert len(p1.image_urls) == 1

        # Post 2: Audi A6 C5 1.9 TDI 2004
        p2 = listings[1]
        assert p2.source_id == "ig_C89X12346"
        assert p2.year == 2004
        assert p2.price == 5100.0
        assert p2.mileage == 295000
        assert p2.location == "Луцьк"

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_login_wall_html(self, fixture_loader):
        login_html = fixture_loader("instagram/sample_login_wall.html")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text=login_html))

        parser = InstagramParser(accounts=["@autopodbor_ua"], client=mock_client, request_delay=0)
        items = [item async for item in parser.fetch_new_listings()]

        assert len(items) == 0
        assert parser.stats.status == "DEGRADED"

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_http_401_or_403(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(401, text="Unauthorized"))

        parser = InstagramParser(accounts=["@autopodbor_ua"], client=mock_client, request_delay=0)
        items = [item async for item in parser.fetch_new_listings()]

        assert len(items) == 0
        assert parser.stats.status == "DEGRADED"


# ==============================================================================
# 7. Error Isolation Tests Across Multi-Source Pipeline
# ==============================================================================

class TestMultiParserErrorIsolation:
    """Verifies that an error or crash in one parser never halts other parsers."""

    @pytest.mark.asyncio
    async def test_failing_parser_does_not_halt_pipeline(self, fixture_loader, json_fixture_loader):
        # 1. Working AutoRia parser
        ria_html = fixture_loader("auto_ria/sample_search_page.html")
        mock_ria_client = AsyncMock(spec=httpx.AsyncClient)
        mock_ria_client.get = AsyncMock(return_value=httpx.Response(200, text=ria_html))
        parser_ria = AutoRiaParser(client=mock_ria_client, request_delay=0)

        # 2. Crashing Parser (e.g. raises network exception or unexpected error)
        class CrashingParser(BaseParser):
            def __init__(self):
                super().__init__(name="crashing", base_url="https://fail.com")
            async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]:
                self.stats.start()
                self.stats.record_error()
                raise ConnectionResetError("Connection abruptly closed by peer!")
                if False:
                    yield None  # Mark as async generator

        parser_fail = CrashingParser()

        # 3. Working RST parser
        rst_html = fixture_loader("rst/sample_rst_search.html")
        mock_rst_client = AsyncMock(spec=httpx.AsyncClient)
        mock_rst_client.get = AsyncMock(return_value=httpx.Response(200, text=rst_html))
        parser_rst = RstParser(client=mock_rst_client, request_delay=0)

        # Ingestion loop running multiple sources with error isolation
        all_collected_listings: List[RawListingPayload] = []
        parsers = [parser_ria, parser_fail, parser_rst]

        async def run_single_parser(p: BaseParser) -> List[RawListingPayload]:
            results: List[RawListingPayload] = []
            try:
                async for item in p.fetch_new_listings():
                    results.append(item)
            except Exception as e:
                p.stats.record_error()
                p.stats.finish(status="FAILED")
            return results

        tasks = [run_single_parser(p) for p in parsers]
        results_lists = await asyncio.gather(*tasks, return_exceptions=False)

        for res in results_lists:
            all_collected_listings.extend(res)

        # AutoRia (4) + RST (3) = 7 listings collected despite crashing parser!
        assert len(all_collected_listings) == 7
        assert parser_fail.stats.status == "FAILED"
        assert parser_fail.stats.errors >= 1
        assert parser_ria.stats.status == "SUCCESS"
        assert parser_rst.stats.status == "SUCCESS"
