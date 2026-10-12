"""3) Keyboard, focus, poll side effects, X-Ray proof explorer content, error
and offline states."""
import json
import time

from playwright.sync_api import sync_playwright

from common import BASE, SHOTS, Recorder, calls, dump, q, reset

R = {}
rec = Recorder()


def run(name, fn):
    try:
        R[name] = fn()
    except Exception as e:  # noqa: BLE001
        R[name] = {"error": f"{type(e).__name__}: {e}"[:400]}
    print(name, json.dumps(R[name], default=str)[:900])


ACTIVE = """() => { const a = document.activeElement; if (!a || a === document.body) return 'BODY';
  const cs = getComputedStyle(a); return (a.id ? '#' + a.id : a.tagName.toLowerCase()) + ' | ' + (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 40)
  + ' | outline=' + cs.outlineStyle + ' ' + cs.outlineWidth + (a.closest('#xray') ? ' | in-xray' : '') }"""


def main():
    reset()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page(); rec.attach(page, "D")
        page.goto(f"{BASE}/#/briefing"); page.wait_for_selector(".rev-badge")

        def tab_order():
            seq = []
            page.locator("body").click(position={"x": 5, "y": 200})
            for _ in range(30):
                page.keyboard.press("Tab"); seq.append(page.evaluate(ACTIVE))
            return {"first30": seq, "no_visible_outline": [s for s in seq if "outline=none" in s]}
        run("tab order 1280", tab_order)

        def poll_steals_focus():
            page.locator("#side-nav a", has_text="What changed").focus()
            before = page.evaluate(ACTIVE)
            page.wait_for_timeout(4600)
            return {"before": before, "after_poll": page.evaluate(ACTIVE)}
        run("poll re-render vs keyboard focus (side nav)", poll_steals_focus)

        def viewer_focus():
            page.locator("#viewer-switch button", has_text="Parent A").focus()
            page.wait_for_timeout(4600)
            return {"after_poll": page.evaluate(ACTIVE)}
        run("poll re-render vs focus (viewer switch)", viewer_focus)

        def xray_keyboard():
            page.locator("#side-nav [data-action=xray-open]").focus()
            page.keyboard.press("Enter"); page.wait_for_selector("#xray.open table")
            opened = page.evaluate(ACTIVE)
            page.locator("#xray [data-action=xray-tab][data-tab=proof]").click(); page.wait_for_selector(".proof-nav")
            page.locator("#xray [data-tab=sources]").click(); page.wait_for_selector("details.evidence")
            page.locator("#xray details.evidence summary").first.click()
            open_before = page.locator("#xray details.evidence[open]").count()
            page.locator("#xray details.evidence summary").first.focus()
            scroll_el = "document.querySelector('#xray-body')"
            page.evaluate(f"{scroll_el}.scrollTop = 200")
            st0 = page.evaluate(f"{scroll_el}.scrollTop")
            page.wait_for_timeout(4800)
            res = {"focus_on_open": opened, "details_open_before_poll": open_before,
                   "details_open_after_poll": page.locator("#xray details.evidence[open]").count(),
                   "focus_after_poll": page.evaluate(ACTIVE), "scrollTop_before": st0, "scrollTop_after": page.evaluate(f"{scroll_el}.scrollTop")}
            # Does keyboard Tab escape the drawer into the page behind?
            seq = []
            for _ in range(60):
                page.keyboard.press("Tab"); seq.append(page.evaluate(ACTIVE))
            res["tab_leaves_drawer"] = any("in-xray" not in s for s in seq)
            page.keyboard.press("Escape")
            res["closed_by_escape"] = page.locator("#xray.open").count() == 0
            res["focus_after_close"] = page.evaluate(ACTIVE)
            return res
        run("x-ray keyboard + poll", xray_keyboard)

        def proof_content():
            page.locator("#side-nav [data-action=xray-open]").click(); page.wait_for_selector("#xray.open")
            page.locator("#xray [data-action=xray-tab][data-tab=proof]").click(); page.wait_for_selector(".proof-nav")
            out = {}
            for tab in ("tests", "reliability", "schema", "stress", "availability", "flows"):
                page.locator(f"#xray [data-tab={tab}]").click()
                page.wait_for_selector(f"#xray [data-tab={tab}][aria-checked=true]")
                out[tab] = page.locator("#xray .proof-view").inner_text()[:1400]
            page.locator("#xray [data-tab=reliability]").click()
            out["reliability_broken_rows"] = page.locator("#xray .proof-view tr", has=page.locator(".no")).all_inner_texts()
            page.screenshot(path=str(SHOTS / "w1280-proof-reliability.png"))
            page.locator("#xray [data-tab=tests]").click()
            page.screenshot(path=str(SHOTS / "w1280-proof-tests.png"))
            page.locator("#xray [data-tab=schema]").click()
            out["schema_zero_rows"] = page.locator("#xray .proof-view tbody tr td:nth-child(2)").all_inner_texts()
            out["actual_rows"] = {t: q(f"SELECT count(*) FROM {t}")[0][0] for t in ("evidence_events", "briefing_revisions", "members", "devices")}
            # Live trace while the proof tab is active: switching viewer re-renders the drawer
            page.locator("#xray-close").click()
            return out
        run("proof explorer content", proof_content)

        def correction_latency():
            page.goto(f"{BASE}/#/briefing"); page.wait_for_selector(".rev-badge")
            page.get_by_role("button", name="Correct final wake").click()
            t0 = time.time()
            page.get_by_role("button", name="Save correction").click()
            page.wait_for_selector("#toast.show"); t_toast = time.time()
            badge_at_toast = page.locator(".rev-badge").inner_text()
            page.wait_for_function("document.querySelector('.rev-badge').textContent.trim() === 'Rev 5'"); t_screen = time.time()
            return {"toast_ms": round((t_toast - t0) * 1000), "screen_rev5_ms": round((t_screen - t0) * 1000),
                    "badge_when_toast_shown": badge_at_toast,
                    "notifications": q("SELECT member_id, revision FROM notifications"),
                    "outbox": q("SELECT kind, state FROM outbox")}
        run("correction: toast vs screen update", correction_latency)

        def sheet_keyboard():
            page.get_by_role("button", name="Correct final wake").click()
            page.wait_for_selector("#correction-sheet[open]")
            f = page.evaluate(ACTIVE)
            page.keyboard.press("Escape")
            return {"focus_in_sheet": f, "closed_by_escape": page.locator("#correction-sheet[open]").count() == 0,
                    "focus_after": page.evaluate(ACTIVE)}
        run("correction sheet keyboard", sheet_keyboard)

        # ---------------- error and offline states
        def boot_error():
            p2 = ctx.new_page()
            p2.route("**/v1/me", lambda r: r.fulfill(status=500, body='{"code":"INTERNAL","message":"db down"}', content_type="application/json"))
            p2.goto(f"{BASE}/"); p2.wait_for_timeout(2500)
            txt = p2.locator("main").inner_text()
            p2.close()
            return {"main": txt[:200]}
        run("boot error (GET /v1/me 500)", boot_error)

        def decide_error():
            page.goto(f"{BASE}/#/features"); page.wait_for_selector(".camera-picker")
            page.route("**/v1/capability-decisions", lambda r: r.fulfill(status=500, body='{"code":"INTERNAL","message":"Something failed."}', content_type="application/json"))
            page.get_by_role("button", name="Check availability").click(); page.wait_for_timeout(800)
            t = page.locator("main .alert-error").inner_text() if page.locator("main .alert-error").count() else None
            page.unroute("**/v1/capability-decisions")
            return {"error_ui": t}
        run("decision error (500)", decide_error)

        def proof_error():
            page.route("**/v1/proof", lambda r: r.fulfill(status=500, body='{"code":"INTERNAL"}', content_type="application/json"))
            page.locator("#side-nav [data-action=xray-open]").click(); page.wait_for_selector("#xray.open")
            page.locator("#xray [data-action=xray-tab][data-tab=proof]").click(); page.wait_for_timeout(800)
            t = page.locator("#xray-body").inner_text()[:200]
            page.unroute("**/v1/proof"); page.keyboard.press("Escape")
            return {"xray": t}
        run("proof explorer error (500)", proof_error)

        def offline():
            page.goto(f"{BASE}/#/briefing"); page.wait_for_selector(".rev-badge")
            errs0 = len(rec.pageerrors)
            ctx.set_offline(True)
            page.wait_for_timeout(4500)
            btn = page.get_by_role("button", name="Acknowledge revision")
            clicked = False
            if btn.count():
                btn.first.click(); clicked = True
            page.wait_for_timeout(1200)
            toast = page.locator("#toast.show").inner_text() if page.locator("#toast.show").count() else None
            ctx.set_offline(False)
            return {"any_offline_indicator": page.locator("text=/offline|connection|couldn't reach/i").count(),
                    "ack_clicked": clicked, "toast": toast, "new_pageerrors": rec.pageerrors[errs0:]}
        run("offline: poll + acknowledge", offline)

        def mobile_drawer():
            m = browser.new_context(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
            mp = m.new_page(); rec.attach(mp, "M")
            mp.goto(f"{BASE}/#/briefing"); mp.wait_for_selector(".rev-badge")
            mp.locator(".tab-bar [data-action=xray-open]").click(); mp.wait_for_selector("#xray.open table")
            seq = []
            for _ in range(40):
                mp.keyboard.press("Tab"); seq.append(mp.evaluate(ACTIVE))
            close_box = mp.locator("#xray-close").bounding_box()
            mp.locator("#xray-scrim").click(position={"x": 5, "y": 300}, force=True) if mp.locator("#xray-scrim.open").count() else None
            res = {"tab_leaves_drawer": any("in-xray" not in s for s in seq), "leaves_to": [s for s in seq if "in-xray" not in s][:4],
                   "close_button_box": close_box, "closed_by_scrim": mp.locator("#xray.open").count() == 0}
            # sheet on mobile
            mp.get_by_role("button", name="Correct final wake").click(); mp.wait_for_selector("#correction-sheet[open]")
            mp.screenshot(path=str(SHOTS / "w375-sheet.png"))
            res["sheet_box"] = mp.locator("#correction-sheet").bounding_box()
            m.close()
            return res
        run("mobile drawer focus + close", mobile_drawer)
        browser.close()
    dump("ux.json", {"results": R, "console": rec.console, "pageerrors": rec.pageerrors,
                     "http_errors": [r for r in rec.requests if r["status"] >= 400]})
    print("pageerrors", rec.pageerrors)


if __name__ == "__main__":
    main()
