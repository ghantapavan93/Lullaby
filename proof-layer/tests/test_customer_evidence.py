"""Customer evidence cards are honest and executable.

Every card cites a dated public report, paraphrases rather than quotes it, names
a scenario that exists and tests that exist, and states what stays unknown.
"""
import re

import pytest

from app import customer_evidence, proof, scenarios

from .conftest import CAREGIVER

CARDS = customer_evidence.CARDS


@pytest.mark.parametrize("card", CARDS, ids=[c["id"] for c in CARDS])
def test_every_card_is_a_publishable_hypothesis_and_executable(card):
    """Public card text cites no specific post: no thread ids, no counts, no quotes."""
    text = " ".join(str(card[k]) for k in ("title", "symptom", "diagnosis", "existing_solution", "improvement",
                                           "engineering", "unknowns"))
    assert not re.search(r"CN-\d|F\d{3}|S\d{3}|EF-\d|community\.nanit|\d+ of \d+", text), text
    assert len(card["symptom"].split()) <= 35 and '"' not in card["symptom"]
    assert "sources" not in card and "confidence" not in card            # those live in private notes
    assert all(key in scenarios.SCENARIOS for key in card["scenarios"])
    index = proof._test_index()
    assert all(name in index for name in card["tests"]), [n for n in card["tests"] if n not in index]
    assert card["unknowns"] and card["existing_solution"]


def test_private_sources_are_dated_when_present():
    private = customer_evidence.private_sources()
    if not private:
        pytest.skip("private research notes are not part of this copy")
    for card_id, extra in private.items():
        assert extra["sources"] and all(s["ref"] and re.match(r"^\d{4}-\d{2}", s["date"]) for s in extra["sources"])


def test_without_private_notes_the_view_says_so(client, demo, monkeypatch, tmp_path):
    monkeypatch.setattr(customer_evidence, "PRIVATE_SOURCES", tmp_path / "missing.json")
    body = client.get("/v1/customer-evidence", headers=CAREGIVER).json()
    assert body["sources_available"] is False and "not distributed" in body["sources_note"]
    assert "public posts" in body["sources_note"] and "Terms" not in body["sources_note"]
    assert all(c["sources"] == [] and c["confidence"] is None for c in body["cards"])


def test_the_demo_card_exists_and_the_caveat_is_served(client, demo):
    body = client.get("/v1/customer-evidence", headers=CAREGIVER).json()
    assert body["demo_card"] in {c["id"] for c in body["cards"]}
    assert "not incident rates" in body["caveat"] and "not of Nanit's internal cause" in body["caveat"]
    assert "hypothesis" in body["caveat"] and "not a confirmed diagnosis" in body["caveat"]
    assert all(s["exists"] for c in body["cards"] for s in c["scenarios"])


def test_no_card_names_a_person():
    text = repr(CARDS).lower()
    for marker in ("@", "username", "posted by"):
        assert marker not in text


def test_the_catalog_says_whether_its_test_report_ran_on_this_code(monkeypatch, tmp_path):
    # A report from other code must not read as current (cold review, 2026-10-11).
    from app import customer_evidence, proof
    stale = tmp_path / "other.xml"
    stale.write_text('<testsuites><testsuite name="pytest" timestamp="2026-10-11T00:00:00"><properties>'
                     '<property name="code_digest" value="' + "0" * 64 + '"/></properties></testsuite></testsuites>',
                     encoding="utf-8")
    monkeypatch.setattr(proof, "REPORT", stale)
    assert customer_evidence.catalog()["report"]["matches_code"] is False
