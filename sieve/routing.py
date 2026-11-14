from __future__ import annotations

import hashlib
from typing import Any, Dict

from sieve.config import Config
from sieve.models import Disposition, Route


def is_qa_sample(alert_id: str, cfg: Config) -> bool:
    routing = cfg.routing
    digest = hashlib.sha256((alert_id + str(routing["qa_salt"])).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100 < int(routing["qa_sample_pct"])


def decide_route(
    disposition: Disposition,
    confidence: float,
    grounding_passed: bool,
    rule_score: float,
    alert_id: str,
    cfg: Config,
) -> Route:
    routing: Dict[str, Any] = cfg.routing
    auto_close_eligible = (
        disposition == Disposition.CLOSE_FALSE_POSITIVE
        and grounding_passed
        and confidence >= float(routing["min_confidence"])
        and rule_score < float(routing["rule_score_floor"])
    )
    if not auto_close_eligible:
        return Route.HUMAN_REVIEW
    if is_qa_sample(alert_id, cfg):
        return Route.QA_SAMPLE
    return Route.AUTO_CLOSE
