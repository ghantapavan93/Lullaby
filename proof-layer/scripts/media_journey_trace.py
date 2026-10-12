"""Trace purchase -> media -> report through the real API on a fresh, isolated
reviewer instance, and check each link against the rows it should leave.

    python scripts/media_journey_trace.py

Steps, as a parent and the store would take them:
plan ended -> clip refused -> store renewal -> access verified -> clip opened ->
worker analyses -> bytes streamed -> page rendered -> caregiver reports.

Writes artifacts/growth/media-journey-trace.json: per link, the code that
does it, the rows found, and whether the link holds. A link that has no
persisted evidence is reported as missing, never inferred. The instance is
destroyed at the end. All data is synthetic.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
import devdb  # noqa: E402
import reviewer  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "artifacts" / "growth" / "media-journey-trace.json"


def sql(db: str, query: str) -> list[list[str]]:
    out = devdb.run(devdb.pg_bin("psql"), "-h", "127.0.0.1", "-p", devdb.PORT, "-U", "proof", "-d", db,
                    "-AtF", "|", "-c", query).stdout
    return [line.split("|") for line in out.splitlines() if line]


def main() -> int:
    inst = reviewer.create(minutes=20)
    db = inst["databases"][0]
    base = inst["url"]
    try:
        def auth(member):
            return {"Authorization": "Bearer " + httpx.post(base + "/v1/dev/sessions", json={"member_id": member},
                                                            timeout=30).json()["token"]}
        parent, caregiver = auth("mem_parent_a"), auth("mem_caregiver_b")
        httpx.post(base + "/v1/dev/access-scenario", headers=parent, json={"scenario": "evidence_journey"},
                   timeout=60).raise_for_status()
        asset_id = httpx.get(base + "/v1/video/assets", headers=parent, timeout=30).json()["assets"][0]["id"]
        refused = httpx.get(base + f"/v1/video/assets/{asset_id}", headers=parent, timeout=30)
        start = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=5))
        httpx.post(base + "/v1/dev/purchase-evidence", headers=parent, json={
            "channel": "apple", "external_ref": "apple-txn-1505", "plan_code": "milestones",
            "provider_event": "DID_RENEW", "provider_version": 2, "starts_at": start.isoformat(), "days": 365},
            timeout=60).raise_for_status()
        verified = httpx.post(base + "/v1/access/verify", headers=parent, json={"device_id": "dev_cam_demo"},
                              timeout=30).json()["check"]["status"]
        httpx.post(base + "/v1/dev/video/worker", headers=parent, timeout=30).raise_for_status()
        for _ in range(120):
            run = httpx.get(base + "/v1/video/assets", headers=parent, timeout=30).json()["assets"][0]["run"]
            if run and run["state"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(1)
        view = httpx.get(base + f"/v1/video/assets/{asset_id}", headers=parent, timeout=30).json()
        with httpx.Client(base_url=base, timeout=30) as browser:                 # cookie-carrying client
            browser.post(f"/v1/video/assets/{asset_id}/media-link", headers=parent).raise_for_status()
            streamed = browser.get(f"/v1/video/media/{asset_id}", headers={"Range": "bytes=0-1023"})
        httpx.post(base + "/v1/analytics", headers=parent, timeout=30, json={
            "event_id": f"trace-{uuid.uuid4().hex[:12]}", "name": "evidence_review_opened",
            "properties": {"device_id": "dev_cam_demo", "asset_id": asset_id}})
        moving = next(o for o in view["observations"] if o["kind"] == "movement")
        report = httpx.post(base + f"/v1/video/assets/{asset_id}/annotations", timeout=30,
                            headers=caregiver | {"Idempotency-Key": f"trace-{uuid.uuid4().hex[:12]}"},
                            json={"label": "infant_still", "t_start": moving["t_start"], "t_end": moving["t_end"],
                                  "expected_revision": view["evidence_revision"]})
        metrics = httpx.get(base + "/v1/access/metrics", headers=parent, timeout=30).json()

        def events(name):
            return sql(db, f"select properties::text from analytics_events where name = '{name}' order by occurred_at")

        links = [
            ("Synthetic purchase (store renewal)", "app/purchase_evidence.py",
             sql(db, "select status, provider_event, provider_version from purchase_evidence "
                     "where external_ref = 'apple-txn-1505' order by provider_version")),
            ("Evidence reconciled to the camera", "app/provider_lifecycle.py, app/claims.py",
             sql(db, "select device_id, event_status, plan_code from entitlement_claims "
                     "where external_ref = 'apple-txn-1505' order by received_at")),
            ("Entitlement effective (verified check)", "app/access.py:verify",
             sql(db, "select status, plan_code, usable_features::text from access_checks order by created_at desc limit 1")),
            ("Feature eligibility (video gate)", "app/video_evidence.py:require_gate",
             events("evidence_access_checked")),
            ("Media bytes authorised", "app/main.py:video_media",
             [[str(streamed.status_code), str(len(streamed.content))]] if streamed.status_code == 206 else []),
            ("Analysis completed by the worker", "app/video_evidence.py:process",
             sql(db, "select state, processor_version, attempts, elapsed_ms from video_analysis_runs")),
            ("Feature opened (page rendered)", "static/app.js:trackRecordedView", events("evidence_review_opened")),
            ("Caregiver report kept beside the measurement", "app/video_evidence.py:annotate",
             sql(db, "select a.revision, a.label, m.display_name from video_annotations a "
                     "join members m on m.id = a.member_id")),
            ("Growth event for the report", "app/growth.py", events("video_annotation_added")),
        ]
        rows = [{"link": name, "code": code, "rows": found, "holds": bool(found)} for name, code, found in links]
        gaps = [
            {"gap": "The access funnel counts usable access checks only, not video-gate passes",
             "evidence": {"time_to_usable": metrics["time_to_usable"]["confirmed_to_usable"],
                          "video": metrics.get("video")},
             "consequence": "Counted as usable here only because this trace ran an explicit access check. A parent "
                            "who opens the clip directly passes the video gate without one, and would show as "
                            "unresolved in the access funnel."},
            {"gap": "A refused read leaves no row",
             "gate_refused_before_renewal": refused.status_code == 403 and refused.json().get("code") == "FEATURE_NOT_IN_PLAN",
             "checked_refused_in_funnel": metrics.get("video", {}).get("access", {}).get("checked_refused"),
             "consequence": "Reads write nothing by design, so the refusal before the renewal is not in the funnel: "
                            "refusals are counted only when a command (a media link, a report) is refused."},
            {"caveat": "The evidence_review_opened event here was sent by this script; the page's own render path "
                       "is checked by tests/e2e/test_browser.py::test_recorded_evidence_after_the_renewal_in_the_browser."},
            {"not_shown": "A finished analysis of one sample clip says nothing about long-term value; no retention "
                          "or repeat-use signal exists here."},
        ]
        result = {"generated_at": datetime.now(timezone.utc).isoformat(), "instance": inst["id"],
                  "verified_status": verified, "report_status": report.status_code,
                  "links": rows, "gaps": gaps, "all_links_hold": all(r["holds"] for r in rows)}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(result, indent=1), encoding="utf-8")
        for r in rows:
            print("HOLDS  " if r["holds"] else "MISSING", r["link"], "|", r["rows"][:2])
        print("verified:", verified, "| report:", report.status_code, "| gate refused before renewal:",
              gaps[1]["gate_refused_before_renewal"], "| refusals in funnel:", gaps[1]["checked_refused_in_funnel"])
        return 0 if result["all_links_hold"] else 1
    finally:
        reviewer.destroy(inst["id"])


if __name__ == "__main__":
    sys.exit(main())
