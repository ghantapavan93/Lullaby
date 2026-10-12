"""Automated accessibility audit with axe-core (WCAG 2.0/2.1/2.2 A and AA rules)
on the main screens of the running app, light and dark.

    python scripts/a11y_audit.py --base http://127.0.0.1:8710

Automated rules catch only part of WCAG; keyboard flow and screen-reader
wording still need a person. Results: artifacts/a11y/axe-report.json.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

AXE = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def audit(page) -> list[dict]:
    page.add_script_tag(url=AXE)
    result = page.evaluate("async (tags) => (await axe.run(document, {runOnly: {type: 'tag', values: tags}})).violations", TAGS)
    return [{"id": v["id"], "impact": v["impact"], "help": v["help"],
             "nodes": [n["target"] for n in v["nodes"]][:8]} for v in result]


def main(base: str, out: Path) -> dict:
    http = httpx.Client(base_url=base, timeout=60)
    report = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scheme in ("light", "dark"):
            for width in (390, 1440):
                token = http.post("/v1/dev/sessions", json={"member_id": "mem_parent_a"}).json()["token"]
                http.post("/v1/dev/reset", headers={"Authorization": f"Bearer {token}"})
                ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
                page = ctx.new_page()
                # Purchase to access: home, the journey with the connect sheet, studio, growth lab.
                http.post("/v1/dev/access-scenario", json={"scenario": "paid_not_active"},
                          headers={"Authorization": f"Bearer {token}"})
                page.goto(f"{base}/#/home")
                page.wait_for_selector(".access-card")
                report[f"{scheme}-{width}-home"] = audit(page)
                page.goto(f"{base}/#/access/dev_cam_demo")
                page.wait_for_selector(".access-status")
                report[f"{scheme}-{width}-access"] = audit(page)
                page.locator("[data-action=access-connect-open]").click()
                page.wait_for_selector("#access-sheet[open]")
                report[f"{scheme}-{width}-access-connect"] = audit(page)
                page.locator("#connect-form button[type=submit]").click()
                page.wait_for_selector(".verified-card")
                report[f"{scheme}-{width}-access-verified"] = audit(page)
                page.goto(f"{base}/#/studio")
                page.wait_for_selector(".studio-card")
                report[f"{scheme}-{width}-studio"] = audit(page)
                # Same symptom, two causes: the comparison runs the real backend, rolled back.
                page.locator("[data-action=studio-compare]").click()
                page.wait_for_selector(".cmp-side", timeout=60000)
                report[f"{scheme}-{width}-studio-compare"] = audit(page)
                page.goto(f"{base}/#/growth")
                page.wait_for_selector(".obs-strip")
                report[f"{scheme}-{width}-growth"] = audit(page)
                page.goto(f"{base}/#/customers")
                page.wait_for_selector(".customer-card")
                report[f"{scheme}-{width}-customers"] = audit(page)
                http.post("/v1/dev/access-scenario", json={"scenario": "plan_ended"},
                          headers={"Authorization": f"Bearer {token}"})
                page.goto("about:blank")
                page.goto(f"{base}/#/access/dev_cam_demo")
                page.wait_for_selector(".access-status")
                report[f"{scheme}-{width}-access-ended"] = audit(page)
                # Store billing states: grace (features work, payment needs attention) and retry (no access).
                for key in ("billing_grace", "billing_retry"):
                    http.post("/v1/dev/access-scenario", json={"scenario": key},
                              headers={"Authorization": f"Bearer {token}"})
                    page.goto("about:blank")
                    page.goto(f"{base}/#/access/dev_cam_demo")
                    page.wait_for_selector(".access-status")
                    report[f"{scheme}-{width}-access-{key.replace('_', '-')}"] = audit(page)
                # Recorded evidence (only when the server has video on): renew, analyse, open a segment.
                auth = {"Authorization": f"Bearer {token}"}
                if http.get("/v1/video/status", headers=auth).status_code == 200:
                    http.post("/v1/dev/access-scenario", json={"scenario": "evidence_journey"}, headers=auth)
                    start = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                             - timedelta(days=5)).isoformat()
                    http.post("/v1/dev/purchase-evidence", headers=auth, json={
                        "channel": "apple", "external_ref": "apple-txn-1505", "plan_code": "milestones",
                        "provider_event": "DID_RENEW", "provider_version": 2, "starts_at": start,
                        "days": 365}).raise_for_status()
                    http.post("/v1/dev/video/worker", headers=auth).raise_for_status()
                    for _ in range(120):
                        assets = http.get("/v1/video/assets", headers=auth).json()["assets"]
                        if assets and assets[0]["run"] and assets[0]["run"]["state"] in ("COMPLETED", "FAILED"):
                            break
                        page.wait_for_timeout(1000)
                    page.goto("about:blank")
                    page.goto(f"{base}/#/recorded")
                    page.wait_for_selector(".timeline-track", timeout=30000)
                    report[f"{scheme}-{width}-recorded"] = audit(page)
                    page.locator(".seg.kind-movement" if width >= 600 else ".interval-row:has(.kind-movement)").first.click()
                    page.wait_for_selector(".explain-card .facts", timeout=10000)
                    report[f"{scheme}-{width}-recorded-explain"] = audit(page)
                http.post("/v1/dev/reset", headers={"Authorization": f"Bearer {token}"})
                page.goto("about:blank")
                page.goto(f"{base}/#/briefing")
                page.wait_for_selector(".rev-badge")
                report[f"{scheme}-{width}-briefing"] = audit(page)
                page.goto(f"{base}/#/features")
                page.get_by_role("button", name="Check availability").click()
                page.wait_for_selector(".offer-card")
                report[f"{scheme}-{width}-features-offer"] = audit(page)
                page.goto(f"{base}/#/changes")
                page.wait_for_selector(".member-grid")
                report[f"{scheme}-{width}-changes"] = audit(page)
                nav = ".tab-bar" if width < 1024 else "#side-nav"
                page.goto(f"{base}/#/briefing")
                page.wait_for_selector(".rev-badge")
                if width >= 1024:   # engineering tools sit behind the "For engineers" disclosure
                    page.evaluate("document.getElementById('nav-eng').open = true")
                page.locator(f"{nav} [data-action=xray-open]").click()
                page.wait_for_selector("#xray.open table")
                report[f"{scheme}-{width}-xray"] = audit(page)
                ctx.close()
        browser.close()
    out.mkdir(parents=True, exist_ok=True)
    (out / "axe-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8710")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "artifacts" / "a11y"))
    args = ap.parse_args()
    r = main(args.base, Path(args.out))
    for screen, violations in r.items():
        print(screen, [(v["id"], v["impact"], len(v["nodes"])) for v in violations] or "no violations")
