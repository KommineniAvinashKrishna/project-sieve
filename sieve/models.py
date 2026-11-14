from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Segment(str, Enum):
    RETAIL = "RETAIL"
    SME = "SME"
    CORPORATE = "CORPORATE"


class Direction(str, Enum):
    CR = "CR"
    DR = "DR"


class Channel(str, Enum):
    CASH = "CASH"
    NEFT = "NEFT"
    RTGS = "RTGS"
    IMPS = "IMPS"
    UPI = "UPI"
    SWIFT = "SWIFT"


class Disposition(str, Enum):
    ESCALATE = "ESCALATE"
    CLOSE_FALSE_POSITIVE = "CLOSE_FALSE_POSITIVE"


class Label(str, Enum):
    SUSPICIOUS = "SUSPICIOUS"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class Route(str, Enum):
    AUTO_CLOSE = "AUTO_CLOSE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    QA_SAMPLE = "QA_SAMPLE"


class Customer(BaseModel):
    customer_id: str
    segment: Segment
    occupation: str
    declared_monthly_income_inr: int = Field(ge=0)
    employer_name: str
    risk_rating: str
    kyc_date: str


class Transaction(BaseModel):
    txn_id: str
    customer_id: str
    txn_ts: str
    amount_inr: int = Field(ge=0)
    direction: Direction
    channel: Channel
    counterparty_name: str
    counterparty_country: str
    narration: str


class Alert(BaseModel):
    alert_id: str
    customer_id: str
    scenario_code: str
    created_at: str
    lookback_days: int = Field(gt=0)


class EvidenceItem(BaseModel):
    id: str
    kind: str
    label: str
    value: Any
    meta: Dict[str, Any] = Field(default_factory=dict)


class EvidencePack(BaseModel):
    alert_id: str
    customer_id: str
    scenario_code: str
    created_at: str
    lookback_days: int
    items: List[EvidenceItem]

    def item(self, item_id: str) -> Optional[EvidenceItem]:
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    @property
    def ids(self) -> List[str]:
        return [item.id for item in self.items]

    def items_of_kind(self, kind: str) -> List[EvidenceItem]:
        return [item for item in self.items if item.kind == kind]


class ReasonCode(BaseModel):
    code: str
    evidence_ids: List[str]
    detail: Optional[str] = None


class NarrativeSentence(BaseModel):
    text: str
    citations: List[str]


class TriageDecision(BaseModel):
    disposition: Disposition
    confidence: float = Field(ge=0.0, le=1.0)
    narrative: List[NarrativeSentence]


class GroundingResult(BaseModel):
    passed: bool
    errors: List[str] = Field(default_factory=list)


class TriageResult(BaseModel):
    alert_id: str
    disposition: Disposition
    confidence: float
    rule_score: float
    reason_codes: List[ReasonCode]
    narrative: List[NarrativeSentence]
    grounding: GroundingResult
    provider: str
    route: Route


def to_decimal(value: Any) -> Optional[Decimal]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return Decimal(str(value))
