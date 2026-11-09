"""Staff review of requests held by the transaction policy (pending_review).

    python -m insurance_copilot.backend.review list [--status pending_review] [--customer CUST-001]
    python -m insurance_copilot.backend.review show TXN-10001
    python -m insurance_copilot.backend.review approve TXN-10001 --by "J. Rivera" [--note "..."]
    python -m insurance_copilot.backend.review reject  TXN-10001 --by "J. Rivera" [--note "..."]

Back-office stand-in; supervisors can also review in the representative chat (review_request). Approving applies the request
through the same backend rules as the chat; if a rule no longer allows it, the transaction is marked failed.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from insurance_copilot.backend.db import BackendError, InsuranceBackend


def main(argv: list[str] | None = None) -> int:
    from insurance_copilot.config import Settings

    parser = argparse.ArgumentParser(prog="python -m insurance_copilot.backend.review", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=None, help="database (default: COPILOT_DB or data/insurance.db)")
    sub = parser.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list", help="list transactions")
    ls.add_argument("--status", default="pending_review", help="status filter ('all' for every status)")
    ls.add_argument("--customer", default=None)
    show = sub.add_parser("show", help="one transaction in full")
    show.add_argument("txn")
    for name in ("approve", "reject"):
        p = sub.add_parser(name, help=f"{name} a pending request")
        p.add_argument("txn")
        p.add_argument("--by", required=True, help="reviewer name")
        p.add_argument("--note", default=None)
    args = parser.parse_args(argv)

    settings = Settings()
    db = args.db or settings.db_path
    if args.command == "list":
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        sql, params = "SELECT id, customer_id, operation, status, reference, created_on, decided_by FROM transactions", []
        where = []
        if args.status != "all":
            where.append("status=?")
            params.append(args.status)
        if args.customer:
            where.append("customer_id=?")
            params.append(args.customer)
        rows = con.execute(sql + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id", params).fetchall()
        con.close()
        if not rows:
            print("No transactions.")
        for r in rows:
            print(f"{r['id']}  {r['customer_id']}  {r['operation']:<24} {r['status']:<15} "
                  f"{r['reference'] or '-':<10} {r['created_on']}  {r['decided_by'] or ''}")
        return 0

    try:
        # Acts as whoever made the request (customer, or the representative working for them).
        backend = InsuranceBackend.for_transaction(db, args.txn, settings.today)
        if args.command == "show":
            out = backend.get_transaction(args.txn)
        else:
            out = backend.review_transaction(args.txn, args.command, args.by, args.note)
    except BackendError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
