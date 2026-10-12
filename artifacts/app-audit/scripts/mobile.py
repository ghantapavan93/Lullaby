"""3b) Mobile (375 px) drawer, sheet, tab bar; focus return after closing the X-Ray."""
import json

from playwright.sync_api import sync_playwright

from common import BASE, SHOTS, dump, reset
from ux import ACTIVE

R = {}


def main():
    reset()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        m = browser.new_context(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
        mp = m.new_page()
        mp.goto(f"{BASE}/#/briefing"); mp.wait_for_selector(".rev-badge")
        mp.locator(".tab-bar [data-action=xray-open]").click(); mp.wait_for_selector("#xray.open table")
        seq = []
        for _ in range(40):
            mp.keyboard.press("Tab"); seq.append(mp.evaluate(ACTIVE))
        R["tab_leaves_drawer"] = [s for s in seq if "in-xray" not in s][:5]
        R["close_box"] = mp.locator("#xray-close").bounding_box()
        R["scrim_open"] = mp.locator("#xray-scrim.open").count()
        R["scrim_box"] = mp.locator("#xray-scrim").bounding_box()
        R["xray_box"] = mp.locator("#xray").bounding_box()
        R["hit_at_5_300"] = mp.evaluate("(() => { const e = document.elementFromPoint(5, 300); return e.id || e.className })()")
        mp.locator("#xray-close").click()
        R["closed_by_close_btn"] = mp.locator("#xray.open").count() == 0
        mp.wait_for_timeout(500)
        R["hit_after_close"] = mp.evaluate("(() => { document.querySelector('[data-action=correct-open]').scrollIntoView({block: 'center'}); const b = document.querySelector('[data-action=correct-open]').getBoundingClientRect(); const e = document.elementFromPoint(b.x + 10, b.y + 10); return e ? [e.tagName, e.className, e.id, Math.round(b.y)] : ['none', Math.round(b.y)] })()")
        try:
            mp.get_by_role("button", name="Correct final wake").click(timeout=5000)
            mp.wait_for_selector("#correction-sheet[open]")
            R["sheet_box"] = mp.locator("#correction-sheet").bounding_box()
            mp.screenshot(path=str(SHOTS / "w375-sheet.png"))
            mp.keyboard.press("Escape")
        except Exception as e:  # noqa: BLE001
            R["sheet_click_error"] = str(e).split("\n")[-3:]
            mp.screenshot(path=str(SHOTS / "w375-sheet-blocked.png"))
        # Keyboard open/close focus return (desktop)
        d = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        d.goto(f"{BASE}/#/briefing"); d.wait_for_selector(".rev-badge")
        d.locator("#side-nav [data-action=xray-open]").focus(); d.keyboard.press("Enter")
        d.wait_for_selector("#xray.open table")
        d.keyboard.press("Escape"); d.wait_for_timeout(300)
        R["desktop_focus_after_escape"] = d.evaluate(ACTIVE)
        d.locator("#side-nav [data-action=xray-open]").focus(); d.keyboard.press("Enter")
        d.wait_for_selector("#xray.open table"); d.locator("#xray-close").press("Enter"); d.wait_for_timeout(300)
        R["desktop_focus_after_close_btn"] = d.evaluate(ACTIVE)
        # Tab-bar X-Ray link with Space (role=button)
        mp.goto(f"{BASE}/#/briefing"); mp.wait_for_selector(".rev-badge")
        mp.locator(".tab-bar [data-action=xray-open]").focus(); mp.keyboard.press("Space"); mp.wait_for_timeout(600)
        R["tabbar_xray_space_opens"] = mp.locator("#xray.open").count() == 1
        browser.close()
    dump("mobile.json", R)
    print(json.dumps(R, indent=1, default=str))


if __name__ == "__main__":
    main()
