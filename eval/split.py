from __future__ import annotations

import random
from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

from sieve.models import Alert


def stratified_split(
    alerts: Sequence[Alert], seed: int, dev_fraction: float = 0.4
) -> Tuple[List[str], List[str]]:
    by_scenario: Dict[str, List[str]] = defaultdict(list)
    for alert in sorted(alerts, key=lambda a: a.alert_id):
        by_scenario[alert.scenario_code].append(alert.alert_id)

    dev: List[str] = []
    test: List[str] = []
    for scenario in sorted(by_scenario):
        ids = list(by_scenario[scenario])
        rng = random.Random("%s:%s" % (seed, scenario))
        rng.shuffle(ids)
        n_dev = int(round(len(ids) * dev_fraction))
        dev.extend(ids[:n_dev])
        test.extend(ids[n_dev:])
    return sorted(dev), sorted(test)
