"""Visual QA capture: drives the real app through the 12 screens listed in
design/VISUAL_QA.md and saves screenshots per viewport width.

Usage (server running, e.g. on 8710):
    python scripts/capture_screens.py --base http://127.0.0.1:8710 --out ../artifacts/browser-screenshots

State is set up through the same public and development APIs the UI uses;
every screenshot is a real render of the running application.
"""
import argparse
import json
import time
from pathlib import Path

import httpx
from playwright.sync_api import Page, sync_playwright

WIDTHS = [375, 390, 768, 1024, 1440, 1920]
FULL_SET_WIDTHS = {390, 1440}


class Demo:
    def __init__(self, base: str):
        self.base = base
        self.http = httpx.Client(base_url=base, timeout=60)

    def token(self, member: str) -> dict:
        t = self.http.post("/v1/dev/sessions", json={"member_id": member}).json()["token"]
        return {"Authorization": f"Bearer {t}"}

    def reset(self) -> dict:
        return self.http.post("/v1/dev/reset", headers=self.token("mem_parent_a")).json()

    def correct(self, seed: dict) -> None:
        self.http.post(f"/v1/windows/{seed['window_id']}/corrections", headers=self.token("mem_parent_a"), json={
            "target_event_id": seed["final_wake_event_id"], "corrected_end": "2026-10-10T06:35:00-04:00",
            "expected_revision": 4, "client_event_id": "qa-correction-0001"}).raise_for_status()

    def mount(self, mount: str) -> None:
        self.http.post("/v1/dev/mount", headers=self.token("mem_parent_a"), json={"mount": mount}).raise_for_status()

    def provider_next(self, behavior: str) -> None:
        self.http.post("/v1/dev/provider/next", headers=self.token("mem_parent_a"), json={"behavior": behavior})

    def purchase(self, crash: bool = False) -> None:
        h = self.token("mem_parent_a")
        d = self.http.post("/v1/capability-decisions", headers=h, json={"feature": "nextnap"}).json()
        self.http.post("/v1/purchase-intents", headers=h | {"Idempotency-Key": "qa-purchase-0001"},
                       json={"decision_id": d["decision_id"], "offer_id": d["offer"]["offer_id"]}).raise_for_status()
        self.http.post("/v1/dev/worker", headers=h, json={"crash_after_provider": crash})


def settle(page: Page) -> None:
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(400)


def shot(page: Page, out: Path, name: str, full: bool = True) -> None:
    out.mkdir(parents=True, exist_ok=True)
    settle(page)
    page.screenshot(path=str(out / f"{name}.png"), full_page=full)


def open_page(browser, base: str, width: int, route: str, scheme: str = "light"):
    ctx = browser.new_context(viewport={"width": width, "height": 900 if width >= 768 else 812},
                              color_scheme=scheme, reduced_motion="reduce",
                              is_mobile=width < 768, has_touch=width < 768)
    page = ctx.new_page()
    page.goto(f"{base}/#/{route}")
    page.wait_for_selector(".rev-badge, .feature-picker, .member-grid", timeout=15000)
    return ctx, page


def capture(base: str, out_root: Path) -> list[dict]:
    demo = Demo(base)
    report = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in WIDTHS:
            out = out_root / f"{width}"
            full = width in FULL_SET_WIDTHS

            seed = demo.reset()
            ctx, page = open_page(browser, base, width, "briefing")
            shot(page, out, "01-initial-briefing")
            if full:
                page.get_by_role("button", name="Correct final wake").click()
                page.wait_for_selector("#correction-sheet[open]")
                shot(page, out, "02-correction-interaction", full=False)
                page.get_by_role("button", name="Save correction").click()
                page.wait_for_selector(".tile.changed")
                shot(page, out, "03-revised-facts")
            ctx.close()

            # 04: Caregiver B keeps revision 4 open, Parent A corrects, B acknowledges.
            seed = demo.reset()
            ctx, page = open_page(browser, base, width, "briefing")
            page.get_by_role("radio", name="Caregiver B").click()
            demo.correct(seed)
            page.get_by_role("button", name="Acknowledge revision 4").click()
            page.wait_for_selector(".ack-card.stale")
            page.locator(".ack-card.stale").scroll_into_view_if_needed()
            shot(page, out, "04-stale-acknowledgment", full=False)
            if full:
                page.goto(f"{base}/#/changes")
                page.wait_for_selector(".member-grid")
                shot(page, out, "04b-what-changed")
            ctx.close()

            if full:
                demo.reset()
                ctx, page = open_page(browser, base, width, "features")
                page.get_by_role("button", name="Check availability").click()
                page.wait_for_selector(".offer-card")
                shot(page, out, "06-eligible-upgrade")
                demo.mount("flex_stand")
                page.get_by_role("button", name="Start simulated upgrade").click()
                page.wait_for_selector("text=We didn't submit this offer.")
                shot(page, out, "07-rejected-stale-offer")
                page.get_by_role("button", name="Check eligibility again").click()
                page.wait_for_selector("text=Blocked by hardware")
                shot(page, out, "05-hardware-ineligible")
                ctx.close()

                demo.reset()
                demo.purchase(crash=True)        # worker dies after provider success
                ctx, page = open_page(browser, base, width, "features")
                page.wait_for_selector(".pill-SUBMITTED")
                shot(page, out, "08-pending-purchase")
                ctx.close()
                owner = demo.token("mem_parent_a")
                demo.http.post("/v1/dev/worker", headers=owner, json={})   # may still be inside the crashed lease
                time.sleep(3.5)
                demo.http.post("/v1/dev/worker", headers=owner, json={})
                ctx, page = open_page(browser, base, width, "features")
                page.wait_for_selector(".pill-CONFIRMED")
                shot(page, out, "09-confirmed-purchase")
                ctx.close()

                for behavior, name in (("decline", "10a-failed-purchase"), ("pending", "10b-unknown-purchase")):
                    demo.reset()
                    demo.provider_next(behavior)
                    demo.purchase()
                    ctx, page = open_page(browser, base, width, "features")
                    page.wait_for_selector(".pill-FAILED, .pill-UNKNOWN")
                    shot(page, out, name)
                    ctx.close()

                demo.reset()
                ctx, page = open_page(browser, base, width, "briefing")
                nav = ".tab-bar" if width < 1024 else "#side-nav"
                page.locator(f"{nav} [data-action=xray-open]").click()
                page.wait_for_selector("#xray.open table")
                shot(page, out, "11-engineering-xray", full=False)
                ctx.close()

            if width < 1024:
                demo.reset()
                ctx, page = open_page(browser, base, width, "briefing")
                page.locator(".tab-bar").scroll_into_view_if_needed()
                shot(page, out, "12-mobile-navigation", full=False)
                ctx.close()

            # Dark theme sample at each width.
            demo.reset()
            ctx, page = open_page(browser, base, width, "briefing", scheme="dark")
            shot(page, out, "13-dark-briefing")
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            ctx.close()
            report.append({"width": width, "horizontal_overflow_px": overflow,
                           "files": sorted(f.name for f in out.glob("*.png"))})
        browser.close()
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8710")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "artifacts" / "browser-screenshots"))
    args = ap.parse_args()
    result = capture(args.base, Path(args.out))
    Path(args.out, "capture-report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
