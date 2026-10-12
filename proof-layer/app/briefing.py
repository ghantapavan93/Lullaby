"""Turn the effective evidence of one window into briefing facts.

Pure functions: no database, no clock. The same evidence always produces the
same facts and the same fingerprints.

Three questions are kept apart (build spec §8):

1. What could the camera actually observe?   -> ``observation_coverage``, ``unobserved``
2. What did a source record about sleep?      -> ``recorded_sleep``, ``sleep_onset``, ``final_wake``
3. Which interpretation did a parent select?  -> a correction supersedes one record;
                                                its provenance stays visible.

Rules
-----
- Coverage counts only camera availability reports with ``usable: true``,
  unioned and clipped to the window. No availability report at all means
  coverage is UNKNOWN (None), which is different from a measured 0%.
- Parent and caregiver sleep records never add observation coverage.
- Recorded sleep is the union of effective sleep intervals, so duplicates and
  overlaps cannot add minutes.
- Reported (non-camera) sleep that lies inside usable camera observation where
  the camera recorded no sleep is CONTESTED, and contested time is never folded
  into an exact total. A correction is exempt only inside its ``camera_basis``:
  the camera record it replaces. Shortening a camera record is a selection;
  stretching it into time the camera watched and saw no sleep is a conflict,
  whether it arrives as a report or as a correction.
- Reported sleep inside unobserved time counts as recorded sleep with parent
  provenance and is reported separately (``reported_in_unobserved_minutes``):
  the camera did not see it, and the briefing says so.
- Durations are elapsed time between UTC instants, so windows that cross a
  daylight-saving change measure what actually elapsed.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from . import intervals as iv

CAMERA = "CAMERA"
REPORTED_SOURCES = {"PARENT", "CAREGIVER"}

# Facts a caregiver can acknowledge, in display order. The key is the stable
# fact identity used by fingerprints and acknowledgments.
ACK_FACTS = (
    ("recorded_sleep", "Recorded sleep"),
    ("sleep_onset", "Fell asleep"),
    ("final_wake", "Final wake"),
    ("observation_coverage", "Camera observation"),
    ("unobserved", "Unobserved time"),
)


@dataclass(frozen=True)
class EvidenceView:
    id: str
    kind: str
    source: str
    started_at: datetime
    ended_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    supersedes_event_id: Optional[str] = None


def _iso(value: Optional[datetime], tz: ZoneInfo) -> Optional[str]:
    return value.astimezone(tz).isoformat() if value else None


def _round(value: float) -> float:
    return round(value, 1)


def compute_facts(window: iv.Interval, timezone: str, evidence: list[EvidenceView]) -> dict[str, Any]:
    tz = ZoneInfo(timezone)
    w_start, w_end = window = (iv.utc(window[0]), iv.utc(window[1]))
    elapsed = iv.minutes([window])

    availability = [e for e in evidence if e.kind == "camera_availability" and e.source == CAMERA]
    usable = iv.union(iv.clip(((e.started_at, e.ended_at) for e in availability
                               if e.payload.get("usable") is True), window))
    reported_unusable = iv.union(iv.clip(((e.started_at, e.ended_at) for e in availability
                                          if e.payload.get("usable") is False), window))

    # ---- 1. observation
    if not availability:
        coverage = {"state": "unknown", "pct": None, "observed_minutes": None,
                    "window_minutes": _round(elapsed)}
        unobserved = {"state": "unknown", "minutes": None, "intervals": []}
    else:
        observed = iv.minutes(usable)
        pct = _round(100.0 * observed / elapsed)
        state = "none" if observed == 0 else ("full" if observed >= elapsed else "partial")
        coverage = {"state": state, "pct": pct, "observed_minutes": _round(observed),
                    "window_minutes": _round(elapsed)}
        missing = iv.subtract([window], usable)
        confirmed = iv.intersect(missing, reported_unusable)
        gaps = []
        for start, end in missing:
            confirmed_here = iv.minutes(iv.intersect([(start, end)], confirmed))
            gaps.append({
                "start": _iso(start, tz), "end": _iso(end, tz),
                "minutes": _round((end - start).total_seconds() / 60),
                "reason": "camera_reported_unavailable" if confirmed_here >= (end - start).total_seconds() / 60
                          else ("partly_reported_unavailable" if confirmed_here > 0 else "no_observation_reported"),
            })
        unobserved = {"state": "measured", "minutes": _round(iv.minutes(missing)), "intervals": gaps}

    # ---- 2. recorded sleep
    sleep = [e for e in evidence if e.kind == "sleep_interval"]
    camera_sleep = iv.union(iv.clip(((e.started_at, e.ended_at) for e in sleep if e.source == CAMERA), window))
    contested: list[iv.Interval] = []
    reported: list[iv.Interval] = []
    for e in sleep:
        if e.source in REPORTED_SOURCES:
            claimed = iv.clip([(e.started_at, e.ended_at)], window)
            reported.extend(claimed)
            basis = [(datetime.fromisoformat(a), datetime.fromisoformat(b))
                     for a, b in (e.payload or {}).get("camera_basis", [])]
            seen_awake = iv.subtract(iv.intersect(claimed, usable), camera_sleep + basis)
            contested.extend(seen_awake)
    contested = iv.union(contested)
    unseen = iv.subtract([window], usable) if availability else []
    reported_unobserved = iv.minutes(iv.intersect(reported, unseen))

    clipped = [(e, iv.clip([(e.started_at, e.ended_at)], window)) for e in sleep]
    clipped = [(e, c[0]) for e, c in clipped if c]
    all_sleep = iv.union(c for _, c in clipped)
    sources = sorted({e.source for e, _ in clipped})

    if not clipped:
        recorded = {"state": "no_records", "minutes": None, "uncontested_minutes": None,
                    "contested_minutes": 0.0, "reported_in_unobserved_minutes": 0.0, "sources": [],
                    "intervals": []}
    else:
        contested_minutes = iv.minutes(contested)
        uncontested = iv.minutes(iv.subtract(all_sleep, contested))
        recorded = {
            "state": "contested" if contested_minutes else "recorded",
            "minutes": None if contested_minutes else _round(uncontested),
            "uncontested_minutes": _round(uncontested),
            "contested_minutes": _round(contested_minutes),
            "reported_in_unobserved_minutes": _round(reported_unobserved),
            "sources": sources,
            "intervals": [
                {"start": _iso(s, tz), "end": _iso(t, tz), "source": e.source,
                 "event_id": e.id, "corrects": e.supersedes_event_id}
                for e, (s, t) in sorted(clipped, key=lambda pair: pair[1][0])
            ],
        }

    def boundary(pick_end: bool) -> dict[str, Any]:
        if not clipped:
            return {"at": None, "sources": []}
        value = max(t for _, (_, t) in clipped) if pick_end else min(s for _, (s, _) in clipped)
        owners = sorted({e.source for e, (s, t) in clipped if (t if pick_end else s) == value})
        return {"at": _iso(value, tz), "sources": owners,
                "at_window_edge": value == (w_end if pick_end else w_start)}

    # ---- status
    if coverage["state"] == "unknown":
        status = "unknown_observation"
    elif contested:
        status = "conflicting_evidence"
    elif coverage["state"] == "none":
        status = "no_observation"
    elif coverage["state"] == "partial":
        status = "incomplete_observation"
    else:
        status = "complete"

    return {
        "window": {"starts_at": _iso(w_start, tz), "ends_at": _iso(w_end, tz),
                   "timezone": timezone, "elapsed_minutes": _round(elapsed)},
        "status": status,
        "recorded_sleep": recorded,
        "sleep_onset": boundary(pick_end=False),
        "final_wake": boundary(pick_end=True),
        "observation_coverage": coverage,
        "unobserved": unobserved,
        "conflicts": [{"start": _iso(s, tz), "end": _iso(t, tz),
                       "minutes": _round((t - s).total_seconds() / 60),
                       "detail": "reported sleep where the camera observed no sleep"}
                      for s, t in contested],
    }


def _semantic(facts: dict[str, Any], key: str) -> Any:
    """The parts of a fact that carry meaning: value, provenance, uncertainty.
    Formatting and unrelated fields are excluded."""
    f = facts[key]
    if key == "recorded_sleep":
        # The intervals are part of the meaning: moving a sleep block with the same
        # total is a change a caregiver should see.
        return {**{k: f[k] for k in ("state", "minutes", "uncontested_minutes", "contested_minutes",
                                     "reported_in_unobserved_minutes", "sources")},
                # A re-delivered record is the same block: it changes no meaning (RT-7).
                "intervals": list(dict.fromkeys((i["start"], i["end"], i["source"]) for i in f["intervals"]))}
    if key in ("sleep_onset", "final_wake"):
        return {"at": f["at"], "sources": f["sources"]}
    if key == "observation_coverage":
        return {"state": f["state"], "pct": f["pct"]}
    if key == "unobserved":
        return {"state": f["state"], "minutes": f["minutes"],
                "intervals": [(g["start"], g["end"], g["reason"]) for g in f["intervals"]]}
    raise KeyError(key)


def fingerprint(facts: dict[str, Any], key: str) -> str:
    canonical = json.dumps(_semantic(facts, key), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{key}:{canonical}".encode()).hexdigest()


def fingerprints(facts: dict[str, Any]) -> dict[str, str]:
    return {key: fingerprint(facts, key) for key, _ in ACK_FACTS}
