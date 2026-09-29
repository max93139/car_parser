"""
Unit tests for Milestone M4: Telegram Notification Engine & Message Templating.
Tests 100% mocked dispatches for single photo, media groups, templates,
price drop alerts, needs review badges, rate limiting, and 429 FloodWait recovery.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import html
import time
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from src.database.models import ListingModel
from src.models.listing import Listing
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


@pytest.fixture
def sample_listing_data() -> Dict[str, Any]:
    return {
        "source": "auto_ria",
        "source_id": "36582914",
        "url": "https://auto.ria.com/uk/auto_audi_a6_36582914.html",
        "title": "Audi A6 1.8 T",
        "description": "Офіційна європейка, двигун AWT, турбіна суха, ходова зроблена.",
        "brand": "Audi",
        "model": "A6",
        "generation": "C5",
        "year": 2001,
        "price": 4500.0,
        "currency": "USD",
        "price_usd": 4500.0,
        "mileage": 265000,
        "engine": "1.8T (Бензин)",
        "engine_code": "1.8T",
        "transmission": "Механіка",
        "location": "Київ",
        "location_city": "Київ",
        "images": [
            "https://cdn.riastatic.com/photos/1.jpg",
            "https://cdn.riastatic.com/photos/2.jpg",
            "https://cdn.riastatic.com/photos/3.jpg",
        ],
    }


@pytest.fixture
def sample_listing(sample_listing_data: Dict[str, Any]) -> Listing:
    return Listing(**sample_listing_data)


# ==============================================================================
# 1. Message Templates & Formatting Tests
# ==============================================================================

class TestNotificationTemplates:
    """Tests for Ukrainian HTML templates, badges, specs, and caption length guards."""

    def test_format_source_badge_all_sources(self):
        assert format_source_badge("auto_ria") == "AUTO.RIA"
        assert format_source_badge("olx") == "OLX"
        assert format_source_badge("rst") == "RST.ua"
        assert format_source_badge("telegram") == "Telegram"
        assert format_source_badge("instagram") == "Instagram"
        assert format_source_badge("other_site") == "Other Site"
        assert format_source_badge(None) == "Marketplace"

    def test_format_price_usd_with_uah_conversion(self):
        res = format_price(4500.0, currency="USD")
        assert "$4 500" in res or "$4,500" in res
        assert "грн" in res

    def test_format_price_uah_with_usd_conversion(self):
        res = format_price(185000.0, currency="UAH")
        assert "185 000 грн" in res or "185,000 грн" in res
        assert "$" in res

    def test_format_price_eur_with_usd_conversion(self):
        res = format_price(4000.0, currency="EUR")
        assert "4 000 €" in res or "4,000 €" in res
        assert "$" in res

    def test_format_price_none_returns_negotiable(self):
        assert format_price(None, currency=None) == "Договірна"

    def test_format_mileage(self):
        assert "265 000 км" in format_mileage(265000)
        assert format_mileage(None) == "Не вказано"
        assert format_mileage(-5) == "Не вказано"

    def test_format_price_drop_badge(self):
        badge = format_price_drop_badge(old_price_usd=4800.0, new_price_usd=4500.0, diff_usd=-300.0)
        assert "ЦІНУ ЗНИЖЕНО" in badge
        assert "Нова:" in badge
        assert "$4 500" in badge

    def test_format_needs_review_badge(self):
        badge_simple = format_needs_review_badge()
        assert "ПОТРЕБУЄ ПЕРЕВІРКИ" in badge_simple

        badge_reason = format_needs_review_badge("Рік перехідний")
        assert "ПОТРЕБУЄ ПЕРЕВІРКИ" in badge_reason
        assert "Рік перехідний" in badge_reason

    def test_caption_contains_all_vital_specs(self, sample_listing: Listing):
        caption = format_listing_caption(sample_listing)
        assert "🚗 <b>Audi A6 1.8 T</b> (2001)" in caption
        assert "💰 <b>Ціна:</b>" in caption
        assert "4 500" in caption
        assert "📅 <b>Рік:</b> 2001" in caption
        assert "⚙️ <b>Двигун:</b> 1.8T (Бензин)" in caption
        assert "🕹️ <b>КПП:</b> Механіка" in caption
        assert "🛣️ <b>Пробіг:</b> 265 000 км" in caption
        assert "📍 <b>Місто:</b> Київ" in caption
        assert "🏷️ <b>Джерело:</b> AUTO.RIA" in caption
        assert "🔗 <a href=\"https://auto.ria.com/uk/auto_audi_a6_36582914.html\">" in caption
        assert "📝 <b>Опис:</b>" in caption

    def test_caption_includes_price_drop_when_passed(self, sample_listing: Listing):
        price_drop_info = {
            "is_price_drop": True,
            "old_price_usd": 4800.0,
            "new_price_usd": 4500.0,
            "diff_usd": -300.0,
        }
        caption = format_listing_caption(sample_listing, price_drop_info=price_drop_info)
        assert "ЦІНУ ЗНИЖЕНО" in caption
        assert "-$300" in caption

    def test_caption_includes_needs_review_badge(self, sample_listing: Listing):
        sample_listing.status = "NEEDS_REVIEW"
        caption = format_listing_caption(sample_listing)
        assert "ПОТРЕБУЄ ПЕРЕВІРКИ" in caption

    def test_caption_strict_length_limit_under_1024_with_massive_description(self, sample_listing: Listing):
        # 5,000 characters description
        sample_listing.description = "Дуже гарний стан авто. " * 300
        assert len(sample_listing.description) > 5000

        caption = format_listing_caption(sample_listing, max_length=1024)
        assert len(caption) <= 1024
        assert caption.endswith("...") or "Посилання на оголошення" in caption

    def test_caption_html_escaping_prevents_broken_tags(self):
        dangerous_listing = {
            "title": "Audi <script>alert(1)</script> & A6",
            "url": "https://example.com/test?a=1&b=2",
            "engine": "1.8T <Turbo>",
            "location": "Київ <Центр>",
            "description": "Condition: 10/10 <clean & fresh>",
        }
        caption = format_listing_caption(dangerous_listing)
        assert "<script>" not in caption
        assert "&lt;script&gt;" in caption
        assert "&lt;Turbo&gt;" in caption
        assert "&lt;clean &amp; fresh&gt;" in caption


# ==============================================================================
# 2. Telegram Notifier Media Strategy & Fallback Tests
# ==============================================================================

class TestTelegramNotifierMediaDispatch:
    """Tests for sendMediaGroup, sendPhoto, sendMessage, and fallback behaviors."""

    @pytest.mark.asyncio
    async def test_send_media_group_success_2_to_10_photos(self, sample_listing: Listing):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True, "result": [{"message_id": 101}, {"message_id": 102}]}
        mock_client.post.return_value = mock_response

        notifier = TelegramNotifier(
            bot_token="123456:TEST_BOT_TOKEN_ABC_DEF",
            chat_id="-1001234567890",
            client=mock_client,
            rate_limit_delay=0.0,
        )

        sent = await notifier.send_listing_alert(sample_listing)
        assert sent is True
        assert sample_listing.is_sent_to_telegram is True

        # Verify POST payload sent to sendMediaGroup
        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        call_json = mock_client.post.call_args[1]["json"]
        assert call_url.endswith("/sendMediaGroup")
        assert call_json["chat_id"] == "-1001234567890"
        assert len(call_json["media"]) == 3
        assert call_json["media"][0]["type"] == "photo"
        assert "caption" in call_json["media"][0]
        assert "parse_mode" in call_json["media"][0]
        assert "caption" not in call_json["media"][1]

    @pytest.mark.asyncio
    async def test_send_media_group_caps_at_10_photos(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True, "result": []}
        mock_client.post.return_value = mock_response

        notifier = TelegramNotifier(
            bot_token="123456:TEST_BOT_TOKEN_ABC_DEF",
            chat_id="-1001234567890",
            client=mock_client,
            rate_limit_delay=0.0,
        )

        photos_15 = [f"https://img.example.com/{i}.jpg" for i in range(15)]
        listing_15 = Listing(
            source="auto_ria",
            source_id="15photos",
            url="https://auto.ria.com/15",
            title="Audi A6",
            images=photos_15,
        )

        sent = await notifier.send_listing_alert(listing_15)
        assert sent is True
        call_json = mock_client.post.call_args[1]["json"]
        assert len(call_json["media"]) == 10

    @pytest.mark.asyncio
    async def test_send_photo_for_single_image(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True, "result": {"message_id": 201}}
        mock_client.post.return_value = mock_response

        notifier = TelegramNotifier(
            bot_token="123456:TEST_BOT_TOKEN_ABC_DEF",
            chat_id="-1001234567890",
            client=mock_client,
            rate_limit_delay=0.0,
        )

        listing_1_photo = Listing(
            source="olx",
            source_id="single",
            url="https://olx.ua/single",
            title="Audi A6 1 Photo",
            images=["https://img.example.com/single.jpg"],
        )

        sent = await notifier.send_listing_alert(listing_1_photo)
        assert sent is True
        call_url = mock_client.post.call_args[0][0]
        call_json = mock_client.post.call_args[1]["json"]
        assert call_url.endswith("/sendPhoto")
        assert call_json["photo"] == "https://img.example.com/single.jpg"
        assert "caption" in call_json

    @pytest.mark.asyncio
    async def test_send_message_fallback_for_zero_images(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True, "result": {"message_id": 301}}
        mock_client.post.return_value = mock_response

        notifier = TelegramNotifier(
            bot_token="123456:TEST_BOT_TOKEN_ABC_DEF",
            chat_id="-1001234567890",
            client=mock_client,
            rate_limit_delay=0.0,
        )

        listing_no_photos = Listing(
            source="rst",
            source_id="nophotos",
            url="https://rst.ua/nophotos",
            title="Audi A6 Text Only",
            images=[],
        )

        sent = await notifier.send_listing_alert(listing_no_photos)
        assert sent is True
        call_url = mock_client.post.call_args[0][0]
        call_json = mock_client.post.call_args[1]["json"]
        assert call_url.endswith("/sendMessage")
        assert "text" in call_json
        assert "Audi A6 Text Only" in call_json["text"]

    @pytest.mark.asyncio
    async def test_send_media_group_fallback_to_photo_then_text_on_400(self, sample_listing: Listing):
        """If Telegram rejects media group (e.g. photo URL un-fetchable), fallback to sendPhoto then sendMessage."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        # 1st call (sendMediaGroup) fails with 400 Bad Request
        resp_400 = MagicMock(spec=httpx.Response)
        resp_400.status_code = 400
        resp_400.json.return_value = {"ok": False, "description": "Bad Request: failed to get HTTP URL content"}

        # 2nd call (sendPhoto fallback) fails with 400
        # 3rd call (sendMessage fallback) succeeds with 200
        resp_200 = MagicMock(spec=httpx.Response)
        resp_200.status_code = 200
        resp_200.json.return_value = {"ok": True, "result": {"message_id": 401}}

        mock_client.post.side_effect = [resp_400, resp_400, resp_200]

        notifier = TelegramNotifier(
            bot_token="123456:TEST_BOT_TOKEN_ABC_DEF",
            chat_id="-1001234567890",
            client=mock_client,
            rate_limit_delay=0.0,
        )

        sent = await notifier.send_listing_alert(sample_listing)
        assert sent is True
        assert mock_client.post.call_count == 3
        last_call_url = mock_client.post.call_args_list[2][0][0]
        assert last_call_url.endswith("/sendMessage")


# ==============================================================================
# 3. Rate Limiter & FloodWait Backoff Tests
# ==============================================================================

class TestRateLimiterAndFloodWait:
    """Tests for 1.2s inter-message throttle and HTTP 429 exponential backoff."""

    @pytest.mark.asyncio
    async def test_inter_message_delay_throttle(self):
        notifier = TelegramNotifier(
            bot_token="123456:TEST_BOT_TOKEN_ABC_DEF",
            chat_id="-1001234567890",
            rate_limit_delay=1.2,
        )

        # Verify rate limit configuration
        assert notifier.rate_limit_delay >= 1.2

        # Test wait_rate_limit timing
        t0 = time.monotonic()
        await notifier.wait_rate_limit()
        t1 = time.monotonic()
        # First call has no previous send, should be virtually immediate
        assert (t1 - t0) < 0.2

    def test_floodwait_backoff_calculation(self):
        notifier = TelegramNotifier(bot_token="dummy", chat_id="123")
        calculated = notifier.calculate_floodwait_backoff(20, multiplier=1.5)
        assert calculated == 30.0

    def test_parse_retry_after_from_parameters_and_headers(self):
        notifier = TelegramNotifier(bot_token="dummy", chat_id="123")

        # Case 1: Telegram API JSON parameters dict
        data = {"parameters": {"retry_after": 35}}
        assert notifier.parse_retry_after(data=data) == 35

        # Case 2: HTTP Retry-After header
        resp = MagicMock(spec=httpx.Response)
        resp.headers = {"Retry-After": "45"}
        assert notifier.parse_retry_after(response=resp) == 45

        # Case 3: Fallback default
        assert notifier.parse_retry_after() == 10

    @pytest.mark.asyncio
    async def test_429_floodwait_retry_and_recovery(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        # 1st call returns 429 Too Many Requests
        resp_429 = MagicMock(spec=httpx.Response)
        resp_429.status_code = 429
        resp_429.json.return_value = {
            "ok": False,
            "error_code": 429,
            "parameters": {"retry_after": 1},
        }

        # 2nd call succeeds 200 OK
        resp_200 = MagicMock(spec=httpx.Response)
        resp_200.status_code = 200
        resp_200.json.return_value = {"ok": True, "result": {"message_id": 501}}

        mock_client.post.side_effect = [resp_429, resp_200]

        notifier = TelegramNotifier(
            bot_token="123456:TEST_BOT_TOKEN_ABC_DEF",
            chat_id="-1001234567890",
            client=mock_client,
            rate_limit_delay=0.0,
            max_retries=3,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            sent = await notifier.send_message("Testing 429")
            assert sent is True
            assert mock_client.post.call_count == 2
            # Sleep was invoked with the calculated backoff
            mock_sleep.assert_called()

    @pytest.mark.asyncio
    async def test_max_retries_exhaustion_returns_false(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        # All calls return 429
        resp_429 = MagicMock(spec=httpx.Response)
        resp_429.status_code = 429
        resp_429.json.return_value = {
            "ok": False,
            "error_code": 429,
            "parameters": {"retry_after": 1},
        }
        mock_client.post.return_value = resp_429

        notifier = TelegramNotifier(
            bot_token="123456:TEST_BOT_TOKEN_ABC_DEF",
            chat_id="-1001234567890",
            client=mock_client,
            rate_limit_delay=0.0,
            max_retries=3,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            sent = await notifier.send_message("Testing exhaustion")
            assert sent is False
            assert mock_client.post.call_count == 3
