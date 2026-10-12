"""Mutation testing: plant one known defect at a time and check the suite notices.

Each mutant is a single, deliberate change to a guard that the docs advertise
(an ownership check, a revision check, a uniqueness constraint...). The runner
copies the app and tests to a temporary directory, applies one mutant there,
and runs the backend suite against its own databases. The working tree is never
modified.

A mutant is KILLED when at least one test fails, and SURVIVED when the whole
suite still passes. A survivor is a finding: either a missing test, or a guard
that is redundant (another layer already enforces the rule). The report says
which.

    python scripts/mutate.py                 # every mutant
    python scripts/mutate.py M04 M11         # selected mutants
    python scripts/mutate.py --baseline      # unmutated copy only

Results: artifacts/mutation/mutation-results.json and mutation-report.md.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT.parent / "artifacts" / "mutation"
APP_DB = "postgresql+psycopg://proof@127.0.0.1:54329/proof_layer_mut"
PROVIDER_DB = "postgresql+psycopg://proof@127.0.0.1:54329/provider_sim_mut"
PSQL = Path(os.environ.get("PG_BIN", r"C:\Program Files\PostgreSQL\17\bin")) / "psql"


@dataclass
class Mutant:
    id: str
    guarantee: str
    file: str = ""
    old: str = ""
    new: str = ""
    sql_before: list[str] = field(default_factory=list)
    sql_after: list[str] = field(default_factory=list)


MUTANTS = [
    Mutant("M01", "A purchase decision must belong to the requesting household",
           "app/purchases.py",
           "CapabilityDecision.household_id == principal.household_id)).scalar_one_or_none()",
           "True)).scalar_one_or_none()"),
    Mutant("M02", "A correction must name the revision the parent saw (expected_revision)",
           "app/engine.py",
           "if window.current_revision != expected_revision:",
           "if False:"),
    Mutant("M03", "Idempotency: a retry with the same key replays; a different payload is rejected (app check)",
           "app/purchases.py",
           "    if existing is not None:\n        if existing.request_fingerprint",
           "    if False:\n        if existing.request_fingerprint"),
    Mutant("M04", "Idempotency: one intent per (household, key) (database constraint)",
           sql_before=["ALTER TABLE purchase_intents DROP CONSTRAINT uq_intents_idempotency"],
           sql_after=["TRUNCATE purchase_intents CASCADE",
                      "ALTER TABLE purchase_intents ADD CONSTRAINT uq_intents_idempotency "
                      "UNIQUE (household_id, idempotency_key)"]),
    Mutant("M05", "A camera on the wrong mount is blocked; a plan offer can't fix hardware",
           "app/entitlements.py",
           'elif not any(m in policy["mounts"] for m in mounts):',
           "elif False:"),
    Mutant("M06", "An unknown provider outcome stays UNKNOWN; it is never turned into CONFIRMED",
           "app/purchases.py",
           'if status == "SUCCEEDED":',
           'if status != "FAILED":'),
    Mutant("M07", "No camera evidence means coverage is unknown or partial, never full",
           "app/briefing.py",
           "observed = iv.minutes(usable)",
           "observed = iv.minutes(usable) if usable else elapsed"),
    Mutant("M08", "A household-owned row is loaded only within the caller's household",
           "app/security.py",
           "    stmt = select(model).where(model.id == resource_id,  # type: ignore[attr-defined]\n"
           "                               model.household_id == principal.household_id)",
           "    stmt = select(model).where(model.id == resource_id)"),
    Mutant("M09", "A revocation committed while a request waits for the lock still stops it",
           "app/security.py",
           "if member is None or member.revoked_at is not None or member.household_id != principal.household_id:",
           "if member is None:"),
    Mutant("M10", "An older acknowledgment can never move a caregiver's head backwards",
           "app/acks.py",
           "where=AckHead.revision < upsert.excluded.revision",
           "where=AckHead.revision != upsert.excluded.revision"),
    Mutant("M11", "No offers on safety surfaces (live view, safety alert)",
           "app/decisions.py",
           'if surface in SAFETY_SURFACES and result["offer"]:',
           "if False:"),
    Mutant("M12", "While a purchase is being confirmed, don't sell it again",
           "app/decisions.py",
           'if result["status"] == "BLOCKED" and unconfirmed:',
           "if False:"),
    Mutant("M13", "Purchase re-runs the rules against the camera as it is now",
           "app/purchases.py",
           'if result["status"] != "BLOCKED" or not live_offer or live_offer["offer_id"] != offer_id:',
           "if False:"),
    Mutant("M14", "Client prices can't override server terms",
           "app/purchases.py",
           '        raise _reject(principal, "TERMS_MISMATCH",',
           '        if False: raise _reject(principal, "TERMS_MISMATCH",'),
    Mutant("M15", "A refund (revoked claim) removes the plan",
           "app/entitlements.py",
           'if "revoked" in statuses:',
           "if False:"),
    Mutant("M16", "A camera's plan doesn't depend on the order its claims arrived",
           "app/entitlements.py",
           "            plan = max(events, key=lambda e: (e.starts_at, rank(e.plan_code))).plan_code",
           "            plan = events[-1].plan_code"),
    Mutant("M17", "A reported sleep claim inside watched time where the camera saw no sleep is contested",
           "app/briefing.py",
           "seen_awake = iv.subtract(iv.intersect(claimed, usable), camera_sleep + basis)",
           "seen_awake = []"),
    Mutant("M18", "One live upgrade per camera (database backstop only)",
           sql_before=["DROP INDEX uq_intents_one_live_upgrade"],
           sql_after=["TRUNCATE purchase_intents CASCADE",
                      "CREATE UNIQUE INDEX uq_intents_one_live_upgrade ON purchase_intents "
                      "(household_id, device_id) WHERE state IN ('PREPARED', 'SUBMITTED', 'UNKNOWN')"]),
    Mutant("M19", "A purchase or code names one camera only (database backstop only)",
           sql_before=["ALTER TABLE entitlement_claims DROP CONSTRAINT ex_claim_one_camera"],
           sql_after=["TRUNCATE entitlement_claims CASCADE",
                      "ALTER TABLE entitlement_claims ADD CONSTRAINT ex_claim_one_camera "
                      "EXCLUDE USING gist (channel WITH =, external_ref WITH =, device_id WITH <>)"]),
    Mutant("M20", "The app's own unconfirmed purchase counts as being confirmed (no offer again)",
           "app/decisions.py",
           'found = set(pending_claims) | {terms["plan_code"] for terms in in_flight}',
           "found = set(pending_claims)"),
    Mutant("M21", "A newly pending higher plan moves the household version (earlier offers go stale)",
           "app/claims.py",
           'return plan["plan_code"], plan["state"], plan["member_cap"], tuple(plan["pending_plans"])',
           'return plan["plan_code"], plan["state"], plan["member_cap"]'),
    Mutant("M22", "A re-delivered record doesn't change the recorded-sleep fingerprint",
           "app/briefing.py",
           'list(dict.fromkeys((i["start"], i["end"], i["source"]) for i in f["intervals"]))',
           '[(i["start"], i["end"], i["source"]) for i in f["intervals"]]'),
    Mutant("M23", "Two households redeeming one code at once get a clear refusal, not a server error",
           "app/claims.py",
           "    except IntegrityError:\n"
           "        settled = _already_recorded(db, device, channel, external_ref, event_status, starts_at, ends_at,\n"
           "                                    request_hash, before)",
           "    except IntegrityError:\n"
           "        raise\n"
           "        settled = None"),
    Mutant("M24", "A killed experiment admits no one and counts no exposure",
           "app/growth.py",
           "    return bool(control and control.killed)",
           "    return False"),
    # Purchase to access (migration 0006).
    Mutant("M25", "A family that already paid is never offered the same capability again",
           "app/decisions.py",
           '    if result["status"] == "BLOCKED" and result["offer"]:\n        result = _withhold_if_already_bought(',
           '    if False:\n        result = _withhold_if_already_bought('),
    Mutant("M26", "A purchase on another camera is refused by the connect command itself",
           "app/access.py",
           '    if binding["bound"] and (binding["elsewhere"] or binding["device_id"] != device.id):\n        code = "CODE_ALREADY_REDEEMED"',
           '    if False:\n        code = "CODE_ALREADY_REDEEMED"'),
    Mutant("M27", "The provider's highest version wins, whatever order events arrive in",
           "app/purchase_evidence.py",
           "    top = max(e.provider_version for e in events)",
           "    top = events[-1].provider_version"),
    Mutant("M28", "A refund of a connected purchase revokes it on that camera",
           "app/purchase_evidence.py",
           '    if state["state"] != "refunded":\n        return None',
           '    if True:\n        return None'),
    Mutant("M29", "A connect from an outdated screen is refused",
           "app/access.py",
           '    if household.state_revision != expected_state_revision:\n        raise DomainError(409, "STALE_ACCESS_VIEW"',
           '    if False:\n        raise DomainError(409, "STALE_ACCESS_VIEW"'),
    Mutant("M30", "A camera that already has a paid plan is not given a duplicate",
           "app/access.py",
           '        if paid_rank >= _rank(catalog_version, found["plan_code"]):\n            raise DomainError(409, "CAMERA_ALREADY_HAS_PLAN",',
           '        if False:\n            raise DomainError(409, "CAMERA_ALREADY_HAS_PLAN",'),
    Mutant("M31", "Evidence for another family's purchase is refused with a clear answer",
           "app/purchase_evidence.py",
           '    if owner != household.id:\n        raise DomainError(409, "PURCHASE_OWNED_ELSEWHERE"',
           '    if False:\n        raise DomainError(409, "PURCHASE_OWNED_ELSEWHERE"'),
    Mutant("M32", "Capability verified is never inferred from operation completed",
           "app/access.py",
           "    opened = None\n    if verified is not None:",
           "    opened = None\n    verified = verified or connect\n    if verified is not None:"),
    Mutant("M33", "A completed connect is not a verified capability until a check runs",
           "app/access.py",
           '    overlay_plan = (last_bind.outcome.get("plan_code") if awaiting_verification',
           '    overlay_plan = (None if awaiting_verification'),
    Mutant("M38", "Another household's purchase can't be put on this family's camera",
           "app/claims.py",
           "    if owner is not None and owner != household.id:",
           "    if False:"),
    Mutant("M39", "A purchase grants the plan of the period covering now (a cheaper renewal applies)",
           "app/entitlements.py",
           "            plan = max((e.plan_code for e in covering), key=rank)",
           "            plan = max((e.plan_code for e in events), key=rank)"),
    Mutant("M40", "A period that hasn't started is scheduled, not ended",
           "app/entitlements.py",
           '        elif upcoming:\n            state = "scheduled"',
           '        elif False:\n            state = "scheduled"'),
    Mutant("M41", "A paid, connected period that hasn't started reads as scheduled, not as no plan",
           "app/access.py",
           "    if scheduled_higher:",
           "    if False:"),
    Mutant("M42", "A check sorts after the connect it saw (commit time, not transaction start)",
           "app/access.py",
           'check = AccessCheck(id=new_id("chk"), created_at=func.clock_timestamp(),',
           'check = AccessCheck(id=new_id("chk"), created_at=func.now(),'),
    Mutant("M43", "An upgrade with unmet prerequisites stops with a diagnostic before changing anything",
           "migrations/versions/0007_claim_periods.py",
           "def upgrade() -> None:\n    _preflight()",
           "def upgrade() -> None:\n    pass"),
    Mutant("M44", "Entitlement time is read through one controllable clock",
           "app/claims.py",
           "def utcnow() -> datetime:\n    return clock.now()",
           "def utcnow() -> datetime:\n    return datetime.now(timezone.utc)"),
    Mutant("M45", "A demo reset takes every lock up front, so an open page is never a deadlock victim",
           "app/seed.py",
           '    db.execute(text(f"LOCK TABLE {LOCK_ORDER} IN ACCESS EXCLUSIVE MODE"))',
           "    pass"),
    Mutant("M46", "A store contradiction is never hidden behind 'connected, checking'",
           "app/access.py",
           '    conflicting = first("conflicting")\n    if conflicting:',
           '    conflicting = first("conflicting")\n    if conflicting and not awaiting_plan:'),
    Mutant("M47", "Billing retry outside a grace period grants no access",
           "app/provider_lifecycle.py",
           '        "DID_FAIL_TO_RENEW": "billing_retry",',
           '        "DID_FAIL_TO_RENEW": "grace",'),
    Mutant("M48", "A store grace period keeps access on the camera until the grace end",
           "app/purchase_evidence.py",
           '    windows = state["periods"] + (state["current_grace"] if state["state"] == "grace" else [])',
           '    windows = state["periods"]'),
    Mutant("M49", "A late grace event can't undo a recovery",
           "app/purchase_evidence.py",
           '    windows = state["periods"] + (state["current_grace"] if state["state"] == "grace" else [])',
           '    windows = state["periods"] + state["grace_windows"]'),
    Mutant("M50", "A grace period whose end has passed is billing retry, not grace",
           "app/purchase_evidence.py",
           '    if state["state"] == "grace" and state["ends_at"] is not None and state["ends_at"] <= now:',
           '    if False:'),
    Mutant("M51", "A paused plan says it is paused, not that it ended",
           "app/access.py",
           '    for state in ("billing_retry", "paused"):',
           '    for state in ("billing_retry",):'),
    Mutant("M52", "A store-reported expiry is not called a missing renewal",
           "app/access.py",
           '        if ended is not None and ended["state"] == "expired":',
           '        if False:'),
    Mutant("M53", "A purchase in billing retry stays on its camera; connecting it again is refused",
           "app/access.py",
           '"billing_retry": ("PURCHASE_PAYMENT_RETRYING",',
           '"billing_retry_": ("PURCHASE_PAYMENT_RETRYING",'),
    Mutant("M54", "When the store ends access early but a claim still grants it, the camera says so",
           "app/access.py",
           '    ended_early = next((p for p in mine if _store_ended_camera_active(p, plan)), None)',
           '    ended_early = None'),
    Mutant("M55", "A plan whose payment is retrying or paused is never sold again",
           "app/decisions.py",
           '             or p["state"] in ("billing_retry", "paused")]',
           '             or False]'),
    Mutant("M56", "Grace names the last day with access, not the end instant",
           "app/access.py",
           '    return _day((datetime.fromisoformat(iso) - timedelta(microseconds=1)).isoformat())',
           '    return _day(iso)'),
    Mutant("M57", "A lower plan's grace doesn't hide a higher plan that works",
           "app/access.py",
           '    if grace and _rank(catalog_version, grace["plan_code"]) >= current_rank:',
           '    if grace:'),
    Mutant("M58", "A late-delivered failure is not timed as a negative recovery",
           "app/access_metrics.py",
           '    out_of_order = took is not None and took < 0',
           '    out_of_order = False'),
    Mutant("M59", "Reading recorded evidence checks the camera's plan",
           "app/video_evidence.py",
           '    g = require_gate(db, household, device, record=False)',
           '    g = gate(db, household, device)'),
    Mutant("M60", "A signed media link expires",
           "app/video_evidence.py",
           '        expired = int(expires) < (now or time.time())',
           '        expired = False'),
    Mutant("M61", "A signed media link opens only the clip it names",
           "app/video_evidence.py",
           '        valid = hmac.compare_digest(sig.encode(), good.encode()) and token_asset == asset_id',
           '        valid = hmac.compare_digest(sig.encode(), good.encode())'),
    Mutant("M62", "A stalled feed is missing evidence, not stillness",
           "app/video_processing.py",
           '    if w["identical_ratio"] is not None and w["identical_ratio"] >= c.frozen_ratio \\',
           '    if False and w["identical_ratio"] is not None \\'),
    Mutant("M67", "A stall right after a pan is still a stall: change carried over the lag doesn't count",
           "app/video_processing.py",
           '    within = w.get("local_change_within", w["local_change"])',
           '    within = w["local_change"]'),
    Mutant("M68", "Passing tests on other code never certify this code",
           "app/proof.py",
           '    verdict = "BLOCKED" if blockers else "CERTIFIED" if build["report_matches_code"] else "UNVERIFIED"',
           '    verdict = "BLOCKED" if blockers else "CERTIFIED"'),
    Mutant("M63", "Missing frames are missing evidence, not stillness",
           "app/video_processing.py",
           '    if w["frames"] == 0 or (w["coverage"] or 0) < c.min_coverage:',
           '    if w["frames"] == 0:'),
    Mutant("M64", "A report from an outdated evidence screen is refused",
           "app/video_evidence.py",
           '    if expected_revision != asset.evidence_revision:',
           '    if False:'),
    Mutant("M66", "A comparison's refusal probes leave nothing behind",
           "app/compare.py",
           "            discard_kept()               # bind's detached analytics would otherwise outlive the rollback",
           "            pass"),
    Mutant("M65", "Camera motion isn't reported as movement in the scene",
           "app/video_processing.py",
           '    if (w["camera_shift_median"] or 0) >= c.camera_shift_px:',
           '    if False:'),
    Mutant("M34", "Two statuses at the same provider version are a conflict, not a guess",
           "app/purchase_evidence.py",
           '        "state": "conflicting" if len(statuses) > 1 else statuses[0],',
           '        "state": statuses[-1],'),
    Mutant("M36", "A confirmed renewal of a connected purchase reaches its camera",
           "app/purchase_evidence.py",
           '    renewed = apply_periods(db, household, after, catalog_version)',
           '    renewed = None'),
    Mutant("M37", "An ended purchase can't be connected as if it were current",
           "app/access.py",
           '    if _ended(found, utcnow()):\n        raise DomainError(409, "PURCHASE_EXPIRED"',
           '    if False:\n        raise DomainError(409, "PURCHASE_EXPIRED"'),
    Mutant("M35", "A repeated connect that changed nothing does not reopen verification",
           "app/access.py",
           '        AccessAction.outcome["already_connected"].astext == "false")',
           '        True)'),
]


def psql(statement: str) -> None:
    subprocess.run([str(PSQL), "-h", "127.0.0.1", "-p", "54329", "-U", "proof", "-d", "proof_layer_mut",
                    "-v", "ON_ERROR_STOP=1", "-qc", statement], check=True, capture_output=True)


def copy_tree(dest: Path) -> None:
    for name in ("app", "tests", "migrations", "bench", "static", "alembic.ini"):
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, dest / name, ignore=shutil.ignore_patterns("__pycache__", "e2e"))
        else:
            shutil.copy2(src, dest / name)


def run_suite(workdir: Path, stop_first: bool) -> dict:
    env = {**os.environ, "DATABASE_URL": APP_DB, "PROVIDER_DATABASE_URL": PROVIDER_DB,
           "PYTHONDONTWRITEBYTECODE": "1"}
    cmd = [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
           "--ignore=tests/e2e", "-o", "addopts="]
    if stop_first:
        cmd.append("-x")
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True)
    lines = proc.stdout.strip().splitlines()
    failed = [line.split(" ", 1)[1].split(" - ")[0] for line in lines if line.startswith("FAILED ")]
    return {"exit_code": proc.returncode, "seconds": round(time.monotonic() - started, 1),
            "summary": lines[-1] if lines else proc.stderr[-500:], "first_failures": failed[:3]}


def run_mutant(m: Mutant | None) -> dict:
    with tempfile.TemporaryDirectory(prefix="mutant-") as tmp:
        work = Path(tmp)
        copy_tree(work)
        if m and m.file:
            path = work / m.file
            text = path.read_text(encoding="utf-8")
            count = text.count(m.old)
            if count != 1:
                raise SystemExit(f"{m.id}: expected exactly one match in {m.file}, found {count}")
            path.write_text(text.replace(m.old, m.new), encoding="utf-8")
        # Bring the schema to head before any database mutation, so the drop has a target.
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=work, check=True,
                       capture_output=True, env={**os.environ, "DATABASE_URL": APP_DB})
        for stmt in (m.sql_before if m else []):
            psql(stmt)
        try:
            result = run_suite(work, stop_first=m is not None)
        finally:
            for stmt in (m.sql_after if m else []):
                psql(stmt)
    if m is None:
        verdict = "GREEN" if result["exit_code"] == 0 else "RED"
    else:
        verdict = "SURVIVED" if result["exit_code"] == 0 else ("KILLED" if result["exit_code"] == 1 else "ERROR")
    return {"id": m.id if m else "BASELINE", "guarantee": m.guarantee if m else "unmutated copy",
            "target": m.file or "database" if m else "", "change": (
                {"old": m.old, "new": m.new} if m and m.file else {"sql": m.sql_before} if m else {}),
            "verdict": verdict, **result}


def write_report(results: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    doc = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "python": platform.python_version(), "machine": platform.processor(),
           "command": "python scripts/mutate.py " + " ".join(sys.argv[1:]), "results": results}
    (OUT / "mutation-results.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    rows = ["| Mutant | Guarantee | Verdict | Caught by (first failing test) | Seconds |", "|---|---|---|---|---|"]
    for r in results:
        caught = r["first_failures"][0] if r["first_failures"] else ("—" if r["verdict"] != "ERROR" else r["summary"])
        rows.append(f"| {r['id']} | {r['guarantee']} | **{r['verdict']}** | `{caught}` | {r['seconds']} |")
    killed = sum(r["verdict"] == "KILLED" for r in results)
    total = sum(r["id"] != "BASELINE" for r in results)
    (OUT / "mutation-report.md").write_text(
        f"# Mutation results\n\nGenerated {doc['generated_at']} by `{doc['command']}`.\n\n"
        f"Killed {killed} of {total} mutants.\n\n" + "\n".join(rows) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    # Every source mutant must still find its target before anything runs: a
    # stale target used to abort a 40-minute run at the mutant that missed.
    stale = [m.id for m in MUTANTS if m.file and (ROOT / m.file).read_text(encoding="utf-8").count(m.old) != 1]
    if stale:
        raise SystemExit(f"mutant targets no longer match the code: {', '.join(stale)}")
    results = [run_mutant(None)]
    print(f"BASELINE: {results[0]['verdict']} {results[0]['summary']}", flush=True)
    if results[0]["verdict"] != "GREEN":
        write_report(results)
        raise SystemExit("baseline is not green; mutation results would mean nothing")
    if not args.baseline:
        for m in MUTANTS:
            if args.ids and m.id not in args.ids:
                continue
            r = run_mutant(m)
            results.append(r)
            print(f"{r['id']}: {r['verdict']} ({r['seconds']}s) {r['first_failures'][:1]}", flush=True)
    write_report(results)


if __name__ == "__main__":
    main()
