"""Model-based test of the commercial core.

Hypothesis generates sequences of commands: store events from every channel,
camera moves, eligibility checks on a normal and a safety surface, purchases
with fresh and reused idempotency keys, provider behaviors, worker runs, a
worker crash after the provider committed, and late provider decisions.

A small reference model, written independently of the app, predicts each
response and the durable state after it:

- a camera's plan is the highest-ranked active transaction; a revoked
  transaction never grants; a pending higher plan means "checking";
- the household version moves on a camera move or when a camera's plan,
  plan state or team limit changes, and a decision is stale once it moves;
- a purchase is accepted only for a current decision with an offer, on a
  camera with no upgrade in flight, and a reused key replays or is refused;
- the purchase's state follows the provider's ledger, never ahead of it.

After every command the app's state is compared with the model: each camera's
derived plan and team limit, each purchase's state, one live upgrade per
camera, and agreement with the provider's ledger.
"""
from dataclasses import dataclass, field
from typing import Optional

from hypothesis import HealthCheck, settings, strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, precondition, rule
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import entitlements, provider, seed, worker
from app.db import SessionLocal, transaction
from app.main import app
from app.models import EntitlementClaim, PurchaseIntent

from .conftest import PARENT

CAT = "cat-2026-10-public"
RANK = {code: p["rank"] for code, p in entitlements.CATALOGS[CAT]["plans"].items()}
FEATURE = "developmental_milestones"
FEATURE_PLANS = {p for policy in entitlements.POLICIES.values() for p in policy["features"][FEATURE]["plans"]}
OVERHEAD = set(entitlements.OVERHEAD_MOUNTS)
LIVE = {"PREPARED", "SUBMITTED", "UNKNOWN"}
FROM_LEDGER = {"SUCCEEDED": "CONFIRMED", "FAILED": "FAILED", "PENDING": "UNKNOWN"}


def reference_plan(claims: set[tuple[str, str, str, str]]) -> tuple[str, str, int, tuple]:
    """(plan, state, team limit, pending plans above it) from a set of (channel, ref, status, plan).
    Every event in these tests covers today, so 'active' means in force."""
    txns: dict[tuple[str, str], tuple[set[str], set[str]]] = {}
    for channel, ref, status, plan in claims:
        statuses, plans = txns.setdefault((channel, ref), (set(), set()))
        statuses.add(status)
        plans.add(plan)
    active, pending = [], []
    for statuses, plans in txns.values():
        best = max(plans, key=RANK.__getitem__)
        if "revoked" in statuses:
            continue
        (active if "active" in statuses else pending).append(best)
    plan = max(active, key=RANK.__getitem__, default="none")
    higher = [p for p in pending if RANK[p] > RANK[plan]]
    state = "checking" if higher else ("active" if plan != "none" else "none")
    return plan, state, entitlements.MEMBER_CAPS[plan], tuple(sorted(set(higher), key=RANK.__getitem__))


@dataclass
class Intent:
    id: str
    device: str
    key: str
    decision_id: str
    offer_id: str
    version: int
    state: str = "PREPARED"


@dataclass
class Model:
    mounts: dict[str, str]
    claims: dict[str, set] = field(default_factory=dict)
    version: int = 0
    intents: dict[str, Intent] = field(default_factory=dict)   # by intent id
    keys: dict[str, Intent] = field(default_factory=dict)      # by idempotency key

    def plan(self, device: str) -> tuple[str, str, int, tuple]:
        return reference_plan(self.claims[device])

    def add_claim(self, device: str, claim: tuple) -> None:
        before = self.plan(device)
        self.claims[device].add(claim)
        if self.plan(device) != before:
            self.version += 1

    def set_state(self, intent: Intent, state: str) -> None:
        if state == "CONFIRMED" and intent.state != "CONFIRMED":
            self.add_claim(intent.device, ("simulated_purchase", f"op_{intent.id}", "active", "milestones"))
        intent.state = state

    def live(self, device: str) -> bool:
        return any(i.device == device and i.state in LIVE for i in self.intents.values())


@dataclass(frozen=True)
class Decision:
    id: str
    device: str
    offer_id: Optional[str]
    version: int


def expire_leases() -> None:
    with transaction() as db:
        db.execute(text("UPDATE outbox SET lease_until = now() - interval '1 second', "
                        "available_at = now() WHERE state IN ('in_progress', 'pending')"))


def db_intents() -> dict[str, str]:
    with SessionLocal() as db:
        return dict(db.execute(select(PurchaseIntent.id, PurchaseIntent.state)).all())


class CommerceMachine(RuleBasedStateMachine):
    decisions = Bundle("decisions")

    def __init__(self):
        super().__init__()
        with transaction() as db:
            self.demo = seed.load(db)
        self.devices = self.demo["devices"]          # {"nursery": id, "playroom": id}
        self.client = TestClient(app)
        self.client.__enter__()
        commercial = self.client.get("/v1/household/commercial", headers=PARENT).json()
        self.model = Model(mounts={d["id"]: d["mount"] for d in commercial["devices"]})
        with SessionLocal() as db:
            for d in self.devices.values():
                rows = db.execute(select(EntitlementClaim).where(EntitlementClaim.device_id == d)).scalars()
                self.model.claims[d] = {(c.channel, c.external_ref, c.event_status, c.plan_code) for c in rows}
        self.model.version = commercial["state_revision"]

    def teardown(self):
        self.client.__exit__(None, None, None)

    # ------------------------------------------------------------- scenario
    @rule(cam=st.sampled_from(["nursery", "playroom"]),
          mount=st.sampled_from(["floor_stand", "wall_mount", "flex_stand", "anywhere_stand"]))
    def move_camera(self, cam, mount):
        device = self.devices[cam]
        r = self.client.post("/v1/dev/mount", headers=PARENT, json={"mount": mount, "device_id": device})
        assert r.status_code == 200, r.text
        self.model.mounts[device] = mount
        self.model.version += 1

    @rule(cam=st.sampled_from(["nursery", "playroom"]),
          channel=st.sampled_from(["apple", "google", "web", "trial"]),
          plan=st.sampled_from(["sleep", "memories", "milestones"]), n=st.integers(0, 1),
          status=st.sampled_from(["active", "pending", "revoked"]))
    def store_event(self, cam, channel, plan, n, status):
        device = self.devices[cam]
        ref = f"{cam}-{channel}-{plan}-{n}"          # one transaction, one plan, one camera
        r = self.client.post("/v1/dev/claims", headers=PARENT, json={
            "device_id": device, "channel": channel, "external_ref": ref, "event_status": status,
            "plan_code": plan, "starts_at": "2026-10-01T00:00:00Z", "days": 3650})
        assert r.status_code == 200, r.text
        self.model.add_claim(device, (channel, ref, status, plan))

    @rule(cam=st.sampled_from(["nursery", "playroom"]), code=st.sampled_from(["CODE-A", "CODE-B"]))
    def redeem_code(self, cam, code):
        device = self.devices[cam]
        r = self.client.post("/v1/dev/claims", headers=PARENT, json={
            "device_id": device, "channel": "activation_code", "external_ref": code, "event_status": "active",
            "plan_code": "milestones", "starts_at": "2026-10-01T00:00:00Z", "days": 3650})
        owner = next((d for d, cs in self.model.claims.items()
                      if any(c[0] == "activation_code" and c[1] == code for c in cs)), None)
        if owner is None or owner == device:
            assert r.status_code == 200, r.text
            self.model.add_claim(device, ("activation_code", code, "active", "milestones"))
        else:
            assert r.status_code == 409 and r.json()["code"] == "CODE_ALREADY_REDEEMED", r.text

    # ------------------------------------------------------------- eligibility
    @rule(target=decisions, cam=st.sampled_from(["nursery", "playroom"]),
          surface=st.sampled_from(["feature_availability", "feature_availability", "live_view"]))
    def decide(self, cam, surface):
        device = self.devices[cam]
        r = self.client.post("/v1/capability-decisions", headers=PARENT,
                             json={"feature": FEATURE, "device_id": device, "surface": surface})
        assert r.status_code == 200, r.text
        d = r.json()
        plan, state, _, pending = self.model.plan(device)
        mount_ok = self.model.mounts[device] in OVERHEAD
        if plan in FEATURE_PLANS and mount_ok:
            assert d["status"] == "AVAILABLE" and d["offer"] is None, d
        elif not mount_ok:
            assert d["status"] == "BLOCKED" and d["offer"] is None, d          # hardware is never sold
            assert "MOUNT_NOT_SUPPORTED" in {b["code"] for b in d["blockers"]}
        elif (pending and pending[-1] in FEATURE_PLANS) or self.model.live(device):
            # A pending store event or this app's own purchase in flight (RT-8).
            assert d["status"] == "UNKNOWN" and d["offer"] is None, d          # never sell what was just bought
        else:
            assert d["status"] == "BLOCKED", d
            if surface == "live_view":
                assert d["offer"] is None, d                                    # no offers on safety surfaces
            else:
                assert d["offer"] and d["offer"]["plan_code"] in FEATURE_PLANS, d
        return Decision(d["decision_id"], device, (d["offer"] or {}).get("offer_id"), self.model.version)

    # ------------------------------------------------------------- purchase
    @rule(decision=decisions, key=st.sampled_from(["key-0001", "key-0002", "key-0003", "key-0004"]))
    def buy(self, decision, key):
        offer_id = decision.offer_id or "offer-none"
        r = self.client.post("/v1/purchase-intents", headers=PARENT | {"Idempotency-Key": key},
                             json={"decision_id": decision.id, "offer_id": offer_id})
        assert r.status_code < 500, r.text
        earlier = self.model.keys.get(key)
        if earlier is not None:
            if (earlier.decision_id, earlier.offer_id) == (decision.id, offer_id):
                assert r.status_code == 200 and r.json()["intent_id"] == earlier.id, r.text
            else:
                assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED", r.text
            return
        if decision.offer_id is None:
            assert r.status_code in (409, 422) and r.json()["code"] in ("NO_ELIGIBLE_OFFER", "STALE_DECISION"), r.text
        elif decision.version != self.model.version:
            assert r.status_code == 409 and r.json()["code"] == "STALE_DECISION", r.text
        elif self.model.live(decision.device):
            assert r.status_code == 409 and r.json()["code"] == "UPGRADE_IN_PROGRESS", r.text
        else:
            assert r.status_code == 201, r.text
            intent = Intent(r.json()["intent_id"], decision.device, key, decision.id, offer_id, self.model.version)
            self.model.intents[intent.id] = intent
            self.model.keys[key] = intent

    # ------------------------------------------------------------- provider and worker
    def _waiting(self) -> list[Intent]:
        """Intents the worker will pick up, in job order (creation order)."""
        return [i for i in self.model.intents.values() if i.state in ("PREPARED", "SUBMITTED")]

    def _submittable(self, i: Intent) -> bool:
        """PREPARED crosses the submission boundary only if nothing changed since
        it was validated; SUBMITTED retries with the same operation key."""
        return i.state == "SUBMITTED" or i.version == self.model.version

    @rule(behavior=st.sampled_from(["succeed", "decline", "pending", "lose_response", "unavailable"]))
    def run_worker(self, behavior):
        waiting = self._waiting()
        scheduled = len(waiting) == 1 and self._submittable(waiting[0])
        if scheduled:                            # exactly one submission takes this behavior
            provider.schedule(seed.HOUSEHOLD, behavior)
        expire_leases()
        worker.run_once("model")
        for i in waiting:                        # the worker's order; a confirmation can make the next one stale
            if not self._submittable(i):
                self.model.set_state(i, "CANCELLED_BEFORE_SUBMISSION")
                continue
            outcome = "CONFIRMED"
            if scheduled:
                outcome = {"succeed": "CONFIRMED", "decline": "FAILED", "pending": "UNKNOWN",
                           "lose_response": "CONFIRMED", "unavailable": "SUBMITTED"}[behavior]
            self.model.set_state(i, outcome)

    @precondition(lambda self: len(self._waiting()) == 1 and self._submittable(self._waiting()[0]))
    @rule()
    def crash_after_provider_then_reconcile(self):
        (i,) = self._waiting()
        import os
        os.environ["PROOF_LAYER_INPROCESS_CRASH"] = "1"
        try:
            expire_leases()
            try:
                worker.run_once("model", crash_after_provider=True)
                crashed = False
            except worker.SimulatedCrash:
                crashed = True
        finally:
            os.environ.pop("PROOF_LAYER_INPROCESS_CRASH", None)
        assert crashed
        assert db_intents()[i.id] == "SUBMITTED"                 # the app hasn't learned the outcome yet
        assert provider.lookup(f"op_{i.id}")["status"] == "SUCCEEDED"
        expire_leases()
        worker.reconcile_intents("model")
        self.model.set_state(i, "CONFIRMED")

    @precondition(lambda self: any(i.state == "UNKNOWN" for i in self.model.intents.values()))
    @rule(settle=st.sampled_from(["SUCCEEDED", "FAILED"]), data=st.data())
    def provider_decides_later(self, settle, data):
        unknown = sorted(i.id for i in self.model.intents.values() if i.state == "UNKNOWN")
        i = self.model.intents[data.draw(st.sampled_from(unknown))]
        provider.resolve_pending(f"op_{i.id}", settle)
        expire_leases()
        worker.reconcile_intents("model")
        self.model.set_state(i, FROM_LEDGER[settle])

    @rule()
    def reconcile_without_news(self):
        """Reconciliation with nothing new must change nothing."""
        expire_leases()
        worker.reconcile_intents("model")

    # ------------------------------------------------------------- invariants
    @invariant()
    def plans_match_the_model(self):
        commercial = self.client.get("/v1/household/commercial", headers=PARENT).json()
        for d in commercial["devices"]:
            plan, state, cap, _ = self.model.plan(d["id"])
            got = d["plan"]
            assert (got["plan_code"], got["state"], got["member_cap"]) == (plan, state, cap), (d["label"], got)
        assert commercial["state_revision"] == self.model.version

    @invariant()
    def purchases_match_the_model_and_the_ledger(self):
        states = db_intents()
        assert states == {i.id: i.state for i in self.model.intents.values()}
        ledger = {op["operation_key"]: op["status"] for op in provider.ledger(seed.HOUSEHOLD)}
        assert len(ledger) <= len(states)
        for intent_id, state in states.items():
            status = ledger.get(f"op_{intent_id}")
            if state in ("CONFIRMED", "FAILED", "UNKNOWN"):
                assert FROM_LEDGER[status] == state, (intent_id, state, status)   # never ahead of the provider

    @invariant()
    def one_live_upgrade_per_camera(self):
        with SessionLocal() as db:
            rows = db.execute(select(PurchaseIntent.device_id).where(PurchaseIntent.state.in_(LIVE))).scalars().all()
        assert len(rows) == len(set(rows))


CommerceMachine.TestCase.settings = settings(
    max_examples=int(__import__("os").environ.get("MODEL_EXAMPLES", "25")), stateful_step_count=18,
    deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
TestCommerceModel = CommerceMachine.TestCase
