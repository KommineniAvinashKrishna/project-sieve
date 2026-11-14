from __future__ import annotations

import argparse
import json
import random
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

from sieve import db
from sieve.models import Alert, Channel, Customer, Direction, Segment, Transaction


BASE_DATE = datetime(2026, 1, 5, 9, 0, 0)
SCENARIOS = ("CASH_STRUCTURING", "HIGH_RISK_COUNTRY", "INCOME_MISMATCH")
LOOKBACK_DAYS = {
    "CASH_STRUCTURING": 7,
    "HIGH_RISK_COUNTRY": 14,
    "INCOME_MISMATCH": 30,
}
HIGH_RISK_COUNTRIES = ["IR", "KP", "MM", "SY", "YE", "AF", "AE", "KY", "PA", "NG"]
JEWELLER_OCCUPATIONS = ("Jeweller", "Goldsmith", "Jewellery retailer")

OCCUPATIONS = {
    Segment.RETAIL: [
        "Teacher",
        "Software Engineer",
        "Doctor",
        "Shopkeeper",
        "Jeweller",
        "Goldsmith",
        "Nurse",
        "Consultant",
        "Analyst",
        "Retired teacher",
    ],
    Segment.SME: ["Importer", "Wholesale trader", "Exporter", "Jewellery retailer"],
    Segment.CORPORATE: ["Finance manager", "Procurement head"],
}

EMPLOYERS = {
    "Teacher": "Meridian Public School",
    "Software Engineer": "NovaTech Solutions",
    "Doctor": "Sunrise Multispeciality Hospital",
    "Shopkeeper": "Krishna General Store",
    "Jeweller": "Vaibhav Jewellers",
    "Goldsmith": "Vaibhav Jewellers",
    "Nurse": "Sunrise Multispeciality Hospital",
    "Consultant": "Sai Advisory Services",
    "Analyst": "NovaTech Solutions",
    "Retired teacher": "Self",
    "Importer": "Clearwater Imports Pvt Ltd",
    "Wholesale trader": "Sri Balaji Wholesale",
    "Exporter": "Sunrise Exports LLP",
    "Jewellery retailer": "Vaibhav Jewellers",
    "Finance manager": "Orion Capital Ltd",
    "Procurement head": "Clearwater Imports Pvt Ltd",
}

INDIVIDUAL_COUNTERPARTIES = [
    "Rajesh Kumar",
    "Meena Iyer",
    "Arjun Mehta",
    "Sunita Rao",
    "Vikram Singh",
    "Priya Nair",
    "Harish Gupta",
    "Anita Desai",
]

SUSPICIOUS_COUNTERPARTIES = [
    "Gulf Star Trading FZE",
    "Caspian Bridge Holdings",
    "Orchid Settlement Services",
    "Blue River Commerce Ltd",
]


class DatasetBuilder:
    def __init__(self, seed: int, n_customers: int, n_alerts: int):
        self.rng = random.Random(seed)
        self.seed = seed
        self.n_customers = n_customers
        self.n_alerts = n_alerts
        self.customers: List[Customer] = []
        self.transactions: List[Transaction] = []
        self.alerts: List[Alert] = []
        self.labels: List[Dict[str, object]] = []
        self._txn_counter = 0
        self._alert_counter = 0
        self._used_customers: set = set()

    def build(self) -> None:
        self._build_customers()
        self._build_baseline_transactions()
        self._build_alerts()

    def _build_customers(self) -> None:
        segments = (
            [Segment.RETAIL] * int(self.n_customers * 0.7)
            + [Segment.SME] * int(self.n_customers * 0.2)
            + [Segment.CORPORATE] * int(self.n_customers * 0.1)
        )
        segments = (segments + [Segment.RETAIL] * self.n_customers)[: self.n_customers]
        for i, segment in enumerate(segments, start=1):
            if segment == Segment.RETAIL and i % 5 == 0:
                occupation = "Jeweller" if i % 10 == 0 else "Goldsmith"
            elif segment == Segment.SME and i % 3 == 0:
                occupation = "Importer"
            else:
                occupation = self.rng.choice(OCCUPATIONS[segment])
            if occupation in JEWELLER_OCCUPATIONS:
                income = 800000 + (i * 7919) % 700000
            elif segment == Segment.RETAIL:
                income = 40000 + (i * 7919) % 160000
            elif segment == Segment.SME:
                income = 400000 + (i * 104729) % 1600000
            else:
                income = 800000 + (i * 1299709) % 4000000
            risk = "HIGH" if i % 12 == 0 else ("MEDIUM" if i % 3 == 0 else "LOW")
            kyc_date = (BASE_DATE - timedelta(days=200 + (i * 37) % 1800)).date().isoformat()
            self.customers.append(
                Customer(
                    customer_id="CUST-%05d" % i,
                    segment=segment,
                    occupation=occupation,
                    declared_monthly_income_inr=int(income),
                    employer_name=EMPLOYERS[occupation],
                    risk_rating=risk,
                    kyc_date=kyc_date,
                )
            )

    def _build_baseline_transactions(self) -> None:
        for cust in self.customers:
            if cust.employer_name != "Self":
                for days in (62, 32, 2):
                    self._add_txn(
                        cust,
                        _ts(BASE_DATE, days, 9),
                        cust.declared_monthly_income_inr,
                        Direction.CR,
                        Channel.NEFT,
                        cust.employer_name,
                        "IN",
                        "Salary credit - %s" % cust.employer_name,
                    )
            for days, amount in ((40, 500 + int(cust.customer_id[-4:]) % 4500), (10, 350)):
                self._add_txn(
                    cust,
                    _ts(BASE_DATE, days, 18),
                    amount,
                    Direction.DR,
                    Channel.UPI,
                    "Local Merchant",
                    "IN",
                    "UPI payment to local merchant",
                )

    def _build_alerts(self) -> None:
        per_scenario = max(1, self.n_alerts // len(SCENARIOS))
        for scenario_index, scenario in enumerate(SCENARIOS):
            plan = ["ambiguous"] * max(1, per_scenario // 10)
            remaining = per_scenario - len(plan)
            n_suspicious = int(round(remaining * 0.35))
            plan += ["suspicious"] * n_suspicious
            plan += ["benign"] * (remaining - n_suspicious)
            self.rng.shuffle(plan)
            benign_counter = 0
            for kind in plan:
                if scenario == "INCOME_MISMATCH" and kind == "benign":
                    benign_kind = "salary" if benign_counter % 2 == 0 else "property"
                    benign_counter += 1
                else:
                    benign_kind = ""
                self._build_alert(scenario, kind, benign_kind)

    def _build_alert(self, scenario: str, kind: str, benign_kind: str) -> None:
        self._alert_counter += 1
        alert_id = "ALERT-%06d" % self._alert_counter
        created_at = BASE_DATE + timedelta(
            days=self._alert_counter // 8, hours=(self._alert_counter % 8) * 2 + 1
        )
        created_ts = created_at.strftime("%Y-%m-%dT%H:%M:%S")
        cust = self._select_customer(scenario, kind, benign_kind)
        lookback = LOOKBACK_DAYS[scenario]
        self.alerts.append(
            Alert(
                alert_id=alert_id,
                customer_id=cust.customer_id,
                scenario_code=scenario,
                created_at=created_ts,
                lookback_days=lookback,
            )
        )
        reason = ""
        if scenario == "CASH_STRUCTURING":
            reason = self._cash_pattern(cust, created_at, kind)
        elif scenario == "HIGH_RISK_COUNTRY":
            reason = self._high_risk_pattern(cust, created_at, kind)
        else:
            reason = self._income_pattern(cust, created_at, kind, benign_kind)

        if kind == "ambiguous":
            label = "SUSPICIOUS" if self.rng.random() < 0.5 else "FALSE_POSITIVE"
            ambiguous = True
            reason = "ambiguous_mixed_signals"
        elif kind == "suspicious":
            label = "SUSPICIOUS"
            ambiguous = False
        else:
            label = "FALSE_POSITIVE"
            ambiguous = False
        self.labels.append(
            {
                "alert_id": alert_id,
                "label": label,
                "planted_reason": reason,
                "ambiguous": ambiguous,
            }
        )

    def _select_customer(self, scenario: str, kind: str, benign_kind: str) -> Customer:
        retail = [c for c in self.customers if c.segment == Segment.RETAIL]
        jewellers = [c for c in retail if c.occupation in JEWELLER_OCCUPATIONS]
        plain_retail = [c for c in retail if c.occupation not in JEWELLER_OCCUPATIONS]
        salaried = [c for c in retail if c.employer_name != "Self"]
        trade = [c for c in self.customers if c.segment in (Segment.SME, Segment.CORPORATE)]

        def unused(pool: List[Customer]) -> List[Customer]:
            return [c for c in pool if c.customer_id not in self._used_customers] or pool

        if scenario == "CASH_STRUCTURING":
            if kind == "suspicious":
                pool = unused(plain_retail)
            else:
                pool = unused(jewellers)
        elif scenario == "HIGH_RISK_COUNTRY":
            if kind == "benign":
                pool = unused(trade)
            elif kind == "ambiguous":
                pool = unused(plain_retail)
            else:
                pool = unused(plain_retail + trade)
        elif kind == "benign" and benign_kind == "salary":
            pool = unused(salaried)
        else:
            pool = unused(plain_retail)
        chosen = self.rng.choice(pool)
        self._used_customers.add(chosen.customer_id)
        return chosen

    def _cash_pattern(self, cust: Customer, created_at: datetime, kind: str) -> str:
        if kind == "benign":
            narrations = ["Festive counter sales", "Jewellery counter sales"]
            reason = "benign_jeweller_seasonal_cash"
        elif kind == "ambiguous":
            narrations = ["Cash sales"]
            reason = "ambiguous_mixed_signals"
        else:
            narrations = ["Cash deposit", "Cash received at branch", "Cash dep"]
            reason = "cash_structuring_pattern"
        n = 8 if kind == "ambiguous" else 6
        for i in range(n):
            amount = self.rng.randint(40000, 49500 if kind != "ambiguous" else 49900)
            self._add_txn(
                cust,
                _ts(created_at, 6 - (i % 6), 10 + (i % 4)),
                amount,
                Direction.CR,
                Channel.CASH,
                "Self",
                "IN",
                narrations[i % len(narrations)],
            )
        return reason

    def _high_risk_pattern(self, cust: Customer, created_at: datetime, kind: str) -> str:
        if kind == "benign":
            for i in range(2):
                self._add_txn(
                    cust,
                    _ts(created_at, 10 - i * 4, 11 + i),
                    self.rng.randint(300000, 1200000),
                    Direction.DR,
                    Channel.NEFT,
                    "Al Noor Trading LLC",
                    "AE",
                    "Payment against invoice INV-2026-00%d" % (41 + i),
                )
            return "benign_trade_invoice"
        countries = ["IR", "KP", "MM", "SY"]
        narrations = ["Consultancy services", "Advance for advisory services", "Service fee"]
        n = 2 if kind == "ambiguous" else 3
        for i in range(n):
            self._add_txn(
                cust,
                _ts(created_at, 8 - i * 3, 12 + i),
                self.rng.randint(300000, 1800000),
                Direction.CR if kind == "suspicious" else Direction.DR,
                Channel.SWIFT if kind == "suspicious" else Channel.NEFT,
                self.rng.choice(SUSPICIOUS_COUNTERPARTIES),
                countries[i % len(countries)] if kind == "suspicious" else "AE",
                narrations[i % len(narrations)]
                if kind == "suspicious"
                else "Payment against invoice INV-2026-10%d" % i,
            )
        return "high_risk_country_wire" if kind == "suspicious" else "ambiguous_mixed_signals"

    def _income_pattern(
        self, cust: Customer, created_at: datetime, kind: str, benign_kind: str
    ) -> str:
        income = cust.declared_monthly_income_inr
        if kind == "benign" and benign_kind == "salary":
            for i in range(2):
                self._add_txn(
                    cust,
                    _ts(created_at, 15 - i * 12, 9 + i),
                    income,
                    Direction.CR,
                    Channel.NEFT,
                    cust.employer_name,
                    "IN",
                    "Salary credit - %s" % cust.employer_name,
                )
            return "benign_salary_from_kyc_employer"
        if kind == "benign":
            self._add_txn(
                cust,
                _ts(created_at, 12, 15),
                min(5000000, income * self.rng.randint(8, 15)),
                Direction.CR,
                Channel.RTGS,
                "Rajesh Kumar",
                "IN",
                "Property sale - sale deed executed",
            )
            return "benign_property_sale_deed"
        if kind == "ambiguous":
            self._add_txn(
                cust,
                _ts(created_at, 11, 15),
                income * 7,
                Direction.CR,
                Channel.RTGS,
                "Skyline Developers Pvt Ltd",
                "IN",
                "Sale deed for ancestral property",
            )
            return "ambiguous_mixed_signals"
        channels = [Channel.NEFT, Channel.IMPS, Channel.UPI, Channel.RTGS]
        narrations = ["Settlement received", "Business receipt", "Loan repayment received"]
        for i in range(4):
            amount = min(5000000, income * self.rng.randint(6, 14))
            self._add_txn(
                cust,
                _ts(created_at, 22 - i * 6, 11 + i),
                amount,
                Direction.CR,
                channels[i],
                SUSPICIOUS_COUNTERPARTIES[i % len(SUSPICIOUS_COUNTERPARTIES)],
                "IN" if i % 2 == 0 else "AE",
                narrations[i % len(narrations)],
            )
        return "income_mismatch_unexplained"

    def _add_txn(
        self,
        cust: Customer,
        txn_ts: str,
        amount: int,
        direction: Direction,
        channel: Channel,
        counterparty_name: str,
        counterparty_country: str,
        narration: str,
    ) -> None:
        self._txn_counter += 1
        self.transactions.append(
            Transaction(
                txn_id="TXN-%06d" % self._txn_counter,
                customer_id=cust.customer_id,
                txn_ts=txn_ts,
                amount_inr=int(amount),
                direction=direction,
                channel=channel,
                counterparty_name=counterparty_name,
                counterparty_country=counterparty_country,
                narration=narration,
            )
        )


def _ts(base: datetime, days_before: int, hour: int) -> str:
    return (base - timedelta(days=days_before, hours=hour)).strftime("%Y-%m-%dT%H:%M:%S")


def generate_dataset(
    seed: int = 7,
    out_dir: str = "data",
    n_alerts: int = 360,
    n_customers: int = 720,
) -> Dict[str, object]:
    builder = DatasetBuilder(seed=seed, n_customers=n_customers, n_alerts=n_alerts)
    builder.build()

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    db_path = str(out_path / "sieve.db")
    db.reset_db(db_path)
    with closing(db.connect(db_path)) as conn:
        conn.executemany(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    c.customer_id,
                    c.segment.value,
                    c.occupation,
                    c.declared_monthly_income_inr,
                    c.employer_name,
                    c.risk_rating,
                    c.kyc_date,
                )
                for c in builder.customers
            ],
        )
        conn.executemany(
            "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    t.txn_id,
                    t.customer_id,
                    t.txn_ts,
                    t.amount_inr,
                    t.direction.value,
                    t.channel.value,
                    t.counterparty_name,
                    t.counterparty_country,
                    t.narration,
                )
                for t in builder.transactions
            ],
        )
        conn.executemany(
            "INSERT INTO alerts VALUES (?, ?, ?, ?, ?)",
            [
                (a.alert_id, a.customer_id, a.scenario_code, a.created_at, a.lookback_days)
                for a in builder.alerts
            ],
        )
        conn.commit()

    labels_path = out_path / "labels.jsonl"
    with labels_path.open("w", encoding="utf-8") as fh:
        for row in sorted(builder.labels, key=lambda r: str(r["alert_id"])):
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    suspicious = sum(1 for row in builder.labels if row["label"] == "SUSPICIOUS")
    ambiguous = sum(1 for row in builder.labels if row["ambiguous"])
    return {
        "db_path": db_path,
        "labels_path": str(labels_path),
        "customers": len(builder.customers),
        "transactions": len(builder.transactions),
        "alerts": len(builder.alerts),
        "suspicious": suspicious,
        "ambiguous": ambiguous,
    }


def main(argv: List[str] = None) -> None:
    parser = argparse.ArgumentParser(description="Generate the Project Sieve synthetic dataset")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out-dir", default="data")
    parser.add_argument("--alerts", type=int, default=360)
    parser.add_argument("--customers", type=int, default=720)
    args = parser.parse_args(argv)
    summary = generate_dataset(
        seed=args.seed, out_dir=args.out_dir, n_alerts=args.alerts, n_customers=args.customers
    )
    print("generated dataset:", json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
