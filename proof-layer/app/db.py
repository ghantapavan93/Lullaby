"""Database engines for the two durable boundaries.

The application database and the simulated payment provider are separate
PostgreSQL databases with separate engines and separate transactions. A
provider success therefore cannot be "recorded" by the same commit that
updates the application: the gap between the two commits is real, and the
recovery path is exercised against it.

Both URLs are configurable. Defaults point at the private development cluster
created by ``scripts/devdb.py`` (127.0.0.1:54329, trust auth, user ``proof``).
"""
import contextvars
import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_APP_URL = "postgresql+psycopg://proof@127.0.0.1:54329/proof_layer"
DEFAULT_PROVIDER_URL = "postgresql+psycopg://proof@127.0.0.1:54329/provider_sim"

APP_DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_APP_URL)
PROVIDER_DATABASE_URL = os.environ.get("PROVIDER_DATABASE_URL", DEFAULT_PROVIDER_URL)

if not APP_DATABASE_URL.startswith("postgresql"):
    # The guarantees this prototype claims (row locks, ON CONFLICT, SKIP LOCKED,
    # append-only triggers) are PostgreSQL features. Refuse to run silently
    # weaker rather than pass tests that prove nothing.
    raise RuntimeError("DATABASE_URL must be a PostgreSQL URL (postgresql+psycopg://...)")


class Base(DeclarativeBase):
    """Application schema."""


class ProviderBase(DeclarativeBase):
    """Simulated provider schema; lives in a different database."""


# Sessions run in UTC so every timestamp read back is a UTC instant; local
# presentation uses the household's named timezone explicitly.
_UTC = {"options": "-c timezone=UTC"}
# One request holds at most one pooled connection (see keep()), so a pool of
# 10 + 10 overflow queues excess requests instead of starving them.
engine = create_engine(APP_DATABASE_URL, pool_pre_ping=True, connect_args=_UTC, pool_size=10, max_overflow=10)
provider_engine = create_engine(PROVIDER_DATABASE_URL, pool_pre_ping=True, connect_args=_UTC)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
ProviderSessionLocal = sessionmaker(bind=provider_engine, autoflush=False, expire_on_commit=False)


# Records that must be written whether or not the surrounding work succeeds:
# authorization decisions (allowed and denied) and server analytics such as a
# rejected purchase. They are written on the SAME connection as the work, in
# its transaction on success or right after its rollback on failure, so a
# request never needs a second pooled connection. (Writing them on a separate
# connection starved the pool under 32 concurrent requests: each request held
# one connection while waiting for another. See docs/BENCHMARKS.md.)
_kept: contextvars.ContextVar[Optional[list[Any]]] = contextvars.ContextVar("kept_records", default=None)


def begin_request_scope() -> contextvars.Token:
    """Called once per HTTP request (middleware) so the list is shared by every
    thread the request's dependencies and handler run in."""
    return _kept.set([])


def end_request_scope(token: contextvars.Token) -> None:
    _kept.reset(token)


def keep(record: Any) -> None:
    pending = _kept.get()
    if pending is None:
        raise RuntimeError("keep() called outside a request or transaction()")
    pending.append(record)


def discard_kept() -> int:
    """Drop records queued by keep(): for work that is rolled back on purpose
    (a dry-run probe), so nothing of it survives. Returns how many."""
    return len(_take_kept())


def _take_kept() -> list[Any]:
    pending = _kept.get()
    if not pending:
        return []
    taken = list(pending)
    pending.clear()
    return taken


def _write_after_failure(db: Session) -> None:
    records = _take_kept()
    if records:
        db.rollback()
        with db.begin():
            db.add_all(records)


def get_db() -> Iterator[Session]:
    """Read session. Kept records commit with it; on failure they are written
    after the rollback, on the same connection."""
    db = SessionLocal()
    try:
        yield db
        records = _take_kept()
        if records:
            db.add_all(records)
            db.commit()
    except BaseException:
        _write_after_failure(db)
        raise
    finally:
        db.close()


@contextmanager
def transaction() -> Iterator[Session]:
    """One application transaction: commit on success, roll back on any error.
    Kept records commit inside it, or are written after its rollback."""
    db = SessionLocal()
    token = _kept.set([]) if _kept.get() is None else None
    try:
        with db.begin():
            yield db
            db.add_all(_take_kept())
    except BaseException:
        _write_after_failure(db)
        raise
    finally:
        if token is not None:
            _kept.reset(token)
        db.close()
