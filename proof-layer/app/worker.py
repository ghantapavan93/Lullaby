"""Outbox worker and reconciliation.

Run:  python -m app.worker            # loop
      python -m app.worker --once     # drain due jobs, then exit
      python -m app.worker --once --crash-after-provider
            # kill this process (os._exit) right after the provider commits,
            # before the application records the outcome. Used by the demo
            # and the recovery test to produce a real crash, not a mock.

Jobs are claimed with ``FOR UPDATE SKIP LOCKED`` and a lease. A job whose
worker died is reclaimed when its lease expires. Re-running a submit job is
safe: the intent row says where it got to, and the provider deduplicates on
the fixed operation key, so duplicate deliveries never create a second
external operation.

On start, and every 30 seconds while running, the worker reconciles every
SUBMITTED or UNKNOWN intent against the provider's ledger. Submit jobs for
undecided purchases are never dead-lettered.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from . import provider, purchases, video_evidence
from .db import SessionLocal, transaction
from .models import Member, Notification, OutboxJob, PurchaseIntent
from .security import new_id, trace_id_var

LEASE = timedelta(seconds=float(os.environ.get("PROOF_LAYER_LEASE_SECONDS", "30")))
MAX_ATTEMPTS = 8
MAX_BACKOFF = timedelta(seconds=60)
RECONCILE_EVERY = 30.0  # seconds, in the long-running loop


class SimulatedCrash(BaseException):
    """Raised in-process by tests to stop exactly where a real crash would.
    BaseException so ordinary error handling cannot swallow it."""


def claim(db: Session, worker_id: str) -> Optional[OutboxJob]:
    job = db.execute(
        select(OutboxJob)
        .where(or_(OutboxJob.state == "pending",
                   (OutboxJob.state == "in_progress") & (OutboxJob.lease_until < func.now())),
               OutboxJob.available_at <= func.now())
        .order_by(OutboxJob.id).with_for_update(skip_locked=True).limit(1)).scalar_one_or_none()
    if job is None:
        return None
    job.state = "in_progress"
    job.attempts += 1
    job.lease_until = func.now() + LEASE
    job.updated_at = func.now()
    job.last_error = None
    return job


def finish(job_id: int, *, error: Optional[str] = None, retry_in: Optional[timedelta] = None) -> None:
    with transaction() as db:
        job = db.get(OutboxJob, job_id, with_for_update=True)
        if error is None and retry_in is None:
            job.state, job.lease_until = "done", None
        else:
            job.last_error = (error or "")[:500]
            job.lease_until = None
            # A purchase whose outcome is undecided is never abandoned: its job keeps
            # asking at a capped interval (alert on attempts, don't dead-letter).
            give_up = job.attempts >= MAX_ATTEMPTS and job.kind != "submit_purchase"
            job.state = "dead" if give_up else "pending"
            backoff = retry_in or timedelta(seconds=2 ** min(job.attempts, 6))
            job.available_at = func.now() + min(backoff, MAX_BACKOFF)
        job.updated_at = func.now()


def process_submit(job: OutboxJob, actor: str, crash_after_provider: bool) -> None:
    intent_id, household_id = job.payload["intent_id"], job.household_id
    with transaction() as db:
        intent = purchases.authorize_submission(db, intent_id, household_id, actor)
    if intent is None:
        finish(job.id)
        return
    try:
        record = purchases.call_provider(intent)
        reason = "provider_response"
    except provider.ResponseLost:
        record, reason = provider.lookup(intent.provider_operation_key), "reconciled_after_lost_response"
    except provider.ProviderUnavailable as exc:
        # Nothing was recorded at the provider. Stay SUBMITTED; retry with the same key.
        finish(job.id, error=f"provider unavailable: {exc}")
        return
    if crash_after_provider:
        if os.environ.get("PROOF_LAYER_INPROCESS_CRASH") == "1":
            raise SimulatedCrash("crash after provider commit")
        os._exit(17)  # real process death between the two commits
    with transaction() as db:
        intent = purchases.record_outcome(db, intent_id, household_id, record, actor, reason)
    if intent.state == "UNKNOWN":
        finish(job.id, retry_in=timedelta(seconds=5))  # keep asking the provider
    else:
        finish(job.id)


def process_notify(job: OutboxJob) -> None:
    with transaction() as db:
        recipients = db.execute(select(Member).where(
            Member.household_id == job.household_id, Member.revoked_at.is_(None),
            Member.id != job.payload["by_member_id"])).scalars()
        for member in recipients:
            db.execute(pg_insert(Notification).values(
                id=new_id("ntf"), household_id=job.household_id, member_id=member.id,
                window_id=job.payload["window_id"], revision=job.payload["revision"],
                kind="briefing_corrected",
                body={"revision_id": job.payload["revision_id"],
                      "text": "Last night's briefing was corrected. Review what changed."},
            ).on_conflict_do_nothing(constraint="uq_notification_once"))
    finish(job.id)


def process_video(job: OutboxJob) -> None:
    """Advance one video analysis run. A media problem fails the run with a
    code (the same bytes would fail again). An unexpected error is retried with
    backoff; after MAX_ATTEMPTS the run is failed, never left hanging."""
    def heartbeat() -> None:
        """Keep this worker's lease while it decodes (only if it still holds it)."""
        with transaction() as db:
            db.execute(update(OutboxJob).where(OutboxJob.id == job.id, OutboxJob.attempts == job.attempts,
                                               OutboxJob.state == "in_progress")
                       .values(lease_until=func.now() + LEASE))
    try:
        video_evidence.process(job.payload["run_id"], heartbeat=heartbeat)
    except SimulatedCrash:
        raise
    except Exception as exc:
        if job.attempts >= MAX_ATTEMPTS:
            video_evidence._fail(job.payload["run_id"], "WORKER_GAVE_UP", {"error": type(exc).__name__})
            finish(job.id)
            return
        raise
    finish(job.id)


def reconcile_intents(actor: str) -> list[dict[str, Any]]:
    """Ask the provider about every SUBMITTED or UNKNOWN intent whose submit
    job is not currently leased by a live worker."""
    with SessionLocal() as db:
        rows = db.execute(select(PurchaseIntent.id, PurchaseIntent.household_id,
                                 PurchaseIntent.provider_operation_key)
                          .where(PurchaseIntent.state.in_(("SUBMITTED", "UNKNOWN")))).all()
    results = []
    for intent_id, household_id, key in rows:
        with transaction() as db:
            job = db.execute(select(OutboxJob).where(OutboxJob.dedupe_key == f"submit:{intent_id}")
                             .with_for_update(skip_locked=True)).scalar_one_or_none()
            if job is None or (job.state == "in_progress" and job.lease_until
                               and job.lease_until > datetime.now(timezone.utc)):
                continue  # a live worker owns it
            record = provider.lookup(key)
            if record is None:
                # Authoritative absence: the provider never recorded this key.
                # Re-queue the SAME operation; never invent a new key.
                job.state, job.available_at, job.lease_until = "pending", func.now(), None
                results.append({"intent_id": intent_id, "provider": None, "action": "requeued_same_key"})
                continue
            intent = purchases.record_outcome(db, intent_id, household_id, record, actor,
                                              "reconciled_from_provider_ledger")
            if intent.state in purchases.TERMINAL:
                job.state, job.lease_until = "done", None
            results.append({"intent_id": intent_id, "provider": record["status"], "state": intent.state})
    return results


def run_once(worker_id: str = "worker", crash_after_provider: bool = False) -> int:
    trace_id_var.set(f"trc_{worker_id}_{int(time.time())}")
    processed = 0
    while True:
        with transaction() as db:
            job = claim(db, worker_id)
            if job is None:
                return processed
            db.flush()
            db.expunge(job)
        try:
            if job.kind == "submit_purchase":
                process_submit(job, f"worker:{worker_id}", crash_after_provider)
            elif job.kind == "notify_revision":
                process_notify(job)
            elif job.kind == "analyze_video":
                process_video(job)
            else:
                finish(job.id, error=f"unknown job kind {job.kind}")
        except SimulatedCrash:
            raise
        except Exception as exc:  # recorded on the job; the loop keeps going
            finish(job.id, error=repr(exc))
        processed += 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--crash-after-provider", action="store_true")
    parser.add_argument("--id", default=f"w{os.getpid()}")
    args = parser.parse_args(argv)
    last_reconcile = 0.0
    # A deployed worker touches this file on every pass, so a health check can
    # tell a live loop from a hung one (deploy/gateway reads its age).
    heartbeat = Path(os.environ["PROOF_LAYER_HEARTBEAT"]) if os.environ.get("PROOF_LAYER_HEARTBEAT") else None
    while True:
        if heartbeat is not None:
            heartbeat.touch()
        if time.monotonic() - last_reconcile >= RECONCILE_EVERY or args.once:
            reconciled = reconcile_intents(f"worker:{args.id}")
            if reconciled:
                print("reconciled:", reconciled, flush=True)
            last_reconcile = time.monotonic()
        n = run_once(args.id, args.crash_after_provider)
        if args.once:
            print(f"processed {n} job(s)", flush=True)
            return 0
        time.sleep(1.0)


if __name__ == "__main__":
    sys.exit(main())
