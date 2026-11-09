"""Intent catalog. Tool-to-intent mapping lives on the MCP tools themselves (meta.intents)."""

from typing import Literal

INTENTS: dict[str, str] = {
    "view_policies": "See their policies, coverage, deductibles, premiums or policy details.",
    "get_quote": "Get a price quote for a new policy without committing to buy yet.",
    "buy_policy": "Buy / purchase / sign up for a new policy (including accepting a quote already shown).",
    "modify_policy": "Change coverage level or deductible on an existing policy.",
    "manage_drivers": "Add or remove a driver on an auto policy, or see who is listed as a driver.",
    "manage_riders": "Explore, get advice on, add or remove optional riders / add-on covers for a policy "
                     "(e.g. flood, burglary, fire, rental car, roadside, critical illness).",
    "cancel_policy": "Cancel an existing policy.",
    "file_claim": "Report an incident / file a new claim (accident, theft, damage, etc.).",
    "claim_status": "Check status or history of existing claims.",
    "billing": "Check balance, premium due, payment history, or make a payment.",
    "request_status": "Check what happened to a request they made in chat (a change, purchase, payment or "
                      "cancellation): whether it is done, waiting for review, rejected or failed. Not for claims.",
    "update_profile": "View or change personal details such as mailing address or date of birth "
                      "(changes need a proof document), including sending that proof.",
    "policy_documents": "Get a copy of a policy document: declarations page, proof of insurance, or an auto ID card.",
    "human_help": "Talk to a person: a representative, an agent, a callback, or a complaint or dispute they want a "
                  "human to handle.",
    "general_question": "General insurance questions about products, coverage terms, or how things work.",
    "out_of_scope": "Anything unrelated to the customer's insurance (weather, coding, jokes, other companies' accounts).",
}

IntentName = Literal[
    "view_policies", "get_quote", "buy_policy", "modify_policy", "manage_drivers", "manage_riders", "cancel_policy", "file_claim",
    "claim_status", "billing", "request_status", "update_profile", "policy_documents", "human_help", "general_question", "out_of_scope",
]


# Extra intents for Harbor Mutual representatives (persona 2). Customers' router never sees them.
REP_INTENTS: dict[str, str] = {
    "find_customer": "Find, verify, switch or stop working with a customer: search by name, email, customer or policy "
                     "id, the caller's identity answers, or the representative's own role and book.",
    "work_queue": "The work queue: requests waiting for approval, approving or rejecting them, new-customer "
                  "applications (issue or decline), customer cases and callbacks (take or close), follow-up tasks.",
    "case_notes": "Record an interaction note or a follow-up task, or see the open customer's service history and notes.",
}
ALL_INTENTS: dict[str, str] = {**INTENTS, **REP_INTENTS}

RepIntentName = Literal[
    "view_policies", "get_quote", "buy_policy", "modify_policy", "manage_drivers", "manage_riders", "cancel_policy", "file_claim",
    "claim_status", "billing", "request_status", "update_profile", "policy_documents", "human_help", "general_question", "out_of_scope",
    "find_customer", "work_queue", "case_notes",
]


def catalog_text(rep: bool = False) -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in (ALL_INTENTS if rep else INTENTS).items())
