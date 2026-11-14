from __future__ import annotations

import json
import shutil

import pytest

from sieve.generate import generate_dataset


@pytest.fixture(scope="session")
def dataset(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("sieve_data")
    summary = generate_dataset(seed=7, out_dir=str(out_dir), n_alerts=360, n_customers=720)
    labels = {}
    with open(summary["labels_path"], "r", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            labels[row["alert_id"]] = row
    return {
        "db": summary["db_path"],
        "labels": labels,
        "labels_path": summary["labels_path"],
        "summary": summary,
    }


@pytest.fixture
def db_path(tmp_path, dataset):
    target = tmp_path / "sieve.db"
    shutil.copy(dataset["db"], target)
    return str(target)


@pytest.fixture
def client(db_path):
    from fastapi.testclient import TestClient

    from sieve.api import create_app
    from sieve.providers.fake import FakeProvider

    return TestClient(create_app(db_path=db_path, provider=FakeProvider()))
