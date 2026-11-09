"""Insurance MCP server: exposes the backend operations as MCP tools.

One server instance is bound to ONE authenticated customer (passed in by the host
application, never by the model). Each tool carries MCP metadata used by the chatbot:

* ``meta.intents``                - which customer intents the tool serves (intent-based selection)
* ``meta.requires_confirmation``  - write actions the host must confirm with the user first
* ``meta.level``                  - how far a call goes for this customer: read | prepare | submit | commit
                                    (capped by the transaction policy; ``meta.level_overrides`` gives
                                    per-argument exceptions, e.g. a date-of-birth change goes to review)
* ``meta.disclosure``             - {id, text} the customer must accept: the host shows the text in the
                                    approval prompt and, only on approval, adds ``disclosure_accepted``
                                    (a parameter hidden from the model) to the call
* ``meta.step_up_minutes``        - the call needs a passed verify_identity within this many minutes; the
                                    host checks first so the customer isn't asked to approve a call that
                                    would be refused (the backend enforces it either way)
* ``annotations.read_only_hint``  - standard MCP hint: does the tool change state?

A guest (prospective customer, not signed in) gets only the tools a prospect can use: products, quotes,
riders by product, applying from a quote, and asking for a representative.

A representative (REP-...) gets the customer tools plus the representative tools: their role, customer search
and verification, service history, notes and tasks, and the work queue (approvals, applications, cases). The
customer tools act on the customer the representative verified (the backend's service context), never on a
customer named in an argument, and each operation goes only as far as the representative's role allows.

Every call is written to the backend's ``audit_log`` (ok / denied / error), which the test
harness uses to verify access control. Every submit/commit request is also recorded in
``transactions`` with its status (committed, submitted, pending_review, rejected, failed).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Any, Callable, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from insurance_copilot.backend.db import PRODUCTS, AccessDenied, BackendError, InsuranceBackend
from insurance_copilot.backend.policy import DISCLOSURES, STEP_UP_MINUTES

READ = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)

ProductCode = Literal["AUTO", "HOME", "RENTERS", "TRAVEL", "LIFE"]
CoverageLevel = Literal["basic", "standard", "premium"]
Deductible = Literal[250, 500, 1000, 2000]
ClaimType = Literal["collision", "theft", "weather", "fire", "water_damage", "liability", "medical",
                    "trip_cancellation", "other"]
PaymentMethod = Literal["card_on_file", "bank_account"]
ChangeType = Literal["mailing_address", "date_of_birth"]
DocumentType = Literal["utility_bill", "bank_statement", "lease_agreement", "government_id", "passport",
                       "drivers_license", "birth_certificate", "other"]
RiderCode = Literal["BURGLARY", "FLOOD", "FIRE_EXTENDED", "WATER_BACKUP", "EARTHQUAKE", "VALUABLES",
                    "RENTAL_CAR", "ROADSIDE", "GAP", "TRIP_DELAY_PLUS", "ADVENTURE_SPORTS", "ACCIDENTAL_DEATH",
                    "CRITICAL_ILLNESS", "WAIVER_OF_PREMIUM"]
Relationship = Literal["spouse", "partner", "child", "parent", "sibling", "other_household_member"]
PolicyId = Annotated[str, Field(description="Policy id, e.g. POL-1001")]
TransactionStatus = Literal["committed", "submitted", "pending_review", "rejected", "failed"]
RepeatOf = Annotated[str | None, Field(
    description="Only after a DUPLICATE_REQUEST error, and only if the customer confirmed they really want a "
                "second identical request: the transaction id from that error")]
IsoDate = Annotated[str, Field(description="Date in YYYY-MM-DD format", pattern=r"^\d{4}-\d{2}-\d{2}$")]
# Filled in by the host app when the customer approves the prompt that shows the disclosure. Hidden from the model.
DisclosureAccepted = Annotated[str | None, Field(description="Set by the app only. Never set this yourself.")]
DocumentKind = Literal["declarations", "proof_of_insurance", "id_card"]
Channel = Literal["phone", "email", "chat"]
QueueKind = Literal["all", "requests", "applications", "cases", "tasks"]


def build_server(backend: InsuranceBackend) -> MCPServer:
    server = MCPServer(
        "insurance-backend",
        instructions=("Insurance operations for a guest (prospective customer, not signed in)." if backend.is_guest
                      else f"Insurance operations for Harbor Mutual representative {backend.principal}, acting for "
                           "the customer they verified." if backend.is_rep
                      else f"Insurance operations for signed-in customer {backend.customer_id}."),
        log_level="WARNING",
    )

    def _meta(intents: list[str], confirm: bool = False, op: str | None = None) -> dict[str, Any]:
        meta: dict[str, Any] = {"intents": intents, "requires_confirmation": confirm,
                                "level": backend.policy.effective(op) if op else "read"}
        if op and (overrides := backend.policy.variants(op)):
            meta["level_overrides"] = overrides
        if op in DISCLOSURES:
            meta["disclosure"] = DISCLOSURES[op]
        if op in backend.policy.step_up and not backend.is_guest:
            meta["step_up_minutes"] = STEP_UP_MINUTES  # needs verify_identity this recently
        return meta

    def tool(*, guest: bool = False, customer: bool = True, rep: bool | None = None,
             **kwargs: Any) -> Callable[[Callable], Callable]:
        """Register a tool for the personas it serves: signed-in customers (customer=True), guests (guest=True),
        representatives (rep; by default the same as customer: they use customer tools for the verified customer)."""
        rep = customer if rep is None else rep
        if (backend.is_guest and guest) or (backend.is_rep and rep) or \
                (not backend.is_guest and not backend.is_rep and customer):
            return server.tool(**kwargs)
        return lambda fn: fn

    def run(tool: str, arguments: dict[str, Any], fn: Callable[[], Any]) -> Any:
        try:
            backend.load_context()  # representatives: which verified customer is open right now
            result = fn()
        except AccessDenied as exc:
            backend.audit(tool, arguments, "denied", str(exc))
            raise ToolError(str(exc)) from None
        except BackendError as exc:
            backend.audit(tool, arguments, "error", str(exc))
            raise ToolError(str(exc)) from None
        except ValueError as exc:  # e.g. malformed dates
            backend.audit(tool, arguments, "error", str(exc))
            raise ToolError(f"INVALID_REQUEST: {exc}") from None
        backend.audit(tool, arguments, "ok")
        return result

    # ------------------------------------------------------------------ read tools
    @tool(annotations=READ, meta=_meta(["update_profile", "buy_policy", "general_question"]))
    def get_customer_profile() -> dict:
        """Get the signed-in customer's profile: name, email, mailing address and date of birth."""
        return run("get_customer_profile", {}, backend.get_profile)

    @tool(annotations=READ, meta=_meta(
        ["view_policies", "modify_policy", "manage_drivers", "manage_riders", "cancel_policy", "file_claim",
         "claim_status", "billing", "buy_policy"]))
    def list_policies(
        include_cancelled: Annotated[bool, Field(description="Also return cancelled policies")] = False,
    ) -> dict:
        """List the signed-in customer's insurance policies with coverage, premium and balance.
        Use this to find the right policy id when the customer refers to a policy by type
        (e.g. "my car policy")."""
        args = {"include_cancelled": include_cancelled}
        return {"policies": run("list_policies", args, lambda: backend.list_policies(include_cancelled))}

    @tool(annotations=READ, meta=_meta(
        ["view_policies", "modify_policy", "manage_drivers", "manage_riders", "cancel_policy", "file_claim",
         "billing"]))
    def get_policy_details(policy_id: PolicyId) -> dict:
        """Get full details for one of the customer's policies."""
        return run("get_policy_details", {"policy_id": policy_id}, lambda: backend.get_policy(policy_id))

    @tool(guest=True, annotations=READ, meta=_meta(["buy_policy", "get_quote", "general_question"]))
    def list_products() -> dict:
        """List insurance products available for purchase, the insured-item fields each product
        needs for a quote, and the allowed coverage levels and deductibles."""
        return {"products": run("list_products", {}, backend.list_products)}

    @tool(annotations=READ, meta=_meta(["claim_status", "file_claim"]))
    def list_claims(
        policy_id: Annotated[str | None, Field(description="Optional policy id to filter by")] = None,
    ) -> dict:
        """List the customer's insurance claims and their status."""
        return {"claims": run("list_claims", {"policy_id": policy_id}, lambda: backend.list_claims(policy_id))}

    @tool(annotations=READ, meta=_meta(["billing"]))
    def get_billing(policy_id: PolicyId) -> dict:
        """Get billing for a policy: monthly premium, balance due, next due date and past payments."""
        return run("get_billing", {"policy_id": policy_id}, lambda: backend.get_billing(policy_id))

    # Quotes are recorded but non-binding, so no confirmation is needed.
    @tool(guest=True, annotations=WRITE, meta=_meta(["buy_policy", "get_quote"], op="get_quote"), description=(
        "Create a non-binding price quote for a new policy. Returns a quote_id valid for 30 days.\n"
        "insured_item must use exactly these keys for each product:\n"
        + "\n".join(f"- {code}: {', '.join(fields)}" for code, _, _, _, fields in PRODUCTS)
        + "\nIf any of these details are unknown, ask the customer for them instead of guessing."))
    def get_quote(
        product_code: ProductCode,
        coverage_level: CoverageLevel,
        deductible: Deductible,
        insured_item: Annotated[dict[str, str | int | float], Field(
            description="Details of what is insured, keyed by the product's required fields listed above.")],
    ) -> dict:
        insured_item = {k: str(v) for k, v in insured_item.items()}
        args = {"product_code": product_code, "coverage_level": coverage_level, "deductible": deductible,
                "insured_item": insured_item}
        return run("get_quote", args,
                   lambda: backend.execute("get_quote", args))

    # ------------------------------------------------------------------ write tools (confirmed)
    @tool(annotations=WRITE, meta=_meta(["buy_policy"], confirm=True, op="purchase_policy"))
    def purchase_policy(
        quote_id: Annotated[str, Field(description="Quote id from get_quote, e.g. QTE-7001")],
        start_date: Annotated[str | None, Field(
            description="Optional start date YYYY-MM-DD; defaults to today")] = None,
        disclosure_accepted: DisclosureAccepted = None,
    ) -> dict:
        """Buy a new policy from an open quote. Binding: creates the policy and the first premium bill.
        Only call after the customer has seen the quoted premium and asked to buy."""
        args = {"quote_id": quote_id, "start_date": start_date}
        return run("purchase_policy", args, lambda: backend.execute(
            "purchase_policy", {**args, "disclosure_accepted": disclosure_accepted}))

    @tool(annotations=WRITE, meta=_meta(["modify_policy"], confirm=True, op="update_policy_coverage"))
    def update_policy_coverage(
        policy_id: PolicyId,
        deductible: Annotated[Deductible | None, Field(description="New deductible")] = None,
        coverage_level: Annotated[CoverageLevel | None, Field(description="New coverage level")] = None,
    ) -> dict:
        """Change the deductible and/or coverage level of an active policy. The premium is
        recalculated and the old and new values are returned."""
        args = {"policy_id": policy_id, "deductible": deductible, "coverage_level": coverage_level}
        return run("update_policy_coverage", args,
                   lambda: backend.execute("update_policy_coverage", args))

    @tool(annotations=READ, meta=_meta(["manage_drivers", "view_policies"]))
    def list_drivers(policy_id: PolicyId) -> dict:
        """List the drivers on an auto policy: the policyholder (primary driver) and any additional drivers."""
        return run("list_drivers", {"policy_id": policy_id}, lambda: backend.list_drivers(policy_id))

    @tool(annotations=WRITE, meta=_meta(["manage_drivers"], confirm=True, op="add_driver"))
    def add_driver(
        policy_id: PolicyId,
        full_name: Annotated[str, Field(description="Driver's full name as given by the customer")],
        date_of_birth: IsoDate,
        relationship: Annotated[Relationship, Field(description="Driver's relationship to the policyholder")],
    ) -> dict:
        """Add a driver to an active auto policy. Drivers must be at least 16. Each additional driver
        raises the monthly premium (more for drivers under 25 or 70 and over); the old and new premium
        are returned. The exact date of birth is required - if the customer only gives an age, ask for it."""
        args = {"policy_id": policy_id, "full_name": full_name, "date_of_birth": date_of_birth,
                "relationship": relationship}
        return run("add_driver", args,
                   lambda: backend.execute("add_driver", args))

    @tool(annotations=DESTRUCTIVE, meta=_meta(["manage_drivers"], confirm=True, op="remove_driver"))
    def remove_driver(
        policy_id: PolicyId,
        driver_id: Annotated[str, Field(description="Driver id from list_drivers, e.g. DRV-3001")],
    ) -> dict:
        """Remove an additional driver from an auto policy. The policyholder (primary driver) cannot
        be removed. Look up the driver id with list_drivers first. Returns the old and new premium."""
        args = {"policy_id": policy_id, "driver_id": driver_id}
        return run("remove_driver", args, lambda: backend.execute("remove_driver", args))

    @tool(guest=True, annotations=READ, meta=_meta(["manage_riders", "general_question", "buy_policy"]))
    def list_riders(
        policy_id: Annotated[str | None, Field(
            description="Policy id to check riders against (eligibility, price, already added)")] = None,
        product_code: Annotated[ProductCode | None, Field(
            description="Without a policy_id: riders offered for this product. If the customer has exactly "
                        "one active policy of this product, the result is for that policy")] = None,
    ) -> dict:
        """List optional riders (add-on covers). With policy_id, each rider shows whether it is eligible
        for that policy (and why not), its monthly cost, any waiting period, and whether it is already on
        the policy; eligible riders come first. Each rider has a good_for field to match against the
        customer's situation when recommending."""
        args = {"policy_id": policy_id, "product_code": product_code}
        return run("list_riders", args, lambda: backend.list_riders(policy_id, product_code))

    @tool(annotations=WRITE, meta=_meta(["manage_riders"], confirm=True, op="add_rider"))
    def add_rider(policy_id: PolicyId, rider_code: RiderCode) -> dict:
        """Add one rider to an active policy. Fails with the reason if the rider is not eligible for
        that policy. Returns the rider cost, when coverage starts, and the old and new monthly premium."""
        args = {"policy_id": policy_id, "rider_code": rider_code}
        return run("add_rider", args, lambda: backend.execute("add_rider", args))

    @tool(annotations=WRITE, meta=_meta(["manage_riders"], confirm=True, op="remove_rider"))
    def remove_rider(policy_id: PolicyId, rider_code: RiderCode) -> dict:
        """Remove a rider from a policy. Returns the old and new monthly premium."""
        args = {"policy_id": policy_id, "rider_code": rider_code}
        return run("remove_rider", args, lambda: backend.execute("remove_rider", args))

    @tool(annotations=READ, meta=_meta(["update_profile"]))
    def get_change_requirements(change_type: ChangeType) -> dict:
        """What a change to the customer's core details needs: the current value, the follow-up
        questions to ask, and the proof documents accepted. Call this first for any address or
        date-of-birth change."""
        return run("get_change_requirements", {"change_type": change_type},
                   lambda: backend.change_requirements(change_type))

    @tool(annotations=READ, meta=_meta(["update_profile"]))
    def list_documents() -> dict:
        """List documents the customer has uploaded in the chat, and whether each was already used."""
        return {"documents": run("list_documents", {}, backend.list_documents)}

    @tool(annotations=WRITE, meta=_meta(["update_profile"], confirm=True, op="submit_profile_change"))
    def submit_profile_change(
        change_type: ChangeType,
        new_value: Annotated[str, Field(
            description="mailing_address: full address with street, city, state, ZIP. "
                        "date_of_birth: YYYY-MM-DD")],
        document_id: Annotated[str, Field(
            description="Id of the proof document the customer attached in the chat, e.g. DOC-6001")],
        document_type: Annotated[DocumentType, Field(
            description="What the proof document is, as stated by the customer or clear from the file name")],
        reason: Annotated[str | None, Field(description="Why the change is needed, in the customer's words. Required for date_of_birth")] = None,
        effective_date: Annotated[str | None, Field(description="YYYY-MM-DD the change takes effect. Required for mailing_address: the move date")] = None,
    ) -> dict:
        """Change the customer's mailing address or date of birth, with a proof document they attached
        in this chat. Needed before calling - ask the customer for everything missing in one message:
        mailing_address: full new address, the date they moved (effective_date), and a proof
        (utility bill, bank statement, lease agreement or government ID showing the new address).
        date_of_birth: the correct date (not an age), the reason for the correction, and a proof
        (passport, driver's license or birth certificate). Fails if the document is missing, belongs to someone else, was already used, or is
        not an accepted proof for this change (see get_change_requirements)."""
        args = {"change_type": change_type, "new_value": new_value, "document_id": document_id,
                "document_type": document_type, "reason": reason, "effective_date": effective_date}
        return run("submit_profile_change", args, lambda: backend.execute("submit_profile_change", args))

    @tool(annotations=WRITE, meta=_meta(["file_claim"], confirm=True, op="file_claim"))
    def file_claim(
        policy_id: PolicyId,
        incident_date: IsoDate,
        claim_type: ClaimType,
        description: Annotated[str, Field(description="What happened, in the customer's words")],
        estimated_amount: Annotated[float, Field(description="Estimated loss in USD", gt=0)],
        repeat_of: RepeatOf = None,
    ) -> dict:
        """File a new insurance claim on an active policy."""
        args = {"policy_id": policy_id, "incident_date": incident_date, "claim_type": claim_type,
                "description": description, "estimated_amount": estimated_amount}
        return run("file_claim", args, lambda: backend.execute("file_claim", args, repeat_of))

    @tool(annotations=WRITE, meta=_meta(["billing"], confirm=True, op="make_payment"))
    def make_payment(
        policy_id: PolicyId,
        amount: Annotated[float, Field(description="Amount in USD, at most the balance due", gt=0)],
        payment_method: PaymentMethod = "card_on_file",
        repeat_of: RepeatOf = None,
    ) -> dict:
        """Pay toward a policy's balance due."""
        args = {"policy_id": policy_id, "amount": amount, "payment_method": payment_method}
        return run("make_payment", args, lambda: backend.execute("make_payment", args, repeat_of))

    @tool(annotations=DESTRUCTIVE, meta=_meta(["cancel_policy"], confirm=True, op="cancel_policy"))
    def cancel_policy(
        policy_id: PolicyId,
        reason: Annotated[str, Field(description="Customer's reason for cancelling")],
        effective_date: Annotated[str | None, Field(
            description="Optional effective date YYYY-MM-DD; defaults to today")] = None,
        disclosure_accepted: DisclosureAccepted = None,
    ) -> dict:
        """Cancel an active policy. Coverage ends on the effective date."""
        args = {"policy_id": policy_id, "reason": reason, "effective_date": effective_date}
        return run("cancel_policy", args, lambda: backend.execute(
            "cancel_policy", {**args, "disclosure_accepted": disclosure_accepted}))

    # ------------------------------------------------------------------ request tracking
    @tool(guest=True, annotations=READ, meta=_meta(["request_status"]))
    def get_transaction_status(
        transaction_id: Annotated[str, Field(description="Transaction id, e.g. TXN-10001")],
    ) -> dict:
        """Status of one request the customer made (a change, purchase, payment, claim or cancellation):
        committed, submitted, pending_review, rejected or failed, with what was asked, the reference of
        what was created or changed, and any reviewer decision."""
        return run("get_transaction_status", {"transaction_id": transaction_id},
                   lambda: backend.get_transaction(transaction_id))

    @tool(guest=True, annotations=READ, meta=_meta(["request_status"]))
    def list_transactions(
        status: Annotated[TransactionStatus | None, Field(description="Optional status filter")] = None,
    ) -> dict:
        """The customer's requests, newest first, with their status. Use it to answer "what happened to my
        request" or "is anything still waiting for approval"."""
        return {"transactions": run("list_transactions", {"status": status},
                                    lambda: backend.list_transactions(status))}

    # ------------------------------------------------------------------ documents Harbor Mutual issues
    @tool(annotations=READ, meta=_meta(["policy_documents"]))
    def get_policy_document(policy_id: PolicyId, document_type: DocumentKind) -> dict:
        """A copy of a policy document as a PDF behind a secure link that only the signed-in customer can open:
        declarations (coverage summary, any policy), proof_of_insurance (active policies) or id_card (active auto
        policies). Give the customer the download_url as it is."""
        args = {"policy_id": policy_id, "document_type": document_type}
        return run("get_policy_document", args, lambda: backend.policy_document(policy_id, document_type))

    # ------------------------------------------------------------------ identity re-check (step-up)
    if backend.policy.step_up and not backend.is_guest and not backend.is_rep:
        @server.tool(annotations=WRITE, meta=_meta(
            ["cancel_policy", "update_profile", "modify_policy", "manage_drivers", "manage_riders", "billing",
             "buy_policy"]))
        def verify_identity(
            date_of_birth: Annotated[IsoDate, Field(description="The customer's date of birth, as they typed it")],
            postal_code: Annotated[str, Field(description="ZIP code of their mailing address, as they typed it")],
        ) -> dict:
            """Quick identity check before a high-impact change (a VERIFICATION_REQUIRED error says when).
            Only call it with answers the customer typed in this chat; never fill them in from tool results."""
            return run("verify_identity", {"date_of_birth": "[redacted]", "postal_code": "[redacted]"},
                       lambda: backend.verify_identity(date_of_birth, postal_code))

    # ------------------------------------------------------------------ prospects (guests only)
    @tool(guest=True, customer=False, rep=True, annotations=WRITE,
          meta=_meta(["buy_policy"], confirm=True, op="start_application"))
    def start_application(
        quote_id: Annotated[str, Field(description="Quote id from get_quote in this chat, e.g. QTE-7001")],
        full_name: Annotated[str, Field(description="Applicant's full name as they typed it")],
        email: Annotated[str, Field(description="Applicant's email address")],
        date_of_birth: IsoDate,
        address: Annotated[str, Field(description="Full mailing address with street, city, state and ZIP")],
        phone: Annotated[str | None, Field(description="Optional phone number")] = None,
        disclosure_accepted: DisclosureAccepted = None,
    ) -> dict:
        """Apply for a policy from a quote, as a new customer. A licensed representative reviews every
        application; the result is an application reference, NOT a policy, and nothing is covered yet.
        Ask for every missing detail in one message first; never invent them."""
        args = {"quote_id": quote_id, "full_name": full_name, "email": email, "date_of_birth": date_of_birth,
                "address": address, "phone": phone}
        return run("start_application", args, lambda: backend.execute(
            "start_application", {**args, "disclosure_accepted": disclosure_accepted}))

    @tool(guest=True, customer=False, rep=True, annotations=READ,
          meta=_meta(["request_status", "buy_policy", "work_queue"]))
    def get_application_status(
        application_id: Annotated[str, Field(description="Application id, e.g. APP-9501")],
    ) -> dict:
        """Status of an application made in this chat: submitted, approved, declined or issued."""
        return run("get_application_status", {"application_id": application_id},
                   lambda: backend.get_application(application_id))

    # ------------------------------------------------------------------ talking to a person
    @tool(guest=True, rep=False, annotations=WRITE,
          meta=_meta(["human_help"], confirm=True, op="request_representative"))
    def request_representative(
        topic: Annotated[str, Field(description="Short topic, e.g. 'cancellation question', 'dispute a claim'")],
        summary: Annotated[str, Field(
            description="Factual summary of what the customer needs and what was already done in the chat, for the "
                        "representative. No guesses, no internal notes")],
        preferred_channel: Annotated[Channel, Field(description="How the customer wants to be contacted")],
        contact: Annotated[str | None, Field(
            description="Phone number or email to use, as the customer typed it. Signed-in customers who want email "
                        "can leave it empty to use the email on file")] = None,
        callback_time: Annotated[str | None, Field(
            description="When the customer would like the callback, in their words, if they said")] = None,
        disclosure_accepted: DisclosureAccepted = None,
    ) -> dict:
        """Hand the conversation to a Harbor Mutual representative: creates a case in their queue with the
        summary, and the representative contacts the customer. Use it when the customer asks for a person, when
        something can't be done in the chat (NOT_PERMITTED, failed identity checks), or for disputes."""
        args = {"topic": topic, "summary": summary, "preferred_channel": preferred_channel, "contact": contact,
                "callback_time": callback_time}
        return run("request_representative", args, lambda: backend.execute(
            "request_representative", {**args, "disclosure_accepted": disclosure_accepted}))

    # ------------------------------------------------------------------ representatives (persona 2)
    def rep_meta(intents: list[str], confirm: bool = False, level: str = "read", op: str | None = None) -> dict:
        meta = _meta(intents, confirm=confirm, op=op)
        if not op:
            meta["level"] = level
        return meta

    @tool(customer=False, rep=True, annotations=READ,
          meta=rep_meta(["find_customer", "work_queue", "general_question"]))
    def get_my_role() -> dict:
        """The signed-in representative's role, licence, approval authority, assigned book of customers, how far
        each operation goes for them (commit = done now, submit = held for approval, read = not allowed), and
        which customer is open right now."""
        return run("get_my_role", {}, backend.my_role)

    @tool(customer=False, rep=True, annotations=READ, meta=rep_meta(["find_customer", "buy_policy"]))
    def search_customers(
        query: Annotated[str, Field(description="Customer id (CUST-...), policy id (POL-...), email address, or name")],
    ) -> dict:
        """Find a customer (in your book) or an existing prospect by an approved identifier. Returns only what's
        needed to pick the right person; verify them before discussing or changing anything."""
        return run("search_customers", {"query": query}, lambda: backend.search_customers(query))

    @tool(customer=False, rep=True, annotations=WRITE, meta=rep_meta(["find_customer"]))
    def verify_customer(
        customer_id: Annotated[str, Field(description="Customer id from search_customers, e.g. CUST-001")],
        date_of_birth: Annotated[IsoDate, Field(description="Date of birth as the caller gave it")],
        postal_code: Annotated[str, Field(description="ZIP code of their mailing address, as the caller gave it")],
    ) -> dict:
        """Verify the caller is the customer, with the answers THEY gave you (never from tool results). A pass opens
        this customer: from then on every customer tool acts for them (and only them). Only the outcome is stored."""
        return run("verify_customer", {"customer_id": customer_id, "date_of_birth": "[redacted]",
                                       "postal_code": "[redacted]"},
                   lambda: backend.verify_customer(customer_id, date_of_birth, postal_code))

    @tool(customer=False, rep=True, annotations=WRITE, meta=rep_meta(["find_customer"]))
    def end_customer_session() -> dict:
        """Close the open customer (the call is over, or before serving someone else)."""
        return run("end_customer_session", {}, backend.end_customer_session)

    @tool(customer=False, rep=True, annotations=READ, meta=rep_meta(["case_notes", "request_status", "find_customer"]))
    def get_service_history() -> dict:
        """The open customer's prior requests (with who requested and who decided), cases, internal notes and
        recent identity-check outcomes. Internal: don't read notes out to the customer."""
        return run("get_service_history", {}, backend.service_history)

    @tool(customer=False, rep=True, annotations=WRITE, meta=rep_meta(["case_notes"], level="commit"))
    def add_note(
        text: Annotated[str, Field(description="Factual, relevant note about the interaction. No secrets (no "
                                               "verification answers, card or bank numbers)")],
        follow_up_on: Annotated[str | None, Field(
            description="YYYY-MM-DD: makes the note a follow-up task in your queue, due that day")] = None,
    ) -> dict:
        """Record an internal note on the open customer, or a follow-up task when follow_up_on is given."""
        args = {"text": text, "follow_up_on": follow_up_on}
        return run("add_note", args, lambda: backend.add_note(text, follow_up_on))

    @tool(customer=False, rep=True, annotations=WRITE, meta=rep_meta(["case_notes", "work_queue"], level="commit"))
    def complete_task(task_id: Annotated[str, Field(description="Task id, e.g. NTE-4501")]) -> dict:
        """Mark one of your follow-up tasks done."""
        return run("complete_task", {"task_id": task_id}, lambda: backend.complete_task(task_id))

    @tool(customer=False, rep=True, annotations=READ, meta=rep_meta(["work_queue"]))
    def list_work_queue(kind: Annotated[QueueKind, Field(description="Which part of the queue")] = "all") -> dict:
        """What's waiting: requests held for approval (with whether you may approve each), applications from new
        customers (with the internal match check), open customer cases (callbacks, handoffs) and your follow-up
        tasks. Internal view."""
        return run("list_work_queue", {"kind": kind}, lambda: backend.work_queue(kind))

    @tool(customer=False, rep=True, annotations=WRITE, meta=rep_meta(
        ["work_queue"], confirm=True, level="approve" if backend.rep and backend.rep["approver"] else "read"))
    def review_request(
        transaction_id: Annotated[str, Field(description="Transaction id from the queue, e.g. TXN-10001")],
        decision: Annotated[Literal["approve", "reject"], Field(description="approve applies it now; reject drops it")],
        note: Annotated[str | None, Field(description="Reason; required when rejecting")] = None,
    ) -> dict:
        """Supervisors: approve or reject a request held for review. Approving applies it now under the normal
        rules (if a rule no longer allows it, it is marked failed). You can't review a request you made."""
        args = {"transaction_id": transaction_id, "decision": decision, "note": note}
        return run("review_request", args, lambda: backend.review_request(transaction_id, decision, note))

    @tool(customer=False, rep=True, annotations=WRITE,
          meta=rep_meta(["work_queue"], confirm=True, op="issue_application"))
    def issue_application(
        application_id: Annotated[str, Field(description="Application id, e.g. APP-9501")],
        existing_customer_id: Annotated[str | None, Field(
            description="Only if the applicant is an existing customer you matched and confirmed with them")] = None,
        start_date: Annotated[str | None, Field(description="YYYY-MM-DD; defaults to today")] = None,
        disclosure_accepted: DisclosureAccepted = None,
    ) -> dict:
        """Issue the policy an applicant applied for (an underwriting decision; needs a licensed agent, otherwise it
        is held for approval). Creates the customer record for a new customer. For an applicant who is an existing
        customer, verify that customer first (verify_customer) and pass existing_customer_id. Read the purchase
        disclosure to the applicant first: approving the prompt confirms they accepted it."""
        args = {"application_id": application_id, "existing_customer_id": existing_customer_id,
                "start_date": start_date}
        return run("issue_application", args, lambda: backend.execute(
            "issue_application", {**args, "disclosure_accepted": disclosure_accepted}))

    @tool(customer=False, rep=True, annotations=DESTRUCTIVE,
          meta=rep_meta(["work_queue"], confirm=True, op="decline_application"))
    def decline_application(
        application_id: Annotated[str, Field(description="Application id, e.g. APP-9501")],
        reason: Annotated[str, Field(description="Why it is declined (recorded on the application)")],
    ) -> dict:
        """Decline an application (licensed agents and supervisors only). Nothing is issued."""
        args = {"application_id": application_id, "reason": reason}
        return run("decline_application", args, lambda: backend.execute("decline_application", args))

    @tool(customer=False, rep=True, annotations=WRITE, meta=rep_meta(["work_queue"], confirm=True, level="commit"))
    def update_handoff(
        handoff_id: Annotated[str, Field(description="Case id, e.g. HND-3501")],
        action: Annotated[Literal["assign", "close"], Field(description="assign it to yourself, or close it")],
        resolution: Annotated[str | None, Field(description="How it was resolved; required to close")] = None,
    ) -> dict:
        """Take a customer case (callback or chat handoff), or close it with its resolution. Closing also takes a
        case that isn't assigned yet, so to close one, call close directly."""
        args = {"handoff_id": handoff_id, "action": action, "resolution": resolution}
        return run("update_handoff", args, lambda: backend.update_handoff(handoff_id, action, resolution))

    return server


def main() -> None:
    """Stdio entry point. The host passes the signed-in customer via environment variables."""
    import os

    logging.basicConfig(level=logging.WARNING)
    customer_id = os.environ["COPILOT_CUSTOMER_ID"]
    db_path = os.environ["COPILOT_DB"]
    today = date.fromisoformat(os.environ["COPILOT_TODAY"]) if os.environ.get("COPILOT_TODAY") else None
    build_server(InsuranceBackend(db_path, customer_id, today)).run("stdio")
