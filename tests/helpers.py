from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sieve.config import load_config
from sieve.evidence import build_evidence_pack
from sieve.models import (
    Alert,
    Channel,
    Customer,
    Direction,
    EvidencePack,
    Segment,
    Transaction,
)


CREATED_AT = "2026-01-10T09:00:00"


def txn(
    amount: int,
    direction: str = "CR",
    channel: str = "NEFT",
    days_before: int = 5,
    counterparty: str = "Acme Trading",
    country: str = "IN",
    narration: str = "Payment received",
) -> Dict[str, object]:
    return {
        "amount": amount,
        "direction": direction,
        "channel": channel,
        "days_before": days_before,
        "counterparty": counterparty,
        "country": country,
        "narration": narration,
    }


def build_pack(
    txns: Optional[List[Dict[str, object]]] = None,
    occupation: str = "Teacher",
    employer: str = "Meridian Public School",
    income: int = 100000,
    segment: str = "RETAIL",
    risk_rating: str = "LOW",
    created_at: str = CREATED_AT,
    lookback_days: int = 30,
) -> EvidencePack:
    customer = Customer(
        customer_id="CUST-TEST",
        segment=Segment(segment),
        occupation=occupation,
        declared_monthly_income_inr=income,
        employer_name=employer,
        risk_rating=risk_rating,
        kyc_date="2024-06-01",
    )
    alert = Alert(
        alert_id="ALERT-TEST",
        customer_id=customer.customer_id,
        scenario_code="CASH_STRUCTURING",
        created_at=created_at,
        lookback_days=lookback_days,
    )
    base = datetime.fromisoformat(created_at)
    transactions = []
    for index, item in enumerate(txns or [], start=1):
        ts = base - timedelta(days=int(item.get("days_before", 5)), hours=2)
        transactions.append(
            Transaction(
                txn_id="TXN-%06d" % index,
                customer_id=customer.customer_id,
                txn_ts=ts.strftime("%Y-%m-%dT%H:%M:%S"),
                amount_inr=int(item["amount"]),
                direction=Direction(item["direction"]),
                channel=Channel(item["channel"]),
                counterparty_name=str(item["counterparty"]),
                counterparty_country=str(item["country"]),
                narration=str(item["narration"]),
            )
        )
    return build_evidence_pack(alert, customer, transactions, load_config())
