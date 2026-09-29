"""
Unit tests for 3-level Deduplicator service and price version tracking.
"""

from __future__ import annotations

from decimal import Decimal
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import ListingModel, ListingVersionModel
from src.models.listing import Listing
from src.services.deduplicator import Deduplicator


@pytest.mark.asyncio
async def test_dedup_brand_new_listing(db_session: AsyncSession, sample_listing: Listing):
    dedup = Deduplicator()
    res = await dedup.evaluate(sample_listing, db_session)

    assert res.is_duplicate is False
    assert res.matched_level is None
    assert res.action == "CREATED"
    assert res.listing_id is not None
    assert res.is_price_drop is False

    # Check listing in database
    stmt = select(ListingModel).where(ListingModel.id == res.listing_id)
    db_item = (await db_session.execute(stmt)).scalar_one()
    assert db_item.status == "NEW"
    assert db_item.dedup_level == 0
    assert db_item.is_sent_to_telegram is False

    # Check initial version created
    v_stmt = select(ListingVersionModel).where(ListingVersionModel.listing_id == res.listing_id)
    versions = (await db_session.execute(v_stmt)).scalars().all()
    assert len(versions) == 1
    assert versions[0].change_type == "INITIAL"
    assert versions[0].price_usd == Decimal("4800.00")


@pytest.mark.asyncio
async def test_dedup_level_1_unchanged(db_session: AsyncSession, sample_listing: Listing):
    dedup = Deduplicator()
    # First ingestion
    first_res = await dedup.evaluate(sample_listing, db_session)
    orig_id = first_res.listing_id

    # Second ingestion with same attributes
    second_res = await dedup.evaluate(sample_listing, db_session)
    assert second_res.is_duplicate is True
    assert second_res.matched_level == 1
    assert second_res.existing_listing_id == orig_id
    assert second_res.action == "UNCHANGED"
    assert second_res.is_price_drop is False


@pytest.mark.asyncio
async def test_dedup_level_1_price_drop_and_increase(
    db_session: AsyncSession, sample_listing: Listing
):
    dedup = Deduplicator()
    # Initial price: 4800
    res1 = await dedup.evaluate(sample_listing, db_session)
    orig_id = res1.listing_id

    # Re-scrape with price drop: 4200 (-600)
    drop_listing = sample_listing.model_copy(update={"price": 4200.0, "price_usd": 4200.0})
    res_drop = await dedup.evaluate(drop_listing, db_session)

    assert res_drop.is_duplicate is True
    assert res_drop.matched_level == 1
    assert res_drop.is_price_drop is True
    assert res_drop.price_diff_usd == -600.0
    assert res_drop.action == "PRICE_DROPPED"

    # Verify listing updated in DB
    stmt = select(ListingModel).where(ListingModel.id == orig_id)
    item = (await db_session.execute(stmt)).scalar_one()
    assert item.price_usd == Decimal("4200.00")

    # Re-scrape with price increase: 4500 (+300)
    inc_listing = sample_listing.model_copy(update={"price": 4500.0, "price_usd": 4500.0})
    res_inc = await dedup.evaluate(inc_listing, db_session)

    assert res_inc.is_duplicate is True
    assert res_inc.matched_level == 1
    assert res_inc.is_price_drop is False
    assert res_inc.price_diff_usd == 300.0
    assert res_inc.action == "UPDATED"

    # Check all versions recorded (INITIAL, PRICE_DROP, PRICE_INCREASE)
    v_stmt = (
        select(ListingVersionModel)
        .where(ListingVersionModel.listing_id == orig_id)
        .order_by(ListingVersionModel.id)
    )
    versions = (await db_session.execute(v_stmt)).scalars().all()
    assert len(versions) == 3
    assert versions[0].change_type == "INITIAL"
    assert versions[1].change_type == "PRICE_DROP"
    assert versions[1].price_diff_usd == Decimal("-600.00")
    assert versions[2].change_type == "PRICE_INCREASE"
    assert versions[2].price_diff_usd == Decimal("300.00")


@pytest.mark.asyncio
async def test_dedup_level_2_canonical_url(db_session: AsyncSession, sample_listing: Listing):
    dedup = Deduplicator()
    res1 = await dedup.evaluate(sample_listing, db_session)
    orig_id = res1.listing_id

    # Same canonical URL, but different source_id or query string variant
    url_variant = sample_listing.model_copy(
        update={
            "source_id": "different_session_id_999",
            "url": sample_listing.url + "&session_token=xyz123",
            "canonical_url": sample_listing.canonical_url,
        }
    )

    res2 = await dedup.evaluate(url_variant, db_session)
    assert res2.is_duplicate is True
    assert res2.matched_level == 2
    assert res2.existing_listing_id == orig_id
    assert res2.action == "DUPLICATE_URL"

    # Verify duplicate record was created in DB and linked
    stmt = select(ListingModel).where(ListingModel.id == res2.listing_id)
    dup_item = (await db_session.execute(stmt)).scalar_one()
    assert dup_item.status == "DUPLICATE"
    assert dup_item.duplicate_of_id == orig_id
    assert dup_item.dedup_level == 2


@pytest.mark.asyncio
async def test_dedup_level_3_content_fingerprint(
    db_session: AsyncSession, sample_listing: Listing
):
    dedup = Deduplicator()
    res1 = await dedup.evaluate(sample_listing, db_session)
    orig_id = res1.listing_id

    # Cross-posted to OLX: different source, different source_id, different URL, but identical vehicle fingerprint
    cross_posted = Listing(
        source="olx",
        source_id="olx_8829104",
        url="https://www.olx.ua/d/obyavlenie/audi-a6-c5-1-8t-ID8829104.html",
        title="Audi A6 C5 1.8 Turbo 2001",
        year=sample_listing.year,
        body_type=sample_listing.body_type,
        price=sample_listing.price,
        currency=sample_listing.currency,
        mileage=sample_listing.mileage,
        engine=sample_listing.engine,
        engine_code=sample_listing.engine_code,
        fuel_type=sample_listing.fuel_type,
        transmission=sample_listing.transmission,
        drive_type=sample_listing.drive_type,
        location=sample_listing.location,
        seller=sample_listing.seller,
        seller_phone=sample_listing.seller_phone,
    )

    assert cross_posted.content_fingerprint == sample_listing.content_fingerprint

    res_cross = await dedup.evaluate(cross_posted, db_session)
    assert res_cross.is_duplicate is True
    assert res_cross.matched_level == 3
    assert res_cross.existing_listing_id == orig_id
    assert res_cross.action == "DUPLICATE_CONTENT"

    # Verify duplicate record in DB
    stmt = select(ListingModel).where(ListingModel.id == res_cross.listing_id)
    dup_item = (await db_session.execute(stmt)).scalar_one()
    assert dup_item.status == "DUPLICATE"
    assert dup_item.duplicate_of_id == orig_id
    assert dup_item.dedup_level == 3


@pytest.mark.asyncio
async def test_dedup_distinct_car_is_not_duplicate(
    db_session: AsyncSession, sample_listing: Listing
):
    dedup = Deduplicator()
    await dedup.evaluate(sample_listing, db_session)

    # Completely different car: 2.4 petrol Avant from Lviv
    different_car = Listing(
        source="rst",
        source_id="rst_991122",
        url="https://rst.ua/oldcars/audi/a6/audi_a6_991122.html",
        title="Audi A6 C5 2.4 Avant 2003",
        year=2003,
        body_type="avant",
        price=5500.0,
        currency="USD",
        mileage=310000,
        engine="2.4",
        engine_code="2.4",
        fuel_type="gas_petrol",
        transmission="automatic",
        location="Львів",
        seller="Петро",
        seller_phone="+380671234567",
    )

    res = await dedup.evaluate(different_car, db_session)
    assert res.is_duplicate is False
    assert res.matched_level is None
    assert res.action == "CREATED"


@pytest.mark.asyncio
async def test_dedup_persist_false(db_session: AsyncSession, sample_listing: Listing):
    dedup = Deduplicator()
    res = await dedup.evaluate(sample_listing, db_session, persist=False)
    assert res.is_duplicate is False
    assert res.listing_id is None

    # Verify no row was inserted
    stmt = select(ListingModel).where(ListingModel.source_listing_id == sample_listing.source_id)
    found = (await db_session.execute(stmt)).scalar_one_or_none()
    assert found is None


@pytest.mark.asyncio
async def test_dedup_auto_provisions_unseeded_source(db_session: AsyncSession):
    dedup = Deduplicator()
    custom_listing = Listing(
        source="custom_auto_channel",
        source_id="chan_991",
        url="https://custom.com/991.html",
        title="Audi A6 C5 1.8T",
        price=4000.0,
    )
    res = await dedup.evaluate(custom_listing, db_session, persist=True)
    assert res.is_duplicate is False
    assert res.action == "CREATED"

    # Verify custom source was created in sources table
    from src.database.models import SourceModel
    s_stmt = select(SourceModel).where(SourceModel.id == "custom_auto_channel")
    src_obj = (await db_session.execute(s_stmt)).scalar_one_or_none()
    assert src_obj is not None
    assert src_obj.id == "custom_auto_channel"


@pytest.mark.asyncio
async def test_dedup_price_change_below_threshold(
    db_session: AsyncSession, sample_listing: Listing
):
    # Threshold set to $10.0
    dedup = Deduplicator(price_drop_threshold_usd=10.0)
    await dedup.evaluate(sample_listing, db_session)

    # Minor price change ($4800 -> $4795, diff = -$5, below $10 threshold)
    minor_drop = sample_listing.model_copy(update={"price": 4795.0, "price_usd": 4795.0})
    res_minor = await dedup.evaluate(minor_drop, db_session)

    assert res_minor.is_duplicate is True
    assert res_minor.action == "UNCHANGED"
    assert res_minor.is_price_drop is False


@pytest.mark.asyncio
async def test_dedup_duplicate_chaining_always_points_to_root_level_2(
    db_session: AsyncSession, sample_listing: Listing
):
    """
    Ensure duplicate chaining at Level 2 always points directly to the primary root listing,
    even across multi-hop duplicate chains (A -> B -> C -> D).
    """
    dedup = Deduplicator()
    # 1. Root listing A
    res_a = await dedup.evaluate(sample_listing, db_session)
    root_id = res_a.listing_id

    # 2. Duplicate B
    dup_b = sample_listing.model_copy(
        update={
            "source_id": "alias_b",
            "url": sample_listing.url + "?ref=partner_b",
            "canonical_url": sample_listing.canonical_url,
        }
    )
    res_b = await dedup.evaluate(dup_b, db_session)
    assert res_b.is_duplicate is True
    assert res_b.matched_level == 2
    assert res_b.existing_listing_id == root_id

    stmt_b = select(ListingModel).where(ListingModel.id == res_b.listing_id)
    item_b = (await db_session.execute(stmt_b)).scalar_one()
    assert item_b.duplicate_of_id == root_id

    # 3. Duplicate C
    dup_c = sample_listing.model_copy(
        update={
            "source_id": "alias_c",
            "url": sample_listing.url + "?ref=partner_c",
            "canonical_url": sample_listing.canonical_url,
        }
    )
    res_c = await dedup.evaluate(dup_c, db_session)
    assert res_c.is_duplicate is True
    assert res_c.matched_level == 2
    assert res_c.existing_listing_id == root_id

    stmt_c = select(ListingModel).where(ListingModel.id == res_c.listing_id)
    item_c = (await db_session.execute(stmt_c)).scalar_one()
    # Must point to root_id (A), NOT item_b.id!
    assert item_c.duplicate_of_id == root_id


@pytest.mark.asyncio
async def test_dedup_duplicate_chaining_always_points_to_root_level_3(
    db_session: AsyncSession, sample_listing: Listing
):
    """
    Ensure duplicate chaining at Level 3 always points directly to the primary root listing,
    even across multi-hop cross-posts (A -> B -> C).
    """
    dedup = Deduplicator()
    res_a = await dedup.evaluate(sample_listing, db_session)
    root_id = res_a.listing_id

    # Duplicate B on OLX
    cross_b = sample_listing.model_copy(
        update={
            "source": "olx",
            "source_id": "olx_cross_b",
            "url": "https://www.olx.ua/d/obyavlenie/audi-b.html",
            "canonical_url": "https://www.olx.ua/d/obyavlenie/audi-b.html",
        }
    )
    res_b = await dedup.evaluate(cross_b, db_session)
    assert res_b.matched_level == 3
    assert res_b.existing_listing_id == root_id

    stmt_b = select(ListingModel).where(ListingModel.id == res_b.listing_id)
    item_b = (await db_session.execute(stmt_b)).scalar_one()
    assert item_b.duplicate_of_id == root_id

    # Duplicate C on RST
    cross_c = sample_listing.model_copy(
        update={
            "source": "rst",
            "source_id": "rst_cross_c",
            "url": "https://rst.ua/oldcars/audi/a6/c.html",
            "canonical_url": "https://rst.ua/oldcars/audi/a6/c.html",
        }
    )
    res_c = await dedup.evaluate(cross_c, db_session)
    assert res_c.matched_level == 3
    assert res_c.existing_listing_id == root_id

    stmt_c = select(ListingModel).where(ListingModel.id == res_c.listing_id)
    item_c = (await db_session.execute(stmt_c)).scalar_one()
    # Must point to root_id (A), NOT item_b.id!
    assert item_c.duplicate_of_id == root_id


@pytest.mark.asyncio
async def test_dedup_price_discovery_none_to_float_transition(db_session: AsyncSession):
    """
    Ensure that a listing scraped with price=None and subsequently updated with a valid price
    records a PRICE_SET version and updates existing record in DB.
    """
    dedup = Deduplicator()
    l1 = Listing(
        source="auto_ria",
        source_id="disc_1",
        url="https://auto.ria.com/car/disc_1.html",
        title="Audi A6 Discovery Test",
        price=None,
    )
    r1 = await dedup.evaluate(l1, db_session)
    assert r1.action == "CREATED"
    listing_id = r1.listing_id

    l2 = Listing(
        source="auto_ria",
        source_id="disc_1",
        url="https://auto.ria.com/car/disc_1.html",
        title="Audi A6 Discovery Test",
        price=4500.0,
    )
    r2 = await dedup.evaluate(l2, db_session)
    assert r2.is_duplicate is True
    assert r2.matched_level == 1
    assert r2.action == "PRICE_SET"

    stmt = select(ListingModel).where(ListingModel.id == listing_id)
    db_item = (await db_session.execute(stmt)).scalar_one()
    assert db_item.price == Decimal("4500.00")
    assert db_item.price_usd == Decimal("4500.00")

    # Check that ListingVersionModel was created with change_type="PRICE_SET"
    stmt_v = select(ListingVersionModel).where(
        ListingVersionModel.listing_id == listing_id,
        ListingVersionModel.change_type == "PRICE_SET",
    )
    version = (await db_session.execute(stmt_v)).scalar_one_or_none()
    assert version is not None
    assert version.price == Decimal("4500.00")
    assert version.price_usd == Decimal("4500.00")

