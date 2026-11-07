"""How uploaded documents appear in the conversation.

The customer uploads a file in the chat (web 📎 button, CLI /attach, or an eval scenario). The file is
stored by the backend and the customer's message gets one line per document, which the model reads and
passes on as document_id to submit_profile_change.
"""

from __future__ import annotations

from typing import Any


def attachment_line(doc: dict[str, Any]) -> str:
    size_kb = max(1, round(doc["size_bytes"] / 1024))
    return f"[Attached document {doc['document_id']}: {doc['filename']} ({doc['content_type']}, {size_kb} KB)]"


def compose_user_message(text: str, docs: list[dict[str, Any]]) -> str:
    text = (text or "").strip()
    lines = [attachment_line(d) for d in docs]
    if not text:
        text = "Here is the document." if len(docs) == 1 else "Here are the documents."
    return "\n".join([text, *lines]) if lines else text
