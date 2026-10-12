"""2) Drive every product and scenario control, capture the endpoint each one
calls, check the database effect, reload persistence and what a second client
(the other caregiver, in a separate browser context) sees."""
import json
import time
import traceback

import httpx
from playwright.sync_api import sync_playwright

from common import BASE, PROVIDER_DB, SHOTS, Recorder, calls, dump, q, reset, token

R = {}          # control -> evidence
rec = Recorder()


def night_rev(date="2026-10-09"):
    return q("""SELECT max(r.revision) FROM briefing_revisions r JOIN briefing_windows w ON w.id = r.window_id
                WHERE w.local_date = %s""", (date,))[0][0]


def run(name, fn):
    t0 = time.time()
    try:
        R[name] = {"ok": True, **(fn() or {})}
    except Exception as e:  # noqa: BLE001
        R[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:400], "tb": traceback.format_exc()[-600:]}
    R[name]["calls"] = calls(rec.since(t0))
    print(name, json.dumps({k: v for k, v in R[name].items() if k != "tb"}, default=str)[:700])


def open_dev(page):
    if not page.locator("#side-nav details.dev-panel").get_attribute("open") is not None:
        pass
    if page.locator("#side-nav details.dev-panel[open]").count() == 0:
        page.locator("#side-nav details.dev-panel summary").click()


def dev(page, action, **attrs):
    open_dev(page)
    sel = f"#side-nav [data-action={action}]" + "".join(f'[data-{k}="{v}"]' for k, v in attrs.items())
    page.locator(sel).click()


def last_log(page):
    return page.locator("#side-nav .dev-log").inner_text().split("\n")[0]


def boot(page, route="briefing"):
    page.goto(f"{BASE}/#/{route}")
    page.wait_for_selector(".rev-badge" if route == "briefing" else "main h1")


def main():
    reset()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctxA = browser.new_context(viewport={"width": 1280, "height": 900})
        ctxB = browser.new_context(viewport={"width": 1280, "height": 900})
        A, B = ctxA.new_page(), ctxB.new_page()
        rec.attach(A, "A"); rec.attach(B, "B")
        boot(A); boot(B)

        def f_reset():
            dev(A, "dev-reset")
            A.wait_for_function("document.querySelector('#side-nav .dev-log')?.textContent.includes('demo reset')")
            return {"db_rev": night_rev(), "badge": A.locator(".rev-badge").inner_text(), "log": last_log(A)}
        run("dev-reset", f_reset)
        boot(B)

        def f_viewer():
            B.locator("#viewer-switch button", has_text="Caregiver B").click()
            checked = B.locator("#viewer-switch [aria-checked=true]").inner_text()
            B.reload(); B.wait_for_selector(".rev-badge")
            after = B.locator("#viewer-switch [aria-checked=true]").inner_text()
            B.locator("#viewer-switch button", has_text="Caregiver B").click()
            return {"checked": checked, "after_reload": after, "correct_btn_for_B": B.get_by_role("button", name="Correct final wake").count()}
        run("viewer-switch", f_viewer)

        def f_correct():
            A.get_by_role("button", name="Correct final wake").click()
            A.wait_for_selector("#correction-sheet[open]")
            A.fill("#wake-time", "06:35")
            A.get_by_role("button", name="Save correction").click()
            A.wait_for_selector("#toast.show")
            toast = A.locator("#toast").inner_text()
            badge = A.locator(".rev-badge").first.inner_text()
            was = A.locator(".was").all_inner_texts()
            A.reload(); A.wait_for_selector(".rev-badge")
            return {"toast": toast, "badge": badge, "was": was, "db_rev": night_rev(),
                    "badge_after_reload": A.locator(".rev-badge").inner_text(),
                    "notifications": q("SELECT member_id, revision FROM notifications ORDER BY id DESC LIMIT 2")}
        run("correct-final-wake (sheet)", f_correct)

        def f_other_client_sees():
            B.wait_for_selector(".stale-banner", timeout=9000)
            return {"B_banner": B.locator(".stale-banner .alert-title").inner_text(),
                    "B_badge": B.locator(".rev-badge").inner_text(),
                    "B_changes_badge": B.locator("#side-nav .badge").all_inner_texts()}
        run("second-client stale banner (poll)", f_other_client_sees)

        def f_ack_stale():
            B.get_by_role("button", name="Acknowledge revision 4").click()
            B.wait_for_selector(".ack-card.stale")
            head = q("SELECT revision FROM ack_heads WHERE member_id='mem_caregiver_b'")
            return {"B_card": B.locator(".ack-card.stale .ack-title").inner_text(), "db_ack_head": head}
        run("acknowledge (stale rev 4, Caregiver B)", f_ack_stale)

        def f_changes_other():
            A.goto(f"{BASE}/#/changes"); A.wait_for_selector(".member-grid")
            A.wait_for_function("document.querySelector('.member-grid').innerText.includes('Acknowledged revision 4')", timeout=9000)
            return {"A_sees": [t.split("\n")[1] for t in A.locator(".member-card").all_inner_texts()],
                    "lede": A.locator(".lede").inner_text()}
        run("changes route: other client sees B's ack", f_changes_other)

        def f_review_latest():
            B.get_by_role("button", name="Review revision 5").click()
            B.wait_for_selector(".rev-badge:not(.superseded)")
            changed = B.locator(".tile.changed .tile-label").all_inner_texts()
            B.get_by_role("button", name="Acknowledge revision 5").click()
            B.wait_for_selector("text=You acknowledged revision 5")
            return {"changed_tiles": changed, "db_ack_head": q("SELECT revision FROM ack_heads WHERE member_id='mem_caregiver_b'")}
        run("review latest + ack rev 5", f_review_latest)

        def f_show_list():
            A.goto(f"{BASE}/#/briefing"); A.wait_for_selector(".rev-badge")
            A.locator("details.list-toggle summary").click()
            rows = A.locator("details.list-toggle tbody tr").count()
            return {"rows": rows}
        run("show as list", f_show_list)

        def f_conflict():
            A.get_by_role("button", name="Correct final wake").click()
            A.wait_for_selector("#correction-sheet[open]")
            # Another client (Parent A on another device) corrects first.
            t = token()
            hh = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {t}"})
            me = hh.get("/v1/me").json()
            wid = next(w["window_id"] for w in me["windows"] if w["local_date"] == "2026-10-09")
            brief = hh.get(f"/v1/windows/{wid}/brief").json()
            last = sorted(brief["facts"]["recorded_sleep"]["intervals"], key=lambda i: i["end"])[-1]
            r = hh.post(f"/v1/windows/{wid}/corrections", json={"target_event_id": last["event_id"],
                        "corrected_end": "2026-10-10T06:40:00-04:00", "expected_revision": brief["revision"],
                        "client_event_id": "audit-other-device-1"})
            rev_before = night_rev()
            A.fill("#wake-time", "06:20")
            t1 = time.time()
            A.get_by_role("button", name="Save correction").click()
            A.wait_for_selector("#correction-error .alert")
            err = A.locator("#correction-error").inner_text()
            first = calls(rec.since(t1, "A"))
            # Second press on Save after the error.
            t2 = time.time()
            A.get_by_role("button", name="Save correction").click()
            A.wait_for_timeout(1500)
            second = [c for c in calls(rec.since(t2, "A")) if "corrections" in c]
            return {"other_device": r.status_code, "inline_error": err, "first_submit": [c for c in first if "corrections" in c],
                    "second_submit_requests": second, "sheet_open_after_second": A.locator("#correction-sheet[open]").count() == 1,
                    "toast_visible": A.locator("#toast.show").count(), "db_rev": night_rev(), "rev_before": rev_before}
        run("correction conflict + retry Save", f_conflict)
        A.keyboard.press("Escape")

        # ---------------- features
        def f_decide():
            A.goto(f"{BASE}/#/features"); A.wait_for_selector(".camera-picker")
            A.get_by_role("button", name="Check availability").click()
            A.wait_for_selector(".offer-card")
            A.wait_for_timeout(800)
            d = q("SELECT id, status, offer IS NOT NULL FROM capability_decisions ORDER BY created_at DESC LIMIT 1")
            rr = q("SELECT surface, variant FROM render_receipts WHERE surface='feature_availability'")
            return {"alert": A.locator(".blocker-card .alert-title").inner_text(), "db_decision": d, "render_receipts": rr}
        run("check availability (offer)", f_decide)

        def f_stale_buy():
            dev(A, "dev-mount", mount="flex_stand")
            A.wait_for_function("document.querySelector('#side-nav .dev-log').textContent.includes('flex_stand')")
            A.get_by_role("button", name="Start simulated upgrade").click()
            A.wait_for_selector("text=We didn't submit this offer")
            note = A.locator(".alert-warning").first.inner_text()
            intents = q("SELECT count(*) FROM purchase_intents")[0][0]
            ops = q("SELECT count(*) FROM provider_operations", db=PROVIDER_DB)[0][0]
            A.get_by_role("button", name="Check eligibility again").click()
            A.wait_for_selector(".blocker-card.hardware")
            return {"note": note, "db_intents": intents, "provider_ops": ops,
                    "recheck": A.locator(".blocker-card .alert-title").inner_text(), "offer_cards": A.locator(".offer-card").count(),
                    "success_anim": A.locator(".pill-CONFIRMED").count()}
        run("stale buy after camera moved", f_stale_buy)

        def f_buy_ok():
            dev(A, "dev-mount", mount="floor_stand")
            A.wait_for_function("document.querySelector('#side-nav .dev-log').textContent.split('\\n')[0].includes('floor_stand')")
            A.get_by_role("button", name="Check availability").click(); A.wait_for_selector(".offer-card")
            A.get_by_role("button", name="Start simulated upgrade").click()
            A.wait_for_selector(".pill-CONFIRMED", timeout=30000)
            intent = q("SELECT state, device_id FROM purchase_intents ORDER BY created_at DESC LIMIT 1")
            ops = q("SELECT status, attempts FROM provider_operations", db=PROVIDER_DB)
            cam = A.locator(".camera-picker [aria-checked=true] .camera-meta").first.inner_text()
            A.reload(); A.wait_for_selector(".camera-picker")
            return {"pill": A.locator(".pill").all_inner_texts() if A.locator(".pill").count() else None, "db_intent": intent, "provider": ops,
                    "camera_plan_line": cam, "after_reload_panel": A.locator("[role=status] .pill").all_inner_texts(),
                    "after_reload_plan": A.locator(".camera-picker [aria-checked=true] .camera-meta").first.inner_text()}
        run("buy (confirmed) + reload", f_buy_ok)

        def f_caregiver_features():
            B.goto(f"{BASE}/#/features"); B.wait_for_selector("main h1")
            return {"B": B.locator("main .alert-title").inner_text()}
        run("caregiver B on features", f_caregiver_features)

        def f_camera_pick():
            A.goto(f"{BASE}/#/features"); A.wait_for_selector(".camera-picker")
            A.locator(".camera-picker button", has_text="Playroom").click()
            A.wait_for_selector("text=What each plan would unlock on the playroom camera")
            A.get_by_role("button", name="Check availability").click()
            A.wait_for_selector(".blocker-card")
            res = {"alert": A.locator(".blocker-card .alert-title").inner_text(), "offers": A.locator(".offer-card").count()}
            A.reload(); A.wait_for_selector(".camera-picker")
            res["camera_after_reload"] = A.locator(".camera-picker [aria-checked=true] .camera-name").inner_text()
            return res
        run("camera picker (Playroom) + reload", f_camera_pick)

        def f_feature_pick():
            out = {}
            for f in ("Sleep analysis", "NextNap", "7-day video history", "Developmental milestones"):
                A.locator(".feature-picker button", has_text=f).click()
                A.get_by_role("button", name="Check availability").click()
                A.wait_for_selector(".blocker-card h2")
                out[f] = A.locator(".blocker-card .alert-title").inner_text()
            return out
        run("feature picker x4", f_feature_pick)

        # ---------------- recovery scenarios
        def fresh_offer():
            dev(A, "dev-reset")
            A.wait_for_function("document.querySelector('#side-nav .dev-log').textContent.split('\\n')[0].includes('demo reset')")
            A.goto(f"{BASE}/#/features"); A.wait_for_selector(".camera-picker")
            A.locator(".feature-picker button", has_text="Developmental milestones").click()
            A.get_by_role("button", name="Check availability").click(); A.wait_for_selector(".offer-card")

        def f_crash():
            fresh_offer()
            dev(A, "dev-crash")
            A.wait_for_selector(".pill-SUBMITTED", timeout=30000)
            mid = {"pill": A.locator(".pill").inner_text(), "db": q("SELECT state FROM purchase_intents"),
                   "provider": q("SELECT status FROM provider_operations", db=PROVIDER_DB)}
            A.wait_for_timeout(3500)
            dev(A, "dev-worker")
            try:
                A.wait_for_selector(".pill-CONFIRMED", timeout=8000)
            except Exception:  # lease may not have expired; documented: click again
                dev(A, "dev-worker"); A.wait_for_selector(".pill-CONFIRMED", timeout=8000)
            tr = q("SELECT from_state, to_state, reason FROM intent_transitions ORDER BY id")
            return {"mid": mid, "end": A.locator(".pill").inner_text(), "transitions": tr,
                    "provider_ops": q("SELECT count(*) FROM provider_operations", db=PROVIDER_DB)[0][0]}
        run("crash after provider + restart worker", f_crash)

        def f_concurrent():
            fresh_offer()
            dev(A, "dev-concurrent")
            A.wait_for_selector(".pill-CONFIRMED", timeout=30000)
            return {"log": A.locator("#side-nav .dev-log").inner_text().split("\n")[:4], "db_intents": q("SELECT count(*) FROM purchase_intents")[0][0]}
        run("2 simultaneous requests (same key)", f_concurrent)

        def f_decline():
            fresh_offer()
            dev(A, "dev-provider", behavior="decline")
            A.get_by_role("button", name="Start simulated upgrade").click()
            A.wait_for_selector(".pill-FAILED", timeout=30000)
            return {"pill": A.locator(".pill").inner_text(), "confirmed_shown": A.locator(".pill-CONFIRMED").count(),
                    "db": q("SELECT state FROM purchase_intents"), "offer_again": A.locator(".offer-card").count()}
        run("provider decline", f_decline)

        def f_pending():
            fresh_offer()
            dev(A, "dev-provider", behavior="pending")
            A.get_by_role("button", name="Start simulated upgrade").click()
            A.wait_for_selector(".pill-UNKNOWN", timeout=30000)
            mid = A.locator(".pill").inner_text()
            dev(A, "dev-resolve", status="SUCCEEDED")
            A.wait_for_timeout(500)
            still = A.locator(".pill").inner_text()
            dev(A, "dev-worker")
            A.wait_for_selector(".pill-CONFIRMED", timeout=15000)
            return {"mid": mid, "after_resolve_before_reconcile": still, "end": A.locator(".pill").inner_text(),
                    "db": q("SELECT state FROM purchase_intents")}
        run("provider pending -> resolve -> reconcile", f_pending)

        def f_pending_failed():
            fresh_offer()
            dev(A, "dev-provider", behavior="pending")
            A.get_by_role("button", name="Start simulated upgrade").click()
            A.wait_for_selector(".pill-UNKNOWN", timeout=30000)
            dev(A, "dev-resolve", status="FAILED"); A.wait_for_timeout(300)
            dev(A, "dev-worker")
            A.wait_for_selector(".pill-FAILED", timeout=15000)
            return {"end": A.locator(".pill").inner_text(), "db": q("SELECT state FROM purchase_intents")}
        run("provider pending -> settles failed", f_pending_failed)

        def f_lost():
            fresh_offer()
            dev(A, "dev-provider", behavior="lose_response")
            A.get_by_role("button", name="Start simulated upgrade").click()
            A.wait_for_timeout(4000)
            mid = A.locator(".pill").inner_text()
            dev(A, "dev-worker"); A.wait_for_timeout(3000)
            if A.locator(".pill-CONFIRMED").count() == 0:
                A.wait_for_timeout(2000); dev(A, "dev-worker"); A.wait_for_timeout(3000)
            return {"mid": mid, "end": A.locator(".pill").inner_text(), "db": q("SELECT state FROM purchase_intents"),
                    "provider": q("SELECT status, attempts FROM provider_operations", db=PROVIDER_DB)}
        run("provider response lost", f_lost)

        def f_resolve_without_purchase():
            dev(A, "dev-reset")
            A.wait_for_function("document.querySelector('#side-nav .dev-log').textContent.split('\\n')[0].includes('demo reset')")
            dev(A, "dev-resolve", status="SUCCEEDED")
            w = last_log(A)
            dev(A, "dev-crash"); c = last_log(A)
            dev(A, "dev-concurrent"); cc = last_log(A)
            return {"resolve": w, "crash": c, "concurrent": cc}
        run("dev controls without preconditions", f_resolve_without_purchase)

        # ---------------- channel claims
        def f_claims():
            out = {}
            A.goto(f"{BASE}/#/features"); A.wait_for_selector(".camera-picker")
            for label in ("Apple purchase pending (Milestones)",):
                open_dev(A); A.locator("#side-nav button", has_text=label).click(); A.wait_for_timeout(700); out[label] = last_log(A)
            A.get_by_role("button", name="Check availability").click(); A.wait_for_selector(".blocker-card")
            out["decide_while_pending"] = A.locator(".blocker-card .alert").inner_text()[:160]
            out["offer_while_pending"] = A.locator(".offer-card").count()
            for label in ("Apple confirms it late", "same Google event delivered twice", "activation code on Nursery",
                          "same code on Playroom", "refund the Apple purchase"):
                A.locator("#side-nav button", has_text=label).click(); A.wait_for_timeout(900)
                out[label] = A.locator("#side-nav .dev-log").inner_text().split("\n")[:2 if "twice" in label else 1]
            out["db_claims"] = q("SELECT channel, external_ref, event_status, device_id FROM entitlement_claims ORDER BY received_at")
            out["nursery_line"] = A.locator(".camera-picker button").first.inner_text().replace("\n", " | ")
            return out
        run("channel claims (6 buttons)", f_claims)

        def f_past():
            A.goto(f"{BASE}/#/briefing"); A.wait_for_selector(".rev-badge")
            before = A.locator(".week-night").all_inner_texts()
            dev(A, "dev-past"); A.wait_for_timeout(1200)
            after = A.locator(".week-night.corrected").all_inner_texts()
            A.reload(); A.wait_for_selector(".week")
            return {"log": last_log(A) if A.locator('#side-nav details.dev-panel[open]').count() else None,
                    "edited_after": after, "edited_after_reload": A.locator(".week-night.corrected").count(),
                    "db_oct8_rev": night_rev("2026-10-08"), "avg": A.locator(".week .timeline-head .muted").inner_text()}
        run("past-night correction + week", f_past)

        def f_dev_correct_ack():
            dev(A, "dev-two"); A.wait_for_selector(".screens.two")
            dev(A, "dev-correct"); A.wait_for_timeout(1500)
            dev(A, "dev-ack-b"); A.wait_for_timeout(1200)
            frames = A.locator(".screen-frame .rev-badge").all_inner_texts()
            st = A.locator(".screen-frame").nth(1).locator(".ack-card").inner_text()[:140]
            A.reload(); A.wait_for_selector(".rev-badge")
            return {"frames": frames, "B_ack": st, "two_screens_after_reload": A.locator(".screens.two").count(), "db_rev": night_rev()}
        run("dev: two screens, correct, B ack", f_dev_correct_ack)

        def f_theme():
            A.locator("#theme-toggle").click()
            t = A.evaluate("document.documentElement.dataset.theme")
            ls = A.evaluate("localStorage.getItem('pl-theme')")
            A.reload(); A.wait_for_selector(".rev-badge")
            return {"theme": t, "localStorage": ls, "after_reload": A.evaluate("document.documentElement.dataset.theme"),
                    "B_theme": B.evaluate("document.documentElement.dataset.theme || null")}
        run("theme toggle", f_theme)

        def f_reset_other_client():
            # B has an open screen; A resets the demo (window ids change).
            B.goto(f"{BASE}/#/briefing"); B.wait_for_selector(".rev-badge")
            B.locator("#viewer-switch button", has_text="Caregiver B").click()
            before = B.locator(".rev-badge").inner_text()
            dev(A, "dev-reset"); A.wait_for_timeout(1500)
            t0 = time.time()
            B.wait_for_timeout(9000)
            polls = [c for c in calls(rec.since(t0, "B"), skip_poll=False) if " 4" in c[-4:] or " 5" in c[-4:]]
            badge = B.locator(".rev-badge").inner_text()
            btn = B.get_by_role("button", name="Acknowledge revision")
            res = {"B_before": before, "B_after_9s": badge, "B_poll_errors": polls[:6], "stale_indicator": B.locator(".stale-banner").count()}
            if btn.count():
                t1 = time.time(); btn.first.click(); B.wait_for_timeout(1500)
                res["ack_after_reset"] = calls(rec.since(t1, "B"))
                res["toast"] = B.locator("#toast").inner_text()
            return res
        run("other client after reset (stale window)", f_reset_other_client)

        A.screenshot(path=str(SHOTS / "w1280-after-flows.png"))
        browser.close()
    dump("flows.json", {"results": R, "console": rec.console, "pageerrors": rec.pageerrors,
                        "http_errors": [r for r in rec.requests if r["status"] >= 400]})
    print("console", rec.console[:8]); print("pageerrors", rec.pageerrors[:5])
    print("http>=400", [(r["method"], r["url"], r["status"]) for r in rec.requests if r["status"] >= 400][:20])


if __name__ == "__main__":
    main()
