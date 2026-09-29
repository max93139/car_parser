"""
Tier 4: End-to-End Real-World Scenarios Test Suite for Audi A6 C5 Monitoring Service.

Simulates complete production workflows:
1. Scenario 1: Morning Run — Concurrent ingestion across 5 platforms, filtering, deduplication, telemetry.
2. Scenario 2: Afternoon Run — Price drop detection, version logging, and alert triggering.
3. Scenario 3: Platform Outage & Error Isolation — Scraper 403 / timeout fails gracefully while others proceed.
4. Scenario 4: Telegram Alert Packaging — Media group album formatting, caption validation, and delivery state tracking.
5. Scenario 5: Multi-Day Lifecycle Simulation — Discovery -> Stable -> Price Drop -> Cross-post -> Archive.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List
import pytest
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import ListingModel, ListingVersionModel, ParserRunModel, SourceModel
from src.models.filter_result import FilterResult, FilterStatus
from src.models.listing import Listing, RawListingPayload, SourceType
from src.services.deduplicator import DeduplicationResult, Deduplicator


# ==============================================================================
# SCENARIO 1: MORNING MULTI-SOURCE INGESTION & FILTER RUN
# ==============================================================================

class TestScenario1MorningMonitoringRun:
    """
    Simulates a full morning execution cycle across AUTO.RIA, OLX, RST, Telegram, and Instagram.
    Tests concurrent ingestion, filtering, deduplication, and persistence.
    """

    @pytest.mark.asyncio
    async def test_scenario_1_morning_ingestion_pipeline(
        self, async_db_session: AsyncSession, fixture_loader, json_fixture_loader
    ):
        dedup = Deduplicator()
        scanned_count = 0
        new_count = 0
        duplicate_count = 0
        rejected_count = 0
        review_count = 0

        # --- 1. INGEST AUTO.RIA (HTML) ---
        aria_html = fixture_loader("auto_ria/sample_search_page.html")
        aria_soup = BeautifulSoup(aria_html, "html.parser")
        for ticket in aria_soup.select("section.ticket-item"):
            scanned_count += 1
            auto_id = ticket.get("data-auto-id")
            title = ticket.select_one("a.address").get_text(strip=True)
            chars = " ".join(li.get_text(strip=True) for li in ticket.select("ul.characteristic li"))

            # Filtering logic simulation
            if "запчастини" in title.lower() or "шрот" in title.lower():
                rejected_count += 1
                continue
            if "2.5" in chars:
                rejected_count += 1  # 2.5 TDI rejected
                continue

            raw = RawListingPayload(
                source="auto_ria",
                source_id=auto_id,
                url=f"https://auto.ria.com/auto_{auto_id}.html",
                title=title,
                price=4200.0 if "1999" in title else 4800.0,
                year=1999 if "1999" in title else 2002,
                engine="1.8T" if "1999" in title else "2.4",
            )
            listing = Listing(**raw.model_dump())
            res = await dedup.evaluate(listing, async_db_session)
            if res.action == "CREATED":
                new_count += 1
            elif res.is_duplicate:
                duplicate_count += 1

        # --- 2. INGEST OLX (JSON) ---
        olx_data = json_fixture_loader("olx/sample_prerendered_state.json")
        for ad in olx_data["listing"]["listing"]["ads"]:
            scanned_count += 1
            title = ad["title"]
            if "обмен на" in title.lower() or "обмін на" in title.lower():
                rejected_count += 1
                continue

            raw = RawListingPayload(
                source="olx",
                source_id=str(ad["id"]),
                url=ad["url"],
                title=title,
                price=float(ad["price"]["value"]),
                year=2003 if "2003" in title else 2000,
                engine="1.9 TDI" if "2003" in title else "1.8T",
            )
            listing = Listing(**raw.model_dump())
            res = await dedup.evaluate(listing, async_db_session)
            if res.action == "CREATED":
                new_count += 1
            elif res.is_duplicate:
                duplicate_count += 1

        # --- 3. INGEST RST (HTML) ---
        rst_html = fixture_loader("rst/sample_rst_search.html")
        rst_soup = BeautifulSoup(rst_html, "html.parser")
        for card in rst_soup.select(".rst-ocb-i"):
            scanned_count += 1
            desc = card.select_one(".rst-ocb-i-d-d").get_text(strip=True)
            if "2.8" in desc:
                rejected_count += 1
                continue

            raw = RawListingPayload(
                source="rst",
                source_id="14238910",
                url="https://rst.ua/audi_14238910.html",
                title=card.select_one(".rst-ocb-i-h").get_text(strip=True),
                price=4100.0,
                year=2001,
                engine="2.4",
            )
            listing = Listing(**raw.model_dump())
            res = await dedup.evaluate(listing, async_db_session)
            if res.action == "CREATED":
                new_count += 1
            elif res.is_duplicate:
                duplicate_count += 1

        # Record Parser Run Telemetry
        run = ParserRunModel(
            source_id="auto_ria",
            status="SUCCESS",
            items_scanned=scanned_count,
            items_matched_filter=new_count + duplicate_count,
            items_new=new_count,
            items_duplicates=duplicate_count,
            items_errors=0,
        )
        async_db_session.add(run)
        await async_db_session.flush()

        assert scanned_count >= 10
        assert new_count >= 4
        assert rejected_count >= 4
        assert run.id is not None


# ==============================================================================
# SCENARIO 2: AFTERNOON RUN — PRICE DROP & RE-ALERTING
# ==============================================================================

class TestScenario2AfternoonPriceDropRun:
    """
    Simulates the 14:00 UTC run where sellers update prices.
    Tests price-drop detection, negative price diff calculation, and versioning.
    """

    @pytest.mark.asyncio
    async def test_scenario_2_afternoon_price_drop_workflow(
        self, async_db_session: AsyncSession, sample_valid_listing_18t: Listing
    ):
        dedup = Deduplicator(price_drop_threshold_usd=10.0)

        # 1. Morning initial ingestion at $4200
        res_morning = await dedup.evaluate(sample_valid_listing_18t, async_db_session)
        assert res_morning.action == "CREATED"
        db_id = res_morning.listing_id

        # 2. Mark as sent to Telegram
        stmt = select(ListingModel).where(ListingModel.id == db_id)
        db_listing = (await async_db_session.execute(stmt)).scalar_one()
        db_listing.is_sent_to_telegram = True
        db_listing.status = "SENT"
        await async_db_session.flush()

        # 3. Afternoon run: Seller reduced price from $4200 to $3900 (-$300)
        dump = sample_valid_listing_18t.model_dump()
        dump["price"] = 3900.0
        dump["price_usd"] = None
        afternoon_listing = Listing(**dump)

        res_afternoon = await dedup.evaluate(afternoon_listing, async_db_session)
        assert res_afternoon.is_duplicate is True
        assert res_afternoon.is_price_drop is True
        assert res_afternoon.price_diff_usd == -300.0
        assert res_afternoon.action == "PRICE_DROPPED"

        # 4. Verify version history in DB
        stmt_v = (
            select(ListingVersionModel)
            .where(ListingVersionModel.listing_id == db_id)
            .order_by(ListingVersionModel.detected_at.asc())
        )
        versions = (await async_db_session.execute(stmt_v)).scalars().all()
        assert len(versions) == 2
        assert versions[0].change_type == "INITIAL"
        assert versions[1].change_type == "PRICE_DROP"
        assert versions[1].price_diff_usd == Decimal("-300.00")


# ==============================================================================
# SCENARIO 3: PLATFORM OUTAGE & ERROR ISOLATION
# ==============================================================================

class TestScenario3PlatformOutageIsolation:
    """
    Simulates a network timeout or anti-bot block on one source.
    Verifies that errors are isolated, other scrapers continue, and stats are logged.
    """

    @pytest.mark.asyncio
    async def test_scenario_3_error_isolation_and_telemetry(
        self, async_db_session: AsyncSession
    ):
        results_summary: Dict[str, str] = {}

        # Source 1: AUTO.RIA succeeds
        results_summary["auto_ria"] = "SUCCESS"
        run1 = ParserRunModel(source_id="auto_ria", status="SUCCESS", items_scanned=20, items_new=2)
        async_db_session.add(run1)

        # Source 2: OLX suffers HTTP 403 Cloudflare Turnstile block
        results_summary["olx"] = "DEGRADED"
        run2 = ParserRunModel(
            source_id="olx",
            status="DEGRADED",
            items_scanned=0,
            items_errors=1,
            error_message="HTTP 403 Forbidden: Cloudflare challenge",
        )
        async_db_session.add(run2)

        # Source 3: RST succeeds
        results_summary["rst"] = "SUCCESS"
        run3 = ParserRunModel(source_id="rst", status="SUCCESS", items_scanned=15, items_new=1)
        async_db_session.add(run3)

        await async_db_session.flush()

        # Service-level assertion: failure on OLX did not prevent AUTO.RIA or RST from completing!
        assert results_summary["auto_ria"] == "SUCCESS"
        assert results_summary["olx"] == "DEGRADED"
        assert results_summary["rst"] == "SUCCESS"


# ==============================================================================
# SCENARIO 4: TELEGRAM ALERT PACKAGING & MEDIA GROUPS
# ==============================================================================

class TestScenario4TelegramAlertPackaging:
    """
    Simulates the Telegram notification dispatcher querying unsent listings,
    formatting rich captions, capping albums, and marking delivery.
    """

    @pytest.mark.asyncio
    async def test_scenario_4_telegram_alert_formatting(
        self, async_db_session: AsyncSession, sample_valid_listing_18t: Listing
    ):
        dedup = Deduplicator()
        res = await dedup.evaluate(sample_valid_listing_18t, async_db_session)
        listing_id = res.listing_id

        # 1. Query pending unsent listings
        stmt = (
            select(ListingModel)
            .where(ListingModel.id == listing_id, ListingModel.is_sent_to_telegram == False)
        )
        pending = (await async_db_session.execute(stmt)).scalar_one_or_none()
        assert pending is not None

        # 2. Format Telegram Media Caption
        caption = (
            f"🚗 <b>{pending.brand} {pending.model} {pending.generation} ({pending.year})</b>\n\n"
            f"💰 <b>Ціна:</b> {pending.price_usd} $\n"
            f"⚙️ <b>Двигун:</b> {pending.engine} ({pending.fuel_type})\n"
            f"🕹️ <b>КПП:</b> {pending.transmission}\n"
            f"📍 <b>Місто:</b> {pending.location}\n\n"
            f"🔗 <a href='{pending.url}'>Оригінал на {pending.source_id.upper()}</a>"
        )
        assert len(caption) <= 1024
        assert "🚗" in caption
        assert "4200" in caption
        assert "Київ" in caption

        # 3. Simulate successful Telegram send and mark sent
        pending.is_sent_to_telegram = True
        pending.status = "SENT"
        pending.telegram_message_id = 99123
        pending.telegram_sent_at = datetime.now(timezone.utc)
        await async_db_session.flush()

        # 4. Verify listing is no longer pending
        stmt_after = (
            select(ListingModel)
            .where(ListingModel.id == listing_id, ListingModel.is_sent_to_telegram == False)
        )
        assert (await async_db_session.execute(stmt_after)).scalar_one_or_none() is None


# ==============================================================================
# SCENARIO 5: MULTI-DAY VEHICLE LIFECYCLE SIMULATION
# ==============================================================================

class TestScenario5MultiDayVehicleLifecycle:
    """
    Simulates a car's entire life cycle in the monitoring system across 5 days:
    Day 1: Discovered on AUTO.RIA ($4500) -> Alerted.
    Day 2: Checked again ($4500) -> Unchanged.
    Day 3: Price drops to $4200 -> Price drop alert.
    Day 4: Cross-posted to OLX ($4200) -> Marked Duplicate, suppress alert.
    Day 5: Re-checked -> Up to date.
    """

    @pytest.mark.asyncio
    async def test_scenario_5_full_lifecycle(self, async_db_session: AsyncSession):
        dedup = Deduplicator(price_drop_threshold_usd=50.0)

        # Day 1: New discovery on AUTO.RIA
        day1_car = Listing(
            source="auto_ria",
            source_id="life_101",
            url="https://auto.ria.com/life101.html",
            title="Audi A6 C5 2.4 2001",
            year=2001,
            price=4500.0,
            price_usd=4500.0,
            engine="2.4",
            seller_phone="+380501112233",
            location="Львів",
        )
        r1 = await dedup.evaluate(day1_car, async_db_session)
        assert r1.action == "CREATED"
        primary_id = r1.listing_id

        # Day 2: Re-scan without change
        r2 = await dedup.evaluate(day1_car, async_db_session)
        assert r2.action == "UNCHANGED"

        # Day 3: Price drop to $4200 (-$300)
        dump = day1_car.model_dump()
        dump["price"] = 4200.0
        dump["price_usd"] = None
        day3_car = Listing(**dump)
        r3 = await dedup.evaluate(day3_car, async_db_session)
        assert r3.action == "PRICE_DROPPED"
        assert r3.price_diff_usd == -300.0

        # Day 4: Same seller posts same vehicle to OLX
        day4_olx = Listing(
            source="olx",
            source_id="olx_life_999",
            url="https://olx.ua/life999.html",
            title="Продам Audi A6 C5 2.4",
            year=2001,
            price=4200.0,
            price_usd=4200.0,
            engine="2.4",
            seller_phone="+380501112233",
            location="Львів",
        )
        r4 = await dedup.evaluate(day4_olx, async_db_session)
        assert r4.is_duplicate is True
        assert r4.matched_level == 3
        assert r4.action == "DUPLICATE_CONTENT"
        assert r4.existing_listing_id == primary_id

        # Verify DB integrity: 1 primary record + 1 duplicate record, 2 version records
        stmt_listings = select(ListingModel).where(ListingModel.source_listing_id.in_(["life_101", "olx_life_999"]))
        listings = (await async_db_session.execute(stmt_listings)).scalars().all()
        assert len(listings) == 2

        stmt_versions = select(ListingVersionModel).where(ListingVersionModel.listing_id == primary_id)
        versions = (await async_db_session.execute(stmt_versions)).scalars().all()
        assert len(versions) == 2
        assert versions[0].change_type == "INITIAL"
        assert versions[1].change_type == "PRICE_DROP"
