"""Half-open time intervals ``[start, end)`` over timezone-aware datetimes.

Everything that turns evidence into minutes goes through this module, so
"no double counting" and "clip to the window first" are properties of one
implementation instead of habits repeated at each call site.

Every endpoint is normalized to UTC on the way in. Python compares and
subtracts two datetimes that share a tzinfo by *wall clock*, so a night that
crosses a daylight-saving change would otherwise measure 720 minutes instead
of the 780 that actually elapsed.
"""
from datetime import datetime, timezone
from typing import Iterable

Interval = tuple[datetime, datetime]


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetime: every instant must carry its UTC offset")
    return value.astimezone(timezone.utc)


def clip(intervals: Iterable[Interval], window: Interval) -> list[Interval]:
    """Intersect each interval with the window; drop empty results."""
    w_start, w_end = utc(window[0]), utc(window[1])
    out = []
    for start, end in intervals:
        s, e = max(utc(start), w_start), min(utc(end), w_end)
        if s < e:
            out.append((s, e))
    return out


def union(intervals: Iterable[Interval]) -> list[Interval]:
    """Merge overlapping or touching intervals. Result is sorted and disjoint."""
    merged: list[list[datetime]] = []
    for start, end in sorted((utc(s), utc(e)) for s, e in intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def subtract(base: Iterable[Interval], remove: Iterable[Interval]) -> list[Interval]:
    """``base`` minus ``remove``, both normalized first."""
    result = []
    removal = union(remove)
    for start, end in union(base):
        cursor = start
        for r_start, r_end in removal:
            if r_end <= cursor or r_start >= end:
                continue
            if r_start > cursor:
                result.append((cursor, r_start))
            cursor = max(cursor, r_end)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return result


def intersect(a: Iterable[Interval], b: Iterable[Interval]) -> list[Interval]:
    out = []
    for a_start, a_end in union(a):
        for b_start, b_end in union(b):
            s, e = max(a_start, b_start), min(a_end, b_end)
            if s < e:
                out.append((s, e))
    return union(out)


def minutes(intervals: Iterable[Interval]) -> float:
    """Elapsed minutes of the union. Uses absolute instants, so a window that
    crosses a daylight-saving change measures real elapsed time."""
    return sum((e - s).total_seconds() for s, e in union(intervals)) / 60.0
