"""Terminal chat. Shows every stage (intent, plan, tool calls, confirmations) as it happens.

    python -m insurance_copilot.cli --customer CUST-001
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from insurance_copilot.backend.db import BackendError, InsuranceBackend, init_db
from insurance_copilot.config import Settings
from insurance_copilot.core.attachments import compose_user_message
from insurance_copilot.core.session import open_session
from insurance_copilot.core.trace import ConfirmationRequest, Event

CONTENT_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
DIM, BOLD, CYAN, GREEN, YELLOW, RED, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"
if os.name == "nt":
    os.system("")  # enables ANSI escape codes in the Windows console


def render(event: Event, verbose: bool) -> None:
    d = event.data
    if event.type == "intent":
        ents = ", ".join(f"{e['name']}={e['value']}" for e in d["entities"])
        print(f"{DIM}  [1 intent] {', '.join(d['intents'])} ({d['confidence']}){'  ' + ents if ents else ''}{RESET}")
    elif event.type == "tools_selected":
        print(f"{DIM}  [tools]    {', '.join(d['tools']) or 'none'}{RESET}")
    elif event.type == "plan":
        steps = " -> ".join(s["action"] for s in d["steps"])
        print(f"{DIM}  [2 plan]   {steps}{RESET}")
    elif event.type == "tool_call":
        print(f"{CYAN}  [3 call]   {d['name']}({json.dumps(d['arguments'])}){RESET}")
    elif event.type == "tool_result":
        color = RED if d["is_error"] else GREEN
        tag = d["blocked_reason"] or ("ACCESS_DENIED" if d["access_denied"] else "error" if d["is_error"] else "ok")
        preview = d["result"].replace("\n", " ")
        print(f"{color}  [4 result] {d['name']}: {tag}{RESET}" + (f"{DIM} {preview[:160]}{RESET}" if verbose else ""))
    elif event.type == "error":
        print(f"{RED}  [error] {d['message']}{RESET}")


async def chat(args: argparse.Namespace) -> None:
    settings = Settings()
    if args.db:
        settings.db_path = Path(args.db)
    if args.transport:
        settings.mcp_transport = args.transport
    if args.reset_db or not settings.db_path.exists():
        init_db(settings.db_path, settings.today, queue=True)
        print(f"{DIM}Seeded demo database at {settings.db_path}{RESET}")

    async def confirm(req: ConfirmationRequest) -> bool:
        if args.auto_approve:
            print(f"{YELLOW}  [approve]  {req.summary()} -> auto-approved{RESET}")
            return True
        warn = " (cannot be undone)" if req.destructive else ""
        answer = await asyncio.to_thread(input, f"{YELLOW}  [approve]  {req.summary()}{warn}\n  Approve? [y/N] {RESET}")
        return answer.strip().lower() in {"y", "yes"}

    trace_file = open(args.trace, "a", encoding="utf-8") if args.trace else None
    print(f"{BOLD}Harbor Mutual assistant{RESET} - signed in as {args.customer}. Type 'exit' to quit.\n")
    async with open_session(customer_id=args.customer, confirm=confirm, settings=settings,
                            on_event=lambda e: render(e, args.verbose)) as session:
        backend = InsuranceBackend(settings.db_path, args.customer, settings.today)
        pending: list[dict] = []
        print(f"{DIM}Tip: /attach <file> uploads a proof document (PDF, JPG, PNG, WebP).{RESET}")
        while True:
            try:
                text = (await asyncio.to_thread(input, f"{BOLD}you> {RESET}")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text.lower() in {"exit", "quit"}:
                break
            if text.lower().startswith("/attach "):  # upload a proof document, sent with the next message
                path = Path(text[8:].strip().strip('"'))
                ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
                try:
                    doc = backend.store_document(path.name, ctype, path.read_bytes())
                    pending.append(doc)
                    print(f"{DIM}  attached {doc['document_id']} {doc['filename']} - it will be sent with your next message{RESET}")
                except (OSError, BackendError) as exc:
                    print(f"{RED}  could not attach: {exc}{RESET}")
                continue
            text, pending = compose_user_message(text, pending), []
            trace = await session.send(text)
            print(f"\n{BOLD}assistant>{RESET} {trace.reply}\n")
            if trace_file:
                trace_file.write(json.dumps(trace.to_dict()) + "\n")
                trace_file.flush()
    if trace_file:
        trace_file.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Insurance conversation-to-action assistant (CLI)")
    parser.add_argument("--customer", default="CUST-001", help="Signed-in customer: CUST-001, CUST-002, CUST-003")
    parser.add_argument("--db", help="SQLite database path (default data/insurance.db)")
    parser.add_argument("--reset-db", action="store_true", help="Re-seed the demo database first")
    parser.add_argument("--auto-approve", action="store_true", help="Approve write actions without asking")
    parser.add_argument("--transport", choices=["stdio", "inprocess"], help="MCP transport (default stdio)")
    parser.add_argument("--trace", help="Append each turn's trace as JSON lines to this file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show tool result previews")
    args = parser.parse_args()
    try:
        asyncio.run(chat(args))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
