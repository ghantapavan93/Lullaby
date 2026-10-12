"""Provision the Proof Layer's one VM on Oracle Cloud, inside Always Free.

    python deploy/oci/provision.py --profile nanit [--ssh-from 203.0.113.7] [--open-web]

Uses the OCI CLI with a browser-signed session (`oci session authenticate`),
so no API key or password is handled here. Idempotent: every resource is
found by its display name before anything is created.

Creates, in the tenancy's root compartment and home region:
- VCN 10.0.0.0/16 with an internet gateway and a default route;
- a security list: SSH from one address only (--ssh-from), ICMP for path MTU;
  ports 80 and 443 only with --open-web, which needs the owner's approval;
- one public subnet;
- one VM.Standard.A1.Flex instance: 4 OCPU, 24 GB, a 50 GB boot volume,
  Canonical Ubuntu 24.04 aarch64, the deploy SSH key.

Always Free bounds checked before launching: the A1 core and memory limits,
and that no other A1 instance already uses them. It refuses anything else.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

NAME = "nanit-proof-layer"
# On Windows the CLI lives at a short path: its help files exceed the 260-character limit otherwise.
OCI = os.environ.get("OCI_CLI") or (r"C:\oci-cli\Scripts\oci.exe" if os.name == "nt" else "oci")
SSH_KEY = Path.home() / ".ssh" / "nanit_oci.pub"
FREE_OCPUS, FREE_MEMORY_GB, BOOT_GB = 4, 24, 50


class Oci:
    def __init__(self, profile: str):
        self.profile = profile

    def __call__(self, *args: str, check: bool = True) -> dict:
        cmd = [OCI, "--auth", "security_token", "--profile", self.profile, "--output", "json", *args]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            if check:
                raise RuntimeError(f"oci {' '.join(args[:3])} failed: {r.stderr.strip()[:800]}")
            return {"error": r.stderr}
        return json.loads(r.stdout) if r.stdout.strip() else {"data": []}   # an empty list prints nothing


def config_value(profile: str, key: str) -> str:
    current = None
    for line in (Path.home() / ".oci" / "config").read_text().splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        elif current == profile and line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{key} not found for profile {profile} in ~/.oci/config")


def find(items: list[dict], name: str) -> dict | None:
    return next((i for i in items if i.get("display-name") == name and i.get("lifecycle-state") not in
                 ("TERMINATED", "TERMINATING")), None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="nanit")
    ap.add_argument("--ssh-from", help="the one address allowed to SSH in (default: this machine's public IP)")
    ap.add_argument("--open-web", action="store_true", help="allow 80/443 from anywhere (needs the owner's approval)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ocpus", type=int, default=FREE_OCPUS, help="1-4: smaller shapes find capacity more often")
    a = ap.parse_args()
    oci = Oci(a.profile)
    tenancy = config_value(a.profile, "tenancy")
    region = config_value(a.profile, "region")
    ssh_from = a.ssh_from or subprocess.run(["curl", "-s", "https://api.ipify.org"], capture_output=True,
                                            text=True).stdout.strip()
    print(f"tenancy …{tenancy[-8:]}  region {region}  ssh from {ssh_from}/32  web {'OPEN' if a.open_web else 'closed'}")

    # Always Free bounds, before anything is created.
    ads = [ad["name"] for ad in oci("iam", "availability-domain", "list", "--compartment-id", tenancy)["data"]]
    limits = {}
    for limit in ("standard-a1-core-count", "standard-a1-memory-count"):
        values = oci("limits", "value", "list", "--compartment-id", tenancy, "--service-name", "compute",
                     "--name", limit, "--all")["data"]
        limits[limit] = sum(v.get("value") or 0 for v in values)
    instances = oci("compute", "instance", "list", "--compartment-id", tenancy, "--all")["data"]
    a1 = [i for i in instances if i["shape"] == "VM.Standard.A1.Flex" and i["lifecycle-state"] not in ("TERMINATED",)]
    used = sum(i["shape-config"]["ocpus"] for i in a1 if i["display-name"] != NAME)
    print(f"A1 limits {limits}  other A1 OCPUs in use {used}")
    if not 1 <= a.ocpus <= FREE_OCPUS or used + a.ocpus > FREE_OCPUS:
        raise SystemExit("another A1 instance already uses the Always Free allowance; refusing to exceed it")
    if a.dry_run:
        return 0

    vcn = find(oci("network", "vcn", "list", "--compartment-id", tenancy, "--all")["data"], NAME)
    if vcn is None:
        vcn = oci("network", "vcn", "create", "--compartment-id", tenancy, "--display-name", NAME,
                  "--cidr-blocks", '["10.0.0.0/16"]', "--dns-label", "nanitpl", "--wait-for-state", "AVAILABLE")["data"]
    igw = find(oci("network", "internet-gateway", "list", "--compartment-id", tenancy, "--vcn-id", vcn["id"])["data"], NAME)
    if igw is None:
        igw = oci("network", "internet-gateway", "create", "--compartment-id", tenancy, "--vcn-id", vcn["id"],
                  "--display-name", NAME, "--is-enabled", "true", "--wait-for-state", "AVAILABLE")["data"]
    oci("network", "route-table", "update", "--rt-id", vcn["default-route-table-id"], "--force", "--route-rules",
        json.dumps([{"destination": "0.0.0.0/0", "destinationType": "CIDR_BLOCK", "networkEntityId": igw["id"]}]))
    ingress = [{"protocol": "6", "source": f"{ssh_from}/32", "tcpOptions": {"destinationPortRange": {"min": 22, "max": 22}}},
               {"protocol": "1", "source": "0.0.0.0/0", "icmpOptions": {"type": 3, "code": 4}}]
    if a.open_web:
        ingress += [{"protocol": "6", "source": "0.0.0.0/0", "tcpOptions": {"destinationPortRange": {"min": p, "max": p}}}
                    for p in (80, 443)]
    egress = [{"protocol": "all", "destination": "0.0.0.0/0"}]
    sl = find(oci("network", "security-list", "list", "--compartment-id", tenancy, "--vcn-id", vcn["id"])["data"], NAME)
    if sl is None:
        sl = oci("network", "security-list", "create", "--compartment-id", tenancy, "--vcn-id", vcn["id"],
                 "--display-name", NAME, "--ingress-security-rules", json.dumps(ingress),
                 "--egress-security-rules", json.dumps(egress), "--wait-for-state", "AVAILABLE")["data"]
    else:
        oci("network", "security-list", "update", "--security-list-id", sl["id"], "--force",
            "--ingress-security-rules", json.dumps(ingress), "--egress-security-rules", json.dumps(egress))
    subnet = find(oci("network", "subnet", "list", "--compartment-id", tenancy, "--vcn-id", vcn["id"])["data"], NAME)
    if subnet is None:
        subnet = oci("network", "subnet", "create", "--compartment-id", tenancy, "--vcn-id", vcn["id"],
                     "--display-name", NAME, "--cidr-block", "10.0.0.0/24", "--dns-label", "web",
                     "--security-list-ids", json.dumps([sl["id"]]), "--route-table-id", vcn["default-route-table-id"],
                     "--wait-for-state", "AVAILABLE")["data"]

    instance = find(instances, NAME)
    if instance is None:
        image = oci("compute", "image", "list", "--compartment-id", tenancy, "--operating-system", "Canonical Ubuntu",
                    "--operating-system-version", "24.04", "--shape", "VM.Standard.A1.Flex",
                    "--sort-by", "TIMECREATED", "--sort-order", "DESC", "--limit", "1")["data"][0]
        print("image", image["display-name"])
        last = None
        for ad in ads:   # A1 capacity varies by availability domain
            r = oci("compute", "instance", "launch", "--availability-domain", ad, "--compartment-id", tenancy,
                    "--shape", "VM.Standard.A1.Flex",
                    "--shape-config", json.dumps({"ocpus": a.ocpus, "memoryInGBs": a.ocpus * 6}),
                    "--image-id", image["id"], "--subnet-id", subnet["id"], "--assign-public-ip", "true",
                    "--display-name", NAME, "--boot-volume-size-in-gbs", str(BOOT_GB),
                    "--ssh-authorized-keys-file", str(SSH_KEY), "--wait-for-state", "RUNNING",
                    "--no-retry", check=False)   # fail fast on "Out of host capacity"; the caller decides when to retry
            if "error" not in r:
                instance = r["data"]
                break
            last = r["error"]
            print(f"{ad}: {last.strip()[:200]}")
        if instance is None:
            raise SystemExit(f"no availability domain had A1 capacity: {last[:300] if last else ''}")
    vnics = oci("compute", "instance", "list-vnics", "--instance-id", instance["id"])["data"]
    public_ip = vnics[0].get("public-ip")
    state = {"instance": instance["id"], "region": region, "public_ip": public_ip, "ssh_from": ssh_from,
             "web_open": a.open_web, "shape": f"VM.Standard.A1.Flex {a.ocpus} OCPU {a.ocpus * 6} GB",
             "boot_gb": BOOT_GB}
    out = Path(__file__).with_name("state.json")
    out.write_text(json.dumps(state, indent=1), encoding="utf-8")
    print(json.dumps(state, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
