"""
Pydantic v2 Listing and RawListingPayload models for Audi A6 C5 Monitoring Service.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class SourceType(str, Enum):
    AUTO_RIA = "auto_ria"
    OLX = "olx"
    RST = "rst"
    TELEGRAM = "telegram"
    INSTAGRAM = "instagram"


class FuelType(str, Enum):
    PETROL = "petrol"
    GAS_PETROL = "gas_petrol"  # LPG / бензин
    DIESEL = "diesel"
    OTHER = "other"


class TransmissionType(str, Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"
    TIPTRONIC = "tiptronic"
    UNKNOWN = "unknown"


class DriveType(str, Enum):
    FRONT = "front"
    QUATTRO = "quattro"
    UNKNOWN = "unknown"


class BodyType(str, Enum):
    SEDAN = "sedan"
    AVANT = "avant"  # Wagon / универсал
    ALLROAD = "allroad"
    UNKNOWN = "unknown"


class ListingStatus(str, Enum):
    NEW = "NEW"
    SENT = "SENT"
    DUPLICATE = "DUPLICATE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"
    INACTIVE = "INACTIVE"


class Currency(str, Enum):
    USD = "USD"
    EUR = "EUR"
    UAH = "UAH"


EXCHANGE_RATES_TO_USD: Dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "UAH": 0.0241,  # ~41.5 UAH per USD
}


class RawListingPayload(BaseModel):
    """
    Raw untyped / semi-typed payload yielded by scrapers before normalization.
    Supports both raw text fields and normalized fields.
    """
    model_config = ConfigDict(
        extra="allow",
        arbitrary_types_allowed=True,
        populate_by_name=True,
    )

    source: str = Field(..., description="Source identifier: auto_ria, olx, rst, telegram, instagram")
    source_id: str = Field(..., description="Platform-native listing identifier")
    url: str = Field(..., description="Original URL of the listing")
    title: str = Field(..., description="Listing headline or title")
    description: Optional[str] = Field(default=None, description="Listing description text")
    raw_text: Optional[str] = Field(default=None, description="Full unparsed text body")

    # Raw extracted parameters
    raw_price: Optional[str] = Field(default=None)
    price: Optional[float] = Field(default=None)
    currency: Optional[str] = Field(default="USD")

    raw_year: Optional[str] = Field(default=None)
    year: Optional[int] = Field(default=None)

    raw_mileage: Optional[str] = Field(default=None)
    mileage: Optional[int] = Field(default=None)

    raw_engine: Optional[str] = Field(default=None)
    engine: Optional[str] = Field(default=None)

    raw_fuel: Optional[str] = Field(default=None)
    fuel_type: Optional[str] = Field(default=None)

    raw_transmission: Optional[str] = Field(default=None)
    transmission: Optional[str] = Field(default=None)

    raw_location: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None)

    seller: Optional[str] = Field(default=None)
    seller_name: Optional[str] = Field(default=None)
    seller_phone: Optional[str] = Field(default=None)

    images: List[str] = Field(default_factory=list)
    image_urls: List[str] = Field(default_factory=list)

    published_at: Optional[datetime] = Field(default=None)
    extra_attributes: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def harmonize_fields(self) -> RawListingPayload:
        if not self.description and self.raw_text:
            self.description = self.raw_text
        if not self.raw_text and self.description:
            self.raw_text = self.description

        if not self.seller and self.seller_name:
            self.seller = self.seller_name
        if not self.seller_name and self.seller:
            self.seller_name = self.seller

        if not self.images and self.image_urls:
            self.images = list(self.image_urls)
        elif not self.image_urls and self.images:
            self.image_urls = list(self.images)

        return self


class Listing(BaseModel):
    """
    Unified strongly-typed Listing Model representing an ad across all platforms.
    """
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        populate_by_name=True,
        from_attributes=True,
        use_enum_values=True,
        arbitrary_types_allowed=True,
    )

    # Source identity
    source: str = Field(..., description="Platform identifier: auto_ria, olx, rst, telegram, instagram")
    source_id: str = Field(..., min_length=1, max_length=128, description="Native source ad ID")
    url: str = Field(..., min_length=1, description="Original raw ad URL")
    canonical_url: Optional[str] = Field(default=None, description="Cleaned normalized URL")

    # Content
    title: str = Field(..., min_length=1, max_length=500, description="Listing title")
    description: Optional[str] = Field(default=None, description="Listing description")
    brand: str = Field(default="Audi", max_length=50)
    model: str = Field(default="A6", max_length=50)
    generation: str = Field(default="C5", max_length=20)
    year: Optional[int] = Field(default=None, ge=1990, le=2015, description="Manufacturing year")
    body_type: Optional[str] = Field(default="unknown", max_length=32)

    # Financial
    price: Optional[float] = Field(default=None, ge=0.0, description="Price in listing currency")
    currency: Optional[str] = Field(default="USD", max_length=8)
    price_usd: Optional[float] = Field(default=None, ge=0.0, description="Normalized price in USD")

    # Specifications
    mileage: Optional[int] = Field(default=None, ge=0, le=2_000_000, description="Odometer in km")
    engine: Optional[str] = Field(default=None, max_length=100, description="Raw or parsed engine string")
    engine_code: Optional[str] = Field(default=None, max_length=32, description="Normalized engine code: 1.8T, 2.4, 1.9TDI")
    fuel_type: Optional[str] = Field(default="other", max_length=32)
    transmission: Optional[str] = Field(default="unknown", max_length=32)
    drive_type: Optional[str] = Field(default="unknown", max_length=32)

    # Location & Seller
    location: Optional[str] = Field(default=None, max_length=128)
    location_city: Optional[str] = Field(default=None, max_length=64)
    location_region: Optional[str] = Field(default=None, max_length=64)
    seller: Optional[str] = Field(default=None, max_length=128)
    seller_phone: Optional[str] = Field(default=None, max_length=32)

    # Media
    images: List[str] = Field(default_factory=list, description="Cleaned list of image URLs")

    # Fingerprinting & Deduplication
    content_fingerprint: Optional[str] = Field(default=None, max_length=64)
    fuzzy_fingerprint: Optional[str] = Field(default=None, max_length=64)
    status: str = Field(default="NEW", max_length=32)
    duplicate_of_id: Optional[int] = Field(default=None)
    dedup_level: int = Field(default=0)

    # Telegram notification state
    is_sent_to_telegram: bool = Field(default=False)
    telegram_message_id: Optional[int] = Field(default=None)
    telegram_sent_at: Optional[datetime] = Field(default=None)

    # Timestamps
    published_at: Optional[datetime] = Field(default=None)
    first_seen_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when service first saw listing",
    )
    last_checked_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when service last verified listing",
    )

    # Raw audit payload
    raw_data: Dict[str, Any] = Field(default_factory=dict)

    # ----------------- Validators -----------------

    @field_validator("seller_phone", mode="before")
    @classmethod
    def normalize_seller_phone(cls, v: Any) -> Optional[str]:
        if not v or not isinstance(v, str):
            return None
        digits = re.sub(r"\D", "", v)
        if not digits:
            return None
        if digits.startswith("380") and len(digits) == 12:
            return f"+{digits}"
        if digits.startswith("0") and len(digits) == 10:
            return f"+38{digits}"
        if len(digits) == 9:
            return f"+380{digits}"
        return f"+{digits}"

    @field_validator("images", mode="before")
    @classmethod
    def clean_images(cls, v: Any) -> List[str]:
        if not v:
            return []
        if isinstance(v, str):
            s = v.strip()
            return [s] if s else []
        if isinstance(v, (list, tuple, set)):
            cleaned: List[str] = []
            seen = set()
            for img in v:
                if isinstance(img, str):
                    s = img.strip()
                    if s and s not in seen:
                        cleaned.append(s)
                        seen.add(s)
            return cleaned
        return []

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, v: Any) -> str:
        if not v:
            return "USD"
        v_str = str(v).strip().upper()
        if v_str in ("$", "US", "USD"):
            return "USD"
        if v_str in ("€", "EUR"):
            return "EUR"
        if v_str in ("ГРН", "ГРН.", "UAH"):
            return "UAH"
        return v_str

    @model_validator(mode="after")
    def compute_derived_fields(self) -> Listing:
        # 1. Canonical URL
        if self.canonical_url is None:
            if self.url and self.url.strip():
                object.__setattr__(
                    self,
                    "canonical_url",
                    self.compute_canonical_url(self.source, self.url, self.source_id),
                )
            else:
                object.__setattr__(self, "canonical_url", "")

        # 2. Price in USD
        if self.price is not None and self.price_usd is None:
            curr = (self.currency or "USD").upper()
            rate = EXCHANGE_RATES_TO_USD.get(curr, 1.0)
            object.__setattr__(self, "price_usd", round(float(self.price) * rate, 2))

        # 3. Location City extraction if not provided
        if not self.location_city and self.location:
            parts = re.split(r"[,/|;]", self.location)
            if parts and parts[0].strip():
                object.__setattr__(self, "location_city", parts[0].strip())

        # 4. Fingerprints
        if not self.content_fingerprint:
            object.__setattr__(
                self, "content_fingerprint", self.compute_content_fingerprint(strict=True)
            )
        if not self.fuzzy_fingerprint:
            object.__setattr__(
                self, "fuzzy_fingerprint", self.compute_content_fingerprint(strict=False)
            )

        return self

    # ----------------- Computation Helpers -----------------

    @staticmethod
    def compute_canonical_url(source: Union[str, SourceType], url: str, source_id: str) -> str:
        """
        Cleans tracking queries, session parameters and normalizes domain/path per source.
        """
        if not url:
            return ""
        parsed = urlparse(url)
        drop_params = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "ref", "fbclid", "gclid", "yclid", "_ga", "from", "hash", "session_id",
            "reason", "tab", "search_id", "ad_index", "igsh", "igshid", "_gl"
        }
        filtered_query = [
            (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
            if k.lower() not in drop_params and not k.lower().startswith("utm_")
        ]
        filtered_query.sort(key=lambda x: x[0])
        clean_query = urlencode(filtered_query)

        path = parsed.path.rstrip("/")
        netloc = parsed.netloc.lower()

        # Strip standard port if present
        if ":" in netloc:
            netloc = netloc.split(":")[0]

        src_str = str(source).lower()
        if "auto_ria" in src_str or "auto.ria" in netloc:
            path = re.sub(r"^/(uk|ru)/", "/", path)
            netloc = "auto.ria.com"
        elif "olx" in src_str or "olx" in netloc:
            path = re.sub(r"^/d/(uk|ru)/", "/d/", path)
            path = re.sub(r"^/(uk|ru)/", "/", path)
            netloc = "www.olx.ua"
        elif "rst" in src_str or "rst" in netloc:
            path = re.sub(r"^/ukr/", "/", path)
            netloc = "rst.ua"
        elif "telegram" in src_str or "t.me" in netloc:
            netloc = "t.me"
        elif "instagram" in src_str or "instagram" in netloc:
            netloc = "www.instagram.com"
            if re.match(r"^/(p|reel|tv)/", path):
                clean_query = ""

        return urlunparse(("https", netloc, path, "", clean_query, ""))

    def compute_content_fingerprint(self, strict: bool = True) -> str:
        """
        SHA-256 fingerprint over invariant vehicle characteristics.
        Strict includes seller identification; Fuzzy excludes seller identification.
        """
        # Bucket mileage into 10k increments
        if self.mileage is not None and self.mileage > 0:
            mileage_bucket = str((int(self.mileage) // 10_000) * 10_000)
        else:
            mileage_bucket = "UNKNOWN_MILEAGE"

        year_str = str(self.year) if self.year else "UNKNOWN_YEAR"
        body_str = str(self.body_type or "unknown").lower().strip()
        engine_str = (self.engine_code or self.engine or "UNKNOWN_ENGINE").upper().strip()
        fuel_str = str(self.fuel_type or "other").lower().strip()
        trans_str = str(self.transmission or "unknown").lower().strip()
        drive_str = str(self.drive_type or "unknown").lower().strip()

        loc = (self.location_city or self.location or "UNKNOWN_LOC").lower().strip()
        # Strip common Ukrainian/Russian geographic prefixes (м., г., город, місто)
        loc = re.sub(r"^(?:м\.|г\.|город|місто)\s*", "", loc).strip()

        # Normalize Ukrainian city names
        loc = re.sub(r"^(киев|київ)$", "kyiv", loc)
        loc = re.sub(r"^(львов|львів)$", "lviv", loc)
        loc = re.sub(r"^(одесса|одеса)$", "odesa", loc)
        loc = re.sub(r"^(днепр|дніпро|днепропетровск|дніпропетровськ)$", "dnipro", loc)
        loc = re.sub(r"^(харьков|харків)$", "kharkiv", loc)

        tokens = [
            "AUDI_A6_C5",
            year_str,
            body_str,
            engine_str,
            fuel_str,
            trans_str,
            drive_str,
            mileage_bucket,
            loc,
        ]

        if strict:
            if self.seller_phone:
                tokens.append(self.seller_phone)
            elif self.seller:
                tokens.append(self.seller.lower().strip())

        raw_fingerprint = "|".join(tokens)
        return hashlib.sha256(raw_fingerprint.encode("utf-8")).hexdigest()
