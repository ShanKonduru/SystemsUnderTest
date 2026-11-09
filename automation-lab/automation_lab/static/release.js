/* Harbor Mutual Automation Lab - UI releases.
 *
 * The page is authored once, as release v1. At load, HMRelease rewrites the DOM for the selected release so the
 * same behaviour sits behind different locators - the ground a self-healing test framework has to cover:
 *
 *   v1 Baseline  ids, names, classes, data-testid exactly as authored.
 *   v2 Redesign  ids renamed (quote-first-name -> hmQuoteFirstName), names camelCased, classes prefixed (ui-),
 *                some button texts and placeholders reworded, some fields swapped, every .field wrapped in an
 *                extra <div> (absolute XPaths break). data-testid kept.
 *   v3 Dynamic   ids, names and classes regenerated on every load, data-testid removed, v2's texts and layout.
 *                Only labels, ARIA roles/names and visible text stay stable.
 *
 * App code never queries by the rendered locators: it uses $(origId) (an element registry captured before the
 * rewrite) and h() / C() / N() to create elements and classes in the current release's naming.
 * Authoring markers (data-t: rewordable text) are removed after the rewrite, in every release.
 */
(function () {
  "use strict";

  const TEXT_V2 = {
    "Get my quote": "Calculate premium",
    "Start over": "Clear form",
    "Next": "Continue",
    "Back": "Previous",
    "Submit claim": "File my claim",
    "Upload": "Upload files",
    "Browse files": "Select files",
    "Choose a photo": "Pick an image",
    "Export selected": "Download selected",
    "Search": "Find",
    "Open dialog": "Show dialog",
    "Load agent": "Find my agent",
    "Pay now": "Authorize payment",
    "Add coverage": "Include coverage",
    "File another claim": "Start a new claim",
  };
  const PLACEHOLDER_V2 = {
    "Policy number or name": "Search policies…",
    "you@example.com": "name@email.com",
    "Start typing a make…": "Vehicle make",
  };
  // pairs of element ids (as authored) whose positions are swapped in v2 and v3
  const SWAPS = [
    ["quote-field-email", "quote-field-phone"],
    ["quote-field-first-name", "quote-field-last-name"],
    ["claim-field-incident-type", "claim-field-incident-date"],
    ["docs-card-input", "docs-card-dropzone"],
  ];

  const pascal = (s) => s.replace(/(^|[-_])([a-z0-9])/g, (_, __, c) => c.toUpperCase());
  const camel = (s) => { const p = pascal(s); return p.charAt(0).toLowerCase() + p.slice(1); };
  function token(n) {
    const bytes = new Uint8Array(n);
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    return Array.from(bytes, (b) => "abcdefghijklmnopqrstuvwxyz0123456789"[b % 36]).join("");
  }

  const R = {
    name: "v1",
    reg: Object.create(null),      // authored id -> element (static page)
    _ids: new Map(), _cls: new Map(), _names: new Map(),

    init(name) { this.name = ["v1", "v2", "v3"].includes(name) ? name : "v1"; return this; },
    id(orig) {
      if (!orig || this.name === "v1") return orig;
      if (!this._ids.has(orig)) this._ids.set(orig, this.name === "v2" ? "hm" + pascal(orig) : "_" + token(7));
      return this._ids.get(orig);
    },
    cls(orig) {
      if (!orig || this.name === "v1") return orig;
      if (!this._cls.has(orig)) this._cls.set(orig, this.name === "v2" ? "ui-" + orig : "css-" + token(6));
      return this._cls.get(orig);
    },
    fname(orig) {   // form control name attribute
      if (!orig || this.name === "v1") return orig;
      if (!this._names.has(orig)) this._names.set(orig, this.name === "v2" ? camel(orig) : "f" + token(6));
      return this._names.get(orig);
    },
    tid(orig) { return this.name === "v3" ? null : orig; },
    text(orig) { return this.name === "v1" ? orig : (TEXT_V2[orig] || orig); },
    placeholder(orig) { return this.name === "v1" ? orig : (PLACEHOLDER_V2[orig] || orig); },

    /* Rewrite a static tree (the document, a shadow root, an iframe's document) for this release. */
    apply(root) {
      root.querySelectorAll("[id]").forEach((el) => { this.reg[el.id] = el; });
      if (this.name !== "v1") {
        SWAPS.forEach(([a, b]) => {
          const x = this.reg[a], y = this.reg[b];
          if (!x || !y || !root.contains(x) || !root.contains(y)) return;
          const mark = document.createComment("swap");
          x.replaceWith(mark); y.replaceWith(x); mark.replaceWith(y);
        });
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
      if (this.name === "v1") return this;
      root.querySelectorAll("[id]").forEach((el) => { el.id = this.id(el.id); });
      ["for", "aria-labelledby", "aria-describedby", "aria-controls", "aria-activedescendant", "list"].forEach((attr) => {
        root.querySelectorAll(`[${attr}]`).forEach((el) => {
          el.setAttribute(attr, el.getAttribute(attr).split(/\s+/).filter(Boolean).map((v) => this.id(v)).join(" "));
        });
      });
      root.querySelectorAll("[class]").forEach((el) => {
        el.setAttribute("class", el.getAttribute("class").split(/\s+/).filter(Boolean).map((c) => this.cls(c)).join(" "));
      });
      root.querySelectorAll("[name]").forEach((el) => {
        if (el.tagName !== "META" && el.tagName !== "IFRAME") el.setAttribute("name", this.fname(el.getAttribute("name")));
      });
      root.querySelectorAll("[data-testid]").forEach((el) => {
        const t = this.tid(el.getAttribute("data-testid"));
        if (t) el.setAttribute("data-testid", t); else el.removeAttribute("data-testid");
      });
      root.querySelectorAll("[placeholder]").forEach((el) => {
        el.setAttribute("placeholder", this.placeholder(el.getAttribute("placeholder")));
      });
      this.rewriteSheets(root.styleSheets || []);
      return this;
    },

    /* Rename class selectors in same-origin style sheets so the page still looks right after a rename. */
    rewriteSheets(sheets) {
      if (this.name === "v1") return;
      const fix = (rules) => {
        for (const rule of Array.from(rules)) {
          if (rule.selectorText) rule.selectorText = rule.selectorText.replace(/\.(-?[_a-zA-Z][\w-]*)/g, (_, c) => "." + this.cls(c));
          if (rule.cssRules) fix(rule.cssRules);
        }
      };
      for (const sheet of Array.from(sheets)) {
        try { fix(sheet.cssRules); } catch (e) { /* cross-origin sheet: not ours */ }
      }
    },
  };

  /* $(authoredId): the element, whatever it is called in this release. */
  function $(orig) {
    return R.reg[orig] || document.getElementById(R.id(orig));
  }
  /* C("a b"): class names in this release's naming (for classList and selectors). */
  function C(names) { return names.split(/\s+/).filter(Boolean).map((c) => R.cls(c)).join(" "); }
  /* N("first_name"): a form control's name in this release. */
  function N(orig) { return R.fname(orig); }
  /* Q(".a .b", root): querySelectorAll with authored class names. */
  function Q(selector, root) {
    const s = selector.replace(/\.(-?[_a-zA-Z][\w-]*)/g, (_, c) => "." + R.cls(c));
    return Array.from((root || document).querySelectorAll(s));
  }

  /* h(tag, props, ...children): create an element in this release's naming.
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
      else if (["for", "aria-labelledby", "aria-describedby", "aria-controls", "aria-activedescendant"].includes(k)) el.setAttribute(k, String(v).split(/\s+/).map((x) => R.id(x)).join(" "));
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
  window.HM = { R, $, C, N, Q, h };
})();
