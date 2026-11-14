from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from sieve import db
from sieve.config import Config, load_config
from sieve.evidence import build_evidence_pack
from sieve.grounding import validate_grounding
from sieve.models import (
    Alert,
    Customer,
    Disposition,
    EvidencePack,
    GroundingResult,
    TriageDecision,
    TriageResult,
    Transaction,
)
from sieve.prompt import build_prompt
from sieve.providers.base import LLMProvider
from sieve.providers.fake import FakeProvider
from sieve.routing import decide_route
from sieve.rules import evaluate_rules


class AlertNotFound(Exception):
    pass


@dataclass
class CoreResult:
    result: TriageResult
    pack: EvidencePack
    prompt: str
    prompt_hash: str
    evidence_hash: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def triage_core(
    alert: Alert,
    customer: Customer,
    customer_txns: List[Transaction],
    provider: LLMProvider,
    cfg: Optional[Config] = None,
) -> CoreResult:
    cfg = cfg or load_config()
    pack = build_evidence_pack(alert, customer, customer_txns, cfg)
    rule_score, reason_codes = evaluate_rules(pack, cfg)
    prompt = build_prompt(pack, reason_codes)
    prompt_hash = _sha256(prompt)
    evidence_hash = _sha256(pack.model_dump_json())

    decision: Optional[TriageDecision] = None
    try:
        raw = provider.triage(pack, reason_codes)
        decision = TriageDecision.model_validate_json(raw)
        grounding = validate_grounding(decision, pack)
    except Exception as exc:  # provider or parse failure must never close an alert
        grounding = GroundingResult(
            passed=False,
            errors=["PROVIDER: malformed or failed provider output (%s)" % type(exc).__name__],
        )

    if decision is None:
        disposition = Disposition.ESCALATE
        confidence = 0.0
        narrative = []
    else:
        disposition = decision.disposition
        confidence = decision.confidence
        narrative = decision.narrative
    if not grounding.passed:
        disposition = Disposition.ESCALATE

    route = decide_route(
        disposition=disposition,
        confidence=confidence,
        grounding_passed=grounding.passed,
        rule_score=rule_score,
        alert_id=alert.alert_id,
        cfg=cfg,
    )
    result = TriageResult(
        alert_id=alert.alert_id,
        disposition=disposition,
        confidence=confidence,
        rule_score=rule_score,
        reason_codes=reason_codes,
        narrative=narrative,
        grounding=grounding,
        provider=provider.name,
        route=route,
    )
    return CoreResult(
        result=result,
        pack=pack,
        prompt=prompt,
        prompt_hash=prompt_hash,
        evidence_hash=evidence_hash,
    )


def triage_alert(
    alert_id: str,
    provider: Optional[LLMProvider] = None,
    db_path: Optional[str] = None,
    cfg: Optional[Config] = None,
) -> TriageResult:
    alert = db.fetch_alert(alert_id, db_path)
    if alert is None:
        raise AlertNotFound(alert_id)
    customer = db.fetch_customer(alert.customer_id, db_path)
    if customer is None:
        raise AlertNotFound(alert_id)
    txns = db.fetch_transactions_window(
        alert.customer_id, window_start(alert), alert.created_at, db_path
    )
    provider = provider or FakeProvider(mode=os.environ.get("SIEVE_FAKE_MODE", "normal"))
    core = triage_core(alert, customer, txns, provider, cfg)
    db.save_triage_result(core.result, core.prompt_hash, core.evidence_hash, db_path)
    db.insert_audit(
        event_type="triage",
        alert_id=alert_id,
        actor="system",
        provider=core.result.provider,
        prompt_hash=core.prompt_hash,
        evidence_hash=core.evidence_hash,
        disposition=core.result.disposition.value,
        route=core.result.route.value,
        override_flag=0,
        db_path=db_path,
    )
    return core.result


def window_start(alert: Alert) -> str:
    from datetime import datetime, timedelta

    created = datetime.fromisoformat(alert.created_at)
    return (created - timedelta(days=alert.lookback_days)).strftime("%Y-%m-%dT%H:%M:%S")


def triage_all(
    provider: LLMProvider,
    out_path: str,
    db_path: Optional[str] = None,
    cfg: Optional[Config] = None,
) -> int:
    cfg = cfg or load_config()
    alerts = db.fetch_all_alerts(db_path)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(out_path, "w", encoding="utf-8") as fh:
        for alert in alerts:
            result = triage_alert(alert.alert_id, provider=provider, db_path=db_path, cfg=cfg)
            fh.write(result.model_dump_json() + "\n")
            written += 1
    return written


def build_provider(name: str, fake_mode: str = "normal") -> LLMProvider:
    if name == "fake":
        return FakeProvider(mode=fake_mode)
    if name in ("real", "openai", "openai-compatible"):
        from sieve.providers.real import OpenAICompatibleProvider

        return OpenAICompatibleProvider.from_env()
    raise ValueError("unknown provider: %s" % name)


def main(argv: List[str] = None) -> None:
    parser = argparse.ArgumentParser(description="Triage AML alerts")
    parser.add_argument("--all", action="store_true", help="triage every alert")
    parser.add_argument("--alert-id", help="triage a single alert")
    parser.add_argument("--provider", default="fake", choices=["fake", "real", "openai-compatible"])
    parser.add_argument("--fake-mode", default="normal")
    parser.add_argument("--out", default="runs/fake.jsonl")
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)

    if not args.all and not args.alert_id:
        parser.error("pass --all or --alert-id")
    provider = build_provider(args.provider, args.fake_mode)
    cfg = load_config()

    if args.alert_id:
        result = triage_alert(args.alert_id, provider=provider, db_path=args.db, cfg=cfg)
        print(result.model_dump_json(indent=2))
        return
    written = triage_all(provider, args.out, db_path=args.db, cfg=cfg)
    print("wrote %d decisions to %s" % (written, args.out))


if __name__ == "__main__":
    main()
