"""State premium tax shown on quotes (BR-16). The tax is collected by the state; it is not part of the premium."""

from decimal import Decimal

from .money import to_money

PREMIUM_TAX_RATES = {
    "CT": Decimal("0.0175"),
    "NJ": Decimal("0.0210"),
    "NY": Decimal("0.0200"),
    "PA": Decimal("0.0200"),
}


def premium_tax(state: str, premium: Decimal) -> Decimal:
    return to_money(premium * PREMIUM_TAX_RATES[state.upper()])
