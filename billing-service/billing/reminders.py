"""Payment reminders (BR-15): a reminder goes out `reminder_days` (config/billing.yaml) before each unpaid
installment is due."""

import calendar
from datetime import date

REMINDER_DAYS = 7


def reminder_date(due: date, days: int = REMINDER_DAYS) -> date:
    """The day the reminder for an installment due on `due` is sent."""
    if due.day > days:
        return due.replace(day=due.day - days)
    # early in the month: count back from the end of the previous month
    month = due.month - 1 or 12
    last_day = calendar.monthrange(due.year, month)[1]
    return date(due.year, month, last_day - (days - due.day))
