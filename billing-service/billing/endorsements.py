"""Coverage changes during the term (BR-20): the new premium and the adjustment for the rest of the term."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .errors import BillingError
from .money import to_money
from .premiums import quote_premium
from .rates import RateTable
from .renewals import renewal_date, term_days


@dataclass(frozen=True)
class Endorsement:
    coverage_from: str
    coverage_to: str
    old_premium: Decimal
    new_premium: Decimal
    days_remaining: int
    term_days: int
    adjustment: Decimal  # positive: charged to the member; negative: refunded


def endorsement_quote(
    rates: RateTable, policy: dict[str, Any], *, coverage: str, on: date, policy_count: int, paperless: bool
) -> Endorsement:
    """What changing `policy` to `coverage` from `on` costs: the premium difference for the days left in the term,
    the endorsement date included (BR-20)."""
    effective = date.fromisoformat(policy["effective_date"])
    end = renewal_date(effective)
    if not effective <= on < end:
        raise BillingError(f"the endorsement date must be within the term {effective} to {end}")
    if coverage == policy["coverage"]:
        raise BillingError(f"policy {policy['id']} already has {coverage} coverage")
    new = quote_premium(
        rates, state=policy["state"], product=policy["product"], coverage=coverage,
        driver_age=policy["driver_age"], policy_count=policy_count,
    ).premium
    old = Decimal(policy["annual_premium"])
    days, remaining = term_days(effective), (end - on).days
    return Endorsement(policy["coverage"], coverage, old, new, remaining, days,
                       to_money((new - old) * remaining / days))
