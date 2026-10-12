"""Application schema.

Tenant consistency is a database property, not a convention: every row that
belongs to a household carries ``household_id``, and every reference between
household-owned rows is a *composite* foreign key ``(household_id, x_id)``.
A row in household A therefore cannot point at a child, revision, member,
decision or event of household B even if application code asks it to.

Other invariants the database enforces (see the Alembic migration for the
triggers):

- evidence_events and briefing_revisions are append-only (UPDATE/DELETE raise);
- one correction per evidence event (unique ``supersedes_event_id``);
- one revision number per window (unique ``window_id, revision``);
- one acknowledgment per member per revision;
- one purchase intent per household per idempotency key, one in flight per camera;
- one camera per purchase in every channel (exclusion constraint); plans
  derive from append-only claims, one event per purchase, status and period;
- one provider operation key per intent;
- one outbox job per dedupe key; one experiment assignment per household,
  experiment and config version; one render receipt per household render id.
"""
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey,
    ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

TS = DateTime(timezone=True)


def _now():
    return mapped_column(TS, server_default=func.now(), nullable=False)


# ---------------------------------------------------------------- tenancy

class Household(Base):
    __tablename__ = "households"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64))
    # Bumped on every change to commercial state (plan, hardware). A capability
    # decision snapshots it; a purchase against an older value is stale.
    state_revision: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = _now()


class Member(Base):
    __tablename__ = "members"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(20))  # owner | admin | caregiver
    can_bill: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TS)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("household_id", "id", name="uq_members_tenant"),
        CheckConstraint("role in ('owner','admin','caregiver')", name="ck_members_role"),
    )


class Child(Base):
    __tablename__ = "children"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    __table_args__ = (UniqueConstraint("household_id", "id", name="uq_children_tenant"),)


class Device(Base):
    """Synthetic camera configuration. Plans attach to a camera (Nanit's public
    model), and mount changes are commercial-state changes."""
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), index=True)
    label: Mapped[str] = mapped_column(String(60), server_default="Camera")
    model: Mapped[str] = mapped_column(String(40))
    mount: Mapped[str] = mapped_column(String(40))
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    updated_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("household_id", "id", name="uq_devices_tenant"),
        Index("uq_devices_one_primary", "household_id", unique=True, postgresql_where=text("is_primary")),
    )


CLAIM_CHANNELS = ("seed", "trial", "apple", "google", "web", "activation_code", "simulated_purchase")


class EntitlementClaim(Base):
    """One event from one purchase channel about one camera's plan. Append-only.
    A camera's plan is computed from all of its claims (entitlements.effective_plan)."""
    __tablename__ = "entitlement_claims"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40))
    device_id: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(24))
    external_ref: Mapped[str] = mapped_column(String(120))
    event_status: Mapped[str] = mapped_column(String(12))  # active | pending | revoked
    plan_code: Mapped[str] = mapped_column(String(40))
    starts_at: Mapped[datetime] = mapped_column(TS)
    ends_at: Mapped[Optional[datetime]] = mapped_column(TS)
    request_hash: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    received_at: Mapped[datetime] = _now()
    __table_args__ = (
        # One event per purchase, status and period: a renewal is a new period (migration 0007).
        UniqueConstraint("channel", "external_ref", "event_status", "starts_at", "ends_at", name="uq_claim_event",
                         postgresql_nulls_not_distinct=True),
        ForeignKeyConstraint(["household_id", "device_id"], ["devices.household_id", "devices.id"],
                             name="fk_claim_device"),
        Index("ix_claims_device", "household_id", "device_id"),
        # Every claim for one purchase names the same camera, in every channel and period.
        ExcludeConstraint(("channel", "="), ("external_ref", "="), ("device_id", "<>"),
                          name="ex_claim_one_camera", using="gist"),
        CheckConstraint("event_status IN ('active', 'pending', 'revoked')", name="ck_claim_status"),
        CheckConstraint("channel IN " + str(CLAIM_CHANNELS), name="ck_claim_channel"),
        CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_claim_period"),
    )


class CommercialConfig(Base):
    """Singleton: which catalog and policy versions are live right now."""
    __tablename__ = "commercial_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catalog_version: Mapped[str] = mapped_column(String(40))
    policy_version: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[datetime] = _now()
    __table_args__ = (CheckConstraint("id = 1", name="ck_commercial_config_singleton"),)


# ---------------------------------------------------------------- evidence

class EvidenceEvent(Base):
    """Append-only. A correction is a new row whose supersedes_event_id names
    the row it replaces; the original is never updated."""
    __tablename__ = "evidence_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40), index=True)
    child_id: Mapped[str] = mapped_column(String(40))
    client_event_id: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    # sleep_interval | camera_availability | care_log
    kind: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16))  # CAMERA | PARENT | CAREGIVER
    actor_member_id: Mapped[Optional[str]] = mapped_column(String(40))
    device_id: Mapped[Optional[str]] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(TS)
    ended_at: Mapped[datetime] = mapped_column(TS)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    supersedes_event_id: Mapped[Optional[str]] = mapped_column(String(40))
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    received_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("household_id", "id", name="uq_evidence_tenant"),
        UniqueConstraint("household_id", "client_event_id", name="uq_evidence_client_key"),
        UniqueConstraint("supersedes_event_id", name="uq_evidence_single_correction"),
        ForeignKeyConstraint(["household_id", "child_id"],
                             ["children.household_id", "children.id"], name="fk_evidence_child"),
        ForeignKeyConstraint(["household_id", "supersedes_event_id"],
                             ["evidence_events.household_id", "evidence_events.id"],
                             name="fk_evidence_supersedes"),
        ForeignKeyConstraint(["household_id", "actor_member_id"],
                             ["members.household_id", "members.id"], name="fk_evidence_actor"),
        CheckConstraint("ended_at > started_at", name="ck_evidence_interval"),
        CheckConstraint("kind in ('sleep_interval','camera_availability','care_log')",
                        name="ck_evidence_kind"),
        CheckConstraint("source in ('CAMERA','PARENT','CAREGIVER')", name="ck_evidence_source"),
    )


class BriefingWindow(Base):
    __tablename__ = "briefing_windows"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40), index=True)
    child_id: Mapped[str] = mapped_column(String(40))
    local_date: Mapped[date] = mapped_column(Date)  # evening date in `timezone`
    timezone: Mapped[str] = mapped_column(String(64))
    starts_at: Mapped[datetime] = mapped_column(TS)
    ends_at: Mapped[datetime] = mapped_column(TS)
    current_revision: Mapped[int] = mapped_column(Integer, server_default="0")
    __table_args__ = (
        UniqueConstraint("household_id", "id", name="uq_windows_tenant"),
        UniqueConstraint("child_id", "local_date", name="uq_windows_child_date"),
        ForeignKeyConstraint(["household_id", "child_id"],
                             ["children.household_id", "children.id"], name="fk_windows_child"),
        CheckConstraint("ends_at > starts_at", name="ck_windows_interval"),
    )


class BriefingRevision(Base):
    """Immutable computed briefing. Append-only."""
    __tablename__ = "briefing_revisions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40), index=True)
    window_id: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fingerprints: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB)
    cause: Mapped[dict[str, Any]] = mapped_column(JSONB)
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("household_id", "id", name="uq_revisions_tenant"),
        UniqueConstraint("window_id", "revision", name="uq_revisions_number"),
        ForeignKeyConstraint(["household_id", "window_id"],
                             ["briefing_windows.household_id", "briefing_windows.id"],
                             name="fk_revisions_window"),
        CheckConstraint("revision >= 1", name="ck_revisions_positive"),
    )


class Acknowledgment(Base):
    """A member's action on one specific immutable revision. It records what
    was displayed, never what was understood."""
    __tablename__ = "acknowledgments"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40), index=True)
    window_id: Mapped[str] = mapped_column(String(40))
    revision_id: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    member_id: Mapped[str] = mapped_column(String(40))
    fingerprints: Mapped[dict[str, Any]] = mapped_column(JSONB)
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("member_id", "revision_id", name="uq_ack_member_revision"),
        ForeignKeyConstraint(["household_id", "revision_id"],
                             ["briefing_revisions.household_id", "briefing_revisions.id"],
                             name="fk_ack_revision"),
        ForeignKeyConstraint(["household_id", "member_id"],
                             ["members.household_id", "members.id"], name="fk_ack_member"),
        ForeignKeyConstraint(["household_id", "window_id"],
                             ["briefing_windows.household_id", "briefing_windows.id"],
                             name="fk_ack_window"),
    )


class AckHead(Base):
    """Highest revision each member has acknowledged per window. Updated with a
    conditional upsert, so a delayed older acknowledgment can never lower it."""
    __tablename__ = "ack_heads"
    member_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    window_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    acknowledgment_id: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[datetime] = _now()
    __table_args__ = (
        ForeignKeyConstraint(["household_id", "member_id"],
                             ["members.household_id", "members.id"], name="fk_head_member"),
        ForeignKeyConstraint(["household_id", "window_id"],
                             ["briefing_windows.household_id", "briefing_windows.id"],
                             name="fk_head_window"),
    )


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40), index=True)
    member_id: Mapped[str] = mapped_column(String(40))
    window_id: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("member_id", "window_id", "revision", "kind", name="uq_notification_once"),
        ForeignKeyConstraint(["household_id", "member_id"],
                             ["members.household_id", "members.id"], name="fk_notification_member"),
    )


# ---------------------------------------------------------------- commerce

class CapabilityDecision(Base):
    """Immutable snapshot: what was true for this household when evaluated."""
    __tablename__ = "capability_decisions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40), index=True)
    member_id: Mapped[str] = mapped_column(String(40))
    device_id: Mapped[str] = mapped_column(String(40))
    feature: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16))  # AVAILABLE | BLOCKED | UNKNOWN
    blockers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    offer: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB(none_as_null=True))
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state_revision: Mapped[int] = mapped_column(Integer)
    catalog_version: Mapped[str] = mapped_column(String(40))
    policy_version: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[datetime] = mapped_column(TS)
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("household_id", "id", name="uq_decisions_tenant"),
        ForeignKeyConstraint(["household_id", "member_id"],
                             ["members.household_id", "members.id"], name="fk_decision_member"),
        ForeignKeyConstraint(["household_id", "device_id"], ["devices.household_id", "devices.id"],
                             name="fk_capability_decisions_device"),
        CheckConstraint("status in ('AVAILABLE','BLOCKED','UNKNOWN')", name="ck_decision_status"),
        CheckConstraint("offer is null or status = 'BLOCKED'", name="ck_decision_offer_only_blocked"),
    )


INTENT_STATES = ("PREPARED", "CANCELLED_BEFORE_SUBMISSION", "SUBMITTED",
                 "CONFIRMED", "FAILED", "UNKNOWN")


class PurchaseIntent(Base):
    __tablename__ = "purchase_intents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40), index=True)
    member_id: Mapped[str] = mapped_column(String(40))
    decision_id: Mapped[str] = mapped_column(String(40))
    device_id: Mapped[str] = mapped_column(String(40))
    offer_id: Mapped[str] = mapped_column(String(60))
    idempotency_key: Mapped[str] = mapped_column(String(120))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    terms: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(32))
    provider_operation_key: Mapped[str] = mapped_column(String(80))
    validated_state_revision: Mapped[Optional[int]] = mapped_column(Integer)
    outcome: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB(none_as_null=True))
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("household_id", "id", name="uq_intents_tenant"),
        UniqueConstraint("household_id", "idempotency_key", name="uq_intents_idempotency"),
        UniqueConstraint("provider_operation_key", name="uq_intents_provider_key"),
        # At most one upgrade in flight per camera (migrations 0002, 0003).
        Index("uq_intents_one_live_upgrade", "household_id", "device_id", unique=True,
              postgresql_where=text("state IN ('PREPARED', 'SUBMITTED', 'UNKNOWN')")),
        ForeignKeyConstraint(["household_id", "device_id"], ["devices.household_id", "devices.id"],
                             name="fk_purchase_intents_device"),
        ForeignKeyConstraint(["household_id", "decision_id"],
                             ["capability_decisions.household_id", "capability_decisions.id"],
                             name="fk_intent_decision"),
        ForeignKeyConstraint(["household_id", "member_id"],
                             ["members.household_id", "members.id"], name="fk_intent_member"),
        CheckConstraint("state in " + str(INTENT_STATES), name="ck_intent_state"),
    )


class IntentTransition(Base):
    """Append-only history of every intent state change."""
    __tablename__ = "intent_transitions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    household_id: Mapped[str] = mapped_column(String(40), index=True)
    intent_id: Mapped[str] = mapped_column(String(40), index=True)
    from_state: Mapped[Optional[str]] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    actor: Mapped[str] = mapped_column(String(60))
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    at: Mapped[datetime] = _now()
    __table_args__ = (
        ForeignKeyConstraint(["household_id", "intent_id"],
                             ["purchase_intents.household_id", "purchase_intents.id"],
                             name="fk_transition_intent"),
    )


class OutboxJob(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    household_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # submit_purchase | notify_revision
    dedupe_key: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(16), server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    available_at: Mapped[datetime] = _now()
    lease_until: Mapped[Optional[datetime]] = mapped_column(TS)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_outbox_dedupe"),
        CheckConstraint("state in ('pending','in_progress','done','dead')", name="ck_outbox_state"),
        # Claimable jobs only; finished jobs stay as history (migration 0004, measured).
        Index("ix_outbox_claimable", "id", postgresql_where=text("state IN ('pending', 'in_progress')")),
    )


# ---------------------------------------------------------------- growth

class ExperimentAssignment(Base):
    __tablename__ = "experiment_assignments"
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), primary_key=True)
    experiment_key: Mapped[str] = mapped_column(String(60), primary_key=True)
    config_version: Mapped[str] = mapped_column(String(40), primary_key=True)
    variant: Mapped[str] = mapped_column(String(40))
    assigned_at: Mapped[datetime] = _now()


class ExperimentControl(Base):
    """Run-time switch for one experiment (migration 0005). A killed experiment
    serves its control variant to everyone, writes no new assignments and
    refuses exposure receipts."""
    __tablename__ = "experiment_controls"
    experiment_key: Mapped[str] = mapped_column(String(60), primary_key=True)
    killed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    changed_by: Mapped[Optional[str]] = mapped_column(String(40))
    updated_at: Mapped[datetime] = _now()


class RenderReceipt(Base):
    """Exposure: the client reports that a surface was actually rendered and
    visible. Fetching data never creates one."""
    __tablename__ = "render_receipts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40), index=True)
    member_id: Mapped[str] = mapped_column(String(40))
    render_id: Mapped[str] = mapped_column(String(80))
    surface: Mapped[str] = mapped_column(String(40))
    subject_type: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[str] = mapped_column(String(40))
    experiment_key: Mapped[Optional[str]] = mapped_column(String(60))
    config_version: Mapped[Optional[str]] = mapped_column(String(40))
    variant: Mapped[Optional[str]] = mapped_column(String(40))
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("household_id", "render_id", name="uq_render_once"),
        ForeignKeyConstraint(["household_id", "member_id"],
                             ["members.household_id", "members.id"], name="fk_render_member"),
    )


class AnalyticsEvent(Base):
    """Idempotent product analytics. Properties are allow-listed per event
    name; free text (care notes, names) is never accepted."""
    __tablename__ = "analytics_events"
    household_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    event_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(60), index=True)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB)
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    occurred_at: Mapped[datetime] = _now()
    received_at: Mapped[datetime] = _now()


class AuthorizationRecord(Base):
    """Every authorization decision, allowed or denied, for the X-Ray view."""
    __tablename__ = "authorization_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    household_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    member_id: Mapped[Optional[str]] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(60))
    resource: Mapped[str] = mapped_column(String(120))
    allowed: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(120))
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    at: Mapped[datetime] = _now()


# ---------------------------------------------------------------- purchase to access (migration 0006)

EVIDENCE_CHANNELS = ("web", "apple", "google", "activation_code")
# The last four are the store billing lifecycle (migration 0009, app/provider_lifecycle.py).
EVIDENCE_STATUSES = ("verified", "pending", "refunded", "unverified", "grace", "billing_retry", "paused", "expired")


class PurchaseRef(Base):
    """Which household owns a purchase reference. One owner per (channel, ref):
    evidence for the same purchase can never be recorded under two families."""
    __tablename__ = "purchase_refs"
    channel: Mapped[str] = mapped_column(String(24), primary_key=True)
    external_ref: Mapped[str] = mapped_column(String(120), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("channel", "external_ref", "household_id", name="uq_purchase_ref_owner"),
        CheckConstraint("channel IN " + str(EVIDENCE_CHANNELS), name="ck_purchase_ref_channel"),
    )


class PurchaseEvidence(Base):
    """One provider event about a purchase that may not be connected to any
    camera yet. Append-only. The purchase's status is its highest
    provider_version; two statuses at that version are a conflict."""
    __tablename__ = "purchase_evidence"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(24))
    external_ref: Mapped[str] = mapped_column(String(120))
    plan_code: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16))
    provider_version: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(TS)
    ends_at: Mapped[Optional[datetime]] = mapped_column(TS)
    # The store event this row was translated from; NULL when recorded by status.
    provider_event: Mapped[Optional[str]] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    received_at: Mapped[datetime] = _now()
    __table_args__ = (
        ForeignKeyConstraint(["channel", "external_ref", "household_id"],
                             ["purchase_refs.channel", "purchase_refs.external_ref", "purchase_refs.household_id"],
                             name="fk_evidence_owner"),
        UniqueConstraint("channel", "external_ref", "provider_version", "status", name="uq_evidence_event"),
        Index("ix_purchase_evidence_household", "household_id"),
        CheckConstraint("status IN " + str(EVIDENCE_STATUSES), name="ck_evidence_status"),
        CheckConstraint("provider_version >= 1", name="ck_evidence_version"),
        CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_evidence_period"),
    )


class AccessAction(Base):
    """A parent's command to connect a purchase to a camera. Append-only; one
    per household and idempotency key, and a replay returns the stored outcome."""
    __tablename__ = "access_actions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40))
    member_id: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(24))
    channel: Mapped[str] = mapped_column(String(24))
    external_ref: Mapped[str] = mapped_column(String(120))
    device_id: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[dict[str, Any]] = mapped_column(JSONB)
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        ForeignKeyConstraint(["household_id", "device_id"], ["devices.household_id", "devices.id"],
                             name="fk_access_action_device"),
        ForeignKeyConstraint(["household_id", "member_id"], ["members.household_id", "members.id"],
                             name="fk_access_action_member"),
        UniqueConstraint("household_id", "idempotency_key", name="uq_access_action_key"),
        CheckConstraint("kind IN ('bind')", name="ck_access_action_kind"),
    )


class AccessCheck(Base):
    """A recorded verification of one camera's access: what was usable, under
    which plan, at which household revision. Append-only, so a later change can
    be compared with what was verified."""
    __tablename__ = "access_checks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40))
    device_id: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(16))   # activation | recheck
    status: Mapped[str] = mapped_column(String(40))
    plan_code: Mapped[str] = mapped_column(String(40))
    usable_features: Mapped[list[str]] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64))
    state_revision: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        ForeignKeyConstraint(["household_id", "device_id"], ["devices.household_id", "devices.id"],
                             name="fk_access_check_device"),
        Index("ix_access_checks_device", "household_id", "device_id", "created_at"),
        CheckConstraint("kind IN ('activation', 'recheck')", name="ck_access_check_kind"),
    )


# ---------------------------------------------------------------- video evidence (migration 0010)

VIDEO_RUN_STATES = ("REGISTERED", "VALIDATING", "ANALYZING", "COMPLETED", "FAILED")
VIDEO_LABELS = ("caregiver_present", "infant_moving", "infant_still", "camera_moved", "not_sure")


class VideoAsset(Base):
    """One stored clip per household and content hash. The bytes live in the
    private media store, never in a served directory."""
    __tablename__ = "video_assets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40))
    device_id: Mapped[str] = mapped_column(String(40))
    source_kind: Mapped[str] = mapped_column(String(24))
    storage_key: Mapped[str] = mapped_column(String(120))
    source_sha256: Mapped[str] = mapped_column(String(64))     # the file as received (the dedupe key)
    sha256: Mapped[str] = mapped_column(String(64))            # the stored video-only copy
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    container: Mapped[str] = mapped_column(String(40))
    codec: Mapped[str] = mapped_column(String(16))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    fps: Mapped[Optional[float]] = mapped_column(Float)
    duration_s: Mapped[Optional[float]] = mapped_column(Float)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence_revision: Mapped[int] = mapped_column(Integer, server_default="0")
    retention_until: Mapped[datetime] = mapped_column(TS)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TS)
    created_at: Mapped[datetime] = _now()


class VideoAnalysisRun(Base):
    """The durable record of one analysis: (clip, processor version, config)."""
    __tablename__ = "video_analysis_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40))
    asset_id: Mapped[str] = mapped_column(String(40))
    processor_version: Mapped[str] = mapped_column(String(40))
    config_digest: Mapped[str] = mapped_column(String(16))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), server_default="REGISTERED")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    error_code: Mapped[Optional[str]] = mapped_column(String(40))
    error_detail: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    summary: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    windows: Mapped[Optional[list[Any]]] = mapped_column(JSONB)
    frame_samples: Mapped[Optional[list[Any]]] = mapped_column(JSONB)
    elapsed_ms: Mapped[Optional[int]] = mapped_column(Integer)
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    started_at: Mapped[Optional[datetime]] = mapped_column(TS)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TS)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class VideoObservation(Base):
    """A measured interval of a completed run. Append-only."""
    __tablename__ = "video_observations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40))
    run_id: Mapped[str] = mapped_column(String(40))
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(48))
    t_start: Mapped[float] = mapped_column(Float)
    t_end: Mapped[float] = mapped_column(Float)
    windows: Mapped[int] = mapped_column(Integer)
    frames: Mapped[int] = mapped_column(Integer)
    coverage_min: Mapped[Optional[float]] = mapped_column(Float)
    local_change_mean: Mapped[Optional[float]] = mapped_column(Float)
    local_change_max: Mapped[Optional[float]] = mapped_column(Float)
    brightness_mean: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = _now()


class VideoAnnotation(Base):
    """What a person reported about an interval. Append-only; a correction is
    a new row naming the one it replaces."""
    __tablename__ = "video_annotations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40))
    asset_id: Mapped[str] = mapped_column(String(40))
    member_id: Mapped[str] = mapped_column(String(40))
    client_ref: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(24))
    t_start: Mapped[float] = mapped_column(Float)
    t_end: Mapped[float] = mapped_column(Float)
    supersedes_annotation_id: Mapped[Optional[str]] = mapped_column(String(40))
    trace_id: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = _now()


class VideoAcknowledgment(Base):
    """A member saw this evidence revision. Append-only."""
    __tablename__ = "video_acknowledgments"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40))
    asset_id: Mapped[str] = mapped_column(String(40))
    member_id: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()
