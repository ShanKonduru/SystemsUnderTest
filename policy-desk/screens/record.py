"""The system of record, data\\policydesk.json: what tests check outcomes against, not only the screen."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class Record:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "policydesk.json"

    def load(self) -> dict[str, Any]:
        # The app replaces the file atomically; a read can still meet the moment of the swap.
        for attempt in range(20):
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                if attempt == 19:
                    raise
                time.sleep(0.05)
        raise AssertionError("unreachable")

    def quote(self, number: int) -> dict[str, Any] | None:
        return next((q for q in self.load()["quotes"] if q["number"] == number), None)

    def quotes(self) -> list[dict[str, Any]]:
        return self.load()["quotes"]

    def activity_kinds(self) -> list[str]:
        return [a["kind"] for a in self.load()["activity"]]
