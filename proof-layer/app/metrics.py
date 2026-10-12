"""Operational metrics in Prometheus format at /metrics.

Two kinds:
- request metrics, recorded by middleware: count and latency by route
  template (never by raw path, so ids don't explode label cardinality);
- state gauges, read from the database at scrape time: outbox backlog and
  oldest due job, purchase intents by state, and the age of the oldest
  undecided purchase, counted from its creation (the number an on-call
  engineer watches after a provider incident).

Aggregates only: no household, member or child identifiers are exported.
"""
from sqlalchemy import func, select
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily

from .db import SessionLocal
from .models import OutboxJob, PurchaseIntent

registry = CollectorRegistry()
REQUESTS = Counter("proof_layer_http_requests", "HTTP requests", ["method", "route", "status"], registry=registry)
LATENCY = Histogram("proof_layer_http_request_seconds", "HTTP request latency", ["method", "route"],
                    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10), registry=registry)


def observe(method: str, route: str, status: int, seconds: float) -> None:
    REQUESTS.labels(method, route, str(status)).inc()
    LATENCY.labels(method, route).observe(seconds)


class StateCollector:
    """Read at scrape time; one short read-only query batch."""

    def collect(self):
        outbox = GaugeMetricFamily("proof_layer_outbox_jobs", "Outbox jobs by kind and state", labels=["kind", "state"])
        oldest = GaugeMetricFamily("proof_layer_outbox_oldest_due_seconds",
                                   "Age of the oldest pending outbox job that is due")
        intents = GaugeMetricFamily("proof_layer_purchase_intents", "Purchase intents by state", labels=["state"])
        undecided = GaugeMetricFamily("proof_layer_undecided_purchase_oldest_seconds",
                                      "Seconds since the oldest still-SUBMITTED or UNKNOWN purchase was created")
        with SessionLocal() as db:
            for kind, state, n in db.execute(select(OutboxJob.kind, OutboxJob.state, func.count())
                                             .group_by(OutboxJob.kind, OutboxJob.state)):
                outbox.add_metric([kind, state], n)
            due = db.execute(select(func.extract("epoch", func.now() - func.min(OutboxJob.available_at)))
                             .where(OutboxJob.state == "pending", OutboxJob.available_at <= func.now())).scalar()
            oldest.add_metric([], float(due or 0))
            for state, n in db.execute(select(PurchaseIntent.state, func.count()).group_by(PurchaseIntent.state)):
                intents.add_metric([state], n)
            age = db.execute(select(func.extract("epoch", func.now() - func.min(PurchaseIntent.created_at)))
                             .where(PurchaseIntent.state.in_(("SUBMITTED", "UNKNOWN")))).scalar()
            undecided.add_metric([], float(age or 0))
        yield from (outbox, oldest, intents, undecided)


registry.register(StateCollector())


def render() -> tuple[bytes, str]:
    return generate_latest(registry), CONTENT_TYPE_LATEST
