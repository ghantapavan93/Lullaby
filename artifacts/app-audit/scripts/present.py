"""4) Present Mode: each audience end to end (fast), captions vs database state,
Pause/Next/Exit controls, Escape, and exit mid-way (undo)."""
import json
import time

from playwright.sync_api import sync_playwright

from common import BASE, PROVIDER_DB, Recorder, SHOTS, dump, q, reset

R = {}
rec = Recorder()


def captions(page, limit_s):
    seen, t0 = [], time.time()
    while time.time() - t0 < limit_s:
        if page.locator("#present-bar").count() == 0:
            break
        try:
            item = page.locator("#present-bar .present-count").inner_text() + " | " + page.locator("#present-bar .present-title").inner_text() \
                + " | " + page.locator("#present-bar .present-line").inner_text()
        except Exception:  # noqa: BLE001
            break
        if not seen or seen[-1] != item:
            seen.append(item)
        page.wait_for_timeout(150)
    return seen, round(time.time() - t0, 1)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for audience in ("recruiter", "product", "engineering"):
            reset()
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page(); rec.attach(page, audience)
            page.goto(f"{BASE}/?present={audience}&fast=1#/briefing")
            page.wait_for_selector("#present-bar")
            seen, secs = captions(page, 120)
            R[audience] = {"captions": seen, "seconds": secs,
                           "db_night_rev": q("""SELECT w.current_revision FROM briefing_windows w WHERE w.local_date='2026-10-09'"""),
                           "db_intents": q("SELECT state FROM purchase_intents"),
                           "db_ack_b": q("SELECT revision FROM ack_heads WHERE member_id='mem_caregiver_b'"),
                           "provider": q("SELECT status, attempts FROM provider_operations", db=PROVIDER_DB),
                           "mount": q("SELECT mount FROM devices WHERE id='dev_cam_demo'"),
                           "failed_steps": [l for l in page.locator("#side-nav .dev-log").inner_text().split("\n") if "present:" in l]}
            print(audience, json.dumps(R[audience], default=str)[:1500])
            ctx.close()

        # Exit during the stale-offer step: does undo restore the mount?
        reset()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page(); rec.attach(page, "exit")
        page.goto(f"{BASE}/?present=recruiter#/briefing")       # normal speed
        page.wait_for_selector("#present-bar")
        page.locator("#present-bar [data-present=pause]").click()
        paused = page.locator("#present-bar [data-present=pause]").inner_text()
        page.locator("#present-bar [data-present=pause]").click()
        for _ in range(4):
            page.locator("#present-bar [data-present=next]").click(); page.wait_for_timeout(2500)
        page.wait_for_function("document.querySelector('#present-bar .present-title')?.textContent.includes('camera moves')", timeout=40000)
        page.wait_for_function("!document.querySelector('#present-bar').classList.contains('waiting')", timeout=20000)
        mount_mid = q("SELECT mount FROM devices WHERE id='dev_cam_demo'")
        page.keyboard.press("Escape"); page.wait_for_timeout(2500)
        R["exit_mid_stale_offer"] = {"pause_label": paused, "mount_during": mount_mid,
                                     "mount_after_exit": q("SELECT mount FROM devices WHERE id='dev_cam_demo'"),
                                     "bar_gone": page.locator("#present-bar").count() == 0}
        # Header Present button
        page.locator("header [data-present='start:recruiter']").click()
        page.wait_for_selector("#present-bar"); started = page.locator("#present-bar .present-count").inner_text()
        page.locator("#present-bar [data-present=exit]").click(); page.wait_for_timeout(2500)
        R["header_present_button"] = {"started": started, "exited": page.locator("#present-bar").count() == 0}
        page.screenshot(path=str(SHOTS / "w1280-after-present-exit.png"))
        print(json.dumps({k: R[k] for k in ("exit_mid_stale_offer", "header_present_button")}, default=str))
        ctx.close()
        browser.close()
    dump("present.json", {"results": R, "console": rec.console, "pageerrors": rec.pageerrors,
                          "http_errors": [(r["page"], r["method"], r["url"], r["status"]) for r in rec.requests if r["status"] >= 400]})
    print("pageerrors", rec.pageerrors, "http_errors", [(r["page"], r["url"], r["status"]) for r in rec.requests if r["status"] >= 400])


if __name__ == "__main__":
    main()
