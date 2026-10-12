"""5) axe-core (WCAG 2.0/2.1/2.2 A+AA) on the screens scripts/a11y_audit.py covers,
plus the Data & proof explorer, two-screen mode, the correction sheet, privacy and 404.
Adapted copy for port 8711; the original script is unchanged."""
import json

from playwright.sync_api import sync_playwright

from common import BASE, dump, reset

AXE = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def audit(page):
    page.add_script_tag(url=AXE)
    v = page.evaluate("async (tags) => (await axe.run(document, {runOnly: {type: 'tag', values: tags}})).violations", TAGS)
    return [{"id": x["id"], "impact": x["impact"], "help": x["help"], "nodes": [n["target"] for n in x["nodes"]][:6]} for x in v]


def main():
    report = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scheme in ("light", "dark"):
            for width in (375, 1280):
                reset()
                ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
                page = ctx.new_page()
                k = f"{scheme}-{width}"
                page.goto(f"{BASE}/#/briefing"); page.wait_for_selector(".rev-badge")
                report[f"{k}-briefing"] = audit(page)
                page.goto(f"{BASE}/#/changes"); page.wait_for_selector(".member-grid")
                report[f"{k}-changes"] = audit(page)
                page.goto(f"{BASE}/#/features"); page.get_by_role("button", name="Check availability").click()
                page.wait_for_selector(".offer-card"); report[f"{k}-features-offer"] = audit(page)
                page.goto(f"{BASE}/#/briefing"); page.wait_for_selector(".rev-badge")
                page.get_by_role("button", name="Correct final wake").click(); page.wait_for_selector("#correction-sheet[open]")
                report[f"{k}-correction-sheet"] = audit(page); page.keyboard.press("Escape")
                nav = ".tab-bar" if width < 1024 else "#side-nav"
                page.locator(f"{nav} [data-action=xray-open]").click(); page.wait_for_selector("#xray.open table")
                report[f"{k}-xray-live"] = audit(page)
                page.locator("#xray [data-action=xray-tab][data-tab=proof]").click(); page.wait_for_selector(".proof-nav")
                for tab in ("flows", "schema", "stress", "tests"):
                    page.locator(f"#xray [data-tab={tab}]").click(); page.wait_for_selector(f"#xray [data-tab={tab}][aria-checked=true]")
                    report[f"{k}-proof-{tab}"] = audit(page)
                page.keyboard.press("Escape")
                if width >= 1024:
                    page.locator("#side-nav .dev-panel summary").click()
                    page.locator("#side-nav [data-action=dev-two]").click(); page.wait_for_selector(".screens.two")
                    report[f"{k}-two-screens-devpanel"] = audit(page)
                page.goto(f"{BASE}/privacy"); report[f"{k}-privacy"] = audit(page)
                page.goto(f"{BASE}/nope"); report[f"{k}-404"] = audit(page)
                ctx.close()
        browser.close()
    dump("axe-report.json", report)
    bad = {k: [(v["id"], v["impact"], len(v["nodes"])) for v in vs] for k, vs in report.items() if vs}
    print("screens", len(report), "with violations", len(bad))
    print(json.dumps(bad, indent=1))


if __name__ == "__main__":
    main()
