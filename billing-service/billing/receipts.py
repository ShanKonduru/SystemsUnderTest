"""Payment receipts (BR-19): every payment returns a signed receipt the member can show later, and anyone holding
it can have it checked. The signing key is BILLING_RECEIPT_KEY; without one, a random key is used until restart."""

from __future__ import annotations

from typing import Any

import jwt

from .errors import BillingError

ALGORITHM = "HS256"
ISSUER = "harbor-mutual-billing"


def issue(payment: dict[str, Any], key: str) -> str:
    return jwt.encode({"iss": ISSUER, **payment}, key, algorithm=ALGORITHM)


def verify(token: str, key: str) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(token, key, algorithms=[ALGORITHM], issuer=ISSUER)
    except jwt.InvalidTokenError as exc:
        raise BillingError(f"not a valid receipt: {exc}") from exc
    claims.pop("iss")
    return claims
