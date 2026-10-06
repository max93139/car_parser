"""
SQLAlchemy 2.0 Declarative ORM models for Audi A6 C5 Monitoring Service.
Supports both PostgreSQL (asyncpg) and SQLite (aiosqlite).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

# Cross-dialect JSON type: uses JSONB on PostgreSQL, JSON on SQLite
JSONType = JSON().with_variant(JSONB, "postgresql")

# BigInteger compatible with SQLite autoincrement
AutoBigInteger = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class SourceModel(Base):
    """
    Registry of scraper sources and operational configs.
    """
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    scrape_interval_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    listings: Mapped[List[ListingModel]] = relationship(
        "ListingModel",
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    parser_runs: Mapped[List[ParserRunModel]] = relationship(
        "ParserRunModel",
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ListingModel(Base):
    """
    Core master table storing the current state of car listings.
    """
    __tablename__ = "listings"

    __table_args__ = (
        UniqueConstraint("source_id", "source_listing_id", name="uq_listings_source_id_listing_id"),
        CheckConstraint("year >= 1990 AND year <= 2030", name="chk_listings_year"),
        CheckConstraint("mileage >= 0", name="chk_listings_mileage"),
        Index("idx_listings_canonical_url", "canonical_url"),
        Index("idx_listings_content_fingerprint", "content_fingerprint"),
        Index("idx_listings_fuzzy_fingerprint", "fuzzy_fingerprint"),
        Index("idx_listings_tg_pending", "first_seen_at"),
    )

    id: Mapped[int] = mapped_column(AutoBigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_listing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    brand: Mapped[str] = mapped_column(String(50), default="Audi", nullable=False)
    model: Mapped[str] = mapped_column(String(50), default="A6", nullable=False)
    generation: Mapped[str] = mapped_column(String(20), default="C5", nullable=False)
    year: Mapped[Optional[int]] = mapped_column(SmallInteger)
    body_type: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)

    # Pricing
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    currency: Mapped[Optional[str]] = mapped_column(String(8), default="USD")
    price_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))

    # Technical specifications
    mileage: Mapped[Optional[int]] = mapped_column(Integer)
    engine: Mapped[Optional[str]] = mapped_column(String(100))
    engine_code: Mapped[Optional[str]] = mapped_column(String(32))
    engine_volume: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 1))
    fuel_type: Mapped[str] = mapped_column(String(32), default="other", nullable=False)
    transmission: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    drive_type: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)

    # Location & Contact
    location: Mapped[Optional[str]] = mapped_column(String(128))
    location_city: Mapped[Optional[str]] = mapped_column(String(64))
    location_region: Mapped[Optional[str]] = mapped_column(String(64))
    seller: Mapped[Optional[str]] = mapped_column(String(128))
    seller_phone: Mapped[Optional[str]] = mapped_column(String(32))

    # Media
    images: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)

    # Deduplication & Status
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    fuzzy_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="NEW", nullable=False)
    duplicate_of_id: Mapped[Optional[int]] = mapped_column(
        AutoBigInteger, ForeignKey("listings.id", ondelete="SET NULL")
    )
    dedup_level: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)

    # Telegram notification
    is_sent_to_telegram: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    telegram_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Timestamps
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    # Raw audit payload
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    # Relationships
    source: Mapped[SourceModel] = relationship(
        "SourceModel", back_populates="listings", lazy="selectin"
    )
    versions: Mapped[List[ListingVersionModel]] = relationship(
        "ListingVersionModel",
        back_populates="listing",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    duplicate_of: Mapped[Optional[ListingModel]] = relationship(
        "ListingModel",
        remote_side=[id],
        backref="duplicates",
        lazy="selectin",
    )


class ListingVersionModel(Base):
    """
    Historical point-in-time snapshots and price-drop tracking.
    """
    __tablename__ = "listing_versions"

    __table_args__ = (
        Index("idx_listing_versions_listing_date", "listing_id", "detected_at"),
    )

    id: Mapped[int] = mapped_column(AutoBigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        AutoBigInteger, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_diff_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    mileage: Mapped[Optional[int]] = mapped_column(Integer)
    title: Mapped[Optional[str]] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text)
    change_type: Mapped[str] = mapped_column(String(32), default="INITIAL", nullable=False)
    change_summary: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    listing: Mapped[ListingModel] = relationship(
        "ListingModel", back_populates="versions", lazy="selectin"
    )


class ParserRunModel(Base):
    """
    Execution telemetry and health logs for scraper invocations.
    """
    __tablename__ = "parser_runs"

    __table_args__ = (
        Index("idx_parser_runs_source_started", "source_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(AutoBigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), default=uuid.uuid4, nullable=False)
    source_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", nullable=False)
    items_scanned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_matched_filter: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_new: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_duplicates: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    execution_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    source: Mapped[SourceModel] = relationship(
        "SourceModel", back_populates="parser_runs", lazy="selectin"
    )


class UserFilterModel(Base):
    """
    User customizable search preferences and filter toggles per Telegram chat.
    """
    __tablename__ = "user_filters"

    chat_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    selected_models: Mapped[list[str]] = mapped_column(JSONType, default=lambda: ["A6 C5"], nullable=False)
    engines: Mapped[list[str]] = mapped_column(JSONType, default=lambda: ["1.8T", "2.4", "1.9 TDI"], nullable=False)
    min_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    min_year: Mapped[Optional[int]] = mapped_column(Integer, default=1997, nullable=True)
    max_year: Mapped[Optional[int]] = mapped_column(Integer, default=2005, nullable=True)
    max_mileage: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    transmission: Mapped[str] = mapped_column(String(32), default="any", nullable=False)
    exclude_damaged: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

