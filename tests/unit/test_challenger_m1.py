"""
Challenger 1 Adversarial & Empirical Stress Test Suite for Milestone M1.

Evaluates:
1. Level 1 deduplication collision resistance, idempotency, and price drop vs increase state machine.
2. Level 2 URL canonicalization permutations and tracking parameter stripping.
3. Level 3 SHA-256 fingerprint collision resistance, cross-platform matching, and false-positive mining.
4. Currency conversion and price threshold logic.
5. High-throughput stress test (1,000 distinct listings + 500 duplicates).
6. Empirical Bug Reproductions (RecursionError on empty URL, null-price transition, Instagram igsh leak, city prefix false negatives).
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import ListingModel, ListingVersionModel, SourceModel
from src.models.listing import Listing, SourceType
from src.services.deduplicator import Deduplicator


# =====================================================================
# 1. Level 1 Deduplication & Collision Tests
# =====================================================================

@pytest.mark.asyncio
async def test_level_1_cross_source_same_id_no_collision(db_session: AsyncSession):
    """
    Ensure listings from different sources with identical source_id do NOT collide.
    e.g. auto_ria '1001' vs olx '1001'.
    """
    dedup = Deduplicator()

    l_ria = Listing(
        source="auto_ria",
        source_id="1001",
        url="https://auto.ria.com/car/1001.html",
        title="Audi A6 C5 1.8T AutoRia",
        year=2000,
        price=4500.0,
        engine="1.8T",
        mileage=250000,
        location="Київ",
    )
    l_olx = Listing(
        source="olx",
        source_id="1001",
        url="https://www.olx.ua/d/obyavlenie/audi-1001.html",
        title="Audi A6 C5 2.4 OLX",
        year=2002,
        price=5200.0,
        engine="2.4",
        mileage=310000,
        location="Львів",
    )

    res1 = await dedup.evaluate(l_ria, db_session)
    assert res1.is_duplicate is False
    assert res1.action == "CREATED"

    res2 = await dedup.evaluate(l_olx, db_session)
    assert res2.is_duplicate is False
    assert res2.action == "CREATED"
    assert res2.listing_id != res1.listing_id


@pytest.mark.asyncio
async def test_level_1_idempotency_100_runs(db_session: AsyncSession, sample_listing: Listing):
    """
    100 repeated re-scrapes with unchanged price must return is_duplicate=True,
    matched_level=1, action='UNCHANGED', and produce zero duplicate records or versions.
    """
    dedup = Deduplicator()
    res_init = await dedup.evaluate(sample_listing, db_session)
    assert res_init.is_duplicate is False
    init_id = res_init.listing_id

    for _ in range(100):
        res = await dedup.evaluate(sample_listing, db_session)
        assert res.is_duplicate is True
        assert res.matched_level == 1
        assert res.existing_listing_id == init_id
        assert res.action == "UNCHANGED"
        assert res.is_price_drop is False

    # Check that database has exactly 1 listing and exactly 1 version
    stmt_count = select(func.count(ListingModel.id))
    total_listings = (await db_session.execute(stmt_count)).scalar()
    assert total_listings == 1

    stmt_ver_count = select(func.count(ListingVersionModel.id)).where(
        ListingVersionModel.listing_id == init_id
    )
    total_versions = (await db_session.execute(stmt_ver_count)).scalar()
    assert total_versions == 1


@pytest.mark.asyncio
async def test_level_1_whitespace_in_ids(db_session: AsyncSession):
    """Whitespace in source_id should be stripped by Pydantic model."""
    dedup = Deduplicator()
    l1 = Listing(
        source="auto_ria",
        source_id="  334455  ",
        url="https://auto.ria.com/car/334455.html",
        title="Audi A6 C5",
        price=4000.0,
    )
    assert l1.source_id == "334455"

    res1 = await dedup.evaluate(l1, db_session)
    assert res1.is_duplicate is False

    l2 = Listing(
        source="auto_ria",
        source_id="334455",
        url="https://auto.ria.com/car/334455.html",
        title="Audi A6 C5",
        price=4000.0,
    )
    res2 = await dedup.evaluate(l2, db_session)
    assert res2.is_duplicate is True
    assert res2.matched_level == 1


# =====================================================================
# 2. Level 2 URL Canonicalization Permutations & Edge Cases
# =====================================================================

@pytest.mark.parametrize(
    "raw_url,expected_canonical",
    [
        # AUTO.RIA trailing slashes, tracking query params, language subpaths, uppercasing
        (
            "https://AUTO.RIA.COM/uk/car/audi/a6/auto_audi_a6_99999.html/",
            "https://auto.ria.com/car/audi/a6/auto_audi_a6_99999.html",
        ),
        (
            "http://auto.ria.com/ru/car/audi/a6/auto_audi_a6_99999.html?utm_source=facebook&utm_campaign=retarget&gclid=123",
            "https://auto.ria.com/car/audi/a6/auto_audi_a6_99999.html",
        ),
        (
            "https://m.auto.ria.com/car/audi/a6/auto_audi_a6_99999.html?search_id=1&session_id=abc#photos",
            "https://auto.ria.com/car/audi/a6/auto_audi_a6_99999.html",
        ),
        (
            "https://auto.ria.com:443/uk/car/audi/a6/auto_audi_a6_99999.html?b=2&a=1",
            "https://auto.ria.com/car/audi/a6/auto_audi_a6_99999.html?a=1&b=2",
        ),
        # OLX variants
        (
            "https://www.olx.ua/d/uk/obyavlenie/audi-a6-c5-ID777.html?reason=observed_ad&fbclid=XYZ",
            "https://www.olx.ua/d/obyavlenie/audi-a6-c5-ID777.html",
        ),
        (
            "https://olx.ua/d/ru/obyavlenie/audi-a6-c5-ID777.html/?ref=feed",
            "https://www.olx.ua/d/obyavlenie/audi-a6-c5-ID777.html",
        ),
        (
            "https://m.olx.ua/uk/obyavlenie/audi-a6-c5-ID777.html",
            "https://www.olx.ua/obyavlenie/audi-a6-c5-ID777.html",
        ),
        # RST variants
        (
            "https://rst.ua/ukr/oldcars/audi/a6/audi_a6_555.html?from=front",
            "https://rst.ua/oldcars/audi/a6/audi_a6_555.html",
        ),
        (
            "http://RST.UA/oldcars/audi/a6/audi_a6_555.html/",
            "https://rst.ua/oldcars/audi/a6/audi_a6_555.html",
        ),
        # Telegram variants
        (
            "https://t.me/audi_club_ua/12345?comment=999#msg",
            "https://t.me/audi_club_ua/12345?comment=999",
        ),
        # Instagram standard link
        (
            "https://instagram.com/p/DA12345/?utm_source=ig_web_copy_link",
            "https://www.instagram.com/p/DA12345",
        ),
    ],
)
def test_url_canonicalization_oracle(raw_url: str, expected_canonical: str):
    canonical = Listing.compute_canonical_url("auto_ria" if "ria" in raw_url else "olx" if "olx" in raw_url else "rst" if "rst" in raw_url else "telegram" if "t.me" in raw_url else "instagram", raw_url, "test_id")
    assert canonical == expected_canonical


@pytest.mark.asyncio
async def test_level_2_deduplication_via_url_permutations(db_session: AsyncSession):
    """
    Ingesting different URL permutations of the same listing (with different source_ids)
    must trigger Level 2 deduplication.
    """
    dedup = Deduplicator()

    primary = Listing(
        source="auto_ria",
        source_id="primary_123",
        url="https://auto.ria.com/uk/car/audi/a6/auto_audi_a6_12345.html",
        title="Audi A6 C5 1.8T",
        price=4500.0,
    )
    res_primary = await dedup.evaluate(primary, db_session)
    assert res_primary.is_duplicate is False
    primary_id = res_primary.listing_id

    # Permutations with different source_id to bypass Level 1
    url_variations = [
        "https://auto.ria.com/ru/car/audi/a6/auto_audi_a6_12345.html",
        "https://auto.ria.com/car/audi/a6/auto_audi_a6_12345.html/",
        "https://AUTO.RIA.COM/uk/car/audi/a6/auto_audi_a6_12345.html?utm_source=telegram&utm_medium=cpc",
        "https://m.auto.ria.com/uk/car/audi/a6/auto_audi_a6_12345.html?gclid=abcd1234#gallery",
    ]

    for idx, var_url in enumerate(url_variations):
        var_listing = Listing(
            source="auto_ria",
            source_id=f"variant_{idx}",
            url=var_url,
            title="Audi A6 C5 1.8T",
            price=4500.0,
        )
        res = await dedup.evaluate(var_listing, db_session)
        assert res.is_duplicate is True, f"Failed for URL: {var_url}"
        assert res.matched_level == 2, f"Failed to match Level 2 for URL: {var_url}"
        assert res.existing_listing_id == primary_id
        assert res.action == "DUPLICATE_URL"


# =====================================================================
# 3. Level 3 SHA-256 Content Fingerprint Collisions & Cross-Posting
# =====================================================================

@pytest.mark.asyncio
async def test_level_3_cross_platform_trio(db_session: AsyncSession):
    """
    Identical vehicle cross-posted to AUTO.RIA, OLX, and RST.
    All 3 have different URLs, different sources, and different source_ids.
    First must be CREATED, subsequent two must be DUPLICATE_CONTENT at Level 3.
    """
    dedup = Deduplicator()

    ria = Listing(
        source="auto_ria",
        source_id="ria_881",
        url="https://auto.ria.com/car/audi/a6/auto_audi_a6_881.html",
        title="Audi A6 C5 1.8T 2001",
        year=2001,
        body_type="sedan",
        price=4700.0,
        currency="USD",
        mileage=285000,
        engine="1.8T",
        engine_code="1.8T",
        fuel_type="petrol",
        transmission="manual",
        drive_type="quattro",
        location="Київ",
        seller="Михайло",
        seller_phone="+380509998877",
    )

    olx = Listing(
        source="olx",
        source_id="olx_992",
        url="https://www.olx.ua/d/obyavlenie/audi-a6-c5-1-8t-2001-ID992.html",
        title="Продам Audi A6 C5 1.8 Turbo",
        year=2001,
        body_type="sedan",
        price=4700.0,
        currency="USD",
        mileage=282000,  # 282k falls in same 280k bucket
        engine="1.8 Turbo",
        engine_code="1.8T",
        fuel_type="petrol",
        transmission="manual",
        drive_type="quattro",
        location="Киев",  # Kyiv synonym
        seller="Михайло",
        seller_phone="050-999-88-77",  # Format variation
    )

    rst = Listing(
        source="rst",
        source_id="rst_773",
        url="https://rst.ua/oldcars/audi/a6/audi_a6_773.html",
        title="Audi A6 1.8 T sedan 2001",
        year=2001,
        body_type="sedan",
        price=4700.0,
        currency="USD",
        mileage=289000,  # 289k also falls in 280k bucket
        engine="1.8T",
        engine_code="1.8T",
        fuel_type="petrol",
        transmission="manual",
        drive_type="quattro",
        location="kyiv",
        seller="Михайло",
        seller_phone="+38 (050) 999 88 77",
    )

    # 1. SHA-256 invariant fingerprints must be identical
    assert ria.content_fingerprint == olx.content_fingerprint
    assert olx.content_fingerprint == rst.content_fingerprint

    # 2. Sequential ingestion
    res_ria = await dedup.evaluate(ria, db_session)
    assert res_ria.is_duplicate is False
    assert res_ria.action == "CREATED"
    ria_db_id = res_ria.listing_id

    res_olx = await dedup.evaluate(olx, db_session)
    assert res_olx.is_duplicate is True
    assert res_olx.matched_level == 3
    assert res_olx.existing_listing_id == ria_db_id
    assert res_olx.action == "DUPLICATE_CONTENT"

    res_rst = await dedup.evaluate(rst, db_session)
    assert res_rst.is_duplicate is True
    assert res_rst.matched_level == 3
    assert res_rst.action == "DUPLICATE_CONTENT"


def test_level_3_collision_resistance_single_diff():
    """
    Varying any single spec must yield a different SHA-256 content_fingerprint.
    """
    base = Listing(
        source="auto_ria",
        source_id="1",
        url="https://auto.ria.com/1.html",
        title="Audi A6 C5",
        year=2001,
        body_type="sedan",
        engine_code="1.8T",
        fuel_type="petrol",
        transmission="manual",
        drive_type="quattro",
        mileage=285000,
        location="Київ",
        seller_phone="+380501112233",
    )

    # 1. Different year
    diff_year = base.model_copy(update={"year": 2002, "content_fingerprint": None})
    diff_year = diff_year.compute_derived_fields()
    assert diff_year.content_fingerprint != base.content_fingerprint

    # 2. Different body type
    diff_body = base.model_copy(update={"body_type": "avant", "content_fingerprint": None})
    diff_body = diff_body.compute_derived_fields()
    assert diff_body.content_fingerprint != base.content_fingerprint

    # 3. Different engine
    diff_engine = base.model_copy(update={"engine_code": "2.4", "content_fingerprint": None})
    diff_engine = diff_engine.compute_derived_fields()
    assert diff_engine.content_fingerprint != base.content_fingerprint

    # 4. Different fuel type
    diff_fuel = base.model_copy(update={"fuel_type": "gas_petrol", "content_fingerprint": None})
    diff_fuel = diff_fuel.compute_derived_fields()
    assert diff_fuel.content_fingerprint != base.content_fingerprint

    # 5. Different transmission
    diff_trans = base.model_copy(update={"transmission": "automatic", "content_fingerprint": None})
    diff_trans = diff_trans.compute_derived_fields()
    assert diff_trans.content_fingerprint != base.content_fingerprint

    # 6. Different drive type
    diff_drive = base.model_copy(update={"drive_type": "front", "content_fingerprint": None})
    diff_drive = diff_drive.compute_derived_fields()
    assert diff_drive.content_fingerprint != base.content_fingerprint

    # 7. Different mileage bucket (285k -> 295k)
    diff_mileage = base.model_copy(update={"mileage": 295000, "content_fingerprint": None})
    diff_mileage = diff_mileage.compute_derived_fields()
    assert diff_mileage.content_fingerprint != base.content_fingerprint

    # 8. Different city (Kyiv -> Lviv)
    diff_city = base.model_copy(update={"location": "Львів", "location_city": "Львів", "content_fingerprint": None})
    diff_city = diff_city.compute_derived_fields()
    assert diff_city.content_fingerprint != base.content_fingerprint

    # 9. Different seller phone
    diff_seller = base.model_copy(update={"seller_phone": "+380509990000", "content_fingerprint": None})
    diff_seller = diff_seller.compute_derived_fields()
    assert diff_seller.content_fingerprint != base.content_fingerprint


@pytest.mark.parametrize(
    "loc1,loc2",
    [
        ("Київ", "Киев"),
        ("Львів", "Львов"),
        ("Одеса", "Одесса"),
        ("Дніпро", "Днепр"),
        ("Дніпропетровськ", "Днепропетровск"),
        ("Харків", "Харьков"),
    ],
)
def test_city_normalization_synonyms(loc1: str, loc2: str):
    l1 = Listing(
        source="auto_ria",
        source_id="1",
        url="https://auto.ria.com/1.html",
        title="Audi A6",
        location=loc1,
        year=2000,
        mileage=200000,
    )
    l2 = Listing(
        source="olx",
        source_id="2",
        url="https://olx.ua/2.html",
        title="Audi A6",
        location=loc2,
        year=2000,
        mileage=200000,
    )
    assert l1.content_fingerprint == l2.content_fingerprint


# =====================================================================
# 4. Price Drop vs Increase vs Currency Tests
# =====================================================================

@pytest.mark.asyncio
async def test_price_drop_and_increase_state_machine(db_session: AsyncSession):
    """
    Full lifecycle test of price updates:
    INITIAL ($5000) -> DROP ($4500) -> UNCHANGED ($4500) -> INCREASE ($4800) -> DROP ($4000)
    """
    dedup = Deduplicator(price_drop_threshold_usd=1.0)
    listing = Listing(
        source="auto_ria",
        source_id="price_cycle_1",
        url="https://auto.ria.com/car/price_cycle_1.html",
        title="Audi A6 C5",
        price=5000.0,
        currency="USD",
    )

    # 1. Initial creation
    r1 = await dedup.evaluate(listing, db_session)
    assert r1.action == "CREATED"
    assert r1.is_price_drop is False
    assert r1.price_diff_usd is None

    # 2. Price drop: 5000 -> 4500 (-$500)
    l_drop1 = listing.model_copy(update={"price": 4500.0, "price_usd": 4500.0})
    r2 = await dedup.evaluate(l_drop1, db_session)
    assert r2.action == "PRICE_DROPPED"
    assert r2.is_price_drop is True
    assert r2.price_diff_usd == -500.0

    # 3. Unchanged: 4500 -> 4500 ($0)
    r3 = await dedup.evaluate(l_drop1, db_session)
    assert r3.action == "UNCHANGED"
    assert r3.is_price_drop is False
    assert r3.price_diff_usd == 0.0

    # 4. Price increase: 4500 -> 4800 (+$300)
    l_inc = listing.model_copy(update={"price": 4800.0, "price_usd": 4800.0})
    r4 = await dedup.evaluate(l_inc, db_session)
    assert r4.action == "UPDATED"
    assert r4.is_price_drop is False
    assert r4.price_diff_usd == 300.0

    # 5. Second price drop: 4800 -> 4000 (-$800)
    l_drop2 = listing.model_copy(update={"price": 4000.0, "price_usd": 4000.0})
    r5 = await dedup.evaluate(l_drop2, db_session)
    assert r5.action == "PRICE_DROPPED"
    assert r5.is_price_drop is True
    assert r5.price_diff_usd == -800.0

    # Verify history in listing_versions
    v_stmt = (
        select(ListingVersionModel)
        .where(ListingVersionModel.listing_id == r1.listing_id)
        .order_by(ListingVersionModel.id)
    )
    versions = (await db_session.execute(v_stmt)).scalars().all()
    assert len(versions) == 4
    assert versions[0].change_type == "INITIAL"
    assert versions[0].price_usd == Decimal("5000.00")
    assert versions[1].change_type == "PRICE_DROP"
    assert versions[1].price_diff_usd == Decimal("-500.00")
    assert versions[2].change_type == "PRICE_INCREASE"
    assert versions[2].price_diff_usd == Decimal("300.00")
    assert versions[3].change_type == "PRICE_DROP"
    assert versions[3].price_diff_usd == Decimal("-800.00")


@pytest.mark.asyncio
async def test_currency_conversion_price_drop(db_session: AsyncSession):
    """
    Seller converts listing currency from 200,000 UAH (~$4,820 USD) to $4,200 USD.
    Must correctly detect price drop in USD equivalent (-$620).
    """
    dedup = Deduplicator()
    l_uah = Listing(
        source="olx",
        source_id="curr_conv_1",
        url="https://olx.ua/curr_conv_1.html",
        title="Audi A6 C5 1.8T",
        price=200000.0,
        currency="UAH",
    )
    assert l_uah.price_usd == 4820.0  # 200000 * 0.0241

    r1 = await dedup.evaluate(l_uah, db_session)
    assert r1.action == "CREATED"

    l_usd = Listing(
        source="olx",
        source_id="curr_conv_1",
        url="https://olx.ua/curr_conv_1.html",
        title="Audi A6 C5 1.8T",
        price=4200.0,
        currency="USD",
    )
    assert l_usd.price_usd == 4200.0

    r2 = await dedup.evaluate(l_usd, db_session)
    assert r2.is_duplicate is True
    assert r2.is_price_drop is True
    assert r2.price_diff_usd == -620.0
    assert r2.action == "PRICE_DROPPED"


# =====================================================================
# 5. High-Throughput Stress Test: 1,000 Listings & 500 Duplicates
# =====================================================================

@pytest.mark.asyncio
async def test_stress_high_volume_and_collision_rate(db_session: AsyncSession):
    """
    Empirical stress test:
    1. Generate 1,000 distinct listings with varying parameters.
    2. Ingest all 1,000 into Deduplicator -> assert exactly 1,000 CREATED, 0 duplicates.
    3. Generate 500 duplicates across Level 1, 2, and 3.
    4. Assert 100% detection rate (0 false negatives).
    """
    dedup = Deduplicator()
    sources = ["auto_ria", "olx", "rst", "telegram", "instagram"]
    engines = ["1.8T", "2.4", "1.9TDI"]
    fuels = ["petrol", "gas_petrol", "diesel"]
    transmissions = ["manual", "automatic", "tiptronic"]
    drives = ["front", "quattro"]
    cities = ["Kyiv", "Lviv", "Odesa", "Dnipro", "Kharkiv", "Vinnytsia", "Zaporizhzhia"]

    listings = []

    for i in range(1000):
        src = sources[i % len(sources)]
        eng = engines[i % len(engines)]
        l = Listing(
            source=src,
            source_id=f"stress_{i}",
            url=f"https://{src}.example.com/listing_{i}.html",
            title=f"Audi A6 C5 {eng} #{i}",
            year=1997 + (i % 8),
            body_type="sedan" if i % 2 == 0 else "avant",
            price=3000.0 + (i * 5),
            currency="USD",
            mileage=150000 + (i * 350),
            engine=eng,
            engine_code=eng,
            fuel_type=fuels[i % len(fuels)],
            transmission=transmissions[i % len(transmissions)],
            drive_type=drives[i % len(drives)],
            location=cities[i % len(cities)],
            seller=f"Seller_{i}",
            seller_phone=f"+38050{1000000 + i}",
        )
        listings.append(l)

    # Ingest 1,000 listings
    for idx, l in enumerate(listings):
        res = await dedup.evaluate(l, db_session)
        assert res.is_duplicate is False, f"Listing {idx} incorrectly flagged as duplicate!"
        assert res.action == "CREATED"

    # Total in DB must be exactly 1000
    total_db = (await db_session.execute(select(func.count(ListingModel.id)))).scalar()
    assert total_db == 1000

    # Ingest 500 duplicates
    # Level 1: 200 duplicates (same source + id, with slight price drops or same price)
    for i in range(200):
        orig = listings[i]
        dup_l1 = orig.model_copy(update={"price": orig.price - 100.0, "price_usd": orig.price_usd - 100.0})
        res_l1 = await dedup.evaluate(dup_l1, db_session)
        assert res_l1.is_duplicate is True
        assert res_l1.matched_level == 1
        assert res_l1.action == "PRICE_DROPPED"

    # Level 2: 150 duplicates (different source_id, same canonical URL)
    for i in range(200, 350):
        orig = listings[i]
        dup_l2 = orig.model_copy(
            update={
                "source_id": f"alias_{i}",
                "url": orig.url + "?utm_source=social&ref=partner",
                "canonical_url": orig.canonical_url,
            }
        )
        res_l2 = await dedup.evaluate(dup_l2, db_session)
        assert res_l2.is_duplicate is True
        assert res_l2.matched_level == 2
        assert res_l2.action == "DUPLICATE_URL"

    # Level 3: 150 duplicates (cross-posted to different source, different source_id, different URL)
    for i in range(350, 500):
        orig = listings[i]
        cross_source = "olx" if orig.source != "olx" else "rst"
        dup_l3 = Listing(
            source=cross_source,
            source_id=f"crosspost_{i}",
            url=f"https://{cross_source}.example.com/crosspost_{i}.html",
            title=orig.title,
            year=orig.year,
            body_type=orig.body_type,
            price=orig.price,
            currency=orig.currency,
            mileage=orig.mileage,
            engine=orig.engine,
            engine_code=orig.engine_code,
            fuel_type=orig.fuel_type,
            transmission=orig.transmission,
            drive_type=orig.drive_type,
            location=orig.location,
            seller=orig.seller,
            seller_phone=orig.seller_phone,
        )
        assert dup_l3.content_fingerprint == orig.content_fingerprint
        res_l3 = await dedup.evaluate(dup_l3, db_session)
        assert res_l3.is_duplicate is True
        assert res_l3.matched_level == 3
        assert res_l3.action == "DUPLICATE_CONTENT"


# =====================================================================
# 6. Empirical Bug Reproductions (Proving Vulnerabilities & Edge Cases)
# =====================================================================

def test_reproduce_bug_empty_url_recursion_error():
    """
    REMEDIATION VERIFICATION:
    Creating a Listing with empty string or whitespace url cleanly raises
    a pydantic ValidationError (not RecursionError).
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Listing(
            source="auto_ria",
            source_id="rec_test_1",
            url="",
            title="Audi A6 C5",
        )
    with pytest.raises(ValidationError):
        Listing(
            source="auto_ria",
            source_id="rec_test_2",
            url="   ",
            title="Audi A6 C5",
        )


@pytest.mark.asyncio
async def test_reproduce_bug_null_price_transition_dropped(db_session: AsyncSession):
    """
    REMEDIATION VERIFICATION:
    When a listing is initially scraped without a price (price=None),
    a subsequent re-scrape with a valid price (e.g. 4500.0) updates the DB price
    and records a PRICE_SET version!
    """
    dedup = Deduplicator()

    # Initial scrape without price
    l1 = Listing(
        source="auto_ria",
        source_id="price_null_test",
        url="https://auto.ria.com/car/price_null_test.html",
        title="Audi A6 C5",
        price=None,
    )
    r1 = await dedup.evaluate(l1, db_session)
    assert r1.action == "CREATED"

    # Re-scrape with price added by seller
    l2 = Listing(
        source="auto_ria",
        source_id="price_null_test",
        url="https://auto.ria.com/car/price_null_test.html",
        title="Audi A6 C5",
        price=4500.0,
    )
    r2 = await dedup.evaluate(l2, db_session)

    assert r2.action == "PRICE_SET"
    stmt = select(ListingModel).where(ListingModel.source_listing_id == "price_null_test")
    db_item = (await db_session.execute(stmt)).scalar_one()
    assert db_item.price == Decimal("4500.00")
    assert db_item.price_usd == Decimal("4500.00")


def test_reproduce_bug_instagram_tracking_leak():
    """
    REMEDIATION VERIFICATION:
    Instagram share URLs with modern `igsh` parameter are stripped by compute_canonical_url.
    """
    raw_url = "https://instagram.com/p/DA12345/?utm_source=ig_web_copy_link&igsh=XYZ123"
    canonical = Listing.compute_canonical_url("instagram", raw_url, "DA12345")
    assert "igsh" not in canonical
    assert canonical == "https://www.instagram.com/p/DA12345"


def test_reproduce_bug_city_prefix_false_negative():
    """
    REMEDIATION VERIFICATION:
    City normalization handles Ukrainian prefixes 'м. ' or 'г. '.
    Two identical cars in Kyiv with 'м. Київ' and 'Київ' produce identical fingerprints at Level 3.
    """
    l1 = Listing(
        source="auto_ria",
        source_id="1",
        url="https://auto.ria.com/1.html",
        title="Audi A6",
        location="Київ",
        year=2001,
        mileage=200000,
    )
    l2 = Listing(
        source="olx",
        source_id="2",
        url="https://olx.ua/2.html",
        title="Audi A6",
        location="м. Київ",
        year=2001,
        mileage=200000,
    )
    assert l1.content_fingerprint == l2.content_fingerprint
