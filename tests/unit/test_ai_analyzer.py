"""
Unit tests for Google Gemini 2.0 Flash AI Car Evaluation Service,
card markup builders, and Telegram bot callback query handlers.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.bot.handlers import BotHandler
from src.bot.keyboards import build_listing_card_markup
from src.database.models import Base, ListingModel, SourceModel
from src.models.listing import Listing
from src.notifier.telegram import TelegramNotifier
from src.services.ai_analyzer import (
    DEFAULT_MODEL_NAME,
    GEMINI_API_ENDPOINT,
    GeminiCarAnalyzer,
    build_gemini_car_prompt,
    sanitize_markdown_to_telegram_html,
)


@pytest.fixture
def sample_listing_obj() -> Listing:
    return Listing(
        id=42,
        source="auto_ria",
        source_id="123456",
        url="https://auto.ria.com/uk/auto_audi_a6_123456.html",
        title="Audi A6 1.8 T 2001",
        description="Машина в ідеалі, мотор шепче, кондиціонер треба заправити. Ходова зроблена.",
        brand="Audi",
        model="A6",
        generation="C5",
        year=2001,
        price=4500.0,
        currency="USD",
        price_usd=4500.0,
        mileage=265000,
        engine="1.8T",
        engine_code="1.8T",
        fuel_type="petrol",
        transmission="manual",
        drive_type="front",
        body_type="sedan",
        location="Київ",
        seller="Максим",
        images=["https://cdn.example.com/photo1.jpg"],
    )


class TestGeminiPromptAndSanitization:
    """Tests for prompt builder and Telegram HTML sanitization."""

    def test_build_gemini_car_prompt_contains_all_specs(self, sample_listing_obj: Listing):
        prompt = build_gemini_car_prompt(sample_listing_obj)
        assert "Audi A6 C5 (2001 р.в.)" in prompt
        assert "$4,500.0" in prompt
        assert "265,000 км" in prompt
        assert "1.8T" in prompt
        assert "petrol" in prompt
        assert "manual" in prompt
        assert "Київ" in prompt
        assert "Максим" in prompt
        assert "мотор шепче" in prompt
        assert "Оцінка ціни" in prompt
        assert "Технічні нюанси двигуна та КПП" in prompt
        assert "Аналіз опису продавця" in prompt
        assert "Підсумковий вердикт" in prompt
        assert "Чек-лист для огляду на СТО" in prompt

    def test_build_gemini_car_prompt_handles_dict_safely(self):
        minimal_dict = {"title": "Audi A6", "price": 3200, "currency": "USD"}
        prompt = build_gemini_car_prompt(minimal_dict)
        assert "Audi A6" in prompt
        assert "3,200.0 USD" in prompt
        assert "Опис відсутній" in prompt

    def test_sanitize_markdown_converts_headers_bold_italics(self):
        md = "### Оцінка авто\n**Жирний текст** та *курсив*.\n- Пункт 1\n* Пункт 2"
        res = sanitize_markdown_to_telegram_html(md)
        assert "<b>Оцінка авто</b>" in res
        assert "<b>Жирний текст</b>" in res
        assert "<i>курсив</i>" in res
        assert "• Пункт 1" in res
        assert "• Пункт 2" in res

    def test_sanitize_markdown_strips_codeblock_wrappers(self):
        wrapped = "```html\n<b>Вердикт:</b> 🟢 Варто дивитися\n```"
        res = sanitize_markdown_to_telegram_html(wrapped)
        assert "```" not in res
        assert "<b>Вердикт:</b> 🟢 Варто дивитися" in res

    def test_sanitize_markdown_truncates_long_output(self):
        huge_text = "А" * 5000
        res = sanitize_markdown_to_telegram_html(huge_text)
        assert len(res) <= 4000
        assert "[Скорочено через ліміт Telegram]" in res

    def test_build_gemini_car_prompt_robust_against_none_dict_and_strings(self):
        edge_dict = {
            "brand": None,
            "model": None,
            "generation": None,
            "price": "договірна",
            "price_usd": None,
            "mileage": "250 тис",
        }
        prompt = build_gemini_car_prompt(edge_dict)
        assert "Audi A6 C5" in prompt
        assert "договірна USD" in prompt
        assert "250 тис км" in prompt

    def test_sanitize_markdown_balances_tags_when_truncated(self):
        long_bold = "<b>" + ("Слово " * 700)
        res = sanitize_markdown_to_telegram_html(long_bold)
        assert len(res) <= 4000
        assert res.endswith("...\n<i>[Скорочено через ліміт Telegram]</i>")
        assert res.count("<b>") == res.count("</b>")

    def test_sanitize_markdown_escapes_forbidden_tags_and_preserves_allowed(self):
        text = "### <b>Заголовок</b>\n<p>Параграф</p>\n<script>alert(1)</script>\n`code`"
        res = sanitize_markdown_to_telegram_html(text)
        assert "<b>Заголовок</b>" in res
        assert "&lt;p&gt;" in res
        assert "&lt;script&gt;" in res
        assert "<code>code</code>" in res


class TestGeminiCarAnalyzerClient:
    """Tests for GeminiCarAnalyzer async client, error branches, and mock responses."""

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_guidance(self, sample_listing_obj: Listing):
        with patch.dict("os.environ", {}, clear=True):
            analyzer = GeminiCarAnalyzer(api_key="")
            result = await analyzer.analyze_listing(sample_listing_obj)
            assert "не налаштовано GEMINI_API_KEY" in result
            assert "Google AI Studio" in result
            assert "GEMINI_API_KEY=AIzaSy..." in result

    @pytest.mark.asyncio
    async def test_successful_analysis(self, sample_listing_obj: Listing):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    "💰 <b>Оцінка ціни:</b>\nРинкова.\n\n"
                                    "⚙️ <b>Технічні нюанси:</b>\n1.8T надійний двигун.\n\n"
                                    "🚦 <b>Підсумковий вердикт:</b>\n🟢 <b>Варто дивитися</b>"
                                )
                            }
                        ]
                    }
                }
            ]
        }
        mock_client.post.return_value = mock_resp

        analyzer = GeminiCarAnalyzer(api_key="test_api_key", client=mock_client)
        result = await analyzer.analyze_listing(sample_listing_obj)

        assert "Експертний ШІ-аналіз (Gemini 2.0 Flash)" in result
        assert "Оцінка ціни" in result
        assert "Варто дивитися" in result

        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        assert "gemini-2.0-flash:generateContent" in call_url
        assert "key=test_api_key" in call_url

    @pytest.mark.asyncio
    async def test_rate_limit_429_handled_gracefully(self, sample_listing_obj: Listing):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 429
        mock_client.post.return_value = mock_resp

        analyzer = GeminiCarAnalyzer(api_key="test_api_key", client=mock_client)
        result = await analyzer.analyze_listing(sample_listing_obj)
        assert "429 Rate Limit" in result
        assert "30–60 секунд" in result

    @pytest.mark.asyncio
    async def test_auth_error_403_handled_gracefully(self, sample_listing_obj: Listing):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 403
        mock_resp.text = '{"error": {"message": "API key not valid"}}'
        mock_client.post.return_value = mock_resp

        analyzer = GeminiCarAnalyzer(api_key="invalid_key", client=mock_client)
        result = await analyzer.analyze_listing(sample_listing_obj)
        assert "Помилка доступу до Google Gemini API" in result
        assert "GEMINI_API_KEY" in result

    @pytest.mark.asyncio
    async def test_timeout_and_network_exceptions(self, sample_listing_obj: Listing):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = httpx.TimeoutException("Timeout")

        analyzer = GeminiCarAnalyzer(api_key="test_key", client=mock_client)
        res_timeout = await analyzer.analyze_listing(sample_listing_obj)
        assert "Таймаут запиту" in res_timeout

        mock_client.post.side_effect = httpx.ConnectError("Network down")
        res_net = await analyzer.analyze_listing(sample_listing_obj)
        assert "Мережева помилка" in res_net


class TestListingCardMarkupBuilder:
    """Tests for inline button keyboard generation."""

    def test_markup_with_id_and_url(self):
        kb = build_listing_card_markup(listing_id=101, url="https://auto.ria.com/car")
        assert kb is not None
        rows = kb.get("inline_keyboard", [])
        assert len(rows) == 2
        assert rows[0][0]["text"] == "🚗 Відкрити оголошення"
        assert rows[0][0]["url"] == "https://auto.ria.com/car"
        assert rows[1][0]["text"] == "🧠 Аналіз ШІ"
        assert rows[1][0]["callback_data"] == "ai_analyze:101"

    def test_markup_with_id_only(self):
        kb = build_listing_card_markup(listing_id=202, url=None)
        assert kb is not None
        rows = kb.get("inline_keyboard", [])
        assert len(rows) == 1
        assert rows[0][0]["callback_data"] == "ai_analyze:202"

    def test_markup_with_url_only(self):
        kb = build_listing_card_markup(listing_id=None, url="https://auto.ria.com/car")
        assert kb is not None
        rows = kb.get("inline_keyboard", [])
        assert len(rows) == 1
        assert rows[0][0]["url"] == "https://auto.ria.com/car"

    def test_markup_with_empty_args_returns_none(self):
        assert build_listing_card_markup(None, None) is None
        assert build_listing_card_markup(None, "") is None


class TestBotHandlerAIAnalyzeCallback:
    """Tests for BotHandler callback query handling for ai_analyze:<id>."""

    @pytest.mark.asyncio
    async def test_handle_callback_query_ai_analyze_success(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_maker = async_sessionmaker(engine, expire_on_commit=False)
        async with session_maker() as session:
            source = SourceModel(
                id="auto_ria",
                name="AUTO.RIA",
                base_url="https://auto.ria.com",
                source_type="auto_ria",
            )
            session.add(source)
            await session.flush()

            listing = ListingModel(
                id=77,
                source_id="auto_ria",
                source_listing_id="aria_77",
                url="https://auto.ria.com/77",
                canonical_url="https://auto.ria.com/77",
                title="Audi A6 2.4 V6 2002",
                description="Оригінальний пробіг, стан чудовий.",
                brand="Audi",
                model="A6",
                generation="C5",
                year=2002,
                price=4800,
                price_usd=4800,
                mileage=240000,
                engine="2.4",
                engine_code="2.4",
                transmission="manual",
                drive_type="front",
                body_type="sedan",
                content_fingerprint="fp77",
                fuzzy_fingerprint="fuzz77",
            )
            session.add(listing)
            await session.commit()

        client = AsyncMock(spec=httpx.AsyncClient)
        # Mock answers for answerCallbackQuery and sendMessage
        resp_ack = MagicMock(spec=httpx.Response)
        resp_ack.status_code = 200
        resp_ack.json.return_value = {"ok": True}

        resp_msg = MagicMock(spec=httpx.Response)
        resp_msg.status_code = 200
        resp_msg.json.return_value = {"ok": True, "result": {"message_id": 111}}

        client.post.side_effect = [resp_ack, resp_msg]

        handler = BotHandler("fake_token", client)

        with patch("src.bot.handlers.get_session_factory", return_value=session_maker):
            with patch.object(
                GeminiCarAnalyzer,
                "analyze_listing",
                return_value="🧠 <b>ШІ-Експертиза:</b> 🟢 Варто дивитися!",
            ) as mock_analyze:
                await handler.handle_callback_query(
                    callback_id="cb_99",
                    chat_id="12345",
                    message_id=50,
                    data="ai_analyze:77",
                )

                mock_analyze.assert_called_once()
                analyzed_arg = mock_analyze.call_args[0][0]
                assert analyzed_arg.id == 77
                assert analyzed_arg.title == "Audi A6 2.4 V6 2002"

                # Verify answerCallbackQuery was sent
                first_call = client.post.call_args_list[0]
                assert first_call[0][0].endswith("/answerCallbackQuery")
                assert first_call[1]["json"]["callback_query_id"] == "cb_99"
                assert "Запускаю аналіз ШІ" in first_call[1]["json"]["text"]

                # Verify sendMessage was sent with AI report
                second_call = client.post.call_args_list[1]
                assert second_call[0][0].endswith("/sendMessage")
                assert second_call[1]["json"]["chat_id"] == "12345"
                assert "Варто дивитися" in second_call[1]["json"]["text"]
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_handle_callback_query_ai_analyze_non_existent_listing(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, expire_on_commit=False)

        client = AsyncMock(spec=httpx.AsyncClient)
        resp_ack = MagicMock(spec=httpx.Response)
        resp_ack.status_code = 200
        resp_ack.json.return_value = {"ok": True}

        resp_msg = MagicMock(spec=httpx.Response)
        resp_msg.status_code = 200
        resp_msg.json.return_value = {"ok": True, "result": {"message_id": 112}}

        client.post.side_effect = [resp_ack, resp_msg]
        handler = BotHandler("fake_token", client)

        with patch("src.bot.handlers.get_session_factory", return_value=session_maker):
            await handler.handle_callback_query(
                callback_id="cb_404",
                chat_id="12345",
                message_id=51,
                data="ai_analyze:99999",
            )

            msg_call = client.post.call_args_list[1]
            assert "не знайдено в базі даних" in msg_call[1]["json"]["text"]
        await engine.dispose()


class TestTelegramNotifierAIButtonIntegration:
    """Tests that TelegramNotifier attaches [ 🧠 Аналіз ШІ ] button when listing has ID."""

    @pytest.mark.asyncio
    async def test_notifier_single_photo_attaches_ai_button(self, sample_listing_obj: Listing):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "result": {"message_id": 555}}
        mock_client.post.return_value = mock_resp

        notifier = TelegramNotifier(
            bot_token="test_token",
            chat_id="123456",
            client=mock_client,
            rate_limit_delay=0.0,
        )

        sent = await notifier.send_listing_alert(sample_listing_obj)
        assert sent is True

        mock_client.post.assert_called_once()
        call_json = mock_client.post.call_args[1]["json"]
        assert "reply_markup" in call_json
        kb_rows = call_json["reply_markup"]["inline_keyboard"]
        assert any(
            btn.get("text") == "🧠 Аналіз ШІ" and btn.get("callback_data") == "ai_analyze:42"
            for row in kb_rows
            for btn in row
        )

    @pytest.mark.asyncio
    async def test_notifier_zero_photos_attaches_ai_button(self, sample_listing_obj: Listing):
        sample_listing_obj.images = []
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "result": {"message_id": 556}}
        mock_client.post.return_value = mock_resp

        notifier = TelegramNotifier(
            bot_token="test_token",
            chat_id="123456",
            client=mock_client,
            rate_limit_delay=0.0,
        )

        sent = await notifier.send_listing_alert(sample_listing_obj)
        assert sent is True

        mock_client.post.assert_called_once()
        call_json = mock_client.post.call_args[1]["json"]
        assert call_json.get("reply_markup") is not None
        kb_rows = call_json["reply_markup"]["inline_keyboard"]
        assert any(btn.get("callback_data") == "ai_analyze:42" for row in kb_rows for btn in row)

    @pytest.mark.asyncio
    async def test_gemini_safety_block_handled(self, sample_listing_obj: Listing):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "finishReason": "SAFETY",
                    "content": {"parts": []},
                }
            ]
        }
        mock_client.post.return_value = mock_resp

        analyzer = GeminiCarAnalyzer(api_key="key", client=mock_client)
        res = await analyzer.analyze_listing(sample_listing_obj)
        assert "фільтром безпеки" in res

    @pytest.mark.asyncio
    async def test_gemini_custom_model_passed_in_url(self, sample_listing_obj: Listing):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "OK"}]}}]
        }
        mock_client.post.return_value = mock_resp

        analyzer = GeminiCarAnalyzer(api_key="key", client=mock_client, model="gemini-2.0-flash-lite")
        await analyzer.analyze_listing(sample_listing_obj)
        call_url = mock_client.post.call_args[0][0]
        assert "gemini-2.0-flash-lite:generateContent" in call_url

    @pytest.mark.asyncio
    async def test_handle_text_boost_with_id_executes_analysis(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, expire_on_commit=False)

        async with session_maker() as session:
            source = SourceModel(
                id="auto_ria",
                name="AUTO.RIA",
                base_url="https://auto.ria.com",
                source_type="auto_ria",
            )
            session.add(source)
            await session.flush()

            listing = ListingModel(
                id=88,
                source_id="auto_ria",
                source_listing_id="aria_88",
                url="https://auto.ria.com/88",
                canonical_url="https://auto.ria.com/88",
                title="Audi A6 1.8T 2003",
                brand="Audi",
                model="A6",
                generation="C5",
                year=2003,
                price=5200,
                content_fingerprint="fp88",
                fuzzy_fingerprint="fuzz88",
            )
            session.add(listing)
            await session.commit()

        client = AsyncMock(spec=httpx.AsyncClient)
        resp_msg = MagicMock(spec=httpx.Response)
        resp_msg.status_code = 200
        resp_msg.json.return_value = {"ok": True, "result": {"message_id": 999}}
        client.post.return_value = resp_msg

        handler = BotHandler("fake_token", client)

        with patch("src.bot.handlers.get_session_factory", return_value=session_maker):
            with patch.object(
                GeminiCarAnalyzer,
                "analyze_listing",
                return_value="🧠 <b>ШІ-Експертиза:</b> Відмінний вибір!",
            ) as mock_analyze:
                await handler.handle_text_message("12345", "/boost 88")

                mock_analyze.assert_called_once()
                arg = mock_analyze.call_args[0][0]
                assert arg.id == 88
                assert client.post.call_count == 2
                call2 = client.post.call_args_list[1]
                assert "Відмінний вибір" in call2[1]["json"]["text"]
        await engine.dispose()

