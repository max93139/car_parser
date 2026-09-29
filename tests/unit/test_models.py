"""
Unit tests for Pydantic models: Listing, RawListingPayload, and FilterResult.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from src.models.filter_result import FilterResult, FilterStatus
from src.models.listing import (
    BodyType,
    Currency,
    DriveType,
    FuelType,
    Listing,
    ListingStatus,
    RawListingPayload,
    SourceType,
    TransmissionType,
)


def test_listing_defaults_and_validation(sample_listing: Listing):
    assert sample_listing.brand == "Audi"
    assert sample_listing.model == "A6"
    assert sample_listing.generation == "C5"
    assert sample_listing.year == 2001
    assert sample_listing.price == 4800.0
    assert sample_listing.currency == "USD"
    assert sample_listing.price_usd == 4800.0
    assert sample_listing.seller_phone == "+380979876543"
    assert sample_listing.content_fingerprint is not None
    assert len(sample_listing.content_fingerprint) == 64
    assert sample_listing.fuzzy_fingerprint is not None
    assert len(sample_listing.fuzzy_fingerprint) == 64


def test_canonical_url_auto_ria():
    raw_url = "https://auto.ria.com/uk/car/audi/a6/auto_audi_a6_36482145.html?utm_source=telegram&utm_medium=share&tab=reviews&search_id=9876"
    canonical = Listing.compute_canonical_url(SourceType.AUTO_RIA, raw_url, "36482145")
    assert canonical == "https://auto.ria.com/car/audi/a6/auto_audi_a6_36482145.html"

    # ru language prefix
    raw_url_ru = "https://auto.ria.com/ru/car/audi/a6/auto_audi_a6_36482145.html"
    canonical_ru = Listing.compute_canonical_url("auto_ria", raw_url_ru, "36482145")
    assert canonical_ru == "https://auto.ria.com/car/audi/a6/auto_audi_a6_36482145.html"


def test_canonical_url_olx():
    raw_url = "https://www.olx.ua/d/uk/obyavlenie/audi-a6-c5-1-8t-ID12345.html?reason=observed_ad&utm_source=chat"
    canonical = Listing.compute_canonical_url(SourceType.OLX, raw_url, "12345")
    assert canonical == "https://www.olx.ua/d/obyavlenie/audi-a6-c5-1-8t-ID12345.html"


def test_canonical_url_rst():
    raw_url = "https://rst.ua/ukr/oldcars/audi/a6/audi_a6_14238910.html?ref=front"
    canonical = Listing.compute_canonical_url(SourceType.RST, raw_url, "14238910")
    assert canonical == "https://rst.ua/oldcars/audi/a6/audi_a6_14238910.html"


def test_canonical_url_telegram():
    raw_url = "https://t.me/autobazar_ua/10423?comment=12"
    canonical = Listing.compute_canonical_url(SourceType.TELEGRAM, raw_url, "autobazar_ua_10423")
    assert "https://t.me/autobazar_ua/10423" in canonical


def test_canonical_url_instagram():
    raw_url = "https://www.instagram.com/p/C9Xyz123/?utm_source=ig_web_copy_link"
    canonical = Listing.compute_canonical_url(SourceType.INSTAGRAM, raw_url, "C9Xyz123")
    assert canonical == "https://www.instagram.com/p/C9Xyz123"


def test_price_usd_normalization():
    # USD
    l_usd = Listing(
        source="auto_ria",
        source_id="1",
        url="https://auto.ria.com/car/1.html",
        title="Audi A6",
        price=5000.0,
        currency="USD",
    )
    assert l_usd.price_usd == 5000.0

    # EUR
    l_eur = Listing(
        source="auto_ria",
        source_id="2",
        url="https://auto.ria.com/car/2.html",
        title="Audi A6",
        price=5000.0,
        currency="EUR",
    )
    assert l_eur.price_usd == round(5000.0 * 1.08, 2)

    # UAH
    l_uah = Listing(
        source="auto_ria",
        source_id="3",
        url="https://auto.ria.com/car/3.html",
        title="Audi A6",
        price=200000.0,
        currency="UAH",
    )
    assert l_uah.price_usd == round(200000.0 * 0.0241, 2)


def test_phone_normalization_formats():
    l1 = Listing(
        source="rst",
        source_id="1",
        url="https://rst.ua/1.html",
        title="Audi",
        seller_phone="+380501234567",
    )
    assert l1.seller_phone == "+380501234567"

    l2 = Listing(
        source="rst",
        source_id="2",
        url="https://rst.ua/2.html",
        title="Audi",
        seller_phone="050-123-45-67",
    )
    assert l2.seller_phone == "+380501234567"

    l3 = Listing(
        source="rst",
        source_id="3",
        url="https://rst.ua/3.html",
        title="Audi",
        seller_phone="(067) 999 88 77",
    )
    assert l3.seller_phone == "+380679998877"

    l4 = Listing(
        source="rst",
        source_id="4",
        url="https://rst.ua/4.html",
        title="Audi",
        seller_phone="invalid-phone",
    )
    assert l4.seller_phone is None


def test_image_cleaning_and_deduplication():
    l = Listing(
        source="olx",
        source_id="1",
        url="https://olx.ua/1.html",
        title="Audi A6",
        images=[
            "https://img.com/1.jpg ",
            "https://img.com/2.jpg",
            "https://img.com/1.jpg",  # duplicate
            "",                       # empty
            "https://img.com/3.jpg",
        ],
    )
    assert l.images == [
        "https://img.com/1.jpg",
        "https://img.com/2.jpg",
        "https://img.com/3.jpg",
    ]


def test_content_fingerprint_mileage_bucketing():
    l1 = Listing(
        source="auto_ria",
        source_id="1",
        url="https://auto.ria.com/1.html",
        title="Audi A6 1.8T",
        year=2001,
        engine="1.8T",
        mileage=284000,
        location="Київ",
        seller_phone="0501112233",
    )

    # Same car with minor mileage difference (within 10k bucket: 280,000)
    l2 = Listing(
        source="olx",
        source_id="2",
        url="https://olx.ua/2.html",
        title="Audi A6 1.8 Turbo",
        year=2001,
        engine="1.8T",
        mileage=281000,
        location="Киев",  # Kyiv synonym
        seller_phone="050 111 22 33",
    )

    assert l1.content_fingerprint == l2.content_fingerprint


def test_strict_vs_fuzzy_fingerprint():
    # Same car, different sellers
    l_seller1 = Listing(
        source="auto_ria",
        source_id="1",
        url="https://auto.ria.com/1.html",
        title="Audi A6",
        year=2002,
        engine="2.4",
        mileage=300000,
        location="Lviv",
        seller_phone="0501112233",
    )

    l_seller2 = Listing(
        source="rst",
        source_id="2",
        url="https://rst.ua/2.html",
        title="Audi A6",
        year=2002,
        engine="2.4",
        mileage=300000,
        location="Lviv",
        seller_phone="0679998877",  # Different phone
    )

    # Strict fingerprints should differ because phone numbers differ
    assert l_seller1.content_fingerprint != l_seller2.content_fingerprint
    # Fuzzy fingerprints should match because vehicle attributes match
    assert l_seller1.fuzzy_fingerprint == l_seller2.fuzzy_fingerprint


def test_raw_listing_payload_harmonization(sample_raw_payload: RawListingPayload):
    assert sample_raw_payload.description == sample_raw_payload.raw_text
    assert sample_raw_payload.seller == sample_raw_payload.seller_name
    assert sample_raw_payload.images == sample_raw_payload.image_urls
    assert sample_raw_payload.price == 4500.0
    assert sample_raw_payload.year == 2001
    assert sample_raw_payload.mileage == 284000


def test_filter_result_model():
    res_pass = FilterResult(
        status=FilterStatus.PASS,
        confidence=1.0,
        reasons=["MODEL_AUDI_A6_C5", "ENGINE_1.8T"],
        normalized_engine="1.8T",
        normalized_generation="C5",
    )
    assert res_pass.is_passed is True
    assert res_pass.is_rejected is False
    assert res_pass.is_review_needed is False

    res_reject = FilterResult(
        status=FilterStatus.REJECT,
        confidence=1.0,
        reasons=["NEGATIVE_KEYWORD_DISMANTLER"],
    )
    assert res_reject.is_passed is False
    assert res_reject.is_rejected is True

    res_review = FilterResult(
        status=FilterStatus.NEEDS_REVIEW,
        confidence=0.75,
        reasons=["AMBIGUOUS_ENGINE_DISPLACEMENT"],
    )
    assert res_review.is_review_needed is True


def test_listing_validation_errors():
    from pydantic import ValidationError

    # Year too early (< 1990)
    with pytest.raises(ValidationError):
        Listing(
            source="auto_ria",
            source_id="err1",
            url="https://auto.ria.com/1.html",
            title="Old car",
            year=1980,
        )

    # Year too late (> 2015)
    with pytest.raises(ValidationError):
        Listing(
            source="auto_ria",
            source_id="err2",
            url="https://auto.ria.com/2.html",
            title="Future car",
            year=2030,
        )

    # Negative price
    with pytest.raises(ValidationError):
        Listing(
            source="auto_ria",
            source_id="err3",
            url="https://auto.ria.com/3.html",
            title="Free car",
            price=-500.0,
        )

    # Negative mileage
    with pytest.raises(ValidationError):
        Listing(
            source="auto_ria",
            source_id="err4",
            url="https://auto.ria.com/4.html",
            title="Rolled car",
            mileage=-1000,
        )


def test_listing_json_serialization(sample_listing: Listing):
    json_str = sample_listing.model_dump_json()
    assert isinstance(json_str, str)
    assert "Audi" in json_str
    assert "36482145" in json_str
    # Can round-trip parse back to Listing
    parsed = Listing.model_validate_json(json_str)
    assert parsed.source_id == sample_listing.source_id
    assert parsed.price_usd == sample_listing.price_usd
    assert parsed.content_fingerprint == sample_listing.content_fingerprint
