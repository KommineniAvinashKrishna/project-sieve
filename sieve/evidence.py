from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from sieve.config import Config
from sieve.facts import compute_facts
from sieve.models import Alert, Customer, EvidenceItem, EvidencePack, Transaction


def build_evidence_pack(
    alert: Alert,
    customer: Customer,
    customer_txns: List[Transaction],
    config: Config,
) -> EvidencePack:
    created = datetime.fromisoformat(alert.created_at)
    start = (created - timedelta(days=alert.lookback_days)).strftime("%Y-%m-%dT%H:%M:%S")
    window = sorted(
        (t for t in customer_txns if start <= t.txn_ts <= alert.created_at),
        key=lambda t: (t.txn_ts, t.txn_id),
    )

    items: List[EvidenceItem] = [
        EvidenceItem(
            id="KYC.occupation",
            kind="kyc",
            label="Declared occupation",
            value=customer.occupation,
        ),
        EvidenceItem(
            id="KYC.employer_name",
            kind="kyc",
            label="KYC employer name",
            value=customer.employer_name,
        ),
        EvidenceItem(
            id="KYC.declared_monthly_income_inr",
            kind="kyc",
            label="Declared monthly income (INR)",
            value=customer.declared_monthly_income_inr,
            meta={"unit": "INR"},
        ),
        EvidenceItem(
            id="KYC.segment",
            kind="kyc",
            label="Customer segment",
            value=customer.segment.value,
        ),
        EvidenceItem(
            id="KYC.risk_rating",
            kind="kyc",
            label="KYC risk rating",
            value=customer.risk_rating,
        ),
        EvidenceItem(
            id="KYC.kyc_date",
            kind="kyc",
            label="Date KYC was last refreshed",
            value=customer.kyc_date,
        ),
    ]

    for txn in window:
        items.append(
            EvidenceItem(
                id=txn.txn_id,
                kind="txn",
                label="%s %s txn" % (txn.direction.value, txn.channel.value),
                value=txn.amount_inr,
                meta={
                    "txn_ts": txn.txn_ts,
                    "amount_inr": txn.amount_inr,
                    "direction": txn.direction.value,
                    "channel": txn.channel.value,
                    "counterparty_name": txn.counterparty_name,
                    "counterparty_country": txn.counterparty_country,
                    "narration": txn.narration,
                },
            )
        )

    items.extend(compute_facts(alert, customer, window, config))

    return EvidencePack(
        alert_id=alert.alert_id,
        customer_id=alert.customer_id,
        scenario_code=alert.scenario_code,
        created_at=alert.created_at,
        lookback_days=alert.lookback_days,
        items=items,
    )
