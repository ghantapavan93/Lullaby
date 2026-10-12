"""Two reviewer instances side by side (scripts/reviewer.py), as real processes
on their own databases. What one reviewer does must not reach the other:

- both start from the same synthetic state;
- a scenario change in one leaves the other's state and revision unchanged;
- a reset in one while the other is being read never fails or changes a read;
- a session token or a clip id from one is refused by the other;
- an instance stops answering at its expiry, and reaping it removes its
  process, databases and media folder;
- an instance listens on loopback only and never uses the real infant clip.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from ..conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import devdb  # noqa: E402
import reviewer  # noqa: E402


@pytest.fixture(scope="module")
def pair():
    a = reviewer.create(minutes=15)
    try:
        b = reviewer.create(minutes=15)
    except BaseException:
        reviewer.destroy(a["id"])
        raise
    yield a, b
    reviewer.destroy(a["id"])
    reviewer.destroy(b["id"])


def session(inst, member="mem_parent_a"):
    token = httpx.post(inst["url"] + "/v1/dev/sessions", json={"member_id": member}, timeout=30).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def access(inst, headers):
    body = httpx.get(inst["url"] + "/v1/access", headers=headers, timeout=30).json()
    return body["state_revision"], sorted((c["device"]["id"], c["status"]) for c in body["cameras"])


def databases():
    out = devdb.run(devdb.pg_bin("psql"), "-h", "127.0.0.1", "-p", devdb.PORT, "-U", "proof", "-d", "postgres",
                    "-Atc", "select datname from pg_database").stdout
    return set(out.split())


def test_two_instances_start_alike_and_a_change_in_one_stays_there(pair):
    a, b = pair
    ha, hb = session(a), session(b)
    assert access(a, ha)[1] == access(b, hb)[1]                  # the same synthetic starting point
    before_b = access(b, hb)
    httpx.post(a["url"] + "/v1/dev/access-scenario", headers=ha, json={"scenario": "paid_not_active"},
               timeout=60).raise_for_status()
    assert access(a, ha) != before_b                             # A changed
    assert access(b, hb) == before_b                             # B didn't


def test_a_reset_in_one_instance_never_disturbs_reads_in_the_other(pair):
    a, b = pair
    ha, hb = session(a), session(b)
    expected = access(b, hb)
    seen, stop = [], threading.Event()

    def read_b():
        while not stop.is_set():
            r = httpx.get(b["url"] + "/v1/access", headers=hb, timeout=30)
            seen.append((r.status_code, r.json()["state_revision"] if r.status_code == 200 else None))

    readers = [threading.Thread(target=read_b) for _ in range(3)]
    for t in readers:
        t.start()
    try:
        for key in ("plan_ended", "billing_retry", "paid_not_active"):
            httpx.post(a["url"] + "/v1/dev/access-scenario", headers=ha, json={"scenario": key},
                       timeout=60).raise_for_status()
    finally:
        stop.set()
        for t in readers:
            t.join(timeout=30)
    assert seen and all(status == 200 and rev == expected[0] for status, rev in seen), set(seen)


def test_tokens_and_clip_ids_do_not_cross_instances(pair):
    a, b = pair
    ha, hb = session(a), session(b)
    assert httpx.get(b["url"] + "/v1/access", headers=ha, timeout=30).status_code == 401   # A's token, B's server
    clip = httpx.post(a["url"] + "/v1/dev/video/sample", headers=ha, json={}, timeout=120)
    assert clip.status_code == 200, clip.text
    asset = clip.json()["asset"]
    assert asset["source_kind"] == "staged_sample"               # never the real infant clip, even if it's on disk
    assert httpx.get(b["url"] + f"/v1/video/assets/{asset['id']}", headers=hb, timeout=30).status_code == 404
    assert httpx.get(b["url"] + "/v1/video/assets", headers=hb, timeout=30).json()["assets"] == []
    assert not any(Path(b["media_root"]).rglob("*.webm"))         # nothing of A's on B's disk


def test_an_instance_listens_on_loopback_only(pair):
    a, _ = pair
    host_ip = socket.gethostbyname(socket.gethostname())
    if host_ip.startswith("127."):
        pytest.skip("this machine has no non-loopback address to try")
    with socket.socket() as s:
        s.settimeout(2)
        assert s.connect_ex((host_ip, a["port"])) != 0


def test_an_instance_expires_and_reaping_removes_everything():
    c = reviewer.create(minutes=0.05)                             # three seconds
    try:
        time.sleep(4)
        r = httpx.get(c["url"] + "/v1/dev/accounts", timeout=10)
        assert r.status_code == 410 and r.json()["code"] == "REVIEW_EXPIRED"
        assert c["id"] in reviewer.reap()
        assert c["id"] not in reviewer._load()
        assert not set(c["databases"]) & databases()
        assert not Path(c["media_root"]).exists()
        with pytest.raises(httpx.HTTPError):
            httpx.get(c["url"] + "/v1/dev/accounts", timeout=3)
    finally:
        reviewer.destroy(c["id"])
