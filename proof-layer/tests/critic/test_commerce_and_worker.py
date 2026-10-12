"""Red-team reproductions: purchases, provider and worker.

Every test here FAILS on the reviewed code and should pass once the defect is fixed.
"""
from app import provider, seed, worker
from app.entitlements import CATALOGS

from ._helpers import buy, decide, intents_by_state, make_jobs_due, plan_code

RANK = {code: p["rank"] for code, p in CATALOGS["cat-2026-10-public"]["plans"].items()}


def settle_everything_pending():
    for op in provider.ledger(seed.HOUSEHOLD):
        if op["status"] == "PENDING":
            provider.resolve_pending(op["operation_key"], "SUCCEEDED")
    make_jobs_due()
    worker.reconcile_intents("critic")


# F-01 -----------------------------------------------------------------------------------------
def test_one_upgrade_is_never_charged_twice(client, demo):
    """Two tabs (two idempotency keys) on the same Memories offer. Both intents are
    PREPARED because nothing considers in-flight intents. The first provider reply is
    PENDING, so the worker's pre-submission recheck still sees 'Sleep plan, blocked'
    and submits the second one too. When the first settles, the household holds two
    CONFIRMED purchases and two SUCCEEDED provider charges for one upgrade."""
    d = decide(client, "nextnap")
    assert buy(client, d, "critic-double-0001").status_code == 201
    buy(client, d, "critic-double-0002")              # a refusal here is an acceptable repair
    provider.schedule(seed.HOUSEHOLD, "pending")
    worker.run_once("critic")
    settle_everything_pending()
    confirmed = [i for i in intents_by_state() if i[1] == "CONFIRMED"]
    charged = [op for op in provider.ledger(seed.HOUSEHOLD) if op["status"] == "SUCCEEDED"]
    assert len(confirmed) <= 1 and len(charged) <= 1, (confirmed, charged)


# F-02 -----------------------------------------------------------------------------------------
def test_a_confirmed_purchase_never_lowers_the_plan(client, demo):
    """Memories (for NextNap) and Milestones (for milestones) are prepared from two
    current decisions. Memories is PENDING at the provider; Milestones confirms. When
    Memories settles later, _grant_entitlement overwrites the plan with the LOWER
    rank: the household paid for Milestones and loses developmental milestones."""
    nextnap, milestones = decide(client, "nextnap"), decide(client, "developmental_milestones")
    assert buy(client, nextnap, "critic-downgrade-01").status_code == 201
    buy(client, milestones, "critic-downgrade-02")    # a refusal here is an acceptable repair
    provider.schedule(seed.HOUSEHOLD, "pending")
    worker.run_once("critic")
    settle_everything_pending()
    confirmed_ranks = [RANK[p] for _, s, p in intents_by_state() if s == "CONFIRMED"]
    assert confirmed_ranks, "expected at least one confirmed purchase"
    assert RANK[plan_code()] >= max(confirmed_ranks), (plan_code(), intents_by_state())


# F-09 -----------------------------------------------------------------------------------------
def test_unknown_outcome_is_still_reconciled_by_a_running_worker(client, demo):
    """A PENDING provider outcome keeps the intent UNKNOWN; the submit job is retried
    every 5 s and dead-lettered after MAX_ATTEMPTS (8). The long-running loop
    (`python -m app.worker`) reconciles only at start-up, so once the job is dead an
    outcome the provider settles later is never learned until a restart."""
    d = decide(client, "nextnap")
    r = buy(client, d, "critic-liveness-01")
    assert r.status_code == 201
    intent_id, key = r.json()["intent_id"], r.json()["provider_operation_key"]
    provider.schedule(seed.HOUSEHOLD, "pending")
    for _ in range(worker.MAX_ATTEMPTS + 2):          # what a running loop does for ~50 s
        make_jobs_due()
        worker.run_once("critic-loop")
    provider.resolve_pending(key, "SUCCEEDED")
    make_jobs_due()
    worker.run_once("critic-loop")                    # the same running loop, no restart
    state = dict((i, s) for i, s, _ in intents_by_state())[intent_id]
    assert state == "CONFIRMED", f"intent stuck in {state} after the provider settled"
