from __future__ import annotations

from pathlib import Path

import pytest

from sieve.grounding import validate_grounding
from sieve.models import Disposition, TriageDecision
from sieve.providers.fake import FakeProvider
from tests.helpers import build_pack, txn


def test_normal_mode_is_grounded_and_deterministic():
    pack = build_pack(
        txns=[txn(45000, channel="CASH", days_before=1), txn(46000, channel="CASH", days_before=2), txn(47000, channel="CASH", days_before=3)]
    )
    provider = FakeProvider()
    first = provider.triage(pack, [])
    second = provider.triage(pack, [])
    assert first == second
    decision = TriageDecision.model_validate_json(first)
    result = validate_grounding(decision, pack)
    assert result.passed is True, result.errors
    assert decision.disposition == Disposition.ESCALATE


def test_fake_provider_uses_narration_which_rules_do_not():
    sale = build_pack(
        txns=[txn(800000, channel="RTGS", counterparty="Rajesh Kumar", narration="Property sale - sale deed executed", days_before=12)]
    )
    vague = build_pack(
        txns=[txn(800000, channel="RTGS", counterparty="Acme Holdings", narration="Settlement received", days_before=12)]
    )
    provider = FakeProvider()
    sale_decision = TriageDecision.model_validate_json(provider.triage(sale, []))
    vague_decision = TriageDecision.model_validate_json(provider.triage(vague, []))
    assert sale_decision.disposition == Disposition.CLOSE_FALSE_POSITIVE
    assert vague_decision.disposition == Disposition.ESCALATE


def test_faulty_modes_are_detectable():
    pack = build_pack(txns=[txn(45000, days_before=3)])

    uncited = TriageDecision.model_validate_json(FakeProvider("uncited").triage(pack, []))
    assert validate_grounding(uncited, pack).passed is False

    foreign = TriageDecision.model_validate_json(FakeProvider("foreign_citation").triage(pack, []))
    errors = validate_grounding(foreign, pack).errors
    assert any("TXN-999999" in error for error in errors)

    bad_amount = TriageDecision.model_validate_json(FakeProvider("bad_amount").triage(pack, []))
    errors = validate_grounding(bad_amount, pack).errors
    assert any(error.startswith("NUMERIC") for error in errors)

    with pytest.raises(ValueError):
        TriageDecision.model_validate_json(FakeProvider("malformed_json").triage(pack, []))


def test_fake_provider_never_reads_labels():
    source = Path(__file__).resolve().parent.parent / "sieve" / "providers" / "fake.py"
    text = source.read_text(encoding="utf-8")
    assert "labels" not in text
    assert "sieve.db" not in text
    assert "sqlite" not in text
