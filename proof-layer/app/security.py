"""Identity, authorization and the household lock.

Identity is a signed development token ``pl1.<member_id>.<hmac>`` issued by
the server (``POST /v1/dev/sessions``). The server resolves household and role
from the members table on every request; nothing the client sends about its
own role or household is trusted. Revocation therefore takes effect on the
next request, with the same token.

This is DEVELOPMENT-ONLY authentication for a local prototype. A real
deployment would use its identity provider; the authorization rules below
would stay the same.

Every resource lookup is scoped by the caller's household. A foreign
identifier is indistinguishable from a missing one (404), so a caller cannot
probe another household for existence.
"""
import contextvars
import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import Optional, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import keep
from .models import AuthorizationRecord, Household, Member

# No published default: without PROOF_LAYER_SECRET each process signs with a
# random key, so tokens cannot be forged from the source code and expire with
# the process.
SECRET = (os.environ.get("PROOF_LAYER_SECRET") or secrets.token_hex(32)).encode()

trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def current_trace_id() -> Optional[str]:
    return trace_id_var.get()


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


@dataclass(frozen=True)
class Principal:
    member_id: str
    household_id: str
    role: str
    can_bill: bool
    display_name: str


# action -> roles allowed. Billing additionally requires members.can_bill.
POLICY = {
    "briefing.read": {"owner", "admin", "caregiver"},
    "briefing.acknowledge": {"owner", "admin", "caregiver"},
    "evidence.correct": {"owner", "admin"},       # mirrors Nanit's documented Owner/Admin logging rule
    "evidence.ingest": {"owner", "admin"},
    "capability.evaluate": {"owner", "admin"},
    "purchase.create": {"owner", "admin"},
    "purchase.read": {"owner", "admin"},
    "growth.render": {"owner", "admin", "caregiver"},
    "xray.read": {"owner", "admin", "caregiver"},
    "commercial.read": {"owner", "admin"},       # plan, devices, decisions, purchases, provider ledger
    "access.read": {"owner", "admin", "caregiver"},   # status only; purchase details need commercial.read
    "access.bind": {"owner", "admin"},                # Nanit's support pages: viewers can't change a plan
    "access.verify": {"owner", "admin", "caregiver"},
    "dev.control": {"owner", "admin"},
    "video.read": {"owner", "admin", "caregiver"},       # gated per camera by the plan, in app/video_evidence.py
    "video.annotate": {"owner", "admin", "caregiver"},   # a caregiver beside the crib can say so
    "video.manage": {"owner", "admin"},                  # register, analyse, retry, delete
}


def issue_token(member_id: str) -> str:
    sig = hmac.new(SECRET, member_id.encode(), hashlib.sha256).hexdigest()[:32]
    return f"pl1.{member_id}.{sig}"


def _member_from_token(token: str) -> str:
    try:
        version, member_id, sig = token.split(".", 2)
    except ValueError:
        raise AuthError(401, "UNAUTHENTICATED", "malformed session token")
    expected = hmac.new(SECRET, member_id.encode(), hashlib.sha256).hexdigest()[:32]
    # Compare bytes: compare_digest raises TypeError on non-ASCII text (RT-4).
    if version != "pl1" or not hmac.compare_digest(sig.encode(), expected.encode()):
        raise AuthError(401, "UNAUTHENTICATED", "invalid session token")
    return member_id


def record_authorization(*, household_id: Optional[str], member_id: Optional[str], action: str,
                         resource: str, allowed: bool, reason: str) -> None:
    """Kept: written with the request's work, or after its rollback, on the
    same connection, so a denial survives the failure it causes."""
    # The resource names caller input (a path id). Bound it and drop NUL so the
    # audit record itself can always be written, denials included (RT-1, RT-2).
    resource = resource.replace("\x00", "\\0")[:120]
    keep(AuthorizationRecord(household_id=household_id, member_id=member_id, action=action,
                             resource=resource, allowed=allowed, reason=reason, trace_id=current_trace_id()))


def authenticate(db: Session, authorization: Optional[str]) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError(401, "UNAUTHENTICATED", "missing bearer session token")
    member_id = _member_from_token(authorization.removeprefix("Bearer ").strip())
    member = db.get(Member, member_id)
    if member is None:
        raise AuthError(401, "UNAUTHENTICATED", "unknown member")
    if member.revoked_at is not None:
        record_authorization(household_id=member.household_id, member_id=member.id,
                             action="authenticate", resource=f"member:{member.id}",
                             allowed=False, reason="membership_revoked")
        raise AuthError(403, "ACCESS_REVOKED", "this caregiver's access was revoked")
    return Principal(member.id, member.household_id, member.role, member.can_bill, member.display_name)


def authorize(principal: Principal, action: str, resource: str) -> None:
    allowed = principal.role in POLICY[action]
    reason = f"role={principal.role}"
    if allowed and action == "purchase.create" and not principal.can_bill:
        allowed, reason = False, "no_billing_permission"
    record_authorization(household_id=principal.household_id, member_id=principal.member_id,
                         action=action, resource=resource, allowed=allowed,
                         reason=reason if allowed else f"denied:{reason}")
    if not allowed:
        raise AuthError(403, "FORBIDDEN", f"{action} is not permitted for this member ({reason})")


T = TypeVar("T")


def load_owned(db: Session, principal: Principal, model: type[T], resource_id: str,
               *, action: str, lock: bool = False) -> T:
    """Fetch a household-owned row by id *within the caller's household*.
    Foreign and missing identifiers both raise 404."""
    stmt = select(model).where(model.id == resource_id,  # type: ignore[attr-defined]
                               model.household_id == principal.household_id)  # type: ignore[attr-defined]
    if lock:
        stmt = stmt.with_for_update()
    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        record_authorization(household_id=principal.household_id, member_id=principal.member_id,
                             action=action, resource=f"{model.__tablename__}:{resource_id}",  # type: ignore[attr-defined]
                             allowed=False, reason="denied:not_in_household")
        raise AuthError(404, "NOT_FOUND", f"{model.__tablename__} not found")  # type: ignore[attr-defined]
    return row


def lock_household(db: Session, household_id: str) -> Household:
    """The serialization point for every household write (build spec §9).
    Lock order everywhere: household, then window or intent rows."""
    return db.execute(select(Household).where(Household.id == household_id).with_for_update()).scalar_one()


def recheck_member(db: Session, principal: Principal) -> None:
    """Re-read membership under the household lock: a revocation committed while
    this request waited for the lock must still stop it."""
    member = db.get(Member, principal.member_id, populate_existing=True)
    if member is None or member.revoked_at is not None or member.household_id != principal.household_id:
        raise AuthError(403, "ACCESS_REVOKED", "membership changed while the request was waiting")
