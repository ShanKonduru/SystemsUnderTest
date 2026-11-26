"""Settings: config/billing.yaml, with BILLING_* environment variables for the deployment-specific parts."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

SERVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SERVICE_ROOT / "config" / "billing.yaml"
DEFAULT_RATES = SERVICE_ROOT / "reference" / "rate_tables.csv"
DEFAULT_TODAY = "2026-11-03"


@dataclass(frozen=True)
class Settings:
    currency: str
    timezone: str
    grace_days: int
    free_look_days: int
    renewal_notice_days: int
    reminder_days: int
    late_fee: Decimal
    cancellation_fee: Decimal
    installment_fees: Mapping[str, Decimal]
    today: date
    data_dir: Path
    rates_path: Path
    sandbox_token: str = ""
    notify_url: str = ""
    receipt_key: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "billing.db"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "logs" / "billing.log"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Business rules come from the YAML file; paths, the clock, the timezone, the token and the gateway from the environment."""
    env = os.environ if env is None else env
    raw = yaml.safe_load(Path(env.get("BILLING_CONFIG") or DEFAULT_CONFIG).read_text(encoding="utf-8"))
    fees = raw["fees"]
    return Settings(
        currency=raw["currency"],
        timezone=env.get("BILLING_TIMEZONE") or raw["timezone"],
        grace_days=int(raw["grace_days"]),
        free_look_days=int(raw["free_look_days"]),
        renewal_notice_days=int(raw["renewal_notice_days"]),
        reminder_days=int(raw["reminder_days"]),
        late_fee=Decimal(str(fees["late"])),
        cancellation_fee=Decimal(str(fees["cancellation"])),
        installment_fees={plan: Decimal(str(fee)) for plan, fee in fees["installment"].items()},
        today=date.fromisoformat(env.get("BILLING_TODAY") or DEFAULT_TODAY),
        data_dir=Path(env.get("BILLING_DATA") or SERVICE_ROOT / "data"),
        rates_path=Path(env.get("BILLING_RATES") or DEFAULT_RATES),
        sandbox_token=env.get("BILLING_SANDBOX_TOKEN", ""),
        notify_url=env.get("BILLING_NOTIFY_URL", ""),
        receipt_key=env.get("BILLING_RECEIPT_KEY", ""),
    )
