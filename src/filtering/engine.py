"""
Audi A6 C5 Strict Filtering Engine.

Orchestrates multi-pass text normalization, negative context elimination,
model & generation validation (strictly Audi A6 C5 1997-2005), and engine
whitelist / blacklist evaluation (1.8T, 2.4, 1.9 TDI).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from src.models.filter_result import FilterResult, FilterStatus
from src.models.listing import Listing, RawListingPayload
from src.filtering.engine_rules import (
    check_engine_blacklist,
    identify_target_engine,
)
from src.filtering.model_rules import (
    check_conflicting_generations,
    extract_year,
    is_avant_body,
    is_c5_explicit,
    is_sedan_body,
    validate_brand_and_model,
)
from src.filtering.negative_rules import evaluate_negative_rules
from src.filtering.normalizer import (
    normalize_automotive_identifiers,
    normalize_text,
    to_track_a,
)


class FilterEngine:
    """
    Deterministic 5-stage filtering engine for Audi A6 C5 monitoring.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}

    def filter_listing(
        self,
        listing_or_payload: Optional[Union[RawListingPayload, Listing, str, Dict[str, Any]]] = None,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        year: Optional[int] = None,
        brand: Optional[str] = None,
        model: Optional[str] = None,
        engine: Optional[str] = None,
        engine_str: Optional[str] = None,
        fuel_type: Optional[str] = None,
        body_type: Optional[str] = None,
        **kwargs: Any,
    ) -> FilterResult:
        """
        Evaluates a listing through the 5-stage deterministic filtering pipeline:
        Stage 0: Normalization & Preprocessing (Dual-Track)
        Stage 1: Negative Context Filtering
        Stage 2: Brand & Model Validation
        Stage 3: Generation & Year Validation
        Stage 4: Engine Whitelist / Blacklist
        Stage 5: Boundary & Ambiguity Arbitration
        """
        # 1. Unpack fields from listing_or_payload if provided
        raw_title = title
        raw_desc = description
        raw_year = year
        raw_brand = brand
        raw_model = model
        raw_engine = engine or engine_str
        raw_fuel = fuel_type
        raw_body = body_type

        if listing_or_payload is not None:
            if isinstance(listing_or_payload, str):
                if not raw_title:
                    raw_title = listing_or_payload
            elif isinstance(listing_or_payload, dict):
                raw_title = raw_title or listing_or_payload.get("title")
                raw_desc = raw_desc or listing_or_payload.get("description") or listing_or_payload.get("raw_text")
                raw_year = raw_year if raw_year is not None else listing_or_payload.get("year")
                raw_brand = raw_brand or listing_or_payload.get("brand")
                raw_model = raw_model or listing_or_payload.get("model")
                raw_engine = raw_engine or listing_or_payload.get("engine") or listing_or_payload.get("engine_code")
                raw_fuel = raw_fuel or listing_or_payload.get("fuel_type")
                raw_body = raw_body or listing_or_payload.get("body_type")
            elif hasattr(listing_or_payload, "__dict__"):
                # Pydantic model (Listing or RawListingPayload)
                raw_title = raw_title or getattr(listing_or_payload, "title", None)
                raw_desc = raw_desc or getattr(listing_or_payload, "description", None) or getattr(listing_or_payload, "raw_text", None)
                raw_year = raw_year if raw_year is not None else getattr(listing_or_payload, "year", None)
                raw_brand = raw_brand or getattr(listing_or_payload, "brand", None)
                raw_model = raw_model or getattr(listing_or_payload, "model", None)
                raw_engine = raw_engine or getattr(listing_or_payload, "engine_code", None) or getattr(listing_or_payload, "engine", None)
                raw_fuel = raw_fuel or getattr(listing_or_payload, "fuel_type", None)
                raw_body = raw_body or getattr(listing_or_payload, "body_type", None)

        title_str = raw_title or ""
        desc_str = raw_desc or ""
        engine_input = raw_engine or ""

        # ----------------------------------------------------------------------
        # STAGE 0: Normalization & Preprocessing (Dual-Track)
        # ----------------------------------------------------------------------
        # Pass 0: basic NFKC, zero-width removal, decimal comma standardisation
        norm_title = normalize_text(title_str)
        norm_desc = normalize_text(desc_str)
        norm_engine = normalize_text(engine_input)

        # Track A: pristine semantic text (lowercased) for negative rule checks
        clean_title_sem = to_track_a(norm_title)
        clean_desc_sem = to_track_a(norm_desc)

        # Track B: dual-script token harmonized text for identifiers & models
        norm_title_id = normalize_automotive_identifiers(norm_title)
        norm_desc_id = normalize_automotive_identifiers(norm_desc)
        norm_engine_id = normalize_automotive_identifiers(norm_engine)
        full_text_id = f"{norm_title_id} {norm_desc_id} {norm_engine_id}".strip()

        # ----------------------------------------------------------------------
        # STAGE 1: Negative Context Filtering (Track A)
        # ----------------------------------------------------------------------
        neg_reason = evaluate_negative_rules(clean_title_sem, clean_desc_sem)
        if neg_reason:
            return FilterResult(
                status=FilterStatus.REJECT,
                confidence=1.0,
                reasons=[neg_reason],
                normalized_engine=None,
                normalized_generation=None,
            )

        # ----------------------------------------------------------------------
        # STAGE 2: Brand & Model Validation
        # ----------------------------------------------------------------------
        is_model_valid, model_rejection_reason = validate_brand_and_model(
            structured_brand=raw_brand,
            structured_model=raw_model,
            title=norm_title_id,
            full_text=full_text_id,
        )
        if not is_model_valid and model_rejection_reason:
            return FilterResult(
                status=FilterStatus.REJECT,
                confidence=1.0,
                reasons=[model_rejection_reason],
                normalized_engine=None,
                normalized_generation=None,
            )

        # ----------------------------------------------------------------------
        # STAGE 3: Generation & Year Validation
        # ----------------------------------------------------------------------
        # Year extraction
        detected_year = extract_year(raw_year, full_text_id)

        # Check explicit conflicting generation (C4, C6, C7, C8)
        conflicting_gen = check_conflicting_generations(full_text_id)
        if conflicting_gen:
            reasons = [conflicting_gen]
            if detected_year is not None:
                if detected_year < 1997:
                    reasons = [f"YEAR_TOO_EARLY_{detected_year}", conflicting_gen]
                elif detected_year > 2005:
                    reasons = [conflicting_gen, f"YEAR_TOO_LATE_{detected_year}"]
            return FilterResult(
                status=FilterStatus.REJECT,
                confidence=1.0,
                reasons=reasons,
                normalized_engine=None,
                normalized_generation=None,
            )

        # Strict year boundary check
        if detected_year is not None:
            if detected_year < 1997:
                return FilterResult(
                    status=FilterStatus.REJECT,
                    confidence=1.0,
                    reasons=[f"YEAR_TOO_EARLY_{detected_year}"],
                    normalized_engine=None,
                    normalized_generation=None,
                )
            if detected_year > 2005:
                return FilterResult(
                    status=FilterStatus.REJECT,
                    confidence=1.0,
                    reasons=[f"YEAR_TOO_LATE_{detected_year}"],
                    normalized_engine=None,
                    normalized_generation=None,
                )

        # ----------------------------------------------------------------------
        # STAGE 4: Engine Whitelist / Blacklist Evaluation
        # ----------------------------------------------------------------------
        # Check engine blacklist first
        rej_engine = check_engine_blacklist(full_text_id)
        if rej_engine:
            return FilterResult(
                status=FilterStatus.REJECT,
                confidence=1.0,
                reasons=[rej_engine],
                normalized_engine=None,
                normalized_generation=None,
            )

        # Whitelist engine detection
        target_engine, engine_ambiguity = identify_target_engine(
            full_text_id,
            structured_engine=raw_engine,
            structured_fuel=raw_fuel,
        )

        if engine_ambiguity == "REJECTED_ENGINE_1.8_NON_TURBO":
            return FilterResult(
                status=FilterStatus.REJECT,
                confidence=1.0,
                reasons=["REJECTED_ENGINE_1.8_NON_TURBO"],
                normalized_engine=None,
                normalized_generation=None,
            )

        # ----------------------------------------------------------------------
        # STAGE 5: Boundary & Ambiguity Arbitration
        # ----------------------------------------------------------------------
        # Check C5 generation confirmation
        c5_confirmed = is_c5_explicit(full_text_id)

        # Pre-facelift & Facelift sweet spot (1998-2003) is exclusively C5
        if not c5_confirmed and detected_year and 1998 <= detected_year <= 2003:
            c5_confirmed = True

        # Transition Year 1997 Arbitration:
        if detected_year == 1997:
            if target_engine in ("1.8T", "2.4"):
                # Audi never fitted 1.8T or 2.4 in C4!
                c5_confirmed = True
            elif target_engine == "1.9_TDI":
                if not c5_confirmed:
                    return FilterResult(
                        status=FilterStatus.NEEDS_REVIEW,
                        confidence=0.7,
                        reasons=["YEAR_1997_1.9TDI_CHECK_C4_OR_C5"],
                        normalized_engine="1.9_TDI",
                        normalized_generation=None,
                    )

        # Transition Year 2004 Arbitration:
        if detected_year == 2004:
            if target_engine in ("1.8T", "1.9_TDI"):
                # Audi never fitted 1.8T or 1.9 TDI in C6!
                c5_confirmed = True
            elif target_engine == "2.4":
                if is_avant_body(raw_body, full_text_id):
                    # C6 Avant did not launch until 2005!
                    c5_confirmed = True
                elif not c5_confirmed:
                    return FilterResult(
                        status=FilterStatus.NEEDS_REVIEW,
                        confidence=0.7,
                        reasons=["YEAR_2004_2.4_CHECK_C5_OR_C6"],
                        normalized_engine="2.4",
                        normalized_generation=None,
                    )

        # Transition Year 2005 Arbitration:
        if detected_year == 2005:
            if not c5_confirmed:
                # 2005 without explicit C5/4B badge is overwhelmingly C6
                return FilterResult(
                    status=FilterStatus.REJECT,
                    confidence=1.0,
                    reasons=["YEAR_2005_CHECK_C5_OR_C6"],
                    normalized_engine=None,
                    normalized_generation=None,
                )

        # Handle ambiguous 1.8 petrol
        if engine_ambiguity == "AMBIGUOUS_1.8_CHECK_TURBO":
            return FilterResult(
                status=FilterStatus.NEEDS_REVIEW,
                confidence=0.6,
                reasons=["AMBIGUOUS_1.8_CHECK_TURBO"],
                normalized_engine=None,
                normalized_generation="C5" if c5_confirmed else None,
            )

        # Ambiguous multiple engines in text
        if engine_ambiguity == "AMBIGUOUS_MULTIPLE_ENGINES":
            return FilterResult(
                status=FilterStatus.NEEDS_REVIEW,
                confidence=0.5,
                reasons=["AMBIGUOUS_MULTIPLE_ENGINES"],
                normalized_engine=None,
                normalized_generation="C5" if c5_confirmed else None,
            )

        # Handle missing or unidentified engine
        if engine_ambiguity == "ENGINE_NOT_IDENTIFIED" or not target_engine:
            return FilterResult(
                status=FilterStatus.NEEDS_REVIEW,
                confidence=0.7,
                reasons=["ENGINE_NOT_IDENTIFIED"],
                normalized_engine=None,
                normalized_generation="C5" if c5_confirmed else None,
            )

        # Unconfirmed generation when no year or out-of-range indicators
        if not c5_confirmed:
            return FilterResult(
                status=FilterStatus.NEEDS_REVIEW,
                confidence=0.7,
                reasons=["AMBIGUOUS_GENERATION_CHECK_C5"],
                normalized_engine=target_engine,
                normalized_generation=None,
            )

        # ----------------------------------------------------------------------
        # FINAL PASS DECISION
        # ----------------------------------------------------------------------
        return FilterResult(
            status=FilterStatus.PASS,
            confidence=1.0,
            reasons=[f"AUDI_A6_C5_{target_engine}"],
            normalized_engine=target_engine,
            normalized_generation="C5",
        )
