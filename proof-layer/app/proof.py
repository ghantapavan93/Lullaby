"""Data & Proof Explorer: the evidence behind the app, read from where it lives.

Nothing here is typed in as a number. Each section is read at request time from
one of three places, and says which:

- ``artifact``: a committed file produced by a script (benchmarks, mutation
  runs, test reports, the public-data manifest);
- ``database``: the live PostgreSQL catalog (tables, constraints, triggers,
  row estimates);
- ``code``: the running modules. Every step of a flow names a function, and its
  file and line are resolved with ``inspect``; a step whose function no longer
  exists is reported as missing, not hidden.

The flow steps and the list of open unknowns are written by hand. They are
labelled ``hand-written`` and are checked by tests (``test_proof.py``).
"""
from __future__ import annotations

import importlib
import inspect
import json
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

APP_ROOT = Path(__file__).resolve().parent.parent
REPO = APP_ROOT.parent
ARTIFACTS = REPO / "artifacts"
MANIFEST = REPO / "data" / "public_research" / "manifest.json"

# ------------------------------------------------------------------ flows (hand-written, code-checked)

FLOWS: list[dict[str, Any]] = [
    {"id": "correction", "title": "Correct a night",
     "summary": "A parent corrects the final wake time. One transaction appends evidence, writes the next "
                "revision and queues the other caregiver's notification.",
     "steps": [
         ("Request schema", "app.main:CorrectionIn", "validation", "Timezone-aware times; expected_revision required"),
         ("Authenticate", "app.security:authenticate", "auth", "Signed token → member; revoked members stop here"),
         ("Lock household", "app.security:lock_household", "transaction", "SELECT … FOR UPDATE: the serialization point"),
         ("Recheck membership", "app.security:recheck_member", "auth", "A revocation that landed while waiting still stops it"),
         ("Authorize", "app.security:authorize", "auth", "Role policy; every decision is logged"),
         ("Scoped load", "app.security:load_owned", "tenancy", "Foreign ids look like missing ids (404)"),
         ("Revision check", "app.engine:apply_correction", "domain", "expected_revision ≠ current → REVISION_CONFLICT"),
         ("Recompute", "app.briefing:compute_facts", "domain", "Interval algebra in UTC; coverage only from usable camera reports"),
         ("Write revision", "app.engine:write_revision", "storage", "Append-only; fingerprints per fact; outbox job in the same commit"),
         ("Notify", "app.worker:process_notify", "worker", "Once per member and revision (unique constraint)"),
     ],
     "tests": ["test_canonical_correction", "test_stale_screen_correction_gets_revision_conflict",
               "test_simultaneous_corrections_one_wins_one_conflicts", "test_revision_and_notification_work_commit_together",
               "test_original_evidence_is_retained_and_database_refuses_rewrites"]},
    {"id": "acknowledgment", "title": "Acknowledge what was shown",
     "summary": "A caregiver acknowledges the revision their screen displayed. The server binds it to that "
                "revision and says what changed since.",
     "steps": [
         ("Request schema", "app.main:AckIn", "validation", "revision_id plus the facts that were displayed"),
         ("Authenticate → lock → recheck → authorize", "app.main:write_scope", "auth", "Same order as every write"),
         ("Bind to displayed revision", "app.acks:acknowledge", "domain", "Never acknowledges a newer revision implicitly"),
         ("Monotonic head", "app.acks:acknowledge", "storage", "Conditional upsert: an old ack can't move the head back"),
         ("Explain differences", "app.acks:member_status", "domain", "Per-fact comparison with the current revision"),
     ],
     "tests": ["test_stale_screen_acknowledgment_binds_to_displayed_revision",
               "test_late_old_acknowledgment_cannot_replace_a_newer_one", "test_acknowledgment_is_idempotent",
               "test_partial_acknowledgment_only_covers_displayed_facts", "test_revoked_caregiver_loses_access_with_the_same_token"]},
    {"id": "eligibility", "title": "Check a feature on one camera",
     "summary": "The camera's plan is derived from its channel claims; its mount comes from the device. "
                "The decision is stored with every version it depends on.",
     "steps": [
         ("Request schema", "app.main:DecisionIn", "validation", "feature, optional camera, surface"),
         ("Authenticate → lock → recheck → authorize", "app.main:write_scope", "auth", "capability.evaluate"),
         ("Camera plan from claims", "app.claims:camera_plan", "domain", "Pure function of the claim set; order and duplicates can't change it"),
         ("Rules", "app.entitlements:evaluate", "domain", "Plan and mount; unknown inputs give UNKNOWN with no offer"),
         ("Being confirmed", "app.decisions:evaluate_now", "domain", "A pending purchase that would unlock it → UNKNOWN, no offer"),
         ("Versioned decision", "app.decisions:create", "storage", "state_revision, catalog, policy, expiry; no offers on safety surfaces"),
     ],
     "tests": ["test_every_offer_unlocks_its_feature_and_hardware_blocks_never_get_offers",
               "test_order_and_duplicates_never_change_the_plan", "test_a_purchase_being_confirmed_is_never_sold_again",
               "test_no_offer_is_ever_attached_on_a_safety_surface", "test_a_second_pending_purchase_makes_earlier_offers_stale"]},
    {"id": "purchase", "title": "Prepare and submit a purchase",
     "summary": "An owned, current decision becomes one durable intent. A worker crosses the submission "
                "boundary and asks an independent provider ledger; reconciliation never invents an outcome.",
     "steps": [
         ("Request schema", "app.main:PurchaseIn", "validation", "Idempotency-Key header (8–120 chars)"),
         ("Authenticate → lock → recheck → authorize", "app.main:write_scope", "auth", "purchase.create needs billing permission"),
         ("Idempotency", "app.purchases:prepare", "domain", "Same key + same request → replay; different request → IDEMPOTENCY_KEY_REUSED"),
         ("Ownership and staleness", "app.decisions:staleness", "domain", "Decision must be this household's and current"),
         ("Live re-check", "app.decisions:evaluate_now", "domain", "Rules re-run against the camera as it is now"),
         ("Durable intent + outbox", "app.purchases:prepare", "storage", "One live upgrade per camera (partial unique index)"),
         ("Claim job", "app.worker:claim", "worker", "FOR UPDATE SKIP LOCKED with a lease"),
         ("Submission boundary", "app.purchases:authorize_submission", "transaction", "PREPARED → SUBMITTED only if nothing changed"),
         ("Provider (separate database)", "app.provider:submit", "external", "Operation key = idempotency at the provider"),
         ("Record outcome", "app.purchases:record_outcome", "transaction", "SUCCEEDED → claim on the camera; PENDING → UNKNOWN"),
         ("Reconcile", "app.worker:reconcile_intents", "worker", "Ask the ledger; requeue the same key if it has no record"),
     ],
     "tests": ["test_simultaneous_same_key_requests_create_one_intent", "test_same_key_with_different_content_conflicts",
               "test_client_price_manipulation_is_rejected", "test_hardware_blocked_decision_cannot_create_a_submission",
               "test_crash_of_a_real_worker_process_is_recovered_by_a_restart",
               "test_provider_unknown_stays_unknown_until_the_provider_decides", "test_concurrent_workers_submit_once"]},
    {"id": "experiment", "title": "Assign, render, count",
     "summary": "Assignment is sticky per household. Exposure is a render receipt sent by the client when the "
                "surface is actually on screen, never inferred from a read.",
     "steps": [
         ("Kill switch", "app.growth:serve", "domain", "Killed: control variant for everyone, no new assignment, exposure refused"),
         ("Sticky assignment", "app.growth:assignment", "domain", "Stored on first use with its config version"),
         ("Render receipt", "app.growth:record_render", "storage", "Once per render id (unique constraint); variant must match"),
         ("Typed analytics", "app.growth:ingest", "validation", "Allow-listed names and property types; server-only events refused"),
         ("Health check", "app.growth:sample_ratio", "analysis", "Chi-square; p < 0.001 flags a mismatch"),
     ],
     "tests": ["test_reading_data_never_creates_exposure", "test_render_receipt_is_recorded_once_per_render",
               "test_assignment_is_sticky_even_if_the_hash_would_change", "test_analytics_are_idempotent_and_allow_listed",
               "test_sample_ratio_check_flags_broken_exposure_and_passes_a_fair_split", "test_rt_kill_switch"]},
    {"id": "referral", "title": "Referral qualification",
     "summary": "Not built. Nanit runs a referral program, and no parent feedback in our sample mentions it, "
                "so it was deferred (research/nanit-intelligence/11_RESEARCH_DECISIONS.md).",
     "steps": [], "tests": []},
]

UNKNOWNS = [
    ("Real store payloads", "Apple and Google notifications are simulated; their formats, signatures and timing are not modelled."),
    ("Nanit's plan limits", "Nanit pages disagree on caregiver limits (50 vs unlimited); the code labels 50 as an assumption."),
    ("Production scale", "Benchmarks are one laptop, one driver; they say nothing about Nanit's systems or traffic."),
    ("Demand", "Forum counts describe a support-channel sample, not prevalence among Nanit families."),
    ("Row-level Nanit data", "None is public. Papers' data are on request; every family in this app is synthetic."),
    ("Real authentication", "Dev tokens only; no identity provider, sessions or device binding."),
]

_CODE_RE = re.compile(r"^(?P<module>[\w.]+):(?P<name>\w+)$")


def resolve(ref: str) -> dict[str, Any]:
    """`module:name` → file and line in the running code, or missing."""
    m = _CODE_RE.match(ref)
    if not m:
        return {"ref": ref, "exists": False}
    try:
        obj = getattr(importlib.import_module(m["module"]), m["name"])
        lines, start = inspect.getsourcelines(obj)
        path = Path(inspect.getsourcefile(obj)).resolve().relative_to(REPO).as_posix()
        return {"ref": ref, "exists": True, "file": path, "line": start, "lines": len(lines)}
    except (ImportError, AttributeError, OSError, TypeError, ValueError):
        return {"ref": ref, "exists": False}


@lru_cache(maxsize=1)
def _test_index() -> dict[str, str]:
    index = {}
    for path in (APP_ROOT / "tests").rglob("test_*.py"):
        for m in re.finditer(r"^\s*def (test_\w+)\(", path.read_text(encoding="utf-8"), re.M):
            index.setdefault(m[1], path.relative_to(REPO).as_posix())
    return index


def flows() -> list[dict[str, Any]]:
    index = _test_index()
    out = []
    for f in FLOWS:
        steps = [{"label": label, "boundary": kind, "note": note, **resolve(ref)} for label, ref, kind, note in f["steps"]]
        tests = [{"name": t, "file": index.get(t), "exists": t in index} for t in f["tests"]]
        out.append({**{k: f[k] for k in ("id", "title", "summary")}, "steps": steps, "tests": tests,
                    "origin": "hand-written; code locations resolved at runtime"})
    return out


# ------------------------------------------------------------------ database (live catalog)

def schema(db: Session) -> dict[str, Any]:
    tables = db.execute(text("""
        SELECT c.relname AS name, GREATEST(c.reltuples, 0)::bigint AS estimated_rows,
               pg_total_relation_size(c.oid) AS bytes
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'
        ORDER BY c.relname""")).mappings().all()
    constraints = db.execute(text("""
        SELECT conrelid::regclass::text AS tbl, conname AS name, contype AS kind,
               pg_get_constraintdef(oid) AS definition
        FROM pg_constraint WHERE connamespace = 'public'::regnamespace AND contype IN ('f', 'u', 'c')
        ORDER BY 1, 2""")).mappings().all()
    partial = db.execute(text("""
        SELECT tablename AS tbl, indexname AS name, indexdef AS definition FROM pg_indexes
        WHERE schemaname = 'public' AND indexdef LIKE 'CREATE UNIQUE INDEX%WHERE%' ORDER BY 1, 2""")).mappings().all()
    triggers = db.execute(text("""
        SELECT event_object_table AS tbl, trigger_name AS name, string_agg(event_manipulation, ',') AS events
        FROM information_schema.triggers WHERE trigger_schema = 'public'
        GROUP BY 1, 2 ORDER BY 1, 2""")).mappings().all()
    version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    # Planner estimates are 0 until a table is analyzed (e.g. right after a
    # reset). Small tables are cheap to count exactly; big ones keep the estimate.
    tables = [dict(t) for t in tables]
    for t in tables:
        if t["estimated_rows"] == 0 and t["bytes"] < 64 * 2**20:
            t["estimated_rows"] = db.execute(text(f'SELECT count(*) FROM "{t["name"]}"')).scalar()
    by_table: dict[str, dict[str, Any]] = {t["name"]: {**t, "foreign_keys": [], "unique": [], "checks": [],
                                                         "partial_unique": [], "append_only": False}
                                           for t in tables}
    kinds = {"f": "foreign_keys", "u": "unique", "c": "checks"}
    for c in constraints:
        if c["tbl"] in by_table:
            by_table[c["tbl"]][kinds[c["kind"]]].append({"name": c["name"], "definition": c["definition"]})
    for i in partial:
        if i["tbl"] in by_table:
            by_table[i["tbl"]]["partial_unique"].append({"name": i["name"], "definition": i["definition"]})
    for t in triggers:
        if t["tbl"] in by_table and "UPDATE" in t["events"] and "DELETE" in t["events"]:
            by_table[t["tbl"]]["append_only"] = True
    return {"origin": "database: live PostgreSQL catalog", "migration": version,
            "tables": list(by_table.values())}


# ------------------------------------------------------------------ artifacts

def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def sources() -> dict[str, Any]:
    m = _load(MANIFEST)
    if m is None:
        return {"origin": "artifact: missing", "sources": []}
    keep = ("id", "title", "publisher", "publication_date", "last_verified", "source_url", "access_status",
            "license", "record_count", "participant_count", "granularity", "can_use_in_prototype",
            "can_redistribute", "proves", "cannot_prove")
    return {"origin": f"artifact: {MANIFEST.relative_to(REPO).as_posix()}",
            "generated_at": m.get("generated_at_utc"),
            "row_level_public": m.get("row_level_nanit_dataset_publicly_downloadable"),
            "sources": [{k: s.get(k) for k in keep} for s in m.get("sources", [])]}


def benchmarks() -> dict[str, Any]:
    runs = []
    for path in sorted((ARTIFACTS / "benchmarks").glob("*.json")):
        d = _load(path)
        if not isinstance(d, dict):
            continue
        runs.append({"file": path.relative_to(REPO).as_posix(), "label": d.get("label"), "scenario": d.get("scenario"),
                     "concurrency": d.get("concurrency"), "measured": d.get("measured"), "invariant": d.get("invariant"),
                     "environment": d.get("environment"), "explain": d.get("explain"),
                     **{k: d.get(k) for k in ("baseline", "after", "change", "speedup", "identical_result",
                                              "write_cost_10k_jobs_ms", "workload", "dataset") if k in d}})
    return {"origin": "artifact: artifacts/benchmarks/*.json", "runs": runs}


def mutation() -> dict[str, Any]:
    d = _load(ARTIFACTS / "mutation" / "mutation-results.json")
    if d is None:
        return {"origin": "artifact: missing", "results": []}
    return {"origin": "artifact: artifacts/mutation/mutation-results.json", **{
        k: d.get(k) for k in ("generated_at", "command", "results")}}


def test_reports() -> dict[str, Any]:
    reports = []
    for path in sorted((ARTIFACTS / "test-results").glob("final-*.xml")):
        try:
            suite = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        s = suite if suite.tag == "testsuite" else suite.find("testsuite")
        if s is None:
            continue
        reports.append({"file": path.relative_to(REPO).as_posix(), **{
            k: s.get(k) for k in ("tests", "failures", "errors", "skipped", "time", "timestamp")}})
    return {"origin": "artifact: artifacts/test-results/final-*.xml (JUnit)", "reports": reports}


def recovery() -> dict[str, Any]:
    traces = []
    for path in sorted((ARTIFACTS / "recovery-traces").glob("*.json")):
        d = _load(path)
        if isinstance(d, dict):
            # The trace's shape only: who acted and the member ids stay in the file.
            transitions = [{k: t.get(k) for k in ("from", "to", "reason", "at")} for t in d.get("transitions") or []]
            record = d.get("provider_record") or {}
            traces.append({"file": path.relative_to(REPO).as_posix(), "state": d.get("state"),
                           "transitions": transitions,
                           "provider_record": {k: record.get(k) for k in ("status", "attempts", "operation_key")}})
    lab = _load(ARTIFACTS / "failure-lab" / "failure-lab.json")
    return {"origin": "artifact: artifacts/recovery-traces/*.json, artifacts/failure-lab/failure-lab.json",
            "traces": traces, "lab": lab}


# What the suite exercises. A report records a digest of exactly these files,
# so whether it describes the running code is a comparison, not a guess from
# file times (a fresh clone gives every file a new time; cold review, 2026-10-11).
CODE_DIRS = ("app", "migrations", "static", "tests")


def report_where() -> str:
    """The report's path as shown to people: repo-relative when it is in the repo."""
    try:
        return REPORT.relative_to(REPO).as_posix()
    except ValueError:
        return REPORT.name


def code_digest(root: Path = APP_ROOT) -> str:
    """SHA-256 over the code the tests exercise, with line endings normalised
    (a checkout may convert them) and caches left out."""
    import hashlib

    h = hashlib.sha256()
    for d in CODE_DIRS:
        for f in sorted((root / d).rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                h.update(f.relative_to(root).as_posix().encode() + b"\0")
                h.update(f.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def git(*args: str) -> str:
    import subprocess

    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _report_meta(path: Path) -> dict[str, Any]:
    """The suite-level facts a report carries: when it ran, and on which code."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {}
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return {}
    props = {p.get("name"): p.get("value") for p in suite.iter("property")}
    return {"ran_at": suite.get("timestamp"), "code_digest": props.get("code_digest"),
            "commit": props.get("commit")}


def build_info() -> dict[str, Any]:
    """Which code is running, and whether the test report was produced on that
    same code. Read from git, the files and the report; nothing is run."""
    meta = _report_meta(REPORT)
    reported = meta.get("code_digest")
    running = code_digest()
    return {"commit": git("rev-parse", "--short", "HEAD") or None,
            "committed_at": git("log", "-1", "--format=%cI") or None,
            "uncommitted_app_changes": bool(git("status", "--porcelain", "--",
                                                *(f"proof-layer/{d}" for d in CODE_DIRS))),
            "code_digest": running[:12],
            "report_generated_at": meta.get("ran_at"), "report_commit": meta.get("commit"),
            "report_code_digest": reported[:12] if reported else None,
            # None: the report doesn't say which code it ran on, so it can't be trusted either.
            "report_matches_code": (reported == running) if reported else None,
            "note": "Certified only when the report names this exact code. Otherwise rerun the suite."}


def explorer(db: Session) -> dict[str, Any]:
    return {
        "labels": {"artifact": "Read from a committed file a script produced",
                   "database": "Read from the live database catalog",
                   "code": "Resolved from the running code",
                   "hand-written": "Written by hand; checked by tests"},
        "sources": sources(), "schema": schema(db), "flows": flows(), "benchmarks": benchmarks(),
        "mutation": mutation(), "tests": test_reports(), "recovery": recovery(),
        "certification": certification(),
        "unknowns": {"origin": "hand-written", "items": [{"topic": t, "detail": d} for t, d in UNKNOWNS]},
    }


# ------------------------------------------------------------------ growth reliability certification

# Each invariant names the code that enforces it and the tests that prove it.
# A blocking invariant without a passing run in the latest JUnit report blocks
# certification: "not run" is never read as "passed".
INVARIANTS: list[dict[str, Any]] = [
    {"id": "no-resale", "blocking": True,
     "statement": "A family that already paid is never offered the same capability again.",
     "code": ["app.decisions:_withhold_if_already_bought", "app.access:assess"],
     "tests": ["test_pending_evidence_waits_and_never_offers_a_purchase",
               "test_unconnected_purchase_withholds_the_offer_for_what_it_unlocks_only",
               "test_every_scenario_reaches_its_expected_status_and_never_sells"]},
    {"id": "hardware-not-billing", "blocking": True,
     "statement": "A mount problem is never answered with a plan.",
     "code": ["app.entitlements:evaluate", "app.access:assess"],
     "tests": ["test_active_plan_on_wrong_mount_is_not_a_billing_problem",
               "test_every_offer_unlocks_its_feature_and_hardware_blocks_never_get_offers"]},
    {"id": "no-transfer", "blocking": True,
     "statement": "A purchase on one camera is never moved to another automatically.",
     "code": ["app.access:bind", "app.claims:_already_recorded"],
     "tests": ["test_purchase_bound_to_another_camera_is_not_moved",
               "test_reassignment_attempt_leaves_verified_access_unchanged"]},
    {"id": "bind-once", "blocking": True,
     "statement": "Concurrent connects of one purchase bind it exactly once.",
     "code": ["app.security:lock_household", "app.claims:record"],
     "tests": ["test_concurrent_connects_of_one_purchase_bind_exactly_once"]},
    {"id": "no-duplicate", "blocking": True,
     "statement": "A camera that already has a paid plan is not given a duplicate of it.",
     "code": ["app.access:bind"],
     "tests": ["test_duplicate_purchase_is_not_connected_to_the_same_camera",
               "test_a_trial_does_not_make_a_paid_purchase_a_duplicate"]},
    {"id": "stale-and-idempotent", "blocking": True,
     "statement": "A connect from an outdated screen is refused; a retry returns the first outcome.",
     "code": ["app.access:bind"],
     "tests": ["test_stale_view_is_refused_with_the_current_revision",
               "test_replay_returns_the_same_outcome_and_key_reuse_is_refused"]},
    {"id": "provider-order", "blocking": True,
     "statement": "The provider's latest version wins; a contradiction is never guessed.",
     "code": ["app.purchase_evidence:purchase_state"],
     "tests": ["test_purchase_state_depends_only_on_the_set_of_events",
               "test_late_older_provider_event_changes_nothing",
               "test_same_version_contradiction_is_conflicting_and_cannot_be_connected"]},
    {"id": "refund-revokes", "blocking": True,
     "statement": "A refund removes access through the claim derivation and keeps the history.",
     "code": ["app.purchase_evidence:_revoke_if_refunded"],
     "tests": ["test_refund_after_activation_revokes_access_and_keeps_history",
               "test_store_active_after_a_refund_is_a_conflict_not_a_reactivation"]},
    {"id": "renewal-continuity", "blocking": True,
     "statement": "Every confirmed period reaches the camera (renewal, extension, late year); an ended plan is "
                  "said plainly, never as a paywall.",
     "code": ["app.purchase_evidence:apply_periods", "app.entitlements:effective_plan"],
     "tests": ["test_renewal_of_a_connected_purchase_keeps_the_camera_active",
               "test_an_extension_with_the_same_start_reaches_the_camera",
               "test_an_older_period_that_arrives_late_is_still_written",
               "test_a_cheaper_plan_at_renewal_takes_effect_when_its_period_starts",
               "test_an_ended_plan_without_renewal_says_so_and_never_sells"]},
    {"id": "tenancy", "blocking": True,
     "statement": "Another family's purchase or camera reads exactly like a missing one.",
     "code": ["app.purchase_evidence:_claim_owner", "app.security:load_owned"],
     "tests": ["test_another_familys_purchase_cannot_be_recorded_or_seen",
               "test_another_household_cannot_put_this_familys_purchase_on_its_camera",
               "test_another_familys_camera_is_not_found",
               "test_database_refuses_evidence_under_a_household_that_does_not_own_the_purchase"]},
    {"id": "pure-reads", "blocking": True,
     "statement": "Reading access writes nothing.",
     "code": ["app.access:assess"],
     "tests": ["test_reading_access_writes_nothing_but_the_authorization_record"]},
    {"id": "three-outcomes", "blocking": True,
     "statement": "Operation completed, capability verified and customer outcome observed are never inferred "
                  "from one another.",
     "code": ["app.access:outcome_review"],
     "tests": ["test_three_outcomes_are_never_inferred_from_one_another",
               "test_server_access_events_cannot_be_posted_by_clients"]},
    {"id": "crash-recovery", "blocking": True,
     "statement": "A worker crash after the provider commits recovers to one provider operation per idempotency key and usable access.",
     "code": ["app.worker:reconcile_intents"],
     "tests": ["test_worker_crash_recovers_to_one_charge_and_usable_access"]},
    {"id": "video-evidence", "blocking": True,
     "statement": "Recorded evidence opens only on a plan with video history, re-checked on every media request; "
                  "missing, dark or stalled video is never reported as stillness; a person's report never changes "
                  "a measurement.",
     "code": ["app.video_evidence:require_gate", "app.main:video_media", "app.video_processing:_classify",
              "app.video_evidence:annotate"],
     "tests": ["test_the_evidence_needs_a_plan_with_video_history",
               "test_a_valid_media_link_stops_working_when_the_member_or_the_plan_goes",
               "test_another_family_cant_see_annotate_or_stream_a_clip",
               "test_the_shipped_demo_clip_is_measured_as_scripted_in_every_window",
               "test_missing_frames_are_missing_evidence_not_stillness",
               "test_a_contradicting_annotation_keeps_the_measurement"]},
    {"id": "billing-lifecycle", "blocking": True,
     "statement": "Billing retry, a pause or an expiry never grants access or sells the plan again; a grace period "
                  "grants access only through its end; recovery returns to the same camera with no new connect.",
     "code": ["app.provider_lifecycle:translate", "app.purchase_evidence:as_of", "app.access:_billing_status",
              "app.decisions:_withhold_if_already_bought"],
     "tests": ["test_billing_retry_never_maps_to_access",
               "test_apple_grace_keeps_access_until_the_grace_end",
               "test_billing_retry_without_grace_removes_access_but_keeps_the_camera",
               "test_billing_recovery_restores_access_on_the_same_camera",
               "test_google_pause_has_no_access_and_says_when_it_resumes",
               "test_a_plan_whose_payment_is_retrying_or_paused_is_never_sold_again",
               "test_a_later_store_word_inside_a_written_grace_window_is_not_hidden",
               "test_refund_during_grace_ends_access_now"]},
]
REPORT = ARTIFACTS / "test-results" / "final-backend.xml"


def _junit_results(path: Path) -> dict[str, list[str]]:
    """test function name -> outcome of each case (parametrized cases share a name)."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {}
    results: dict[str, list[str]] = {}
    for case in root.iter("testcase"):
        outcome = ("failed" if case.find("failure") is not None or case.find("error") is not None
                   else "skipped" if case.find("skipped") is not None else "passed")
        results.setdefault(case.get("name", "").split("[")[0], []).append(outcome)
    return results


def _soak_gate() -> dict[str, Any]:
    runs = sorted((ARTIFACTS / "benchmarks").glob("soak-large-100k-2*.json"))
    d = _load(runs[-1]) if runs else None
    if not isinstance(d, dict):
        return {"id": "soak-4-process", "state": "not_run", "file": None}
    m = d.get("measured", {})
    return {"id": "soak-4-process", "state": "passed" if d.get("invariant", {}).get("held") else "failed",
            "statement": "Four uvicorn processes, 16 clients, about 10 minutes, 100k households: no drift, "
                         "no timeouts.",
            "file": runs[-1].relative_to(REPO).as_posix(),
            "measured": {k: m.get(k) for k in ("requests", "client_timeouts", "p95_ms", "error_rate")},
            "note": "Unresolved. Narrowed by a later diagnosis (audits/SOAK_DIAGNOSIS_2026-10-11.md): with 4 "
                    "processes on this Windows machine a worker sometimes freezes accepting connections on the shared "
                    "port; 1 process never did, and lock waits, pool exhaustion and foreign-key locks were ruled out. "
                    "Not fixed, and not yet run on another OS or behind a proxy. It blocks any production-readiness "
                    "claim, not the access invariants."}


def certification() -> dict[str, Any]:
    results = _junit_results(REPORT)
    index = _test_index()
    rows = []
    for inv in INVARIANTS:
        tests = []
        for name in inv["tests"]:
            outcomes = results.get(name, [])
            state = ("not_run" if not outcomes else "failed" if "failed" in outcomes
                     else "passed" if all(o == "passed" for o in outcomes) else "skipped")
            tests.append({"name": name, "file": index.get(name), "state": state, "cases": len(outcomes)})
        states = {t["state"] for t in tests}
        state = "failed" if "failed" in states else "passed" if states == {"passed"} else "not_run"
        rows.append({**{k: inv[k] for k in ("id", "statement", "blocking")}, "state": state, "tests": tests,
                     "code": [resolve(ref) for ref in inv["code"]]})
    blockers = [r["id"] for r in rows if r["blocking"] and r["state"] != "passed"]
    build = build_info()
    # Passing tests on other code prove nothing about this code.
    verdict = "BLOCKED" if blockers else "CERTIFIED" if build["report_matches_code"] else "UNVERIFIED"
    return {"origin": f"hand-written invariants; results from {report_where()} (JUnit)",
            "report_found": bool(results), "build": build,
            "verdict": verdict, "blocked_by": blockers,
            "invariants": rows, "release_gates": [_soak_gate()],
            "scope": "Certifies the purchase-to-access invariants in this prototype. It is not a claim that "
                     "the application is production-ready."}
