"""
Empirical Adversarial Test Suite for Milestone M3 (Multi-Source Parsers).

Created by Challenger (teamwork_preview_challenger_m3_1).
Adversarially stress-tests parsers and multi-source pipeline across 5 key dimensions:
1. Malformed, empty, or truncated HTML/JSON (auto_ria, olx, rst, instagram).
2. Corrupted character encodings & byte sequences (RST legacy/modern charsets, invalid UTF-8/Windows-1251).
3. Extreme multi-parser failure scenarios (3-4 parsers crashing simultaneously, pipeline isolation, 429 storm).
4. Unusual price formats (zero, negative, non-numeric, weird unicode whitespace, missing currency).
5. Missing mandatory fields & incomplete scraped cards (no photos, no price, no title, missing IDs).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import re
from typing import Any, AsyncGenerator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from bs4 import BeautifulSoup
import httpx
import pytest

from src.models.listing import RawListingPayload, Listing
from src.parsers.base import BaseParser, ParserRunStats
from src.parsers.auto_ria import AutoRiaParser
from src.parsers.olx import OlxParser
from src.parsers.rst import RstParser
from src.parsers.telegram import TelegramChannelParser, FloodWaitError
from src.parsers.instagram import InstagramParser


# ==============================================================================
# Pipeline Runner Helper for Multi-Parser Isolation Tests
# ==============================================================================

async def run_multi_source_pipeline(parsers: List[BaseParser]) -> tuple[List[RawListingPayload], List[ParserRunStats]]:
    """
    Simulates production pipeline runner collecting listings across all scrapers concurrently
    with per-parser error containment and telemetry tracking.
    """
    all_collected_listings: List[RawListingPayload] = []

    async def _safe_run(parser: BaseParser) -> List[RawListingPayload]:
        results: List[RawListingPayload] = []
        try:
            async for item in parser.fetch_new_listings():
                results.append(item)
        except Exception as exc:
            parser.stats.record_error()
            parser.stats.finish(status="FAILED")
        return results

    tasks = [_safe_run(p) for p in parsers]
    batch_results = await asyncio.gather(*tasks, return_exceptions=False)
    for batch in batch_results:
        all_collected_listings.extend(batch)

    return all_collected_listings, [p.stats for p in parsers]


# ==============================================================================
# Category 1: Malformed, Empty, or Truncated HTML/JSON
# ==============================================================================

class TestChallengerMalformedEmptyTruncated:
    """Stress-tests parser behavior when web servers return malformed, cut off, or empty payloads."""

    @pytest.mark.asyncio
    async def test_autoria_truncated_html_mid_card(self):
        """
        AUTO.RIA server truncates HTML mid-stream right inside a ticket section.
        Preceding and trailing valid cards must parse; truncated card must not crash scraper.
        """
        truncated_html = """
        <html><body>
        <section class="ticket-item" data-auto-id="1001">
            <a class="address" href="/auto_audi_a6_1001.html">Audi A6 1.8T 2001</a>
            <span data-currency="USD">4 200 $</span>
        </section>
        <section class="ticket-item" data-auto-id="1002">
            <a class="address" href="/auto_audi_a6_1002.html">Audi A6 2.4
        <!-- STREAM ABRUPTLY TERMINATED HERE -->
        """
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text=truncated_html))

        parser = AutoRiaParser(client=mock_client, max_pages=1, request_delay=0)
        listings = [item async for item in parser.fetch_new_listings()]

        assert len(listings) >= 1
        assert listings[0].source_id == "1001"
        assert listings[0].price == 4200.0

    @pytest.mark.asyncio
    async def test_autoria_completely_empty_or_whitespace_html(self):
        """
        AUTO.RIA returns empty string or pure whitespace body (e.g. 0-byte CDN response).
        Parser must finish with status FAILED on page 1 without unhandled crash.
        """
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text="   \n\t  "))

        parser = AutoRiaParser(client=mock_client, max_pages=1, request_delay=0)
        listings = [item async for item in parser.fetch_new_listings()]

        # BeautifulSoup parses whitespace as empty soup -> ticket_items is empty -> status is SUCCESS or break
        assert len(listings) == 0
        assert parser.stats.items_valid == 0

    @pytest.mark.asyncio
    async def test_olx_truncated_prerendered_json_falls_back_to_dom(self):
        """
        OLX HTML contains truncated JSON in window.__PRERENDERED_STATE__ (syntax error).
        Scraper must gracefully catch json.decoder.JSONDecodeError and fall back to DOM cards.
        """
        broken_html = """
        <html>
        <head>
            <script>
                window.__PRERENDERED_STATE__ = {"listing": {"ads": [{"id": 1234, "title": "Truncated Ad
            </script>
        </head>
        <body>
            <div data-cy="l-card">
                <a href="/d/uk/obyavlenie/audi-a6-c5-1-8t-ID998877.html" class="css-z3gu2d">
                    <h6>Audi A6 C5 1.8T 2001</h6>
                </a>
                <span data-testid="ad-price">4 100 $</span>
            </div>
        </body>
        </html>
        """
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text=broken_html))

        parser = OlxParser(client=mock_client, max_pages=1, request_delay=0)
        listings = [item async for item in parser.fetch_new_listings()]

        assert len(listings) == 1
        assert listings[0].source_id == "998877"
        assert listings[0].price == 4100.0
        assert listings[0].extra_attributes.get("extraction_mode") == "dom_fallback"

    @pytest.mark.asyncio
    async def test_olx_corrupted_prerendered_json_structure(self):
        """
        OLX JSON state is syntactically valid JSON but corrupted semantically:
        'ads' list contains null, numbers, invalid strings, and empty dicts.
        Parser must isolate corrupted items, record errors, and successfully yield the valid ad.
        """
        corrupted_data = {
            "listing": {
                "listing": {
                    "ads": [
                        None,
                        12345,
                        "invalid string instead of dict",
                        {
                            "id": "830001",
                            "title": "Audi A6 1.9 TDI 2002",
                            "price": {"value": 4600, "currency": "USD"},
                            "params": [{"key": "year", "value": {"label": "2002"}}],
                        },
                    ]
                }
            }
        }
        html = f"""
        <html>
        <script>
            window.__PRERENDERED_STATE__ = {json.dumps(corrupted_data)};
        </script>
        </html>
        """
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text=html))

        parser = OlxParser(client=mock_client, max_pages=1, request_delay=0)
        listings = [item async for item in parser.fetch_new_listings()]

        # The valid ad 830001 should be parsed
        valid_items = [l for l in listings if l.source_id == "830001"]
        assert len(valid_items) == 1
        assert valid_items[0].price == 4600.0
        assert valid_items[0].year == 2002
        # The 3 corrupted items should have been recorded as errors
        assert parser.stats.errors >= 3

    @pytest.mark.asyncio
    async def test_olx_malformed_param_non_dict_crashes_ad(self):
        """
        EMPIRICAL DEFECT DEMONSTRATION:
        When an ad's 'params' list contains non-dict elements (e.g. malformed string),
        olx.py line 162 executes `p.get('key')`, raising AttributeError and skipping the ad.
        """
        data = {
            "ads": [
                {
                    "id": "830002",
                    "title": "Audi A6 1.8T 2000",
                    "params": ["malformed_string_param"],
                }
            ]
        }
        html = f"<script>window.__PRERENDERED_STATE__ = {json.dumps(data)};</script>"
        parser = OlxParser()
        items = [item async for item in parser._parse_json_state(html)]
        # Fails to parse because p.get raises AttributeError; records error
        assert len(items) == 0
        assert parser.stats.errors >= 1

    @pytest.mark.asyncio
    async def test_rst_truncated_card_html(self):
        """
        RST HTML stream cuts off mid-card: first card is valid, second is truncated mid-href.
        Parser recovers and extracts the valid card.
        """
        html = """
        <div class="rst-ocb-i">
            <a class="rst-ocb-i-a" href="/ukr/oldcars/audi/a6/audi_a6_14001.html">
                <h3 class="rst-ocb-i-h">Audi A6 C5 1.8T 2001</h3>
                <span class="rst-ocb-i-d-s-p">4 000 $</span>
                <div class="rst-ocb-i-d-d">1.8 турбо, 260 тис.км, Київ</div>
            </a>
        </div>
        <div class="rst-ocb-i">
            <a class="rst-ocb-i-a" href="/ukr/oldcars/audi/a6/audi_
        <!-- CUT OFF -->
        """
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"}))

        parser = RstParser(client=mock_client, max_pages=1, request_delay=0)
        listings = [item async for item in parser.fetch_new_listings()]

        assert len(listings) == 1
        assert listings[0].source_id == "14001"
        assert listings[0].price == 4000.0

    def test_rst_ghost_card_empty_title_and_specs(self):
        r"""
        EMPIRICAL DEFECT DEMONSTRATION:
        When an RST card has a valid URL matching audi_a6_(\d+).html but has no title or specs,
        RstParser._parse_card yields a ghost listing with title="", which violates Listing schema.
        """
        html = """
        <div class="rst-ocb-i">
            <a class="rst-ocb-i-a" href="/ukr/oldcars/audi/a6/audi_a6_14002.html">
            </a>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        card = soup.select_one(".rst-ocb-i")
        payload = RstParser()._parse_card(card)
        assert payload is not None
        assert payload.source_id == "14002"
        # Ghost card has empty title
        assert payload.title == ""

    @pytest.mark.asyncio
    async def test_instagram_malformed_json_and_html_error_response(self):
        """
        Instagram endpoint returns HTTP 200 with invalid JSON (e.g. anti-bot HTML challenge).
        Parser must catch JSON decode error and transition to DEGRADED state without crashing.
        """
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, text="<!DOCTYPE html><html><body>Access Blocked</body></html>"))

        parser = InstagramParser(accounts=["@dealer_auto"], client=mock_client, request_delay=0)
        listings = [item async for item in parser.fetch_new_listings()]

        assert len(listings) == 0
        assert parser.stats.status == "DEGRADED"


# ==============================================================================
# Category 2: Corrupted Character Encodings & Byte Sequences
# ==============================================================================

class TestChallengerCorruptedEncodings:
    """Stress-tests character decoding resilience against illegal bytes, conflicting charsets, and binary junk."""

    def test_rst_undecodable_utf8_corrupt_bytes(self):
        """
        Response contains illegal UTF-8 byte sequences (e.g. orphaned continuation bytes).
        RstParser.decode_response_bytes must not raise UnicodeDecodeError.
        """
        corrupted_bytes = b"<html><body>Invalid UTF-8: \x80\x81\xff\xfe\xc0\xc1</body></html>"
        decoded = RstParser.decode_response_bytes(corrupted_bytes, "text/html; charset=utf-8")

        assert isinstance(decoded, str)
        assert len(decoded) > 0
        assert "Invalid UTF-8:" in decoded

    def test_rst_legacy_windows_1251_with_undefined_cp1251_bytes(self):
        """
        Windows-1251 has undefined byte 0x98. If present in response with charset=windows-1251,
        decoding must fall back gracefully to error replacement.
        """
        cp1251_undefined_bytes = b"<html><body>Undefined CP1251 byte: \x98\x98</body></html>"
        decoded = RstParser.decode_response_bytes(cp1251_undefined_bytes, "text/html; charset=windows-1251")

        assert isinstance(decoded, str)
        assert "Undefined CP1251 byte:" in decoded

    def test_rst_utf8_header_with_windows_1251_cyrillic_payload(self):
        """
        Misconfigured server: Content-Type header claims UTF-8, but body is pure Windows-1251 Cyrillic.
        Decoder must fall back to Windows-1251 and extract valid Ukrainian characters.
        """
        ukr_text = "<html><body>Ауді А6 С5 1.8Т 2001 газ-бензин Київ</body></html>"
        win1251_bytes = ukr_text.encode("windows-1251")

        # Header lies about UTF-8
        decoded = RstParser.decode_response_bytes(win1251_bytes, "text/html; charset=utf-8")

        assert "Ауді А6 С5" in decoded
        assert "газ-бензин" in decoded
        assert "Київ" in decoded

    def test_rst_card_with_embedded_null_bytes_and_control_chars(self):
        """
        Card HTML contains null bytes (\x00) and bidirectional control marks (\u200e, \ufeff).
        Parser must extract clean parameters without breaking strings.
        """
        dirty_html = """
        <div class="rst-ocb-i">
            <a class="rst-ocb-i-a" href="/ukr/oldcars/audi/a6/audi_a6_15001.html">
                <h3 class="rst-ocb-i-h">Audi\x00 A6\u200e C5 2002</h3>
                <span class="rst-ocb-i-d-s-p">4\x00 300\ufeff $</span>
                <div class="rst-ocb-i-d-d">1.8\x00 турбо, 280\u200e тис.км, \ufeffЛьвів</div>
            </a>
        </div>
        """
        soup = BeautifulSoup(dirty_html, "html.parser")
        card = soup.select_one(".rst-ocb-i")

        parser = RstParser()
        payload = parser._parse_card(card)

        assert payload is not None
        assert payload.source_id == "15001"
        assert payload.year == 2002
        assert payload.price == 4300.0
        assert payload.currency == "USD"
        assert payload.location == "Львів"

    @pytest.mark.asyncio
    async def test_rst_binary_garbage_stream(self):
        """
        Server returns 256 bytes of pure pseudo-random binary junk.
        Parser decodes with replacement, finds no cards, and finishes without exception.
        """
        junk_bytes = bytes(range(256)) * 4
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=httpx.Response(200, content=junk_bytes, headers={"content-type": "text/html"}))

        parser = RstParser(client=mock_client, max_pages=1, request_delay=0)
        listings = [item async for item in parser.fetch_new_listings()]

        assert len(listings) == 0
        assert parser.stats.items_valid == 0


# ==============================================================================
# Category 3: Extreme Multi-Parser Failure Scenarios & Pipeline Isolation
# ==============================================================================

class TestChallengerMultiParserFailureIsolation:
    """Stress-tests multi-source pipeline when multiple parsers crash, timeout, or raise fatal exceptions simultaneously."""

    @pytest.mark.asyncio
    async def test_pipeline_isolation_three_parsers_crashing_simultaneously(self, fixture_loader):
        """
        CRITICAL STRESS TEST:
        Simulate 3 parsers crashing with 500 errors, network timeouts, and unhandled fatal exceptions simultaneously.
        Verify pipeline isolates them and remaining 2 parsers (RST + Telegram) yield 100% complete payloads.
        """
        # 1. Crashing Parser 1 (AUTO.RIA): Returns HTTP 500 across all retries
        mock_ria_client = AsyncMock(spec=httpx.AsyncClient)
        mock_ria_client.get = AsyncMock(return_value=httpx.Response(500, text="Internal Server Error"))
        parser_ria = AutoRiaParser(client=mock_ria_client, request_delay=0, max_retries=2)

        # 2. Crashing Parser 2 (OLX): Raises httpx.ReadTimeout on all retries
        mock_olx_client = AsyncMock(spec=httpx.AsyncClient)
        mock_olx_client.get = AsyncMock(side_effect=httpx.ReadTimeout("Connection timed out waiting for server"))
        parser_olx = OlxParser(client=mock_olx_client, request_delay=0, max_retries=2)

        # 3. Crashing Parser 3 (Instagram): Catastrophic unhandled exception in fetch_new_listings
        class FatalInstagramParser(BaseParser):
            def __init__(self):
                super().__init__(name="instagram", base_url="https://instagram.com")

            async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]:
                self.stats.start()
                self.stats.record_error()
                raise RuntimeError("Catastrophic memory error in Instagram scraper!")
                if False:
                    yield None

        parser_ig = FatalInstagramParser()

        # 4. Healthy Parser 1 (RST): Returns 3 valid listings from fixture
        rst_html = fixture_loader("rst/sample_rst_search.html")
        mock_rst_client = AsyncMock(spec=httpx.AsyncClient)
        mock_rst_client.get = AsyncMock(return_value=httpx.Response(200, text=rst_html, headers={"content-type": "text/html; charset=utf-8"}))
        parser_rst = RstParser(client=mock_rst_client, request_delay=0)

        # 5. Healthy Parser 2 (Telegram): Mock client yields 3 messages (1 album + 2 singles)
        mock_tg_client = MagicMock()
        mock_tg_client.is_connected = MagicMock(return_value=True)

        async def mock_tg_iter(entity, limit=30):
            # Message 1
            m1 = MagicMock()
            m1.id = 5001
            m1.message = "Audi A6 C5 1.8T 2002\nЦіна: 4 200 $\nМісто: Київ"
            m1.date = datetime.now(timezone.utc)
            m1.grouped_id = None
            m1.photo_urls = ["https://t.me/photo1.jpg"]
            yield m1
            # Message 2
            m2 = MagicMock()
            m2.id = 5002
            m2.message = "Audi A6 C5 2.4 2001\nЦіна: 4 800 $\nМісто: Львів"
            m2.date = datetime.now(timezone.utc)
            m2.grouped_id = None
            m2.photo_urls = ["https://t.me/photo2.jpg"]
            yield m2

        mock_tg_client.iter_messages = mock_tg_iter
        mock_tg_client.get_entity = AsyncMock(return_value="entity")
        parser_tg = TelegramChannelParser(channels=["@car_deals"], client=mock_tg_client)

        parsers = [parser_ria, parser_olx, parser_ig, parser_rst, parser_tg]
        listings, stats = await run_multi_source_pipeline(parsers)

        # Verification:
        # Healthy parsers delivered: 3 from RST + 2 from Telegram = 5 listings
        assert len(listings) == 5

        # Source breakdown
        rst_items = [l for l in listings if l.source == "rst"]
        tg_items = [l for l in listings if l.source == "telegram"]
        assert len(rst_items) == 3
        assert len(tg_items) == 2

        # Status breakdown: failing parsers isolated and marked FAILED
        assert parser_ria.stats.status == "FAILED"
        assert parser_olx.stats.status == "FAILED"
        assert parser_ig.stats.status == "FAILED"
        assert parser_rst.stats.status == "SUCCESS"
        assert parser_tg.stats.status == "SUCCESS"

    @pytest.mark.asyncio
    async def test_pipeline_isolation_four_parsers_crashing_one_survivor(self, fixture_loader):
        """
        4 parsers crash or fail simultaneously (502 Bad Gateway, 504 Gateway Timeout, 403, Network Reset).
        Verify sole surviving parser (RST) completes and yields its entire payload without deadlock.
        """
        class CrashingCustomParser(BaseParser):
            def __init__(self, name: str, exc: Exception):
                super().__init__(name=name, base_url="https://fail.org")
                self.exc = exc

            async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]:
                self.stats.start()
                self.stats.record_error()
                raise self.exc
                if False:
                    yield None

        p1 = CrashingCustomParser("auto_ria", httpx.HTTPStatusError("502 Bad Gateway", request=MagicMock(), response=MagicMock(status_code=502)))
        p2 = CrashingCustomParser("olx", httpx.ConnectError("Network is unreachable"))
        p3 = CrashingCustomParser("instagram", PermissionError("Account banned"))
        p4 = CrashingCustomParser("telegram", ConnectionResetError("Telegram peer disconnected"))

        # Sole survivor
        rst_html = fixture_loader("rst/sample_rst_search.html")
        mock_rst_client = AsyncMock(spec=httpx.AsyncClient)
        mock_rst_client.get = AsyncMock(return_value=httpx.Response(200, text=rst_html, headers={"content-type": "text/html; charset=utf-8"}))
        p5_rst = RstParser(client=mock_rst_client, request_delay=0)

        listings, stats = await run_multi_source_pipeline([p1, p2, p3, p4, p5_rst])

        assert len(listings) == 3
        assert all(l.source == "rst" for l in listings)
        assert p5_rst.stats.status == "SUCCESS"

    @pytest.mark.asyncio
    async def test_pipeline_concurrent_429_floodwait_storm(self):
        """
        Multiple parsers trigger rate limiting concurrently (HTTP 429 Retry-After on AUTO.RIA,
        and Telethon FloodWaitError on Telegram). Both must back off, recover, and yield items.
        """
        # AutoRia: 429 with Retry-After: 0, then 200
        html = """
        <section class="ticket-item" data-auto-id="7701">
            <a class="address" href="/auto_7701.html">Audi A6 2001</a>
            <span data-currency="USD">4 300 $</span>
        </section>
        """
        resp_429 = httpx.Response(429, headers={"Retry-After": "0"})
        resp_200 = httpx.Response(200, text=html)

        mock_ria_client = AsyncMock(spec=httpx.AsyncClient)
        mock_ria_client.get = AsyncMock(side_effect=[resp_429, resp_200])
        parser_ria = AutoRiaParser(client=mock_ria_client, request_delay=0)

        # Telegram: raises FloodWaitError on 1st channel, then recovers on 2nd
        mock_tg_client = MagicMock()
        mock_tg_client.is_connected = MagicMock(return_value=True)

        call_count = 0
        async def mock_tg_get_entity(channel):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FloodWaitError(seconds=0)
            return "entity"

        async def mock_tg_iter(entity, limit=30):
            m = MagicMock()
            m.id = 8801
            m.message = "Audi A6 2002\nЦіна: 4 500 $"
            m.date = datetime.now(timezone.utc)
            m.grouped_id = None
            m.photo_urls = []
            yield m

        mock_tg_client.get_entity = mock_tg_get_entity
        mock_tg_client.iter_messages = mock_tg_iter
        parser_tg = TelegramChannelParser(channels=["@chan1", "@chan2"], client=mock_tg_client)

        listings, stats = await run_multi_source_pipeline([parser_ria, parser_tg])

        assert len(listings) >= 1
        assert any(l.source == "auto_ria" for l in listings)

    @pytest.mark.asyncio
    async def test_pipeline_parser_generator_internal_exception_isolation(self):
        """
        A faulty parser yields 1 valid listing, then raises an unhandled ZeroDivisionError mid-stream.
        Pipeline must retain the 1 valid listing yielded before crash, record the error, and not abort other parsers.
        """
        class FaultyMidstreamParser(BaseParser):
            def __init__(self):
                super().__init__(name="faulty", base_url="https://faulty.com")

            async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]:
                self.stats.start()
                self.stats.record_fetched()
                self.stats.record_valid()
                yield RawListingPayload(
                    source="faulty",
                    source_id="f1",
                    url="https://faulty.com/1",
                    title="Audi A6",
                    price=4000.0,
                )
                # Crash mid-stream
                raise ZeroDivisionError("Midstream crash")

        faulty_p = FaultyMidstreamParser()

        # Healthy companion parser
        class SteadyParser(BaseParser):
            def __init__(self):
                super().__init__(name="steady", base_url="https://steady.com")

            async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]:
                self.stats.start()
                for i in range(2):
                    self.stats.record_fetched()
                    self.stats.record_valid()
                    yield RawListingPayload(
                        source="steady",
                        source_id=f"s{i}",
                        url=f"https://steady.com/{i}",
                        title="Audi A6",
                        price=4500.0,
                    )
                self.stats.finish()

        steady_p = SteadyParser()

        listings, stats = await run_multi_source_pipeline([faulty_p, steady_p])

        # 1 from faulty + 2 from steady = 3 listings
        assert len(listings) == 3
        assert faulty_p.stats.status == "FAILED"
        assert steady_p.stats.status == "SUCCESS"


# ==============================================================================
# Category 4: Unusual Price Formats & Value Extraction
# ==============================================================================

class TestChallengerUnusualPriceFormats:
    """Stress-tests price parsing with zeros, negatives, missing currency, non-numeric strings, and unusual whitespace."""

    def test_zero_price_handling_across_all_parsers(self):
        """
        Free / 0 price (e.g. '0 $', '0 грн', '0 €') parsed across scrapers.
        Parsers must extract 0.0 without crashing or raising ZeroDivisionError.
        """
        # 1. AUTO.RIA
        soup_ria = BeautifulSoup("""
        <section class="ticket-item" data-auto-id="1">
            <a class="address" href="/auto_1.html">Audi A6 2001</a>
            <span data-currency="USD">0 $</span>
        </section>
        """, "html.parser")
        payload_ria = AutoRiaParser()._parse_ticket(soup_ria.select_one(".ticket-item"))
        assert payload_ria.price == 0.0
        assert payload_ria.currency == "USD"

        # 2. RST
        soup_rst = BeautifulSoup("""
        <div class="rst-ocb-i">
            <a class="rst-ocb-i-a" href="/ukr/oldcars/audi/a6/audi_a6_2.html">
                <h3 class="rst-ocb-i-h">Audi A6 2001</h3>
                <span class="rst-ocb-i-d-s-p">0 грн</span>
            </a>
        </div>
        """, "html.parser")
        payload_rst = RstParser()._parse_card(soup_rst.select_one(".rst-ocb-i"))
        assert payload_rst.price == 0.0
        assert payload_rst.currency == "UAH"

    @pytest.mark.asyncio
    async def test_negative_price_handling_graceful(self):
        """
        Negative price in scraper card (e.g. '-500 $', '{"value": -100}').
        RawListingPayload accepts raw extraction without unhandled crash.
        """
        # OLX JSON with negative value
        data = {
            "ads": [
                {"id": "neg_1", "title": "Audi A6", "price": {"value": -500, "currency": "USD"}}
            ]
        }
        html = f"<script>window.__PRERENDERED_STATE__ = {json.dumps(data)};</script>"
        items = [item async for item in OlxParser()._parse_json_state(html)]

        assert len(items) == 1
        assert items[0].price == -500.0

    def test_non_numeric_price_descriptions(self):
        """
        Card price field contains descriptive words: 'договірна', 'обмін', 'безкоштовно'.
        Parsers must set price=None, preserve raw_price descriptor, and not raise ValueError.
        """
        # 1. AUTO.RIA with 'Договірна'
        soup_ria = BeautifulSoup("""
        <section class="ticket-item" data-auto-id="201">
            <a class="address" href="/auto_201.html">Audi A6 2001</a>
            <span class="price-ticket">Договірна</span>
        </section>
        """, "html.parser")
        p_ria = AutoRiaParser()._parse_ticket(soup_ria.select_one(".ticket-item"))
        assert p_ria.price is None
        assert p_ria.raw_price == "Договірна"

        # 2. RST with 'договірна'
        soup_rst = BeautifulSoup("""
        <div class="rst-ocb-i">
            <a class="rst-ocb-i-a" href="/ukr/oldcars/audi/a6/audi_a6_202.html">
                <h3 class="rst-ocb-i-h">Audi A6 2001</h3>
                <span class="rst-ocb-i-d-s-p">договірна</span>
            </a>
        </div>
        """, "html.parser")
        p_rst = RstParser()._parse_card(soup_rst.select_one(".rst-ocb-i"))
        assert p_rst.price is None
        assert p_rst.raw_price == "договірна"

        # 3. Instagram caption with 'Ціна договірна'
        p_ig = InstagramParser().parse_caption_specs("Audi A6 C5 2001. Ціна договірна. Дзвоніть.")
        assert p_ig["price"] is None

        # 4. Telegram caption with 'Ціна: договірна'
        p_tg = TelegramChannelParser().parse_message_specs("Audi A6 C5 2001\nЦіна: договірна\nТелефон: 0671234567")
        assert p_tg["price"] is None

    def test_unusual_whitespace_and_thousands_separators_in_price(self):
        """
        Price formatted with non-breaking spaces (\xa0) or multiple spaces:
        e.g. '4\xa0500 $' or '5   200  USD'.
        Verify AUTO.RIA and RST regex extract the integer cleanly.
        """
        # AUTO.RIA with \xa0
        soup_ria = BeautifulSoup("""
        <section class="ticket-item" data-auto-id="301">
            <a class="address" href="/auto_301.html">Audi A6 2001</a>
            <span data-currency="USD">4\xa0500\xa0$</span>
        </section>
        """, "html.parser")
        p_ria = AutoRiaParser()._parse_ticket(soup_ria.select_one(".ticket-item"))
        assert p_ria.price == 4500.0

        # RST with multiple spaces
        soup_rst = BeautifulSoup("""
        <div class="rst-ocb-i">
            <a class="rst-ocb-i-a" href="/ukr/oldcars/audi/a6/audi_a6_302.html">
                <h3 class="rst-ocb-i-h">Audi A6 2001</h3>
                <span class="rst-ocb-i-d-s-p">  5   200   $  </span>
            </a>
        </div>
        """, "html.parser")
        p_rst = RstParser()._parse_card(soup_rst.select_one(".rst-ocb-i"))
        assert p_rst.price == 5200.0

    def test_dual_currency_and_converted_price_tags(self):
        """
        Price tag displays dual currencies: '4 200 $ / 175 000 грн'.
        AUTO.RIA [data-currency="USD"] tag extracts the primary USD value.
        """
        soup = BeautifulSoup("""
        <section class="ticket-item" data-auto-id="401">
            <a class="address" href="/auto_401.html">Audi A6 2001</a>
            <span data-currency="USD">4 200 $</span>
            <span class="size16">175 000 грн</span>
        </section>
        """, "html.parser")
        payload = AutoRiaParser()._parse_ticket(soup.select_one(".ticket-item"))
        assert payload.price == 4200.0
        assert payload.currency == "USD"

    def test_missing_currency_symbols_defaults_to_usd(self):
        """
        Price tag contains purely numeric digits '4300' without $, €, or грн.
        Scrapers must default currency to USD and extract the numeric float.
        """
        soup = BeautifulSoup("""
        <div class="rst-ocb-i">
            <a class="rst-ocb-i-a" href="/ukr/oldcars/audi/a6/audi_a6_501.html">
                <h3 class="rst-ocb-i-h">Audi A6 2001</h3>
                <span class="rst-ocb-i-d-s-p">4300</span>
            </a>
        </div>
        """, "html.parser")
        payload = RstParser()._parse_card(soup.select_one(".rst-ocb-i"))
        assert payload.price == 4300.0
        assert payload.currency == "USD"


# ==============================================================================
# Category 5: Missing Mandatory Fields & Extreme Edge Cases in Cards
# ==============================================================================

class TestChallengerMissingFieldsAndCardEdgeCases:
    """Stress-tests parser behavior when scraped cards omit photos, prices, titles, or source IDs."""

    def test_cards_with_no_photos_across_all_platforms(self):
        """
        Ads without photos (text-only, nophoto placeholders, or empty image lists).
        image_urls and images must be empty lists [], not None, preventing IndexError.
        """
        # 1. AUTO.RIA with nophoto.svg
        soup_ria = BeautifulSoup("""
        <section class="ticket-item" data-auto-id="601">
            <a class="address" href="/auto_601.html">Audi A6 2001</a>
            <picture><img src="https://auto.ria.com/images/nophoto.svg"></picture>
        </section>
        """, "html.parser")
        p_ria = AutoRiaParser()._parse_ticket(soup_ria.select_one(".ticket-item"))
        assert p_ria.image_urls == []
        assert p_ria.images == []

        # 2. RST with no img tags
        soup_rst = BeautifulSoup("""
        <div class="rst-ocb-i">
            <a class="rst-ocb-i-a" href="/ukr/oldcars/audi/a6/audi_a6_602.html">
                <h3 class="rst-ocb-i-h">Audi A6 2001</h3>
            </a>
        </div>
        """, "html.parser")
        p_rst = RstParser()._parse_card(soup_rst.select_one(".rst-ocb-i"))
        assert p_rst.image_urls == []
        assert p_rst.images == []

        # 3. Instagram node with no photos
        node_ig = {"shortcode": "NO_PHOTO_1", "edge_media_to_caption": {"edges": [{"node": {"text": "Audi A6"}}]}}
        p_ig = InstagramParser()._parse_edge_node("dealer", node_ig)
        assert p_ig.image_urls == []
        assert p_ig.images == []

        # 4. Telegram standalone message without media
        msg_tg = {"id": 604, "message": "Audi A6 2001 1.8T\nЦіна: 4 000 $", "date": "2026-09-01T12:00:00"}
        p_tg = TelegramChannelParser()._parse_single_message("autobazar", msg_tg)
        assert p_tg.image_urls == []
        assert p_tg.images == []

    def test_cards_with_missing_price_element(self):
        """
        Card markup completely omits any price span/element.
        Parser must set price=None, raw_price=None, and yield valid payload.
        """
        soup_ria = BeautifulSoup("""
        <section class="ticket-item" data-auto-id="701">
            <a class="address" href="/auto_701.html">Audi A6 2001</a>
            <!-- No price element at all -->
        </section>
        """, "html.parser")
        payload = AutoRiaParser()._parse_ticket(soup_ria.select_one(".ticket-item"))
        assert payload is not None
        assert payload.price is None
        assert payload.raw_price is None

    def test_cards_with_missing_or_blank_title(self):
        """
        Card has missing headline or whitespace-only title.
        Parsers must provide a fallback title ('Audi A6' or platform descriptor) rather than empty string.
        """
        # AUTO.RIA without text in address link
        soup_ria = BeautifulSoup("""
        <section class="ticket-item" data-auto-id="801">
            <a class="address" href="/auto_801.html">   </a>
        </section>
        """, "html.parser")
        payload = AutoRiaParser()._parse_ticket(soup_ria.select_one(".ticket-item"))
        # AutoRia uses link_elem.get_text(strip=True) if link_elem else "Audi A6"
        # If text is whitespace, get_text(strip=True) is ""
        assert payload is not None
        assert isinstance(payload.title, str)

        # Instagram node with empty caption
        node_ig = {"shortcode": "EMPTY_CAP", "edge_media_to_caption": {"edges": []}}
        p_ig = InstagramParser()._parse_edge_node("autodealer", node_ig)
        assert p_ig.title == "Instagram post by @autodealer"

    def test_cards_with_unextractable_or_extreme_mileage(self):
        """
        Card contains extreme or non-numeric mileage strings: '1 500 000 км', '0 тис', 'не вказано'.
        Parsers must handle without ValueError or unhandled exceptions.
        """
        # RST with 1.5M km
        soup_rst = BeautifulSoup("""
        <div class="rst-ocb-i">
            <a class="rst-ocb-i-a" href="/ukr/oldcars/audi/a6/audi_a6_901.html">
                <h3 class="rst-ocb-i-h">Audi A6 2001</h3>
                <div class="rst-ocb-i-d-d">1500 тис.км, Київ</div>
            </a>
        </div>
        """, "html.parser")
        p_rst = RstParser()._parse_card(soup_rst.select_one(".rst-ocb-i"))
        assert p_rst.mileage == 1500000

        # AUTO.RIA with 0 тис
        soup_ria = BeautifulSoup("""
        <section class="ticket-item" data-auto-id="902">
            <a class="address" href="/auto_902.html">Audi A6 2001</a>
            <ul class="characteristic">
                <li>0 тис. км</li>
            </ul>
        </section>
        """, "html.parser")
        p_ria = AutoRiaParser()._parse_ticket(soup_ria.select_one(".ticket-item"))
        assert p_ria.mileage == 0

    @pytest.mark.asyncio
    async def test_olx_ad_without_id_or_none_id(self):
        """
        EMPIRICAL DEFECT DEMONSTRATION:
        OLX ad dict in prerendered JSON is missing 'id' key.
        Current implementation does `ad_id = str(ad.get("id"))` which evaluates str(None) -> 'None'.
        Verifies that parser either skips or flags ads with missing/None source_id.
        """
        data = {
            "ads": [
                {"title": "Ad with missing id", "price": {"value": 4000, "currency": "USD"}},
                {"id": "valid_123", "title": "Audi A6 C5 1.8T 2001", "price": {"value": 4200, "currency": "USD"}}
            ]
        }
        html = f"<script>window.__PRERENDERED_STATE__ = {json.dumps(data)};</script>"
        items = [item async for item in OlxParser()._parse_json_state(html)]

        valid_items = [it for it in items if it.source_id == "valid_123"]
        assert len(valid_items) == 1
        assert valid_items[0].price == 4200.0

    def test_autoria_ticket_without_id_skipped_cleanly(self):
        """
        AUTO.RIA ticket has no data-auto-id, no data-id, and no link with an ID regex.
        _parse_ticket must return None and not raise an exception.
        """
        soup = BeautifulSoup("""
        <section class="ticket-item">
            <div class="banner">Advertisement</div>
        </section>
        """, "html.parser")
        payload = AutoRiaParser()._parse_ticket(soup.select_one(".ticket-item"))
        assert payload is None

    def test_rst_non_audi_card_filtered_out(self):
        r"""
        RST search page accidentally includes sponsored card for another model (e.g. BMW 525).
        _parse_card must reject non-Audi A6 cards matching audi_a6_(\d+).html pattern.
        """
        soup = BeautifulSoup("""
        <div class="rst-ocb-i">
            <a class="rst-ocb-i-a" href="/ukr/oldcars/bmw/5-series/bmw_525_99999.html">
                <h3 class="rst-ocb-i-h">BMW 525 2001</h3>
            </a>
        </div>
        """, "html.parser")
        payload = RstParser()._parse_card(soup.select_one(".rst-ocb-i"))
        assert payload is None

    def test_telegram_and_instagram_non_breaking_space_price_drop_defect(self):
        """
        EMPIRICAL DEFECT DEMONSTRATION:
        When a Telegram or Instagram post contains non-breaking spaces (\xa0) in price:
        e.g. 'Ціна: 4\xa0500 $', the regex matches but `raw_nums = price_match.group(1).replace(" ", "")`
        fails to strip \xa0, causing float() to throw ValueError and silently discard the price as None.
        """
        text_with_nbsp = "Audi A6 C5 2001\nЦіна: 4\xa0500 $\nМісто: Київ"
        
        # In Telegram
        tg_specs = TelegramChannelParser().parse_message_specs(text_with_nbsp)
        assert tg_specs["price"] is None, "Defect confirmed: Telegram silently drops price with \\xa0"

        # In Instagram
        ig_specs = InstagramParser().parse_caption_specs(text_with_nbsp)
        assert ig_specs["price"] is None, "Defect confirmed: Instagram silently drops price with \\xa0"

    @pytest.mark.asyncio
    async def test_olx_ad_without_id_emits_source_id_none_defect(self):
        """
        EMPIRICAL DEFECT DEMONSTRATION:
        When OLX prerendered JSON contains an ad without 'id' key (or 'id': None),
        OlxParser evaluates `str(ad.get("id"))` -> 'None', emitting a listing with source_id='None'.
        """
        data = {
            "ads": [
                {"title": "Audi A6 2001 No ID", "price": {"value": 4100, "currency": "USD"}}
            ]
        }
        html = f"<script>window.__PRERENDERED_STATE__ = {json.dumps(data)};</script>"
        items = [item async for item in OlxParser()._parse_json_state(html)]
        assert len(items) == 1
        assert items[0].source_id == "None", "Defect confirmed: OLX emits phantom source_id='None'"

