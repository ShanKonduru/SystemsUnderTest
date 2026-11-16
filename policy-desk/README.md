# Harbor Mutual Policy Desk (system under test)

A Windows desktop app ("thick client") for showing **desktop test automation, test-script generation and
self-healing**. Branch agents of the fictitious insurer Harbor Mutual use it to quote, underwrite, bind and decline
policies. It is WinForms on .NET Framework 4.x, has no LLM and no server, and builds with the C# compiler that ships
with Windows, so no SDK or Visual Studio is needed.

It is the desktop counterpart of the [Automation Lab](../automation-lab) (web) and grew out of the "Order Desk"
sample thick client. Like the other apps here, everything it does is recorded in a system of record
(`data\policydesk.json`), so a test run can be checked against the record and not only against the screen.

| Screen | What it exercises |
|---|---|
| Sign-in (modal, over the main window) | Edits, a password field, a drop-down list, an error label, Enter/Esc buttons, lockout after 3 wrong passwords |
| Main window | Menu bar with shortcuts (Ctrl+N/S/E/F/O, F1), an icon-only toolbar, a status bar, three tabs |
| New quote tab | Text boxes, a masked phone box, drop-down lists, a date picker, check boxes (Roadside only for Auto), radio buttons, validation messages with error icons, **an asynchronous underwriting check** with a progress bar (Save stays disabled until it finishes), edit mode |
| Quotes tab | Filter text and status filter, a sortable data grid, double-click, a right-click context menu, the **Save File dialog** for CSV export |
| Quote details | A modal with read-only fields, Bind (Pending only) and Decline, which opens **a modal on top of a modal** |
| Options, Product guide, About | Group box with radio buttons, check boxes, a **tree view** to expand, a Win32 message box |
| Activity tab | A list view of everything recorded, newest first |

Deliberately "legacy", the way real thick clients often are:

- **Toolbar:** its four buttons show only an icon. They have no text and no control name, only a tooltip, so only position-based XPath or image recognition can reach them.
- **Notes box:** it has no control name and no label.
- **Premium chart:** it is drawn by hand, so UI Automation sees an empty pane. Only image-based checks can verify it.

## Build and run

```bat
start_policy_desk.bat                           :: build, then launch (release v1, data in policy-desk\data)
start_policy_desk.bat --reset                   :: start from the seed data
start_policy_desk.bat --release v3 --drift 10   :: UI release v3, every control renamed every 10 s
start_policy_desk.bat --no-build --help         :: the app's options
```

`./start_policy_desk.sh` does the same from Git Bash. On Linux and macOS it runs under Mono, for viewing only:
UI Automation, and so desktop automation, needs Windows. To build only, run `app\build.bat` (or `bash app/build.sh`).
That gives you:

- `app\bin\PolicyDesk.exe`: the app.
- `app\bin\policydesk-cli.exe`: the same app as a console program, for `--self-test` and `--locator-map`.
- `app\bin\releases.json`: the UI release rules.

**Sign in:** use any user name. The password is `--password`, else `POLICYDESK_PASSWORD`, else `harbor`.

## Test hooks: launch options

Every option also has an environment variable. The command line wins.

| Option | Variable | Default | Purpose |
|---|---|---|---|
| `--reset` | | off | Start from the seed data: quotes 1001–1005, an empty activity log, default options |
| `--today YYYY-MM-DD` | `POLICYDESK_TODAY` | `2026-11-03` | Fixed "today", so start dates, validation and records are reproducible |
| `--data DIR` | `POLICYDESK_DATA` | `policy-desk\data` | Folder for the system of record and the live locator map |
| `--release v1…v4` | `POLICYDESK_RELEASE` | `v1` | UI release (below) |
| `--release-seed N` | `POLICYDESK_RELEASE_SEED` | random | Makes v3/v4 random names repeatable |
| `--drift N` | `POLICYDESK_DRIFT_SECONDS` | `0` | Rename every control every N seconds from launch (0 = off, else 2–300) |
| `--latency-ms N` | `POLICYDESK_LATENCY_MS` | `800` | How long the underwriting check takes (0–30000) |
| `--password TEXT` | `POLICYDESK_PASSWORD` | `harbor` | Sign-in password |
| `--export-dir DIR` | `POLICYDESK_EXPORT_DIR` | Documents | Folder the Export dialog opens in |
| `--defects a,b` | `POLICYDESK_DEFECTS` | (none) | **Test runs only.** A "buggy build": planted defects (below) |
| `--locator-map R [--out F]` | | | Write release R's locator map and exit (use `policydesk-cli.exe`) |
| `--self-test` | | | Run the built-in checks and exit, 0 = all passed (use `policydesk-cli.exe`) |

Unknown options and bad values are refused with a message and exit code 2.

## Business rules

| Rule | Value |
|---|---|
| Base premium | Auto 480.00, Home 920.00, Renters 180.00, Umbrella 310.00 a year |
| Coverage | Basic ×1.00, Standard ×1.25, Premium ×1.60 |
| Deductible | 250 ×1.10, 500 ×1.00, 1000 ×0.90 |
| Business customer | +15% |
| Multi-policy discount | −10% |
| Roadside assistance | +45.00, Auto only (the check box is disabled for other products) |
| Rounding | To cents after every step, half away from zero |
| Monthly plan | 12 payments of premium × 1.03 / 12, rounded to cents |
| Underwriting | **Referred** when the premium is over 1,500.00, or for Umbrella without a Bound Auto or Home policy for the same policyholder (name match ignores case and outer spaces); otherwise **Approved** |
| Saving | Only after an underwriting check for the current inputs. An Approved quote saves as Pending, a Referred one as Referred. Quotes are numbered from 1006 |
| Validation | Policyholder required, at most 60 characters. An email or a phone is required. A valid email if given. A full 10-digit phone if given. Start date from today to today + 60 |
| Status changes | Pending → Bound (policy number `HMP-<quote>`). Pending or Referred → Declined (with a reason; "Other" needs a note). Only Pending and Referred quotes can be edited |

Examples: Auto, Standard, deductible 500 costs 600.00, or 12 × 51.50 monthly. Home, Premium, deductible 250 costs
1,619.20 and is Referred. The self-test lists more.

## The system of record

`data\policydesk.json` is written atomically after every change:

- `quotes` with every field and price
- `settings`
- `next_quote`
- `activity`: the log, the same as the Activity tab

Activity kinds:

| Group | Kinds |
|---|---|
| Lifecycle | `reset`, `launch`, `drift` |
| Sign-in | `sign_in`, `sign_in_failed`, `sign_out` |
| Quotes | `underwriting_checked`, `quote_saved`, `quote_updated`, `quote_bound`, `quote_declined`, `quotes_exported` |
| Options | `settings_saved` |

The folder is git-ignored. Check outcomes there, for example "quote 1006 is Pending at 387.50". Don't stop at the
status bar text.

## Planted defects

`--defects a,b` or `POLICYDESK_DEFECTS=a,b` turns on deliberate bugs, so a test suite can show it catches them. They
are off by default. An unknown name is refused, so a typo never runs a clean build by accident. The app never reveals
which defects are on: not on screen, not in the activity log, not in the locator map. They live in
[`app/Defects.cs`](app/Defects.cs) and the places it lists.

| Defect | What goes wrong | Kind of bug | Caught by |
|---|---|---|---|
| `multi_policy_ignored` | The screen applies the 10% multi-policy discount, but the saved quote is priced without it | Screen and record disagree | `test_the_saved_premium_is_the_premium_on_screen` |
| `monthly_truncated` | Monthly installments are cut to the cent instead of rounded (16.99 instead of 17.00) | Calculation | `test_monthly_installment_is_rounded` |
| `umbrella_unchecked` | Umbrella is approved without a bound Auto or Home policy for the policyholder | Business rule | `test_umbrella_without_a_bound_policy_is_referred` |
| `start_today_rejected` | A start date of today is refused as "in the past" | Boundary (off by one) | `test_a_start_date_of_today_is_accepted` |
| `phantom_bind` | Bind policy reports the new policy number, but the quote is never bound or saved | Write reported, not persisted | `test_binding_a_pending_quote` |
| `no_lockout` | Sign-in never locks after 3 wrong passwords | Security | `test_three_wrong_passwords_lock_sign_in` |
| `grid_not_refreshed` | A saved or updated quote does not show in the Quotes grid until the app restarts | Stale UI | `test_a_saved_quote_is_in_the_grid_and_the_record` |
| `export_drops_last` | Export writes every visible quote but the last, while reporting the full count | Partial output reported as complete | `test_export_writes_every_visible_quote` |
| `slow_underwriting` | The underwriting check takes 12 seconds, whatever `--latency-ms` says | Performance and timing | `test_save_waits_for_an_underwriting_check` (and every test that waits for underwriting) |
| `email_unlabeled` | The Email box loses its label, so UI Automation gives it no Name | Accessibility and locators | `test_every_field_is_named_by_its_label` |

## The baseline test suite

A pytest suite of 28 tests. It drives the app through native UI Automation with
[pywinauto](https://pywinauto.readthedocs.io/) (backend `uia`) and checks the outcome in `policydesk.json`.

- **Screen objects** in [`screens/`](screens): sign-in, the New quote tab, the Quotes tab, the details and decline
  dialogs, message boxes, the status bar and the record.
- **Tests** in [`tests/`](tests): sign-in and lockout, pricing, validation and its boundaries, underwriting,
  saving, the grid and its filter, binding, declining, editing, export through the Save dialog, field names, and
  the activity log.

It is the reference that generated scripts and self-healing are measured against:

- **v1 locators:** it uses the v1 AutomationIds, as a recorded suite would. It passes on v1 and stops at the first
  locator on v2–v4.
- **Defect coverage:** each test that targets a planted defect is marked `@pytest.mark.catches("<defect>")`.

```bat
cd policy-desk
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-test.txt
.venv\Scripts\python -m pytest                      :: builds the app, then 28 tests (about 3.5 minutes)
.venv\Scripts\python -m pytest --release v2         :: the same suite against v2: it breaks, by design
.venv\Scripts\python tools\defect_matrix.py         :: clean run, then each defect against its tests
.venv\Scripts\ruff check screens tests tools
```

`tools\defect_matrix.py` takes the defect list from the app itself, so a new defect without a test fails it. It
exits 0 only when the clean build passes and every defect makes at least one test fail. Add `--full` to run the
whole suite against each defect (about 35 minutes).

The suite moves the mouse and types, so it needs an unlocked desktop and isn't run in CI; CI lints it. Every
launch has its own data and export folders, under `.pytest-tmp`.

Result on 2026-11-16: 28 passed on the clean build, and all 10 defects caught. Each failure names the symptom, for
example `assert 'Pending' == 'Bound'` for `phantom_bind`.

## UI releases, live drift and the locator map: self-healing

Each screen is written once, as v1, and rewritten while it is built for the selected release. The rules live only in
[`app/releases.json`](app/releases.json). Behaviour and data are identical in every release; only what a driver sees
changes.

| Locator | v1 Baseline | v2 Redesign | v3 Dynamic | v4 Structural |
|---|---|---|---|---|
| AutomationId (control name) | `txtPolicyholder` | `hmInsuredName` | random per launch | random per launch |
| Window title | Harbor Mutual Policy Desk 3.0 | Policy Desk 3.1 - Harbor Mutual | as v2 | Harbor Mutual Policy Desk 4.0 |
| Button, menu and tab texts | Save quote, Quotes | Save, Quote list | as v2 | as v2, plus Quote price, Go, Details… |
| Labels, so the Names of fields | Policyholder: | stable | stable | reworded: Insured name:, Agent ID:… |
| Layout | as written | Email ↔ Phone, Coverage ↔ Deductible | as v2 | as v2, plus Annual ↔ Monthly and User name ↔ Password |
| Control type | Button | stable | stable | 6 buttons become link labels (a Text element with a Hyperlink child) |

| Kind of healing | Use | Why |
|---|---|---|
| Design-time (repair the saved script once) | **v2** | Renames are fixed, so a repaired locator keeps working |
| Run-time (find the element live) | **v3** | AutomationIds change on every launch (`--release-seed` makes a run repeatable) |
| Run-time, hardest | **v4** | Labels, texts, positions and control types change too |
| Run-time, mid-session | **`--drift N`** | Every AutomationId changes again every N seconds, the sign-in dialog included |

- **Swapped fields trap position-based healing.** In v2–v4, Email and Phone trade places. A wrong heal can still
  "pass" on screen. `policydesk.json` then shows the values in the wrong fields, or validation fails.
- **Scoring heals:** run `app\bin\policydesk-cli.exe --locator-map v2` (add `--release-seed N` for v3/v4) to get
  every authored element of every window, keyed by its v1 name. Each element has:
  - its AutomationId, UI Automation control type and expected Name
  - whether it is legacy
  - a `swapped_with` value, when it was swapped
  - notes on how to reach it when it has no AutomationId

  The running app also writes this launch's map to `data\locator-map.json` and rewrites it after every drift.
  The map is ground truth for scoring. A framework under test must not use it to heal.

## Tools for a desktop automation engagement

| Tool | What it does |
|---|---|
| `app\bin\policydesk-cli.exe --self-test` | Checks for prices, underwriting, validation, the store, options, CSV, every release rule and the defects that live in the business rules. CI runs it on Windows |
| `pytest` and `tools\defect_matrix.py` | The baseline suite and its proof that it catches every planted defect (above) |
| `tools\uia_report.bat -Exe app\bin\PolicyDesk.exe` | UI Automation readiness report in the two views drivers use. Grades each control and gives a readiness percentage (below) |
| `tools\smoke.bat [v1…v4]` | End-to-end smoke through native UIA3, details below. Needs an interactive desktop, so it isn't run in CI |

The smoke signs in, quotes, waits for underwriting, saves, uses the icon-only toolbar and a message box, then binds
from the grid. It finds every control through the locator map and checks `policydesk.json`. It is also a worked
example for generated scripts.

The readiness report shows both UI Automation clients that desktop drivers use:

| View | Tool | Same as | v1 readiness |
|---|---|---|---|
| Native UIA3 (COM) | `tools\bin\UiaReport.exe` | Inspect.exe, WinAppDriver / Appium "Windows" driver, FlaUI | 70%: 46 interactive, 23 with a stable AutomationId, 18 name only, 5 unlocatable, 1 opaque pane |
| NovaWindows | `tools\uia_report_nova.ps1` | Appium NovaWindows driver (managed `System.Windows.Automation`) | 37%: WinForms ids are window handles, types are all `Pane`, the menu, toolbar and status bar are opaque |

**Use a native-UIA driver for this app.**

## Desktop automation traps worth knowing

- **Modal dialogs block Invoke.** Invoking a button that opens a modal dialog (Open quote, Decline…, Export…, About,
  any message box) through the UI Automation Invoke pattern blocks until the dialog closes. Click with the mouse, or
  post the click asynchronously.
- **Drop-down lists:** they fire their change events only for user input. Choose with the keyboard or the mouse,
  not by setting the selection through UI Automation.
- **No AutomationId:** menu items, status bar texts and tab headers (TabItems) have none. Match them by Name within
  their parent. The date picker is a Pane whose Name is the date it shows.
- **WinForms naming quirks:**
  - An empty label borrows the previous label's text as its Name.
  - The menu bar and toolbar report a stray Name ("Status:").
- **Hidden tabs:** a tab page's controls are only in the tree while its tab is selected.

## Generating tests

[`docs/`](docs) holds the inputs a test-script generator, or a test engineer, starts from: a knowledge base,
requirements with acceptance criteria, and test cases with release-proof locators and record checks.
