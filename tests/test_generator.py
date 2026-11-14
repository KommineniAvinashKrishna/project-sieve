from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from sieve.generate import generate_dataset


def test_generation_is_deterministic(tmp_path):
    first = generate_dataset(seed=7, out_dir=str(tmp_path / "run1"), n_alerts=360, n_customers=720)
    second = generate_dataset(seed=7, out_dir=str(tmp_path / "run2"), n_alerts=360, n_customers=720)

    labels_one = Path(first["labels_path"]).read_bytes()
    labels_two = Path(second["labels_path"]).read_bytes()
    assert labels_one == labels_two

    for key in ("customers", "transactions", "alerts"):
        assert first[key] == second[key]
    counts = []
    for summary in (first, second):
        conn = sqlite3.connect(summary["db_path"])
        counts.append(
            tuple(
                conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
                for table in ("customers", "transactions", "alerts")
            )
        )
        conn.close()
    assert counts[0] == counts[1]


def test_dataset_shape_and_labels(dataset):
    labels = list(dataset["labels"].values())
    assert len(labels) >= 300

    scenarios = {row["alert_id"] for row in labels}
    assert len(scenarios) == len(labels)

    suspicious = sum(1 for row in labels if row["label"] == "SUSPICIOUS")
    ratio = suspicious / len(labels)
    assert 0.30 <= ratio <= 0.45

    ambiguous = sum(1 for row in labels if row["ambiguous"])
    assert 0.08 <= ambiguous / len(labels) <= 0.12

    reasons = Counter(row["planted_reason"] for row in labels)
    benign_reasons = {
        "benign_jeweller_seasonal_cash",
        "benign_trade_invoice",
        "benign_salary_from_kyc_employer",
        "benign_property_sale_deed",
    }
    assert benign_reasons <= set(reasons)
    for row in labels:
        if row["label"] == "FALSE_POSITIVE" and not row["ambiguous"]:
            assert row["planted_reason"].startswith("benign_")


def test_labels_file_is_read_only_by_generator_and_eval():
    root = Path(__file__).resolve().parent.parent
    referenced = set()
    for package in ("sieve", "eval"):
        for path in (root / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "labels.jsonl" in text or "load_labels(" in text:
                referenced.add(path)
    assert referenced == {root / "sieve" / "generate.py", root / "eval" / "run.py"}

    import_pattern = re.compile(r"^\s*(?:from|import)\s+eval\b", re.MULTILINE)
    for path in (root / "sieve").rglob("*.py"):
        assert not import_pattern.search(path.read_text(encoding="utf-8"))
