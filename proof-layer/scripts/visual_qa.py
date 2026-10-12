"""Visual QA: the same scenes at the same viewports, with measured layout.

    python scripts/visual_qa.py before      # against a running demo server
    python scripts/visual_qa.py after --base http://127.0.0.1:8710

For every viewport and scene this saves a screenshot and measures what the
eye sees: content width against the space available, the smallest text sizes
in the parent UI, horizontal overflow, small click targets, whether the X-Ray
drawer covers the content, and console errors. Results land in
artifacts/visual-qa/<phase>/ (screenshots + metrics.json + summary.md).

State is set through the real API and the real scenario controls; nothing is
faked in the page.
"""
from __future__ import annotations

import re

import argparse
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VIEWPORTS = [(1280, 800), (1366, 768), (1440, 900), (1536, 864), (1920, 1080), (390, 844), (768, 1024)]

MEASURE = r"""
() => {
  const vw = innerWidth, main = document.querySelector('main');
  const nav = document.querySelector('.side-nav');
  const navW = nav && getComputedStyle(nav).display !== 'none' ? nav.getBoundingClientRect().width : 0;
  const mr = main.getBoundingClientRect();
  // Width actually used by content inside main (widest direct child).
  const kids = [...main.children].map(c => c.getBoundingClientRect()).filter(r => r.width > 0);
  const contentW = kids.length ? Math.max(...kids.map(r => r.width)) : mr.width;
  const xray = document.querySelector('.xray.open');
  const xr = xray ? xray.getBoundingClientRect() : null;
  // Text sizes of visible text in the parent UI (main), by character count.
  const sizes = [];
  const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
  let n; while ((n = walker.nextNode())) {
    const t = n.textContent.trim(); if (!t) continue;
    const el = n.parentElement, cs = getComputedStyle(el), r = el.getBoundingClientRect();
    if (cs.visibility === 'hidden' || cs.display === 'none' || r.width === 0) continue;
    sizes.push([parseFloat(cs.fontSize), t.length]);
  }
  const chars = sizes.reduce((a, [, c]) => a + c, 0) || 1;
  const under = (px) => Math.round(100 * sizes.filter(([s]) => s < px).reduce((a, [, c]) => a + c, 0) / chars);
  const targets = [...document.querySelectorAll('main button, main a, main summary, header button, .side-nav a, .side-nav button')]
    .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; })
    .filter(e => { const r = e.getBoundingClientRect(); return r.height < 40 || r.width < 40; })
    .map(e => (e.textContent || e.getAttribute('aria-label') || '').trim().slice(0, 30));
  const cards = [...main.querySelectorAll('.card, .tile, .camera-picker button, .screen-frame')]
    .map(e => Math.round(e.getBoundingClientRect().width)).filter(w => w > 0);
  return {
    vw, vh: innerHeight, nav_w: Math.round(navW), main_w: Math.round(mr.width), content_w: Math.round(contentW),
    content_left: Math.round(kids.length ? Math.min(...kids.map(r => r.left)) : mr.left),
    space_available: Math.round(vw - navW - (xr && xr.left < vw ? xr.width : 0)),
    utilization_pct: Math.round(100 * contentW / Math.max(1, vw - navW)),
    text_under_14_pct: under(14), text_under_16_pct: under(16),
    min_text_px: sizes.length ? Math.min(...sizes.map(([s]) => s)) : null,
    body_text_px: parseFloat(getComputedStyle(document.body).fontSize),
    h1_px: (() => { const h = main.querySelector('h1'); return h ? parseFloat(getComputedStyle(h).fontSize) : null; })(),
    horizontal_overflow_px: Math.max(0, document.documentElement.scrollWidth - vw),
    small_targets: targets.length, small_target_examples: targets.slice(0, 6),
    card_widths: cards.slice(0, 12),
    xray_open: Boolean(xr), xray_w: xr ? Math.round(xr.width) : 0,
    xray_covers_content_px: xr ? Math.max(0, Math.round(mr.right - xr.left)) : 0,
  };
}
"""


RESET_404 = re.compile(r"status of 404 .*\(https?://[^)]+/v1/windows/win_[0-9a-f]+/(brief|changes)\)$")


def token(base: str) -> dict:
    t = httpx.post(base + "/v1/dev/sessions", json={"member_id": "mem_parent_a"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def reset(base: str) -> None:
    httpx.post(base + "/v1/dev/reset", headers=token(base), timeout=60).raise_for_status()


def scenario(base: str, key: str) -> None:
    httpx.post(base + "/v1/dev/access-scenario", json={"scenario": key}, headers=token(base),
               timeout=120).raise_for_status()


def fresh(page, url: str) -> None:
    """A full load: after a reset, a same-URL hash navigation would keep the old page."""
    page.goto("about:blank")
    page.goto(url)


def click_action(page, action: str) -> None:
    """Scenario controls live in the side nav or a drawer depending on the
    layout; clicking by data-action works in both."""
    page.evaluate(f"document.querySelector('[data-action=\"{action}\"]').click()")


def run(phase: str, base: str, only: set[str] | None, viewports: list[tuple[int, int]]) -> None:
    out = ROOT / "artifacts" / "visual-qa" / phase
    out.mkdir(parents=True, exist_ok=True)
    results, errors, resets = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for w, h in viewports:
            tag = f"{w}x{h}"
            ctx = browser.new_context(viewport={"width": w, "height": h}, reduced_motion="reduce")
            page = ctx.new_page()
            console = []
            # With the failing URL: "Failed to load resource" alone can't be traced.
            page.on("console", lambda m, c=console: c.append(
                m.text + (f" ({m.location['url']})" if m.location.get("url") else "")) if m.type == "error" else None)

            def shot(scene: str, full: bool = False):
                if only and scene not in only:
                    return
                page.wait_for_timeout(600)
                path = out / f"{scene}-{tag}.png"
                page.screenshot(path=str(path), full_page=full)
                m = page.evaluate(MEASURE)
                results.append({"scene": scene, "viewport": tag, "screenshot": path.relative_to(ROOT).as_posix(), **m})

            def wanted(*scenes: str) -> bool:
                return not only or bool(only & set(scenes))

            # Purchase to access: home, the journey, connect, verified, a refusal, studio, growth.
            if wanted("home", "access-unbound", "access-connect", "access-verified"):
                scenario(base, "paid_not_active")
                fresh(page, base + "/#/home")
                page.wait_for_selector(".access-card", timeout=30000)
                shot("home", full=True)
                page.goto(base + "/#/access/dev_cam_demo")
                page.wait_for_selector(".access-status", timeout=30000)
                shot("access-unbound", full=True)
                click_action(page, "access-connect-open")
                page.wait_for_selector("#access-sheet[open]", timeout=10000)
                shot("access-connect")
                page.locator("#connect-form button[type=submit]").click()
                page.wait_for_selector(".verified-card", timeout=30000)
                shot("access-verified", full=True)
            if wanted("access-refused"):
                scenario(base, "paid_wrong_camera")
                fresh(page, base + "/#/access/dev_cam_demo")
                page.wait_for_selector("#access-lookup", timeout=30000)
                page.select_option("#lookup-channel", "activation_code")
                page.fill("#lookup-ref", "CODE-MEM-2002")
                page.get_by_role("button", name="Check this purchase").click()
                page.wait_for_selector(".lookup-result", timeout=15000)
                page.locator(".lookup-result").scroll_into_view_if_needed()
                shot("access-refused")
            if wanted("studio"):
                fresh(page, base + "/#/studio")
                page.wait_for_selector(".studio-card", timeout=30000)
                page.locator('[data-action="studio-run"][data-key="paid_twice"]').click()
                page.wait_for_selector(".studio-card.active .st-ok, .studio-card.active .st-changed", timeout=60000)
                shot("studio", full=True)
            if wanted("customers", "access-ended"):
                fresh(page, base + "/#/customers")
                page.wait_for_selector(".customer-card", timeout=30000)
                shot("customers", full=True)
                page.locator('[data-action="customer-try"][data-key="plan_ended"]').click()
                page.wait_for_selector(".access-status", timeout=60000)
                shot("access-ended", full=True)
            if wanted("renewal-ended", "renewal-renewed", "renewal-xray"):
                fresh(page, base + "/#/studio")
                page.wait_for_selector(".studio-card", timeout=30000)
                page.locator('.studio-card [data-action="studio-run"][data-key="plan_ended"]').click()
                page.wait_for_selector(".studio-card.active", timeout=60000)
                page.evaluate("location.hash = '#/access/dev_cam_demo'")
                page.wait_for_selector(".demo-bar", timeout=30000)
                shot("renewal-ended", full=True)
                page.locator(".demo-bar button").click()
                page.wait_for_selector(".period-tag", timeout=30000)
                page.evaluate("window.scrollTo(0, 0)")
                shot("renewal-renewed", full=True)
                click_action(page, "xray-open")
                page.wait_for_selector("#xray .xsec[data-sec=periods] tbody tr", timeout=30000)
                page.evaluate("document.querySelector('#xray .xsec[data-sec=periods]').scrollIntoView()")
                shot("renewal-xray")
                page.keyboard.press("Escape")
            # Recorded evidence, through the real APIs: the plan renews, the worker
            # analyses the clip in its own process, then the parent opens it.
            if wanted("recorded") and httpx.get(base + "/v1/video/status", headers=token(base)).status_code == 200:
                scenario(base, "evidence_journey")
                auth = token(base)
                start = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                         - timedelta(days=5)).isoformat()
                httpx.post(base + "/v1/dev/purchase-evidence", headers=auth, json={
                    "channel": "apple", "external_ref": "apple-txn-1505", "plan_code": "milestones",
                    "provider_event": "DID_RENEW", "provider_version": 2, "starts_at": start, "days": 365}).raise_for_status()
                httpx.post(base + "/v1/dev/video/worker", headers=auth).raise_for_status()
                for _ in range(120):
                    runs = httpx.get(base + "/v1/video/assets", headers=auth).json()["assets"]
                    if runs and runs[0]["run"] and runs[0]["run"]["state"] in ("COMPLETED", "FAILED"):
                        break
                    page.wait_for_timeout(1000)
                fresh(page, base + "/#/recorded")
                page.wait_for_selector(".timeline-track", timeout=30000)
                # Below 600px the interval list is the control; the timeline is a picture.
                page.locator(".seg.kind-movement" if w >= 600 else ".interval-row:has(.kind-movement)").first.click()
                page.wait_for_selector(".explain-card .facts", timeout=10000)
                page.evaluate("window.scrollTo(0, 0)")
                shot("recorded", full=True)
            # Store billing states: grace keeps access, retry and pause don't; never an offer.
            for key, name in (("billing_grace", "billing-grace"), ("billing_retry", "billing-retry"),
                              ("plan_paused", "billing-paused")):
                if wanted(name):
                    scenario(base, key)
                    fresh(page, base + "/#/access/dev_cam_demo")
                    page.wait_for_selector(".access-status", timeout=30000)
                    shot(name, full=True)
            if wanted("growth"):
                scenario(base, "access_lost_after_refund")
                fresh(page, base + "/#/growth")
                page.wait_for_selector(".obs-strip", timeout=30000)
                shot("growth", full=True)

            reset(base)
            fresh(page, base + "/#/briefing")
            page.wait_for_selector("text=Last night, clearly", timeout=30000)
            shot("briefing")
            shot("briefing-full", full=True)

            page.goto(base + "/#/features"); page.wait_for_timeout(800)
            page.get_by_role("button", name="Check availability").click()
            page.wait_for_selector(".offer-card, .blocker-card", timeout=15000)
            shot("features", full=True)

            page.goto(base + "/#/briefing"); page.wait_for_timeout(800)
            click_action(page, "xray-open"); page.wait_for_timeout(1200)
            shot("xray-live")
            page.evaluate("document.querySelector('[data-action=\"xray-tab\"][data-tab=\"proof\"]').click()")
            page.wait_for_timeout(1200)
            page.evaluate("document.querySelector('[data-action=\"proof-flow\"][data-flow=\"purchase\"]').click()")
            shot("xray-proof")
            page.keyboard.press("Escape"); page.wait_for_timeout(400)

            # A correction from Parent A while Caregiver B's screen still shows rev 4.
            click_action(page, "dev-two"); page.wait_for_timeout(800)
            click_action(page, "dev-correct"); page.wait_for_timeout(2500)
            shot("briefing-two")
            click_action(page, "dev-ack-b"); page.wait_for_timeout(1500)
            page.goto(base + "/#/changes"); page.wait_for_timeout(1500)
            shot("changes", full=True)

            page.goto(base + "/?present=product"); page.wait_for_timeout(9000)
            shot("present")
            page.evaluate("document.querySelector('[data-present=\"exit\"]')?.click()")
            # The script resets the demo through the API, so the open page sees
            # its night vanish: a 404 on the old night's brief or changes is how
            # the page detects a reset made elsewhere (app.js refreshLatest), not
            # a fault. Counted apart; every other error stays an error.
            for e in console:
                (resets if RESET_404.search(e) else errors).append({"viewport": tag, "error": e})
            ctx.close()
        browser.close()
    (out / "metrics.json").write_text(json.dumps({"results": results, "console_errors": errors,
                                                  "reset_detected_404s": resets}, indent=2), encoding="utf-8")
    lines = [f"# Visual QA — {phase}", "",
             "| Scene | Viewport | Nav | Content / available | Use % | Min text | Text < 14px | Body | H1 | Overflow | Small targets | X-Ray covers |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['scene']} | {r['viewport']} | {r['nav_w']} | {r['content_w']} / {r['space_available']} | "
                     f"{r['utilization_pct']} | {r['min_text_px']} | {r['text_under_14_pct']}% | {r['body_text_px']} | "
                     f"{r['h1_px']} | {r['horizontal_overflow_px']} | {r['small_targets']} | {r['xray_covers_content_px']} |")
    lines += ["", f"Console errors: {len(errors)}"] + [f"- {e['viewport']}: {e['error'][:200]}" for e in errors[:20]]
    lines += ["", f"Resets detected by the page (404 on the previous night after a scripted reset; expected): {len(resets)}"]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    use = [r["utilization_pct"] for r in results if r["scene"] == "briefing" and int(r["viewport"].split("x")[0]) >= 1280]
    print(f"{len(results)} captures; desktop briefing utilization median {statistics.median(use) if use else '-'}%; "
          f"console errors {len(errors)}; resets detected {len(resets)}; saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("phase")
    ap.add_argument("--base", default="http://127.0.0.1:8710")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--viewports", nargs="*", help="e.g. 1440x900 390x844; default: all seven")
    a = ap.parse_args()
    sizes = [tuple(int(x) for x in v.split("x")) for v in a.viewports] if a.viewports else VIEWPORTS
    run(a.phase, a.base, set(a.only) if a.only else None, sizes)
