"""Browser smoke test of the Automation Lab: every area, in all three UI releases, with one script.

The script uses only release-proof locators (labels, roles, accessible names, visible text, scoped to the visible
page region), so it is also the reference for what a self-healed locator should look like.

    pip install playwright && playwright install chromium        (or use an installed Edge/Chrome, below)
    docker compose -f automation-lab/docker-compose.yml up -d --build
    python automation-lab/e2e/smoke.py                 # all releases;   python automation-lab/e2e/smoke.py v2
Settings: LAB_URL (default http://127.0.0.1:8400), LAB_BROWSER_CHANNEL (e.g. msedge or chrome; default: Playwright's
Chromium), LAB_E2E_OUT (folder for screenshots; default: the system temp folder).
It changes lab data (quotes, claims, uploads, board moves) like any user would.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("LAB_URL", "http://127.0.0.1:8400").rstrip("/")
OUT = Path(os.environ.get("LAB_E2E_OUT") or tempfile.gettempdir())
CHANNEL = os.environ.get("LAB_BROWSER_CHANNEL") or None
fails = []


def check(name, fn):
    try:
        fn()
        print(f"  ok   {name}")
    except Exception as e:
        fails.append(f"{release}: {name}: {" | ".join(l.strip() for l in str(e).splitlines()[:7])[:900]}")
        print(f"  FAIL {name}: {" | ".join(l.strip() for l in str(e).splitlines()[:7])[:900]}")


with sync_playwright() as p:
    browser = p.chromium.launch(channel=CHANNEL, headless=True)
    for release in sys.argv[1:] or ("v1", "v2", "v3"):
        print(f"== release {release}")
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, accept_downloads=True)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("dialog", lambda d: d.accept("POL-1001") if d.type == "prompt" else d.accept())
        page.goto(f"{BASE}/?release={release}#/quote")
        page.wait_for_load_state("networkidle")

        def locators():
            n_id = page.locator("#quote-first-name").count()
            n_v2 = page.locator("#hmQuoteFirstName").count()
            n_tid = page.locator("[data-testid]").count()
            if release == "v1": assert n_id == 1 and n_tid > 50
            if release == "v2": assert n_id == 0 and n_v2 == 1 and n_tid > 50
            if release == "v3": assert n_id == 0 and n_v2 == 0 and n_tid == 0
            btn = "Get my quote" if release == "v1" else "Calculate premium"
            bg = page.get_by_role("button", name=btn).evaluate("e => getComputedStyle(e).backgroundColor")
            assert bg in ("rgb(38, 53, 127)", "rgb(143, 160, 255)"), f"primary button not styled: {bg}"
        check("locators change per release, styling survives", locators)

        def quote():
            q = page.get_by_role("region", name="Get a quote")
            q.get_by_label("Product").select_option("auto")
            q.get_by_label("Coverage start date").fill("2026-11-20")
            q.get_by_label("Premium", exact=True).check()
            q.get_by_label("Roadside assistance (+$48)").check()
            page.get_by_role("combobox", name="Vehicle make").fill("toy")
            page.get_by_role("option", name="Toyota").click()
            q.get_by_label("First name").fill("Shan")
            q.get_by_label("Last name").fill("Mercer")
            q.get_by_label("Date of birth").fill("1990-05-01")
            q.get_by_label("Email").fill("shan@example.com")
            q.get_by_label("ZIP code").fill("02110")
            page.get_by_role("switch", name=re.compile("Paperless")).click()
            q.get_by_label(re.compile("I agree that Harbor Mutual")).check()
            page.get_by_role("button", name=re.compile("Get my quote|Calculate premium")).click()
            expect(page.get_by_text(re.compile(r"^Q-2026-\d{4}$"))).to_be_visible(timeout=5000)
            expect(page.get_by_text(re.compile(r"/ year$"))).to_be_visible()
            page.screenshot(path=str(OUT / f"quote-{release}.png"), full_page=True)
        check("quote form end to end", quote)

        def quote_validation():
            page.get_by_role("button", name=re.compile("Start over|Clear form")).click()
            page.get_by_role("button", name=re.compile("Get my quote|Calculate premium")).click()
            expect(page.get_by_role("alert").filter(has_text="Please correct")).to_be_visible()
            expect(page.get_by_text("Enter a valid email address.")).to_be_visible()
        check("quote validation", quote_validation)

        def claim():
            page.get_by_role("link", name="File a claim").first.click()
            page.get_by_label("Policy number").fill("POL-1001")
            expect(page.get_by_text("Policyholder: Shan Mercer", exact=False)).to_be_visible()
            page.get_by_label("What happened?").select_option("collision")
            page.get_by_label("Date of incident").fill("2026-10-30")
            page.get_by_label("Where did it happen?").fill("Main St, Boston")
            nxt = page.get_by_role("button", name=re.compile("^(Next|Continue)$"))
            nxt.click()
            page.get_by_label("Describe what happened").fill("Rear-ended at a stop light; bumper damaged.")
            page.get_by_label("Estimated damage (USD)").fill("2500")
            nxt.click()
            page.get_by_label("Attach files").set_input_files([{"name": "photo.png", "mimeType": "image/png", "buffer": b"png"}])
            expect(page.get_by_text("photo.png")).to_be_visible()
            nxt.click()
            page.get_by_label("Sign by typing the policyholder's full name").fill("Shan Mercer")
            page.get_by_label(re.compile("information I've given is true")).check()
            page.get_by_role("button", name=re.compile("Submit claim|File my claim")).click()
            expect(page.get_by_text(re.compile(r"^CLM-9\d{3}$"))).to_be_visible()
        check("claim wizard end to end", claim)

        def documents():
            page.goto(f"{BASE}/?release={release}#/documents")
            page.get_by_label("Files", exact=True).set_input_files([
                {"name": "estimate.pdf", "mimeType": "application/pdf", "buffer": b"%PDF demo"},
                {"name": "bad.exe", "mimeType": "application/octet-stream", "buffer": b"MZ"}])
            page.get_by_role("button", name=re.compile("^Upload( files)?$")).click()
            expect(page.get_by_text("Uploaded 1 file, 1 rejected")).to_be_visible()
            # drop a file onto the zone with a synthetic DataTransfer
            dt = page.evaluate_handle("""() => { const d = new DataTransfer();
                d.items.add(new File(['hello'], 'dropped.txt', {type: 'text/plain'})); return d; }""")
            zone = page.get_by_role("button", name=re.compile("Drop files here"))
            zone.dispatch_event("dragenter", {"dataTransfer": dt})
            zone.dispatch_event("drop", {"dataTransfer": dt})
            expect(page.get_by_text(re.compile("dropped.txt uploaded as UPL-"))).to_be_visible()
            with page.expect_download() as dl:
                page.get_by_role("link", name="Download policy list (CSV)").click()
            assert dl.value.suggested_filename == "harbor-mutual-policies.csv"
        check("uploads, drop zone, download", documents)

        def board():
            page.goto(f"{BASE}/?release={release}#/board")
            page.request.put(f"{BASE}/api/board/cards/CLM-5003", data={"column": "new", "position": 0})
            page.reload()
            card = page.get_by_role("listitem", name=re.compile(r"^CLM-5003,"))
            lane = page.get_by_role("list", name="In review")
            card.drag_to(lane)
            expect(page.get_by_text("Moved CLM-5003 from New to In review")).to_be_visible()
            page.get_by_role("button", name="Move CLM-5003 left").click()
            expect(page.get_by_text("Moved CLM-5003 from In review to New")).to_be_visible()
            thumb = page.get_by_role("slider", name="Coverage limit")
            thumb.focus(); page.keyboard.press("End")
            expect(page.get_by_text("$1,000,000").first).to_be_visible()
            box = page.get_by_label("Signature pad").bounding_box()
            page.mouse.move(box["x"] + 20, box["y"] + 40); page.mouse.down()
            page.mouse.move(box["x"] + 200, box["y"] + 100, steps=8); page.mouse.up()
            expect(page.get_by_text(re.compile("Signed: 1 stroke"))).to_be_visible()
            handle = page.get_by_role("button", name="Reorder Audit large payments")
            handle.scroll_into_view_if_needed(); page.mouse.wheel(0, 200); page.wait_for_timeout(200)
            h = handle.bounding_box()
            page.mouse.move(h["x"] + 5, h["y"] + 5); page.mouse.down()
            page.mouse.move(h["x"] + 5, h["y"] - 220, steps=12); page.mouse.up()
            expect(page.get_by_text(re.compile(r"Order: 1\. Audit large payments"))).to_be_visible()
        check("board drag and drop, slider, signature, sortable", board)

        def policies():
            page.goto(f"{BASE}/?release={release}#/policies")
            expect(page.get_by_text("Showing 1–10 of 57 policies")).to_be_visible()
            page.get_by_role("searchbox", name="Search").fill("indu")
            expect(page.get_by_text("Showing 1–3 of 3 policies")).to_be_visible()
            page.get_by_role("searchbox", name="Search").fill("")
            expect(page.get_by_text("Showing 1–10 of 57 policies")).to_be_visible()
            page.get_by_role("button", name="Page 2").click()
            expect(page.get_by_text("Showing 11–20 of 57 policies")).to_be_visible()
            page.get_by_role("button", name="Actions for POL-1011").click()
            page.get_by_role("menuitem", name="View details").click()
            expect(page.get_by_role("dialog", name="Policy POL-1011")).to_be_visible()
            page.keyboard.press("Escape")
            page.get_by_label("Select POL-1012").check()
            expect(page.get_by_text("1 selected")).to_be_visible()
        check("policies table", policies)

        def interactions():
            page.goto(f"{BASE}/?release={release}#/interactions")
            page.get_by_text("Double-click this box").dblclick()
            expect(page.get_by_text(re.compile("Double-clicked the box"))).to_be_visible()
            page.get_by_text("Right-click this box").click(button="right")
            page.get_by_role("menuitem", name="Flag for review").click()
            expect(page.get_by_text("Context menu: Flag for review")).to_be_visible()
            page.get_by_role("button", name="Show prompt").click()
            expect(page.get_by_text("Prompt answered: POL-1001")).to_be_visible()
            page.get_by_role("button", name=re.compile("Open dialog|Show dialog")).click()
            page.get_by_role("dialog").get_by_label("Preferred name").fill("Shan")
            page.get_by_role("button", name="Save").click()
            expect(page.get_by_text("Preferred name saved: Shan")).to_be_visible()
            frame = page.frame_locator("iframe[title='Payment widget']")
            frame.get_by_label("Name on card").fill("Shan Mercer")
            frame.get_by_label("Card number").fill("4242424242424242")
            frame.get_by_label("Expiry (MM/YY)").fill("1229")
            frame.get_by_label("Security code").fill("123")
            frame.get_by_role("button", name=re.compile("Pay now|Authorize payment")).click()
            expect(page.get_by_text(re.compile("Payment authorized: AUTH-4242"))).to_be_visible()
            page.get_by_label("Coverage to add").select_option("rental")   # pierces the open shadow root
            page.get_by_role("button", name=re.compile("Add coverage|Include coverage")).click()
            expect(page.get_by_text("Added coverage: Rental car ×1 (total 1)")).to_be_visible()
            with ctx.expect_page() as tab:
                page.get_by_role("link", name="Open receipt in a new tab").click()
            expect(tab.value.get_by_text("RCPT-2026-0042")).to_be_visible()
            tab.value.close()
            with ctx.expect_page() as pop:
                page.get_by_role("button", name="Open popup window").click()
            try:   # the popup closes itself as the click lands
                pop.value.get_by_role("button", name="Confirm and close").click(no_wait_after=True)
            except Exception as e:
                if "closed" not in str(e):
                    raise
            expect(page.get_by_text("Popup confirmed receipt RCPT-POPUP-01")).to_be_visible()
            page.get_by_role("tab", name="Terms").click()
            expect(page.get_by_text("Coverage renews every 12 months")).to_be_visible()
            page.get_by_role("button", name="You found me").click()
            expect(page.get_by_text("Found the hidden button")).to_be_visible()
        check("interactions: mouse, dialogs, iframe, shadow DOM, tabs, windows", interactions)

        def dynamic():
            page.goto(f"{BASE}/?release={release}#/dynamic")
            page.get_by_role("button", name=re.compile("Load agent|Find my agent")).click()
            expect(page.get_by_text("Your agent: Morgan Ellis")).to_be_visible(timeout=6000)
            page.get_by_role("button", name="Start", exact=True).click()
            expect(page.get_by_role("button", name="Accept terms")).to_be_enabled(timeout=8000)
            page.get_by_role("button", name="Process claim").click()
            expect(page.get_by_role("region", name="Waits & dynamic content").get_by_text("Approved", exact=True)).to_be_visible(timeout=5000)
            feed = page.get_by_label("Member notices")
            expect(page.get_by_text("Showing 10 of 60 notices")).to_be_visible()
            feed.evaluate("e => e.scrollTop = e.scrollHeight")
            expect(page.get_by_text("Showing 20 of 60 notices")).to_be_visible()
        check("waits and dynamic content", dynamic)

        def activity():
            page.goto(f"{BASE}/?release={release}#/activity")
            expect(page.get_by_role("cell", name=re.compile(r"Claim CLM-9\d{3} filed on POL-1001")).first).to_be_visible()
        check("activity log", activity)

        page.goto(f"{BASE}/?release={release}#/overview")
        page.screenshot(path=str(OUT / f"overview-{release}.png"))
        page.goto(f"{BASE}/?release={release}#/board")
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT / f"board-{release}.png"))
        if errors:
            fails.append(f"{release}: console/page errors: {errors[:5]}")
            print("  console errors:", errors[:5])
        ctx.close()

    # ------------------------------------------------------------ healing features: locator map, v4, live drift
    if not sys.argv[1:]:
        release = "healing"
        print("== healing features")
        ctx = browser.new_context(viewport={"width": 1366, "height": 900})
        page = ctx.new_page()

        def map_of(rel):
            return {e["key"]: e for e in page.request.get(f"{BASE}/api/locator-map?release={rel}").json()["elements"]}

        def map_matches_dom():
            for rel in ("v1", "v2", "v3", "v4"):
                page.goto(f"{BASE}/?release={rel}#/quote"); page.wait_for_load_state("networkidle")
                for key, e in map_of(rel).items():
                    if e["page"] == "payment-frame":
                        continue
                    live = page.evaluate("k => window.HM.R.locatorMap().elements[k]", key)
                    assert live, f"{rel}: {key} missing from the page"
                    assert live["tag"] == e["tag"], f"{rel}: {key} tag {live['tag']} != {e['tag']}"
                    if e["id"]:
                        assert page.locator(f"#{e['id']}").count() == 1, f"{rel}: #{e['id']} not found"
                    else:
                        assert live["id"] and live["id"] != key, f"{rel}: {key} kept its authored id"
                    assert live["testid"] == e["testid"], f"{rel}: {key} testid {live['testid']} != {e['testid']}"
                    if e["page"] == "quote" and e["role"] in {"textbox", "button", "combobox", "checkbox", "radio", "slider", "switch"} and e["accessible_name"]:
                        found = page.get_by_role(e["role"], name=e["accessible_name"], exact=True, include_hidden=True).count()
                        assert found >= 1, f"{rel}: role={e['role']} name={e['accessible_name']!r} ({key}) not found"
        check("locator map matches the rendered page in v1-v4", map_matches_dom)

        def v4_heals_with_map():
            names = map_of("v4")
            page.goto(f"{BASE}/?release=v4#/quote"); page.wait_for_load_state("networkidle")
            q = page.get_by_role("region", name="Get a quote")
            assert q.get_by_label("First name", exact=True).count() == 0, "v1 label still present in v4"
            label = lambda k: q.get_by_label(names[k]["accessible_name"], exact=True)  # noqa: E731
            label("quote-product").select_option("home")
            label("quote-start-date").fill("2026-11-20")
            label("quote-first-name").fill("Indu"); label("quote-last-name").fill("Varma")
            label("quote-dob").fill("1985-02-14"); label("quote-email").fill("indu@example.com")
            label("quote-zip").fill("02108"); label("quote-consent").check()
            q.get_by_role("button", name=names["quote-submit"]["text"]).click()
            expect(page.get_by_text(re.compile(r"^Q-2026-\d{4}$"))).to_be_visible(timeout=5000)
            page.goto(f"{BASE}/?release=v4#/interactions")
            assert page.get_by_role("button", name="Click me").count() == 0
            page.get_by_role("link", name="Click me").click()
            expect(page.get_by_text(re.compile(r"^Clicked \d+ times?$"))).to_be_visible()
            page.goto(f"{BASE}/?release=v4#/claim")
            c = page.get_by_role("region", name="File a claim")
            c.get_by_label("Policy no.").fill("POL-1001")
            expect(c.get_by_text("Policyholder: Shan Mercer", exact=False)).to_be_visible()
            c.get_by_label("Type of incident").select_option("theft")
            c.get_by_label("Incident date").fill("2026-10-30")
            c.get_by_label("Incident location").fill("Main St")
            c.get_by_role("button", name="Next step").click()        # a <div role="button"> in v4
            expect(c.get_by_role("heading", name=re.compile("Step 2 of 4"))).to_be_visible()
        check("v4 renames labels and element types; the map heals a script", v4_heals_with_map)

        def live_drift():
            page.goto(f"{BASE}/?release=v2&drift=2#/quote"); page.wait_for_load_state("networkidle")
            first = lambda: page.evaluate("window.HM.R.locatorMap().elements['quote-first-name'].id")  # noqa: E731
            assert first() == "hmQuoteFirstName", "drifted before the first check"
            expect(page.locator("html")).to_have_attribute("data-drift-generation", "1", timeout=5000)
            after = first()
            assert after != "hmQuoteFirstName" and page.locator("#hmQuoteFirstName").count() == 0
            assert page.locator("[data-testid]").count() == 0
            expect(page.locator("html")).to_have_attribute("data-drift-generation", "2", timeout=5000)
            assert first() != after, "the second drift did not rename again"
            q = page.get_by_role("region", name="Get a quote")
            q.get_by_label("Product").select_option("auto")
            q.get_by_label("Coverage start date").fill("2026-11-20")
            q.get_by_label("First name").fill("Shan"); q.get_by_label("Last name").fill("Mercer")
            q.get_by_label("Date of birth").fill("1990-05-01"); q.get_by_label("Email", exact=True).fill("shan@example.com")
            q.get_by_label("ZIP code").fill("02110"); q.get_by_label(re.compile("I agree that Harbor Mutual")).check()
            btn = q.get_by_role("button", name="Calculate premium")
            bg = btn.evaluate("e => getComputedStyle(e).backgroundColor")
            assert bg in ("rgb(38, 53, 127)", "rgb(143, 160, 255)"), f"styles lost after drift: {bg}"
            btn.click()
            expect(page.get_by_text(re.compile(r"^Q-2026-\d{4}$"))).to_be_visible(timeout=5000)
            kinds, after = [], 0
            while rows := page.request.get(f"{BASE}/api/activity?after={after}").json():   # 200 rows per call
                kinds += [a["kind"] for a in rows]; after = rows[-1]["seq"]
            assert "drift" in kinds, "no drift activity recorded"
        check("live drift renames while the page is open; behaviour and styles survive", live_drift)
        ctx.close()

    # phone width: no horizontal scroll
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    page.goto(f"{BASE}/#/quote"); page.wait_for_load_state("networkidle")
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    page.screenshot(path=str(OUT / "quote-phone.png"))
    print("phone overflow px:", over)
    if over > 0:
        fails.append(f"phone width overflows by {over}px")
    browser.close()

print("\nFAILURES:" if fails else "\nALL PASSED", *fails, sep="\n")
sys.exit(1 if fails else 0)
