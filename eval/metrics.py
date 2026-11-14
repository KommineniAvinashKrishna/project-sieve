from __future__ import annotations

from typing import Dict, List

POSITIVE = "SUSPICIOUS"
NEGATIVE = "FALSE_POSITIVE"


def binary_metrics(y_true: List[str], y_pred: List[str]) -> Dict[str, object]:
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must have the same length")
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == POSITIVE and p == POSITIVE)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == NEGATIVE and p == POSITIVE)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == POSITIVE and p == NEGATIVE)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == NEGATIVE and p == NEGATIVE)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "n": len(y_true),
    }
