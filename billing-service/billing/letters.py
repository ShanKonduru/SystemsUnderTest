"""Letters to members (BR-17), rendered from the HTML templates in billing/templates/."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape


def long_date(value: str | date) -> str:
    """2026-12-17 -> December 17, 2026."""
    day = date.fromisoformat(value) if isinstance(value, str) else value
    return f"{day:%B} {day.day}, {day.year}"


def dollars(value: str | Decimal) -> str:
    """1411.60 -> $1,411.60."""
    return f"${Decimal(value):,.2f}"


_env = Environment(
    loader=PackageLoader("billing", "templates"),
    autoescape=select_autoescape(["html"]),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters.update(long_date=long_date, dollars=dollars)


def render(template: str, **context: Any) -> str:
    return _env.get_template(template).render(**context)
