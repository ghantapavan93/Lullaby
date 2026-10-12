"""Desktop and mobile must show the same recorded-evidence facts, and both
must match the API, for one controlled run.

    python scripts/media_consistency.py [--base http://127.0.0.1:8710] [--setup]

With --setup the demo is first brought to "analysed" through the real API
(scripts/evidence_journey.py). Then, for 1440x900 and 390x844, the script opens
the recorded-evidence page, selects the same interval (the first one measured
as movement, by its sequence number), reads what the page says, and compares.

Controlled: the same clip hash, processor version, config digest, scenario,
evidence revision, interval and code commit for both captures; all of them are
written to the report. Results: artifacts/visual-qa/consistency/.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / "artifacts" / "visual-qa" / "consistency"
VIEWPORTS = ((1440, 900), (390, 844))
SCENARIO = "evidence_journey"


def api_facts(base: str) -> dict:
    http = httpx.Client(base_url=base, timeout=60)
    auth = {"Authorization": "Bearer " + http.post("/v1/dev/sessions", json={"member_id": "mem_parent_a"}).json()["token"]}
    asset_id = http.get("/v1/video/assets", headers=auth).json()["assets"][0]["id"]
    view = http.get(f"/v1/video/assets/{asset_id}", headers=auth).json()
    build = http.get("/v1/proof", headers=auth).json()["certification"]["build"]
    target = next(o for o in view["observations"] if o["kind"] == "movement")
    return {"asset_id": asset_id, "sha256": view["asset"]["sha256"], "source_kind": view["asset"]["source_kind"],
            "duration_s": view["asset"]["duration_s"], "processor_version": view["run"]["processor_version"],
            "config_digest": view["run"]["config_digest"], "evidence_revision": view["evidence_revision"],
            "interval": {"seq": target["seq"], "t_start": target["t_start"], "t_end": target["t_end"],
                         "kind": target["kind"]},
            "commit": build["commit"], "code_digest": build["code_digest"]}


def page_facts(page, base: str, width: int, seq: int) -> dict:
    page.goto(base + "/#/recorded")
    page.wait_for_selector(".timeline-track", timeout=30000)
    if width >= 600:
        page.locator(f'.seg[data-seq="{seq}"]').click()
    else:
        page.locator(f'.interval-row[data-seq="{seq}"]').click()
    page.wait_for_selector(".explain-card .facts", timeout=10000)
    page.evaluate("window.scrollTo(0, 0)")
    shot = OUT / f"recorded-{width}.png"
    page.screenshot(path=str(shot), full_page=True)
    page.evaluate("document.querySelector('details.behind').open = true")
    behind = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('details.behind table.plain:not(.compare) tr')]
        .map((tr) => [tr.querySelector('th').innerText.trim(), tr.querySelector('td').innerText.trim()]))""")
    facts = page.evaluate("""() => [...document.querySelectorAll('.explain-card .facts dt')]
        .map((dt) => [dt.innerText.trim(), dt.nextElementSibling.innerText.trim()])""")
    summary = page.evaluate("""() => [...document.querySelectorAll('.clip-summary dt')]
        .map((dt) => [dt.innerText.trim(), dt.nextElementSibling.innerText.trim()])""")
    return {"heading": page.locator("#explain-title").inner_text().strip(),
            "kind": page.locator(".explain-card .kind-line").inner_text().strip(),
            "facts": facts, "summary": summary,
            "caption": page.locator("#clip-caption").inner_text().strip(),
            "sha256": behind.get("SHA-256"), "run": behind.get("Run"),
            "evidence_revision": behind.get("Evidence revision"),
            "screenshot": shot.relative_to(ROOT.parent).as_posix()}


def main(base: str) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    expected = api_facts(base)
    captures = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for w, h in VIEWPORTS:
            ctx = browser.new_context(viewport={"width": w, "height": h}, reduced_motion="reduce")
            captures[f"{w}x{h}"] = page_facts(ctx.new_page(), base, w, expected["interval"]["seq"])
            ctx.close()
        browser.close()
    desk, mob = captures["1440x900"], captures["390x844"]
    shared = ("heading", "kind", "facts", "summary", "caption", "sha256", "run", "evidence_revision")
    checks = {f"same {k} on desktop and mobile": desk[k] == mob[k] for k in shared}
    iv = expected["interval"]
    checks |= {
        "page shows the API's clip hash": desk["sha256"] == expected["sha256"],
        "page shows the API's processor version": expected["processor_version"] in (desk["run"] or ""),
        "page shows the API's config digest": expected["config_digest"] in (desk["run"] or ""),
        "page shows the API's evidence revision": desk["evidence_revision"] == str(expected["evidence_revision"]),
        "page shows the API's interval": desk["heading"].startswith(
            f"{int(iv['t_start']) // 60}:{int(iv['t_start']) % 60:02d}–{int(iv['t_end']) // 60}:{int(iv['t_end']) % 60:02d}"),
        "summary adds up to the clip length": abs(sum(float(v.split()[0]) for _, v in desk["summary"])
                                                 - round(expected["duration_s"])) <= len(desk["summary"]) * 0.5,
    }
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "base": base, "scenario": SCENARIO,
              "controlled": expected, "captures": captures, "checks": checks, "passed": all(checks.values())}
    (OUT / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    for name, ok in checks.items():
        print("PASS" if ok else "FAIL", name)
    print("commit", expected["commit"], "digest", expected["code_digest"], "clip", expected["sha256"][:12],
          expected["processor_version"], "revision", expected["evidence_revision"], "interval", iv)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8710")
    ap.add_argument("--setup", action="store_true")
    a = ap.parse_args()
    if a.setup:
        sys.path.insert(0, str(Path(__file__).parent))
        import evidence_journey
        evidence_journey.run(a.base)
    sys.exit(main(a.base))
