from __future__ import annotations

import pytest

from sieve.grounding import validate_grounding
from sieve.models import Disposition, NarrativeSentence, TriageDecision
from tests.helpers import build_pack, txn


def decision(text, citations):
    return TriageDecision(
        disposition=Disposition.ESCALATE,
        confidence=0.8,
        narrative=[NarrativeSentence(text=text, citations=citations)],
    )


def pack():
    return build_pack(
        txns=[
            txn(45000, channel="CASH", days_before=5),
            txn(46000, channel="CASH", days_before=4),
            txn(47000, channel="CASH", days_before=3),
        ]
    )


NUMERIC_CASES = [
    ("The credit of \u20b945,000 was recorded on 2026-01-05.", ["TXN-000001"], True),
    ("The credit of 45000 was recorded.", ["TXN-000001"], True),
    ("The credit of INR 45,000.00 was recorded.", ["TXN-000001"], True),
    (
        "The lookback window contains 3 cash credits.",
        ["F.cash_credits_40k_50k_count_7d"],
        True,
    ),
    ("Declared monthly income is \u20b91,00,000.", ["KYC.declared_monthly_income_inr"], True),
    ("The credit of \u20b984,000 was recorded.", ["TXN-000001"], False),
    ("The credit was recorded on 2026-01-06.", ["TXN-000001"], False),
    (
        "The lookback window contains 8 cash credits.",
        ["F.cash_credits_40k_50k_count_7d"],
        False,
    ),
]


@pytest.mark.parametrize("text,citations,expected", NUMERIC_CASES)
def test_numeric_faithfulness_table(text, citations, expected):
    result = validate_grounding(decision(text, citations), pack())
    assert result.passed is expected
    if not expected:
        assert any(error.startswith("NUMERIC") for error in result.errors)


def test_uncited_sentence_fails_grounding():
    result = validate_grounding(decision("This looks suspicious.", []), pack())
    assert result.passed is False
    assert any(error.startswith("CITATION") for error in result.errors)


def test_citation_outside_pack_fails_grounding():
    result = validate_grounding(
        decision("A related transaction supports this view.", ["TXN-999999"]), pack()
    )
    assert result.passed is False
    assert any(error.startswith("CITATION") for error in result.errors)


def test_valid_multi_sentence_narrative_passes():
    result = validate_grounding(
        TriageDecision(
            disposition=Disposition.ESCALATE,
            confidence=0.8,
            narrative=[
                NarrativeSentence(
                    text="The customer is a teacher with declared income of \u20b91,00,000.",
                    citations=["KYC.occupation", "KYC.declared_monthly_income_inr"],
                ),
                NarrativeSentence(
                    text="The window contains 3 cash credits.",
                    citations=["F.cash_credits_40k_50k_count_7d"],
                ),
            ],
        ),
        pack(),
    )
    assert result.passed is True
    assert result.errors == []
