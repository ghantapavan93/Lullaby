"""Run the main journeys in Chromium, Firefox and WebKit, at 390 and 1440 px.

    python deploy/cross_browser.py http://127.0.0.1:8710      # a local demo server (no gateway)

For each engine and width: Home and the same-problem comparison, connect then
verify, a plan check with an offer, the night briefing and its correction
sheet, and recorded evidence from loading the clip to playing an interval.
Records console errors, uncaught errors, failed requests and sideways overflow.
Prints one line per finding and a summary; exit code 0 only when clean.
Synthetic data only.
"""
from __future__ import annotations

import sys
import time

import httpx
from playwright.sync_api import expect, sync_playwright


def reset(base: str) -> None:
    token = httpx.post(base + "/v1/dev/sessions", json={"member_id": "mem_parent_a"}).json()["token"]
    httpx.post(base + "/v1/dev/reset", headers={"Authorization": f"Bearer {token}"}, timeout=60).raise_for_status()


def journeys(pg, base: str, note) -> None:
    def overflow(where: str) -> None:
        extra = pg.evaluate("document.scrollingElement.scrollWidth - innerWidth")
        if extra > 1:
            note("overflow", f"{where}: {extra}px")

    pg.goto(base + "/#/home")
    expect(pg.locator(".start-here")).to_be_visible(timeout=30000)
    overflow("home")
    pg.get_by_role("button", name="Same problem. Different cause.").click()
    expect(pg.locator(".cmp-side")).to_have_count(2, timeout=90000)
    if pg.locator(".cmp-side").first.locator(".cmp-steps > li").count() != 5:
        note("compare", "a side doesn't show five steps")
    overflow("compare")

    reset(base)
    token = httpx.post(base + "/v1/dev/sessions", json={"member_id": "mem_parent_a"}).json()["token"]
    httpx.post(base + "/v1/dev/access-scenario", headers={"Authorization": f"Bearer {token}"},
               json={"scenario": "paid_not_active"}, timeout=60).raise_for_status()
    pg.goto(base + "/#/access/dev_cam_demo")
    pg.reload()
    pg.get_by_role("button", name="Connect to Nursery camera").click()
    pg.locator("#access-sheet button[type=submit]").click()
    expect(pg.locator("#main .verified-card h2")).to_be_visible(timeout=60000)
    overflow("access verified")

    pg.goto(base + "/#/features")
    pg.get_by_role("button", name="Check availability").click()
    expect(pg.locator(".blocker-card")).to_be_visible(timeout=30000)
    overflow("features")

    reset(base)
    pg.goto(base + "/#/briefing")
    pg.reload()
    expect(pg.locator(".track")).to_be_visible(timeout=30000)
    pg.get_by_role("button", name="Correct final wake").first.click()
    expect(pg.locator("#correction-sheet")).to_be_visible()
    pg.keyboard.press("Escape")
    expect(pg.locator("#correction-sheet")).to_be_hidden()
    overflow("briefing")

    pg.goto(base + "/#/recorded")
    pg.get_by_role("button", name="Load the demo clip").click()
    pg.get_by_role("button", name="Build it").click()
    pg.get_by_role("button", name="Run the worker").click(timeout=60000)
    expect(pg.locator(".timeline-track .seg").first).to_be_visible(timeout=120000)
    # Phones select from the interval list (the bar is decorative there); wider screens use the bar.
    row = pg.locator(".interval-row").first if pg.evaluate("innerWidth < 600") else pg.locator(".timeline-track .seg").first
    row.click()
    play = pg.get_by_role("button", name="Play this part")
    if play.count():
        play.first.click()
        deadline = time.monotonic() + 20
        status = ""
        while time.monotonic() < deadline:
            status = pg.locator("#play-status").inner_text() if pg.locator("#play-status").count() else ""
            if status and "Playing" not in status and "Loading" not in status:
                break
            pg.wait_for_timeout(300)
        if "couldn't" in status.lower() or "not play" in status.lower():
            note("playback", status[:160])
    else:
        note("playback", "no 'Play this part' button after selecting an interval")
    overflow("recorded")


def main() -> int:
    base = sys.argv[1].rstrip("/")
    findings: list[str] = []
    with sync_playwright() as p:
        for engine in ("chromium", "firefox", "webkit"):
            browser = getattr(p, engine).launch()
            for width, height in ((1440, 900), (390, 844)):
                reset(base)
                context = browser.new_context(viewport={"width": width, "height": height})
                pg = context.new_page()
                tag = f"{engine} {width}"

                def note(kind: str, detail: str, tag=tag) -> None:
                    findings.append(f"{tag} {kind}: {detail}")
                    print(f"  ! {tag} {kind}: {detail}", flush=True)
                pg.on("console", lambda m, note=note: note("console", m.text[:200])
                      if m.type == "error" and "Failed to load resource" not in m.text else None)
                pg.on("pageerror", lambda e, note=note: note("pageerror", str(e)[:200]))
                pg.on("response", lambda r, note=note: note("http", f"{r.request.method} {r.url.split('8710')[-1]} -> {r.status}")
                      if r.status >= 500 else None)
                t = time.monotonic()
                try:
                    journeys(pg, base, note)
                    print(f"- {tag}: journeys done in {time.monotonic() - t:.0f}s", flush=True)
                except Exception as exc:
                    note("crash", f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
                context.close()
            browser.close()
    print(f"\n{len(findings)} finding(s)")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
