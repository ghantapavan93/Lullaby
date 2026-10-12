"""Record the recruiter walkthrough on a deployed copy, as a real browser sees it.

    python deploy/record_demo.py https://lullaby-rho.vercel.app

Wakes the backend first (a cold start is not part of the demo), then records
`/app?present=recruiter` at 1440x900 until the walkthrough ends. Writes
artifacts/deploy/demo-recruiter.webm (and .mp4 when PyAV is installed), plus a
caption log with timestamps. The workspace is ended afterwards. Synthetic data
only; nothing needs a password.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "artifacts" / "deploy"


def wake(base: str, budget_s: float = 240) -> None:
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(base + "/healthz", timeout=60).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(5)
    raise SystemExit("the backend didn't wake")


def to_mp4(webm: Path, mp4: Path) -> bool:
    try:
        import av
    except ImportError:
        return False
    with av.open(str(webm)) as src, av.open(str(mp4), "w") as dst:
        stream_in = src.streams.video[0]
        stream_out = dst.add_stream("libx264", rate=25)
        stream_out.width, stream_out.height = stream_in.codec_context.width, stream_in.codec_context.height
        stream_out.pix_fmt = "yuv420p"
        for frame in src.decode(stream_in):
            for packet in stream_out.encode(frame.reformat(format="yuv420p")):
                dst.mux(packet)
        for packet in stream_out.encode():
            dst.mux(packet)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--audience", default="recruiter", choices=["recruiter", "product", "engineering"])
    a = ap.parse_args()
    base = a.base.rstrip("/")
    wake(base)
    OUT.mkdir(parents=True, exist_ok=True)
    video_dir = OUT / "demo-video"
    captions: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900},
                                      record_video_dir=str(video_dir), record_video_size={"width": 1440, "height": 900})
        page = context.new_page()
        started = time.monotonic()
        try:
            page.goto(f"{base}/app?present={a.audience}")
            page.wait_for_selector("#present-bar", timeout=180000)
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline and page.locator("#present-bar").count():
                line = page.locator(".present-line").first.inner_text() if page.locator(".present-line").count() else ""
                if line and (not captions or captions[-1]["text"] != line):
                    captions.append({"t": round(time.monotonic() - started, 1), "text": line})
                page.wait_for_timeout(300)
            page.wait_for_timeout(1500)
        finally:
            try:
                page.evaluate("fetch('/v1/workspace', {method: 'DELETE'})")
            except Exception:
                pass
            raw = Path(page.video.path())
            context.close()
            browser.close()
    webm = OUT / f"demo-{a.audience}.webm"
    raw.replace(webm)
    mp4 = webm.with_suffix(".mp4")
    converted = to_mp4(webm, mp4)
    (OUT / f"demo-{a.audience}-captions.json").write_text(json.dumps(captions, indent=1), encoding="utf-8")
    print(f"{webm} ({captions[-1]['t'] if captions else 0} s of captions){' and ' + str(mp4) if converted else ''}")
    for c in captions:
        print(f"  {c['t']:6.1f}s  {c['text'][:110]}")
    failed = [c for c in captions if "didn't complete" in c["text"] or "Not saved" in c["text"]]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
