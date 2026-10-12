"""Sweep a deployed copy for bugs, end to end, in a real browser.

    python deploy/live_sweep.py https://lullaby-rho.vercel.app [--width 390 --height 844]

Every page, every Scenario Studio scenario (the server's status against the
expected one), the two-cause comparison, a plan check, the X-Ray, the theme,
reload and back navigation, a Present walkthrough, and the workspace hand-off.
Throughout, it records every console error, uncaught page error, failed
request (4xx/5xx) and horizontal overflow. The workspace is always ended, even
when a step fails. Writes artifacts/deploy/sweep-<w>.json and prints one line
per finding. Synthetic data only; nothing needs a password.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

OUT = Path(__file__).resolve().parents[1] / "artifacts" / "deploy"
# Requests that are supposed to fail, with the reason they're fine.
EXPECTED_FAILURES = [
    (r"/v1/windows/win_[0-9a-f]+/(brief|changes)$", 404, "the page noticing a reset made by a scenario"),
    (r"/v1/workspace$", 404, "only when run without the gateway"),
    (r"/v1/purchase-intents$", 409, "Present's stale-offer step: the old offer is refused before any payment"),
]


class Sweep:
    def __init__(self, page, base: str, width: int):
        self.pg, self.base, self.width = page, base, width
        self.findings: list[dict] = []
        self.steps: list[str] = []
        self.results: dict = {}
        self.current = "start"
        page.on("console", self._console)
        page.on("pageerror", lambda e: self.finding("pageerror", str(e)[:300]))
        page.on("response", self._response)

    def _console(self, m):
        if m.type == "error" and "Failed to load resource" not in m.text:
            self.finding("console", m.text[:200])

    def _response(self, r):
        if r.status < 400 or not r.url.startswith(self.base):
            return
        path = r.url[len(self.base):].split("?")[0]
        if any(r.status == status and re.search(pattern, path) for pattern, status, _ in EXPECTED_FAILURES):
            return
        self.finding("http", f"{r.request.method} {path} -> {r.status}")

    def finding(self, kind: str, detail: str) -> None:
        self.findings.append({"step": self.current, "kind": kind, "detail": detail})
        print(f"  ! [{self.current}] {kind}: {detail}", flush=True)

    def step(self, name: str) -> None:
        self.current = name
        self.steps.append(name)
        print(f"- {name}", flush=True)

    def overflow(self) -> None:
        extra = self.pg.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
        if extra > 1:
            self.finding("overflow", f"{extra}px wider than the viewport")

    def visible(self, selector: str, timeout: int = 60000, what: str = "render") -> bool:
        try:
            expect(self.pg.locator(selector).first).to_be_visible(timeout=timeout)
            return True
        except AssertionError:
            self.finding(what, f"{selector} never appeared")
            return False

    def run(self) -> None:
        pg, base = self.pg, self.base
        self.step("landing")
        pg.goto(base + "/")
        expect(pg.locator("#status")).to_contain_text("Backend awake", timeout=200000)
        self.overflow()

        self.step("open the prototype")
        pg.goto(base + "/app")
        expect(pg.locator(".access-card").first).to_be_visible(timeout=200000)
        expect(pg.get_by_role("note", name="Prototype disclosure")).to_be_visible()
        self.overflow()

        for route, ready in (("#/access/dev_cam_demo", ".access-status"), ("#/recorded", "#main h1"),
                             ("#/briefing", ".rev-badge"), ("#/changes", ".member-grid"), ("#/features", "#main h1"),
                             ("#/studio", ".studio-card"), ("#/growth", "#window-title"),
                             ("#/customers", ".customer-card")):
            self.step(f"page {route}")
            pg.goto(base + "/app" + route)
            self.visible(ready)
            pg.wait_for_timeout(800)
            self.overflow()

        self.step("studio: every scenario")
        pg.goto(base + "/app#/studio")
        self.visible(".studio-card")
        keys = pg.eval_on_selector_all('.studio-card [data-action="studio-run"]', "els => els.map((e) => e.dataset.key)")
        for key in keys:
            self.current = f"scenario {key}"
            t = time.monotonic()
            pg.locator(f'.studio-card [data-action="studio-run"][data-key="{key}"]').click()
            card = pg.locator(f'.studio-card.active:has(.btn[data-key="{key}"])')
            try:
                expect(card.locator(".expect-pair .st-ok, .expect-pair .st-changed").first).to_be_visible(timeout=120000)
            except AssertionError:
                self.finding("scenario", f"{key}: no result shown")
                continue
            mismatches = card.locator(".st-changed").all_inner_texts()
            self.results[key] = {"seconds": round(time.monotonic() - t, 1), "mismatches": mismatches}
            if mismatches:
                self.finding("scenario", f"{key}: {mismatches}")
            print(f"    {key}: {'ok' if not mismatches else mismatches} ({self.results[key]['seconds']}s)", flush=True)
        self.overflow()

        self.step("studio: same symptom, two causes")
        pg.locator('[data-action="studio-compare"]').click()
        if self.visible(".cmp-side", 120000, "compare") and pg.locator(".cmp-side").count() != 2:
            self.finding("compare", f"{pg.locator('.cmp-side').count()} sides shown")

        self.step("features: check availability")
        pg.goto(base + "/app#/features")
        button = pg.get_by_role("button", name="Check availability")
        if button.count():
            button.first.click()
            pg.wait_for_timeout(3000)
            text = pg.locator("#main").inner_text().lower()
            if not any(w in text for w in ("simulated payment", "nothing more to buy", "offer", "already")):
                self.finding("features", "no offer or explanation after checking availability")
        else:
            self.finding("features", "no 'Check availability' button")
        self.overflow()

        self.step("x-ray")
        pg.goto(base + "/app#/briefing")
        self.visible(".rev-badge")
        if self.width >= 1024:
            pg.evaluate("document.getElementById('nav-eng') && (document.getElementById('nav-eng').open = true)")
        opener = pg.locator('[data-action="xray-open"]:visible').first
        if opener.count():
            opener.click()
            self.visible("#xray.open table", 30000, "xray")
            pg.keyboard.press("Escape")
        else:
            self.finding("xray", "no visible X-Ray button")

        self.step("theme toggle")
        before = pg.evaluate("document.documentElement.dataset.theme || ''")
        pg.locator("#theme-toggle").click()
        if pg.evaluate("document.documentElement.dataset.theme || ''") == before:
            self.finding("theme", f"theme stayed {before!r}")
        pg.locator("#theme-toggle").click()

        self.step("reload and back")
        pg.goto(base + "/app#/access/dev_cam_demo")
        self.visible(".access-status")
        pg.reload()
        self.visible(".access-status")
        pg.goto(base + "/app#/home")
        self.visible(".access-card")
        pg.go_back()
        self.visible(".access-status")

        self.step("present: product walkthrough")
        captions = []
        pg.goto(base + "/app?present=product&fast=1")
        if self.visible("#present-bar", 120000, "present"):
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline and pg.locator("#present-bar").count():
                line = pg.locator(".present-line").first.inner_text() if pg.locator(".present-line").count() else ""
                if line and (not captions or captions[-1] != line):
                    captions.append(line)
                pg.wait_for_timeout(400)
            if pg.locator("#present-bar").count():
                self.finding("present", "the walkthrough didn't finish in 4 minutes")
            for c in captions:
                if "didn't complete" in c or "Not saved" in c:
                    self.finding("present", c[:200])

        self.step("workspace hand-off")
        ended = pg.evaluate("fetch('/v1/workspace', {method: 'DELETE'}).then((r) => r.json())")
        if not ended.get("ended"):
            self.finding("workspace", f"ending it returned {ended}")
        pg.goto(base + "/app")
        self.visible(".access-card", 120000, "workspace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    a = ap.parse_args()
    base = a.base.rstrip("/")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": a.width, "height": a.height}).new_page()
        sweep = Sweep(page, base, a.width)
        try:
            sweep.run()
        except Exception as exc:          # a broken step is a finding, and the workspace is still ended
            sweep.finding("crash", f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            try:
                page.evaluate("fetch('/v1/workspace', {method: 'DELETE'})")
            except Exception:
                pass
            browser.close()
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"base": base, "width": a.width, "steps": sweep.steps, "scenarios": sweep.results,
              "findings": sweep.findings}
    (OUT / f"sweep-{a.width}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    matched = sum(1 for r in sweep.results.values() if not r["mismatches"])
    print(f"\n{len(sweep.findings)} finding(s); {matched} of {len(sweep.results)} scenarios matched", flush=True)
    return 0 if not sweep.findings else 1


if __name__ == "__main__":
    sys.exit(main())
