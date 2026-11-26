"""Applying a payment to the next installment due (BR-10 to BR-12)."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .errors import BillingError, Conflict
from .installments import Installment


@dataclass(frozen=True)
class PaymentOutcome:
    installment: int
    received_on: date
    late: bool
    late_fee: Decimal
    amount_due: Decimal


def received_date(received_at: datetime, timezone: str) -> date:
    """The billing day a payment falls on: its timestamp read in the billing timezone (BR-10)."""
    if received_at.tzinfo is None:
        raise BillingError("received_at must include a UTC offset")
    return received_at.astimezone(ZoneInfo(timezone)).date()


def assess_payment(
    installment: Installment, amount: Decimal, received_on: date, grace_days: int, late_fee: Decimal
) -> PaymentOutcome:
    """On or before the due date: the installment total. In the grace period: the total plus the late fee (BR-11).
    After the grace period the policy has lapsed and payments are refused (BR-12)."""
    grace_ends = installment.due_date + timedelta(days=grace_days)
    if received_on > grace_ends:
        raise Conflict(f"installment {installment.number} was due {installment.due_date}; the policy lapsed after "
                       f"{grace_ends} and needs reinstatement")
    late = received_on > installment.due_date
    fee = late_fee if late else Decimal("0.00")
    due = installment.total + fee
    if amount != due:
        reason = f" including the {late_fee} late fee" if late else ""
        raise BillingError(f"installment {installment.number} needs exactly {due}{reason}; received {amount}")
    return PaymentOutcome(installment.number, received_on, late, fee, due)
