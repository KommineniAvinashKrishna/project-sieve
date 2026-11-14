from __future__ import annotations

from eval.metrics import binary_metrics


def test_binary_metrics_on_ten_item_prediction_set():
    y_true = [
        "SUSPICIOUS",
        "SUSPICIOUS",
        "SUSPICIOUS",
        "SUSPICIOUS",
        "SUSPICIOUS",
        "FALSE_POSITIVE",
        "FALSE_POSITIVE",
        "FALSE_POSITIVE",
        "FALSE_POSITIVE",
        "FALSE_POSITIVE",
    ]
    y_pred = [
        "SUSPICIOUS",
        "SUSPICIOUS",
        "SUSPICIOUS",
        "FALSE_POSITIVE",
        "FALSE_POSITIVE",
        "SUSPICIOUS",
        "FALSE_POSITIVE",
        "FALSE_POSITIVE",
        "FALSE_POSITIVE",
        "FALSE_POSITIVE",
    ]
    metrics = binary_metrics(y_true, y_pred)
    assert metrics["confusion"] == {"tp": 3, "fp": 1, "fn": 2, "tn": 4}
    assert metrics["precision"] == 0.75
    assert metrics["recall"] == 0.6
    assert metrics["f1"] == 0.6667
    assert metrics["n"] == 10


def test_binary_metrics_handles_empty_positives():
    metrics = binary_metrics(["FALSE_POSITIVE"], ["FALSE_POSITIVE"])
    assert metrics["precision"] == 0.0
    assert metrics["recall"] == 0.0
    assert metrics["f1"] == 0.0
