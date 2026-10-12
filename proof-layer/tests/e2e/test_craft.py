"""Visual craft, measured in a real browser: the details a design reviewer
notices first. Each test pins a defect found by the frontend audit of
2026-10-12 (failure ledger, round 15).

- links styled as buttons read as buttons, not as underlined links;
- the night timeline draws awake time as a thin rule, as its legend says;
- stacked cards never touch, and cards side by side line up;
- form controls on a phone are at least 16 px, so iOS doesn't zoom in;
- the browser tab names the screen.
"""
import pytest
from playwright.sync_api import expect

from .test_browser import browser, reset, server  # noqa: F401  (fixtures)

ROUTES = ["#/home", "#/access/dev_cam_demo", "#/briefing", "#/changes", "#/features", "#/studio", "#/customers",
          "#/growth"]


@pytest.fixture
def desktop(server, browser):  # noqa: F811
    reset(server)
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    pg = context.new_page()
    pg.goto(server + "/#/home")
    expect(pg.locator(".access-card").first).to_be_visible()
    yield pg
    context.close()


def open_route(pg, route, ready="#main h1"):
    pg.goto(pg.url.split("#")[0] + route)
    expect(pg.locator(ready).first).to_be_visible()
    pg.wait_for_timeout(300)


def test_links_styled_as_buttons_are_not_underlined(desktop):
    underlined = []
    for route in ROUTES:
        open_route(desktop, route)
        underlined += desktop.eval_on_selector_all(
            "a.btn", "els => els.filter((e) => getComputedStyle(e).textDecorationLine !== 'none')"
                     ".map((e) => e.textContent.trim())")
    assert underlined == []


def test_awake_time_on_the_night_timeline_is_a_thin_rule(desktop):
    open_route(desktop, "#/briefing", ".track")
    awake = desktop.eval_on_selector_all(".track .seg-awake", "els => els.map((e) => [e.getBoundingClientRect().height,"
                                                              " getComputedStyle(e).cursor])")
    assert awake, "the seeded night has awake time"
    assert all(height <= 6 and cursor == "default" for height, cursor in awake), awake


GAPS = """() => {
  const cards = [...document.querySelectorAll('#main .card, #main .studio-grid, #main .clip-grid, #main .member-grid')]
    .filter((e) => e.offsetParent !== null);
  const bad = [];
  for (const a of cards) {
    const ra = a.getBoundingClientRect();
    for (const b of cards) {
      if (a === b || a.contains(b) || b.contains(a)) continue;
      const rb = b.getBoundingClientRect();
      const overlapX = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left) > 40;
      const gap = rb.top - ra.bottom;
      if (overlapX && gap > -1 && gap < 12) bad.push([a.className, b.className, Math.round(gap)]);
      const sideBySide = a.parentElement === b.parentElement && Math.abs(ra.top - rb.top) > 0.5
        && Math.min(ra.bottom, rb.bottom) > Math.max(ra.top, rb.top) && !overlapX
        && getComputedStyle(a.parentElement).display === 'grid';
      if (sideBySide) bad.push(['misaligned', a.className, b.className, Math.round(rb.top - ra.top)]);
    }
  }
  return bad;
}"""


@pytest.mark.parametrize("route", ["#/access/dev_cam_demo", "#/studio", "#/changes"])
def test_stacked_cards_never_touch_and_side_by_side_cards_line_up(desktop, route):
    open_route(desktop, route, ".card")
    assert desktop.evaluate(GAPS) == []


def test_form_controls_on_a_phone_are_large_enough_not_to_zoom(server, browser):  # noqa: F811
    reset(server)
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    pg = context.new_page()
    small = []
    for route in ("#/studio", "#/access/dev_cam_demo", "#/features"):
        pg.goto(server + "/" + route)
        expect(pg.locator("#main h1").first).to_be_visible()
        pg.wait_for_timeout(300)
        small += pg.eval_on_selector_all(
            "select, input:not([type=radio]):not([type=checkbox]), textarea",
            "els => els.filter((e) => e.offsetParent !== null && parseFloat(getComputedStyle(e).fontSize) < 16)"
            ".map((e) => e.id || e.name || e.tagName)")
    context.close()
    assert small == []


def test_the_browser_tab_names_the_screen(desktop):
    open_route(desktop, "#/studio", ".studio-card")
    assert desktop.title().startswith("Scenario Studio")
    open_route(desktop, "#/briefing", ".track")
    assert desktop.title().startswith("Night briefing")


# ---------------------------------------------------------------- phones (mobile audit, 2026-10-12)

@pytest.fixture
def phone(server, browser):  # noqa: F811
    reset(server)
    context = browser.new_context(viewport={"width": 375, "height": 667}, has_touch=True, reduced_motion="reduce")
    pg = context.new_page()
    pg.goto(server + "/#/home")
    expect(pg.locator(".access-card").first).to_be_visible()
    yield pg
    context.close()


def test_plan_cards_fit_a_small_phone_when_the_camera_is_on_a_flex_stand(phone, server):  # noqa: F811
    from .test_browser import _api
    assert _api(server, "POST", "/v1/dev/mount", json={"mount": "flex_stand"}).status_code == 200
    phone.goto(server + "/#/features")
    expect(phone.locator("#main h1")).to_be_visible()
    phone.wait_for_timeout(500)
    assert phone.evaluate("document.scrollingElement.scrollWidth - innerWidth") <= 0


def test_the_x_ray_closes_when_one_of_its_links_opens_a_page(phone):
    phone.locator('.tab-bar [data-action="xray-open"]').click()
    expect(phone.locator("#xray")).to_have_attribute("aria-modal", "true")
    phone.locator('#xray a[href="#/studio"]').first.click()
    expect(phone.locator("#xray")).to_have_attribute("aria-hidden", "true")
    expect(phone.get_by_role("heading", name="Scenario Studio", level=1)).to_be_in_viewport()


def test_after_connecting_the_result_is_on_screen_with_focus_on_it(phone, server):  # noqa: F811
    from .test_browser import _api
    assert _api(server, "POST", "/v1/dev/access-scenario", json={"scenario": "paid_not_active"}).status_code == 200
    phone.goto(server + "/#/access/dev_cam_demo")
    phone.get_by_role("button", name="Connect to Nursery camera").click()
    phone.locator("#access-sheet button[type=submit]").click()
    heading = phone.locator("#main .verified-card h2")
    expect(heading).to_be_focused(timeout=30000)
    expect(heading).to_be_in_viewport()


def test_same_problem_different_cause_opens_from_home_with_five_steps_per_case(desktop):
    desktop.get_by_role("button", name="Same problem. Different cause.").click()
    sides = desktop.locator(".cmp-side")
    expect(sides).to_have_count(2, timeout=120000)
    expect(desktop.locator("#compare")).to_be_in_viewport()
    for i in range(2):
        steps = sides.nth(i).locator(".cmp-steps > li h3")
        assert steps.all_inner_texts() == ["What the parent sees", "What the backend found", "What it lets the parent do",
                                           "If the parent tries anyway", "What works on the camera now"]
    # The two cases differ in what the backend decided, and codes stay behind the engineering detail.
    texts = [sides.nth(i).locator(".cmp-steps").inner_text() for i in range(2)]
    assert texts[0] != texts[1]
    assert "_" not in texts[0] + texts[1], "status codes leaked into the parent-facing steps"
    expect(sides.first.locator("details.cmp-eng")).not_to_have_attribute("open", "")
