from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from sieve import db
from sieve.config import Config, load_config
from sieve.evidence import build_evidence_pack
from sieve.models import Disposition, EvidencePack, Route, TriageResult
from sieve.providers import provider_from_env
from sieve.providers.base import LLMProvider
from sieve.review import AlreadyReviewed, CommentRequired, NotTriaged, review_alert
from sieve.triage import AlertNotFound, triage_alert, window_start


class ReviewRequest(BaseModel):
    reviewer_id: str = Field(min_length=1)
    decision: Disposition
    comment: str = ""


class QueueRoute(str, Enum):
    HUMAN_REVIEW = "HUMAN_REVIEW"
    QA_SAMPLE = "QA_SAMPLE"


def create_app(
    db_path: Optional[str] = None,
    provider: Optional[LLMProvider] = None,
    config: Optional[Config] = None,
) -> FastAPI:
    cfg = config or load_config()
    resolved_db = db_path or db.default_db_path()
    db.init_db(resolved_db)

    app = FastAPI(title="Project Sieve", version="1.0.0")
    app.state.db_path = resolved_db
    app.state.provider = provider
    app.state.config = cfg

    def get_provider() -> LLMProvider:
        return app.state.provider or provider_from_env()

    @app.post("/alerts/{alert_id}/triage", response_model=TriageResult)
    def triage(alert_id: str) -> TriageResult:
        try:
            return triage_alert(
                alert_id, provider=get_provider(), db_path=resolved_db, cfg=cfg
            )
        except AlertNotFound:
            raise HTTPException(status_code=404, detail="alert not found")

    @app.get("/alerts/{alert_id}/evidence", response_model=EvidencePack)
    def evidence(alert_id: str) -> EvidencePack:
        alert = db.fetch_alert(alert_id, resolved_db)
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        customer = db.fetch_customer(alert.customer_id, resolved_db)
        if customer is None:
            raise HTTPException(status_code=404, detail="alert not found")
        txns = db.fetch_transactions_window(
            alert.customer_id, window_start(alert), alert.created_at, resolved_db
        )
        return build_evidence_pack(alert, customer, txns, cfg)

    @app.get("/queue")
    def queue(route: QueueRoute = Query(..., description="HUMAN_REVIEW or QA_SAMPLE")) -> List[Dict[str, Any]]:
        return db.fetch_queue(Route(route.value), resolved_db)

    @app.post("/alerts/{alert_id}/review")
    def review(alert_id: str, request: ReviewRequest) -> Dict[str, Any]:
        try:
            return review_alert(
                alert_id=alert_id,
                reviewer_id=request.reviewer_id,
                decision=request.decision.value,
                comment=request.comment,
                db_path=resolved_db,
            )
        except LookupError:
            raise HTTPException(status_code=404, detail="alert not found")
        except (AlreadyReviewed, NotTriaged) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except CommentRequired as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/metrics")
    def metrics() -> Dict[str, Any]:
        data = db.fetch_metrics_data(resolved_db)
        total = data["triaged"]
        reviews = data["reviews"]
        if total == 0:
            auto_close_rate = human_review_rate = qa_sample_rate = 0.0
        else:
            auto_close_rate = round((data["auto_close"] + data["qa_sample"]) / total, 4)
            human_review_rate = round(data["human_review"] / total, 4)
            qa_sample_rate = round(data["qa_sample"] / total, 4)
        override_rate = round(data["overrides"] / reviews, 4) if reviews else 0.0
        return {
            "alerts_triaged": total,
            "auto_close_rate": auto_close_rate,
            "human_review_rate": human_review_rate,
            "qa_sample_rate": qa_sample_rate,
            "reviewer_override_rate": override_rate,
            "counts": data,
        }

    return app


app = create_app()
