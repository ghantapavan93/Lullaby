"""Versioned capability policy and simulated catalog.

Rules are ASSUMED public rules, restated in our own words, with the public
page each one comes from (sources listed per rule). They are a model, not
Nanit's billing system, and may be out of date: re-check each against its
page by hand before relying on it or publishing it. Prices are list prices
as seen on 2026-10-10 and are used only for a SIMULATED purchase; no payment
provider is contacted.

``evaluate`` is pure: the same inputs and versions always produce the same
answer. An offer is attached only when (a) every blocker is a subscription
blocker and (b) re-evaluating with the offered plan actually makes the feature
available. (b) is checked by running the same rules, so an offer that does
not unlock its feature cannot be constructed.

Unknown inputs never become a yes: no camera on record means the hardware
requirement is UNKNOWN, and an UNKNOWN decision carries no offer.
"""
from typing import Any, Optional

MEMBERSHIPS_URL = "https://www.nanit.com/pages/memberships"
SUPPORT_PLANS = ("https://support.nanit.com/en_us/"
                 "what-is-the-difference-between-the-different-nanit-insights-plans-SJylmn0Kgg")
SLEEP_ANALYSIS_NOTE = "Sleep analysis requires a Floor Stand or Wall Mount (memberships page)."

CATALOGS: dict[str, dict[str, Any]] = {
    "cat-2026-10-public": {
        "plans": {
            "none": {"label": "No Insights plan", "rank": 0, "purchasable": False},
            # Sold for $100/yr on my.nanit.com ("for the first year"); bundle pages show
            # $9/mo billed annually. Prices differ by channel; this is the web price.
            "sleep": {"label": "Sleep", "rank": 1, "purchasable": True,
                      "amount_minor": 10000, "currency": "USD", "billing_period": "YEAR",
                      "source": SUPPORT_PLANS,
                      "price_basis": "First-year web price on my.nanit.com (Nanit support article, Oct 2026). "
                                     "Other Nanit pages show monthly prices."},
            "memories": {"label": "Memories", "rank": 2, "purchasable": True,
                         "amount_minor": 12000, "currency": "USD", "billing_period": "YEAR",
                         "source": MEMBERSHIPS_URL,
                         "price_basis": "Public list price, nanit.com memberships page, Oct 2026"},
            "milestones": {"label": "Milestones", "rank": 3, "purchasable": True,
                           "amount_minor": 30000, "currency": "USD", "billing_period": "YEAR",
                           "source": MEMBERSHIPS_URL,
                           "price_basis": "Public list price, nanit.com memberships page, Oct 2026"},
        },
    },
}

OVERHEAD_MOUNTS = ("floor_stand", "wall_mount")
MOUNT_LABELS = {"floor_stand": "Floor Stand", "wall_mount": "Wall Mount", "flex_stand": "Flex Stand",
                "multi_stand": "Multi-Stand", "anywhere_stand": "Anywhere Stand (Nanit Essential)"}

SUPPORT = "https://support.nanit.com/en_us/"

# Encoded from research/site-audit/ENTITLEMENT_FACTS.md (rows EF-xx). Where
# Nanit's own pages disagree, the rule below is a labeled assumption.
POLICIES: dict[str, dict[str, Any]] = {
    "pol-2026-10-public": {
        "features": {
            "developmental_milestones": {
                "label": "Developmental milestones",
                "parent_value": "clips of milestones the camera notices",
                "plans": ("milestones",),
                "mounts": OVERHEAD_MOUNTS,
                "sources": [SUPPORT + "developmental-milestones-S1u1QhAKxg", MEMBERSHIPS_URL],
                "assumption": "The support article requires the Milestones plan; some Memories copy still "
                              "mentions milestone clips. Floor Stand or Wall Mount required.",
            },
            "nextnap": {
                "label": "NextNap",
                "parent_value": "a suggested window for the next nap",
                "plans": ("memories", "milestones"),
                "mounts": OVERHEAD_MOUNTS,
                "sources": ["https://www.nanit.com/blogs/parent-confidently/nextnap-baby-nap-schedule-predictor",
                            "https://www.nanit.com/products/insights-plan"],
                "assumption": "Memories and Milestones are documented; Sleep-plan eligibility is unclear "
                              "and treated as not included. The mount rule follows the Floor Stand/Wall Mount footnote.",
            },
            "sleep_analysis": {
                "label": "Sleep analysis",
                "parent_value": "sleep tracking and the morning sleep summary",
                "plans": ("sleep", "memories", "milestones"),
                "mounts": OVERHEAD_MOUNTS,
                "sources": [MEMBERSHIPS_URL, SUPPORT + "nanit-sleep-coach-cards-Sk6Q7Pmgfe"],
            },
            "video_history_7d": {
                "label": "7-day video history",
                "parent_value": "seven days of continuous video history",
                "plans": ("milestones",),
                "mounts": None,
                "sources": [MEMBERSHIPS_URL, SUPPORT + "video-history-r12173AYgg"],
            },
        },
    },
}


def plan_label(catalog_version: str, plan_code: Optional[str]) -> str:
    if plan_code is None:
        return "Unknown plan"
    return CATALOGS[catalog_version]["plans"].get(plan_code, {}).get("label", plan_code)


def evaluate(*, feature: str, plan_code: Optional[str], mounts: Optional[list[str]],
             catalog_version: str, policy_version: str, with_offer: bool = True) -> dict[str, Any]:
    policy = POLICIES[policy_version]["features"].get(feature)
    if policy is None:
        return {"status": "UNKNOWN", "blockers": [{"code": "UNKNOWN_FEATURE", "kind": "unknown",
                                                    "resolution": "NONE",
                                                    "parent_message": "We don't recognize this feature."}],
                "offer": None, "requirements": []}
    plans = CATALOGS[catalog_version]["plans"]
    blockers: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []

    requirements = [{"kind": "subscription", "label": "Plan: " + " or ".join(plans[p]["label"] for p in policy["plans"]),
                     "met": None if plan_code is None else plan_code in policy["plans"]}]
    if plan_code is None:
        unknown.append({"code": "PLAN_UNKNOWN", "kind": "subscription", "resolution": "NONE",
                        "parent_message": "We couldn't confirm your current plan."})
    elif plan_code not in policy["plans"]:
        blockers.append({
            "code": "PLAN_REQUIRED", "kind": "subscription", "resolution": "PURCHASE",
            "parent_message": f"{policy['label']} is included with "
                              + " or ".join(plans[p]["label"] for p in policy["plans"])
                              + (". This camera has no plan yet." if plan_code == "none" else
                                 f". Your current plan is {plans.get(plan_code, {}).get('label', plan_code)}."),
            "detail": {"current_plan": plan_code, "eligible_plans": list(policy["plans"])},
        })

    if policy["mounts"]:
        requirements.append({"kind": "hardware",
                             "label": "Camera mount: " + " or ".join(MOUNT_LABELS[m] for m in policy["mounts"]),
                             "met": None if not mounts else any(m in policy["mounts"] for m in mounts)})
        if not mounts:
            unknown.append({"code": "HARDWARE_UNKNOWN", "kind": "hardware", "resolution": "NONE",
                            "parent_message": "We couldn't confirm how your camera is mounted."})
        elif not any(m in policy["mounts"] for m in mounts):
            current = ", ".join(MOUNT_LABELS.get(m, m) for m in mounts)
            blockers.append({
                "code": "MOUNT_NOT_SUPPORTED", "kind": "hardware", "resolution": "SETUP",
                "parent_message": f"{policy['label']} needs the camera above the crib on a Floor Stand or "
                                  f"Wall Mount. It's currently on {'an' if current[:1] in 'AEIOU' else 'a'} {current}. "
                                  "A plan upgrade can't change this.",
                "detail": {"current_mounts": mounts, "supported_mounts": list(policy["mounts"])},
            })

    if unknown:
        return {"status": "UNKNOWN", "blockers": blockers + unknown, "offer": None,
                "requirements": requirements}
    if not blockers:
        return {"status": "AVAILABLE", "blockers": [], "offer": None, "requirements": requirements}

    offer = None
    if with_offer and all(b["resolution"] == "PURCHASE" for b in blockers):
        offer = _cheapest_resolving_offer(feature, plan_code, mounts, catalog_version, policy_version,
                                          [b["code"] for b in blockers])
    return {"status": "BLOCKED", "blockers": blockers, "offer": offer, "requirements": requirements}


def _cheapest_resolving_offer(feature: str, plan_code: str, mounts: Optional[list[str]],
                              catalog_version: str, policy_version: str,
                              blocker_codes: list[str]) -> Optional[dict[str, Any]]:
    plans = CATALOGS[catalog_version]["plans"]
    current_rank = plans.get(plan_code, {"rank": 0})["rank"]
    candidates = sorted(
        (code for code, p in plans.items() if p["purchasable"] and p["rank"] > current_rank),
        key=lambda code: plans[code]["amount_minor"])
    for code in candidates:
        after = evaluate(feature=feature, plan_code=code, mounts=mounts, catalog_version=catalog_version,
                         policy_version=policy_version, with_offer=False)
        if after["status"] == "AVAILABLE":
            p = plans[code]
            return {
                "offer_id": f"offer-{code}-{p['billing_period'].lower()}-{catalog_version}",
                "plan_code": code, "plan_label": p["label"], "feature": feature,
                "amount_minor": p["amount_minor"], "currency": p["currency"],
                "billing_period": p["billing_period"], "catalog_version": catalog_version,
                "policy_version": policy_version, "resolves": blocker_codes, "simulated": True,
                "label": f"Simulated {p['label']} plan — ${p['amount_minor'] // 100}/year",
                "price_source": p["source"], "price_basis": p["price_basis"],
            }
    return None


# ---------------------------------------------------------------- plans from claims

# Team-member limits by plan (assumed public rule). Milestones is "50" on
# most Nanit pages and "unlimited" on one card: 50 is an assumption, labelled.
MEMBER_CAPS = {"none": 2, "sleep": 2, "memories": 10, "milestones": 50}
MEMBER_CAP_ASSUMPTION = "Milestones cap of 50 assumed; one Nanit page says unlimited."


def effective_plan(claims, now, catalog_version: str) -> dict[str, Any]:
    """A camera's plan as a pure function of its channel claims.

    Claims are grouped into transactions by (channel, external_ref). A
    transaction is revoked if any event revoked it, active if an active event
    covers `now`, pending if it only has pending events, otherwise lapsed.
    The plan is the highest-ranked active transaction. Pending transactions
    that would raise the plan put the camera in "checking": an app should say
    "confirming your purchase", never show a paywall for what was just bought.

    Order and duplicates cannot change the result: it depends only on the set
    of (channel, external_ref, event_status, plan, period) facts.
    """
    plans = CATALOGS[catalog_version]["plans"]

    def rank(code: str) -> int:
        return plans.get(code, {"rank": 0})["rank"]

    groups: dict[tuple[str, str], list] = {}
    for c in claims:
        groups.setdefault((c.channel, c.external_ref), []).append(c)

    transactions = []
    for (channel, ref), events in sorted(groups.items()):
        statuses = {e.event_status for e in events}
        covering = [e for e in events if e.event_status == "active" and e.starts_at <= now
                    and (e.ends_at is None or now < e.ends_at)]
        upcoming = [e for e in events if e.event_status == "active" and e.starts_at > now]
        if "revoked" in statuses:
            state = "revoked"
        elif covering:
            state = "active"
        elif upcoming:
            state = "scheduled"          # paid for a period that hasn't started: not "ended"
        elif "active" in statuses:
            state = "lapsed"
        else:
            state = "pending"
        # The plan a purchase grants is the one of the period that covers now;
        # otherwise its latest period's. A renewal can change the tier, down too
        # (found by the 2026-10-11 critique: the highest of every period won).
        if covering:
            plan = max((e.plan_code for e in covering), key=rank)
        else:
            plan = max(events, key=lambda e: (e.starts_at, rank(e.plan_code))).plan_code
        ends = max((e.ends_at for e in covering if e.ends_at), default=None)
        starts_next = min((e.starts_at for e in upcoming), default=None)
        ended = max((e.ends_at for e in events if e.event_status == "active" and e.ends_at and e.ends_at <= now),
                    default=None)
        transactions.append({"channel": channel, "external_ref": ref, "plan_code": plan, "state": state,
                             "ends_at": ends.isoformat() if ends else None,
                             "last_ended": ended.isoformat() if ended else None,
                             "next_start": starts_next.isoformat() if starts_next else None})

    active = [t["plan_code"] for t in transactions if t["state"] == "active"]
    plan_code = max(active, key=rank, default="none")
    checking = sorted({t["plan_code"] for t in transactions
                       if t["state"] == "pending" and rank(t["plan_code"]) > rank(plan_code)}, key=rank)
    return {
        "plan_code": plan_code,
        "plan_label": plans.get(plan_code, {}).get("label", plan_code),
        "state": "checking" if checking else ("active" if plan_code != "none" else "none"),
        "pending_plans": checking,
        "member_cap": MEMBER_CAPS.get(plan_code, 2),
        "transactions": transactions,
    }
