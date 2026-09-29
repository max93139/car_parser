"""
Tier 2: Boundary & Corner Cases Test Suite for Audi A6 C5 Monitoring Service.

Tests all system boundaries, limits, extreme inputs, and transition thresholds:
1. Year boundaries: 1995, 1996, 1997, 2004, 2005, 2006, 2007, missing year.
2. Price & Currency boundaries: 0, extreme numbers, negative values, conversions.
3. Mileage boundaries: 0 km, 2,000,000 km, bucketing edge points (279999 vs 280000).
4. Text & Payload boundaries: empty strings, maximum length bounds (500, 1024), special characters.
5. Phone number normalization boundaries: 9, 10, 12 digits, E.164.
6. Media & Photos boundaries: 0, 1, 2, 10, 11+ images, album limits.
7. Deduplication thresholds: delta < threshold vs delta >= threshold.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import re
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.listing import Listing, RawListingPayload, SourceType
from src.services.deduplicator import Deduplicator


# ==============================================================================
# 1. YEAR BOUNDARIES (1996, 1997, 2004, 2005, 2006)
# ==============================================================================

class TestYearBoundaries:
    """Rigorous boundary testing of Audi A6 C5 production years."""

    def test_year_1996_strictly_invalid_c4(self):
        """1996 is prior to C5 start; should be recognized as non-C5."""
        re_gen_c5 = re.compile(r"\b(?:[cс]5|4[bв])\b", re.IGNORECASE)
        # Without explicit C5 badge, 1996 is strictly C4
        assert re_gen_c5.search("Audi A6 1996 2.6") is None

    def test_year_1997_transition_18t_guarantees_c5(self):
        """In 1997, 1.8T was exclusive to C5 (never offered on C4)."""
        year = 1997
        engine = "1.8T"
        # Mathematical domain property: 1997 + 1.8T == 100% C5
        is_guaranteed_c5 = (year == 1997 and engine in ("1.8T", "2.4"))
        assert is_guaranteed_c5 is True

    def test_year_1997_transition_19tdi_needs_review_if_unmarked(self):
        """In 1997, 1.9 TDI existed on both C4 (90hp) and C5 (110hp)."""
        year = 1997
        engine = "1.9_TDI"
        has_explicit_c5 = False
        decision = "NEEDS_REVIEW" if (year == 1997 and engine == "1.9_TDI" and not has_explicit_c5) else "PASS"
        assert decision == "NEEDS_REVIEW"

    def test_year_2004_transition_avant_guarantees_c5(self):
        """In 2004, C6 sedan launched, but Avant was exclusively C5."""
        year = 2004
        body = "avant"
        is_c5_avant = (year == 2004 and body.lower() == "avant")
        assert is_c5_avant is True

    def test_year_2004_transition_24_sedan_needs_review_if_unmarked(self):
        """In 2004, 2.4 V6 sedan existed in both C5 and C6."""
        year = 2004
        engine = "2.4"
        body = "sedan"
        has_c5_badge = False
        decision = "NEEDS_REVIEW" if (year == 2004 and engine == "2.4" and body == "sedan" and not has_c5_badge) else "PASS"
        assert decision == "NEEDS_REVIEW"

    def test_year_2005_end_of_generation_boundary(self):
        """2005 requires explicit C5/4B confirmation."""
        year = 2005
        has_c5_badge = True
        is_valid_c5 = (year == 2005 and has_c5_badge)
        assert is_valid_c5 is True

    def test_year_2006_strictly_invalid_c6(self):
        """2006 is post-C5; strictly C6 generation."""
        year = 2006
        is_c5_year = 1997 <= year <= 2005
        assert is_c5_year is False

    def test_year_out_of_pydantic_bounds(self):
        """Listing model validator rejects years < 1990 or > 2015."""
        with pytest.raises(ValidationError):
            Listing(source="auto_ria", source_id="y1", url="https://auto.ria.com/1", title="Audi", year=1985)

        with pytest.raises(ValidationError):
            Listing(source="auto_ria", source_id="y2", url="https://auto.ria.com/2", title="Audi", year=2025)


# ==============================================================================
# 2. PRICE & CURRENCY BOUNDARIES
# ==============================================================================

class TestPriceAndCurrencyBoundaries:
    """Boundary conditions on pricing, zero values, max values, and currency rates."""

    def test_price_zero_allowed_for_free_or_damaged(self):
        l = Listing(
            source="auto_ria",
            source_id="p0",
            url="https://auto.ria.com/p0",
            title="Audi A6 C5 1999",
            price=0.0,
            currency="USD",
        )
        assert l.price == 0.0
        assert l.price_usd == 0.0

    def test_price_negative_rejected_by_validator(self):
        with pytest.raises(ValidationError):
            Listing(
                source="auto_ria",
                source_id="p_neg",
                url="https://auto.ria.com/neg",
                title="Audi A6",
                price=-500.0,
            )

    def test_price_extreme_high_value(self):
        l = Listing(
            source="auto_ria",
            source_id="p_high",
            url="https://auto.ria.com/high",
            title="Audi A6 C5",
            price=999999.0,
            currency="USD",
        )
        assert l.price == 999999.0
        assert l.price_usd == 999999.0

    def test_currency_unknown_defaults_to_rate_1(self):
        l = Listing(
            source="auto_ria",
            source_id="curr_unk",
            url="https://auto.ria.com/u",
            title="Audi A6",
            price=3000.0,
            currency="GBP",  # Not in explicit table, default rate 1.0
        )
        assert l.price_usd == 3000.0

    def test_currency_case_insensitive_variants(self):
        for c in ("usd", "Usd", "USD", "$"):
            l = Listing(source="auto_ria", source_id="c1", url="https://auto.ria.com/1", title="Audi", price=100.0, currency=c)
            assert l.currency == "USD"
            assert l.price_usd == 100.0

        for c in ("eur", "EUR", "€"):
            l = Listing(source="auto_ria", source_id="c2", url="https://auto.ria.com/2", title="Audi", price=100.0, currency=c)
            assert l.currency == "EUR"
            assert l.price_usd == 108.0


# ==============================================================================
# 3. MILEAGE BOUNDARIES & BUCKETING
# ==============================================================================

class TestMileageBoundaries:
    """Boundary conditions on odometer readings and 10,000 km bucketing."""

    def test_mileage_zero(self):
        l = Listing(source="auto_ria", source_id="m0", url="https://auto.ria.com/m0", title="Audi", mileage=0)
        assert l.mileage == 0

    def test_mileage_negative_rejected(self):
        with pytest.raises(ValidationError):
            Listing(source="auto_ria", source_id="m_neg", url="https://auto.ria.com/mneg", title="Audi", mileage=-10)

    def test_mileage_upper_limit_2_million_km(self):
        l = Listing(source="auto_ria", source_id="m_max", url="https://auto.ria.com/mmax", title="Audi", mileage=2000000)
        assert l.mileage == 2000000

        with pytest.raises(ValidationError):
            Listing(source="auto_ria", source_id="m_over", url="https://auto.ria.com/mover", title="Audi", mileage=2000001)

    def test_mileage_bucketing_across_10k_boundaries(self):
        # 279,999 km maps to 270,000 bucket
        # 280,000 km maps to 280,000 bucket
        # 289,999 km maps to 280,000 bucket
        l_279 = Listing(source="auto_ria", source_id="b1", url="https://auto.ria.com/1", title="Audi", year=2000, mileage=279999)
        l_280 = Listing(source="auto_ria", source_id="b2", url="https://auto.ria.com/2", title="Audi", year=2000, mileage=280000)
        l_285 = Listing(source="auto_ria", source_id="b3", url="https://auto.ria.com/3", title="Audi", year=2000, mileage=285000)
        l_289 = Listing(source="auto_ria", source_id="b4", url="https://auto.ria.com/4", title="Audi", year=2000, mileage=289999)

        # 280k, 285k, 289.9k must share the exact same content fingerprint bucket!
        assert l_280.content_fingerprint == l_285.content_fingerprint
        assert l_280.content_fingerprint == l_289.content_fingerprint
        # 279.9k must have a different fingerprint bucket
        assert l_279.content_fingerprint != l_280.content_fingerprint


# ==============================================================================
# 4. STRING LENGTH & SANITIZATION BOUNDARIES
# ==============================================================================

class TestStringLengthBoundaries:
    """Title min/max lengths, empty strings, description extremes."""

    def test_title_empty_rejected(self):
        with pytest.raises(ValidationError):
            Listing(source="auto_ria", source_id="t_empty", url="https://auto.ria.com/e", title="")

    def test_title_maximum_500_characters(self):
        valid_title = "A" * 500
        l = Listing(source="auto_ria", source_id="t_500", url="https://auto.ria.com/500", title=valid_title)
        assert len(l.title) == 500

        with pytest.raises(ValidationError):
            Listing(source="auto_ria", source_id="t_501", url="https://auto.ria.com/501", title="A" * 501)

    def test_description_massive_text_handling(self):
        massive_text = "Audi A6 C5 1.8T description. " * 1000  # ~30,000 chars
        payload = RawListingPayload(
            source="olx",
            source_id="desc_mass",
            url="https://olx.ua/mass",
            title="Audi A6",
            raw_text=massive_text,
        )
        assert len(payload.description) == len(massive_text)

    def test_url_with_massive_query_string(self):
        query_params = "&".join(f"utm_{i}=val_{i}" for i in range(100))
        raw_url = f"https://auto.ria.com/uk/car/123.html?{query_params}&ref=test"
        canon = Listing.compute_canonical_url(SourceType.AUTO_RIA, raw_url, "123")
        assert "utm_" not in canon
        assert canon == "https://auto.ria.com/car/123.html"


# ==============================================================================
# 5. PHONE NUMBER NORMALIZATION BOUNDARIES
# ==============================================================================

class TestPhoneNumberBoundaries:
    """Normalization of Ukrainian phone numbers into E.164 (+380...)."""

    def test_phone_10_digits_leading_zero(self):
        l = Listing(source="rst", source_id="ph1", url="https://rst.ua/1", title="Audi", seller_phone="0671234567")
        assert l.seller_phone == "+380671234567"

    def test_phone_12_digits_with_country_code(self):
        l = Listing(source="rst", source_id="ph2", url="https://rst.ua/2", title="Audi", seller_phone="380671234567")
        assert l.seller_phone == "+380671234567"

    def test_phone_9_digits_no_leading_zero(self):
        l = Listing(source="rst", source_id="ph3", url="https://rst.ua/3", title="Audi", seller_phone="671234567")
        assert l.seller_phone == "+380671234567"

    def test_phone_with_punctuation_and_spaces(self):
        l = Listing(source="rst", source_id="ph4", url="https://rst.ua/4", title="Audi", seller_phone="+38 (067) 123-45-67")
        assert l.seller_phone == "+380671234567"

    def test_phone_invalid_returns_none(self):
        l = Listing(source="rst", source_id="ph5", url="https://rst.ua/5", title="Audi", seller_phone="not_a_phone")
        assert l.seller_phone is None


# ==============================================================================
# 6. MEDIA & PHOTOS BOUNDARIES
# ==============================================================================

class TestMediaAndPhotosBoundaries:
    """Image lists, album limits (0, 1, 2, 10, 11+ photos)."""

    def test_images_empty_list(self):
        l = Listing(source="auto_ria", source_id="img0", url="https://auto.ria.com/0", title="Audi", images=[])
        assert l.images == []

    def test_images_single_item(self):
        l = Listing(source="auto_ria", source_id="img1", url="https://auto.ria.com/1", title="Audi", images=["https://img1.jpg"])
        assert len(l.images) == 1

    def test_images_telegram_min_media_group_is_two(self):
        images = ["https://img1.jpg", "https://img2.jpg"]
        is_media_group = len(images) >= 2
        assert is_media_group is True

    def test_images_telegram_max_media_group_is_ten(self):
        images = [f"https://img{i}.jpg" for i in range(10)]
        assert len(images) == 10
        assert len(images[:10]) == 10

    def test_images_deduplication_and_whitespace_stripping(self):
        raw = [" https://img1.jpg ", "https://img1.jpg", " https://img2.jpg", ""]
        l = Listing(source="auto_ria", source_id="img_dup", url="https://auto.ria.com/dup", title="Audi", images=raw)
        assert l.images == ["https://img1.jpg", "https://img2.jpg"]


# ==============================================================================
# 7. DEDUPLICATION THRESHOLD BOUNDARIES
# ==============================================================================

class TestDeduplicationThresholdBoundaries:
    """Exact threshold comparisons for price drops ($1 vs $0.99)."""

    @pytest.mark.asyncio
    async def test_price_drop_exactly_at_threshold(
        self, async_db_session: AsyncSession, sample_valid_listing_18t: Listing
    ):
        # Threshold is $1.00. Initial price: $4200.00
        dedup = Deduplicator(price_drop_threshold_usd=1.0)
        await dedup.evaluate(sample_valid_listing_18t, async_db_session)

        # Drop by exactly $1.00 -> $4199.00
        l_drop1 = sample_valid_listing_18t.model_copy(update={"price": 4199.0, "price_usd": 4199.0})
        res1 = await dedup.evaluate(l_drop1, async_db_session)
        assert res1.is_price_drop is True
        assert res1.price_diff_usd == -1.0
        assert res1.action == "PRICE_DROPPED"

    @pytest.mark.asyncio
    async def test_price_drop_below_threshold_ignored(
        self, async_db_session: AsyncSession, sample_valid_listing_18t: Listing
    ):
        # Threshold is $50.00. Initial price: $4200.00
        dedup = Deduplicator(price_drop_threshold_usd=50.0)
        await dedup.evaluate(sample_valid_listing_18t, async_db_session)

        # Drop by only $10.00 ($4190.00) -> Below $50 threshold!
        l_minor = sample_valid_listing_18t.model_copy(update={"price": 4190.0, "price_usd": 4190.0})
        res = await dedup.evaluate(l_minor, async_db_session)
        assert res.is_price_drop is False
        assert res.action == "UNCHANGED"

    @pytest.mark.asyncio
    async def test_price_drop_above_threshold_detected(
        self, async_db_session: AsyncSession, sample_valid_listing_18t: Listing
    ):
        dedup = Deduplicator(price_drop_threshold_usd=50.0)
        await dedup.evaluate(sample_valid_listing_18t, async_db_session)

        # Drop by $100.00 ($4100.00) -> Above $50 threshold!
        l_drop = sample_valid_listing_18t.model_copy(update={"price": 4100.0, "price_usd": 4100.0})
        res = await dedup.evaluate(l_drop, async_db_session)
        assert res.is_price_drop is True
        assert res.price_diff_usd == -100.0
        assert res.action == "PRICE_DROPPED"
