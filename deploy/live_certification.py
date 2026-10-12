"""Ask a deployed copy whether its test report describes the code it runs.

    python deploy/live_certification.py https://lullaby-rho.vercel.app [--wait 900]

Opens one workspace, reads the certification the live Growth Lab shows
(`GET /v1/proof`), prints the verdict, the running code digest and the
report's, then ends the workspace. With --wait it re-reads in the same
workspace until the verdict is CERTIFIED or the time runs out (a deploy may
still be rolling out); one workspace for the whole wait, so the per-address
limit on new workspaces isn't spent. Exit code 0 only for CERTIFIED.
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx


def read(c: httpx.Client) -> dict:
    token = c.post("/v1/dev/sessions", json={"member_id": "mem_parent_a"}).json()["token"]
    cert = c.get("/v1/proof", headers={"Authorization": "Bearer " + token}).json()["certification"]
    build = cert.get("build", {})
    return {"verdict": cert["verdict"], "passed": sum(1 for i in cert["invariants"] if i["state"] == "passed"),
            "of": len(cert["invariants"]), "code": build.get("code_digest"),
            "report": build.get("report_code_digest"), "ran_at": build.get("report_generated_at")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--wait", type=float, default=0, help="seconds to keep re-reading until CERTIFIED")
    a = ap.parse_args()
    deadline = time.monotonic() + a.wait
    with httpx.Client(base_url=a.base.rstrip("/"), timeout=120) as c:
        result: dict = {"verdict": "no answer"}
        try:
            while True:
                try:
                    r = c.post("/v1/workspace")       # keeps the same workspace while its cookie is valid
                    if r.status_code in (200, 201):
                        result = read(c)
                    else:
                        result = {"verdict": f"no workspace ({r.status_code})"}
                except (httpx.HTTPError, KeyError, ValueError) as exc:
                    result = {"verdict": f"no answer ({type(exc).__name__})"}   # a deploy restarting the backend
                print(result, flush=True)
                if result["verdict"] == "CERTIFIED" or time.monotonic() > deadline:
                    break
                time.sleep(30)
        finally:
            try:
                c.delete("/v1/workspace")
            except httpx.HTTPError:
                pass
    return 0 if result["verdict"] == "CERTIFIED" else 1


if __name__ == "__main__":
    sys.exit(main())
