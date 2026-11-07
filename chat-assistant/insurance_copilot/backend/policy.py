"""Action levels and the transaction policy: how far each persona may take each operation.

Levels, lowest to highest (see docs/Harbor Mutual Conversational-to-Action Persona Scope.md, section 6):

* ``read``    - retrieve information, nothing changes
* ``prepare`` - collect and validate, no business transaction (e.g. a non-binding quote)
* ``submit``  - send a confirmed request into a workflow; someone else decides or applies it
* ``commit``  - change the system of record
* ``approve`` - independently approve a restricted or exception workflow

Each write operation needs a level to take effect (``OPERATION_LEVELS``). A policy caps the level a
persona may use per operation. When the cap is below what the operation needs, the backend validates
the request without applying it and records it as ``pending_review`` for an authorised reviewer.
A cap of ``read`` (or no access) means the persona can't request the operation in chat at all.

The policy is enforced by the backend, never by the model's instructions.
"""

from __future__ import annotations

import os
from typing import Any

LEVELS = ("read", "prepare", "submit", "commit", "approve")


def rank(level: str) -> int:
    return LEVELS.index(level)


# Level each write operation needs to take effect.
OPERATION_LEVELS: dict[str, str] = {
    "get_quote": "prepare",
    "file_claim": "submit",            # a claim always goes to an adjuster
    "purchase_policy": "commit",
    "update_policy_coverage": "commit",
    "add_driver": "commit",
    "remove_driver": "commit",
    "add_rider": "commit",
    "remove_rider": "commit",
    "submit_profile_change": "commit",
    "make_payment": "commit",
    "cancel_policy": "commit",
    "start_application": "submit",     # a prospect's application goes to underwriting / a representative
    "request_representative": "submit",  # a handoff case for the representative queue
    "issue_application": "commit",     # representative: issue a policy from an application (underwriting decision)
    "decline_application": "commit",   # representative: decline an application
}

# Backend method behind each operation (tool arguments map 1:1 onto its parameters).
OPERATION_METHODS: dict[str, str] = {
    "get_quote": "create_quote",
    "file_claim": "file_claim",
    "purchase_policy": "purchase_policy",
    "update_policy_coverage": "update_coverage",
    "add_driver": "add_driver",
    "remove_driver": "remove_driver",
    "add_rider": "add_rider",
    "remove_rider": "remove_rider",
    "submit_profile_change": "submit_profile_change",
    "make_payment": "make_payment",
    "cancel_policy": "cancel_policy",
    "start_application": "start_application",
    "request_representative": "request_representative",
    "issue_application": "issue_application",
    "decline_application": "decline_application",
}

OPERATION_LABELS: dict[str, str] = {
    "file_claim": "New claim",
    "purchase_policy": "Policy purchase",
    "update_policy_coverage": "Coverage change",
    "add_driver": "Add driver",
    "remove_driver": "Remove driver",
    "add_rider": "Add rider",
    "remove_rider": "Remove rider",
    "submit_profile_change": "Profile change",
    "make_payment": "Payment",
    "cancel_policy": "Policy cancellation",
    "start_application": "New-customer application",
    "request_representative": "Representative request",
    "issue_application": "Issue policy from application",
    "decline_application": "Decline application",
}

# Operations where an identical request shortly after another is most likely an accidental retry.
DUPLICATE_GUARDED = {"make_payment", "file_claim"}
DUPLICATE_WINDOW_MINUTES = 10

# Self-service customer policies. Keys are operation names, or "operation:argument=value" for a variant
# (the most specific key wins). Missing operations may use their full level.
POLICIES: dict[str, dict[str, str]] = {
    # Today's demo behaviour: customers can complete every operation themselves.
    "direct": {},
    # The persona scope's defaults for self-service: high-impact changes, purchases (issue/bind) and
    # identity corrections are submitted for review; contact details, payments and claims go straight in.
    "review": {
        "purchase_policy": "submit",
        "update_policy_coverage": "submit",
        "add_driver": "submit",
        "remove_driver": "submit",
        "add_rider": "submit",
        "remove_rider": "submit",
        "cancel_policy": "submit",
        "submit_profile_change:change_type=date_of_birth": "submit",
    },
}

DEFAULT_POLICY = "direct"

# Operations that need a fresh identity check (verify_identity) in this session, per policy. Signing in
# proves the account; a high-impact change also needs the person at the keyboard to prove who they are.
STEP_UP: dict[str, set[str]] = {
    "direct": set(),
    "review": {"cancel_policy", "submit_profile_change"},
}
STEP_UP_MINUTES = 15       # how long a successful check lasts
STEP_UP_MAX_FAILURES = 3   # failed checks within STEP_UP_MINUTES before the chat stops trying (handoff)

# Disclosures the customer must accept before an operation. The app shows the text in the approval prompt
# and, only when the customer approves, passes the disclosure id with the call (disclosure_accepted). The
# model never sees or sets that argument; the backend refuses the operation without the current id.
DISCLOSURES: dict[str, dict[str, str]] = {
    "purchase_policy": {
        "id": "DSC-PURCHASE-v1",
        "text": "Your policy is issued on the terms of the quote shown and the policy documents. The first "
                "monthly premium is billed on the start date. You can cancel at any time; any refund is "
                "calculated under the policy terms.",
    },
    "cancel_policy": {
        "id": "DSC-CANCEL-v1",
        "text": "Coverage ends on the effective date; anything that happens after it is not covered. "
                "Any premium already due stays payable, and a refund, if any, follows the policy terms.",
    },
    "start_application": {
        "id": "DSC-PRIVACY-v1",
        "text": "Harbor Mutual will use the details you give to assess your application and contact you about "
                "it. A licensed representative reviews every application; this is not yet a policy and "
                "nothing is covered until you receive confirmation. We keep your details as our privacy "
                "notice describes.",
    },
    "request_representative": {
        "id": "DSC-HANDOFF-v1",
        "text": "A Harbor Mutual representative will see the summary below, your contact details, and this "
                "conversation, so they can pick up where the chat left off.",
    },
}


# Issuing from an application binds coverage: the applicant accepts the same terms as a purchase.
DISCLOSURES["issue_application"] = DISCLOSURES["purchase_policy"]

# ---------------------------------------------------------------------------- representatives (persona 2)
# A representative works for customers, one verified customer at a time. Their role sets how far each operation
# goes (caps, as for self-service policies), which customers they may serve (book), whether they hold a licence
# (sales and issuing) and whether they may approve requests others made (the review queue, dual control).
ROLES: dict[str, dict[str, Any]] = {
    "service_rep": {
        "label": "Customer service representative",
        "book": "assigned",   # only customers assigned to them
        "licensed": False,
        "approver": False,
        "caps": {
            "purchase_policy": "submit",       # sales need a licensed agent: held for review
            "issue_application": "submit",
            "decline_application": "read",     # underwriting decisions: not theirs at all
            "cancel_policy": "submit",
            "submit_profile_change:change_type=date_of_birth": "submit",
        },
    },
    "licensed_agent": {
        "label": "Licensed insurance agent",
        "book": "all",
        "licensed": True,
        "approver": False,
        "caps": {
            "submit_profile_change:change_type=date_of_birth": "submit",  # identity corrections: second pair of eyes
        },
    },
    "supervisor": {
        "label": "Service supervisor",
        "book": "all",
        "licensed": True,
        "approver": True,
        "caps": {},
    },
}
REP_PREFIX = "REP-"
SERVICE_CONTEXT_MINUTES = 60  # how long a verified customer stays open for a representative (then re-verify)


class TransactionPolicy:
    def __init__(self, name: str | None = None, *, role: str | None = None):
        if role is not None:  # a representative: the role's caps, no step-up (they verify the customer instead)
            if role not in ROLES:
                raise ValueError(f"Unknown representative role {role!r}; choose one of {sorted(ROLES)}")
            self.name, self.caps, self.step_up = f"role:{role}", ROLES[role]["caps"], set()
            return
        name = (name or os.environ.get("COPILOT_TXN_POLICY") or DEFAULT_POLICY).strip().lower()
        if name not in POLICIES:
            raise ValueError(f"Unknown transaction policy {name!r}; choose one of {sorted(POLICIES)}")
        self.name = name
        self.caps = POLICIES[name]
        self.step_up = STEP_UP.get(name, set())

    def allowed(self, operation: str, arguments: dict[str, Any] | None = None) -> str:
        """Highest level this persona may use for this request."""
        for key, cap in self.caps.items():
            if ":" in key and key.split(":", 1)[0] == operation:
                arg, value = key.split(":", 1)[1].split("=", 1)
                if str((arguments or {}).get(arg)) == value:
                    return cap
        return self.caps.get(operation, "approve")

    def effective(self, operation: str, arguments: dict[str, Any] | None = None) -> str:
        """The level the request will actually reach: what it needs, capped by the policy."""
        needed = OPERATION_LEVELS[operation]
        cap = self.allowed(operation, arguments)
        return needed if rank(cap) >= rank(needed) else cap

    def variants(self, operation: str) -> dict[str, dict[str, str]]:
        """Per-argument overrides of the effective level, e.g. {"change_type": {"date_of_birth": "submit"}}."""
        out: dict[str, dict[str, str]] = {}
        for key in self.caps:
            if ":" in key and key.split(":", 1)[0] == operation:
                arg, value = key.split(":", 1)[1].split("=", 1)
                out.setdefault(arg, {})[value] = self.effective(operation, {arg: value})
        return out
