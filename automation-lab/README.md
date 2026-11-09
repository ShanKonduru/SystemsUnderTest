# Harbor Mutual Automation Lab (system under test)

A web UI for showing off **test-automation script generation, execution and self-healing**. It shares the Harbor
Mutual look of the chat assistant. It has no login and no LLM. Every area exercises a different kind of control a
generated script has to drive, and what a script does is recorded on the server. A run can therefore be checked
against the system of record, not only against the screen.

| Area (`#/…`) | What it exercises |
|---|---|
| `quote` · Get a quote | Every form control: select, radio, segmented radio, checkboxes (some disabled by product), range, date, a `role=switch`, an autocomplete combobox, textarea with counter. Conditional sections, client and server validation, a priced result and a JSON download |
| `claim` · File a claim | A 4-step wizard: datalist, live policy lookup, time input, multi-select, conditional field, file attach and drop, a review page and a typed signature |
| `documents` | A visible `input[type=file] multiple`, a drag-and-drop zone with upload progress, a hidden input behind a styled button with image preview, plus downloads and inline delete confirmation. Disallowed types, empty files and oversized files are rejected with a reason |
| `board` · Drag & drop | A claims board with HTML5 drag and drop (moves are saved on the server), a sortable list driven by pointer events (with ↑/↓ as the keyboard alternative), a custom `role=slider` and a canvas signature pad |
| `policies` | Server-side search (debounced), filters, sortable headers (`aria-sort`), paging, row selection with select-all, row action menus, a details dialog and CSV export |
| `interactions` | Click, double-click, right-click context menu, hover tooltip and hover menu, key combos, native `alert`/`confirm`/`prompt`, a custom modal, a new tab, a popup window that reports back, a payment form in an **iframe**, a web component in **shadow DOM**, tabs, an accordion, `contenteditable`, a colour input and a button hidden in a scrolling box |
| `dynamic` · Waits | Late elements, a button enabled after a countdown, a disappearing notice, changing status, progress, toasts, a random delay, re-rendered (stale) nodes, a slow API call and infinite scroll |
| `activity` | The server's record: `server` rows are real changes, `page` rows are interactions the page reported |

## Run it on Docker Desktop

```bat
:: from the repo root; the lab needs no LLM settings
docker compose -f automation-lab\docker-compose.yml up -d --build
:: open http://127.0.0.1:8400
docker compose -f automation-lab\docker-compose.yml down
```

The port is bound to `127.0.0.1` only. Data (`lab.db`, `uploads/`) is in `automation-lab/data/`, which git ignores.

| Setting (root `.env`) | Default | Purpose |
|---|---|---|
| `LAB_PORT` | `8400` | Port on 127.0.0.1 |
| `LAB_RELEASE` | `v1` | UI release served when the URL doesn't ask for one (v1–v4) |
| `LAB_TODAY` | `2026-11-03` | Fixed "today", so dates and validation are reproducible |
| `LAB_LATENCY_MS` | `300` | Network wait added to the policies table and the notices feed |
| `LAB_MAX_UPLOAD_MB` | `5` | Upload size limit |
| `LAB_DRIFT_SECONDS` | `0` | Rename ids, names and classes every N seconds while a page is open (0 = off, else 2–300) |
| `LAB_SANDBOX_TOKEN` | (empty) | Token for `/sandbox/status`, `/sandbox/reset`, `/sandbox/release` and `/sandbox/drift`. When empty, all of them refuse every request |
| `LAB_DEFECTS` | (empty) | **Test runs only.** A "buggy build": comma-separated planted defects (below) |

## UI releases, live drift and the locator map: self-healing

The lab doesn't heal anything itself. It is the website whose locators break, so your framework's healing can be
shown and scored. The page is written once and rewritten at load time for the selected release
(`static/release.js`, using the rules in `static/releases.json`). Behaviour and data are identical in every release;
only the locators change.

| Locator | v1 Baseline | v2 Redesign | v3 Dynamic | v4 Structural |
|---|---|---|---|---|
| `id` | `quote-first-name` | `hmQuoteFirstName` | random on every load | random on every load |
| `name` | `first_name` | `firstName` | random on every load | random on every load |
| `class` | `btn-primary` | `ui-btn-primary` | random on every load | random on every load |
| `data-testid` | kept | kept | removed | removed |
| Button text | Get my quote | Calculate premium | Calculate premium | See my price |
| Placeholder | you@example.com | name@email.com | name@email.com | you@domain.com |
| Layout | as written | 4 pairs of fields swapped, every field wrapped in an extra `<div>` | as v2 | as v2, plus Location ↔ Approximate time |
| Label, ARIA name | First name | stable | stable | reworded: Given name, Policy no., Limit of cover… |
| Element type | `<button>` | stable | stable | 5 buttons become links, 3 become `<div role="button">` |

| Kind of healing | Use | Why |
|---|---|---|
| Design-time (repair the saved script once) | **v2** | Renames are fixed, so a repaired locator keeps working on every load |
| Run-time (find the element live, script unchanged) | **v3** | Ids, names and classes change on every load; only live matching gets through |
| Run-time, hardest | **v4** | Labels, ARIA names, button texts and element types change too |
| Run-time, mid-session | **`?drift=N`** | Renames every id, name and class again every N seconds while the page is open |

- **Choosing a release:** the header's **UI release** select, `?release=v1|v2|v3|v4` for one page load, or
  `POST /sandbox/release {"release": "v4"}` (or `LAB_RELEASE`) as the default. The payment iframe and the popup
  follow the page's release; the new-tab receipt link always opens v1.
- **Live drift:** `?drift=N` (2–300 s; `0` = off) wins for that load; `POST /sandbox/drift {"seconds": 10}` (or
  `LAB_DRIFT_SECONDS`) sets the default. Each drift renames the page, its shadow DOM and the payment iframe, drops
  every `data-testid`, sets `<html data-drift-generation="N">`, fires a `hm:drift` window event and logs a `drift`
  activity. Behaviour and styling are unaffected.
- **Swapped fields trap position-based healing.** First name ↔ Last name and Email ↔ Phone swap places in v2–v4.
  A wrong heal still "passes" on screen; `GET /api/quotes` shows the values in the wrong fields.
- **Scoring heals:** `GET /api/locator-map?release=v2` lists every authored element (key = its v1 id) with its id,
  CSS selector, name, `data-testid`, tag, role, accessible name, placeholder and text in that release. v1 and v2 are
  exact; v3 and v4 ids are random per load (`"dynamic": true`), and `window.HM.R.locatorMap()` in the page returns
  this load's values (also after drift). The map is ground truth for scoring; a framework under test must not use it
  to heal.

[`e2e/smoke.py`](e2e/smoke.py) drives every area in v1, v2 and v3 with one script using only release-proof locators
(labels, roles and names scoped to the visible page region), then checks the locator map against the rendered page in
all four releases, completes v4 flows using names taken from the map, and runs a quote while drift renames the page.
Pages that aren't shown stay in the DOM (hidden), so an unscoped `get_by_label("Email")` also matches the claim
wizard's "Email" radio button.

```bat
automation-lab\.venv\Scripts\python -m pip install playwright
set LAB_BROWSER_CHANNEL=msedge
automation-lab\.venv\Scripts\python automation-lab\e2e\smoke.py
```

## Checking what a script did

- The API answers what the page shows: `GET /api/quotes`, `/api/claims`, `/api/uploads`, `/api/board`,
  `/api/activity` (up to 200 rows a call; page with `?after=<last seq>`). The header comment of `automation_lab/app.py` has the full list.
- Reference numbers are deterministic from a clean start: quotes `Q-2026-0001`…, claims `CLM-9001`…, uploads `UPL-0001`….
- Seed data includes 57 policies (`POL-1001` Shan Mercer is active, `POL-1002` has lapsed, `POL-1004` is cancelled)
  and 10 claims on the board.
- Test card numbers in the payment iframe: `4242 4242 4242 4242` is authorized and `4000 0000 0000 0002` is declined.
  Nothing is charged.
- `POST /sandbox/reset` (with `X-Sandbox-Token`) restores the seed and deletes the uploads.

## Planted defects (buggy build)

`LAB_DEFECTS` switches on defects that copy real UI and backend bugs, so a framework can show its generated scripts
catch them ([`automation_lab/defects.py`](automation_lab/defects.py)). An unknown name is an error.

| Defect | Where | What it does |
|---|---|---|
| `discount_ignored` | server | quotes leave out the 10% multi-policy discount, while the page still says it was applied |
| `phantom_claim` | server | a filed claim gets a claim number but is never saved |
| `upload_drops_last` | server | a multi-file upload reports every file but stores all but the last |
| `board_not_saved` | server | moving a card on the board reports success but is not saved (a reload shows it back) |
| `slow_ui` | page | the quote result and the policies table render after 8 s instead of under 1 s |

## Develop and test

```bat
cd automation-lab
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pytest pytest-cov httpx ruff
.venv\Scripts\python -m pytest -q --cov
.venv\Scripts\uvicorn automation_lab.app:app --reload --port 8400
```

There are 47 offline tests at 99% coverage, with a 95% gate. The page is plain HTML, CSS and JavaScript, with no
build step. In `static/app.js`, reach elements through `HM.$("authored-id")` and create them with `HM.h()`. Use
`HM.C()` for class names. Style only by class, element or ARIA attribute, never by id or `data-testid`. Otherwise
v2 and v3 break.
