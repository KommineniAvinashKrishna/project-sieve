from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from sieve.models import (
    Direction,
    Disposition,
    EvidenceItem,
    EvidencePack,
    NarrativeSentence,
    ReasonCode,
    TriageDecision,
)
from sieve.providers.base import LLMProvider


MODES = (
    "normal",
    "uncited",
    "foreign_citation",
    "malformed_json",
    "bad_amount",
    "overconfident",
)

JEWELLER_OCCUPATIONS = ("Jeweller", "Goldsmith", "Jewellery retailer")
FESTIVE_KEYWORDS = ("festive", "counter sales", "diwali", "seasonal")


@dataclass
class Signals:
    suspicious: float = 0.0
    benign: float = 0.0
    benign_kind: Optional[str] = None
    benign_txn: Optional[EvidenceItem] = None


def _inr(value: int) -> str:
    return "\u20b9{:,}".format(int(value))


class FakeProvider(LLMProvider):
    """Deterministic offline provider. It reads narration text and KYC risk rating,
    which the rule engine deliberately does not use."""

    name = "fake"

    def __init__(self, mode: str = "normal"):
        if mode not in MODES:
            raise ValueError("unknown fake provider mode: %s" % mode)
        self.mode = mode

    def triage(self, pack: EvidencePack, reason_codes: List[ReasonCode]) -> str:
        if self.mode == "malformed_json":
            return '{"disposition": "ESCALATE", "confidence": '
        signals = self._signals(pack)
        disposition = self._disposition(signals)
        confidence = self._confidence(signals, disposition)
        if self.mode == "overconfident":
            disposition = Disposition.CLOSE_FALSE_POSITIVE
            confidence = 0.99
        narrative = self._narrative(pack, disposition, signals)
        if self.mode == "uncited":
            narrative.append(
                NarrativeSentence(text="This alert looks concerning and needs attention.", citations=[])
            )
        if self.mode == "foreign_citation":
            narrative.append(
                NarrativeSentence(
                    text="A related transaction outside the pack supports this view.",
                    citations=["TXN-999999"],
                )
            )
        decision = TriageDecision(
            disposition=disposition, confidence=confidence, narrative=narrative
        )
        return decision.model_dump_json()

    def _signals(self, pack: EvidencePack) -> Signals:
        kyc: Dict[str, EvidenceItem] = {i.id: i for i in pack.items_of_kind("kyc")}
        facts: Dict[str, EvidenceItem] = {i.id: i for i in pack.items_of_kind("fact")}
        txns = pack.items_of_kind("txn")
        income = int(kyc["KYC.declared_monthly_income_inr"].value)
        employer = str(kyc["KYC.employer_name"].value)
        occupation = str(kyc["KYC.occupation"].value)
        segment = str(kyc["KYC.segment"].value)
        risk_rating = str(kyc["KYC.risk_rating"].value)
        narrations = " ".join(str(t.meta.get("narration", "")) for t in txns).lower()
        ratio = float(facts["F.credits_to_declared_income_ratio"].value)
        cash_count = int(facts["F.cash_credits_40k_50k_count_7d"].value)
        high_risk_count = int(facts["F.high_risk_country_txn_count"].value)
        max_credit = int(facts["F.max_single_credit_inr"].value)

        signals = Signals()
        if cash_count >= 3:
            signals.suspicious += 1.0
        if ratio >= 5:
            signals.suspicious += 1.4
        elif ratio >= 2:
            signals.suspicious += 0.5
        if high_risk_count >= 1:
            signals.suspicious += 1.2
        if risk_rating == "HIGH":
            signals.suspicious += 0.3

        salary_txn = None
        cash_total = sum(
            int(txn.value)
            for txn in txns
            if txn.meta["direction"] == Direction.CR.value
            and txn.meta["channel"] == "CASH"
            and 40000 <= int(txn.value) < 50000
        )
        salary_explains_alert = not (cash_count >= 3 and cash_total >= 0.5 * income)
        if employer != "Self" and "salary" in narrations and salary_explains_alert:
            for txn in txns:
                amount = int(txn.value)
                if (
                    txn.meta["direction"] == Direction.CR.value
                    and txn.meta["counterparty_name"] == employer
                    and income * 0.65 <= amount <= income * 1.35
                ):
                    salary_txn = txn
                    break
        if salary_txn is not None:
            signals.benign += 1.6
            signals.benign_kind = "salary"
            signals.benign_txn = salary_txn

        property_txn = None
        if ("sale deed" in narrations or "property sale" in narrations) and max_credit >= 5 * income:
            for txn in txns:
                if txn.meta["direction"] == Direction.CR.value and int(txn.value) == max_credit:
                    property_txn = txn
                    break
        if property_txn is not None:
            signals.benign += 1.6
            if signals.benign_kind is None:
                signals.benign_kind = "property"
                signals.benign_txn = property_txn

        invoice_txn = None
        if "invoice" in narrations or " inv-" in narrations:
            for txn in txns:
                if txn.meta["direction"] == Direction.DR.value:
                    invoice_txn = txn
                    break
        if invoice_txn is not None:
            signals.benign += 1.4
            if signals.benign_kind is None:
                signals.benign_kind = "trade"
                signals.benign_txn = invoice_txn
            if segment in ("SME", "CORPORATE"):
                signals.benign += 0.5

        festive_txn = None
        if occupation in JEWELLER_OCCUPATIONS and any(k in narrations for k in FESTIVE_KEYWORDS):
            festive_txn = next(
                (
                    txn
                    for txn in txns
                    if txn.meta["direction"] == Direction.CR.value
                    and txn.meta["channel"] == "CASH"
                ),
                None,
            )
        if festive_txn is not None:
            signals.benign += 1.4
            if signals.benign_kind is None:
                signals.benign_kind = "festive"
                signals.benign_txn = festive_txn

        if signals.benign_kind is None:
            for txn in txns:
                if txn.meta["direction"] == Direction.CR.value:
                    signals.benign_txn = txn
                    break
        return signals

    @staticmethod
    def _disposition(signals: Signals) -> Disposition:
        if signals.benign > signals.suspicious:
            return Disposition.CLOSE_FALSE_POSITIVE
        return Disposition.ESCALATE

    @staticmethod
    def _confidence(signals: Signals, disposition: Disposition) -> float:
        gap = abs(signals.benign - signals.suspicious)
        return round(min(0.98, max(0.5, 0.65 + 0.15 * gap)), 2)

    def _narrative(
        self, pack: EvidencePack, disposition: Disposition, signals: Signals
    ) -> List[NarrativeSentence]:
        kyc: Dict[str, EvidenceItem] = {i.id: i for i in pack.items_of_kind("kyc")}
        facts: Dict[str, EvidenceItem] = {i.id: i for i in pack.items_of_kind("fact")}
        txns = pack.items_of_kind("txn")
        occupation = str(kyc["KYC.occupation"].value)
        segment = str(kyc["KYC.segment"].value)
        income = int(kyc["KYC.declared_monthly_income_inr"].value)
        cash_count = int(facts["F.cash_credits_40k_50k_count_7d"].value)
        high_risk_count = int(facts["F.high_risk_country_txn_count"].value)
        total_credits = int(facts["F.total_credits_lookback_inr"].value)
        ratio = float(facts["F.credits_to_declared_income_ratio"].value)

        sentences: List[NarrativeSentence] = [
            NarrativeSentence(
                text="The customer is a %s in the %s segment with declared monthly income of %s."
                % (occupation.lower(), segment.lower(), _inr(income)),
                citations=[
                    "KYC.occupation",
                    "KYC.segment",
                    "KYC.declared_monthly_income_inr",
                ],
            )
        ]
        if cash_count > 0:
            sentences.append(
                NarrativeSentence(
                    text="The lookback window contains %d cash credits just below the reporting threshold."
                    % cash_count,
                    citations=["F.cash_credits_40k_50k_count_7d"],
                )
            )
        if total_credits > 0:
            sentences.append(
                NarrativeSentence(
                    text="Credits in the lookback total %s, equal to %.2f times declared income."
                    % (_inr(total_credits), ratio),
                    citations=[
                        "F.total_credits_lookback_inr",
                        "F.credits_to_declared_income_ratio",
                        "KYC.declared_monthly_income_inr",
                    ],
                )
            )
        if high_risk_count > 0:
            sentences.append(
                NarrativeSentence(
                    text="The window includes %d transfers involving a high-risk jurisdiction."
                    % high_risk_count,
                    citations=["F.high_risk_country_txn_count"],
                )
            )

        target: Optional[EvidenceItem] = signals.benign_txn
        if target is None and txns:
            target = max(
                txns,
                key=lambda txn: (
                    txn.meta["direction"] == Direction.CR.value,
                    int(txn.value),
                ),
            )
        if target is not None:
            amount = int(target.value)
            if self.mode == "bad_amount":
                amount = amount + 40000
            sentences.append(
                NarrativeSentence(
                    text="On %s, a %s of %s was recorded through %s."
                    % (
                        str(target.meta["txn_ts"])[:10],
                        str(target.meta["direction"]),
                        _inr(amount),
                        str(target.meta["channel"]),
                    ),
                    citations=[target.id],
                )
            )

        if disposition == Disposition.CLOSE_FALSE_POSITIVE:
            explanation = self._benign_explanation(signals, kyc, target)
        else:
            explanation = "The evidence supports escalation to an investigator."
        sentences.append(
            NarrativeSentence(
                text=explanation,
                citations=[signals.benign_txn.id if signals.benign_txn else "KYC.risk_rating"],
            )
        )
        return sentences

    @staticmethod
    def _benign_explanation(
        signals: Signals,
        kyc: Dict[str, EvidenceItem],
        target: Optional[EvidenceItem],
    ) -> str:
        employer = str(kyc["KYC.employer_name"].value)
        if signals.benign_kind == "salary":
            return "The credit counterparty matches the KYC employer %s." % employer
        if signals.benign_kind == "property":
            return "The RTGS credit is narrated as a property sale under a registered sale deed."
        if signals.benign_kind == "trade":
            return "The debit is narrated as payment against an import invoice for a business customer."
        if signals.benign_kind == "festive":
            return "The declared occupation and counter-sale narrations support seasonal jewellery trade."
        return "The evidence contains a plausible benign explanation."
