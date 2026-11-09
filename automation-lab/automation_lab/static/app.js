/* Harbor Mutual Automation Lab - page behaviour.
 * Elements are reached through HM.$(authoredId) and created through HM.h(), so everything below works unchanged in
 * every UI release (see release.js). Never use document.getElementById / querySelector with authored names here. */
(function () {
  "use strict";
  const { R, $, C, N, Q, h } = window.HM;

  let CFG = { release: "v1", releases: {}, today: "2026-11-03", max_upload_bytes: 5 * 1024 * 1024, upload_types: [], ui_defects: [] };
  const slowUi = () => CFG.ui_defects.includes("slow_ui");

  // ================================================================== utilities
  const money = (n) => "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const whole = (n) => "$" + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
  const size = (b) => (b < 1024 ? b + " B" : b < 1048576 ? (b / 1024).toFixed(1) + " KB" : (b / 1048576).toFixed(1) + " MB");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const clock = () => new Date().toLocaleTimeString("en-US", { hour12: false });
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  function addDays(iso, n) { const d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
  function ageOn(born, today) {
    const b = new Date(born + "T00:00:00Z"), t = new Date(today + "T00:00:00Z");
    let a = t.getUTCFullYear() - b.getUTCFullYear();
    if (t.getUTCMonth() < b.getUTCMonth() || (t.getUTCMonth() === b.getUTCMonth() && t.getUTCDate() < b.getUTCDate())) a--;
    return a;
  }
  function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
  const PRODUCT_LABEL = { auto: "Auto", home: "Home", renters: "Renters", boat: "Boat", life: "Term life" };

  async function api(path, opts = {}) {
    const init = { ...opts };
    if ("json" in opts) { init.body = JSON.stringify(opts.json); init.headers = { "Content-Type": "application/json" }; delete init.json; }
    const r = await fetch(path, init);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const e = new Error(typeof data.detail === "string" ? data.detail : r.statusText || "Request failed");
      e.status = r.status; e.errors = data.errors || {};
      throw e;
    }
    return data;
  }
  /* Record a page event in the server's activity log (the run's independent record). */
  function track(kind, summary, detail) {
    api("/api/activity", { method: "POST", json: { kind, summary: summary.slice(0, 200), detail: detail || {} } })
      .then(refreshActivity).catch(() => {});
  }

  function uploadFiles(files, area, onProgress) {
    return new Promise((resolve, reject) => {
      const fd = new FormData();
      Array.from(files).forEach((f) => fd.append("files", f));
      fd.append("area", area);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/uploads");
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded * 100) / e.total)); };
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
        if (xhr.status < 300) resolve(data); else reject(new Error(typeof data.detail === "string" ? data.detail : "Upload failed"));
      };
      xhr.onerror = () => reject(new Error("Network error during upload"));
      xhr.send(fd);
    });
  }

  // ================================================================== toasts and the modal dialog
  function toast(text, kind, ms) {
    const t = h("div", { class: "toast" + (kind === "ok" ? " toast-ok" : kind === "err" ? " toast-err" : ""), role: kind === "err" ? "alert" : "status", testid: "toast" },
      h("span", {}, text),
      h("button", { class: "toast-close", type: "button", "aria-label": "Dismiss notification", onclick: () => t.remove() }, "×"));
    $("toasts").append(t);
    setTimeout(() => t.remove(), ms || 4000);
  }

  let modalReturn = null;
  /* actions: [{label, primary, testid, onClick}]; onClick returning false keeps the dialog open. */
  function openModal({ title, body, actions }) {
    $("modal-title").textContent = title;
    $("modal-body").replaceChildren(...[].concat(body).map((x) => (typeof x === "string" ? h("p", {}, x) : x)));
    $("modal-actions").replaceChildren(...actions.map((a) => h("button", {
      class: "btn" + (a.primary ? " btn-primary" : ""), type: "button", testid: a.testid,
      onclick: async () => { if ((await (a.onClick ? a.onClick() : undefined)) !== false) closeModal(); },
    }, a.label)));
    modalReturn = document.activeElement;
    $("modal").hidden = false;
    const first = $("modal-body").querySelector("input, select, textarea") || $("modal-actions").lastElementChild;
    if (first) first.focus();
  }
  function closeModal() {
    if ($("modal").hidden) return;
    $("modal").hidden = true;
    if (modalReturn && modalReturn.focus) modalReturn.focus();
  }
  function wireModal() {
    $("modal").addEventListener("mousedown", (e) => { if (e.target === $("modal")) closeModal(); });
    document.addEventListener("keydown", (e) => {
      if ($("modal").hidden) return;
      if (e.key === "Escape") { e.preventDefault(); closeModal(); return; }
      if (e.key === "Tab") {   // keep focus inside the dialog
        const f = Array.from($("modal-dialog").querySelectorAll("button, input, select, textarea, [tabindex]")).filter((x) => !x.disabled && x.tabIndex >= 0);
        if (!f.length) return;
        const i = f.indexOf(document.activeElement);
        if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); } else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
      }
    });
  }

  /* A floating menu (row actions, context menu). items: [{label, testid, onClick}] */
  let openMenuEl = null;
  function closeMenu() { if (openMenuEl) { openMenuEl.remove(); openMenuEl = null; } $("context-menu").hidden = true; }
  function popupMenu(anchor, items, label) {
    closeMenu();
    const menu = h("ul", { class: "menu", role: "menu", "aria-label": label, testid: "row-menu" },
      items.map((it) => h("li", { role: "none" }, h("button", { class: "menu-item", role: "menuitem", type: "button", testid: it.testid, onclick: () => { closeMenu(); it.onClick(); } }, it.label))));
    document.body.append(menu);
    const r = anchor.getBoundingClientRect();
    menu.style.top = window.scrollY + r.bottom + 4 + "px";
    menu.style.left = Math.max(8, Math.min(window.scrollX + r.right - 170, window.innerWidth - 190)) + "px";
    openMenuEl = menu;
    menu.querySelector("button").focus();
    menuKeys(menu);
  }
  function menuKeys(menu) {
    menu.addEventListener("keydown", (e) => {
      const items = Array.from(menu.querySelectorAll("button"));
      const i = items.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
      if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
      if (e.key === "Escape") { e.preventDefault(); closeMenu(); }
    });
  }

  function setFieldError(errId, input, msg) {
    const el = $(errId);
    if (el) el.textContent = msg || "";
    if (input) { if (msg) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid"); }
  }
  const byName = (form, name) => Array.from(form.querySelectorAll(`[name="${N(name)}"]`));
  const checkedValues = (form, name) => byName(form, name).filter((x) => x.checked).map((x) => x.value);

  // ================================================================== router
  const PAGES = ["overview", "quote", "claim", "documents", "board", "policies", "interactions", "dynamic", "activity"];
  const TITLES = { overview: "Overview", quote: "Get a quote", claim: "File a claim", documents: "Documents & uploads", board: "Drag & drop", policies: "Policies table", interactions: "Interactions", dynamic: "Waits & dynamic content", activity: "Activity log" };
  const pageInit = {}, pageShow = {}, pageHide = {}, inited = new Set();
  let current = null;
  function route() {
    const m = location.hash.match(/^#\/([\w-]+)/);
    const page = m && PAGES.includes(m[1]) ? m[1] : "overview";
    if (current && current !== page && pageHide[current]) pageHide[current]();
    PAGES.forEach((p) => {
      $("page-" + p).hidden = p !== page;
      const link = $("nav-" + p);
      if (link) { if (p === page) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current"); }
    });
    if (!inited.has(page)) { inited.add(page); if (pageInit[page]) pageInit[page](); }
    if (pageShow[page]) pageShow[page]();
    current = page;
    document.title = TITLES[page] + " · Harbor Mutual Automation Lab";
    $("sidenav").classList.remove(C("sidenav-open"));
    $("nav-toggle").setAttribute("aria-expanded", "false");
    closeMenu();
    window.scrollTo(0, 0);
  }

  // ================================================================== quote
  const MAKES = ["Acura", "Audi", "BMW", "Buick", "Chevrolet", "Chrysler", "Dodge", "Ford", "GMC", "Honda", "Hyundai", "Jeep", "Kia", "Lexus", "Mazda", "Mercedes-Benz", "Nissan", "Ram", "Subaru", "Tesla", "Toyota", "Volkswagen", "Volvo"];
  const DEDUCTIBLES = [500, 1000, 2500];
  const ADDON_PRODUCTS = { roadside: ["auto"], rental: ["auto"], glass: ["auto", "boat"], flood: ["home", "renters", "boat"], jewelry: ["home", "renters"] };
  const QUOTE_FIELDS = {   // server field -> [input to mark, error element]
    product: ["quote-product", "quote-product-error"], coverage: ["quote-coverage-basic", "quote-coverage-error"],
    start_date: ["quote-start-date", "quote-start-date-error"], addons: ["quote-addon-roadside", "quote-addons-error"],
    first_name: ["quote-first-name", "quote-first-name-error"], last_name: ["quote-last-name", "quote-last-name-error"],
    date_of_birth: ["quote-dob", "quote-dob-error"], email: ["quote-email", "quote-email-error"],
    phone: ["quote-phone", "quote-phone-error"], zip: ["quote-zip", "quote-zip-error"],
    payment_plan: ["quote-plan-annual", "quote-payment-plan-error"], consent: ["quote-consent", "quote-consent-error"],
  };

  function quoteBody() {
    const form = $("quote-form"), v = (id) => $(id).value.trim();
    const product = v("quote-product");
    const details = product === "auto" ? { vehicle_make: v("quote-make"), vehicle_year: v("quote-vehicle-year"), mileage: v("quote-mileage") }
      : product === "home" || product === "renters" ? { property_type: v("quote-property-type"), square_feet: v("quote-square-feet") }
      : product === "boat" ? { boat_length: v("quote-boat-length"), hull: checkedValues(form, "hull")[0] } : {};
    return {
      product, coverage: checkedValues(form, "coverage")[0] || "", deductible: DEDUCTIBLES[Number($("quote-deductible").value)],
      addons: checkedValues(form, "addons"), first_name: v("quote-first-name"), last_name: v("quote-last-name"),
      date_of_birth: v("quote-dob"), email: v("quote-email"), phone: v("quote-phone"), zip: v("quote-zip"),
      start_date: v("quote-start-date"), payment_plan: checkedValues(form, "payment_plan")[0] || "",
      paperless: $("quote-paperless").getAttribute("aria-checked") === "true", multi_policy: $("quote-multi-policy").checked,
      notes: $("quote-notes").value, consent: $("quote-consent").checked, details,
    };
  }
  function validateQuote(b) {
    const e = {}, today = CFG.today;
    if (!PRODUCT_LABEL[b.product]) e.product = "Choose a product.";
    if (!b.coverage) e.coverage = "Choose a coverage level.";
    if (!b.first_name) e.first_name = "Enter your first name.";
    if (!b.last_name) e.last_name = "Enter your last name.";
    if (!/^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(b.email)) e.email = "Enter a valid email address.";
    if (b.phone && !/^\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}$/.test(b.phone)) e.phone = "Enter a 10-digit phone number.";
    if (!/^\d{5}$/.test(b.zip)) e.zip = "Enter a 5-digit ZIP code.";
    if (!b.date_of_birth) e.date_of_birth = "Enter your date of birth.";
    else { const a = ageOn(b.date_of_birth, today); if (a < 18 || a > 100) e.date_of_birth = "You must be between 18 and 100 years old."; }
    if (!b.start_date) e.start_date = "Choose a coverage start date.";
    else if (b.start_date < today || b.start_date > addDays(today, 90)) e.start_date = "Coverage must start within the next 90 days.";
    if (!b.consent) e.consent = "You must agree to be contacted about this quote.";
    return e;
  }
  function showQuoteErrors(errors) {
    Object.entries(QUOTE_FIELDS).forEach(([k, [input, err]]) => setFieldError(err, $(input), errors[k]));
    const keys = Object.keys(errors);
    const alert = $("quote-form-alert");
    alert.hidden = !keys.length;
    alert.textContent = keys.length ? `Please correct ${keys.length} field${keys.length > 1 ? "s" : ""} before we can price your quote.` : "";
    if (keys.length && QUOTE_FIELDS[keys[0]]) $(QUOTE_FIELDS[keys[0]][0]).focus();
  }
  function quoteProductChanged() {
    const p = $("quote-product").value;
    $("quote-auto-set").hidden = p !== "auto";
    $("quote-home-set").hidden = !(p === "home" || p === "renters");
    $("quote-boat-set").hidden = p !== "boat";
    Object.entries(ADDON_PRODUCTS).forEach(([addon, products]) => {
      const box = $("quote-addon-" + addon);
      box.disabled = !!p && !products.includes(p);
      if (box.disabled) box.checked = false;
    });
  }
  function setDeductible() {
    const amount = DEDUCTIBLES[Number($("quote-deductible").value)];
    $("quote-deductible-value").textContent = whole(amount);
    $("quote-deductible").setAttribute("aria-valuetext", whole(amount));
  }
  function wireCombobox() {
    const input = $("quote-make"), list = $("quote-make-list");
    let active = -1, options = [];
    const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); active = -1; };
    const choose = (make) => { input.value = make; close(); };
    const highlight = (i) => {
      options.forEach((o, j) => o.setAttribute("aria-selected", j === i ? "true" : "false"));
      active = i;
      if (options[i]) { input.setAttribute("aria-activedescendant", options[i].id); options[i].scrollIntoView({ block: "nearest" }); }
    };
    const render = debounce(() => {   // the suggestions arrive a moment after typing, like a remote lookup
      const q = input.value.trim().toLowerCase();
      if (!q) { close(); return; }
      const found = MAKES.filter((m) => m.toLowerCase().includes(q)).slice(0, 8);
      options = found.map((m, i) => h("li", { class: "option", role: "option", id: "quote-make-option-" + i, "aria-selected": "false",
        onmousedown: (e) => { e.preventDefault(); choose(m); } }, m));
      list.replaceChildren(...(options.length ? options : [h("li", { class: "option muted", role: "option", "aria-disabled": "true" }, "No matching make")]));
      list.hidden = false; input.setAttribute("aria-expanded", "true"); active = -1;
    }, 250);
    input.addEventListener("input", render);
    input.addEventListener("keydown", (e) => {
      if (list.hidden || !options.length) return;
      if (e.key === "ArrowDown") { e.preventDefault(); highlight((active + 1) % options.length); }
      else if (e.key === "ArrowUp") { e.preventDefault(); highlight((active - 1 + options.length) % options.length); }
      else if (e.key === "Enter" && active >= 0) { e.preventDefault(); choose(options[active].textContent); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
    });
    input.addEventListener("blur", () => setTimeout(close, 120));
  }
  function resetQuoteResult() {
    $("quote-result-empty").hidden = false; $("quote-result-loading").hidden = true; $("quote-result-body").hidden = true;
  }
  function renderQuote(res, body) {
    $("quote-result-loading").hidden = true;
    $("quote-result-body").hidden = false;
    $("quote-ref").textContent = res.quote_id;
    $("quote-premium").textContent = money(res.annual_premium) + " / year";
    $("quote-monthly").textContent = res.monthly_payment ? `or ${money(res.monthly_payment)} a month` : "Paid once a year";
    $("quote-breakdown").replaceChildren(...res.breakdown.map((line, i) => h("tr", { id: "quote-line-" + i },
      h("td", {}, line.label), h("td", { class: "num" }, (line.amount < 0 ? "−" : "") + money(Math.abs(line.amount))))),
      h("tr", {}, h("th", { scope: "row" }, "Annual premium"), h("th", { class: "num" }, money(res.annual_premium))));
    $("quote-discount-note").hidden = !body.multi_policy;
    const blob = new Blob([JSON.stringify({ ...res, request: body }, null, 2)], { type: "application/json" });
    const link = $("quote-download");
    if (link.href.startsWith("blob:")) URL.revokeObjectURL(link.href);
    link.href = URL.createObjectURL(blob);
    link.setAttribute("download", res.quote_id + ".json");
  }
  pageInit.quote = () => {
    const form = $("quote-form");
    $("quote-start-date").min = CFG.today; $("quote-start-date").max = addDays(CFG.today, 90);
    $("quote-dob").max = CFG.today;
    $("quote-product").addEventListener("change", quoteProductChanged);
    $("quote-deductible").addEventListener("input", setDeductible);
    const sw = $("quote-paperless");
    const flip = () => sw.setAttribute("aria-checked", sw.getAttribute("aria-checked") === "true" ? "false" : "true");
    sw.addEventListener("click", flip);
    sw.addEventListener("keydown", (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); flip(); } });
    $("quote-notes").addEventListener("input", () => { $("quote-notes-count").textContent = `${$("quote-notes").value.length} / 500`; });
    wireCombobox();
    // validate a field when it loses focus, once it has been touched
    Object.entries(QUOTE_FIELDS).forEach(([k, [input, err]]) => {
      $(input).addEventListener("blur", () => {
        const e = validateQuote(quoteBody());
        if ($(input).value || e[k] === undefined) setFieldError(err, $(input), e[k]);
      });
    });
    form.addEventListener("reset", () => {
      setTimeout(() => {
        showQuoteErrors({}); quoteProductChanged(); setDeductible(); resetQuoteResult();
        sw.setAttribute("aria-checked", "false"); $("quote-notes-count").textContent = "0 / 500";
      });
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = quoteBody();
      const errors = validateQuote(body);
      showQuoteErrors(errors);
      if (Object.keys(errors).length) return;
      const btn = $("quote-submit");
      btn.disabled = true; form.setAttribute("aria-busy", "true");
      $("quote-result-empty").hidden = true; $("quote-result-body").hidden = true; $("quote-result-loading").hidden = false;
      const started = Date.now();
      try {
        const res = await api("/api/quotes", { method: "POST", json: body });
        await sleep(Math.max(0, (slowUi() ? 8000 : 800) - (Date.now() - started)));
        renderQuote(res, body);
        refreshActivity();
      } catch (err) {
        resetQuoteResult();
        if (err.status === 422) showQuoteErrors(err.errors); else toast(err.message, "err");
      } finally {
        btn.disabled = false; form.removeAttribute("aria-busy");
      }
    });
  };

  // ================================================================== claim wizard
  const claim = { step: 1, policy: null, lookup: null, files: [] };
  const CLAIM_FIELDS = {   // server field -> [step, input, error element]
    policy_number: [1, "claim-policy", "claim-policy-error"], incident_type: [1, "claim-incident-type", "claim-incident-type-error"],
    incident_date: [1, "claim-incident-date", "claim-incident-date-error"], location: [1, "claim-location", "claim-location-error"],
    description: [2, "claim-description", "claim-description-error"], report_number: [2, "claim-report-number", "claim-report-number-error"],
    estimate: [2, "claim-estimate", "claim-estimate-error"], contact: [2, "claim-contact-email", "claim-contact-error"],
    signature: [4, "claim-signature", "claim-signature-error"], agree: [4, "claim-agree", "claim-agree-error"],
  };
  const INCIDENT_LABEL = { collision: "Collision", theft: "Theft or break-in", water: "Water damage", fire: "Fire or smoke", weather: "Storm or hail", other: "Something else" };

  function claimBody() {
    const form = $("claim-form"), v = (id) => $(id).value.trim();
    const police = checkedValues(form, "police_report")[0] === "yes";
    return {
      policy_number: v("claim-policy").toUpperCase(), incident_type: v("claim-incident-type"), incident_date: v("claim-incident-date"),
      incident_time: v("claim-incident-time"), location: v("claim-location"), description: v("claim-description"),
      police_report: police, report_number: police ? v("claim-report-number") : "",
      estimate: v("claim-estimate") === "" ? null : Number(v("claim-estimate")), injuries: $("claim-injuries").checked,
      contact: checkedValues(form, "contact")[0] || "", available_days: Array.from($("claim-days").selectedOptions).map((o) => o.value),
      upload_ids: claim.files.map((f) => f.id), signature: v("claim-signature"), agree: $("claim-agree").checked,
    };
  }
  async function lookupPolicy() {
    const number = $("claim-policy").value.trim().toUpperCase();
    const note = $("claim-policy-holder");
    claim.policy = null;
    if (!/^POL-\d{4}$/.test(number)) { note.hidden = true; return null; }
    const p = api("/api/policies/" + encodeURIComponent(number)).catch(() => null);
    claim.lookup = p;
    const found = await p;
    if (claim.lookup !== p) return claim.policy;   // a newer lookup is running
    claim.policy = found;
    note.hidden = false;
    note.textContent = found ? `Policyholder: ${found.holder} · ${PRODUCT_LABEL[found.product]} · ${cap(found.status)}` : `No policy ${number} was found.`;
    return found;
  }
  async function validateClaimStep(step) {
    const b = claimBody(), e = {}, today = CFG.today;
    if (step === 1) {
      const p = claim.policy && claim.policy.policy_number === b.policy_number ? claim.policy : await lookupPolicy();
      if (!p) e.policy_number = "We couldn't find that policy number.";
      else if (p.status !== "active") e.policy_number = `Policy ${p.policy_number} is ${p.status}; claims need an active policy.`;
      if (!b.incident_type) e.incident_type = "Choose what happened.";
      if (!b.incident_date) e.incident_date = "Enter the date of the incident.";
      else if (b.incident_date > today || b.incident_date < addDays(today, -365)) e.incident_date = "The incident must be within the last 12 months and not in the future.";
      if (!b.location) e.location = "Enter where it happened.";
    } else if (step === 2) {
      if (b.description.length < 20) e.description = "Describe what happened in 20 to 2000 characters.";
      if (b.police_report && !b.report_number) e.report_number = "Enter the police report number.";
      if (!(b.estimate > 0 && b.estimate <= 1000000)) e.estimate = "Enter an estimate between $1 and $1,000,000.";
      if (!b.contact) e.contact = "Choose how we should contact you.";
    } else if (step === 4) {
      if (!claim.policy || b.signature.replace(/\s+/g, " ").toLowerCase() !== claim.policy.holder.toLowerCase()) e.signature = "Type the policyholder's full name exactly as shown to sign.";
      if (!b.agree) e.agree = "You must confirm the information is true.";
    }
    return e;
  }
  function showClaimErrors(errors, step) {
    Object.entries(CLAIM_FIELDS).forEach(([k, [s, input, err]]) => { if (step === undefined || s === step) setFieldError(err, $(input), errors[k]); });
    const keys = Object.keys(errors);
    const alert = $("claim-form-alert");
    alert.hidden = !keys.length;
    alert.textContent = keys.length ? `Please correct ${keys.length} field${keys.length > 1 ? "s" : ""} on this step.` : "";
    if (keys.length && CLAIM_FIELDS[keys[0]]) $(CLAIM_FIELDS[keys[0]][1]).focus();
  }
  function renderClaimReview() {
    const b = claimBody();
    const rows = [
      ["Policy", b.policy_number], ["Policyholder", claim.policy ? claim.policy.holder : "—"],
      ["What happened", INCIDENT_LABEL[b.incident_type] || "—"], ["When", b.incident_date + (b.incident_time ? " at " + b.incident_time : "")],
      ["Where", b.location], ["Description", b.description.length > 140 ? b.description.slice(0, 140) + "…" : b.description],
      ["Police report", b.police_report ? "Yes, " + b.report_number : "No"], ["Estimate", b.estimate ? money(b.estimate) : "—"],
      ["Injuries", b.injuries ? "Yes" : "No"], ["Contact by", cap(b.contact || "—")],
      ["Available", b.available_days.length ? b.available_days.map(cap).join(", ") : "Any day"],
      ["Documents", claim.files.length ? claim.files.map((f) => f.name).join(", ") : "None"],
    ];
    $("claim-review").replaceChildren(...rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
  }
  function showClaimStep(step) {
    claim.step = step;
    [1, 2, 3, 4].forEach((s) => {
      $("claim-step-" + s).hidden = s !== step;
      const tab = $("claim-step-tab-" + s);
      if (s === step) tab.setAttribute("aria-current", "step"); else tab.removeAttribute("aria-current");
      tab.classList.toggle(C("step-done"), s < step);
    });
    $("claim-back").disabled = step === 1;
    $("claim-next").hidden = step === 4;
    $("claim-submit").hidden = step !== 4;
    if (step === 4) renderClaimReview();
    $("claim-form-alert").hidden = true;
    const first = $("claim-step-" + step).querySelector("input:not([type=hidden]), select, textarea");
    if (first) first.focus({ preventScroll: true });
  }
  function renderClaimFiles() {
    $("claim-file-list").replaceChildren(...claim.files.map((f) => h("li", { class: "file-item" },
      h("span", { class: "file-name" }, f.name), h("span", { class: "small muted mono" }, f.id),
      h("button", { class: "btn btn-small btn-danger", type: "button", "aria-label": "Remove " + f.name,
        onclick: () => { claim.files = claim.files.filter((x) => x !== f); renderClaimFiles(); } }, "Remove"))));
  }
  async function attachClaimFiles(files) {
    if (!files.length) return;
    $("claim-next").disabled = true;
    try {
      const res = await uploadFiles(files, "claim");
      claim.files.push(...res.uploaded.map((u) => ({ id: u.id, name: u.name })));
      res.rejected.forEach((r) => toast(`${r.name}: ${r.reason}`, "err", 6000));
      renderClaimFiles();
    } catch (err) { toast(err.message, "err"); }
    finally { $("claim-next").disabled = false; $("claim-files").value = ""; }
  }
  function resetClaim() {
    $("claim-form").reset();
    claim.policy = null; claim.files = [];
    renderClaimFiles();
    $("claim-policy-holder").hidden = true; $("claim-field-report-number").hidden = true;
    $("claim-description-count").textContent = "0 / 2000 (at least 20)";
    showClaimErrors({});
    $("claim-done").hidden = true; $("claim-actions").hidden = false; $("claim-stepper").hidden = false;
    showClaimStep(1);
  }
  pageInit.claim = () => {
    const form = $("claim-form");
    $("claim-incident-date").max = CFG.today; $("claim-incident-date").min = addDays(CFG.today, -365);
    api("/api/policies?status=active&size=100").then((res) => {
      $("claim-policy-options").replaceChildren(...res.items.map((p) => h("option", { value: p.policy_number }, `${p.holder} · ${PRODUCT_LABEL[p.product]}`)));
    }).catch(() => {});
    $("claim-policy").addEventListener("input", debounce(lookupPolicy, 300));
    $("claim-policy").addEventListener("change", lookupPolicy);
    byName(form, "police_report").forEach((r) => r.addEventListener("change", () => { $("claim-field-report-number").hidden = checkedValues(form, "police_report")[0] !== "yes"; }));
    $("claim-description").addEventListener("input", () => { $("claim-description-count").textContent = `${$("claim-description").value.length} / 2000 (at least 20)`; });
    $("claim-files").addEventListener("change", () => attachClaimFiles(Array.from($("claim-files").files)));
    wireDropzone($("claim-dropzone"), (files) => attachClaimFiles(files), () => $("claim-files").click());
    $("claim-next").addEventListener("click", async () => {
      const errors = await validateClaimStep(claim.step);
      showClaimErrors(errors, claim.step);
      if (!Object.keys(errors).length) showClaimStep(claim.step + 1);
    });
    $("claim-back").addEventListener("click", () => showClaimStep(Math.max(1, claim.step - 1)));
    $("claim-again").addEventListener("click", resetClaim);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (claim.step !== 4) { $("claim-next").click(); return; }
      const errors = await validateClaimStep(4);
      showClaimErrors(errors, 4);
      if (Object.keys(errors).length) return;
      const btn = $("claim-submit");
      btn.disabled = true;
      try {
        const res = await api("/api/claims", { method: "POST", json: claimBody() });
        [1, 2, 3, 4].forEach((s) => { $("claim-step-" + s).hidden = true; });
        $("claim-actions").hidden = true; $("claim-stepper").hidden = true;
        $("claim-number").textContent = res.claim_id;
        $("claim-done").hidden = false;
        $("claim-again").focus();
        refreshActivity();
      } catch (err) {
        if (err.status === 422) {
          const first = Object.keys(err.errors).map((k) => (CLAIM_FIELDS[k] || [4])[0]).sort()[0] || 4;
          showClaimStep(first);
          showClaimErrors(err.errors, first);
        } else toast(err.message, "err");
      } finally { btn.disabled = false; }
    });
    showClaimStep(1);
  };

  // ================================================================== documents
  function wireDropzone(zone, onFiles, onBrowse) {
    let depth = 0;
    zone.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; zone.classList.add(C("dropzone-active")); });
    zone.addEventListener("dragover", (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
    zone.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) zone.classList.remove(C("dropzone-active")); });
    zone.addEventListener("drop", (e) => {
      e.preventDefault(); e.stopPropagation(); depth = 0; zone.classList.remove(C("dropzone-active"));
      const files = Array.from(e.dataTransfer.files || []);
      if (files.length) onFiles(files);
    });
    zone.addEventListener("click", onBrowse);
    zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onBrowse(); } });
  }
  async function refreshDocs() {
    const rows = await api("/api/uploads").catch(() => []);
    $("docs-empty").hidden = rows.length > 0;
    $("docs-tbody").replaceChildren(...rows.slice().reverse().map((u) => {
      const actions = h("td", {});
      const normal = () => actions.replaceChildren(
        h("a", { class: "btn btn-small", href: "/api/uploads/" + u.id, download: u.name, "aria-label": "Download " + u.name }, "Download"), " ",
        h("button", { class: "btn btn-small btn-danger", type: "button", "aria-label": "Delete " + u.name, onclick: confirmDelete }, "Delete"));
      function confirmDelete() {
        actions.replaceChildren(h("span", { class: "small" }, "Delete this file? "),
          h("button", { class: "btn btn-small btn-danger", type: "button", "aria-label": "Confirm delete " + u.name, onclick: async () => {
            try { await api("/api/uploads/" + u.id, { method: "DELETE" }); toast(`Deleted ${u.name}`, "ok"); } catch (err) { toast(err.message, "err"); }
            refreshDocs(); refreshActivity();
          } }, "Yes, delete"), " ",
          h("button", { class: "btn btn-small", type: "button", onclick: normal }, "Keep"));
      }
      normal();
      return h("tr", { id: "upload-row-" + u.id },
        h("td", { class: "mono" }, u.id), h("td", {}, u.name), h("td", {}, cap(u.area)), h("td", { class: "num" }, size(u.size)),
        h("td", {}, u.created.replace("T", " ").replace("+00:00", "")), actions);
    }));
  }
  function resultItem(text, ok) { return h("li", { class: "file-item" + (ok ? "" : " file-rejected") }, h("span", { class: "file-name" }, text)); }
  pageInit.documents = () => {
    $("docs-limit").textContent = size(CFG.max_upload_bytes);
    const input = $("docs-file-input");
    // no accept filter on this input: picking a disallowed type and seeing the server reject it is part of the lab
    input.addEventListener("change", () => {
      const n = input.files.length;
      $("docs-upload").disabled = !n;
      $("docs-input-status").textContent = n ? `${n} file${n > 1 ? "s" : ""} selected` : "";
    });
    $("docs-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!input.files.length) return;
      $("docs-upload").disabled = true; $("docs-input-status").textContent = "Uploading…";
      try {
        const res = await uploadFiles(input.files, "documents");
        $("docs-input-status").textContent = `Uploaded ${res.uploaded.length} file${res.uploaded.length === 1 ? "" : "s"}` + (res.rejected.length ? `, ${res.rejected.length} rejected` : "");
        res.rejected.forEach((r) => toast(`${r.name}: ${r.reason}`, "err", 6000));
        if (res.uploaded.length) toast(`Uploaded ${res.uploaded.map((u) => u.name).join(", ")}`, "ok");
        $("docs-form").reset();
      } catch (err) { $("docs-input-status").textContent = err.message; }
      refreshDocs(); refreshActivity();
    });

    const dz = $("docs-dropzone"), dzInput = $("docs-dropzone-input");
    const send = async (files) => {
      const bar = $("docs-progress"), fill = $("docs-progress-bar");
      bar.hidden = false; fill.style.width = "0%"; bar.setAttribute("aria-valuenow", "0");
      try {
        const res = await uploadFiles(files, "dropzone", (p) => { fill.style.width = p + "%"; bar.setAttribute("aria-valuenow", String(p)); });
        fill.style.width = "100%"; bar.setAttribute("aria-valuenow", "100");
        $("docs-drop-results").prepend(...res.uploaded.map((u) => resultItem(`✓ ${u.name} uploaded as ${u.id}`, true)),
          ...res.rejected.map((r) => resultItem(`✗ ${r.name}: ${r.reason}`, false)));
      } catch (err) { $("docs-drop-results").prepend(resultItem("✗ " + err.message, false)); }
      setTimeout(() => { bar.hidden = true; }, 800);
      refreshDocs(); refreshActivity();
    };
    wireDropzone(dz, send, () => dzInput.click());
    dzInput.addEventListener("change", () => { if (dzInput.files.length) send(Array.from(dzInput.files)); dzInput.value = ""; });

    const photo = $("docs-photo-input");
    $("docs-photo-button").addEventListener("click", () => photo.click());
    photo.addEventListener("change", () => {
      const f = photo.files[0];
      if (!f) return;
      const reader = new FileReader();
      reader.onload = () => { $("docs-photo-preview").src = reader.result; $("docs-photo-name").textContent = `${f.name} · ${size(f.size)}`; $("docs-photo-preview-wrap").hidden = false; };
      reader.readAsDataURL(f);
    });
    $("docs-photo-clear").addEventListener("click", () => { photo.value = ""; $("docs-photo-preview-wrap").hidden = true; });
    $("docs-photo-upload").addEventListener("click", async () => {
      if (!photo.files[0]) return;
      try {
        const res = await uploadFiles([photo.files[0]], "photo");
        if (res.uploaded.length) toast(`Photo uploaded as ${res.uploaded[0].id}`, "ok");
        res.rejected.forEach((r) => toast(`${r.name}: ${r.reason}`, "err", 6000));
        photo.value = ""; $("docs-photo-preview-wrap").hidden = true;
      } catch (err) { toast(err.message, "err"); }
      refreshDocs(); refreshActivity();
    });
    $("docs-refresh").addEventListener("click", refreshDocs);
  };
  pageShow.documents = refreshDocs;

  // ================================================================== drag & drop
  const COLUMNS = ["new", "review", "approved", "paid"];
  const COLUMN_LABEL = { new: "New", review: "In review", approved: "Approved", paid: "Paid" };
  const cardOf = new WeakMap();
  function cardEl(c) {
    const el = h("div", { class: "kcard", role: "listitem", draggable: "true", id: "card-" + c.id, testid: "card-" + c.id,
      "aria-label": `${c.id}, ${c.holder}, ${money(c.amount)}` },
      h("div", { class: "kcard-top" }, h("span", {}, c.id), h("span", { class: "small muted" }, whole(c.amount))),
      h("div", { class: "small" }, c.holder), h("div", { class: "small muted" }, cap(c.type)),
      h("div", { class: "kcard-moves" },
        h("button", { class: "btn btn-small", type: "button", "aria-label": `Move ${c.id} left`, onclick: () => moveCard(c.id, -1) }, "◀"),
        h("button", { class: "btn btn-small", type: "button", "aria-label": `Move ${c.id} right`, onclick: () => moveCard(c.id, 1) }, "▶")));
    cardOf.set(el, c);
    el.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", c.id); e.dataTransfer.effectAllowed = "move"; el.classList.add(C("kcard-dragging")); });
    el.addEventListener("dragend", () => el.classList.remove(C("kcard-dragging")));
    return el;
  }
  const laneCards = (lane) => Q(".kcard", lane);
  function updateLaneCounts() { COLUMNS.forEach((col) => { $("lane-" + col + "-count").textContent = String(laneCards($("lane-" + col)).length); }); }
  async function loadBoard() {
    const cards = await api("/api/board").catch(() => []);
    COLUMNS.forEach((col) => { const lane = $("lane-" + col); laneCards(lane).forEach((x) => x.remove()); });
    cards.forEach((c) => $("lane-" + c.column).append(cardEl(c)));
    updateLaneCounts();
  }
  async function placeCard(id, column, position) {
    const el = $("card-" + id);
    const lane = $("lane-" + column);
    const others = laneCards(lane).filter((x) => x !== el);
    const from = cardOf.get(el).column;
    if (others[position]) lane.insertBefore(el, others[position]); else lane.append(el);
    updateLaneCounts();
    try {
      await api("/api/board/cards/" + encodeURIComponent(id), { method: "PUT", json: { column, position } });
      cardOf.get(el).column = column;
      $("board-status").textContent = `Moved ${id} from ${COLUMN_LABEL[from]} to ${COLUMN_LABEL[column]}`;
      refreshActivity();
    } catch (err) {
      toast("Couldn't move the card: " + err.message, "err");
      loadBoard();
    }
  }
  function moveCard(id, delta) {
    const c = cardOf.get($("card-" + id));
    const i = COLUMNS.indexOf(c.column) + delta;
    if (i < 0 || i >= COLUMNS.length) return;
    placeCard(id, COLUMNS[i], laneCards($("lane-" + COLUMNS[i])).length);
  }
  function wireLanes() {
    COLUMNS.forEach((col) => {
      const lane = $("lane-" + col);
      const indexAt = (y) => {
        const cards = laneCards(lane).filter((x) => !x.classList.contains(C("kcard-dragging")));
        const i = cards.findIndex((x) => { const r = x.getBoundingClientRect(); return y < r.top + r.height / 2; });
        return i < 0 ? cards.length : i;
      };
      lane.addEventListener("dragover", (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; lane.classList.add(C("lane-over")); });
      lane.addEventListener("dragleave", (e) => { if (!lane.contains(e.relatedTarget)) lane.classList.remove(C("lane-over")); });
      lane.addEventListener("drop", (e) => {
        e.preventDefault(); lane.classList.remove(C("lane-over"));
        const id = e.dataTransfer.getData("text/plain");
        if (id && $("card-" + id)) placeCard(id, col, indexAt(e.clientY));
      });
    });
  }

  const PRIORITIES = ["Review water damage claims", "Call back new customers", "Approve pending renewals", "Audit large payments", "Update storm guidance"];
  function renderPriorityRanks() {
    const items = Q(".sort-item", $("priority-list"));
    items.forEach((li, i) => { Q(".sort-rank", li)[0].textContent = String(i + 1); });
    $("priority-order").textContent = "Order: " + items.map((li, i) => `${i + 1}. ${Q(".sort-label", li)[0].textContent}`).join(" · ");
  }
  function wireSortable() {
    const list = $("priority-list");
    list.replaceChildren(...PRIORITIES.map((p, i) => h("li", { class: "sort-item", id: "priority-" + (i + 1), testid: "priority-" + (i + 1) },
      h("span", { class: "sort-rank" }, String(i + 1)), h("span", { class: "sort-label" }, p),
      h("button", { class: "handle btn-link", type: "button", "aria-label": "Reorder " + p, "aria-roledescription": "drag handle" }, "⠿"))));
    const done = () => { renderPriorityRanks(); track("reorder", "Priorities reordered", { order: Q(".sort-label", list).map((x) => x.textContent) }); };
    Q(".handle", list).forEach((handle) => {
      const li = handle.closest("li");
      // move/up are heard on the document: re-inserting the row in the DOM would drop a pointer capture on the handle
      handle.addEventListener("pointerdown", (e) => {
        e.preventDefault(); li.classList.add(C("sort-item-lifted"));
        const move = (ev) => {
          const siblings = Q(".sort-item", list).filter((x) => x !== li);
          const next = siblings.find((x) => { const r = x.getBoundingClientRect(); return ev.clientY < r.top + r.height / 2; });
          if (next) { if (li.nextElementSibling !== next) list.insertBefore(li, next); } else if (list.lastElementChild !== li) list.append(li);
        };
        const up = () => {
          document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up); document.removeEventListener("pointercancel", up);
          li.classList.remove(C("sort-item-lifted")); done(); handle.focus();
        };
        document.addEventListener("pointermove", move); document.addEventListener("pointerup", up); document.addEventListener("pointercancel", up);
      });
      handle.addEventListener("keydown", (e) => {
        if (e.key === "ArrowUp" && li.previousElementSibling) { e.preventDefault(); list.insertBefore(li, li.previousElementSibling); handle.focus(); done(); }
        if (e.key === "ArrowDown" && li.nextElementSibling) { e.preventDefault(); list.insertBefore(li.nextElementSibling, li); handle.focus(); done(); }
      });
    });
    renderPriorityRanks();
  }
  function wireSlider() {
    const MIN = 50000, MAX = 1000000, STEP = 10000;
    const track_ = $("limit-slider"), thumb = $("limit-thumb");
    let value = 300000;
    const set = (v, report) => {
      value = Math.min(MAX, Math.max(MIN, Math.round(v / STEP) * STEP));
      const pct = ((value - MIN) / (MAX - MIN)) * 100;
      thumb.style.left = pct + "%"; $("limit-fill").style.width = pct + "%";
      thumb.setAttribute("aria-valuenow", String(value)); thumb.setAttribute("aria-valuetext", whole(value));
      $("limit-value").textContent = whole(value);
      if (report) track("slider", `Coverage limit set to ${whole(value)}`, { value });
    };
    const fromX = (x) => { const r = track_.getBoundingClientRect(); return MIN + ((x - r.left) / r.width) * (MAX - MIN); };
    track_.addEventListener("pointerdown", (e) => {
      e.preventDefault(); track_.setPointerCapture(e.pointerId); thumb.focus(); set(fromX(e.clientX));
      const move = (ev) => set(fromX(ev.clientX));
      const up = () => { track_.removeEventListener("pointermove", move); track_.removeEventListener("pointerup", up); set(value, true); };
      track_.addEventListener("pointermove", move); track_.addEventListener("pointerup", up);
    });
    thumb.addEventListener("keydown", (e) => {
      const d = { ArrowRight: STEP, ArrowUp: STEP, ArrowLeft: -STEP, ArrowDown: -STEP, PageUp: 100000, PageDown: -100000 }[e.key];
      if (d) { e.preventDefault(); set(value + d, true); }
      if (e.key === "Home") { e.preventDefault(); set(MIN, true); }
      if (e.key === "End") { e.preventDefault(); set(MAX, true); }
    });
    set(value);
  }
  function wireSignature() {
    const canvas = $("signature-pad"), ctx = canvas.getContext("2d");
    let strokes = 0, points = 0, drawing = false, fitted = false;
    const fit = () => {
      const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      if (!r.width) return;
      fitted = true;
      canvas.width = r.width * dpr; canvas.height = r.height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.lineWidth = 2.2; ctx.lineCap = "round"; ctx.lineJoin = "round";
      ctx.strokeStyle = getComputedStyle(document.body).getPropertyValue("--ink") || "#1d2433";
    };
    const pos = (e) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    const status = () => { $("signature-status").textContent = strokes ? `Signed: ${strokes} stroke${strokes > 1 ? "s" : ""}, ${points} points` : "Not signed"; };
    canvas.addEventListener("pointerdown", (e) => {
      if (!fitted) fit();
      drawing = true; canvas.setPointerCapture(e.pointerId); strokes++;
      ctx.beginPath(); ctx.moveTo(...pos(e)); points++;
    });
    canvas.addEventListener("pointermove", (e) => { if (!drawing) return; ctx.lineTo(...pos(e)); ctx.stroke(); points++; });
    const end = () => { if (!drawing) return; drawing = false; status(); track("signature", `Signature drawn (${strokes} strokes, ${points} points)`, { strokes, points }); };
    canvas.addEventListener("pointerup", end); canvas.addEventListener("pointercancel", end);
    $("signature-clear").addEventListener("click", () => { fit(); strokes = 0; points = 0; status(); });
    pageShow.board = () => { fit(); loadBoard(); };
  }
  pageInit.board = () => { wireLanes(); wireSortable(); wireSlider(); wireSignature(); };

  // ================================================================== policies
  const COLS = [["policy_number", "Policy"], ["holder", "Policyholder"], ["product", "Product"], ["status", "Status"], ["premium", "Premium"], ["renewal_date", "Renews"]];
  const pol = { q: "", status: "", products: [], sort: "policy_number", dir: "asc", page: 1, size: 10, seq: 0, items: [], selected: new Set() };
  function policyDetails(p) {
    openModal({
      title: "Policy " + p.policy_number,
      body: h("dl", { class: "kv", testid: "policy-details" }, ...[["Policyholder", p.holder], ["Product", PRODUCT_LABEL[p.product]], ["Status", cap(p.status)],
        ["Annual premium", money(p.premium)], ["Started", p.start_date], ["Renews", p.renewal_date], ["ZIP", p.zip]].flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
      actions: [{ label: "Close", primary: true, testid: "policy-details-close" }],
    });
  }
  function startRenewal(p) {
    openModal({
      title: "Start renewal?", body: `Start the renewal of ${p.policy_number} for ${p.holder}? The new term begins on ${p.renewal_date}.`,
      actions: [{ label: "Cancel", testid: "renewal-cancel" },
        { label: "Start renewal", primary: true, testid: "renewal-confirm", onClick: () => { toast(`Renewal started for ${p.policy_number}`, "ok"); track("renewal", `Renewal started for ${p.policy_number}`, { policy_number: p.policy_number }); } }],
    });
  }
  function renderPolicySelection() {
    const n = pol.selected.size;
    $("policies-selected").textContent = `${n} selected`;
    $("policies-export").disabled = !n;
    const onPage = pol.items.map((p) => p.policy_number);
    const picked = onPage.filter((x) => pol.selected.has(x)).length;
    const all = $("policies-select-all");
    all.checked = picked > 0 && picked === onPage.length;
    all.indeterminate = picked > 0 && picked < onPage.length;
  }
  function renderPolicies(res) {
    pol.res = res; pol.items = res.items;
    $("policies-tbody").replaceChildren(...(res.items.length ? res.items.map((p) => {
      const sel = pol.selected.has(p.policy_number);
      const box = h("input", { type: "checkbox", "aria-label": "Select " + p.policy_number, checked: sel, testid: "select-" + p.policy_number });
      const tr = h("tr", { id: "policy-row-" + p.policy_number, class: sel ? "table-selected" : null },
        h("td", {}, box),
        h("td", {}, h("button", { class: "btn-link mono", type: "button", onclick: () => policyDetails(p) }, p.policy_number)),
        h("td", {}, p.holder), h("td", {}, PRODUCT_LABEL[p.product]),
        h("td", {}, h("span", { class: "status-pill status-" + p.status }, p.status)),
        h("td", { class: "num" }, money(p.premium)), h("td", {}, p.renewal_date),
        h("td", {}, h("button", { class: "btn btn-small", type: "button", "aria-haspopup": "menu", "aria-label": "Actions for " + p.policy_number, testid: "actions-" + p.policy_number,
          onclick: (e) => popupMenu(e.currentTarget, [
            { label: "View details", testid: "menu-view", onClick: () => policyDetails(p) },
            { label: "Start renewal", testid: "menu-renew", onClick: () => startRenewal(p) },
            { label: "Copy policy number", testid: "menu-copy", onClick: () => { navigator.clipboard && navigator.clipboard.writeText(p.policy_number).catch(() => {}); toast(`Copied ${p.policy_number}`); } },
          ], "Actions for " + p.policy_number) }, "⋯")));
      box.addEventListener("change", () => {
        if (box.checked) pol.selected.add(p.policy_number); else pol.selected.delete(p.policy_number);
        tr.classList.toggle(C("table-selected"), box.checked); renderPolicySelection();
      });
      return tr;
    }) : [h("tr", {}, h("td", { colspan: "8", class: "muted" }, "No policies match your search."))]));
    const from = res.total ? (res.page - 1) * res.size + 1 : 0, to = Math.min(res.total, res.page * res.size);
    $("policies-summary").textContent = `Showing ${from}–${to} of ${res.total} policies`;
    const btn = (label, page, props) => h("button", { class: "btn btn-small page-btn", type: "button", disabled: page < 1 || page > res.pages, onclick: () => { pol.page = page; loadPolicies(); }, ...props }, label);
    $("policies-pager").replaceChildren(btn("‹ Prev", res.page - 1, { "aria-label": "Previous page", testid: "page-prev" }),
      ...Array.from({ length: res.pages }, (_, i) => btn(String(i + 1), i + 1, { "aria-label": "Page " + (i + 1), "aria-current": i + 1 === res.page ? "page" : null, testid: "page-" + (i + 1) })),
      btn("Next ›", res.page + 1, { "aria-label": "Next page", testid: "page-next" }));
    COLS.forEach(([key]) => {
      const th = $("policies-th-" + key);
      th.setAttribute("aria-sort", pol.sort === key ? (pol.dir === "asc" ? "ascending" : "descending") : "none");
      Q(".sort-ind", th)[0].textContent = pol.sort === key ? (pol.dir === "asc" ? "▲" : "▼") : "↕";
    });
    renderPolicySelection();
  }
  async function loadPolicies() {
    const seq = ++pol.seq;
    $("policies-loading").hidden = false; $("policies-wrap").setAttribute("aria-busy", "true");
    const qs = new URLSearchParams({ q: pol.q, status: pol.status, product: pol.products.join(","), sort: pol.sort, dir: pol.dir, page: pol.page, size: pol.size });
    try {
      const res = await api("/api/policies?" + qs);
      if (slowUi()) await sleep(8000);
      if (seq !== pol.seq) return;   // a newer request superseded this one
      pol.page = res.page;
      renderPolicies(res);
    } catch (err) { if (seq === pol.seq) toast(err.message, "err"); }
    finally { if (seq === pol.seq) { $("policies-loading").hidden = true; $("policies-wrap").removeAttribute("aria-busy"); } }
  }
  pageInit.policies = () => {
    const head = $("policies-head");
    COLS.forEach(([key, label]) => head.append(h("th", { scope: "col", id: "policies-th-" + key, class: key === "premium" ? "num" : null, "aria-sort": "none" },
      h("button", { class: "sort-btn", type: "button", testid: "sort-" + key, onclick: () => {
        pol.dir = pol.sort === key && pol.dir === "asc" ? "desc" : "asc"; pol.sort = key; pol.page = 1; loadPolicies();
      } }, label, h("span", { class: "sort-ind", "aria-hidden": "true" }, "↕")))));
    head.append(h("th", { scope: "col" }, "Actions"));
    const form = $("policies-toolbar");
    const apply = () => {
      pol.q = $("policies-search").value.trim(); pol.status = $("policies-status").value;
      pol.products = checkedValues(form, "product"); pol.page = 1; loadPolicies();
    };
    $("policies-search").addEventListener("input", debounce(apply, 400));
    $("policies-status").addEventListener("change", apply);
    byName(form, "product").forEach((b) => b.addEventListener("change", apply));
    form.addEventListener("submit", (e) => { e.preventDefault(); apply(); });
    $("policies-size").addEventListener("change", () => { pol.size = Number($("policies-size").value); pol.page = 1; loadPolicies(); });
    $("policies-select-all").addEventListener("change", (e) => {
      pol.items.forEach((p) => { if (e.target.checked) pol.selected.add(p.policy_number); else pol.selected.delete(p.policy_number); });
      if (pol.res) renderPolicies(pol.res);
    });
    $("policies-export").addEventListener("click", () => {
      const ids = Array.from(pol.selected).sort();
      const a = h("a", { href: "/api/policies.csv?ids=" + encodeURIComponent(ids.join(",")), download: "harbor-mutual-policies-selected.csv", hidden: true });
      document.body.append(a); a.click(); a.remove();
      toast(`Exporting ${ids.length} polic${ids.length === 1 ? "y" : "ies"}`, "ok");
      setTimeout(refreshActivity, 500);
    });
    loadPolicies();
  };

  // ================================================================== interactions
  function say(outId, text, kind) { $(outId).textContent = text; if (kind) track(kind, text); }
  class CoverageCard extends HTMLElement {
    connectedCallback() {
      if (this.shadowRoot) return;
      const root = this.attachShadow({ mode: "open" });
      root.innerHTML = `<style>
        .sc { display: grid; gap: 8px; } .sc-row { display: flex; gap: 8px; }
        .sc-input { padding: 7px 9px; border-radius: 8px; border: 1px solid var(--line); background: var(--bg); color: var(--ink); font: inherit; min-width: 0; }
        .sc-grow { flex: 1; } .sc-btn { padding: 7px 12px; border-radius: 8px; border: 1px solid var(--brand); background: var(--brand); color: var(--brand-ink); font: inherit; cursor: pointer; }
        .sc-total { font-weight: 600; } label { font-size: 13px; font-weight: 500; }
      </style>
      <div class="sc">
        <label for="shadow-coverage">Coverage to add</label>
        <div class="sc-row">
          <select class="sc-input sc-grow" id="shadow-coverage" name="coverage_option" data-testid="shadow-coverage">
            <option value="roadside">Roadside assistance</option><option value="rental">Rental car</option><option value="glass">Glass cover</option>
          </select>
          <input class="sc-input" id="shadow-qty" name="quantity" type="number" min="1" max="5" value="1" aria-label="Quantity" data-testid="shadow-qty" style="width:70px">
        </div>
        <button class="sc-btn" id="shadow-add" type="button" data-testid="shadow-add" data-t>Add coverage</button>
        <div class="sc-total" id="shadow-total" role="status" data-testid="shadow-total">0 coverages added</div>
      </div>`;
      R.apply(root);
      let total = 0;
      R.reg["shadow-add"].addEventListener("click", () => {
        const sel = R.reg["shadow-coverage"], qty = Math.max(1, Math.min(5, Number(R.reg["shadow-qty"].value) || 1));
        total += qty;
        R.reg["shadow-total"].textContent = `${total} coverage${total === 1 ? "" : "s"} added`;
        this.dispatchEvent(new CustomEvent("coverage-added", { bubbles: true, composed: true, detail: { coverage: sel.selectedOptions[0].textContent, quantity: qty, total } }));
      });
    }
  }
  pageInit.interactions = () => {
    let clicks = 0;
    $("ix-click").addEventListener("click", () => { clicks++; $("ix-click-count").textContent = String(clicks); say("ix-mouse-output", `Clicked ${clicks} time${clicks > 1 ? "s" : ""}`, "click"); });
    $("ix-dblclick").addEventListener("dblclick", () => { $("ix-dblclick").classList.add(C("target-hit")); say("ix-mouse-output", "Double-clicked the box at " + clock(), "double_click"); });
    $("ix-context").addEventListener("contextmenu", (e) => {
      e.preventDefault(); closeMenu();
      const menu = $("context-menu");
      menu.hidden = false;
      menu.style.left = Math.min(e.clientX, window.innerWidth - 180) + "px"; menu.style.top = Math.min(e.clientY, window.innerHeight - 140) + "px";
      menu.querySelector("button").focus();
      $("ix-context").classList.add(C("target-hit"));
    });
    menuKeys($("context-menu"));
    [["context-copy", "Copy claim number"], ["context-flag", "Flag for review"], ["context-archive", "Archive"]].forEach(([id, label]) =>
      $(id).addEventListener("click", () => { $("context-menu").hidden = true; say("ix-mouse-output", "Context menu: " + label, "context_menu"); }));
    const tip = $("ix-tooltip"), hover = $("ix-hover");
    const showTip = () => { if (tip.hidden) { tip.hidden = false; say("ix-mouse-output", "Tooltip shown"); } };
    hover.addEventListener("mouseenter", showTip); hover.addEventListener("focus", showTip);
    hover.addEventListener("mouseleave", () => { tip.hidden = true; }); hover.addEventListener("blur", () => { tip.hidden = true; });
    Q(".menu-item", $("ix-hover-menu-list")).forEach((b) => b.addEventListener("click", () => { say("ix-mouse-output", "Service chosen: " + b.textContent, "menu"); b.blur(); }));

    const keys = $("ix-keys");
    keys.addEventListener("keydown", (e) => {
      const combo = [e.ctrlKey && "Ctrl", e.metaKey && "Meta", e.altKey && "Alt", e.shiftKey && "Shift"].filter(Boolean)
        .concat(["Control", "Meta", "Alt", "Shift"].includes(e.key) ? [] : [e.key === " " ? "Space" : e.key]).join("+");
      if (!combo) return;
      keys.append(h("div", {}, `${clock()}  ${combo}`));
      while (keys.children.length > 8) keys.firstElementChild.remove();
      keys.scrollTop = keys.scrollHeight;
      const shortcut = { "Ctrl+s": "Draft saved (Ctrl+S)", "Meta+s": "Draft saved (Cmd+S)", "Shift+Enter": "New line added (Shift+Enter)", "Escape": "Cancelled (Escape)", "Ctrl+k": "Search opened (Ctrl+K)" }[combo];
      if (shortcut) { e.preventDefault(); say("ix-shortcut", shortcut, "shortcut"); }
    });
    $("ix-type").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); say("ix-typed", "You typed: " + $("ix-type").value, "typed"); } });

    $("ix-alert").addEventListener("click", () => { window.alert("Your next payment of $86.40 is due on 15 November."); say("ix-dialog-output", "Alert acknowledged", "dialog"); });
    $("ix-confirm").addEventListener("click", () => { const ok = window.confirm("Cancel policy POL-1004? This can't be undone."); say("ix-dialog-output", ok ? "Confirm: you chose OK" : "Confirm: you chose Cancel", "dialog"); });
    $("ix-prompt").addEventListener("click", () => { const v = window.prompt("Enter your policy number", "POL-"); say("ix-dialog-output", v === null ? "Prompt dismissed" : "Prompt answered: " + v, "dialog"); });
    $("ix-modal-open").addEventListener("click", () => {
      const input = h("input", { class: "input", id: "modal-name", type: "text", autocomplete: "off", testid: "modal-name" });
      const err = h("p", { class: "field-error", id: "modal-name-error" });
      input.setAttribute("aria-describedby", R.id("modal-name-error"));
      openModal({
        title: "Update your preferred name",
        body: [h("p", { class: "muted" }, "We'll use this name in letters and calls."), h("div", { class: "field" }, h("label", { for: "modal-name" }, "Preferred name"), input, err)],
        actions: [{ label: "Cancel", testid: "modal-cancel", onClick: () => say("ix-dialog-output", "Dialog cancelled") },
          { label: "Save", primary: true, testid: "modal-save", onClick: () => {
            if (!input.value.trim()) { err.textContent = "Enter a name."; input.setAttribute("aria-invalid", "true"); input.focus(); return false; }
            say("ix-dialog-output", "Preferred name saved: " + input.value.trim(), "dialog");
          } }],
      });
    });

    $("ix-new-tab").addEventListener("click", () => say("ix-window-output", "Opened the receipt in a new tab", "new_tab"));
    $("ix-popup").addEventListener("click", () => {
      const w = window.open(`/static/receipt.html?ref=RCPT-POPUP-01&popup=1&release=${R.name}`, "hm-receipt", "width=520,height=640");
      say("ix-window-output", w ? "Popup opened: waiting for confirmation…" : "The popup was blocked by the browser", "popup");
    });
    window.addEventListener("message", (e) => {
      if (e.origin !== location.origin || !e.data || typeof e.data !== "object") return;
      if (e.data.type === "receipt-confirmed") say("ix-window-output", `Popup confirmed receipt ${e.data.ref}`, "popup");
      if (e.data.type === "payment") say("ix-payment-output", e.data.status === "authorized" ? `Payment authorized: ${e.data.auth} (card ending ${e.data.last4}, ${money(e.data.amount)})` : "Payment declined: " + e.data.reason, "payment");
    });
    $("ix-payment-frame").src = `/static/frames/payment.html?release=${R.name}`;
    $("ix-shadow-card").addEventListener("coverage-added", (e) => say("ix-shadow-output", `Added coverage: ${e.detail.coverage} ×${e.detail.quantity} (total ${e.detail.total})`, "shadow_dom"));

    const tabs = ["summary", "terms", "faq"];
    const select = (name) => tabs.forEach((t) => {
      const tab = $("ix-tab-" + t), on = t === name;
      tab.setAttribute("aria-selected", on ? "true" : "false"); tab.tabIndex = on ? 0 : -1;
      $("ix-panel-" + t).hidden = !on;
      if (on) tab.focus();
    });
    tabs.forEach((t, i) => {
      $("ix-tab-" + t).addEventListener("click", () => select(t));
      $("ix-tab-" + t).addEventListener("keydown", (e) => {
        if (e.key === "ArrowRight") select(tabs[(i + 1) % tabs.length]);
        if (e.key === "ArrowLeft") select(tabs[(i - 1 + tabs.length) % tabs.length]);
      });
    });
    $("ix-color").addEventListener("change", (e) => track("color", "Card colour set to " + e.target.value));
    $("ix-hidden-button").addEventListener("click", () => say("ix-scroll-output", "Found the hidden button", "scroll"));
  };

  // ================================================================== waits & dynamic
  const TASKS = ["Call Avery Collins about CLM-5002", "Check the estimate for CLM-5004", "Send letters for CLM-5001", "Review the storm map"];
  let renders = 0;
  function renderTasks() {
    renders++;
    $("dyn-render-count").textContent = "Render " + renders;
    $("dyn-rerender-list").replaceChildren(...TASKS.map((t, i) => h("li", { class: "file-item", id: "task-" + (i + 1), testid: "task-" + (i + 1) },
      h("span", { class: "file-name" }, t), h("span", { class: "small muted" }, "#" + renders))));
  }
  const feed = { next: 0, loading: false };
  async function loadFeed() {
    if (feed.loading || feed.next === null) return;
    feed.loading = true;
    const box = $("dyn-feed");
    const spinner = h("div", { class: "feed-item muted", id: "dyn-feed-loading" }, h("span", { class: "spinner" }), " Loading more…");
    box.append(spinner);
    try {
      const res = await api(`/api/feed?offset=${feed.next}&limit=10`);
      spinner.remove();
      box.append(...res.items.map((n) => h("div", { class: "feed-item", id: "notice-" + n.id, testid: "notice-" + n.id }, h("strong", {}, n.topic + ": "), n.text)));
      feed.next = res.next;
      const shown = Q(".feed-item", box).length;
      $("dyn-feed-status").textContent = feed.next === null ? `All ${shown} notices loaded` : `Showing ${shown} of 60 notices`;
    } catch (err) { spinner.remove(); toast(err.message, "err"); }
    feed.loading = false;
  }
  pageInit.dynamic = () => {
    $("dyn-load-agent").addEventListener("click", async () => {
      const btn = $("dyn-load-agent"), area = $("dyn-agent-area");
      btn.disabled = true;
      area.replaceChildren(h("span", { class: "spinner" }), " Looking up your agent…");
      await sleep(2500);
      area.replaceChildren(h("div", { class: "notice", id: "dyn-agent", testid: "dyn-agent", role: "status" }, h("strong", {}, "Your agent: Morgan Ellis"), h("br"), "(555) 010-4477 · morgan.ellis@harbormutual.example"));
      btn.disabled = false;
      track("late_element", "Agent loaded");
    });
    $("dyn-countdown-start").addEventListener("click", () => {
      const accept = $("dyn-accept");
      let left = 5;
      accept.disabled = true; $("dyn-countdown-start").disabled = true;
      $("dyn-countdown").textContent = `Please read the terms: ${left} s`;
      const t = setInterval(() => {
        left--;
        if (left > 0) { $("dyn-countdown").textContent = `Please read the terms: ${left} s`; return; }
        clearInterval(t); accept.disabled = false; $("dyn-countdown-start").disabled = false;
        $("dyn-countdown").textContent = "You can accept now.";
      }, 1000);
    });
    $("dyn-accept").addEventListener("click", () => { say("dyn-countdown", "Terms accepted", "enabled_after"); $("dyn-accept").disabled = true; });
    $("dyn-show-notice").addEventListener("click", () => {
      const area = $("dyn-notice-area");
      const n = h("div", { class: "notice", id: "dyn-notice", role: "alert", testid: "dyn-notice" }, "Storm warning: coastal ZIP codes may see delays in claim visits.");
      area.replaceChildren(n);
      setTimeout(() => { if (n.isConnected) n.remove(); }, 4000);
    });
    $("dyn-status-start").addEventListener("click", async () => {
      const s = $("dyn-status"), b = $("dyn-status-start");
      b.disabled = true;
      s.textContent = "Queued"; await sleep(1000);
      s.textContent = "Processing"; await sleep(1200);
      s.textContent = "Approved"; b.disabled = false;
      track("status_change", "Claim processing finished: Approved");
    });
    $("dyn-progress-start").addEventListener("click", () => {
      const btn = $("dyn-progress-start"), bar = $("dyn-progress"), fill = $("dyn-progress-bar"), label = $("dyn-progress-label");
      let p = 0;
      btn.disabled = true;
      const t = setInterval(() => {
        p += 10;
        fill.style.width = p + "%"; bar.setAttribute("aria-valuenow", String(p)); label.textContent = p + "%";
        if (p >= 100) { clearInterval(t); label.textContent = "Complete"; btn.disabled = false; track("progress", "Download complete"); }
      }, 300);
    });
    $("dyn-toast-ok").addEventListener("click", () => toast("Payment received. Thank you!", "ok"));
    $("dyn-toast-err").addEventListener("click", () => toast("We couldn't reach the billing system. Try again.", "err"));
    $("dyn-rate").addEventListener("click", async () => {
      const btn = $("dyn-rate"), out = $("dyn-rate-output");
      btn.disabled = true; out.replaceChildren(h("span", { class: "spinner" }), " Fetching…");
      await sleep(1000 + Math.floor(Math.random() * 3000));
      out.textContent = "Today's rate: 4.25% APR"; btn.disabled = false;
    });
    $("dyn-rerender").addEventListener("click", renderTasks);
    $("dyn-slow").addEventListener("click", async () => {
      const btn = $("dyn-slow"), out = $("dyn-slow-output");
      btn.disabled = true; out.replaceChildren(h("span", { class: "spinner" }), " Waiting for the server…");
      try { const r = await api("/api/slow?ms=3000"); out.textContent = `Server answered after ${r.waited_ms} ms`; } catch (err) { out.textContent = err.message; }
      btn.disabled = false;
    });
    $("dyn-feed").addEventListener("scroll", () => {
      const b = $("dyn-feed");
      if (b.scrollTop + b.clientHeight >= b.scrollHeight - 40) loadFeed();
    });
    renderTasks();
    loadFeed();
  };

  // ================================================================== activity
  const activity = { rows: [], last: 0, timer: null, busy: false };
  async function refreshActivity() {
    if (activity.busy) return;
    activity.busy = true;
    try {
      let rows;
      do {   // the API returns up to 200 rows a call: read until caught up
        rows = await api("/api/activity?after=" + activity.last);
        activity.rows.push(...rows);
        if (rows.length) activity.last = rows[rows.length - 1].seq;
      } while (rows.length >= 200);
      $("activity-count").textContent = String(activity.rows.length);
      if (current === "activity") renderActivity();
    } catch (err) { /* the badge just stays as it is */ }
    activity.busy = false;
  }
  function renderActivity() {
    const filter = $("activity-filter").value;
    const rows = activity.rows.filter((r) => !filter || r.source === filter).slice().reverse();
    $("activity-empty").hidden = rows.length > 0;
    $("activity-tbody").replaceChildren(...rows.map((r) => h("tr", { id: "activity-" + r.seq },
      h("td", { class: "num mono" }, String(r.seq)), h("td", { class: "mono" }, r.at.slice(11, 19)),
      h("td", {}, h("span", { class: "status-pill source-" + r.source }, r.source)), h("td", { class: "mono" }, r.kind), h("td", {}, r.summary))));
  }
  pageInit.activity = () => {
    $("activity-filter").addEventListener("change", renderActivity);
    $("activity-refresh").addEventListener("click", refreshActivity);
    $("activity-live").addEventListener("change", () => { pageHide.activity(); pageShow.activity(); });
  };
  pageShow.activity = () => {
    renderActivity(); refreshActivity();
    if ($("activity-live").checked && !activity.timer) activity.timer = setInterval(refreshActivity, 2000);
  };
  pageHide.activity = () => { clearInterval(activity.timer); activity.timer = null; };

  // ================================================================== boot
  async function stylesReady() {
    const link = document.querySelector('link[rel="stylesheet"]');
    if (link && !link.sheet) await new Promise((r) => { link.addEventListener("load", r, { once: true }); link.addEventListener("error", r, { once: true }); });
  }
  /* Live drift: every `seconds`, rename every id, name and class again (the payment frame too) and drop data-testid. */
  function startDrift(seconds) {
    const html = document.documentElement;
    html.setAttribute("data-drift-seconds", String(seconds));
    html.setAttribute("data-drift-generation", "0");
    setInterval(() => {
      const generation = R.driftNow();
      try {
        const frame = $("ix-payment-frame").contentWindow;
        if (frame && frame.HM) frame.HM.R.driftNow();
      } catch (e) { /* frame not loaded yet */ }
      html.setAttribute("data-drift-generation", String(generation));
      window.dispatchEvent(new CustomEvent("hm:drift", { detail: { generation } }));
      track("drift", `Locators renamed while the page was open (generation ${generation})`, { generation, seconds });
    }, seconds * 1000);
  }

  async function boot() {
    try { CFG = { ...CFG, ...(await api("/api/config")) }; } catch (e) { /* defaults */ }
    const params = new URLSearchParams(location.search);
    R.init(params.get("release") || CFG.release, CFG.rules);
    await stylesReady();
    R.apply(document);
    document.documentElement.setAttribute("data-release", R.name);
    customElements.define("hm-coverage-card", CoverageCard);
    wireModal();

    const sel = $("release-select");
    sel.value = R.name;
    sel.addEventListener("change", () => { const u = new URL(location.href); u.searchParams.set("release", sel.value); location.href = u.toString(); });
    // ?drift=N (seconds; 0 = off) wins over the server default; values are kept within 2-300
    const askedDrift = params.has("drift") ? Number(params.get("drift")) || 0 : Number(CFG.drift_seconds) || 0;
    const drift = askedDrift > 0 ? Math.min(300, Math.max(2, Math.round(askedDrift))) : 0;
    if (R.name !== "v1" || drift) {
      const banner = $("release-banner");
      banner.hidden = false;
      banner.textContent = [R.name !== "v1" ? `UI release ${R.name} — ${CFG.releases[R.name] || ""}` : "",
        drift ? `Live drift: ids, names and classes change every ${drift} s while this page is open` : ""].filter(Boolean).join(" · ");
    }
    if (drift) startDrift(drift);
    $("nav-toggle").addEventListener("click", () => {
      const open = $("sidenav").classList.toggle(C("sidenav-open"));
      $("nav-toggle").setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("click", (e) => {
      if (openMenuEl && !openMenuEl.contains(e.target) && !e.target.closest('[aria-haspopup="menu"]')) closeMenu();
      if (!$("context-menu").hidden && !$("context-menu").contains(e.target)) $("context-menu").hidden = true;
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
    // a file dropped outside a drop zone must not navigate away from the page
    window.addEventListener("dragover", (e) => { if (e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files")) e.preventDefault(); });
    window.addEventListener("drop", (e) => { if (e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files")) e.preventDefault(); });
    window.addEventListener("hashchange", route);
    route();
    refreshActivity();
    document.documentElement.removeAttribute("data-boot");
  }
  boot();
})();
