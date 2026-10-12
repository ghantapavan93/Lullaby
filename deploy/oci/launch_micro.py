"""Launch the stopgap backend: one Always Free AMD micro VM (VM.Standard.E2.1.Micro,
1/8 OCPU, 1 GB RAM) in the network provision.py built, until A1 capacity appears.

    python deploy/oci/launch_micro.py --profile nanit

Always Free allows two of these per tenancy; this makes one, named
nanit-proof-layer-micro, with a 50 GB boot volume (inside the 200 GB free
block storage). Idempotent. Writes deploy/oci/state-micro.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import provision  # noqa: E402

NAME = provision.NAME + "-micro"
SHAPE = "VM.Standard.E2.1.Micro"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="nanit")
    a = ap.parse_args()
    oci = provision.Oci(a.profile)
    tenancy = provision.config_value(a.profile, "tenancy")
    instances = oci("compute", "instance", "list", "--compartment-id", tenancy, "--all")["data"]
    micros = [i for i in instances if i["shape"] == SHAPE and i["lifecycle-state"] != "TERMINATED"]
    instance = provision.find(instances, NAME)
    if instance is None and len(micros) >= 2:
        raise SystemExit("two micro instances already exist: the Always Free allowance is used")
    if instance is None:
        vcn = provision.find(oci("network", "vcn", "list", "--compartment-id", tenancy, "--all")["data"], provision.NAME)
        subnet = provision.find(oci("network", "subnet", "list", "--compartment-id", tenancy, "--vcn-id", vcn["id"])["data"],
                                provision.NAME)
        image = oci("compute", "image", "list", "--compartment-id", tenancy, "--operating-system", "Canonical Ubuntu",
                    "--operating-system-version", "24.04", "--shape", SHAPE, "--sort-by", "TIMECREATED",
                    "--sort-order", "DESC", "--limit", "1")["data"][0]
        print("image", image["display-name"])
        ads = [ad["name"] for ad in oci("iam", "availability-domain", "list", "--compartment-id", tenancy)["data"]]
        last = None
        for ad in ads:   # the micro shape is offered in only some availability domains
            r = oci("compute", "instance", "launch", "--availability-domain", ad, "--compartment-id", tenancy,
                    "--shape", SHAPE, "--image-id", image["id"], "--subnet-id", subnet["id"],
                    "--assign-public-ip", "true", "--display-name", NAME,
                    "--boot-volume-size-in-gbs", str(provision.BOOT_GB),
                    "--ssh-authorized-keys-file", str(provision.SSH_KEY), "--wait-for-state", "RUNNING",
                    "--no-retry", check=False)
            if "error" not in r:
                instance = r["data"]
                break
            last = r["error"]
            print(f"{ad[-4:]}: {last.strip().splitlines()[-2][:160] if last.strip() else last}")
        if instance is None:
            raise SystemExit(f"no availability domain launched the micro shape: {(last or '')[-300:]}")
    vnics = oci("compute", "instance", "list-vnics", "--instance-id", instance["id"])["data"]
    state = {"instance": instance["id"], "public_ip": vnics[0].get("public-ip"), "shape": SHAPE,
             "boot_gb": provision.BOOT_GB}
    (HERE / "state-micro.json").write_text(json.dumps(state, indent=1), encoding="utf-8")
    print(json.dumps(state, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
