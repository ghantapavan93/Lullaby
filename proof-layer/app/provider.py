"""A simulated payment provider with its own durable ledger.

This stands in for an external service. It runs against a *different
database* (``PROVIDER_DATABASE_URL``) with its own transactions, so its
commit and the application's commit are genuinely separate events. No money
moves and no real provider is contacted.

Contract (modeled on documented idempotent-request semantics of real
providers, without claiming any integration):

- ``submit(operation_key, request)`` is idempotent per key. The first call
  creates the operation; later calls with the same request return the stored
  outcome; a different request under the same key is rejected.
- ``lookup(operation_key)`` returns the durable record or ``None`` when the
  provider has authoritatively never seen the key.

Behaviors can be scheduled for testing and the scenario controls:
``succeed`` (default), ``decline`` (FAILED), ``pending`` (PENDING until
resolved), ``lose_response`` (commit SUCCEEDED, then raise as if the reply
was lost in transit), ``unavailable`` (raise before recording anything).
"""
import hashlib
import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import ProviderBase, ProviderSessionLocal

TS = DateTime(timezone=True)
BEHAVIORS = ("succeed", "decline", "pending", "lose_response", "unavailable")


class ProviderOperation(ProviderBase):
    __tablename__ = "provider_operations"
    operation_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    merchant_reference: Mapped[str] = mapped_column(String(80))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16))  # SUCCEEDED | FAILED | PENDING
    decline_code: Mapped[Optional[str]] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class ScheduledBehavior(ProviderBase):
    """The next submission(s) for a merchant reference behave as scheduled."""
    __tablename__ = "provider_scheduled_behaviors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    merchant_reference: Mapped[str] = mapped_column(String(80), index=True)
    behavior: Mapped[str] = mapped_column(String(20))
    consumed: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class ProviderUnavailable(Exception):
    """The call failed before the provider recorded anything."""


class ResponseLost(Exception):
    """The provider committed an outcome but the reply never arrived."""


class ProviderConflict(Exception):
    """Same operation key, different request."""


def _hash(request: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _record(op: ProviderOperation) -> dict[str, Any]:
    return {"operation_key": op.operation_key, "status": op.status, "decline_code": op.decline_code,
            "amount_minor": op.amount_minor, "currency": op.currency,
            "merchant_reference": op.merchant_reference, "attempts": op.attempts,
            "created_at": op.created_at.isoformat() if op.created_at else None,
            "updated_at": op.updated_at.isoformat() if op.updated_at else None}


def schedule(merchant_reference: str, behavior: str) -> None:
    if behavior not in BEHAVIORS:
        raise ValueError(f"unknown behavior {behavior}")
    with ProviderSessionLocal() as db, db.begin():
        db.add(ScheduledBehavior(merchant_reference=merchant_reference, behavior=behavior))


def _take_behavior(db, merchant_reference: str) -> str:
    row = db.execute(select(ScheduledBehavior).where(
        ScheduledBehavior.merchant_reference == merchant_reference,
        ScheduledBehavior.consumed.is_(False)).order_by(ScheduledBehavior.id)
        .with_for_update(skip_locked=True).limit(1)).scalar_one_or_none()
    if row is None:
        return "succeed"
    row.consumed = True
    return row.behavior


def submit(operation_key: str, *, merchant_reference: str, amount_minor: int, currency: str) -> dict[str, Any]:
    request = {"merchant_reference": merchant_reference, "amount_minor": amount_minor, "currency": currency}
    request_hash = _hash(request)
    with ProviderSessionLocal() as db:
        with db.begin():
            existing = db.execute(select(ProviderOperation).where(
                ProviderOperation.operation_key == operation_key).with_for_update()).scalar_one_or_none()
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise ProviderConflict(f"operation {operation_key} exists with different parameters")
                existing.attempts += 1
                existing.updated_at = func.now()
                db.flush()
                db.refresh(existing)
                return _record(existing)
            behavior = _take_behavior(db, merchant_reference)
            result = None
            if behavior != "unavailable":
                status, code = {"succeed": ("SUCCEEDED", None), "lose_response": ("SUCCEEDED", None),
                                "decline": ("FAILED", "card_declined_simulated"),
                                "pending": ("PENDING", None)}[behavior]
                op = ProviderOperation(operation_key=operation_key, request_hash=request_hash,
                                       merchant_reference=merchant_reference, amount_minor=amount_minor,
                                       currency=currency, status=status, decline_code=code)
                db.add(op)
                db.flush()
                db.refresh(op)
                result = _record(op)
        # Provider transaction is committed here (the consumed behavior too).
        if behavior == "unavailable":
            raise ProviderUnavailable("provider unreachable; nothing recorded")
        if behavior == "lose_response":
            raise ResponseLost("provider committed the operation but the response was lost")
        return result


def lookup(operation_key: str) -> Optional[dict[str, Any]]:
    with ProviderSessionLocal() as db:
        op = db.get(ProviderOperation, operation_key)
        return _record(op) if op else None


def resolve_pending(operation_key: str, status: str) -> dict[str, Any]:
    """Simulates the provider settling a PENDING operation later."""
    if status not in ("SUCCEEDED", "FAILED"):
        raise ValueError("status must be SUCCEEDED or FAILED")
    with ProviderSessionLocal() as db, db.begin():
        op = db.execute(select(ProviderOperation).where(
            ProviderOperation.operation_key == operation_key).with_for_update()).scalar_one()
        if op.status == "PENDING":
            op.status = status
            op.decline_code = "settled_failed_simulated" if status == "FAILED" else None
            op.updated_at = func.now()
        db.flush()
        db.refresh(op)
        return _record(op)


def ledger(merchant_reference: Optional[str] = None) -> list[dict[str, Any]]:
    with ProviderSessionLocal() as db:
        stmt = select(ProviderOperation).order_by(ProviderOperation.created_at)
        if merchant_reference:
            stmt = stmt.where(ProviderOperation.merchant_reference == merchant_reference)
        return [_record(op) for op in db.execute(stmt).scalars()]


def create_schema() -> None:
    """The provider owns its schema (it stands for an external system)."""
    from .db import provider_engine
    ProviderBase.metadata.create_all(provider_engine)
