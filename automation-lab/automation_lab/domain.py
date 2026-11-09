"""The lab's business rules: seed data, quote pricing and the validation of quotes and claims.

Everything is deterministic (fixed seed data, fixed "today"), so a generated script's assertions hold run after run.
Validation returns ``{field: message}``; the page shows the same messages next to the same fields.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

PRODUCTS = {"auto": "Auto", "home": "Home", "renters": "Renters", "boat": "Boat", "life": "Term life"}
BASE_PREMIUM = {"auto": 620.0, "home": 940.0, "renters": 180.0, "boat": 410.0, "life": 300.0}
COVERAGE = {"basic": 0.85, "standard": 1.0, "premium": 1.3}
DEDUCTIBLE = {500: 1.10, 1000: 1.0, 2500: 0.88}
ADDONS = {"roadside": 48.0, "rental": 72.0, "glass": 36.0, "flood": 210.0, "jewelry": 95.0}
ADDON_PRODUCTS = {
    "roadside": {"auto"},
    "rental": {"auto"},
    "glass": {"auto", "boat"},
    "flood": {"home", "renters", "boat"},
    "jewelry": {"home", "renters"},
}
MULTI_POLICY_DISCOUNT = 0.10
PAPERLESS_DISCOUNT = 0.03
MONTHLY_SURCHARGE = 0.04
INCIDENT_TYPES = {"collision", "theft", "water", "fire", "weather", "other"}
CONTACT = {"email", "phone", "text"}
CARD_COLUMNS = ("new", "review", "approved", "paid")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
ZIP = re.compile(r"^\d{5}$")
PHONE = re.compile(r"^\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}$")

HOLDERS = [
    "Shan Mercer", "Indu Varma", "Sri Nair", "Avery Collins", "Jordan Blake", "Morgan Ellis", "Riley Chen",
    "Casey Novak", "Taylor Brooks", "Quinn Harper", "Drew Santos", "Jamie Lowell", "Parker Reyes",
    "Skyler Grant", "Reese Okafor", "Emerson Hale", "Rowan Pike", "Sage Whitley", "Hayden Cole",
]
STATUSES = ("active", "active", "active", "pending", "active", "lapsed", "active", "cancelled")


def seed_policies(today: date) -> list[dict[str, Any]]:
    """57 fictitious policies, the same every time for the same ``today``."""
    products = list(PRODUCTS)
    rows = []
    for i in range(57):
        product = products[(i * 3) % len(products)]
        start = today - timedelta(days=(i * 23) % 700)
        rows.append({
            "policy_number": f"POL-{1001 + i}",
            "holder": HOLDERS[i % len(HOLDERS)],
            "product": product,
            "status": STATUSES[(i * 5) % len(STATUSES)],
            "premium": round(BASE_PREMIUM[product] * (1 + ((i * 37) % 50) / 100), 2),
            "start_date": start.isoformat(),
            "renewal_date": (start + timedelta(days=365)).isoformat(),
            "zip": f"{2101 + (i * 7) % 900:05d}",
        })
    return rows


def seed_cards() -> list[dict[str, Any]]:
    """The claims board: ten claims spread over the four columns."""
    raw = [
        ("CLM-5001", "Shan Mercer", "collision", 4200.0, "approved"),
        ("CLM-5002", "Avery Collins", "water", 1850.0, "new"),
        ("CLM-5003", "Indu Varma", "theft", 960.0, "new"),
        ("CLM-5004", "Jordan Blake", "weather", 7300.0, "review"),
        ("CLM-5005", "Sri Nair", "fire", 15400.0, "review"),
        ("CLM-5006", "Morgan Ellis", "collision", 2100.0, "new"),
        ("CLM-5007", "Riley Chen", "water", 640.0, "paid"),
        ("CLM-5008", "Casey Novak", "other", 300.0, "approved"),
        ("CLM-5009", "Taylor Brooks", "theft", 2750.0, "review"),
        ("CLM-5010", "Quinn Harper", "weather", 5120.0, "paid"),
    ]
    positions: dict[str, int] = {}
    cards = []
    for card_id, holder, kind, amount, column in raw:
        cards.append({"id": card_id, "holder": holder, "type": kind, "amount": amount, "column": column,
                      "position": positions.get(column, 0)})
        positions[column] = positions.get(column, 0) + 1
    return cards


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _age(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def validate_quote(body: dict[str, Any], today: date) -> dict[str, str]:
    errors: dict[str, str] = {}
    product = body.get("product")
    if product not in PRODUCTS:
        errors["product"] = "Choose a product."
    if body.get("coverage") not in COVERAGE:
        errors["coverage"] = "Choose a coverage level."
    if body.get("deductible") not in DEDUCTIBLE:
        errors["deductible"] = "Choose a deductible of 500, 1000 or 2500."
    for name, label in (("first_name", "first name"), ("last_name", "last name")):
        value = str(body.get(name) or "").strip()
        if not value:
            errors[name] = f"Enter your {label}."
        elif len(value) > 40:
            errors[name] = f"Your {label} can be at most 40 characters."
    if not EMAIL.match(str(body.get("email") or "").strip()):
        errors["email"] = "Enter a valid email address."
    phone = str(body.get("phone") or "").strip()
    if phone and not PHONE.match(phone):
        errors["phone"] = "Enter a 10-digit phone number."
    if not ZIP.match(str(body.get("zip") or "").strip()):
        errors["zip"] = "Enter a 5-digit ZIP code."
    born = _date(body.get("date_of_birth"))
    if born is None:
        errors["date_of_birth"] = "Enter your date of birth."
    elif not 18 <= _age(born, today) <= 100:
        errors["date_of_birth"] = "You must be between 18 and 100 years old."
    start = _date(body.get("start_date"))
    if start is None:
        errors["start_date"] = "Choose a coverage start date."
    elif not today <= start <= today + timedelta(days=90):
        errors["start_date"] = "Coverage must start within the next 90 days."
    addons = body.get("addons") or []
    if not isinstance(addons, list) or any(a not in ADDONS for a in addons):
        errors["addons"] = "Unknown add-on."
    elif product in PRODUCTS and any(product not in ADDON_PRODUCTS[a] for a in addons):
        errors["addons"] = f"Some add-ons aren't available for {PRODUCTS[product]}."
    if body.get("payment_plan") not in {"annual", "monthly"}:
        errors["payment_plan"] = "Choose a payment plan."
    if len(str(body.get("notes") or "")) > 500:
        errors["notes"] = "Notes can be at most 500 characters."
    if body.get("consent") is not True:
        errors["consent"] = "You must agree to be contacted about this quote."
    return errors


def price_quote(body: dict[str, Any], today: date, *, ignore_multi_policy: bool = False) -> dict[str, Any]:
    """The annual premium and its breakdown, for a quote that passed ``validate_quote``."""
    base = BASE_PREMIUM[body["product"]]
    age = _age(date.fromisoformat(body["date_of_birth"]), today)
    age_factor = 1.35 if age < 25 else 1.15 if age > 65 else 1.0
    lines = [{"label": f"{PRODUCTS[body['product']]} base premium", "amount": base}]
    core = base * COVERAGE[body["coverage"]] * DEDUCTIBLE[body["deductible"]] * age_factor
    lines.append({"label": "Coverage, deductible and age adjustments", "amount": round(core - base, 2)})
    total = core
    for addon in body.get("addons") or []:
        lines.append({"label": f"Add-on: {addon}", "amount": ADDONS[addon]})
        total += ADDONS[addon]
    if body.get("multi_policy") and not ignore_multi_policy:
        cut = round(total * MULTI_POLICY_DISCOUNT, 2)
        lines.append({"label": "Multi-policy discount (10%)", "amount": -cut})
        total -= cut
    if body.get("paperless"):
        cut = round(total * PAPERLESS_DISCOUNT, 2)
        lines.append({"label": "Paperless discount (3%)", "amount": -cut})
        total -= cut
    annual = round(total, 2)
    monthly = round(annual * (1 + MONTHLY_SURCHARGE) / 12, 2)
    if body["payment_plan"] == "monthly":
        lines.append({"label": "Monthly payment plan (4%)", "amount": round(monthly * 12 - annual, 2)})
    return {"annual_premium": annual, "monthly_payment": monthly if body["payment_plan"] == "monthly" else None,
            "breakdown": [{**line, "amount": round(line["amount"], 2)} for line in lines]}


def validate_claim(
    body: dict[str, Any], today: date, policies: dict[str, dict[str, Any]], upload_ids: set[str]
) -> dict[str, str]:
    errors: dict[str, str] = {}
    policy = policies.get(str(body.get("policy_number") or "").strip().upper())
    if policy is None:
        errors["policy_number"] = "We couldn't find that policy number."
    elif policy["status"] != "active":
        errors["policy_number"] = f"Policy {policy['policy_number']} is {policy['status']}; claims need an active policy."
    if body.get("incident_type") not in INCIDENT_TYPES:
        errors["incident_type"] = "Choose what happened."
    when = _date(body.get("incident_date"))
    if when is None:
        errors["incident_date"] = "Enter the date of the incident."
    elif not today - timedelta(days=365) <= when <= today:
        errors["incident_date"] = "The incident must be within the last 12 months and not in the future."
    if not str(body.get("location") or "").strip():
        errors["location"] = "Enter where it happened."
    description = str(body.get("description") or "").strip()
    if not 20 <= len(description) <= 2000:
        errors["description"] = "Describe what happened in 20 to 2000 characters."
    if body.get("police_report") is True and not str(body.get("report_number") or "").strip():
        errors["report_number"] = "Enter the police report number."
    estimate = body.get("estimate")
    if not isinstance(estimate, int | float) or isinstance(estimate, bool) or not 0 < estimate <= 1_000_000:
        errors["estimate"] = "Enter an estimate between $1 and $1,000,000."
    if body.get("contact") not in CONTACT:
        errors["contact"] = "Choose how we should contact you."
    missing = [u for u in body.get("upload_ids") or [] if u not in upload_ids]
    if missing:
        errors["upload_ids"] = f"Unknown upload(s): {', '.join(missing)}."
    signature = " ".join(str(body.get("signature") or "").split()).lower()
    if policy is not None and signature != policy["holder"].lower():
        errors["signature"] = "Type the policyholder's full name exactly as shown to sign."
    if body.get("agree") is not True:
        errors["agree"] = "You must confirm the information is true."
    return errors
