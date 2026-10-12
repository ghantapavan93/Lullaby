"""Retry the Always Free A1 launch until capacity appears, within a time cap.

    python deploy/oci/capacity_retry.py --profile nanit --minutes 180 --every 5

Run provision.py once first: it builds the network and checks the Always Free
bounds. This loop then makes only the launch calls (one per availability
domain), so it stays under Oracle's request rate limits: everything else is
looked up once. The browser-signed session is refreshed every 30 minutes.
Stops at the first success, or at the cap. A failed launch creates nothing.
After a success, run provision.py again to record the address.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import provision  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="nanit")
    ap.add_argument("--minutes", type=float, default=180)
    ap.add_argument("--every", type=float, default=5, help="minutes between rounds")
    ap.add_argument("--ocpus", type=int, default=4)
    a = ap.parse_args()
    oci = provision.Oci(a.profile)
    tenancy = provision.config_value(a.profile, "tenancy")
    ads = [ad["name"] for ad in oci("iam", "availability-domain", "list", "--compartment-id", tenancy)["data"]]
    vcn = provision.find(oci("network", "vcn", "list", "--compartment-id", tenancy, "--all")["data"], provision.NAME)
    subnet = provision.find(oci("network", "subnet", "list", "--compartment-id", tenancy, "--vcn-id", vcn["id"])["data"],
                            provision.NAME)
    image = oci("compute", "image", "list", "--compartment-id", tenancy, "--operating-system", "Canonical Ubuntu",
                "--operating-system-version", "24.04", "--shape", "VM.Standard.A1.Flex",
                "--sort-by", "TIMECREATED", "--sort-order", "DESC", "--limit", "1")["data"][0]
    print(f"{time.strftime('%H:%M:%S')} retrying {a.ocpus} OCPU in {len(ads)} ADs with {image['display-name']}",
          flush=True)
    deadline = time.monotonic() + a.minutes * 60
    # Refresh at once and then every 20 minutes: a session lasts an hour from when it
    # was last refreshed, not from when this loop started (a 30-minute timer counted
    # from the loop's start once let it lapse).
    last_refresh = float("-inf")
    round_ = 0
    while time.monotonic() < deadline:
        round_ += 1
        if time.monotonic() - last_refresh > 20 * 60:
            r = subprocess.run([provision.OCI, "session", "refresh", "--profile", a.profile], capture_output=True, text=True)
            if "no longer valid" in r.stdout + r.stderr:
                print(f"{time.strftime('%H:%M:%S')} SESSION EXPIRED: run `oci session authenticate`; stopping", flush=True)
                return 2
            last_refresh = time.monotonic()
        reasons = []
        # Ask first (one light call per AD): launch only where Oracle reports room.
        shape = [{"instanceShape": "VM.Standard.A1.Flex",
                  "instanceShapeConfig": {"ocpus": a.ocpus, "memoryInGBs": a.ocpus * 6}}]
        open_ads = []
        for ad in ads:
            rep = oci("compute", "compute-capacity-report", "create", "--compartment-id", tenancy,
                      "--availability-domain", ad, "--shape-availabilities", json.dumps(shape), check=False)
            if "error" in rep:
                status = "report-error"
            else:
                status = (rep.get("data") or {}).get("shape-availabilities", [{}])[0].get("availability-status")
            if status == "AVAILABLE":
                open_ads.append(ad)
            else:
                reasons.append(f"{ad[-4:]} {status}")
        for ad in open_ads:
            r = oci("compute", "instance", "launch", "--availability-domain", ad, "--compartment-id", tenancy,
                    "--shape", "VM.Standard.A1.Flex",
                    "--shape-config", json.dumps({"ocpus": a.ocpus, "memoryInGBs": a.ocpus * 6}),
                    "--image-id", image["id"], "--subnet-id", subnet["id"], "--assign-public-ip", "true",
                    "--display-name", provision.NAME, "--boot-volume-size-in-gbs", str(provision.BOOT_GB),
                    "--ssh-authorized-keys-file", str(provision.SSH_KEY), "--no-retry", check=False)
            if "error" not in r:
                print(f"{time.strftime('%H:%M:%S')} round {round_}: LAUNCHED in {ad[-4:]} with {a.ocpus} OCPU: "
                      f"{r['data']['id']}", flush=True)
                return 0
            err = r["error"]
            reasons.append("capacity" if "Out of host capacity" in err else "rate-limited" if "TooManyRequests" in err
                           else err.strip().splitlines()[-1][:120])
            time.sleep(10)                       # spread the three calls out
        print(f"{time.strftime('%H:%M:%S')} round {round_}: {', '.join(reasons)}", flush=True)
        time.sleep(a.every * 60)
    print(f"{time.strftime('%H:%M:%S')} gave up at the time cap; no instance was created", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
