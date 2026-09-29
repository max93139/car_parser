"""
Filtering package for Audi A6 C5 Monitoring Service.
"""

from src.filtering.engine import FilterEngine
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
    replace_homoglyphs,
    to_track_a,
)

__all__ = [
    "FilterEngine",
    "normalize_text",
    "to_track_a",
    "replace_homoglyphs",
    "normalize_automotive_identifiers",
    "evaluate_negative_rules",
    "validate_brand_and_model",
    "extract_year",
    "check_conflicting_generations",
    "is_c5_explicit",
    "is_avant_body",
    "is_sedan_body",
    "check_engine_blacklist",
    "identify_target_engine",
]
