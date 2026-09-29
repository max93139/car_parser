"""
Unit tests for database layer: DDL, connection engine, models, constraints, and cascade.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    ListingModel,
    ListingVersionModel,
    ParserRunModel,
    SourceModel,
)


@pytest.mark.asyncio
async def test_sources_seeded(db_session: AsyncSession):
    stmt = select(SourceModel)
    sources = (await db_session.execute(stmt)).scalars().all()
    source_ids = {s.id for s in sources}
    assert "auto_ria" in source_ids
    assert "olx" in source_ids
    assert "rst" in source_ids
    assert "telegram" in source_ids
    assert "instagram" in source_ids


@pytest.mark.asyncio
async def test_listing_crud(db_session: AsyncSession):
    # Create
    listing = ListingModel(
        source_id="auto_ria",
        source_listing_id="test_101",
        url="https://auto.ria.com/car/101.html",
        canonical_url="https://auto.ria.com/car/101.html",
        title="Audi A6 C5 1.8T",
        brand="Audi",
        model="A6",
        generation="C5",
        year=2001,
        body_type="sedan",
        price=Decimal("4500.00"),
        currency="USD",
        price_usd=Decimal("4500.00"),
        mileage=280000,
        engine="1.8T",
        engine_code="1.8T",
        fuel_type="petrol",
        transmission="manual",
        drive_type="front",
        location="Kyiv",
        seller="Ivan",
        seller_phone="+380501112233",
        images=["https://img.com/1.jpg"],
        content_fingerprint="a" * 64,
        fuzzy_fingerprint="b" * 64,
        status="NEW",
        is_sent_to_telegram=False,
    )
    db_session.add(listing)
    await db_session.flush()

    assert listing.id is not None

    # Read
    stmt = select(ListingModel).where(ListingModel.id == listing.id)
    fetched = (await db_session.execute(stmt)).scalar_one()
    assert fetched.source_listing_id == "test_101"
    assert fetched.price_usd == Decimal("4500.00")
    assert fetched.is_sent_to_telegram is False

    # Update
    fetched.is_sent_to_telegram = True
    fetched.telegram_message_id = 998877
    await db_session.flush()

    refetched = (await db_session.execute(stmt)).scalar_one()
    assert refetched.is_sent_to_telegram is True
    assert refetched.telegram_message_id == 998877


@pytest.mark.asyncio
async def test_listing_versions_and_cascade(db_session: AsyncSession):
    listing = ListingModel(
        source_id="olx",
        source_listing_id="olx_202",
        url="https://olx.ua/202.html",
        canonical_url="https://olx.ua/202.html",
        title="Audi A6 2.4",
        price=Decimal("5200.00"),
        currency="USD",
        price_usd=Decimal("5200.00"),
        content_fingerprint="c" * 64,
        fuzzy_fingerprint="d" * 64,
    )
    db_session.add(listing)
    await db_session.flush()

    # Add historical version
    version = ListingVersionModel(
        listing_id=listing.id,
        price=Decimal("5000.00"),
        currency="USD",
        price_usd=Decimal("5000.00"),
        price_diff_usd=Decimal("-200.00"),
        change_type="PRICE_DROP",
        change_summary={"old": 5200.0, "new": 5000.0},
    )
    db_session.add(version)
    await db_session.flush()

    # Query relationship
    stmt = select(ListingModel).where(ListingModel.id == listing.id)
    item = (await db_session.execute(stmt)).scalar_one()
    assert len(item.versions) == 1
    assert item.versions[0].change_type == "PRICE_DROP"

    # Delete listing and check cascade delete on versions
    await db_session.delete(item)
    await db_session.flush()

    v_stmt = select(ListingVersionModel).where(ListingVersionModel.listing_id == listing.id)
    versions_after = (await db_session.execute(v_stmt)).scalars().all()
    assert len(versions_after) == 0


@pytest.mark.asyncio
async def test_parser_run_telemetry(db_session: AsyncSession):
    run = ParserRunModel(
        source_id="rst",
        status="SUCCESS",
        items_scanned=20,
        items_matched_filter=15,
        items_new=3,
        items_updated=1,
        items_duplicates=11,
        items_errors=0,
        execution_metadata={"duration_ms": 3200},
    )
    db_session.add(run)
    await db_session.flush()

    assert run.id is not None
    assert isinstance(run.run_id, uuid.UUID)

    stmt = select(ParserRunModel).where(ParserRunModel.id == run.id)
    fetched = (await db_session.execute(stmt)).scalar_one()
    assert fetched.source_id == "rst"
    assert fetched.items_new == 3
    assert fetched.source.name == "RST.ua"


@pytest.mark.asyncio
async def test_unique_constraint_source_and_id(db_session: AsyncSession):
    l1 = ListingModel(
        source_id="auto_ria",
        source_listing_id="dup_001",
        url="https://auto.ria.com/dup1.html",
        canonical_url="https://auto.ria.com/dup1.html",
        title="Ad 1",
        content_fingerprint="1" * 64,
        fuzzy_fingerprint="1" * 64,
    )
    db_session.add(l1)
    await db_session.flush()

    # Second listing with exact same (source_id, source_listing_id)
    l2 = ListingModel(
        source_id="auto_ria",
        source_listing_id="dup_001",
        url="https://auto.ria.com/dup2.html",
        canonical_url="https://auto.ria.com/dup2.html",
        title="Ad 2",
        content_fingerprint="2" * 64,
        fuzzy_fingerprint="2" * 64,
    )
    db_session.add(l2)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_year_check_constraint(db_session: AsyncSession):
    # Year outside valid range 1990-2015
    invalid_listing = ListingModel(
        source_id="auto_ria",
        source_listing_id="chk_year_001",
        url="https://auto.ria.com/chk.html",
        canonical_url="https://auto.ria.com/chk.html",
        title="Old car",
        year=1980,  # Fails CheckConstraint
        content_fingerprint="y" * 64,
        fuzzy_fingerprint="y" * 64,
    )
    db_session.add(invalid_listing)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_mileage_check_constraint(db_session: AsyncSession):
    # Mileage negative (< 0)
    invalid_listing = ListingModel(
        source_id="auto_ria",
        source_listing_id="chk_mil_001",
        url="https://auto.ria.com/mil.html",
        canonical_url="https://auto.ria.com/mil.html",
        title="Bad mileage",
        mileage=-500,
        content_fingerprint="m" * 64,
        fuzzy_fingerprint="m" * 64,
    )
    db_session.add(invalid_listing)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_get_db_session_context_manager(test_engine):
    from src.database.connection import get_db_session

    # Successful transaction
    async with get_db_session(engine=test_engine) as session:
        src = SourceModel(
            id="test_ctx_src",
            name="Context Source",
            base_url="https://test.com",
            source_type="TEST",
        )
        session.add(src)

    # Verify persisted
    async with get_db_session(engine=test_engine) as session:
        stmt = select(SourceModel).where(SourceModel.id == "test_ctx_src")
        res = (await session.execute(stmt)).scalar_one_or_none()
        assert res is not None

    # Rollback on exception
    with pytest.raises(RuntimeError):
        async with get_db_session(engine=test_engine) as session:
            src2 = SourceModel(
                id="test_ctx_rollback",
                name="Rollback Source",
                base_url="https://rollback.com",
                source_type="TEST",
            )
            session.add(src2)
            raise RuntimeError("Forced abort")

    # Verify NOT persisted
    async with get_db_session(engine=test_engine) as session:
        stmt = select(SourceModel).where(SourceModel.id == "test_ctx_rollback")
        res = (await session.execute(stmt)).scalar_one_or_none()
        assert res is None
