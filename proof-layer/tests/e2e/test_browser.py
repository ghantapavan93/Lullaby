"""Browser end-to-end checks against a real server, a real database and
Chromium (Playwright). These verify what a person actually sees.

Run:  python -m pytest tests/e2e
"""
import os
import re
import socket
import subprocess
import sys
import time

import httpx
import pytest
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import func, select, text

from app.db import SessionLocal, transaction
from app.models import AccessAction, AnalyticsEvent, PurchaseIntent, RenderReceipt, VideoObservation

from ..conftest import ROOT


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
                            cwd=ROOT, env=os.environ | {"PROOF_LAYER_DEV": "1"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/v1/dev/accounts", timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield base
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def reset(server):
    token = httpx.post(server + "/v1/dev/sessions", json={"member_id": "mem_parent_a"}).json()["token"]
    httpx.post(server + "/v1/dev/reset", headers={"Authorization": f"Bearer {token}"}, timeout=30).raise_for_status()


@pytest.fixture
def page(server, browser):
    reset(server)
    context = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    pg = context.new_page()
    pg.goto(server + "/#/briefing")
    expect(pg.get_by_label("Revision 4, current")).to_be_visible()
    yield pg
    context.close()


def engineering(page: Page):
    """Engineering pages and tools sit behind one disclosure in the side nav."""
    details = page.locator("#nav-eng")
    if details.get_attribute("open") is None:
        details.locator("summary").click()
    return page.locator("#side-nav")


def dev(page: Page, label: str):
    """Scenario controls live in their own drawer, opened from the side nav."""
    drawer = page.locator("#dev-drawer")
    if drawer.get_attribute("aria-hidden") != "false":
        engineering(page).get_by_role("button", name="Scenario controls").click()
        expect(drawer).to_have_attribute("aria-hidden", "false")
    drawer.get_by_role("button", name=label).click()


def receipts() -> int:
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(RenderReceipt)).scalar_one()


def test_stale_screen_keeps_its_revision_and_ack_binds_to_it(page):
    dev(page, "show both caregivers' screens")
    dev(page, "Parent A corrects final wake → 06:35")
    frames = page.locator(".screen-frame")
    expect(frames.nth(0).get_by_text("Rev 5", exact=True)).to_be_visible()
    caregiver = frames.nth(1)
    expect(caregiver.get_by_text("Rev 4 · earlier version")).to_be_visible()
    expect(caregiver.get_by_text("10 h 40 min").first).to_be_visible()          # not swapped underneath
    expect(caregiver.get_by_text("A newer version exists. You're viewing revision 4.")).to_be_visible()
    caregiver.get_by_role("button", name="Acknowledge revision 4").click()
    expect(caregiver.get_by_text("You acknowledged the earlier version.")).to_be_visible()
    expect(caregiver.get_by_text("Two details have changed since then.", exact=False)).to_be_visible()


def test_refresh_shows_durable_state(page, server):
    dev(page, "Parent A corrects final wake → 06:35")
    page.reload()
    expect(page.get_by_text("Rev 5", exact=True)).to_be_visible()
    expect(page.locator(".tile").filter(has_text="Recorded sleep")).to_contain_text("10 h 25 min")
    expect(page.locator(".tile").filter(has_text="Camera observation")).to_contain_text("94.4%")


def test_rendered_explanation_creates_one_exposure_and_refresh_does_not_duplicate(page, server):
    """Exposure is the explanation card, where the experiment's variant renders
    (Growth review, 2026-10-12), not the offer card a hardware-blocked family never sees."""
    before = receipts()
    page.goto(server + "/#/features")
    page.get_by_role("button", name="Check availability").click()
    page.locator(".blocker-card").scroll_into_view_if_needed()
    expect(page.locator(".blocker-card")).to_be_in_viewport()
    page.wait_for_timeout(800)
    with SessionLocal() as db:
        offer = db.execute(select(func.count()).select_from(RenderReceipt)
                           .where(RenderReceipt.surface == "feature_availability")).scalar_one()
    assert offer == 1 and receipts() == before + 1
    page.reload()
    page.get_by_role("button", name="Check availability").click()   # a new decision is a new subject
    page.locator(".blocker-card").scroll_into_view_if_needed()
    page.wait_for_timeout(800)
    page.reload()                                                    # refresh of the same screen
    page.wait_for_timeout(800)
    with SessionLocal() as db:
        ids = db.execute(select(RenderReceipt.render_id)).scalars().all()
    assert len(ids) == len(set(ids))


def test_an_offer_never_rendered_creates_no_exposure(page, server):
    token = httpx.post(server + "/v1/dev/sessions", json={"member_id": "mem_parent_a"}).json()["token"]
    with transaction() as db:
        db.execute(text("TRUNCATE render_receipts"))
    httpx.post(server + "/v1/capability-decisions", json={"feature": "nextnap"},
               headers={"Authorization": f"Bearer {token}"})
    time.sleep(0.5)
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(RenderReceipt)
                          .where(RenderReceipt.surface == "feature_availability")).scalar_one() == 0


@pytest.mark.parametrize("behavior,label", [("decline", "Failed. Not charged"),
                                            ("pending", "Outcome unknown. Checking")])
def test_failed_and_unknown_outcomes_are_distinct_and_never_celebrate(page, server, behavior, label):
    page.goto(server + "/#/features")
    page.get_by_role("button", name="Check availability").click()
    dev(page, f"next provider reply: {'decline' if behavior == 'decline' else 'pending'}")
    page.get_by_role("button", name="Start simulated upgrade").click()
    pill = page.locator(".pill")
    expect(pill).to_have_text(label, timeout=15000)
    expect(page.get_by_text("Confirmed (simulated)")).to_have_count(0)
    assert page.locator(".pill-CONFIRMED").count() == 0


def test_hardware_blocked_purchase_cannot_be_forced_from_the_ui(page, server):
    page.goto(server + "/#/features")
    page.get_by_role("button", name="Check availability").click()
    expect(page.get_by_text("Start simulated upgrade")).to_be_visible()
    dev(page, "camera moved → Flex Stand")
    # Manipulate the page: re-enable and click the old offer as if the client were tampered with.
    # Re-enable and click in one step: a re-render between the two would swap the button.
    page.evaluate("(() => { const b = document.querySelector('[data-action=buy]'); b.disabled = false; b.click(); })()")
    expect(page.get_by_text("We didn't submit this offer.")).to_be_visible()
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(PurchaseIntent)).scalar_one() == 0


def test_mobile_layout_keeps_the_critical_facts(server, browser):
    reset(server)
    context = browser.new_context(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
    pg = context.new_page()
    pg.goto(server + "/#/briefing")
    for text_ in ("10 h 40 min", "06:50", "94.4%", "40 min", "Not affiliated with Nanit"):
        expect(pg.get_by_text(text_, exact=False).first).to_be_visible()
    expect(pg.locator(".tab-bar")).to_be_visible()
    overflow = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 0, f"horizontal overflow {overflow}px"
    context.close()


def test_in_app_reset_reloads_the_night(page):
    """Regression: reset reseeds with new window ids; the UI must resolve the new
    window instead of calling the old one (it returned 404 before the fix)."""
    dev(page, "Parent A corrects final wake → 06:35")
    expect(page.get_by_text("Rev 5", exact=True)).to_be_visible()
    dev(page, "reset demo (night at rev 4)")
    expect(page.get_by_label("Revision 4, current")).to_be_visible()
    dev(page, "Parent A corrects final wake → 06:35")
    expect(page.get_by_text("Rev 5", exact=True)).to_be_visible()


def test_present_mode_drives_the_real_app_to_a_recovered_purchase(server, browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = context.new_page()
    seen = []
    pg.expose_function("note", lambda t: seen.append(t))
    pg.goto(server + "/?present=engineering&fast=1")
    pg.evaluate("""() => setInterval(() => { const l = document.querySelector('.present-line');
        if (l && l.textContent) window.note(l.textContent); }, 150)""")
    expect(pg.locator(".pill")).to_have_text("Confirmed (simulated)", timeout=90000)
    expect(pg.locator("#present-bar")).to_have_count(0, timeout=30000)
    text = " ".join(seen)
    assert "Caregiver B acknowledged the earlier version. Two details have changed since then." in text
    assert "refused before any payment call" in text
    assert "provider attempts 1" in text
    assert "failed" not in pg.locator(".dev-log").first.text_content()
    context.close()


# ---------------------------------------------------------------- found by the frontend audit (2026-10-10)

def _api(server, method, path, member="mem_parent_a", **kw):
    token = httpx.post(server + "/v1/dev/sessions", json={"member_id": member}).json()["token"]
    return httpx.request(method, server + path, headers={"Authorization": f"Bearer {token}"}, timeout=30, **kw)


def test_a_rejected_correction_can_be_retried_without_losing_the_sheet(page, server):
    """D1: after REVISION_CONFLICT the sheet used to drop its submit handler,
    so the next Save closed it and sent nothing."""
    page.get_by_role("button", name="Correct final wake").click()
    # Another parent's correction lands first, so this screen is now stale.
    me = _api(server, "GET", "/v1/me").json()
    w = next(x for x in me["windows"] if x["local_date"] == "2026-10-09")["window_id"]
    b = _api(server, "GET", f"/v1/windows/{w}/brief").json()
    target = sorted(b["facts"]["recorded_sleep"]["intervals"], key=lambda i: i["end"])[-1]["event_id"]
    assert _api(server, "POST", f"/v1/windows/{w}/corrections", json={
        "target_event_id": target, "corrected_end": "2026-10-10T06:40:00-04:00",
        "expected_revision": b["revision"], "client_event_id": "e2e-other-parent-1"}).status_code == 200
    save = page.get_by_role("button", name="Save correction")
    save.click()
    expect(page.get_by_text("The briefing changed since this screen loaded.")).to_be_visible()
    save.click()                                            # the retry still reaches the server
    expect(page.locator("#correction-sheet")).to_have_attribute("open", "")
    page.get_by_role("button", name="Show the newer revision").click()
    expect(page.get_by_label("Revision 5, current")).to_be_visible()


def test_background_refresh_keeps_keyboard_focus(page):
    """D2: the 4-second poll rebuilt the navigation and dropped focus to the page body."""
    page.get_by_role("radio", name="Caregiver B").focus()
    page.wait_for_timeout(9000)                             # two polls
    assert page.evaluate("document.activeElement?.getAttribute('data-viewer')") == "mem_caregiver_b"


def test_closing_xray_returns_focus_to_its_opener(page):
    """D7: closing the drawer left focus inside the hidden drawer."""
    opener = engineering(page).get_by_role("button", name="Engineering X-Ray")
    opener.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#xray")).to_have_attribute("aria-hidden", "false")
    page.keyboard.press("Escape")
    assert page.evaluate("document.activeElement?.dataset.action") == "xray-open"


def test_recruiter_present_run_leaves_the_camera_on_its_stand(server, browser):
    """D5: recruiter and product runs ended with the camera on a Flex Stand,
    so the next availability check said 'blocked by hardware'."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = context.new_page()
    pg.goto(server + "/?present=recruiter&fast=1")
    expect(pg.locator("#present-bar")).to_be_visible(timeout=30000)
    expect(pg.locator("#present-bar")).to_have_count(0, timeout=90000)
    devices = _api(server, "GET", "/v1/household/commercial").json()["devices"]
    assert next(d for d in devices if d["is_primary"])["mount"] == "floor_stand"
    context.close()


def test_scenario_controls_are_a_drawer_outside_the_parent_ui(page):
    """Desktop overhaul: developer controls left the side nav for their own
    drawer; the parent navigation shows only product destinations."""
    assert page.locator("#side-nav .dev-panel").count() == 0
    dev(page, "Parent A corrects final wake → 06:35")
    expect(page.get_by_text("Rev 5", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#dev-drawer")).to_have_attribute("aria-hidden", "true")


def test_docked_xray_leaves_the_page_readable(page):
    """At 1280px and wider the X-Ray docks beside the page instead of covering it."""
    engineering(page).get_by_role("button", name="Engineering X-Ray").click()
    expect(page.locator("#xray")).to_have_attribute("aria-hidden", "false")
    page.wait_for_timeout(500)
    main_right = page.evaluate("document.querySelector('main').getBoundingClientRect().right")
    xray_left = page.evaluate("document.querySelector('#xray').getBoundingClientRect().left")
    assert main_right <= xray_left + 1, (main_right, xray_left)


def test_present_controls_never_cover_the_page(server, browser):
    """Present Mode's controls sit in the sticky chrome above the page."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = context.new_page()
    pg.goto(server + "/?present=product")
    expect(pg.locator("#present-bar")).to_be_visible(timeout=30000)
    bar = pg.locator("#present-bar").bounding_box()
    main = pg.locator("main").bounding_box()
    assert bar["y"] + bar["height"] <= main["y"] + 1 or pg.evaluate("scrollY") > 0
    # Whatever is scrolled, the strip is part of the sticky stack, not an overlay.
    assert pg.evaluate("getComputedStyle(document.getElementById('top-stack')).position") == "sticky"
    assert pg.evaluate("document.getElementById('present-bar').parentElement.id") == "present-slot"
    pg.locator("[data-present=exit]").click()
    expect(pg.locator("#present-bar")).to_have_count(0, timeout=15000)
    context.close()


# ---------------------------------------------------------------- purchase to access

def journey(server, browser, scenario, path, member_view=None):
    _api(server, "POST", "/v1/dev/access-scenario", json={"scenario": scenario}).raise_for_status()
    context = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    pg = context.new_page()
    pg.goto(server + path)
    return context, pg


def test_paid_not_active_connects_then_verifies_without_buying(server, browser):
    context, pg = journey(server, browser, "paid_not_active", "/#/access/dev_cam_demo")
    expect(pg.locator(".access-status")).to_contain_text("We found your plan")
    pg.get_by_role("button", name="Connect to Nursery camera").click()
    sheet = pg.locator("#access-sheet")
    expect(sheet).to_contain_text("Nothing is charged")
    sheet.get_by_role("button", name="Connect to Nursery camera").click()
    expect(pg.locator(".verified-card")).to_contain_text("It works on the nursery camera")
    expect(pg.locator(".access-features")).to_contain_text("Works here")
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(PurchaseIntent)).scalar_one() == 0
    context.close()


def test_a_stale_connect_is_refused_in_the_sheet_and_nothing_changes(server, browser):
    context, pg = journey(server, browser, "paid_not_active", "/#/access/dev_cam_demo")
    pg.get_by_role("button", name="Connect to Nursery camera").click()
    _api(server, "POST", "/v1/dev/mount", json={"mount": "wall_mount"}).raise_for_status()   # changed meanwhile
    pg.locator("#access-sheet").get_by_role("button", name="Connect to Nursery camera").click()
    expect(pg.locator("#connect-error")).to_contain_text("changed since this screen loaded")
    assert _api(server, "GET", "/v1/access/cameras/dev_cam_demo").json()["status"] == "VERIFIED_UNBOUND"
    context.close()


def test_a_code_on_the_wrong_camera_is_explained_not_moved(server, browser):
    context, pg = journey(server, browser, "paid_wrong_camera", "/#/access/dev_cam_demo")
    pg.select_option("#lookup-channel", "activation_code")
    pg.fill("#lookup-ref", "CODE-MEM-2002")
    pg.get_by_role("button", name="Check this purchase").click()
    result = pg.locator(".lookup-result")
    expect(result).to_contain_text("This plan is on another camera")
    expect(result.get_by_role("button", name="Connect to Nursery camera")).to_have_count(0)
    result.get_by_role("button", name="What to tell support").click()
    expect(pg.locator(".support-summary")).to_contain_text("CODE-MEM-2002")
    context.close()


def test_caregiver_is_told_to_ask_the_owner_and_sees_no_purchase_detail(server, browser):
    context, pg = journey(server, browser, "paid_not_active", "/#/home")
    pg.get_by_role("radio", name="Caregiver B").click()
    pg.goto(server + "/#/access/dev_cam_demo")
    expect(pg.locator(".access-status")).to_contain_text("Ask Parent A to connect it")
    expect(pg.get_by_role("button", name="Connect to Nursery camera")).to_have_count(0)
    expect(pg.locator("#access-lookup")).to_have_count(0)
    context.close()


@pytest.mark.parametrize("scenario", ["paid_not_active", "incompatible_hardware", "purchase_uncertain",
                                      "paid_twice", "access_lost_after_refund"])
def test_access_screens_never_offer_a_purchase(server, browser, scenario):
    context, pg = journey(server, browser, scenario, "/#/home")
    expect(pg.locator(".access-card").first).to_be_visible()
    for path in ("/#/home", "/#/access/dev_cam_demo", "/#/access/dev_cam_playroom"):
        pg.goto(server + path)
        pg.wait_for_selector(".access-card, .access-status, .verified-card")
        text = pg.locator("main").inner_text().lower()
        assert "upgrade" not in text and "buy " not in text.replace("nothing more to buy", ""), (scenario, path)
        assert pg.locator("main .offer-card").count() == 0
    context.close()


def test_customer_evidence_opens_the_working_scenario(server, browser):
    """Public report → synthetic scenario → the parent journey on real API state."""
    context = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    pg = context.new_page()
    pg.goto(server + "/#/customers")
    card = pg.locator(".customer-card.demo")
    expect(card).to_contain_text("Renewed, but the app asks to subscribe")
    from app import customer_evidence
    private = customer_evidence.private_sources()    # the author's copy; public copies say "not distributed"
    if private:
        expect(card).to_contain_text(private["renewal-not-reflected"]["sources"][0]["ref"])
    else:
        expect(card).to_contain_text("not distributed")
    card.get_by_role("button", name="Try: Plan ended, no renewal").click()
    expect(pg.locator(".access-status")).to_contain_text("This plan has ended")
    expect(pg.locator(".access-status")).not_to_contain_text("Upgrade")
    pg.goto(server + "/#/customers")
    pg.locator(".customer-card.demo").get_by_role("button", name="Try: Renewed, still active").click()
    expect(pg.locator(".camera-card[aria-checked=true]")).to_contain_text(re.compile("Plan active|Verified on this camera"))
    context.close()


def test_engineering_tools_stay_behind_one_disclosure_on_parent_pages(page):
    """Parent navigation first: on a parent page the engineering links are collapsed."""
    page.goto(page.url.split("#")[0] + "#/home")
    expect(page.locator(".access-card").first).to_be_visible()
    assert page.locator("#nav-eng").get_attribute("open") is None
    expect(page.locator("#side-nav").get_by_role("link", name="Scenario Studio")).to_be_hidden()
    expect(page.locator("#side-nav").get_by_role("link", name="Access journey")).to_be_visible()


def test_an_ended_plan_renews_on_the_same_camera_in_the_browser(server, browser):
    """Task: expired without renewal → correct plan, date, what still works →
    the store's renewal → same camera, feature usable, no purchase, periods shown."""
    context, pg = journey(server, browser, "plan_ended", "/#/studio")
    pg.locator('.studio-card [data-action="studio-run"][data-key="plan_ended"]').click()
    expect(pg.locator(".studio-card.active")).to_be_visible()
    pg.goto(server + "/#/access/dev_cam_demo")
    status = pg.locator(".access-status")
    expect(status).to_contain_text("This plan has ended")
    expect(status).to_contain_text("Memories on Nursery camera ended on")
    expect(status).to_contain_text("Sleep is still active")
    expect(pg.locator(".periods")).to_contain_text("Ended")
    pg.get_by_role("button", name="Store sends the renewal").click()
    expect(pg.locator(".access-status")).to_contain_text("Renewed")
    expect(pg.locator(".access-features")).to_contain_text("Works here")
    expect(pg.locator(".periods .period-tag")).to_contain_text("renewal")
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(PurchaseIntent)).scalar_one() == 0
        cams = db.execute(text("SELECT DISTINCT device_id FROM entitlement_claims WHERE external_ref = 'apple-txn-8008'")).scalars().all()
    assert cams == ["dev_cam_demo"]
    engineering(pg).get_by_role("button", name="Engineering X-Ray").click()
    periods = pg.locator("#xray .xsec[data-sec=periods] tbody tr")
    expect(periods).to_have_count(3)                       # trial + the ended year + the renewed year
    expect(periods.filter(has_text="App Store").filter(has_text="yes")).to_have_count(1)
    context.close()


def test_a_failed_renewal_payment_recovers_on_the_same_camera_in_the_browser(server, browser):
    """Task: Google account hold -> no access, nothing to buy, the purchase stays
    on its camera -> the store's recovery -> usable again with no new connect."""
    context, pg = journey(server, browser, "billing_retry", "/#/studio")
    pg.locator('.studio-card [data-action="studio-run"][data-key="billing_retry"]').click()
    expect(pg.locator(".studio-card.active")).to_be_visible()
    pg.goto(server + "/#/access/dev_cam_demo")
    status = pg.locator(".access-status")
    expect(status).to_contain_text("Your renewal payment didn't go through")
    expect(status).to_contain_text("in your Google Play account")
    expect(status).to_contain_text("nothing more to buy")
    pg.get_by_role("button", name="Store recovers the payment").click()
    expect(pg.locator(".access-status")).to_contain_text(re.compile("Plan active|Verified on this camera"))
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(PurchaseIntent)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(AccessAction)).scalar_one() == 0    # no new connect
        cams = db.execute(text("SELECT DISTINCT device_id FROM entitlement_claims WHERE external_ref = 'gpa-1202'")).scalars().all()
    assert cams == ["dev_cam_demo"]
    context.close()


def test_recorded_evidence_after_the_renewal_in_the_browser(server, browser):
    """Task: Milestones ended -> the clip is refused (no offer) -> the store's
    renewal -> video history verified -> the worker analyses the clip in its
    own process -> a parent's report that contradicts a measurement is kept
    beside it, and the measurement is unchanged."""
    context, pg = journey(server, browser, "evidence_journey", "/#/studio")
    pg.locator('.studio-card [data-action="studio-run"][data-key="evidence_journey"]').click()
    expect(pg.locator(".studio-card.active")).to_be_visible()
    pg.goto(server + "/#/recorded")
    expect(pg.locator("#gate-title")).to_contain_text("isn't on this camera's plan", timeout=30000)
    assert pg.locator(".offer-card").count() == 0
    pg.goto(server + "/#/access/dev_cam_demo")
    pg.get_by_role("button", name="Store sends the renewal").click()
    expect(pg.locator(".access-status")).to_contain_text(re.compile("Plan active|Verified on this camera"))
    pg.locator(".recorded-link").click()
    pg.get_by_role("button", name="Run the worker").click()
    expect(pg.locator(".run-status")).to_contain_text("Analysed", timeout=90000)
    video = pg.locator("video#clip")
    expect(video).to_be_visible()
    assert video.get_attribute("src").startswith("/v1/video/media/")
    pg.wait_for_function("document.getElementById('clip').readyState >= 1", timeout=30000)   # real bytes, decoded
    with SessionLocal() as db:
        measured = db.execute(select(func.count()).select_from(VideoObservation)).scalar_one()
    pg.locator(".seg.kind-movement").first.click()
    pg.locator('input[value="infant_still"]').check()
    pg.get_by_role("button", name="Add my report").click()
    expect(pg.locator(".before-after")).to_contain_text("The baby was still")
    expect(pg.locator(".explain-card")).to_contain_text("disagrees with the measurement; both are kept")
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(VideoObservation)).scalar_one() == measured
        opened = db.execute(select(func.count()).select_from(AnalyticsEvent)
                            .where(AnalyticsEvent.name == "evidence_review_opened")).scalar_one()
    assert opened == 1
    context.close()


# ---------------------------------------------------------------- playback of one interval (2026-10-11 final review)

def _analysed_clip(server):
    """Through the API: Milestones renews, the worker analyses the clip in its own process."""
    from datetime import datetime, timedelta, timezone
    _api(server, "POST", "/v1/dev/access-scenario", json={"scenario": "evidence_journey"}).raise_for_status()
    start = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=5))
    _api(server, "POST", "/v1/dev/purchase-evidence", json={
        "channel": "apple", "external_ref": "apple-txn-1505", "plan_code": "milestones", "provider_event": "DID_RENEW",
        "provider_version": 2, "starts_at": start.isoformat(), "days": 365}).raise_for_status()
    _api(server, "POST", "/v1/dev/video/worker").raise_for_status()
    for _ in range(120):
        assets = _api(server, "GET", "/v1/video/assets").json()["assets"]
        if assets and assets[0]["run"] and assets[0]["run"]["state"] == "COMPLETED":
            view = _api(server, "GET", f"/v1/video/assets/{assets[0]['id']}").json()
            return next(o for o in view["observations"] if o["kind"] == "movement")
        time.sleep(1)
    raise AssertionError("the clip was not analysed")


MEDIA_EVENTS = """() => { const c = document.getElementById('clip'); window.__media = [];
  for (const e of ['seeked', 'playing', 'waiting', 'pause', 'error'])
    c.addEventListener(e, () => window.__media.push([e, c.currentTime])); }"""


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)], ids=["desktop", "mobile"])
def test_play_this_part_plays_exactly_the_selected_interval(server, browser, width, height):
    target = _analysed_clip(server)
    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
    pg = context.new_page()
    pg.goto(server + "/#/recorded")
    expect(pg.locator(".run-status")).to_contain_text("Analysed", timeout=30000)
    pick = f'.seg[data-seq="{target["seq"]}"]' if width >= 600 else f'.interval-row[data-seq="{target["seq"]}"]'
    pg.locator(pick).click()
    expect(pg.locator("#explain-title")).to_contain_text("0:02–0:05")
    pg.wait_for_function("document.getElementById('clip').readyState >= 1", timeout=30000)   # real bytes arrived
    pg.evaluate(MEDIA_EVENTS)
    pg.get_by_role("button", name="Play this part").click()
    expect(pg.locator("#play-status")).to_contain_text("Played 0:02–0:05", timeout=15000)
    events = pg.evaluate("window.__media")
    names = [e for e, _ in events]
    assert "seeked" in names and "playing" in names, events
    seeked_at = next(t for e, t in events if e == "seeked")
    assert abs(seeked_at - target["t_start"]) < 0.05, events                      # started where the interval starts
    state = pg.evaluate("(() => { const c = document.getElementById('clip'); return [c.paused, c.currentTime]; })()")
    assert state[0] and target["t_end"] <= state[1] < target["t_end"] + 0.1, state  # stopped on its end
    pg.goto(server + "/#/home")
    pg.goto(server + "/#/recorded")
    expect(pg.locator("#explain-title")).to_contain_text("0:02–0:05")             # the selection survived
    context.close()


def test_playback_failure_is_said_and_a_lapsed_link_is_renewed(server, browser):
    target = _analysed_clip(server)
    context = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    pg = context.new_page()
    pg.goto(server + "/#/recorded")
    expect(pg.locator(".run-status")).to_contain_text("Analysed", timeout=30000)
    pg.locator(f'.seg[data-seq="{target["seq"]}"]').click()
    pg.wait_for_function("document.getElementById('clip').readyState >= 1", timeout=30000)
    # Five minutes later the media link has lapsed: Play asks for a new one first, then plays.
    pg.evaluate("(() => { const now = Date.now; Date.now = () => now() + 6 * 60 * 1000; })()")
    with pg.expect_request(lambda r: r.url.endswith("/media-link") and r.method == "POST"):
        pg.get_by_role("button", name="Play this part").click()
    expect(pg.locator("#play-status")).to_contain_text("Played 0:02–0:05", timeout=15000)
    # The media server goes away: the page says so instead of doing nothing.
    pg.route("**/v1/video/media/**", lambda route: route.fulfill(status=503, body="unavailable"))
    pg.evaluate("(() => { const c = document.getElementById('clip'); c.src = c.src + '?retry'; })()")
    pg.get_by_role("button", name="Play this part").click()
    expect(pg.locator("#play-status")).to_contain_text("couldn't", ignore_case=True, timeout=15000)
    expect(pg.locator("#play-status")).to_contain_text("Nothing was changed")
    context.close()


def test_present_never_claims_a_correction_another_visitor_beat_it_to(server, browser):
    """Cold review (2026-10-11): two visitors on one instance. Another visitor's
    correction lands first, Present's own correction is refused, and its
    caption used to say "Revision 5: recorded sleep is now ..." anyway."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = context.new_page()
    raced = []

    def other_visitor_first(route):
        if route.request.method == "POST" and not raced:
            me = _api(server, "GET", "/v1/me").json()
            w = next(x for x in me["windows"] if x["local_date"] == "2026-10-09")["window_id"]
            b = _api(server, "GET", f"/v1/windows/{w}/brief").json()
            target = sorted(b["facts"]["recorded_sleep"]["intervals"], key=lambda i: i["end"])[-1]["event_id"]
            raced.append(_api(server, "POST", f"/v1/windows/{w}/corrections", json={
                "target_event_id": target, "corrected_end": "2026-10-10T05:50:00-04:00",
                "expected_revision": b["revision"], "client_event_id": "e2e-other-visitor-race"}).status_code)
        route.continue_()

    pg.route("**/corrections", other_visitor_first)
    pg.goto(server + "/?present=product&fast=1")    # the walkthrough with the correction step
    expect(pg.locator(".present-title")).to_have_text("Parent A corrects the wake time", timeout=60000)
    expect(pg.locator(".present-line")).to_contain_text("Not saved", timeout=30000)
    assert raced == [200]
    assert "Revision 5" not in pg.locator(".present-line").inner_text()
    pg.locator("[data-present=exit]").click()
    context.close()


def test_a_hardware_blocked_decision_is_exposed_to_the_experiment(page, server):
    """The availability experiment asks about hardware-blocked decisions; they
    show no offer card, so exposure must come from the explanation card."""
    assert _api(server, "POST", "/v1/dev/mount", json={"mount": "flex_stand"}).status_code == 200
    before = receipts()
    page.goto(server + "/#/features")
    page.get_by_role("button", name="Check availability").click()
    expect(page.locator(".blocker-card.hardware")).to_be_visible()
    assert page.locator(".offer-card").count() == 0
    page.locator(".blocker-card").scroll_into_view_if_needed()
    page.wait_for_timeout(800)
    assert receipts() == before + 1
