from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sieve.models import Alert, Customer, Route, TriageResult, Transaction


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    segment TEXT NOT NULL,
    occupation TEXT NOT NULL,
    declared_monthly_income_inr INTEGER NOT NULL,
    employer_name TEXT NOT NULL,
    risk_rating TEXT NOT NULL,
    kyc_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    txn_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    txn_ts TEXT NOT NULL,
    amount_inr INTEGER NOT NULL,
    direction TEXT NOT NULL,
    channel TEXT NOT NULL,
    counterparty_name TEXT NOT NULL,
    counterparty_country TEXT NOT NULL,
    narration TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_txn_customer_ts ON transactions(customer_id, txn_ts);
CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    scenario_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    lookback_days INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS triage_results (
    alert_id TEXT PRIMARY KEY,
    disposition TEXT NOT NULL,
    confidence REAL NOT NULL,
    rule_score REAL NOT NULL,
    reason_codes_json TEXT NOT NULL,
    narrative_json TEXT NOT NULL,
    grounding_json TEXT NOT NULL,
    provider TEXT NOT NULL,
    route TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    triaged_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    alert_id TEXT PRIMARY KEY,
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT NOT NULL,
    override_flag INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    alert_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    provider TEXT,
    prompt_hash TEXT,
    evidence_hash TEXT,
    disposition TEXT,
    route TEXT,
    override_flag INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit
BEGIN
    SELECT RAISE(ABORT, 'audit table is append-only');
END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit
BEGIN
    SELECT RAISE(ABORT, 'audit table is append-only');
END;
"""

TABLES = ["customers", "transactions", "alerts", "triage_results", "reviews", "audit"]


def default_db_path() -> str:
    return os.environ.get("SIEVE_DB", "data/sieve.db")


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    with closing(connect(db_path)) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def reset_db(db_path: Optional[str] = None) -> None:
    with closing(connect(db_path)) as conn:
        for table in reversed(TABLES):
            conn.execute("DROP TABLE IF EXISTS %s" % table)
        conn.execute("DROP TRIGGER IF EXISTS audit_no_update")
        conn.execute("DROP TRIGGER IF EXISTS audit_no_delete")
        conn.executescript(SCHEMA)
        conn.commit()


def insert_customer(customer: Customer, db_path: Optional[str] = None) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                customer.customer_id,
                customer.segment.value,
                customer.occupation,
                customer.declared_monthly_income_inr,
                customer.employer_name,
                customer.risk_rating,
                customer.kyc_date,
            ),
        )
        conn.commit()


def insert_transaction(txn: Transaction, db_path: Optional[str] = None) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                txn.txn_id,
                txn.customer_id,
                txn.txn_ts,
                txn.amount_inr,
                txn.direction.value,
                txn.channel.value,
                txn.counterparty_name,
                txn.counterparty_country,
                txn.narration,
            ),
        )
        conn.commit()


def insert_alert(alert: Alert, db_path: Optional[str] = None) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO alerts VALUES (?, ?, ?, ?, ?)",
            (
                alert.alert_id,
                alert.customer_id,
                alert.scenario_code,
                alert.created_at,
                alert.lookback_days,
            ),
        )
        conn.commit()


def fetch_alert(alert_id: str, db_path: Optional[str] = None) -> Optional[Alert]:
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()
    return Alert(**dict(row)) if row else None


def fetch_customer(customer_id: str, db_path: Optional[str] = None) -> Optional[Customer]:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
    return Customer(**dict(row)) if row else None


def fetch_transactions_window(
    customer_id: str, start_ts: str, end_ts: str, db_path: Optional[str] = None
) -> List[Transaction]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE customer_id = ? AND txn_ts >= ? AND txn_ts <= ? "
            "ORDER BY txn_ts ASC, txn_id ASC",
            (customer_id, start_ts, end_ts),
        ).fetchall()
    return [Transaction(**dict(row)) for row in rows]


def fetch_all_alerts(db_path: Optional[str] = None) -> List[Alert]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM alerts ORDER BY alert_id ASC").fetchall()
    return [Alert(**dict(row)) for row in rows]


def fetch_all_customers(db_path: Optional[str] = None) -> Dict[str, Customer]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM customers").fetchall()
    return {row["customer_id"]: Customer(**dict(row)) for row in rows}


def fetch_all_transactions(db_path: Optional[str] = None) -> Dict[str, List[Transaction]]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM transactions ORDER BY txn_id ASC").fetchall()
    grouped: Dict[str, List[Transaction]] = {}
    for row in rows:
        txn = Transaction(**dict(row))
        grouped.setdefault(txn.customer_id, []).append(txn)
    return grouped


def count_rows(db_path: Optional[str] = None) -> Dict[str, int]:
    with closing(connect(db_path)) as conn:
        return {
            table: conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
            for table in TABLES
        }


def save_triage_result(
    result: TriageResult,
    prompt_hash: str,
    evidence_hash: str,
    db_path: Optional[str] = None,
) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO triage_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.alert_id,
                result.disposition.value,
                result.confidence,
                result.rule_score,
                json.dumps([r.model_dump(mode="json") for r in result.reason_codes], sort_keys=True),
                json.dumps([s.model_dump(mode="json") for s in result.narrative], sort_keys=True),
                json.dumps(result.grounding.model_dump(mode="json"), sort_keys=True),
                result.provider,
                result.route.value,
                prompt_hash,
                evidence_hash,
                _now(),
            ),
        )
        conn.commit()


def fetch_triage_row(alert_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM triage_results WHERE alert_id = ?", (alert_id,)
        ).fetchone()
    return dict(row) if row else None


def insert_audit(
    event_type: str,
    alert_id: str,
    actor: str,
    provider: Optional[str],
    prompt_hash: Optional[str],
    evidence_hash: Optional[str],
    disposition: Optional[str],
    route: Optional[str],
    override_flag: int = 0,
    db_path: Optional[str] = None,
) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO audit (ts, event_type, alert_id, actor, provider, prompt_hash, "
            "evidence_hash, disposition, route, override_flag) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now(),
                event_type,
                alert_id,
                actor,
                provider,
                prompt_hash,
                evidence_hash,
                disposition,
                route,
                override_flag,
            ),
        )
        conn.commit()


def fetch_audit_rows(alert_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM audit WHERE alert_id = ? ORDER BY id ASC", (alert_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_review(alert_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM reviews WHERE alert_id = ?", (alert_id,)).fetchone()
    return dict(row) if row else None


def insert_review(
    alert_id: str,
    reviewer_id: str,
    decision: str,
    comment: str,
    override_flag: bool,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    reviewed_at = _now()
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?)",
            (alert_id, reviewer_id, decision, comment, reviewed_at, int(override_flag)),
        )
        conn.commit()
    return {
        "alert_id": alert_id,
        "reviewer_id": reviewer_id,
        "decision": decision,
        "comment": comment,
        "reviewed_at": reviewed_at,
        "override": override_flag,
    }


def fetch_queue(route: Route, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT t.alert_id, t.disposition, t.confidence, t.rule_score, t.route, "
            "t.provider, t.triaged_at FROM triage_results t "
            "LEFT JOIN reviews r ON r.alert_id = t.alert_id "
            "WHERE t.route = ? AND r.alert_id IS NULL "
            "ORDER BY t.rule_score DESC, t.alert_id ASC",
            (route.value,),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_metrics_data(db_path: Optional[str] = None) -> Dict[str, int]:
    with closing(connect(db_path)) as conn:
        total = conn.execute("SELECT COUNT(*) FROM triage_results").fetchone()[0]
        routes = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT route, COUNT(*) FROM triage_results GROUP BY route"
            ).fetchall()
        }
        reviews_total = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        overrides = conn.execute(
            "SELECT COUNT(*) FROM reviews WHERE override_flag = 1"
        ).fetchone()[0]
        reviewed = conn.execute(
            "SELECT COUNT(*) FROM triage_results t JOIN reviews r ON r.alert_id = t.alert_id"
        ).fetchone()[0]
    return {
        "triaged": total,
        "auto_close": routes.get(Route.AUTO_CLOSE.value, 0),
        "qa_sample": routes.get(Route.QA_SAMPLE.value, 0),
        "human_review": routes.get(Route.HUMAN_REVIEW.value, 0),
        "reviews": reviews_total,
        "overrides": overrides,
        "reviewed": reviewed,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
