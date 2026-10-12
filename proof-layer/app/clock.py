"""The application's clock for entitlement time.

Which period covers "now", whether a plan has ended, and when a purchase
expires are all read through `now()`. Tests freeze it to make subscription
scenarios reproducible (a renewal that arrives "next month" is a fixed date,
not a race with the calendar).

Two clocks, on purpose:
- this one decides *what is true* about periods (application time);
- the database decides *what happened first*: rows that are ordered against
  each other (a connect and the check that verifies it) take
  `clock_timestamp()` when they are written, never the transaction's start.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

_frozen: Optional[datetime] = None


def now() -> datetime:
    return _frozen if _frozen is not None else datetime.now(timezone.utc)


@contextmanager
def frozen(at: datetime) -> Iterator[None]:
    """Freeze application time (tests only). Nesting restores the outer value."""
    global _frozen
    if at.tzinfo is None:
        raise ValueError("a frozen clock needs a timezone-aware datetime")
    previous, _frozen = _frozen, at
    try:
        yield
    finally:
        _frozen = previous


def advance_to(at: datetime) -> None:
    """Move a frozen clock forward inside `frozen()` (tests only)."""
    global _frozen
    if _frozen is None:
        raise RuntimeError("advance_to needs a frozen clock")
    if at < _frozen:
        raise ValueError("a frozen clock only moves forward")
    _frozen = at
