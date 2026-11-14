from __future__ import annotations

from typing import Any, Dict, Optional

from sieve import db


class AlreadyReviewed(Exception):
    pass


class NotTriaged(Exception):
    pass


class CommentRequired(Exception):
    pass


def review_alert(
    alert_id: str,
    reviewer_id: str,
    decision: str,
    comment: str = "",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    alert = db.fetch_alert(alert_id, db_path)
    if alert is None:
        raise LookupError("alert not found")
    triage = db.fetch_triage_row(alert_id, db_path)
    if triage is None:
        raise NotTriaged("alert has not been triaged")
    if db.fetch_review(alert_id, db_path) is not None:
        raise AlreadyReviewed("alert has already been reviewed")

    override = decision != triage["disposition"]
    if override and not comment.strip():
        raise CommentRequired("a comment is required when overriding the system disposition")

    review = db.insert_review(
        alert_id=alert_id,
        reviewer_id=reviewer_id,
        decision=decision,
        comment=comment.strip(),
        override_flag=override,
        db_path=db_path,
    )
    db.insert_audit(
        event_type="review",
        alert_id=alert_id,
        actor=reviewer_id,
        provider=triage["provider"],
        prompt_hash=triage["prompt_hash"],
        evidence_hash=triage["evidence_hash"],
        disposition=decision,
        route=triage["route"],
        override_flag=int(override),
        db_path=db_path,
    )
    review["system_disposition"] = triage["disposition"]
    review["override"] = override
    return review
