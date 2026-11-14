from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Set, Tuple

from sieve.models import EvidenceItem, EvidencePack, GroundingResult, TriageDecision


ID_PATTERN = re.compile(r"\b(?:TXN-\d+|KYC\.[A-Za-z_][A-Za-z0-9_.]*|F\.[A-Za-z0-9_.]+)")
DATE_PATTERN = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")
NUMBER_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _normalise_number(value: object) -> Decimal:
    return Decimal(str(value).replace(",", ""))


def _allowed_tokens(item: EvidenceItem) -> Tuple[Set[Decimal], Set[str]]:
    numbers: Set[Decimal] = set()
    dates: Set[str] = set()
    if isinstance(item.value, (int, float)) and not isinstance(item.value, bool):
        numbers.add(_normalise_number(item.value))
    if isinstance(item.value, str) and DATE_PATTERN.fullmatch(item.value.strip()):
        dates.add(item.value.strip())
    if item.kind == "txn":
        amount = item.meta.get("amount_inr")
        if amount is not None:
            numbers.add(_normalise_number(amount))
        txn_ts = str(item.meta.get("txn_ts", ""))
        if len(txn_ts) >= 10:
            dates.add(txn_ts[:10])
    return numbers, dates


def _extract_numbers(text: str) -> Tuple[List[Decimal], List[str]]:
    stripped = ID_PATTERN.sub(" ", text)
    dates = DATE_PATTERN.findall(stripped)
    without_dates = DATE_PATTERN.sub(" ", stripped)
    numbers: List[Decimal] = []
    for token in NUMBER_PATTERN.findall(without_dates):
        try:
            numbers.append(_normalise_number(token))
        except InvalidOperation:
            numbers.append(Decimal("-1"))
    return numbers, dates


def validate_grounding(decision: TriageDecision, pack: EvidencePack) -> GroundingResult:
    by_id: Dict[str, EvidenceItem] = {item.id: item for item in pack.items}
    errors: List[str] = []
    for index, sentence in enumerate(decision.narrative, start=1):
        if not sentence.citations:
            errors.append("CITATION: sentence %d has no citations" % index)
            continue
        unknown = [cid for cid in sentence.citations if cid not in by_id]
        if unknown:
            errors.append(
                "CITATION: sentence %d cites unknown evidence id(s) %s"
                % (index, ", ".join(sorted(unknown)))
            )
            continue

        allowed_numbers: Set[Decimal] = set()
        allowed_dates: Set[str] = set()
        for cid in sentence.citations:
            nums, dates = _allowed_tokens(by_id[cid])
            allowed_numbers.update(nums)
            allowed_dates.update(dates)

        numbers, dates = _extract_numbers(sentence.text)
        for number in numbers:
            if number not in allowed_numbers:
                errors.append(
                    "NUMERIC: sentence %d number %s does not match cited evidence %s"
                    % (index, _format_decimal(number), ",".join(sentence.citations))
                )
        for date in dates:
            if date not in allowed_dates:
                errors.append(
                    "NUMERIC: sentence %d date %s does not match cited evidence %s"
                    % (index, date, ",".join(sentence.citations))
                )
    return GroundingResult(passed=not errors, errors=errors)


def _format_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text
