"""Access funnel metrics: derived from stored records, labelled synthetic, scoped
to one family, and never naming a purchase."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Row

from app import access_metrics

from .conftest import CAREGIVER, PARENT

T = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T + timedelta(seconds=seconds)


def bound(channel: str, ref: str, device: str = "cam", state: str = "verified") -> dict:
    return {"channel": channel, "external_ref": ref, "state": state,
            "binding": {"bound": True, "elsewhere": False, "device_id": device}}


def ev(channel, ref, status, version, seconds):
    return Row(channel=channel, external_ref=ref, status=status, provider_version=version, received_at=at(seconds))


def test_durations_come_from_record_times():
    evidence = [ev("web", "w1", "verified", 1, 0),
                ev("web", "w2", "verified", 1, 5),
                ev("web", "r1", "verified", 1, 0), ev("web", "r1", "refunded", 2, 9),
                ev("apple", "a1", "verified", 1, 0), ev("apple", "a1", "grace", 2, 100), ev("apple", "a1", "verified", 3, 160),
                ev("apple", "a2", "verified", 1, 0), ev("apple", "a2", "grace", 2, 10), ev("apple", "a2", "verified", 3, 20),
                ev("apple", "a2", "billing_retry", 4, 30),
                ev("apple", "a3", "verified", 1, 0), ev("apple", "a3", "verified", 3, 50), ev("apple", "a3", "grace", 2, 70)]
    binds = [Row(channel="web", external_ref="w1", created_at=at(30))]
    # a1 reached its camera through a claim, not a connect command (a renewal, a scenario).
    claims = [Row(channel="apple", external_ref="a1", event_status="active", received_at=at(1))]
    checks = [Row(device_id="cam", status="PURCHASE_PENDING", created_at=at(31)),
              Row(device_id="cam", status=access_metrics.USABLE, created_at=at(40)),
              Row(device_id="cam2", status=access_metrics.USABLE, created_at=at(170))]
    unbound = {"bound": False, "elsewhere": False, "device_id": None}
    m = access_metrics.funnel(evidence=evidence, binds=binds, claims=claims, checks=checks,
                              purchases=[bound("web", "w1"), bound("apple", "a1", "cam2"),
                                         {"channel": "web", "external_ref": "w2", "state": "verified", "binding": unbound},
                                         {"channel": "web", "external_ref": "r1", "state": "refunded", "binding": unbound},
                                         {"channel": "apple", "external_ref": "a2", "state": "billing_retry", "binding": unbound},
                                         {"channel": "apple", "external_ref": "a3", "state": "verified", "binding": unbound}],
                              rejections=[{"code": "ALREADY_ENTITLED"}, {"code": "ALREADY_ENTITLED"}],
                              now=at(600))
    assert m["synthetic"] is True and "not a customer rate" in m["caveat"]
    journeys = {j["purchase"]: j for j in m["time_to_usable"]["journeys"]}
    web = next(j for j in journeys.values() if j["channel"] == "web" and j["connected"])
    assert (web["confirmed_to_connected_s"], web["connected_to_usable_s"], web["confirmed_to_usable_s"]) == (30, 10, 40)
    via_claim = next(j for j in journeys.values() if j["connected_via"] == "claim")
    assert via_claim["confirmed_to_usable_s"] == 170 and via_claim["unresolved_for_s"] is None
    # w2 and a3 are unresolved; the refund is not "waiting"; a2's failing payment is counted under billing.
    assert m["unresolved"] == {"count": 2, "not_connected": 2, "oldest_s": 600}
    recoveries = {r["channel"] + str(i): r for i, r in enumerate(m["billing_recovery"]["purchases"])}
    by_failure = sorted(m["billing_recovery"]["purchases"], key=lambda r: r["purchase"])
    a1, a2, a3 = by_failure
    assert a1["grace"] and a1["failure_to_recovery_s"] == 60 and a1["recovery_to_usable_check_s"] == 10
    assert a2["still_failing"] is True and a2["failure_to_recovery_s"] is None      # failed again after recovering
    assert a3["out_of_order"] is True and a3["failure_to_recovery_s"] is None      # late delivery, not a negative time
    assert m["billing_recovery"]["recovered"] == 2 and m["billing_recovery"]["still_failing"] == 1
    assert m["refused_before_payment"] == {"ALREADY_ENTITLED": 2}
    assert m["verification_outcomes"] == {"checks": 3, "usable": 2,
                                          "by_status": {"ACTIVE_FEATURE_USABLE": 2, "PURCHASE_PENDING": 1}}
    assert recoveries                                                               # numbered, never named


def test_the_endpoint_reads_this_familys_records_and_names_no_purchase(client, demo):
    assert client.post("/v1/dev/access-scenario", headers=PARENT, json={"scenario": "billing_recovered"}).status_code == 200
    r = client.get("/v1/access/metrics", headers=PARENT)
    assert r.status_code == 200
    m = r.json()
    assert m["synthetic"] is True
    assert m["billing_recovery"]["recovered"] == 1 and m["billing_recovery"]["still_failing"] == 0
    assert m["billing_recovery"]["purchases"][0]["recovery_to_usable_check_s"] is not None
    assert "apple-txn" not in r.text and "dev_cam" not in r.text      # numbered, never named
    assert client.get("/v1/access/metrics", headers=CAREGIVER).status_code == 403


def test_a_scenario_connected_by_claim_is_not_reported_unresolved(client, demo):
    client.post("/v1/dev/access-scenario", headers=PARENT, json={"scenario": "billing_recovered"})
    m = client.get("/v1/access/metrics", headers=PARENT).json()
    assert m["unresolved"]["count"] == 0


def test_an_unconnected_purchase_counts_as_unresolved(client, demo):
    client.post("/v1/dev/access-scenario", headers=PARENT, json={"scenario": "paid_not_active"})
    m = client.get("/v1/access/metrics", headers=PARENT).json()
    assert m["unresolved"]["count"] == 1 and m["unresolved"]["not_connected"] == 1
    assert m["time_to_usable"]["confirmed_to_usable"]["count"] == 0
