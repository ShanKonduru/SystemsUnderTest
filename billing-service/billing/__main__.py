"""`python -m billing`: serve the API on 127.0.0.1 (port BILLING_PORT, default 8500)."""

import os

import uvicorn

from .api import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("BILLING_PORT", "8500")))


if __name__ == "__main__":
    main()
