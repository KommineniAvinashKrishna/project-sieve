from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sieve.api import create_app
from sieve.providers.fake import FakeProvider


def test_triage_response_shape_and_citations_resolve(client):
    response = client.post("/alerts/ALERT-000001/triage")
    assert response.status_code == 200
    body = response.json()
    assert body["alert_id"] == "ALERT-000001"
    assert body["disposition"] in ("ESCALATE", "CLOSE_FALSE_POSITIVE")
    assert 0.0 <= body["confidence"] <= 1.0
    assert 0.0 <= body["rule_score"] <= 1.0
    assert body["provider"] == "fake"
    assert body["route"] in ("AUTO_CLOSE", "HUMAN_REVIEW", "QA_SAMPLE")
    assert body["grounding"] == {"passed": True, "errors": []}
    assert body["narrative"]

    evidence = client.get("/alerts/ALERT-000001/evidence").json()
    evidence_ids = {item["id"] for item in evidence["items"]}
    assert "KYC.occupation" in evidence_ids
    assert any(item_id.startswith("TXN-") for item_id in evidence_ids)
    assert any(item_id.startswith("F.") for item_id in evidence_ids)
    for sentence in body["narrative"]:
        assert sentence["citations"]
        assert set(sentence["citations"]) <= evidence_ids


def test_unknown_alert_returns_404(client):
    assert client.post("/alerts/ALERT-999999/triage").status_code == 404
    assert client.get("/alerts/ALERT-999999/evidence").status_code == 404


@pytest.mark.parametrize(
    "mode,error_prefix",
    [
        ("uncited", "CITATION"),
        ("foreign_citation", "CITATION"),
        ("malformed_json", "PROVIDER"),
        ("bad_amount", "NUMERIC"),
    ],
)
def test_faulty_provider_output_escalates_without_500(db_path, mode, error_prefix):
    client = TestClient(create_app(db_path=db_path, provider=FakeProvider(mode=mode)))
    response = client.post("/alerts/ALERT-000001/triage")
    assert response.status_code == 200
    body = response.json()
    assert body["disposition"] == "ESCALATE"
    assert body["grounding"]["passed"] is False
    assert any(error.startswith(error_prefix) for error in body["grounding"]["errors"])


def test_queue_is_sorted_and_filtered(client):
    for index in range(1, 16):
        assert client.post("/alerts/ALERT-%06d/triage" % index).status_code == 200
    queue = client.get("/queue", params={"route": "HUMAN_REVIEW"}).json()
    assert queue
    scores = [row["rule_score"] for row in queue]
    assert scores == sorted(scores, reverse=True)
    assert all(row["route"] == "HUMAN_REVIEW" for row in queue)
    assert client.get("/queue", params={"route": "AUTO_CLOSE"}).status_code == 422
