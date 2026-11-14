from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "thresholds.json"


class Config:
    def __init__(self, data: Dict[str, Any]):
        self._data = data

    @property
    def rules(self) -> Dict[str, Dict[str, Any]]:
        return self._data["rules"]

    @property
    def routing(self) -> Dict[str, Any]:
        return self._data["routing"]

    @property
    def eval(self) -> Dict[str, Any]:
        return self._data["eval"]

    @property
    def threshold_grid(self) -> List[float]:
        return list(self._data["rules_only_threshold_grid"])

    def params(self, code: str) -> Dict[str, Any]:
        return self.rules[code]

    def weight(self, code: str) -> float:
        return float(self.rules[code]["weight"])


def load_config(path: str = None) -> Config:
    resolved = Path(path or os.environ.get("SIEVE_CONFIG") or DEFAULT_CONFIG_PATH)
    with resolved.open("r", encoding="utf-8") as fh:
        return Config(json.load(fh))
