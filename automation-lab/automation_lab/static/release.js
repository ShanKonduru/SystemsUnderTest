/* Harbor Mutual Automation Lab - UI releases, live drift and the in-page locator map.
 *
 * The page is authored once, as release v1. At load, HMRelease rewrites the DOM for the selected release, using the
 * rules in /static/releases.json (served to the page in /api/config), so the same behaviour sits behind different
 * locators - the ground a self-healing test framework has to cover:
 *
 *   v1 Baseline    ids, names, classes, data-testid exactly as authored.
 *   v2 Redesign    ids renamed (quote-first-name -> hmQuoteFirstName), names camelCased, classes prefixed (ui-), some
 *                  button texts and placeholders reworded, some fields swapped, every .field wrapped in an extra <div>.
 *                  data-testid kept. Deterministic: suits design-time healing (repair the script once).
 *   v3 Dynamic     ids, names and classes regenerated on every load, no data-testid, v2's texts and layout.
 *                  Suits run-time healing. Labels, roles and accessible names stay stable.
 *   v4 Structural  v3, plus reworded labels and ARIA names, different button texts, one more swap, and some buttons
 *                  turned into links or <div role="button">. Even label/role/text locators break.
 *
 * Drift (driftNow): renames every id, name and class again while the page is open (and drops data-testid), so a
 * locator found a moment ago stops matching. Behaviour is unaffected.
 *
 * App code never queries by the rendered locators: it uses $(origId) (an element registry captured before the
 * rewrite) and h() / C() / N() / Q() to create and find elements in the current naming.
 * locatorMap() returns this page load's ground truth (authored id -> current id, name, classes, testid, tag), for
 * scoring heals; a framework under test must not use it to heal.
 */
(function () {
  "use strict";

  const pascal = (s) => s.replace(/(^|[-_])([a-z0-9])/g, (_, __, c) => c.toUpperCase());
  const camel = (s) => { const p = pascal(s); return p.charAt(0).toLowerCase() + p.slice(1); };
  const reverse = (m) => new Map(Array.from(m, ([orig, cur]) => [cur, orig]));
  const ID_REFS = ["for", "aria-labelledby", "aria-describedby", "aria-controls", "aria-activedescendant", "list"];
  function token(n) {
    const bytes = new Uint8Array(n);
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => "abcdefghijklmnopqrstuvwxyz0123456789"[b % 36]).join("");
  }

  const R = {
    name: "v1", scheme: "authored", rules: { releases: ["v1"] }, generation: 0, drifted: false,
    reg: Object.create(null),      // authored id -> element (static page, shadow roots)
    roots: [],                     // trees rewritten so far (drift renames them again)
    _ids: new Map(), _cls: new Map(), _names: new Map(),

    init(name, rules) {
      this.rules = rules || this.rules;
      this.name = (this.rules.releases || ["v1"]).includes(name) ? name : "v1";
      this.scheme = (this.rules.schemes || {})[this.name] || "authored";
      return this;
    },
    rule(kind) { return ((this.rules[kind] || {})[this.name]) || null; },
    _map(store, orig, renamed, prefix, n) {
      if (!orig || this.scheme === "authored") return orig;
      if (!store.has(orig)) store.set(orig, this.scheme === "renamed" ? renamed(orig) : prefix + token(n));
      return store.get(orig);
    },
    id(orig) { return this._map(this._ids, orig, (o) => "hm" + pascal(o), "_", 7); },
    cls(orig) { return this._map(this._cls, orig, (o) => "ui-" + o, "css-", 6); },
    fname(orig) { return this._map(this._names, orig, camel, "f", 6); },   // form control name attribute
    tid(orig) { return this.drifted || (this.rules.testid || {})[this.name] === false ? null : orig; },
    text(orig) { const m = this.rule("text"); return (m && m[orig]) || orig; },
    placeholder(orig) { const m = this.rule("placeholder"); return (m && m[orig]) || orig; },

    /* Rewrite a static tree (the document, a shadow root, an iframe's document) for this release. */
    apply(root) {
      root.querySelectorAll("[id]").forEach((el) => { this.reg[el.id] = el; });
      (this.rule("swaps") || []).forEach(([a, b]) => {
        const x = this.reg[a], y = this.reg[b];
        if (!x || !y || !root.contains(x) || !root.contains(y)) return;
        const mark = document.createComment("swap");
        x.replaceWith(mark); y.replaceWith(x); mark.replaceWith(y);
      });
      if (this.rule("wrap_fields")) {
        root.querySelectorAll(".field").forEach((el) => {
          const wrap = document.createElement("div");
          wrap.setAttribute("class", "field-shell");
          el.before(wrap); wrap.append(el);
        });
      }
      root.querySelectorAll("[data-t]").forEach((el) => {
        el.textContent = this.text(el.textContent.trim());
        el.removeAttribute("data-t");
      });
      const labels = this.rule("labels");
      if (labels) {
        root.querySelectorAll("label, legend, label > span").forEach((el) => {
          const words = el.textContent.trim();
          if (!el.children.length && labels[words]) el.textContent = labels[words];
        });
      }
      const aria = this.rule("aria_labels");
      if (aria) root.querySelectorAll("[aria-label]").forEach((el) => {
        const words = el.getAttribute("aria-label");
        if (aria[words]) el.setAttribute("aria-label", aria[words]);
      });
      Object.entries(this.rule("elements") || {}).forEach(([key, kind]) => {
        const el = this.reg[key];
        if (el && root.contains(el)) this.reg[key] = retag(el, kind);
      });
      if (!this.roots.includes(root)) this.roots.push(root);
      if (this.scheme === "authored") return this;
      this._rename(root, (v) => v, (v) => v, (v) => v);
      root.querySelectorAll("[placeholder]").forEach((el) => {
        el.setAttribute("placeholder", this.placeholder(el.getAttribute("placeholder")));
      });
      return this;
    },

    /* Give every id, name and class in `root` its name in the current maps; origX() turns a current value back into
       the authored one (identity on the first rewrite, the old maps reversed on a drift). */
    _rename(root, origId, origCls, origName) {
      root.querySelectorAll("[id]").forEach((el) => { el.id = this.id(origId(el.id)); });
      ID_REFS.forEach((attr) => root.querySelectorAll(`[${attr}]`).forEach((el) => {
        el.setAttribute(attr, el.getAttribute(attr).split(/\s+/).filter(Boolean).map((v) => this.id(origId(v))).join(" "));
      }));
      root.querySelectorAll("[class]").forEach((el) => {
        el.setAttribute("class", el.getAttribute("class").split(/\s+/).filter(Boolean).map((c) => this.cls(origCls(c))).join(" "));
      });
      root.querySelectorAll("[name]").forEach((el) => {
        if (el.tagName !== "META" && el.tagName !== "IFRAME") el.setAttribute("name", this.fname(origName(el.getAttribute("name"))));
      });
      root.querySelectorAll("[data-testid]").forEach((el) => {
        const t = this.tid(el.getAttribute("data-testid"));
        if (t) el.setAttribute("data-testid", t); else el.removeAttribute("data-testid");
      });
      this.rewriteSheets(root.styleSheets || [], (c) => this.cls(origCls(c)));
    },

    /* Rename class selectors in same-origin style sheets so the page still looks right after a rename. */
    rewriteSheets(sheets, to) {
      const fix = (rules) => {
        for (const rule of Array.from(rules)) {
          if (rule.selectorText) rule.selectorText = rule.selectorText.replace(/\.(-?[_a-zA-Z][\w-]*)/g, (_, c) => "." + to(c));
          if (rule.cssRules) fix(rule.cssRules);
        }
      };
      for (const sheet of Array.from(sheets)) {
        try { fix(sheet.cssRules); } catch (e) { /* cross-origin sheet: not ours */ }
      }
    },

    /* Live drift: new random ids, names and classes for every tree rewritten so far; data-testid removed. */
    driftNow() {
      const ids = reverse(this._ids), cls = reverse(this._cls), names = reverse(this._names);
      this.scheme = "random"; this.drifted = true; this.generation++;
      this._ids = new Map(); this._cls = new Map(); this._names = new Map();
      this.roots = this.roots.filter((root) => root === document || root.isConnected || (root.host && root.host.isConnected));
      this.roots.forEach((root) => this._rename(root, (v) => ids.get(v) || v, (v) => cls.get(v) || v, (v) => names.get(v) || v));
      return this.generation;
    },

    /* Ground truth for this page load: authored id -> what that element is called right now. */
    locatorMap() {
      const elements = {};
      for (const [key, el] of Object.entries(this.reg)) {
        if (!el.isConnected) continue;
        elements[key] = { id: el.id || null, name: el.getAttribute("name"), testid: el.getAttribute("data-testid"),
          classes: el.getAttribute("class"), tag: el.tagName.toLowerCase(), role: el.getAttribute("role") };
      }
      return { release: this.name, generation: this.generation, drifted: this.drifted, elements };
    },
  };

  /* v4: turn a <button> into a link or a <div role="button"> that still clicks, focuses and honours `disabled`. */
  function retag(el, kind) {
    const n = document.createElement(kind === "link" ? "a" : "div");
    for (const a of Array.from(el.attributes)) if (a.name !== "type" && a.name !== "disabled") n.setAttribute(a.name, a.value);
    while (el.firstChild) n.append(el.firstChild);
    if (kind === "link") n.setAttribute("href", "javascript:void(0)");
    else { n.setAttribute("role", "button"); n.setAttribute("tabindex", "0"); }
    Object.defineProperty(n, "disabled", {
      configurable: true,
      get() { return n.getAttribute("aria-disabled") === "true"; },
      set(v) { if (v) n.setAttribute("aria-disabled", "true"); else n.removeAttribute("aria-disabled"); },
    });
    n.disabled = el.disabled;
    n.addEventListener("click", (e) => { if (n.disabled) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);
    n.addEventListener("keydown", (e) => {
      if (e.key === " " || (e.key === "Enter" && kind !== "link")) { e.preventDefault(); n.click(); }
    });
    el.replaceWith(n);
    return n;
  }

  /* Pages without /api/config (the payment frame, the receipt) read the rules file directly. */
  async function loadRules() {
    try { return await (await fetch("/static/releases.json")).json(); } catch (e) { return { releases: ["v1"] }; }
  }

  /* $(authoredId): the element, whatever it is called right now. */
  function $(orig) {
    return R.reg[orig] || document.getElementById(R.id(orig));
  }
  /* C("a b"): class names in the current naming (for classList and selectors). */
  function C(names) { return names.split(/\s+/).filter(Boolean).map((c) => R.cls(c)).join(" "); }
  /* N("first_name"): a form control's name right now. */
  function N(orig) { return R.fname(orig); }
  /* Q(".a .b", root): querySelectorAll with authored class names. */
  function Q(selector, root) {
    const s = selector.replace(/\.(-?[_a-zA-Z][\w-]*)/g, (_, c) => "." + R.cls(c));
    return Array.from((root || document).querySelectorAll(s));
  }

  /* h(tag, props, ...children): create an element in the current naming.
     props: id, class, testid, text (rewordable), for/aria-* (id refs), name, on<event>, anything else as attribute. */
  function h(tag, props, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "id") el.id = R.id(v);
      else if (k === "class") el.setAttribute("class", C(v));
      else if (k === "testid") { const t = R.tid(v); if (t) el.setAttribute("data-testid", t); }
      else if (k === "text") el.textContent = R.text(v);
      else if (k === "name") el.setAttribute("name", R.fname(v));
      else if (ID_REFS.includes(k)) el.setAttribute(k, String(v).split(/\s+/).map((x) => R.id(x)).join(" "));
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "value" && "value" in el) el.value = v;
      else if (k === "checked" || k === "disabled" || k === "selected" || k === "hidden") el[k] = !!v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  window.HMRelease = R;
  window.HM = { R, $, C, N, Q, h, loadRules };
})();
