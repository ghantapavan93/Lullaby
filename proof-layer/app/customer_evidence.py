"""Customer evidence: customer-reported symptoms, mapped to the synthetic
scenario and the tests that exercise our answer to each.

Two layers, on purpose:

- The cards below are publishable: each states a symptom category in our own
  words as a HYPOTHESIS, what this prototype does about it, and what stays
  unknown. They quote nobody and cite no specific post.
- Dated sources, counts and confidence live in a private research file
  (research/customer_evidence_sources.json). They come from public forum
  threads that were read by an automated pass Nanit's Terms prohibit, so they
  are never distributed. When the file is present (the author's machine) the
  view shows them; when it isn't (any public copy) the view says so.

A scenario is a deterministic, synthetic representation of the reported
SYMPTOM, never of Nanit's internal cause, which is not public. A forum report
is not an incident rate, and says nothing about whether Nanit already has
reconciliation infrastructure internally.
"""
import json
from typing import Any

from . import proof, scenarios

PRIVATE_SOURCES = proof.REPO / "research" / "customer_evidence_sources.json"

CARDS: list[dict[str, Any]] = [
    {
        "id": "renewal-not-reflected",
        "title": "Renewed, but the app asks to subscribe",
        "symptom": "A plan renewed with the store, but the app asks for a subscription again.",
        "diagnosis": "Unknown. Candidates: the renewal not carried to the camera, a display defect, or an account "
                     "mismatch. A report can't tell them apart.",
        "existing_solution": "Nanit's support pages send iOS subscription changes to Apple's settings and other "
                             "billing questions to support. We found no public article about a renewal the app "
                             "doesn't show; that doesn't mean the case isn't handled.",
        "improvement": "A confirmed renewal is written to the same camera as a new period, in the same "
                       "transaction. If no renewal arrived, the app says the plan ended and when, instead of "
                       "showing a paywall.",
        "engineering": "Our own model could not represent a second period of one purchase. Migrations 0007 and "
                       "0008 key claims by period and keep one camera per purchase with an exclusion constraint.",
        "scenarios": ["plan_ended", "renewal_reflected"],
        "tests": ["test_renewal", "test_renewal_of_a_connected_purchase_keeps_the_camera_active",
                  "test_an_ended_plan_without_renewal_says_so_and_never_sells",
                  "test_a_renewal_delivered_twice_adds_one_period"],
        "unknowns": ["Nanit's actual cause", "Store server-notification handling (not modelled)"],
    },
    {
        "id": "plan-active-features-locked",
        "title": "Plan active, features locked",
        "symptom": "The app shows the plan as active, but its features don't work on the camera.",
        "diagnosis": "Ambiguous. Candidates: the camera's mount, the plan not covering the feature, or a display "
                     "defect.",
        "existing_solution": "Nanit documents that Insights need a Floor Stand or Wall Mount, and lists what each "
                             "plan includes.",
        "improvement": "Five separate checks: purchase, account, camera, plan, feature. The parent sees which one "
                       "fails, and that a plan can't fix a mount. Nothing is offered for sale.",
        "engineering": "One read-only decision over existing claims and feature rules; the mount is the camera's "
                       "own, not any camera in the household.",
        "scenarios": ["incompatible_hardware"],
        "tests": ["test_active_plan_on_wrong_mount_is_not_a_billing_problem",
                  "test_each_camera_is_judged_by_its_own_mount_and_plan"],
        "unknowns": ["Which candidate applies in any real case"],
    },
    {
        "id": "paid-not-connected",
        "title": "Paid, but not on the camera",
        "symptom": "A plan, code or included trial was paid for, but never reached the camera.",
        "diagnosis": "First-activation mismatch; some reports are trial-policy expectations rather than defects.",
        "existing_solution": "Nanit's support pages apply plans to a chosen camera through Manage Plan and send "
                             "errors to membership support.",
        "improvement": "The confirmed purchase is found and shown as not yet connected; one connect writes it to "
                       "the camera, then a separate check verifies the feature works. No second purchase.",
        "engineering": "Purchase evidence before any camera (migration 0006); connect with household lock, "
                       "expected revision and idempotency key; offers withheld while a paid purchase is "
                       "unconnected.",
        "scenarios": ["paid_not_active", "purchase_uncertain"],
        "tests": ["test_paid_not_active_connects_without_a_new_purchase",
                  "test_pending_evidence_waits_and_never_offers_a_purchase",
                  "test_unconnected_purchase_withholds_the_offer_for_what_it_unlocks_only"],
        "unknowns": ["How retailer bundles and trials are recorded inside Nanit"],
    },
    {
        "id": "code-errors",
        "title": "An activation code won't go in",
        "symptom": "An activation code can't be entered, or is rejected with a generic error.",
        "diagnosis": "Often a redemption-page defect this prototype can't address; sometimes a used or invalid "
                     "code.",
        "existing_solution": "Nanit's support pages describe code entry under Manage Plan and point errors to "
                             "membership support.",
        "improvement": "A code check that says exactly why: on another camera, already on this one, not on this "
                       "account, ended. A second redemption is refused; nothing is moved automatically. The "
                       "scenario shows what a check can explain; it doesn't reproduce a server error.",
        "engineering": "Same ownership and one-camera rules as connect; another family's code reads exactly like "
                       "a missing one.",
        "scenarios": ["paid_wrong_camera"],
        "tests": ["test_purchase_bound_to_another_camera_is_not_moved",
                  "test_another_familys_purchase_cannot_be_recorded_or_seen",
                  "test_an_ended_purchase_cannot_be_connected"],
        "unknowns": ["What a real redemption error was"],
    },
    {
        "id": "upgrade-across-channels",
        "title": "An upgrade didn't apply",
        "symptom": "A higher plan bought through another channel doesn't apply, or an upgrade keeps old limits.",
        "diagnosis": "Cross-channel conflict: two purchases from two channels for one camera.",
        "existing_solution": "Plans are per camera, and iOS plans are managed by Apple. We found no public article "
                             "about a camera with plans from two channels.",
        "improvement": "A higher plan from another channel connects; the camera's plan is derived from both "
                       "purchases, and the team limit moves with it in the same step. A same-or-lower duplicate "
                       "is refused with an explanation instead.",
        "engineering": "The plan and its team limit are one pure function of the camera's claims, whatever their "
                       "channel and order.",
        "scenarios": ["upgrade_across_channels", "paid_twice"],
        "tests": ["test_a_higher_plan_from_another_channel_is_connected_not_refused",
                  "test_duplicate_purchase_is_not_connected_to_the_same_camera",
                  "test_upgrade_reflected_with_team_limit_in_the_same_step",
                  "test_order_and_duplicates_never_change_the_plan"],
        "unknowns": ["How Nanit reconciles channels internally"],
    },
    {
        "id": "wrong-record",
        "title": "The plan is on another record",
        "symptom": "The plan is on another account or camera record, and the app asks to buy again.",
        "diagnosis": "Account or camera-record mismatch, not a payment failure.",
        "existing_solution": "We found no public article on what happens to a plan when a camera is replaced or "
                             "detached; support is the documented path.",
        "improvement": "The status names the stage: on another camera, or not on this account. The parent is "
                       "never sent to buy again. Moving a plan is left to support.",
        "engineering": "Ownership per purchase (one household) and per camera (exclusion constraint). Not built: "
                       "a 'replace camera' command that moves a plan under the household lock.",
        "scenarios": ["paid_wrong_camera"],
        "tests": ["test_purchase_bound_to_another_camera_is_not_moved",
                  "test_database_keeps_one_purchase_on_one_camera_in_every_channel"],
        "unknowns": ["What Nanit does with a plan when a camera is replaced", "Account merging"],
    },
]

DEMO_CARD = "renewal-not-reflected"
CAVEAT = ("Each card is a customer-reported symptom stated as a hypothesis, not a confirmed diagnosis. Each "
          "scenario is a synthetic, deterministic representation of the symptom, not of Nanit's internal cause, "
          "and says nothing about whether Nanit already handles it internally. Public reports are a selected, "
          "partly agent-coded sample, not incident rates.")
NO_SOURCES = ("Each card paraphrases a pattern in parents' public posts on the Nanit Community forum. The dated "
              "sources stay in the author's private research notes and are not distributed with this demo.")


def private_sources() -> dict[str, Any]:
    try:
        return json.loads(PRIVATE_SOURCES.read_text(encoding="utf-8"))["cards"]
    except (OSError, ValueError, KeyError):
        return {}


def catalog() -> dict[str, Any]:
    """Cards with each scenario and test checked against the running code and the
    latest JUnit report, plus private sources when this machine has them."""
    results = proof._junit_results(proof.REPORT)
    index = proof._test_index()
    private = private_sources()
    cards = []
    for card in CARDS:
        tests = []
        for name in card["tests"]:
            outcomes = results.get(name, [])
            state = ("not_run" if not outcomes else "failed" if "failed" in outcomes
                     else "passed" if all(o == "passed" for o in outcomes) else "skipped")
            tests.append({"name": name, "file": index.get(name), "exists": name in index, "state": state})
        extra = private.get(card["id"], {})
        cards.append(card | {
            "tests": tests,
            "scenarios": [{"key": key, "exists": key in scenarios.SCENARIOS,
                           "title": scenarios.SCENARIOS.get(key, {}).get("title"),
                           "focus": scenarios.SCENARIOS.get(key, {}).get("focus")} for key in card["scenarios"]],
            "sources": extra.get("sources", []),
            "confidence": extra.get("confidence"),
            "confidence_note": extra.get("confidence_note"),
        })
    # The same check certification makes: passes in a report from other code
    # say nothing about this code (cold review, 2026-10-11).
    build = proof.build_info()
    return {"cards": cards, "demo_card": DEMO_CARD, "caveat": CAVEAT,
            "report": {"matches_code": build["report_matches_code"], "commit": build["report_commit"],
                       "ran_at": build["report_generated_at"]},
            "sources_available": bool(private), "sources_note": None if private else NO_SOURCES,
            "origin": "cards hand-written as hypotheses; test states from "
                      + proof.report_where()}
