# Harbor Mutual Policy Desk — Instructions for Building Test Automation

As of 2026-11-16 · Shan Konduru

These instructions are for an AI engine, or a test engineer, that writes and runs automated tests against
**Harbor Mutual Policy Desk**. Policy Desk is a Windows desktop app (WinForms on .NET Framework 4.x) for the
fictitious insurer Harbor Mutual. It uses no real data and no LLM.

Read this page first. It says what to read next, how to start the app, how scripts must be written, and when a
script is done.

All links are relative to this file, which lives in `policy-desk/docs/`.

---

## 1. Your task

Write automated UI tests for Policy Desk that:

1. Cover the test cases in [policy-desk-test-cases.csv](policy-desk-test-cases.csv). They trace to the acceptance
   criteria in [policy-desk-requirements.csv](policy-desk-requirements.csv).
2. Drive the real app through **native UI Automation (UIA3)**.
3. Check every outcome in the **system of record** (`data\policydesk.json`), not only on screen.
4. Follow the patterns of the existing suite in [`../screens/`](../screens) and [`../tests/`](../tests) unless you are
   told to use another stack.

---

## 2. Read in this order

| # | File | What it gives you | How to use it |
|---|---|---|---|
| 1 | This file | Rules, setup, done criteria | Follow it throughout |
| 2 | [policy-desk-knowledge-base.md](policy-desk-knowledge-base.md) | The full app description: each screen and control, exact texts and messages, pricing with worked examples, validation, seed data, the record format, launch options, releases, and automation traps | The source of truth for behaviour and expected values |
| 3 | [policy-desk-requirements.csv](policy-desk-requirements.csv) | 29 requirements and 138 acceptance criteria in Given/When/Then form (columns: Requirement ID, Epic, Feature, User Story, Priority, Page / Endpoint, AC ID, Acceptance Criterion) | What must be true; every test traces to an AC ID |
| 4 | [policy-desk-test-cases.csv](policy-desk-test-cases.csv) | 144 test cases: preconditions, data, steps, expected result, locators, record check, wait strategy, releases, tags | What to automate, one script per test case or a group per feature |
| 5 | [../README.md](../README.md) | Build and run, business rules, record, releases, and the short list of desktop traps | Setup and a quick reference |
| 6 | [../screens/](../screens) | Screen objects (Python, pywinauto, `uia` backend) that already work against this app | Reuse them, or copy their techniques into your stack |
| 7 | [../tests/](../tests) | 28 working tests built on those screen objects | The style to follow: fixtures, waits, record checks |

The test-case CSV has these columns:

`Test Case ID, Title, Requirement ID, AC IDs Covered, Test Type, Priority, Automation Level, Preconditions,
Test Data, Test Steps, Expected Result, Locators (release-proof), Backend Verification, Wait / Sync Strategy,
UI Releases, Tags`

Locator notation in the CSV:

- `Edit 'Policyholder:'` means ControlType Edit with UIA Name "Policyholder:".
- `[id txtPolicyholder / hmInsuredName]` gives the v1 / v2 AutomationIds.
- `Button /^(Check underwriting|Run underwriting|Check risk)$/` matches the v1, v2/v3 and v4 wordings.

---

## 3. Set up and start the app

**Requirements:**

- Windows 10 or 11 with an unlocked, interactive desktop: tests click and type.
- Python 3.11 or later for the reference suite.
- No SDK is needed to build the app.

```bat
cd policy-desk
app\build.bat                                      :: builds app\bin\PolicyDesk.exe and app\bin\policydesk-cli.exe
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-test.txt
.venv\Scripts\python -m pytest                     :: the reference suite: 28 tests, about 3.5 minutes, all must pass
```

**Start every test from a known state.** Launch a fresh app per test:

```bat
app\bin\PolicyDesk.exe --reset --data <temp folder> --release v1 --latency-ms 300 --export-dir <temp folder>
```

| Option | Why |
|---|---|
| `--reset` | Seed data: quotes 1001–1005 (Bound, Pending, Pending, Referred, Declined); new quotes start at 1006 |
| `--data DIR` | A separate system of record per test, so tests never share state |
| `--release v1` | The UI release under test (v1 is the baseline) |
| `--latency-ms N` | How long the underwriting check takes (default 800 ms; 0 = at once) |
| `--today YYYY-MM-DD` | Fixed "today", default 2026-11-03; every date in the docs assumes it |
| `--export-dir DIR` | Where the Save dialog of Export opens |

**Sign in:** any user name, password `harbor`. Close the app (or kill the process) at the end of every test.

---

## 4. Rules for every script

### 4.1 Locating controls

1. Find the main window by process, then everything else **inside** it. Dialogs (sign-in, quote details, decline,
   options, message boxes) are child windows of the main window.
2. On v1, use the AutomationId. Each control's v1 id is in the test cases (`[id …]`) and in
   [`../screens/`](../screens).
3. Elements with no AutomationId: match them by ControlType + Name inside their parent.
   - Menu items.
   - Tab headers (TabItems).
   - Status bar texts.
   - Message box texts and buttons.
4. **Legacy controls are reached by position:**
   - The four toolbar buttons are icon-only and unnamed: New, Save, Clear, Export, in that order.
   - The Notes box has no name and no label.
   - The premium chart is opaque to UI Automation; verify it only by image, if at all.
5. Scope to the visible tab. Controls on a tab that isn't selected are not in the UI Automation tree.

### 4.2 Acting on controls

| Control | Do | Don't |
|---|---|---|
| Button that opens a modal dialog (Open quote, Edit quote, Decline..., Export..., About, anything that can show a message box) | Click with the mouse | Use the UIA Invoke pattern: it blocks until the dialog closes |
| Other buttons, check boxes, radio buttons | UIA Invoke | — |
| Drop-down list (ComboBox) | Focus it and use the keyboard (Home, then Down until the value matches) | Select through UI Automation: the app sees no change |
| Text box | UIA Value pattern (SetValue) | — |
| Phone (masked box) | Focus, select all, type the 10 digits | — |
| Start date (date picker) | Focus, press Left 3 times, then type the year, Right, the month, Right, the day | Expect a label Name: its Name is the shown date (yyyy-MM-dd) |
| Grid row | Find the cell whose Value is the quote number, then click it | Rely on row index (filters hide rows) |
| Save File dialog | Type the full path in the "File name" Edit (AutomationId 1001), then invoke "Save" | — |

### 4.3 Waiting

Never use fixed sleeps. Wait for a state, with a timeout:

- **Underwriting result:** the `lblUnderwriting` text starts with "Underwriting: Approved" or "Underwriting: Referred".
  With `--latency-ms 300`, treat more than 5 s as a failure.
- **Buttons:** wait for a button to become enabled. Save quote stays disabled until underwriting is checked.
- **Dialogs:** wait for a dialog to exist before using it, and for it to disappear after closing it.
- **Status bar:** wait for the exact status text, for example "Quote 1006 saved (Pending)".

### 4.4 Checking outcomes

Every test that changes data must check `data\policydesk.json` as well as the screen:

- **`quotes`:** number, status, premium, installment, plan_total, policy_number, decline_reason, …
- **`activity`:** check the kinds, in order.
  - Lifecycle: `reset`, `launch`
  - Sign-in: `sign_in_failed`, `sign_in`, `sign_out`
  - Quotes: `underwriting_checked`, `quote_saved`, `quote_updated`, `quote_bound`, `quote_declined`, `quotes_exported`
  - Options: `settings_saved`

The app replaces the file atomically. If a read fails mid-swap, retry it.

### 4.5 Known WinForms quirks

Expect these; they are not bugs:

- **Empty labels:** an empty label reports the previous label's text as its UIA Name. For example, an empty
  validation-errors label reads like the underwriting label. Compare against the expected message and treat a
  borrowed text as empty.
- **Stray Name:** the menu bar and the toolbar report the Name "Status:". Don't use it.
- **Message boxes:** they are titled "Policy Desk"; the About box is titled "About Policy Desk".
- **Find:** it selects the first matching row. Open and Edit then act on that row.

---

## 5. Expected values to rely on

Take expected values from the [knowledge base](policy-desk-knowledge-base.md). Some anchors:

| Input | Expected |
|---|---|
| Auto, Standard, deductible 500 (the form's defaults) | "Premium: 600.00 a year", "Paid annually" |
| Renters, Basic, 250, monthly | "Premium: 198.00 a year", "Monthly: 12 x 17.00 (204.00 a year)" |
| Auto, Premium, 250, Business, multi-policy, roadside | "Premium: 919.37 a year" |
| Home, Premium, 250 | 1,619.20, "Underwriting: Referred - Premium over 1,500.00 needs an underwriter." |
| Umbrella for a policyholder with no bound Auto/Home | "Underwriting: Referred - Umbrella needs a bound Auto or Home policy for this policyholder." |
| Umbrella for "Dana Whitfield" (bound Auto 1001) | "Underwriting: Approved" |
| First quote saved after `--reset` | Quote 1006, status Pending (Approved) or Referred |
| Bind quote 1002 | Status "Quote 1002 bound as policy HMP-1002"; record status Bound, policy_number HMP-1002 |

---

## 6. UI releases (for self-healing work)

The app can present four UI releases with identical behaviour; see the knowledge base, "UI releases".

| Release | What changes |
|---|---|
| v1 | The baseline |
| v2 | Every AutomationId is renamed, texts change, and two field pairs swap places |
| v3 | AutomationIds are random on every launch |
| v4 | Labels, texts, positions and control types change too |
| `--drift N` | Every AutomationId changes again every N seconds |

- **Writing scripts:** write them for v1. Use the v2–v4 wordings in the test cases only when you are asked to make
  scripts release-proof.
- **Healing a broken locator:** do it from what the screen shows (Name, ControlType, label, position in the
  window).
- **Swapped fields:** Email ↔ Phone and Coverage ↔ Deductible swap places in v2–v4. A "heal" by position fills the
  wrong field. Check the record.

---

## 7. What not to use (when your output is being evaluated)

Don't read or use these. They are the answer keys used to score generated scripts and self-healing:

| Don't use | Why |
|---|---|
| `policydesk-cli.exe --locator-map …` and `data\locator-map.json` | Ground truth for scoring heals |
| [`../app/releases.json`](../app/releases.json) | The rename rules, the same answer key |
| [`../app/Defects.cs`](../app/Defects.cs), `--defects` / `POLICYDESK_DEFECTS`, [`../tools/defect_matrix.py`](../tools/defect_matrix.py), the "Planted defects" sections of the README and the knowledge base | Deliberate bugs your tests should find on their own |
| The app's C# source in [`../app/`](../app) | Black-box testing: work from the docs and the screen |

Your tests may be run against a "buggy build" without warning. A good test fails there with a message that names
the symptom (for example `expected Bound, got Pending`). It must never pass by accident, and it must not fail
for an unrelated reason such as a timeout caused by a fixed sleep.

---

## 8. Done criteria

A delivered set of tests is done when:

1. **Clean run:** every test passes on a clean build (`--release v1`, no defects), run one after another from a
   fresh `--reset` each time.
2. **Traceability:** each test names its Test Case ID and AC IDs (in its name, docstring or a marker).
3. **Record checks:** each test that changes data checks `policydesk.json`.
4. **Hygiene:**
   - No fixed sleeps.
   - No clicks by screen coordinates, except the four legacy toolbar buttons and the Notes box.
   - No use of the files in section 7.
5. **Failure messages:** a failing test says what was expected and what was seen.
6. **Clean-up:** a test that fails to launch or sign in still closes the app.
7. **Coverage report:** every test case in the CSV is either automated or listed with the reason it isn't
   (for example "the chart needs image comparison").

---

## 9. Reference: what already exists

| Path | What it is |
|---|---|
| [../README.md](../README.md) | App overview, options, rules, releases, traps |
| [policy-desk-knowledge-base.md](policy-desk-knowledge-base.md) | Full behaviour reference |
| [policy-desk-requirements.csv](policy-desk-requirements.csv) | Requirements and acceptance criteria |
| [policy-desk-test-cases.csv](policy-desk-test-cases.csv) | Test cases to automate |
| [../screens/base.py](../screens/base.py) | Find, wait (`until`), set text, choose (keyboard), press (Invoke), click (mouse), message boxes |
| [../screens/desk.py](../screens/desk.py) | Launch, sign-in, status bar, closing |
| [../screens/quote.py](../screens/quote.py) | New quote tab: fill, phone, date, calculate, underwriting, save, toolbar save |
| [../screens/quotes.py](../screens/quotes.py) | Quotes tab: grid rows, filter, select, open, edit, export; details and decline dialogs |
| [../screens/record.py](../screens/record.py) | Reading `policydesk.json` |
| [../tests/conftest.py](../tests/conftest.py) | Build once, fresh launch per test, temp folders, `--release` option |
| [../tests/](../tests) | 28 example tests |
| [../tools/uia_report.bat](../tools/uia_report.bat) | Prints what UI Automation sees: `tools\uia_report.bat -Exe app\bin\PolicyDesk.exe` |
| [../tools/Smoke.cs](../tools/Smoke.cs) | A 10-step end-to-end example in C# (native UIA3) |
