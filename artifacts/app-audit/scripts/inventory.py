"""1) Enumerate visible interactive controls per screen at 375 px and 1280 px,
with sizes, plus horizontal overflow, console errors and broken links."""
import httpx
from playwright.sync_api import sync_playwright

from common import BASE, SHOTS, Recorder, dump, reset

CONTROLS_JS = """
() => {
  const sel = 'a[href], button, summary, input, select, textarea, [role=button], [role=radio], [tabindex]:not([tabindex="-1"])';
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (el.closest('[aria-hidden=true]')) continue;
    // skip controls inside a closed <details> (not visible)
    const d = el.closest('details'); if (d && !d.open && el.tagName !== 'SUMMARY') continue;
    const name = (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('title') || '').trim().replace(/\\s+/g, ' ').slice(0, 70);
    const region = el.closest('#xray') ? 'xray' : el.closest('.dev-panel') ? 'dev-panel' : el.closest('#side-nav') ? 'side-nav'
      : el.closest('#tab-bar') ? 'tab-bar' : el.closest('header') ? 'header' : el.closest('footer') ? 'footer'
      : el.closest('#present-bar') ? 'present-bar' : el.closest('dialog') ? 'sheet' : 'main';
    out.push({ tag: el.tagName.toLowerCase(), role: el.getAttribute('role'), name, region,
      action: el.dataset.action || el.dataset.present || null, href: el.getAttribute('href'),
      w: Math.round(r.width), h: Math.round(r.height) });
  }
  return out;
}"""
OVERFLOW_JS = """() => ({ scrollW: document.documentElement.scrollWidth, innerW: innerWidth,
  wide: [...document.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect();
    return r.right > innerWidth + 1 && getComputedStyle(e).position !== 'fixed' && !e.closest('.xray, .table-scroll, .xray-scroll'); })
    .slice(0, 5).map(e => e.tagName + '.' + e.className) })"""


def main():
    reset()
    rec = Recorder()
    screens = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height in ((375, 812), (1280, 800)):
            ctx = browser.new_context(viewport={"width": width, "height": height},
                                      is_mobile=width < 600, has_touch=width < 600)
            page = ctx.new_page()
            label = f"w{width}"
            rec.attach(page, label)

            def snap(key, shot=False):
                page.wait_for_timeout(400)
                screens[f"{label}:{key}"] = {"controls": page.evaluate(CONTROLS_JS), "overflow": page.evaluate(OVERFLOW_JS)}
                if shot:
                    page.screenshot(path=str(SHOTS / f"{label}-{key}.png"), full_page=False)

            page.goto(f"{BASE}/#/briefing"); page.wait_for_selector(".rev-badge")
            snap("briefing", shot=True)
            page.goto(f"{BASE}/#/changes"); page.wait_for_selector(".member-grid")
            snap("changes")
            page.goto(f"{BASE}/#/features"); page.wait_for_selector(".camera-picker")
            page.get_by_role("button", name="Check availability").click(); page.wait_for_selector(".offer-card")
            snap("features-offer", shot=width == 375)
            # Caregiver B on features
            page.locator("#viewer-switch button", has_text="Caregiver B").click()
            snap("features-caregiver")
            page.locator("#viewer-switch button", has_text="Parent A").click()
            # Correction sheet
            page.goto(f"{BASE}/#/briefing"); page.wait_for_selector(".rev-badge")
            page.get_by_role("button", name="Correct final wake").click()
            page.wait_for_selector("#correction-sheet[open]")
            snap("correction-sheet", shot=width == 375)
            page.keyboard.press("Escape")
            # X-Ray live
            nav = ".tab-bar" if width < 1024 else "#side-nav"
            page.locator(f"{nav} [data-action=xray-open]").first.click()
            page.wait_for_selector("#xray.open table")
            if width < 1024:
                page.locator("#xray .dev-panel summary").click()
            snap("xray-live", shot=True)
            # Data & proof tabs
            page.locator("#xray [data-action=xray-tab][data-tab=proof]").click()
            page.wait_for_selector(".proof-nav")
            for tab in ("flows", "sources", "availability", "schema", "stress", "reliability", "tests", "unknowns"):
                page.locator(f"#xray [data-action=proof-tab][data-tab={tab}]").click()
                page.wait_for_selector(f"#xray [data-tab={tab}][aria-checked=true]")
                snap(f"proof-{tab}", shot=(width == 1280 and tab in ("tests", "reliability", "schema")))
            page.keyboard.press("Escape")
            # Dev panel (desktop side nav) + two screens
            if width >= 1024:
                page.locator("#side-nav .dev-panel summary").click()
                page.locator("#side-nav [data-action=dev-two]").click()
                page.wait_for_selector(".screens.two")
                snap("two-screens", shot=True)
                page.locator("#side-nav [data-action=dev-two]").click()
            # Static pages
            page.goto(f"{BASE}/privacy"); snap("privacy")
            resp = page.goto(f"{BASE}/no-such-page"); snap("404", shot=width == 375)
            screens[f"{label}:404"]["status"] = resp.status
            ctx.close()
        browser.close()

    # Links: every href seen anywhere
    links = sorted({c["href"] for s in screens.values() for c in s["controls"] if c["href"]})
    link_status = {}
    for href in links:
        if href.startswith("#"):
            link_status[href] = "in-page route"
            continue
        url = href if href.startswith("http") else BASE + href
        try:
            link_status[href] = httpx.get(url, timeout=15, follow_redirects=True).status_code
        except Exception as e:  # noqa: BLE001
            link_status[href] = f"error: {type(e).__name__}"
    link_status["/v1/does-not-exist (json)"] = httpx.get(BASE + "/v1/does-not-exist").json()

    small = [{"screen": k, **c} for k, s in screens.items() for c in s["controls"] if c["h"] < 44 or c["w"] < 44]
    dump("inventory.json", {"screens": screens, "links": link_status, "small_targets": small,
                            "console": rec.console, "pageerrors": rec.pageerrors, "failed": rec.failed,
                            "http_errors": [r for r in rec.requests if r["status"] >= 400]})
    print("screens", len(screens), "controls", sum(len(s["controls"]) for s in screens.values()))
    print("overflow", {k: s["overflow"] for k, s in screens.items() if s["overflow"]["scrollW"] > s["overflow"]["innerW"] or s["overflow"]["wide"]})
    print("links", link_status)
    print("console", rec.console[:10], "pageerrors", rec.pageerrors[:5], "failed", rec.failed[:5])
    print("http>=400", [r for r in rec.requests if r["status"] >= 400][:10])


if __name__ == "__main__":
    main()
