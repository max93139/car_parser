"""
3-Level Deduplication Service for Audi A6 C5 Monitoring Service.

Hierarchy:
  Level 1: Exact Source Identity (source_id, source_listing_id)
  Level 2: Normalized Canonical URL
  Level 3: Invariant Content Fingerprint (SHA-256)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import ListingModel, ListingVersionModel, SourceModel
from src.models.listing import Listing

logger = logging.getLogger(__name__)


class DeduplicationResult(BaseModel):
    """
    Result of evaluating a listing through the 3-level deduplication hierarchy.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    is_duplicate: bool = Field(..., description="True if listing matched existing record at any level")
    matched_level: Optional[int] = Field(
        None, description="1 = (source, source_id), 2 = canonical_url, 3 = content_fingerprint"
    )
    existing_listing_id: Optional[int] = Field(None, description="ID of primary existing listing matched")
    listing_id: Optional[int] = Field(None, description="ID of the inserted or updated DB listing")
    is_price_drop: bool = Field(default=False, description="True if price decreased compared to previous record")
    price_diff_usd: Optional[float] = Field(default=None, description="Price difference in USD (negative = drop)")
    action: str = Field(
        default="CREATED",
        description="CREATED, UPDATED, PRICE_DROPPED, PRICE_SET, DUPLICATE_URL, DUPLICATE_CONTENT, or UNCHANGED",
    )


class Deduplicator:
    """
    Evaluates listings against persistent storage to eliminate duplicate alerts and track price drops.
    """

    def __init__(self, price_drop_threshold_usd: float = 1.0) -> None:
        self.price_drop_threshold_usd = price_drop_threshold_usd

    async def _ensure_source_exists(self, session: AsyncSession, source_id: str) -> None:
        """Auto-provisions source in sources table if not already present."""
        stmt = select(SourceModel.id).where(SourceModel.id == source_id)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if not existing:
            try:
                async with session.begin_nested():
                    new_source = SourceModel(
                        id=source_id,
                        name=source_id.replace("_", " ").title(),
                        base_url=f"https://{source_id}.com",
                        source_type="WEB_SCRAPER",
                        is_active=True,
                        rate_limit_per_min=30,
                        scrape_interval_minutes=15,
                        config={},
                    )
                    session.add(new_source)
                    await session.flush()
            except IntegrityError:
                # Concurrent transaction inserted it already; re-verify
                existing = (await session.execute(stmt)).scalars().first()

    async def evaluate(
        self,
        listing: Listing,
        session: AsyncSession,
        persist: bool = True,
    ) -> DeduplicationResult:
        """
        Executes the 3-level deduplication hierarchy.

        Args:
            listing: Strongly-typed Pydantic Listing model.
            session: Active SQLAlchemy AsyncSession.
            persist: Whether to commit changes or flush to DB.

        Returns:
            DeduplicationResult detailing duplicate status, matched tier, and price drop metrics.
        """
        # Ensure source exists in sources table for foreign key constraint
        if persist:
            await self._ensure_source_exists(session, listing.source)

        now = datetime.now(timezone.utc)

        # -------------------------------------------------------------
        # LEVEL 1: Exact Source + Source Listing ID Match
        # -------------------------------------------------------------
        stmt_lvl1 = select(ListingModel).where(
            ListingModel.source_id == listing.source,
            ListingModel.source_listing_id == listing.source_id,
        )
        existing_lvl1 = (await session.execute(stmt_lvl1)).scalar_one_or_none()

        if existing_lvl1 is not None:
            existing_lvl1.last_checked_at = now
            listing_id = existing_lvl1.id

            # Price comparison
            old_price_usd = (
                float(existing_lvl1.price_usd)
                if existing_lvl1.price_usd is not None
                else (float(existing_lvl1.price) if existing_lvl1.price is not None else None)
            )
            new_price_usd = (
                float(listing.price_usd)
                if listing.price_usd is not None
                else (float(listing.price) if listing.price is not None else None)
            )

            if old_price_usd is None and new_price_usd is not None:
                if persist:
                    if listing.price is not None:
                        existing_lvl1.price = Decimal(str(listing.price))
                    else:
                        existing_lvl1.price = Decimal(str(new_price_usd))
                    existing_lvl1.currency = listing.currency or "USD"
                    existing_lvl1.price_usd = Decimal(str(new_price_usd))

                    version = ListingVersionModel(
                        listing_id=existing_lvl1.id,
                        price=existing_lvl1.price,
                        currency=existing_lvl1.currency,
                        price_usd=existing_lvl1.price_usd,
                        price_diff_usd=Decimal("0.0"),
                        mileage=listing.mileage,
                        title=listing.title,
                        description=listing.description,
                        change_type="PRICE_SET",
                        change_summary={
                            "old_price_usd": None,
                            "new_price_usd": new_price_usd,
                            "diff_usd": 0.0,
                        },
                        detected_at=now,
                    )
                    session.add(version)
                    await session.flush()

                return DeduplicationResult(
                    is_duplicate=True,
                    matched_level=1,
                    existing_listing_id=listing_id,
                    listing_id=listing_id,
                    is_price_drop=False,
                    price_diff_usd=0.0,
                    action="PRICE_SET",
                )

            elif old_price_usd is not None and new_price_usd is not None:
                diff = round(new_price_usd - old_price_usd, 2)
                # Check if price changed beyond threshold
                if abs(diff) >= self.price_drop_threshold_usd:
                    is_drop = diff < 0
                    change_type = "PRICE_DROP" if is_drop else "PRICE_INCREASE"

                    if persist:
                        # Record historical point-in-time snapshot
                        version = ListingVersionModel(
                            listing_id=existing_lvl1.id,
                            price=Decimal(str(listing.price or 0.0)),
                            currency=listing.currency or "USD",
                            price_usd=Decimal(str(new_price_usd)),
                            price_diff_usd=Decimal(str(diff)),
                            mileage=listing.mileage,
                            title=listing.title,
                            description=listing.description,
                            change_type=change_type,
                            change_summary={
                                "old_price_usd": old_price_usd,
                                "new_price_usd": new_price_usd,
                                "diff_usd": diff,
                            },
                            detected_at=now,
                        )
                        session.add(version)

                        # Update existing listing current price
                        if listing.price is not None:
                            existing_lvl1.price = Decimal(str(listing.price))
                        existing_lvl1.currency = listing.currency
                        existing_lvl1.price_usd = Decimal(str(new_price_usd))
                        await session.flush()

                    action = "PRICE_DROPPED" if is_drop else "UPDATED"
                    return DeduplicationResult(
                        is_duplicate=True,
                        matched_level=1,
                        existing_listing_id=listing_id,
                        listing_id=listing_id,
                        is_price_drop=is_drop,
                        price_diff_usd=diff,
                        action=action,
                    )

            if persist:
                await session.flush()

            return DeduplicationResult(
                is_duplicate=True,
                matched_level=1,
                existing_listing_id=listing_id,
                listing_id=listing_id,
                is_price_drop=False,
                price_diff_usd=0.0,
                action="UNCHANGED",
            )

        # -------------------------------------------------------------
        # LEVEL 2: Normalized Canonical URL Match
        # -------------------------------------------------------------
        canonical_target = listing.canonical_url or listing.url
        stmt_lvl2 = (
            select(ListingModel)
            .where(ListingModel.canonical_url == canonical_target)
            .order_by(ListingModel.id.asc())
        )
        existing_lvl2 = (await session.execute(stmt_lvl2)).scalars().first()

        if existing_lvl2 is not None:
            root_id = existing_lvl2.duplicate_of_id or existing_lvl2.id
            new_db_id = None
            if persist:
                db_listing = self._build_listing_model(listing)
                db_listing.status = "DUPLICATE"
                db_listing.duplicate_of_id = root_id
                db_listing.dedup_level = 2
                db_listing.is_sent_to_telegram = False
                session.add(db_listing)
                await session.flush()
                new_db_id = db_listing.id

            return DeduplicationResult(
                is_duplicate=True,
                matched_level=2,
                existing_listing_id=root_id,
                listing_id=new_db_id,
                is_price_drop=False,
                price_diff_usd=None,
                action="DUPLICATE_URL",
            )

        # -------------------------------------------------------------
        # LEVEL 3: Invariant Content Fingerprint (SHA-256)
        # -------------------------------------------------------------
        stmt_lvl3 = (
            select(ListingModel)
            .where(ListingModel.content_fingerprint == listing.content_fingerprint)
            .order_by(ListingModel.id.asc())
        )
        existing_lvl3 = (await session.execute(stmt_lvl3)).scalars().first()

        if existing_lvl3 is not None:
            root_id = existing_lvl3.duplicate_of_id or existing_lvl3.id
            new_db_id = None
            if persist:
                db_listing = self._build_listing_model(listing)
                db_listing.status = "DUPLICATE"
                db_listing.duplicate_of_id = root_id
                db_listing.dedup_level = 3
                db_listing.is_sent_to_telegram = False
                session.add(db_listing)
                await session.flush()
                new_db_id = db_listing.id

            return DeduplicationResult(
                is_duplicate=True,
                matched_level=3,
                existing_listing_id=root_id,
                listing_id=new_db_id,
                is_price_drop=False,
                price_diff_usd=None,
                action="DUPLICATE_CONTENT",
            )

        # -------------------------------------------------------------
        # BRAND NEW LISTING: Level 0 (No Duplicates)
        # -------------------------------------------------------------
        new_db_id = None
        if persist:
            db_listing = self._build_listing_model(listing)
            db_listing.status = listing.status or "NEW"
            db_listing.dedup_level = 0
            db_listing.is_sent_to_telegram = False
            session.add(db_listing)
            await session.flush()
            new_db_id = db_listing.id

            # Record initial version
            init_price = Decimal(str(listing.price)) if listing.price is not None else Decimal("0.0")
            init_price_usd = (
                Decimal(str(listing.price_usd)) if listing.price_usd is not None else Decimal("0.0")
            )
            init_version = ListingVersionModel(
                listing_id=db_listing.id,
                price=init_price,
                currency=listing.currency or "USD",
                price_usd=init_price_usd,
                price_diff_usd=Decimal("0.0"),
                mileage=listing.mileage,
                title=listing.title,
                description=listing.description,
                change_type="INITIAL",
                change_summary={"initial_price_usd": float(init_price_usd)},
                detected_at=now,
            )
            session.add(init_version)
            await session.flush()

        return DeduplicationResult(
            is_duplicate=False,
            matched_level=None,
            existing_listing_id=None,
            listing_id=new_db_id,
            is_price_drop=False,
            price_diff_usd=None,
            action="CREATED",
        )

    def _build_listing_model(self, listing: Listing) -> ListingModel:
        """Converts Pydantic Listing model into SQLAlchemy ListingModel."""
        price_dec = Decimal(str(listing.price)) if listing.price is not None else None
        price_usd_dec = Decimal(str(listing.price_usd)) if listing.price_usd is not None else None

        return ListingModel(
            source_id=listing.source,
            source_listing_id=listing.source_id,
            url=listing.url,
            canonical_url=listing.canonical_url or listing.url,
            title=listing.title,
            description=listing.description,
            brand=listing.brand,
            model=listing.model,
            generation=listing.generation,
            year=listing.year,
            body_type=listing.body_type or "unknown",
            price=price_dec,
            currency=listing.currency,
            price_usd=price_usd_dec,
            mileage=listing.mileage,
            engine=listing.engine,
            engine_code=listing.engine_code,
            fuel_type=listing.fuel_type or "other",
            transmission=listing.transmission or "unknown",
            drive_type=listing.drive_type or "unknown",
            location=listing.location,
            location_city=listing.location_city,
            location_region=listing.location_region,
            seller=listing.seller,
            seller_phone=listing.seller_phone,
            images=listing.images,
            content_fingerprint=listing.content_fingerprint or "",
            fuzzy_fingerprint=listing.fuzzy_fingerprint or "",
            status=listing.status,
            published_at=listing.published_at,
            first_seen_at=listing.first_seen_at,
            last_checked_at=listing.last_checked_at,
            raw_data=listing.raw_data,
        )
