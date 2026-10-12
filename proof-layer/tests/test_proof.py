"""The Data & Proof Explorer only shows evidence that exists.

- Every flow step names a function that exists in the running code, and every
  test a flow cites exists in the suite. A rename can't leave a stale claim.
- The schema comes from the live catalog: append-only tables are the ones with
  update/delete triggers, and the tenant keys are real foreign keys.
- Artifact sections say where they were read from; a missing file is reported
  as missing, never filled in.
- The endpoint needs a session and returns no household records.
"""
from app import proof

from .conftest import CAREGIVER, PARENT


def test_every_flow_step_and_cited_test_exists():
    for flow in proof.flows():
        for step in flow["steps"]:
            assert step["exists"], (flow["id"], step["ref"])
            assert "app/" in step["file"] and step["file"].endswith(".py") and step["line"] > 0
        for t in flow["tests"]:
            assert t["exists"], (flow["id"], t["name"])


def test_unknown_code_reference_is_reported_missing():
    assert proof.resolve("app.engine:no_such_function") == {"ref": "app.engine:no_such_function", "exists": False}
    assert proof.resolve("not a ref")["exists"] is False


def test_schema_is_read_from_the_live_catalog(client, demo):
    body = client.get("/v1/proof", headers=PARENT).json()
    tables = {t["name"]: t for t in body["schema"]["tables"]}
    assert body["schema"]["origin"].startswith("database")
    for name in ("evidence_events", "briefing_revisions", "acknowledgments", "intent_transitions", "entitlement_claims"):
        assert tables[name]["append_only"], name
    assert not tables["purchase_intents"]["append_only"]
    fks = {fk["name"] for fk in tables["entitlement_claims"]["foreign_keys"]}
    assert "fk_claim_device" in fks
    partial = {i["name"] for i in tables["purchase_intents"]["partial_unique"]}
    assert "uq_intents_one_live_upgrade" in partial


def test_sections_name_their_origin_and_need_a_session(client, demo):
    assert client.get("/v1/proof").status_code == 401
    body = client.get("/v1/proof", headers=CAREGIVER).json()
    for key in ("sources", "benchmarks", "mutation", "tests", "recovery"):
        assert body[key]["origin"].startswith("artifact"), key
    assert body["unknowns"]["origin"] == "hand-written"
    text = str(body)
    assert "mem_parent_a" not in text and "pl1." not in text      # no household records or tokens


def test_a_report_from_other_code_is_never_certified(tmp_path, monkeypatch):
    # Every invariant's tests pass in the report; only the code it names differs.
    cases = "".join(f'<testcase classname="t" name="{t}"/>' for i in proof.INVARIANTS for t in i["tests"])

    def report(digest):
        path = tmp_path / f"{digest[:8]}.xml"
        path.write_text(f'<testsuites><testsuite name="pytest" timestamp="2026-10-11T00:00:00">'
                        f'<properties><property name="code_digest" value="{digest}"/></properties>'
                        f'{cases}</testsuite></testsuites>', encoding="utf-8")
        return path

    monkeypatch.setattr(proof, "REPORT", report("0" * 64))
    other = proof.certification()
    assert other["blocked_by"] == [] and other["verdict"] == "UNVERIFIED"
    assert other["build"]["report_matches_code"] is False
    monkeypatch.setattr(proof, "REPORT", report(proof.code_digest()))
    assert proof.certification()["verdict"] == "CERTIFIED"


def test_the_code_digest_ignores_line_endings_and_caches(tmp_path):
    for d in proof.CODE_DIRS:
        (tmp_path / d).mkdir()
    (tmp_path / "app" / "a.py").write_bytes(b"x = 1\n")
    before = proof.code_digest(tmp_path)
    (tmp_path / "app" / "a.py").write_bytes(b"x = 1\r\n")
    (tmp_path / "app" / "__pycache__").mkdir()
    (tmp_path / "app" / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"junk")
    assert proof.code_digest(tmp_path) == before
    (tmp_path / "app" / "a.py").write_bytes(b"x = 2\n")
    assert proof.code_digest(tmp_path) != before
