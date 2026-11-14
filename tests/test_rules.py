from __future__ import annotations

import pytest

from sieve.config import load_config
from sieve.rules import evaluate_rules
from tests.helpers import build_pack, txn


CFG = load_config()


def codes_for(pack):
    _, reasons = evaluate_rules(pack, CFG)
    return reasons


CASES = [
    (
        "RC_CASH_BELOW_50K_REPEATED",
        [txn(45000, channel="CASH", days_before=1), txn(46000, channel="CASH", days_before=2), txn(47000, channel="CASH", days_before=3)],
        True,
    ),
    (
        "RC_CASH_BELOW_50K_REPEATED",
        [txn(45000, channel="CASH", days_before=1), txn(46000, channel="CASH", days_before=2)],
        False,
    ),
    (
        "RC_CREDITS_GT_5X_INCOME",
        [txn(600000, days_before=10)],
        True,
    ),
    (
        "RC_CREDITS_GT_5X_INCOME",
        [txn(400000, days_before=10)],
        False,
    ),
    (
        "RC_CREDITS_GT_2X_INCOME",
        [txn(250000, days_before=10)],
        True,
    ),
    (
        "RC_CREDITS_GT_2X_INCOME",
        [txn(150000, days_before=10)],
        False,
    ),
    (
        "RC_HIGH_RISK_COUNTRY_FLOW",
        [txn(200000, channel="SWIFT", country="IR", days_before=3)],
        True,
    ),
    (
        "RC_HIGH_RISK_COUNTRY_FLOW",
        [txn(200000, channel="SWIFT", country="IN", days_before=3)],
        False,
    ),
    (
        "RC_BENIGN_EMPLOYER_SALARY",
        [txn(100000, counterparty="Meridian Public School", narration="Salary credit", days_before=2)],
        True,
    ),
    (
        "RC_BENIGN_EMPLOYER_SALARY",
        [txn(300000, counterparty="Meridian Public School", narration="Salary credit", days_before=2)],
        False,
    ),
    (
        "RC_BENIGN_SEASONAL_CASH",
        [txn(45000, channel="CASH", days_before=1), txn(46000, channel="CASH", days_before=2), txn(47000, channel="CASH", days_before=3)],
        "jeweller",
    ),
    (
        "RC_BENIGN_SEASONAL_CASH",
        [txn(45000, channel="CASH", days_before=1), txn(46000, channel="CASH", days_before=2), txn(47000, channel="CASH", days_before=3)],
        False,
    ),
    (
        "RC_BENIGN_PROPERTY_SALE",
        [txn(800000, channel="RTGS", counterparty="Rajesh Kumar", days_before=12)],
        True,
    ),
    (
        "RC_BENIGN_PROPERTY_SALE",
        [txn(800000, channel="NEFT", counterparty="Rajesh Kumar", days_before=12)],
        False,
    ),
    (
        "RC_BENIGN_TRADE_INVOICE",
        [txn(300000, direction="DR", country="AE", narration="Payment against invoice", days_before=3)],
        "sme",
    ),
    (
        "RC_BENIGN_TRADE_INVOICE",
        [txn(300000, direction="DR", country="AE", narration="Payment against invoice", days_before=3)],
        False,
    ),
]


@pytest.mark.parametrize("code,txns,expected", CASES)
def test_reason_code_fires_and_does_not_fire(code, txns, expected):
    kwargs = {}
    if expected == "jeweller":
        kwargs["occupation"] = "Jeweller"
        expected = True
    if expected == "sme":
        kwargs["segment"] = "SME"
        expected = True
    pack = build_pack(txns=txns, **kwargs)
    reasons = codes_for(pack)
    found = [reason for reason in reasons if reason.code == code]
    assert bool(found) is expected
    if found:
        assert found[0].evidence_ids
        assert set(found[0].evidence_ids) <= set(pack.ids)


def test_rule_score_is_bounded_and_reason_codes_are_known():
    pack = build_pack(
        txns=[
            txn(45000, channel="CASH", days_before=1),
            txn(46000, channel="CASH", days_before=2),
            txn(47000, channel="CASH", days_before=3),
            txn(600000, channel="SWIFT", country="IR", days_before=4),
        ]
    )
    score, reasons = evaluate_rules(pack, CFG)
    assert 0.0 <= score <= 1.0
    assert {reason.code for reason in reasons} <= set(CFG.rules)
