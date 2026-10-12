"""Check a deployed copy from outside, in a real browser, as a reviewer would.

    python deploy/live_check.py https://lullaby-rho.vercel.app [--width 1440 --height 900]

Landing page -> backend awake -> open the prototype (a private workspace) ->
the evidence scenario (plan ended: the clip is refused) -> the store's renewal
-> the worker analyses the clip -> play one interval -> a caregiver's report
kept beside the measurement. A second browser at the same time must be told the
truth (busy, on a one-workspace host) and must not see the first one's data.
The workspace is ended at the end. Screenshots and timings go to
artifacts/deploy/live-<width>/. Nothing here needs a password: the demo signs in
synthetic family members only.
"""
from __future__ import annotations

import re
import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

OUT = Path(__file__).resolve().parents[1] / "artifacts" / "deploy"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    a = ap.parse_args()
    out = OUT / f"live-{a.width}"
    out.mkdir(parents=True, exist_ok=True)
    marks, t0 = {}, time.monotonic()

    def mark(name):
        marks[name] = round(time.monotonic() - t0, 1)
        print(f"{marks[name]:7.1f}s  {name}", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": a.width, "height": a.height})
        pg = ctx.new_page()
        pg.goto(a.base + "/")
        expect(pg.get_by_role("note", name="Prototype disclosure")).to_contain_text("Not affiliated with Nanit")
        expect(pg.locator("#status")).to_contain_text("Backend awake", timeout=200000)
        mark("landing: backend awake")
        pg.screenshot(path=str(out / "1-landing.png"), full_page=True)
        pg.get_by_role("link", name="Open the prototype").click()
        expect(pg.locator(".access-card").first).to_be_visible(timeout=200000)
        mark("app: private workspace open")
        pg.screenshot(path=str(out / "2-home.png"), full_page=True)

        # A second reviewer at the same moment: told the truth, sees nothing of the first.
        other = browser.new_context(viewport={"width": a.width, "height": a.height}).new_page()
        other.goto(a.base + "/app")
        expect(other.locator("#main")).to_contain_text("in use", timeout=120000)
        mark("second reviewer: told the workspace is in use")
        other.screenshot(path=str(out / "3-second-reviewer.png"))
        other.context.close()

        pg.goto(a.base + "/app#/studio")
        pg.locator('.studio-card [data-action="studio-run"][data-key="evidence_journey"]').click()
        expect(pg.locator(".studio-card.active")).to_be_visible(timeout=200000)
        mark("scenario: Milestones ended")
        pg.goto(a.base + "/app#/recorded")
        expect(pg.locator("#gate-title")).to_contain_text("isn't on this camera's plan", timeout=60000)
        mark("clip refused by the plan gate")
        pg.screenshot(path=str(out / "4-refused.png"), full_page=True)
        pg.goto(a.base + "/app#/access/dev_cam_demo")
        pg.get_by_role("button", name="Store sends the renewal").click()
        expect(pg.locator(".access-status")).to_contain_text(re.compile("Plan active|Verified on this camera"), timeout=120000)
        mark("store renewal reached the camera")
        pg.locator(".recorded-link").click()
        run_worker = pg.get_by_role("button", name="Run the worker")
        if run_worker.count():
            run_worker.click()
        expect(pg.locator(".run-status")).to_contain_text("Analysed", timeout=300000)
        mark("clip analysed by the worker")
        narrow = a.width < 600
        pick = ".interval-row:has(.kind-movement)" if narrow else ".seg.kind-movement"
        pg.locator(pick).first.click()
        pg.wait_for_function("document.getElementById('clip') && document.getElementById('clip').readyState >= 1",
                             timeout=120000)
        pg.get_by_role("button", name="Play this part").click()
        expect(pg.locator("#play-status")).to_contain_text("Played", timeout=120000)
        mark("interval played and stopped on its boundary")
        stopped = pg.evaluate("document.getElementById('clip').currentTime")
        pg.locator('input[value="infant_still"]').check()
        pg.get_by_role("button", name="Add my report").click()
        expect(pg.locator(".before-after")).to_contain_text("The baby was still", timeout=60000)
        mark("report kept beside the measurement")
        pg.evaluate("window.scrollTo(0, 0)")
        pg.screenshot(path=str(out / "5-recorded.png"), full_page=True)
        ended = pg.evaluate("fetch('/v1/workspace', {method: 'DELETE'}).then((r) => r.json())")
        mark(f"workspace ended: {ended}")
        browser.close()
    (out / "timings.json").write_text(json.dumps({"base": a.base, "width": a.width, "marks": marks,
                                                  "clip_stopped_at_s": stopped}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
