"""Record the 75-second story on a running copy, as a real browser sees it.

    python deploy/record_story.py https://lullaby-rho.vercel.app

0-10 s the problem (landing); 10-30 s a purchase that never reached the camera,
built on the backend, then connected without paying again; 30-45 s the separate
check that the camera now works; 45-60 s the same symptom with a different
cause and the refusals; 60-75 s the Engineering X-Ray with the stored evidence.
Every step clicks the real app; nothing is staged in the page. Writes
artifacts/deploy/story.webm and story.mp4 (PyAV) and ends the workspace.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from record_demo import OUT, to_mp4, wake  # noqa: E402


def hold(pg, seconds: float) -> None:
    pg.wait_for_timeout(int(seconds * 1000))


def main() -> int:
    base = sys.argv[1].rstrip("/")
    wake(base)
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(OUT / "story-video"),
                                      record_video_size={"width": 1440, "height": 900})
        pg = context.new_page()
        t0 = time.monotonic()
        marks = []
        mark = lambda what: marks.append((round(time.monotonic() - t0, 1), what))  # noqa: E731
        try:
            pg.goto(base + "/")
            expect(pg.locator("#status")).to_contain_text("Backend awake", timeout=200000)
            mark("the problem"); hold(pg, 6)
            pg.get_by_role("link", name="Open the prototype").click()
            expect(pg.locator(".start-here")).to_be_visible(timeout=200000)
            hold(pg, 2)
            pg.get_by_role("button", name="Show a parent who paid but can't use it").click()
            expect(pg.get_by_role("button", name="Connect to Nursery camera")).to_be_visible(timeout=60000)
            mark("paid, not connected: the backend's evidence"); hold(pg, 8)
            pg.get_by_role("button", name="Connect to Nursery camera").click()
            hold(pg, 3)
            pg.locator("#access-sheet button[type=submit]").click()
            expect(pg.locator("#main .verified-card h2")).to_be_visible(timeout=60000)
            mark("connected without paying again, then verified separately"); hold(pg, 9)
            pg.goto(base + "/app#/home")
            expect(pg.locator(".start-here")).to_be_visible(timeout=60000)
            pg.get_by_role("button", name="Same problem. Different cause.").click()
            expect(pg.locator(".cmp-side")).to_have_count(2, timeout=120000)
            mark("same symptom, different cause, and the refusals"); hold(pg, 4)
            pg.mouse.wheel(0, 500); hold(pg, 4)
            pg.mouse.wheel(0, 500); hold(pg, 4)
            pg.locator('[data-action="xray-open"]:visible').first.click()
            expect(pg.locator("#xray.open")).to_be_visible(timeout=30000)
            mark("Engineering X-Ray"); hold(pg, 11)
        finally:
            try:
                pg.evaluate("fetch('/v1/workspace', {method: 'DELETE'})")
            except Exception:
                pass
            raw = Path(pg.video.path())
            context.close()
            browser.close()
    webm = OUT / "story.webm"
    raw.replace(webm)
    to_mp4(webm, webm.with_suffix(".mp4"))
    for t, what in marks:
        print(f"{t:6.1f}s  {what}")
    print(webm.with_suffix(".mp4"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
