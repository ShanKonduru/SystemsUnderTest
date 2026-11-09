"""The locator map: for every authored element, what it is called in each UI release.

It is the ground truth for scoring a self-healing framework: given a v1 locator (the authored id), it says which id,
name, data-testid, CSS selector, tag, role, accessible name and text the same element has in v2, v3 or v4. It is
computed from the authored page (static/index.html and the payment frame) and the same rules file the browser uses
(static/releases.json), so it cannot disagree with what the page renders.

v3 and v4 generate ids, names and classes on every page load, so those are null here ("dynamic": true); the page's
own `window.HM.R.locatorMap()` gives that load's values. Accessible names are computed the simple way (aria-label,
aria-labelledby, label[for], a wrapping label, then the element's text), which matches every control in the lab.
"""

from __future__ import annotations

import json
import re
from functools import cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

STATIC = Path(__file__).parent / "static"
PAGES = {"index.html": None, "frames/payment.html": "payment-frame"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
NAMED_BY_TEXT = {"button", "a", "legend", "option", "h1", "h2", "h3", "summary", "li"}


class Node:
    def __init__(self, tag: str, attrs: dict[str, str], parent: Node | None) -> None:
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[Node | str] = []

    def elements(self) -> list[Node]:
        return [c for c in self.children if isinstance(c, Node)]

    def walk(self) -> Any:
        yield self
        for c in self.elements():
            yield from c.walk()


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: v if v is not None else "" for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.cur.children.append(Node(tag, {k: v or "" for k, v in attrs}, self.cur))

    def handle_endtag(self, tag: str) -> None:
        node: Node | None = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data: str) -> None:
        self.cur.children.append(data)


def _squash(s: str) -> str:
    return " ".join(s.split())


def pascal(s: str) -> str:
    return re.sub(r"(^|[-_])([a-z0-9])", lambda m: m.group(2).upper(), s)


def camel(s: str) -> str:
    p = pascal(s)
    return p[:1].lower() + p[1:]


@cache
def rules() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((STATIC / "releases.json").read_text(encoding="utf-8"))
    return data


@cache
def _trees() -> tuple[tuple[str | None, Node], ...]:
    out = []
    for name, page in PAGES.items():
        builder = _Builder()
        builder.feed((STATIC / name).read_text(encoding="utf-8"))
        out.append((page, builder.root))
    return tuple(out)


def _rule(kind: str, release: str) -> Any:
    return (rules().get(kind) or {}).get(release)


def _text(node: Node, release: str) -> str:
    """textContent as the release renders it: v4 rewords labels, legends and spans in labels; data-t buttons reword."""
    labels = _rule("labels", release) or {}
    own = _squash("".join(c for c in node.children if isinstance(c, str)))
    if not node.elements():
        if node.tag in {"label", "legend"} or (node.tag == "span" and node.parent and node.parent.tag == "label"):
            return labels.get(own, own)
        if "data-t" in node.attrs:
            return (_rule("text", release) or {}).get(own, own)
        return own
    parts = [c if isinstance(c, str) else _text(c, release) for c in node.children]
    return _squash(" ".join(parts))


def _role(node: Node, tag: str) -> str | None:
    if node.attrs.get("role"):
        return node.attrs["role"]
    a = node.attrs
    if tag == "a":
        return "link" if "href" in a else None
    if tag == "button":
        return "button"
    if tag == "input":
        kind = a.get("type", "text")
        if "list" in a:
            return "combobox"
        return {"checkbox": "checkbox", "radio": "radio", "range": "slider", "search": "searchbox", "number": "spinbutton",
                "text": "textbox", "email": "textbox", "tel": "textbox"}.get(kind)
    if tag == "select":
        return "listbox" if "multiple" in a else "combobox"
    return {"textarea": "textbox", "h1": "heading", "h2": "heading", "h3": "heading", "nav": "navigation",
            "table": "table", "ul": "list", "ol": "list", "dialog": "dialog", "section": "region" if "aria-labelledby" in a else None,
            "form": "form", "fieldset": "group", "iframe": None}.get(tag)


def _name(node: Node, by_id: dict[str, Node], labels_for: dict[str, Node], release: str) -> str | None:
    a = node.attrs
    aria = _rule("aria_labels", release) or {}
    if a.get("aria-label"):
        return aria.get(a["aria-label"], a["aria-label"])
    if a.get("aria-labelledby"):
        return _squash(" ".join(_text(by_id[i], release) for i in a["aria-labelledby"].split() if i in by_id)) or None
    if a.get("id") in labels_for:
        return _text(labels_for[a["id"]], release)
    parent = node.parent
    while parent is not None:
        if parent.tag == "label":
            return _text(parent, release)
        parent = parent.parent
    if node.tag in NAMED_BY_TEXT or a.get("role") in {"button", "tab", "menuitem", "option", "switch"}:
        return _text(node, release) or None
    if a.get("title"):
        return a["title"]
    return None


@cache
def catalog() -> tuple[dict[str, Any], ...]:
    """Every authored element with an id: its page and v1 attributes (the raw nodes are kept for per-release names)."""
    items = []
    for page_override, root in _trees():
        by_id = {n.attrs["id"]: n for n in root.walk() if n.attrs.get("id")}
        labels_for = {n.attrs["for"]: n for n in root.walk() if n.tag == "label" and n.attrs.get("for")}
        for node in root.walk():
            key = node.attrs.get("id")
            if not key:
                continue
            page, parent = page_override, node
            while page is None and parent is not None:
                if parent.tag == "section" and parent.attrs.get("id", "").startswith("page-"):
                    page = parent.attrs["id"][5:]
                parent = parent.parent
            items.append({"key": key, "page": page or "shell", "node": node, "by_id": by_id, "labels_for": labels_for})
    return tuple(items)


def locator_map(release: str) -> dict[str, Any]:
    if release not in rules()["releases"]:
        raise ValueError(f"unknown release {release!r}; known: {', '.join(rules()['releases'])}")
    scheme = rules()["schemes"][release]
    keep_testid = bool(_rule("testid", release))
    retag = _rule("elements", release) or {}
    placeholders = _rule("placeholder", release) or {}
    elements = []
    for item in catalog():
        node, key = item["node"], item["key"]
        a = node.attrs
        tag, role = node.tag, _role(node, node.tag)
        if key in retag:
            tag, role = ("a", "link") if retag[key] == "link" else ("div", "button")
        if scheme == "authored":
            el_id, name = key, a.get("name")
        elif scheme == "renamed":
            el_id, name = "hm" + pascal(key), camel(a["name"]) if a.get("name") else None
        else:
            el_id, name = None, None
        if tag == "iframe":
            name = a.get("name")   # the frame's name is never renamed
        text = _text(node, release) if node.tag in NAMED_BY_TEXT or "data-t" in a else None
        elements.append({
            "key": key, "page": item["page"], "tag": tag, "role": role,
            "accessible_name": _name(node, item["by_id"], item["labels_for"], release),
            "id": el_id, "css": f"#{el_id}" if el_id else None, "name": name,
            "testid": a.get("data-testid") if keep_testid else None,
            "placeholder": placeholders.get(a["placeholder"], a["placeholder"]) if a.get("placeholder") else None,
            "text": text or None, "dynamic": el_id is None,
        })
    return {
        "release": release,
        "dynamic_ids": scheme == "random",
        "structure": {"swaps": _rule("swaps", release) or [], "fields_wrapped": bool(_rule("wrap_fields", release))},
        "note": "Ground truth for scoring heals: 'key' is the v1 (authored) id. In v3, v4 and under drift, ids, names "
                "and classes change on every load; read this load's values from window.HM.R.locatorMap() in the page. "
                "A framework under test must not use this map to heal.",
        "count": len(elements),
        "elements": elements,
    }
