"""
Tier 3: Cross-Feature Combinations & Pairwise Test Suite for Audi A6 C5 Monitoring Service.

Tests interactions between modules:
1. Multi-source Scrapers x Unified Listing Model (AUTO.RIA, OLX, RST, Telegram, Instagram).
2. Filtering Outcomes x Deduplication Service (PASS -> NEW, REJECT -> Skip/Ignore, NEEDS_REVIEW -> Saved).
3. Cross-platform Deduplication (AUTO.RIA vs OLX vs RST vs Telegram vs Instagram).
4. Deduplication State x Telegram Notification Triggering (Only NEW or PRICE_DROPPED alerted).
5. Currency Normalization x Price Drop Tracking.
"""

from __future__ import annotations

import json
from decimal import Decimal
import pytest
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.filter_result import FilterResult, FilterStatus
from src.models.listing import Listing, RawListingPayload, SourceType
from src.services.deduplicator import Deduplicator


# ==============================================================================
# 1. SCRAPER PAYLOADS x UNIFIED LISTING MODEL
# ==============================================================================

class TestScrapersToUnifiedListingPipeline:
    """Pairwise verification: raw scraper outputs transformed into unified Listing."""

    def test_auto_ria_html_to_listing_model(self, fixture_loader):
        html = fixture_loader("auto_ria/sample_search_page.html")
        soup = BeautifulSoup(html, "html.parser")
        ticket = soup.select_one('section[data-auto-id="36482145"]')
        assert ticket is not None

        # Transform HTML extraction into RawListingPayload
        raw = RawListingPayload(
            source="auto_ria",
            source_id=ticket.get("data-auto-id"),
            url=ticket.select_one("a.address")["href"],
            title=ticket.select_one("a.address").get_text(strip=True),
            raw_price=ticket.select_one('span[data-currency="USD"]').get_text(strip=True),
            price=4200.0,
            currency="USD",
            raw_year="1999",
            year=1999,
            raw_mileage="280 тис. км",
            mileage=280000,
            raw_engine="Бензин, 1.8 л.",
            engine="1.8T",
            raw_location="Київ",
            location="Київ",
            image_urls=[img["src"] for img in ticket.select("img") if img.get("src")],
        )

        listing = Listing(**raw.model_dump())
        assert listing.brand == "Audi"
        assert listing.model == "A6"
        assert listing.year == 1999
        assert listing.price_usd == 4200.0
        assert listing.canonical_url == "https://auto.ria.com/auto_audi_a6_36482145.html"

    def test_olx_json_to_listing_model(self, json_fixture_loader):
        data = json_fixture_loader("olx/sample_prerendered_state.json")
        ad = data["listing"]["listing"]["ads"][0]
        params = {p["key"]: p["value"]["label"] for p in ad["params"]}

        raw = RawListingPayload(
            source="olx",
            source_id=str(ad["id"]),
            url=ad["url"],
            title=ad["title"],
            raw_text=ad["description"],
            price=float(ad["price"]["value"]),
            currency=ad["price"]["currency"],
            year=int(params["year"]),
            mileage=int(params["milage"]),
            engine=f"{params['engine_capacity']} TDI",
            fuel_type="diesel",
            transmission="manual",
            location=ad["location"]["city"]["name"],
            image_urls=[p["link"].replace("{width}x{height}", "1000x700") for p in ad["photos"]],
        )

        listing = Listing(**raw.model_dump())
        assert listing.source == "olx"
        assert listing.year == 2003
        assert listing.mileage == 320000
        assert listing.location_city == "Луцьк"
        assert listing.price_usd == 4500.0

    def test_rst_html_to_listing_model(self, fixture_loader):
        html = fixture_loader("rst/sample_rst_search.html")
        soup = BeautifulSoup(html, "html.parser")
        card = soup.select(".rst-ocb-i")[0]

        raw = RawListingPayload(
            source="rst",
            source_id="14238910",
            url="https://rst.ua/ukr/oldcars/audi/a6/audi_a6_14238910.html",
            title=card.select_one(".rst-ocb-i-h").get_text(strip=True),
            raw_price=card.select_one(".rst-ocb-i-d-s-p").get_text(strip=True),
            price=4100.0,
            currency="USD",
            year=2001,
            mileage=295000,
            engine="2.4",
            location="Київ",
            image_urls=["https://img.rst.ua/oldcars/audi/a6/audi_a6_14238910_0.jpg"],
        )

        listing = Listing(**raw.model_dump())
        assert listing.source == "rst"
        assert listing.price_usd == 4100.0
        assert listing.canonical_url == "https://rst.ua/oldcars/audi/a6/audi_a6_14238910.html"

    def test_telegram_post_to_listing_model(self, json_fixture_loader):
        post = json_fixture_loader("telegram/sample_single_post.json")
        raw = RawListingPayload(
            source="telegram",
            source_id=f"{post['channel']}_{post['id']}",
            url=post["url"],
            title="Audi A6 1.9 TDI 2003",
            raw_text=post["message"],
            price=4800.0,
            currency="USD",
            year=2003,
            mileage=315000,
            engine="1.9 TDI",
            location="Тернопіль",
            seller_phone="+380987654321",
            image_urls=post["photo_urls"],
        )

        listing = Listing(**raw.model_dump())
        assert listing.source == "telegram"
        assert listing.seller_phone == "+380987654321"
        assert len(listing.images) == 2

    def test_instagram_edge_to_listing_model(self, json_fixture_loader):
        data = json_fixture_loader("instagram/sample_profile_response.json")
        edge = data["data"]["user"]["edge_owner_to_timeline_media"]["edges"][0]["node"]
        caption = edge["edge_media_to_caption"]["edges"][0]["node"]["text"]

        raw = RawListingPayload(
            source="instagram",
            source_id=f"autopodbor_ua_{edge['shortcode']}",
            url=f"https://www.instagram.com/p/{edge['shortcode']}/",
            title=caption.split("\n")[0],
            raw_text=caption,
            price=4200.0,
            currency="USD",
            year=2001,
            engine="2.4",
            location="Київ",
            seller_phone="0501112233",
            image_urls=[edge["display_url"]],
        )

        listing = Listing(**raw.model_dump())
        assert listing.source == "instagram"
        assert listing.seller_phone == "+380501112233"
        assert listing.price == 4200.0


# ==============================================================================
# 2. FILTERING OUTCOMES x DEDUPLICATION BEHAVIOR
# ==============================================================================

class TestFilteringAndDeduplicationInteractions:
    """Pairwise verification: how filter status governs deduplicator persistence."""

    @pytest.mark.asyncio
    async def test_filter_pass_creates_new_pending_telegram_listing(
        self, async_db_session: AsyncSession, sample_valid_listing_18t: Listing
    ):
        filter_res = FilterResult(
            status=FilterStatus.PASS,
            confidence=1.0,
            reasons=["AUDI_A6_C5_1.8T"],
            normalized_engine="1.8T",
            normalized_generation="C5",
        )
        assert filter_res.is_passed is True

        sample_valid_listing_18t.status = "NEW"
        dedup = Deduplicator()
        res = await dedup.evaluate(sample_valid_listing_18t, async_db_session)

        assert res.action == "CREATED"
        assert res.is_duplicate is False

    @pytest.mark.asyncio
    async def test_filter_needs_review_saved_without_telegram_flag(
        self, async_db_session: AsyncSession
    ):
        review_listing = Listing(
            source="auto_ria",
            source_id="rev_101",
            url="https://auto.ria.com/rev101.html",
            title="Audi A6 2004 2.4 седан",
            year=2004,
            engine="2.4",
            status="NEEDS_REVIEW",  # Transition year ambiguity
        )
        dedup = Deduplicator()
        res = await dedup.evaluate(review_listing, async_db_session)

        assert res.action == "CREATED"
        # Stored in DB with status NEEDS_REVIEW
        assert review_listing.status == "NEEDS_REVIEW"
        assert review_listing.is_sent_to_telegram is False


# ==============================================================================
# 3. CROSS-PLATFORM DEDUPLICATION (AUTO.RIA vs OLX vs RST)
# ==============================================================================

class TestCrossPlatformDeduplication:
    """Simulates realistic Ukrainian market cross-posting across websites."""

    @pytest.mark.asyncio
    async def test_cross_platform_dedup_auto_ria_then_olx(
        self, async_db_session: AsyncSession
    ):
        # 1. Seller posts to AUTO.RIA
        auto_ria_ad = Listing(
            source="auto_ria",
            source_id="ria_9991",
            url="https://auto.ria.com/uk/auto_audi_a6_ria_9991.html",
            title="Audi A6 C5 1.8T 2000",
            year=2000,
            price=4300.0,
            mileage=290000,
            engine="1.8 Turbo",
            engine_code="1.8T",
            location="Львів",
            seller_phone="+380671112233",
        )
        dedup = Deduplicator()
        res1 = await dedup.evaluate(auto_ria_ad, async_db_session)
        assert res1.action == "CREATED"
        original_db_id = res1.listing_id

        # 2. Same seller posts same vehicle to OLX
        olx_ad = Listing(
            source="olx",
            source_id="olx_5552",
            url="https://www.olx.ua/d/uk/obyavlenie/audi-a6-c5-ID5552.html",
            title="Продам Ауді А6 С5 1.8 турбо",
            year=2000,
            price=4300.0,
            mileage=290000,
            engine="1.8T",
            engine_code="1.8T",
            location="Львів",
            seller_phone="+380671112233",
        )
        # Content fingerprint matches Level 3
        assert olx_ad.content_fingerprint == auto_ria_ad.content_fingerprint

        res2 = await dedup.evaluate(olx_ad, async_db_session)
        assert res2.is_duplicate is True
        assert res2.matched_level == 3
        assert res2.action == "DUPLICATE_CONTENT"
        assert res2.existing_listing_id == original_db_id

    @pytest.mark.asyncio
    async def test_cross_platform_dedup_auto_ria_then_telegram_channel(
        self, async_db_session: AsyncSession
    ):
        # 1. Listing on AUTO.RIA
        ria_ad = Listing(
            source="auto_ria",
            source_id="ria_7771",
            url="https://auto.ria.com/uk/car/ria_7771.html",
            title="Audi A6 C5 1.9 TDI 2003",
            year=2003,
            price=4700.0,
            mileage=310000,
            engine="1.9 TDI",
            engine_code="1.9_TDI",
            location="Київ",
            seller="Дмитро",
            seller_phone="+380509876543",
        )
        dedup = Deduplicator()
        res1 = await dedup.evaluate(ria_ad, async_db_session)
        assert res1.action == "CREATED"

        # 2. Reposted in Telegram channel
        tg_ad = Listing(
            source="telegram",
            source_id="autobazar_ua_2048",
            url="https://t.me/autobazar_ua/2048",
            title="Audi A6 1.9 TDI 2003",
            year=2003,
            price=4700.0,
            mileage=310000,
            engine="1.9 TDI",
            engine_code="1.9_TDI",
            location="Київ",
            seller="Дмитро",
            seller_phone="+380509876543",
        )
        res2 = await dedup.evaluate(tg_ad, async_db_session)
        assert res2.is_duplicate is True
        assert res2.matched_level == 3
        assert res2.action == "DUPLICATE_CONTENT"


# ==============================================================================
# 4. DEDUPLICATION STATE x NOTIFICATION TRIGGERING
# ==============================================================================

class TestDeduplicationToNotificationTriggering:
    """Pairwise verification: how dedup results drive Telegram dispatch decisions."""

    @pytest.mark.asyncio
    async def test_only_new_or_price_dropped_are_dispatched(
        self, async_db_session: AsyncSession, sample_valid_listing_18t: Listing
    ):
        dedup = Deduplicator()

        # Step 1: Initial creation -> DISPATCH ALERT
        r1 = await dedup.evaluate(sample_valid_listing_18t, async_db_session)
        should_alert_1 = (r1.action in ("CREATED", "PRICE_DROPPED"))
        assert should_alert_1 is True

        # Step 2: Unchanged re-check -> SUPPRESS ALERT
        r2 = await dedup.evaluate(sample_valid_listing_18t, async_db_session)
        should_alert_2 = (r2.action in ("CREATED", "PRICE_DROPPED"))
        assert should_alert_2 is False

        # Step 3: Duplicate cross-post -> SUPPRESS ALERT
        cross_post = sample_valid_listing_18t.model_copy(update={"source": "olx", "source_id": "cross_99"})
        r3 = await dedup.evaluate(cross_post, async_db_session)
        should_alert_3 = (r3.action in ("CREATED", "PRICE_DROPPED"))
        assert should_alert_3 is False

        # Step 4: Price Drop -> DISPATCH PRICE DROP ALERT
        drop = sample_valid_listing_18t.model_copy(update={"price": 3800.0, "price_usd": 3800.0})
        r4 = await dedup.evaluate(drop, async_db_session)
        should_alert_4 = (r4.action in ("CREATED", "PRICE_DROPPED"))
        assert should_alert_4 is True
        assert r4.is_price_drop is True


# ==============================================================================
# 5. CURRENCY NORMALIZATION x PRICE DROP TRACKING
# ==============================================================================

class TestCurrencyNormalizationAndPriceDrop:
    """Pairwise verification: multi-currency listings and accurate USD delta tracking."""

    @pytest.mark.asyncio
    async def test_currency_conversion_price_drop_accuracy(
        self, async_db_session: AsyncSession
    ):
        # Car initially listed at 4000 EUR (~ $4320 USD)
        l_eur = Listing(
            source="auto_ria",
            source_id="curr_drop_1",
            url="https://auto.ria.com/eur1.html",
            title="Audi A6 C5 2002 2.4",
            year=2002,
            price=4000.0,
            currency="EUR",
        )
        assert l_eur.price_usd == 4320.0

        dedup = Deduplicator()
        await dedup.evaluate(l_eur, async_db_session)

        # Price dropped to 3600 EUR (~ $3888 USD) -> delta = -$432 USD
        dump = l_eur.model_dump()
        dump["price"] = 3600.0
        dump["price_usd"] = None
        l_eur_dropped = Listing(**dump)
        assert l_eur_dropped.price_usd == 3888.0

        res = await dedup.evaluate(l_eur_dropped, async_db_session)
        assert res.is_price_drop is True
        assert res.price_diff_usd == -432.0
        assert res.action == "PRICE_DROPPED"
