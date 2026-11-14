from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from eval.metrics import binary_metrics
from eval.split import stratified_split
from sieve import db
from sieve.config import Config, load_config
from sieve.models import Disposition, Route, TriageResult
from sieve.triage import build_provider, triage_core


def load_labels(path: str) -> Dict[str, Dict[str, object]]:
    labels: Dict[str, Dict[str, object]] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            labels[row["alert_id"]] = row
    return labels


def pick_threshold(
    scores: Dict[str, float],
    labels: Dict[str, Dict[str, object]],
    ids: Sequence[str],
    grid: Sequence[float],
) -> float:
    best_key = None
    best_threshold = grid[0]
    for threshold in sorted(grid):
        y_pred = [
            "SUSPICIOUS" if scores[alert_id] >= threshold else "FALSE_POSITIVE"
            for alert_id in ids
        ]
        y_true = [labels[alert_id]["label"] for alert_id in ids]
        metrics = binary_metrics(y_true, y_pred)
        key = (metrics["recall"], metrics["f1"], threshold)
        if best_key is None or key > best_key:
            best_key = key
            best_threshold = threshold
    return best_threshold


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _system_block(
    ids: Sequence[str],
    labels: Dict[str, Dict[str, object]],
    predicted_positive: Dict[str, bool],
    closed_without_human: Dict[str, bool],
) -> Dict[str, object]:
    y_true = [labels[alert_id]["label"] for alert_id in ids]
    y_pred = ["SUSPICIOUS" if predicted_positive[alert_id] else "FALSE_POSITIVE" for alert_id in ids]
    block = binary_metrics(y_true, y_pred)
    block["auto_close_rate"] = _rate(
        sum(1 for alert_id in ids if closed_without_human[alert_id]), len(ids)
    )
    return block


def _routed_recall(
    ids: Sequence[str],
    labels: Dict[str, Dict[str, object]],
    predicted_positive: Dict[str, bool],
    closed_without_human: Dict[str, bool],
) -> float:
    suspicious = [alert_id for alert_id in ids if labels[alert_id]["label"] == "SUSPICIOUS"]
    caught = sum(
        1
        for alert_id in suspicious
        if predicted_positive[alert_id] or not closed_without_human[alert_id]
    )
    return _rate(caught, len(suspicious))


def run_eval(
    provider_name: str = "fake",
    db_path: Optional[str] = None,
    labels_path: str = "data/labels.jsonl",
    report_path: str = "eval/report.json",
    config: Optional[Config] = None,
) -> Dict[str, object]:
    cfg = config or load_config()
    alerts = db.fetch_all_alerts(db_path)
    labels = load_labels(labels_path)
    missing = [alert.alert_id for alert in alerts if alert.alert_id not in labels]
    if missing:
        raise SystemExit("labels missing for %d alerts; run sieve.generate first" % len(missing))

    customers = db.fetch_all_customers(db_path)
    transactions = db.fetch_all_transactions(db_path)
    provider = build_provider(provider_name)

    results: Dict[str, TriageResult] = {}
    for alert in alerts:
        core = triage_core(
            alert,
            customers[alert.customer_id],
            transactions.get(alert.customer_id, []),
            provider,
            cfg,
        )
        results[alert.alert_id] = core.result

    seed = int(cfg.eval["seed"])
    dev_fraction = float(cfg.eval["dev_fraction"])
    dev_ids, test_ids = stratified_split(alerts, seed=seed, dev_fraction=dev_fraction)

    scores = {alert_id: result.rule_score for alert_id, result in results.items()}
    threshold = pick_threshold(scores, labels, dev_ids, cfg.threshold_grid)

    predicted_positive_rules = {
        alert_id: scores[alert_id] >= threshold for alert_id in results
    }
    predicted_positive_llm = {
        alert_id: results[alert_id].disposition == Disposition.ESCALATE
        for alert_id in results
    }
    closed_without_human_rules = {
        alert_id: not predicted_positive_rules[alert_id] for alert_id in results
    }
    closed_without_human_llm = {
        alert_id: results[alert_id].route != Route.HUMAN_REVIEW for alert_id in results
    }

    by_scenario_dev: Dict[str, int] = defaultdict(int)
    by_scenario_test: Dict[str, int] = defaultdict(int)
    scenario_of = {alert.alert_id: alert.scenario_code for alert in alerts}
    for alert_id in dev_ids:
        by_scenario_dev[scenario_of[alert_id]] += 1
    for alert_id in test_ids:
        by_scenario_test[scenario_of[alert_id]] += 1

    report: Dict[str, object] = {
        "config": {
            "seed": seed,
            "dev_fraction": dev_fraction,
            "threshold_grid": list(cfg.threshold_grid),
            "provider": provider_name,
        },
        "split": {
            "dev": len(dev_ids),
            "test": len(test_ids),
            "by_scenario_dev": dict(sorted(by_scenario_dev.items())),
            "by_scenario_test": dict(sorted(by_scenario_test.items())),
        },
        "rules_only_threshold": {
            "value": threshold,
            "tuned_on": "dev",
            "objective": "max recall, then f1, then higher threshold",
        },
        "dev": {
            "rules_only": _system_block(
                dev_ids, labels, predicted_positive_rules, closed_without_human_rules
            ),
            "rules_plus_llm": _system_block(
                dev_ids, labels, predicted_positive_llm, closed_without_human_llm
            ),
        },
        "test": {
            "rules_only": _system_block(
                test_ids, labels, predicted_positive_rules, closed_without_human_rules
            ),
            "rules_plus_llm": _system_block(
                test_ids, labels, predicted_positive_llm, closed_without_human_llm
            ),
        },
        "per_scenario_test": {},
        "narrative_quality": {},
        "routing_test": {},
    }

    scenarios = sorted({scenario_of[alert_id] for alert_id in test_ids})
    for scenario in scenarios:
        ids = [alert_id for alert_id in test_ids if scenario_of[alert_id] == scenario]
        report["per_scenario_test"][scenario] = {
            "n": len(ids),
            "rules_only": _system_block(ids, labels, predicted_positive_rules, closed_without_human_rules),
            "rules_plus_llm": _system_block(ids, labels, predicted_positive_llm, closed_without_human_llm),
        }

    citation_ok = 0
    numeric_ok = 0
    citation_errors = 0
    numeric_errors = 0
    for alert_id in test_ids:
        result = results[alert_id]
        errors = result.grounding.errors
        # A provider failure leaves no narrative to verify, so it counts against both rates.
        provider_fail = any(error.startswith("PROVIDER") for error in errors)
        cite_fail = provider_fail or any(error.startswith("CITATION") for error in errors)
        number_fail = provider_fail or any(error.startswith("NUMERIC") for error in errors)
        if cite_fail:
            citation_errors += 1
        else:
            citation_ok += 1
        if number_fail:
            numeric_errors += 1
        else:
            numeric_ok += 1
    report["narrative_quality"] = {
        "narratives": len(test_ids),
        "citation_validity_rate": _rate(citation_ok, len(test_ids)),
        "numeric_faithfulness_rate": _rate(numeric_ok, len(test_ids)),
        "narratives_with_citation_errors": citation_errors,
        "narratives_with_numeric_errors": numeric_errors,
    }

    routes = {route.value: 0 for route in Route}
    for alert_id in test_ids:
        routes[results[alert_id].route.value] += 1
    report["routing_test"] = {
        "auto_close": routes["AUTO_CLOSE"],
        "qa_sample": routes["QA_SAMPLE"],
        "human_review": routes["HUMAN_REVIEW"],
        "auto_close_rate": _rate(routes["AUTO_CLOSE"] + routes["QA_SAMPLE"], len(test_ids)),
        "human_review_rate": _rate(routes["HUMAN_REVIEW"], len(test_ids)),
        "qa_sample_rate": _rate(routes["QA_SAMPLE"], len(test_ids)),
        "routed_recall": _routed_recall(
            test_ids, labels, predicted_positive_llm, closed_without_human_llm
        ),
    }

    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    _print_summary(report)
    return report


def _print_summary(report: Dict[str, object]) -> None:
    test = report["test"]
    print(
        "Project Sieve evaluation | dev=%d test=%d | rules-only threshold=%.2f"
        % (
            report["split"]["dev"],
            report["split"]["test"],
            report["rules_only_threshold"]["value"],
        )
    )
    print("%-16s %9s %7s %7s %7s %10s" % ("system", "precision", "recall", "f1", "n", "auto-close"))
    for name, key in (("rules-only", "rules_only"), ("rules+LLM", "rules_plus_llm")):
        block = test[key]
        print(
            "%-16s %9.3f %7.3f %7.3f %7d %10.3f"
            % (
                name,
                block["precision"],
                block["recall"],
                block["f1"],
                block["n"],
                block["auto_close_rate"],
            )
        )
    print("\nPer scenario (test):")
    for scenario, block in sorted(report["per_scenario_test"].items()):
        rules = block["rules_only"]
        llm = block["rules_plus_llm"]
        print(
            "%-20s n=%3d | rules R=%.2f P=%.2f | llm R=%.2f P=%.2f"
            % (
                scenario,
                block["n"],
                rules["recall"],
                rules["precision"],
                llm["recall"],
                llm["precision"],
            )
        )
    quality = report["narrative_quality"]
    routing = report["routing_test"]
    print(
        "\nNarratives: citation validity=%.3f numeric faithfulness=%.3f"
        % (quality["citation_validity_rate"], quality["numeric_faithfulness_rate"])
    )
    print(
        "Routing: auto-close=%.3f human-review=%.3f routed recall=%.3f"
        % (routing["auto_close_rate"], routing["human_review_rate"], routing["routed_recall"])
    )


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate rules-only against rules + LLM")
    parser.add_argument("--provider", default="fake", choices=["fake", "real", "openai-compatible"])
    parser.add_argument("--db", default=None)
    parser.add_argument("--labels", default="data/labels.jsonl")
    parser.add_argument("--report", default="eval/report.json")
    args = parser.parse_args(argv)
    run_eval(
        provider_name=args.provider,
        db_path=args.db,
        labels_path=args.labels,
        report_path=args.report,
    )


if __name__ == "__main__":
    main()
