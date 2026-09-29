"""
Unit tests for Milestone M4: Master Pipeline Runner CLI and Orchestration.
100% mocked end-to-end unit tests of the runner pipeline, CLI flags,
error isolation, dry-run mode, deduplication interaction, and telemetry persistence.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.config import Settings
from src.database.models import Base, ListingModel, ParserRunModel
from src.filtering.engine import FilterEngine
from src.models.listing import RawListingPayload
from src.notifier.telegram import TelegramNotifier
from src.parsers.base import BaseParser
from src.runner import PipelineRunner, async_main, parse_cli_args
from src.services.deduplicator import Deduplicator


class MockScraper(BaseParser):
    """Configurable mock scraper for deterministic pipeline testing."""

    def __init__(self, name: str, items: Optional[List[RawListingPayload]] = None, fail_with: Optional[Exception] = None, **kwargs):
        super().__init__(name=name, base_url=f"https://{name}.example.com", **kwargs)
        self._items = items or []
        self._fail_with = fail_with

    async def fetch_new_listings(self) -> AsyncGenerator[RawListingPayload, None]:
        if self._fail_with:
            self.stats.start()
            self.stats.record_error()
            self.stats.finish(status="FAILED")
            raise self._fail_with

        self.stats.start()
        for item in self._items:
            self.stats.record_fetched()
            self.stats.record_valid()
            yield item
        self.stats.finish(status="SUCCESS")


@pytest.fixture
async def runner_test_engine():
    """In-memory SQLite engine for isolated pipeline runner testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def runner_session_factory(runner_test_engine):
    return async_sessionmaker(bind=runner_test_engine, class_=AsyncSession, expire_on_commit=False)


# ==============================================================================
# 1. CLI Arguments & Configuration Parsing Tests
# ==============================================================================

class TestRunnerCliAndConfig:
    """Tests CLI flag parsing and configuration management."""

    def test_parse_cli_args_defaults(self):
        args = parse_cli_args([])
        assert args.once is True
        assert args.dry_run is False
        assert args.source is None
        assert args.config == "config/config.yaml"

    def test_parse_cli_args_custom(self):
        args = parse_cli_args(["--dry-run", "--source", "auto_ria", "--config", "custom.yaml", "--concurrency", "5"])
        assert args.dry_run is True
        assert args.source == "auto_ria"
        assert args.config == "custom.yaml"
        assert args.concurrency == 5

    def test_pipeline_runner_initialization_defaults(self):
        settings = Settings()
        runner = PipelineRunner(config=settings, dry_run=True, concurrency_limit=2)
        assert runner.dry_run is True
        assert runner.concurrency_limit == 2

    def test_initialize_parsers_respects_source_override(self):
        settings = Settings()
        runner = PipelineRunner(config=settings, source_override="auto_ria")
        parsers = runner.initialize_parsers()
        assert len(parsers) == 1
        assert parsers[0].name == "auto_ria"

    def test_initialize_parsers_initializes_all_enabled(self):
        settings = Settings()
        # Enable all
        settings.parsers.auto_ria.enabled = True
        settings.parsers.olx.enabled = True
        settings.parsers.rst.enabled = True
        settings.parsers.telegram.enabled = True
        settings.parsers.instagram.enabled = True

        runner = PipelineRunner(config=settings)
        parsers = runner.initialize_parsers()
        source_names = [p.name for p in parsers]
        assert "auto_ria" in source_names
        assert "olx" in source_names
        assert "rst" in source_names
        assert "telegram" in source_names
        assert "instagram" in source_names


# ==============================================================================
# 2. End-to-End Pipeline Execution Tests
# ==============================================================================

class TestPipelineRunnerExecution:
    """End-to-end execution of PipelineRunner with mock scrapers and in-memory DB."""

    @pytest.mark.asyncio
    async def test_pipeline_run_ingest_filter_dedup_and_notify(
        self, runner_test_engine, runner_session_factory
    ):
        settings = Settings()
        settings.telegram_bot.bot_token = "123456:TEST_TOKEN"
        settings.telegram_bot.chat_id = "-100123456"

        mock_notifier = MagicMock(spec=TelegramNotifier)
        mock_notifier.send_listing_alert = AsyncMock(return_value=True)
        mock_notifier.close = AsyncMock()

        valid_raw = RawListingPayload(
            source="auto_ria",
            source_id="11111",
            url="https://auto.ria.com/11111.html",
            title="Audi A6 C5 1.8T 2001",
            price=4500.0,
            year=2001,
            engine="1.8T",
            images=["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"],
        )

        rejected_raw = RawListingPayload(
            source="auto_ria",
            source_id="22222",
            url="https://auto.ria.com/22222.html",
            title="BMW 525i 2001 на ходу",
            price=3000.0,
            year=2001,
            engine="2.5",
        )

        mock_parser = MockScraper("auto_ria", items=[valid_raw, rejected_raw])

        runner = PipelineRunner(
            config=settings,
            dry_run=False,
            engine=runner_test_engine,
            session_factory=runner_session_factory,
            notifier=mock_notifier,
        )

        # Patch initialize_parsers to return mock parser
        with patch.object(runner, "initialize_parsers", return_value=[mock_parser]):
            summary = await runner.run()

        # Telemetry assertions
        assert summary["total_scanned"] == 2
        assert summary["matched_filter"] == 1
        assert summary["new"] == 1
        assert summary["notified"] == 1
        assert summary["errors"] == 0

        # Verify TelegramNotifier alert was dispatched for valid listing
        mock_notifier.send_listing_alert.assert_called_once()
        sent_listing = mock_notifier.send_listing_alert.call_args[0][0]
        assert sent_listing.source_id == "11111"

        # Verify database record
        async with runner_session_factory() as session:
            stmt = select(ListingModel).where(ListingModel.source_listing_id == "11111")
            saved = (await session.execute(stmt)).scalar_one_or_none()
            assert saved is not None
            assert saved.title == "Audi A6 C5 1.8T 2001"
            assert saved.is_sent_to_telegram is True
            assert saved.status == "SENT"

            # Verify parser_runs telemetry table was populated
            stmt_run = select(ParserRunModel).where(ParserRunModel.source_id == "auto_ria")
            run_rec = (await session.execute(stmt_run)).scalar_one_or_none()
            assert run_rec is not None
            assert run_rec.status == "SUCCESS"
            assert run_rec.items_scanned == 2
            assert run_rec.items_matched_filter == 1
            assert run_rec.items_new == 1

    @pytest.mark.asyncio
    async def test_pipeline_dry_run_does_not_send_alerts(
        self, runner_test_engine, runner_session_factory
    ):
        settings = Settings()
        mock_notifier = MagicMock(spec=TelegramNotifier)
        mock_notifier.send_listing_alert = AsyncMock(return_value=True)
        mock_notifier.close = AsyncMock()

        valid_raw = RawListingPayload(
            source="olx",
            source_id="dryrun_ad",
            url="https://olx.ua/dryrun",
            title="Audi A6 C5 2.4 газ/бензин 2002",
            price=4200.0,
            year=2002,
            engine="2.4",
        )

        mock_parser = MockScraper("olx", items=[valid_raw])

        runner = PipelineRunner(
            config=settings,
            dry_run=True,  # DRY RUN ACTIVE
            engine=runner_test_engine,
            session_factory=runner_session_factory,
            notifier=mock_notifier,
        )

        with patch.object(runner, "initialize_parsers", return_value=[mock_parser]):
            summary = await runner.run()

        assert summary["total_scanned"] == 1
        assert summary["new"] == 1
        assert summary["notified"] == 0  # No alerts in dry-run mode!
        mock_notifier.send_listing_alert.assert_not_called()

        # Record is saved in DB, but with is_sent_to_telegram = False
        async with runner_session_factory() as session:
            stmt = select(ListingModel).where(ListingModel.source_listing_id == "dryrun_ad")
            saved = (await session.execute(stmt)).scalar_one_or_none()
            assert saved is not None
            assert saved.is_sent_to_telegram is False

    @pytest.mark.asyncio
    async def test_error_isolation_single_source_failure_does_not_halt_pipeline(
        self, runner_test_engine, runner_session_factory
    ):
        settings = Settings()
        mock_notifier = MagicMock(spec=TelegramNotifier)
        mock_notifier.send_listing_alert = AsyncMock(return_value=True)
        mock_notifier.close = AsyncMock()

        # Scraper 1 succeeds
        valid_raw = RawListingPayload(
            source="auto_ria",
            source_id="good_ad",
            url="https://auto.ria.com/good",
            title="Audi A6 C5 1.9 TDI 2003",
            price=5000.0,
            year=2003,
            engine="1.9 TDI",
        )
        good_parser = MockScraper("auto_ria", items=[valid_raw])

        # Scraper 2 crashes with network exception
        bad_parser = MockScraper("olx", fail_with=ConnectionResetError("Remote server closed connection"))

        runner = PipelineRunner(
            config=settings,
            dry_run=False,
            engine=runner_test_engine,
            session_factory=runner_session_factory,
            notifier=mock_notifier,
        )

        with patch.object(runner, "initialize_parsers", return_value=[good_parser, bad_parser]):
            summary = await runner.run()

        # Error on OLX was isolated!
        assert summary["sources"]["auto_ria"]["status"] == "SUCCESS"
        assert summary["sources"]["auto_ria"]["new"] == 1
        assert summary["sources"]["olx"]["status"] == "FAILED"
        assert summary["sources"]["olx"]["errors"] >= 1
        assert "Remote server closed connection" in (summary["sources"]["olx"]["error_message"] or "")

        # Verify parser_runs telemetry records
        async with runner_session_factory() as session:
            stmt_bad = select(ParserRunModel).where(ParserRunModel.source_id == "olx")
            bad_run = (await session.execute(stmt_bad)).scalar_one_or_none()
            assert bad_run is not None
            assert bad_run.status == "FAILED"
            assert "Remote server closed connection" in bad_run.error_message

    @pytest.mark.asyncio
    async def test_price_drop_triggers_price_drop_alert(
        self, runner_test_engine, runner_session_factory
    ):
        settings = Settings()
        mock_notifier = MagicMock(spec=TelegramNotifier)
        mock_notifier.send_listing_alert = AsyncMock(return_value=True)
        mock_notifier.close = AsyncMock()

        # Run 1: First appearance of car at $4,800
        raw_v1 = RawListingPayload(
            source="rst",
            source_id="price_drop_car",
            url="https://rst.ua/car1",
            title="Audi A6 C5 1.8T 2000",
            price=4800.0,
            year=2000,
            engine="1.8T",
        )
        parser_run1 = MockScraper("rst", items=[raw_v1])

        runner = PipelineRunner(
            config=settings,
            dry_run=False,
            engine=runner_test_engine,
            session_factory=runner_session_factory,
            notifier=mock_notifier,
        )

        with patch.object(runner, "initialize_parsers", return_value=[parser_run1]):
            await runner.run()

        assert mock_notifier.send_listing_alert.call_count == 1

        # Run 2: Price drop to $4,500
        raw_v2 = RawListingPayload(
            source="rst",
            source_id="price_drop_car",
            url="https://rst.ua/car1",
            title="Audi A6 C5 1.8T 2000",
            price=4500.0,
            year=2000,
            engine="1.8T",
        )
        parser_run2 = MockScraper("rst", items=[raw_v2])

        with patch.object(runner, "initialize_parsers", return_value=[parser_run2]):
            summary2 = await runner.run()

        # 2nd run detected price drop and alerted!
        assert summary2["updated"] == 1
        assert summary2["notified"] == 1
        assert mock_notifier.send_listing_alert.call_count == 2

        # Check call arguments for price drop info
        last_call_kwargs = mock_notifier.send_listing_alert.call_args[1]
        assert "price_drop_info" in last_call_kwargs
        assert last_call_kwargs["price_drop_info"]["is_price_drop"] is True
        assert last_call_kwargs["price_drop_info"]["diff_usd"] == -300.0

    @pytest.mark.asyncio
    async def test_pipeline_skips_notification_when_send_needs_review_false(
        self, runner_test_engine, runner_session_factory
    ):
        settings = Settings()
        settings.telegram_bot.send_needs_review = False

        mock_notifier = MagicMock(spec=TelegramNotifier)
        mock_notifier.send_listing_alert = AsyncMock(return_value=True)
        mock_notifier.close = AsyncMock()

        # Listing that will trigger NEEDS_REVIEW (1997 transition with 1.9 TDI)
        review_raw = RawListingPayload(
            source="auto_ria",
            source_id="review_car_97",
            url="https://auto.ria.com/review_car",
            title="Audi A6 1.9 TDI 1997",
            price=4000.0,
            year=1997,
            engine="1.9 TDI",
        )
        mock_parser = MockScraper("auto_ria", items=[review_raw])

        runner = PipelineRunner(
            config=settings,
            dry_run=False,
            engine=runner_test_engine,
            session_factory=runner_session_factory,
            notifier=mock_notifier,
        )

        with patch.object(runner, "initialize_parsers", return_value=[mock_parser]):
            summary = await runner.run()

        assert summary["total_scanned"] == 1
        assert summary["matched_filter"] == 1
        assert summary["new"] == 1
        assert summary["notified"] == 0  # Alert skipped due to send_needs_review=False
        mock_notifier.send_listing_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_pipeline_no_parsers_returns_zero_summary(
        self, runner_test_engine, runner_session_factory
    ):
        settings = Settings()
        runner = PipelineRunner(
            config=settings,
            engine=runner_test_engine,
            session_factory=runner_session_factory,
        )

        with patch.object(runner, "initialize_parsers", return_value=[]):
            summary = await runner.run()

        assert summary["total_scanned"] == 0
        assert summary["new"] == 0

    @pytest.mark.asyncio
    async def test_async_main_cli_entry_point(self, runner_test_engine):
        mock_run = AsyncMock(return_value={"total_scanned": 10, "new": 1, "errors": 0})
        with patch("src.runner.PipelineRunner.run", mock_run), \
             patch("src.runner.close_db_engine", AsyncMock()):
            code = await async_main(["--dry-run", "--source", "auto_ria"])
            assert code == 0
            mock_run.assert_called_once()

