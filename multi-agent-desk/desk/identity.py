"""Signed identity envelopes that travel with every handoff.

A token carries who the work is for (``sub``), what they may do (``permissions``), the agents it has passed
through (``chain``) and an expiry. It is HMAC-SHA256 signed with ``DESK_SIGNING_KEY``. ``delegate`` re-signs it
for the next agent: the chain grows by one and permissions can only narrow. The tool server verifies every
token and checks its permissions against the member's registered ones, so a widened or forged identity fails.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Iterable
from typing import Any


class IdentityError(ValueError):
    """The token is malformed, wrongly signed or expired."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sig(payload: str, key: str) -> str:
    return _b64(hmac.new(key.encode(), payload.encode(), hashlib.sha256).digest())


def sign(claims: dict[str, Any], key: str) -> str:
    if not key:
        raise IdentityError("DESK_SIGNING_KEY is not set")
    payload = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    return f"{payload}.{_sig(payload, key)}"


def verify(token: str, key: str, now: float | None = None) -> dict[str, Any]:
    if not key:
        raise IdentityError("DESK_SIGNING_KEY is not set")
    try:
        payload, sig = token.split(".", 1)
        claims: dict[str, Any] = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise IdentityError("malformed identity token") from exc
    if not hmac.compare_digest(sig, _sig(payload, key)):
        raise IdentityError("identity token signature does not match")
    if float(claims.get("exp", 0)) < (time.time() if now is None else now):
        raise IdentityError("identity token has expired")
    return claims


def issue(
    sub: str, permissions: Iterable[str], key: str, agent: str = "triage", ttl: int = 900, now: float | None = None
) -> str:
    t = time.time() if now is None else now
    return sign(
        {"sub": sub, "permissions": sorted(set(permissions)), "chain": [agent], "iat": int(t), "exp": int(t + ttl)}, key
    )


def delegate(token: str, key: str, agent: str, narrow_to: Iterable[str] | None = None) -> str:
    """Re-sign for the next agent. The chain grows; permissions stay the same or narrow, never widen."""
    claims = verify(token, key)
    perms = set(claims["permissions"])
    if narrow_to is not None:
        perms &= set(narrow_to)
    return sign({**claims, "permissions": sorted(perms), "chain": [*claims["chain"], agent]}, key)
