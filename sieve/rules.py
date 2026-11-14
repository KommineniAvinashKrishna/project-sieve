from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from sieve.config import Config
from sieve.models import Direction, EvidenceItem, EvidencePack, ReasonCode


RuleFn = Callable[[EvidencePack, Config], Optional[List[str]]]


def _txns(pack: EvidencePack) -> List[EvidenceItem]:
    return pack.items_of_kind("txn")


def _kyc(pack: EvidencePack) -> Dict[str, EvidenceItem]:
    return {item.id: item for item in pack.items_of_kind("kyc")}


def _facts(pack: EvidencePack) -> Dict[str, EvidenceItem]:
    return {item.id: item for item in pack.items_of_kind("fact")}


def _cash_in_band(pack: EvidencePack, cfg: Config) -> List[EvidenceItem]:
    params = cfg.params("RC_CASH_BELOW_50K_REPEATED")
    created = datetime.fromisoformat(pack.created_at)
    cutoff = (created - timedelta(days=int(params["window_days"]))).strftime("%Y-%m-%dT%H:%M:%S")
    low = int(params["band_low_inr"])
    high = int(params["band_high_inr"])
    return [
        txn
        for txn in _txns(pack)
        if txn.meta["direction"] == Direction.CR.value
        and txn.meta["channel"] == "CASH"
        and low <= int(txn.value) < high
        and txn.meta["txn_ts"] >= cutoff
    ]


def rule_cash_below_50k_repeated(pack: EvidencePack, cfg: Config) -> Optional[List[str]]:
    params = cfg.params("RC_CASH_BELOW_50K_REPEATED")
    matches = _cash_in_band(pack, cfg)
    if len(matches) < int(params["min_count"]):
        return None
    return [txn.id for txn in matches] + ["F.cash_credits_40k_50k_count_7d"]


def _ratio_rule(pack: EvidencePack, cfg: Config, code: str) -> Optional[List[str]]:
    params = cfg.params(code)
    facts = _facts(pack)
    ratio = float(facts["F.credits_to_declared_income_ratio"].value)
    if ratio < float(params["multiple"]):
        return None
    credits = sorted(
        (txn for txn in _txns(pack) if txn.meta["direction"] == Direction.CR.value),
        key=lambda txn: int(txn.value),
        reverse=True,
    )[:4]
    return (
        ["F.credits_to_declared_income_ratio", "KYC.declared_monthly_income_inr"]
        + [txn.id for txn in credits]
        + ["F.total_credits_lookback_inr"]
    )


def rule_credits_gt_5x_income(pack: EvidencePack, cfg: Config) -> Optional[List[str]]:
    return _ratio_rule(pack, cfg, "RC_CREDITS_GT_5X_INCOME")


def rule_credits_gt_2x_income(pack: EvidencePack, cfg: Config) -> Optional[List[str]]:
    return _ratio_rule(pack, cfg, "RC_CREDITS_GT_2X_INCOME")


def rule_high_risk_country_flow(pack: EvidencePack, cfg: Config) -> Optional[List[str]]:
    params = cfg.params("RC_HIGH_RISK_COUNTRY_FLOW")
    matches = [
        txn
        for txn in _txns(pack)
        if txn.meta["counterparty_country"] in params["countries"]
        and int(txn.value) >= int(params["min_amount_inr"])
    ]
    if not matches:
        return None
    return [txn.id for txn in matches] + ["F.high_risk_country_txn_count"]


def rule_benign_employer_salary(pack: EvidencePack, cfg: Config) -> Optional[List[str]]:
    params = cfg.params("RC_BENIGN_EMPLOYER_SALARY")
    kyc = _kyc(pack)
    facts = _facts(pack)
    employer = str(kyc["KYC.employer_name"].value)
    income = int(kyc["KYC.declared_monthly_income_inr"].value)
    if employer == "Self" or income <= 0:
        return None
    high_ratio = float(cfg.params("RC_CREDITS_GT_5X_INCOME")["multiple"])
    if float(facts["F.credits_to_declared_income_ratio"].value) >= high_ratio:
        return None
    tolerance = float(params["income_tolerance_pct"]) / 100.0
    salary_txns = [
        txn
        for txn in _txns(pack)
        if txn.meta["direction"] == Direction.CR.value
        and txn.meta["counterparty_name"] == employer
        and txn.meta["channel"] == "NEFT"
        and income * (1 - tolerance) <= int(txn.value) <= income * (1 + tolerance)
    ]
    if not salary_txns:
        return None
    salary_ids = {txn.id for txn in salary_txns}
    other_credit_total = sum(
        int(txn.value)
        for txn in _txns(pack)
        if txn.meta["direction"] == Direction.CR.value and txn.id not in salary_ids
    )
    other_credit_cap = float(params["max_other_credit_pct_of_income"]) / 100.0
    if other_credit_total >= other_credit_cap * income:
        return None
    high_risk = cfg.params("RC_HIGH_RISK_COUNTRY_FLOW")
    if any(
        txn.meta["direction"] == Direction.DR.value
        and txn.meta["counterparty_country"] in high_risk["countries"]
        and int(txn.value) >= int(high_risk["min_amount_inr"])
        for txn in _txns(pack)
    ):
        return None
    return (
        [txn.id for txn in salary_txns]
        + ["KYC.employer_name", "KYC.declared_monthly_income_inr", "F.salary_credits_count"]
    )


def rule_benign_seasonal_cash(pack: EvidencePack, cfg: Config) -> Optional[List[str]]:
    params = cfg.params("RC_BENIGN_SEASONAL_CASH")
    kyc = _kyc(pack)
    occupation = str(kyc["KYC.occupation"].value)
    if occupation not in params["occupations"]:
        return None
    matches = _cash_in_band(pack, cfg)
    if len(matches) < int(cfg.params("RC_CASH_BELOW_50K_REPEATED")["min_count"]):
        return None
    return [txn.id for txn in matches] + ["KYC.occupation", "F.cash_credits_40k_50k_count_7d"]


def rule_benign_property_sale(pack: EvidencePack, cfg: Config) -> Optional[List[str]]:
    params = cfg.params("RC_BENIGN_PROPERTY_SALE")
    income = int(_kyc(pack)["KYC.declared_monthly_income_inr"].value)
    if income <= 0:
        return None
    threshold = income * float(params["min_income_multiple"])
    large = [
        txn
        for txn in _txns(pack)
        if txn.meta["direction"] == Direction.CR.value and int(txn.value) >= threshold
    ]
    if len(large) != 1:
        return None
    txn = large[0]
    if txn.meta["channel"] != "RTGS" or txn.meta["counterparty_country"] != "IN":
        return None
    return [txn.id, "F.max_single_credit_inr", "KYC.declared_monthly_income_inr"]


def rule_benign_trade_invoice(pack: EvidencePack, cfg: Config) -> Optional[List[str]]:
    params = cfg.params("RC_BENIGN_TRADE_INVOICE")
    if str(_kyc(pack)["KYC.segment"].value) not in params["segments"]:
        return None
    high_risk = cfg.params("RC_HIGH_RISK_COUNTRY_FLOW")
    matches = [
        txn
        for txn in _txns(pack)
        if txn.meta["direction"] == Direction.DR.value
        and txn.meta["counterparty_country"] in high_risk["countries"]
        and int(txn.value) >= int(high_risk["min_amount_inr"])
    ]
    if not matches:
        return None
    return [txn.id for txn in matches] + ["KYC.segment", "F.high_risk_country_txn_count"]


RULES: List[Tuple[str, RuleFn]] = [
    ("RC_CASH_BELOW_50K_REPEATED", rule_cash_below_50k_repeated),
    ("RC_CREDITS_GT_5X_INCOME", rule_credits_gt_5x_income),
    ("RC_CREDITS_GT_2X_INCOME", rule_credits_gt_2x_income),
    ("RC_HIGH_RISK_COUNTRY_FLOW", rule_high_risk_country_flow),
    ("RC_BENIGN_EMPLOYER_SALARY", rule_benign_employer_salary),
    ("RC_BENIGN_SEASONAL_CASH", rule_benign_seasonal_cash),
    ("RC_BENIGN_PROPERTY_SALE", rule_benign_property_sale),
    ("RC_BENIGN_TRADE_INVOICE", rule_benign_trade_invoice),
]


def evaluate_rules(pack: EvidencePack, cfg: Config) -> Tuple[float, List[ReasonCode]]:
    score = 0.0
    reasons: List[ReasonCode] = []
    for code, fn in RULES:
        evidence_ids = fn(pack, cfg)
        if evidence_ids is None:
            continue
        score += cfg.weight(code)
        reasons.append(ReasonCode(code=code, evidence_ids=evidence_ids))
    return round(min(1.0, max(0.0, score)), 4), reasons
