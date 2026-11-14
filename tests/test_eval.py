from __future__ import annotations

import json
from pathlib import Path

from eval.run import run_eval


def test_eval_report_is_deterministic_and_complete(dataset, tmp_path):
    first_path = tmp_path / "report1.json"
    second_path = tmp_path / "report2.json"
    run_eval(
        provider_name="fake",
        db_path=dataset["db"],
        labels_path=dataset["labels_path"],
        report_path=str(first_path),
    )
    run_eval(
        provider_name="fake",
        db_path=dataset["db"],
        labels_path=dataset["labels_path"],
        report_path=str(second_path),
    )
    assert first_path.read_bytes() == second_path.read_bytes()

    report = json.loads(Path(first_path).read_text(encoding="utf-8"))
    assert set(report) >= {
        "split",
        "rules_only_threshold",
        "test",
        "per_scenario_test",
        "narrative_quality",
        "routing_test",
    }
    assert set(report["test"]) == {"rules_only", "rules_plus_llm"}
    for system in ("rules_only", "rules_plus_llm"):
        block = report["test"][system]
        assert {"precision", "recall", "f1", "confusion", "auto_close_rate"} <= set(block)
    assert len(report["per_scenario_test"]) == 3
    assert report["narrative_quality"]["citation_validity_rate"] == 1.0
    assert report["narrative_quality"]["numeric_faithfulness_rate"] == 1.0
    assert 0.0 <= report["routing_test"]["routed_recall"] <= 1.0
