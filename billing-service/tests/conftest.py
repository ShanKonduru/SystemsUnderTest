from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from billing.api import create_app
from billing.config import Settings, load_settings
from billing.rates import RateTable, load_rates

TOKEN = "test-sandbox-token"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return replace(load_settings({}), data_dir=tmp_path / "data", sandbox_token=TOKEN)


@pytest.fixture
def rates(settings: Settings) -> RateTable:
    return load_rates(settings.rates_path)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c
