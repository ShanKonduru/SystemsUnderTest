"""Base rates by territory (state) and product, read from reference/rate_tables.csv."""

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .errors import BillingError

PRODUCTS = ("auto", "home", "renters")


class UnknownTerritory(BillingError):
    """Harbor Mutual doesn't write this product in this state."""

    code = "unknown_territory"


@dataclass(frozen=True)
class RateTable:
    rates: dict[tuple[str, str], Decimal]

    def base_rate(self, state: str, product: str) -> Decimal:
        try:
            return self.rates[(state.upper(), product)]
        except KeyError:
            raise UnknownTerritory(f"no {product} rate for state {state!r}") from None

    def states(self) -> list[str]:
        return sorted({state for state, _ in self.rates})


def load_rates(path: Path) -> RateTable:
    rates: dict[tuple[str, str], Decimal] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for line_no, row in enumerate(csv.DictReader(fh), start=2):
            product = row["product"].strip()
            if product not in PRODUCTS:
                raise ValueError(f"{path.name}:{line_no}: unknown product {product!r}")
            try:
                rate = Decimal(row["base_rate"].strip())
            except InvalidOperation:
                raise ValueError(f"{path.name}:{line_no}: base_rate {row['base_rate']!r} is not a number") from None
            if rate <= 0:
                raise ValueError(f"{path.name}:{line_no}: base_rate must be positive")
            rates[(row["state"].strip().upper(), product)] = rate
    return RateTable(rates)
