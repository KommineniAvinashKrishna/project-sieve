from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from sieve import db
from sieve.api import create_app
from sieve.config import load_config
from sieve.evidence import build_evidence_pack
from sieve.models import Disposition, Route
from sieve.providers.fake import FakeProvider
from sieve.routing import decide_route
from sieve.rules import evaluate_rules


def _alert_with_rule_score_at_least(dataset, minimum=0.5):
    cfg = load_config()
    dbp = dataset["db"]
    customers = db.fetch_all_customers(dbp)
    transactions = db.fetch_all_transactions(dbp)
    for alert in db.fetch_all_alerts(dbp):
        pack = build_evidence_pack(
            alert, customers[alert.customer_id], transactions[alert.customer_id], cfg
        )
        score, _ = evaluate_rules(pack, cfg)
        if score >= minimum:
            return alert.alert_id
    raise AssertionError("no alert with rule score >= %.2f" % minimum)


def test_overconfident_close_above_rule_floor_goes_to_human(dataset, db_path):
    alert_id = _alert_with_rule_score_at_least(dataset, 0.5)
    client = TestClient(create_app(db_path=db_path, provider=FakeProvider("overconfident")))
    body = client.post("/alerts/%s/triage" % alert_id).json()
    assert body["disposition"] == "CLOSE_FALSE_POSITIVE"
    assert body["confidence"] == 0.99
    assert body["rule_score"] >= 0.5
    assert body["route"] == "HUMAN_REVIEW"


def test_routing_policy_boundaries():
    cfg = load_config()
    floor = float(cfg.routing["rule_score_floor"])
    min_conf = float(cfg.routing["min_confidence"])
    auto = decide_route(Disposition.CLOSE_FALSE_POSITIVE, min_conf, True, floor - 0.01, "ALERT-X", cfg)
    assert auto in (Route.AUTO_CLOSE, Route.QA_SAMPLE)
    assert decide_route(Disposition.CLOSE_FALSE_POSITIVE, min_conf - 0.01, True, 0.0, "ALERT-X", cfg) == Route.HUMAN_REVIEW
    assert decide_route(Disposition.CLOSE_FALSE_POSITIVE, min_conf, False, 0.0, "ALERT-X", cfg) == Route.HUMAN_REVIEW
    assert decide_route(Disposition.CLOSE_FALSE_POSITIVE, min_conf, True, floor, "ALERT-X", cfg) == Route.HUMAN_REVIEW
    assert decide_route(Disposition.ESCALATE, 0.99, True, 0.0, "ALERT-X", cfg) == Route.HUMAN_REVIEW


def test_qa_sample_is_deterministic_and_about_ten_percent():
    cfg = load_config()
    ids = ["ALERT-%06d" % i for i in range(1, 2001)]

    def sampled_ids():
        return [
            alert_id
            for alert_id in ids
            if decide_route(
                Disposition.CLOSE_FALSE_POSITIVE,
                float(cfg.routing["min_confidence"]),
                True,
                float(cfg.routing["rule_score_floor"]) - 0.01,
                alert_id,
                cfg,
            )
            == Route.QA_SAMPLE
        ]

    first = sampled_ids()
    second = sampled_ids()
    assert first == second
    assert 0.07 <= len(first) / len(ids) <= 0.13


def test_review_flow_comment_409_and_audit_rows(client, db_path):
    body = client.post("/alerts/ALERT-000001/triage").json()
    system = body["disposition"]
    override = "ESCALATE" if system == "CLOSE_FALSE_POSITIVE" else "CLOSE_FALSE_POSITIVE"

    missing_comment = client.post(
        "/alerts/ALERT-000001/review",
        json={"reviewer_id": "rev-1", "decision": override, "comment": ""},
    )
    assert missing_comment.status_code == 422

    reviewed = client.post(
        "/alerts/ALERT-000001/review",
        json={"reviewer_id": "rev-1", "decision": override, "comment": "Evidence reviewed"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["override"] is True
    assert reviewed.json()["system_disposition"] == system

    duplicate = client.post(
        "/alerts/ALERT-000001/review",
        json={"reviewer_id": "rev-1", "decision": override, "comment": "Again"},
    )
    assert duplicate.status_code == 409

    rows = db.fetch_audit_rows("ALERT-000001", db_path)
    assert [row["event_type"] for row in rows] == ["triage", "review"]
    assert rows[0]["actor"] == "system"
    assert rows[1]["actor"] == "rev-1"
    assert rows[1]["override_flag"] == 1
    assert rows[0]["prompt_hash"] and rows[0]["evidence_hash"]
    assert rows[1]["prompt_hash"] == rows[0]["prompt_hash"]

    queue = client.get("/queue", params={"route": "HUMAN_REVIEW"}).json()
    assert "ALERT-000001" not in {row["alert_id"] for row in queue}


def test_review_without_override_needs_no_comment_and_metrics(client):
    body = client.post("/alerts/ALERT-000002/triage").json()
    same = client.post(
        "/alerts/ALERT-000002/review",
        json={"reviewer_id": "rev-2", "decision": body["disposition"], "comment": ""},
    )
    assert same.status_code == 200
    assert same.json()["override"] is False

    metrics = client.get("/metrics").json()
    assert metrics["alerts_triaged"] == 1
    assert metrics["counts"]["reviews"] == 1
    assert metrics["reviewer_override_rate"] == 0.0
    assert 0.0 <= metrics["auto_close_rate"] <= 1.0
    assert 0.0 <= metrics["human_review_rate"] <= 1.0


def test_audit_table_rejects_updates_and_deletes(client, db_path):
    client.post("/alerts/ALERT-000003/triage")
    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE audit SET actor = 'tamper'")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM audit")
    conn.close()
