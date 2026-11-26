"""Money helpers. Amounts are Decimal dollars; rounding to cents is half-up (BR-04)."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_money(value: Decimal | int | str) -> Decimal:
    """Round to whole cents, half-up: 10.005 becomes 10.01."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def to_cents(amount: Decimal) -> int:
    return int(to_money(amount) * 100)


def from_cents(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(CENT)


def fmt(amount: Decimal) -> str:
    """The wire format: a string with exactly two decimals."""
    return str(to_money(amount))
