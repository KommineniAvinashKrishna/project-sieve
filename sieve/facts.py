from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from sieve.config import Config
from sieve.models import Alert, Customer, Direction, EvidenceItem, Transaction


def compute_facts(
    alert: Alert, customer: Customer, txns: List[Transaction], config: Config
) -> List[EvidenceItem]:
    created = datetime.fromisoformat(alert.created_at)
    cash_params = config.params("RC_CASH_BELOW_50K_REPEATED")
    high_risk_params = config.params("RC_HIGH_RISK_COUNTRY_FLOW")
    cutoff_7d = (created - timedelta(days=int(cash_params["window_days"]))).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    band_low = int(cash_params["band_low_inr"])
    band_high = int(cash_params["band_high_inr"])

    cash_count = sum(
        1
        for t in txns
        if t.direction == Direction.CR
        and t.channel.value == "CASH"
        and band_low <= t.amount_inr < band_high
        and t.txn_ts >= cutoff_7d
    )
    credits = [t for t in txns if t.direction == Direction.CR]
    total_credits = sum(t.amount_inr for t in credits)
    income = customer.declared_monthly_income_inr
    ratio = round(total_credits / income, 2) if income > 0 else 0.0
    salary_count = sum(
        1
        for t in credits
        if t.counterparty_name == customer.employer_name and customer.employer_name != "Self"
    )
    high_risk_count = sum(
        1
        for t in txns
        if t.counterparty_country in high_risk_params["countries"]
        and t.amount_inr >= int(high_risk_params["min_amount_inr"])
    )
    max_credit = max((t.amount_inr for t in credits), default=0)

    return [
        EvidenceItem(
            id="F.cash_credits_40k_50k_count_7d",
            kind="fact",
            label="Cash credits in the 40,000-50,000 INR band over the last 7 days",
            value=cash_count,
            meta={"unit": "count"},
        ),
        EvidenceItem(
            id="F.credits_to_declared_income_ratio",
            kind="fact",
            label="Total credits divided by declared monthly income",
            value=ratio,
            meta={"unit": "ratio"},
        ),
        EvidenceItem(
            id="F.total_credits_lookback_inr",
            kind="fact",
            label="Total credits inside the alert lookback window",
            value=total_credits,
            meta={"unit": "INR"},
        ),
        EvidenceItem(
            id="F.salary_credits_count",
            kind="fact",
            label="Credits from the KYC employer inside the lookback window",
            value=salary_count,
            meta={"unit": "count"},
        ),
        EvidenceItem(
            id="F.high_risk_country_txn_count",
            kind="fact",
            label="Transactions at or above the reporting amount with a high-risk country",
            value=high_risk_count,
            meta={"unit": "count"},
        ),
        EvidenceItem(
            id="F.max_single_credit_inr",
            kind="fact",
            label="Largest single credit inside the lookback window",
            value=max_credit,
            meta={"unit": "INR"},
        ),
    ]
