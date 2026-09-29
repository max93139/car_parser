"""
Filter result models and status definitions for car filtering engine.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class FilterStatus(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class FilterResult(BaseModel):
    """
    Evaluation result returned by the filtering engine for a car listing.
    """
    model_config = ConfigDict(
        use_enum_values=True,
        validate_assignment=True,
        str_strip_whitespace=True,
    )

    status: FilterStatus = Field(..., description="Decision status: PASS, REJECT, or NEEDS_REVIEW")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0")
    reasons: List[str] = Field(default_factory=list, description="Reason codes explaining the decision")
    normalized_engine: Optional[str] = Field(None, description="Normalized engine code, e.g. '1.8T', '2.4', '1.9_TDI'")
    normalized_generation: Optional[str] = Field(None, description="Normalized generation code, e.g. 'C5'")

    @property
    def is_passed(self) -> bool:
        return self.status == FilterStatus.PASS

    @property
    def is_rejected(self) -> bool:
        return self.status == FilterStatus.REJECT

    @property
    def is_review_needed(self) -> bool:
        return self.status == FilterStatus.NEEDS_REVIEW
