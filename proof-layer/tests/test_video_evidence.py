"""Video evidence: real decoding and measurement, stored and served safely.

Two kinds of test:
- processing: generated clips (app/staged_clip.py) with known content
  (still, movement, darkness, a camera pan, a stalled feed, missing frames,
  jittered timestamps) and broken files. Nothing here uses the local CC0
  infant sample; the last test checks that sample only if it is present;
- the domain, against PostgreSQL through the API:
  - registration and analysis idempotency, including concurrent requests;
  - the plan gate;
  - tenancy;
  - signed media links;
  - annotations that contradict a measurement, and corrections;
  - acknowledgments of older revisions;
  - a worker killed mid-analysis, and failure then retry;
  - the module switched off.

No test asserts a sleep state: the processor doesn't produce one.
"""
import os
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app import seed, staged_clip, video_evidence, video_processing as vp, worker
from app.engine import DomainError
from app.db import SessionLocal
from app.models import (AnalyticsEvent, OutboxJob, VideoAcknowledgment, VideoAnalysisRun, VideoAnnotation, VideoAsset,
                        VideoObservation)

from .conftest import CAREGIVER, OTHER, PARENT

SMALL = (120, 160)


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")

    def make(name, segments, **kw):
        return staged_clip.write(d / name, segments, size=kw.pop("size", SMALL), **kw)

    return {
        "scripted": make("scripted.webm", (("still", 2.0), ("move", 2.0), ("dark", 1.5), ("pan", 1.5),
                                           ("frozen", 2.0), ("small", 2.0))),
        "gap": make("gap.webm", (("still", 2.0), ("gap", 2.0), ("still", 2.0))),
        "jitter": make("jitter.webm", (("still", 2.0), ("move", 2.0)), jitter_ms=6.0),
        "corner": make("corner.webm", (("still", 2.0), ("corner", 2.0))),
        "mpeg4": make("mpeg4.mkv", (("still", 1.0),), codec="mpeg4", container="matroska"),
        "dir": d,
    }


def kinds_at(result, t):
    return next(i["kind"] for i in result["intervals"] if i["t_start"] <= t < i["t_end"])


# ---------------------------------------------------------------- processing

def test_a_scripted_clip_is_measured_as_scripted(clips):
    r = vp.analyze(clips["scripted"])
    assert r["frames"]["analyzed"] == r["frames"]["decoded"] == 330 and r["frames"]["timestamp_gap_count"] == 0
    assert kinds_at(r, 1.5) == "no_significant_movement"
    assert kinds_at(r, 3.0) == "movement"
    assert kinds_at(r, 4.6) == "low_quality"                       # dark: too dark to measure, not "still"
    assert kinds_at(r, 6.0) == "camera_motion"                     # the whole image moved, not the scene
    assert kinds_at(r, 8.0) == "insufficient_evidence"             # a stalled feed is not stillness
    assert next(i for i in r["intervals"] if i["t_start"] <= 8.0 < i["t_end"])["reason"] == "frames_repeated"
    assert r["processor_version"] == vp.PROCESSOR_VERSION and r["config_digest"] == vp.Config().digest()


SCRIPTED_AS = {"still": ("no_significant_movement", "local_change_below_threshold"),
               "move": ("movement", "local_change_above_threshold"),
               "small": ("small_change", "local_change_between_thresholds"),
               "dark": ("low_quality", "too_dark"),
               "pan": ("camera_motion", "whole_image_shifted"),
               "frozen": ("insufficient_evidence", "frames_repeated")}


def test_the_shipped_demo_clip_is_measured_as_scripted_in_every_window(clips):
    # The clip the demo and the public copy actually use, at its real size,
    # every window checked (cold review: the frozen feed after a pan was read
    # as "small change" because the lag reached back into the pan).
    r = vp.analyze(staged_clip.write(clips["dir"] / "shipped.webm"))
    t, script = 0.0, []
    for kind, seconds in staged_clip.STAGED_SEGMENTS:
        script.append((t, t + seconds, kind))
        t += seconds
    assert len(r["windows"]) == round(t)
    for w in r["windows"]:
        kind = next(k for a, b, k in script if a <= w["t_start"] and w["t_end"] <= b)
        assert (w["kind"], w["reason"]) == SCRIPTED_AS[kind], (w["t_start"], kind, w)


def test_a_window_that_straddles_two_conditions_is_never_read_as_still(clips):
    # Windows are fixed one-second slots; here one holds half dark, half pan.
    r = vp.analyze(staged_clip.write(clips["dir"] / "straddle.webm", (("still", 1.0), ("dark", 0.5), ("pan", 1.5)),
                                     size=SMALL))
    mixed = next(w for w in r["windows"] if w["t_start"] == 1.0)
    assert mixed["kind"] != "no_significant_movement"


def test_the_same_file_and_config_give_the_same_result(clips):
    a, b = vp.analyze(clips["scripted"]), vp.analyze(clips["scripted"])
    assert (a["windows"], a["intervals"], a["frame_samples"]) == (b["windows"], b["intervals"], b["frame_samples"])
    changed = vp.analyze(clips["scripted"], vp.Config(movement_fraction=0.5))
    assert changed["config_digest"] != a["config_digest"]          # a different config is a different run


def test_missing_frames_are_missing_evidence_not_stillness(clips):
    r = vp.analyze(clips["gap"])
    assert r["frames"]["timestamp_gap_count"] == 1 and r["frames"]["timestamp_gaps"][0]["gap_s"] >= 2.0
    gap = next(i for i in r["intervals"] if i["t_start"] <= 3.0 < i["t_end"])
    assert (gap["kind"], gap["reason"]) == ("insufficient_evidence", "frames_missing")


def test_a_window_missing_half_its_frames_is_missing_evidence(clips):
    # Not empty, just thin: half a second of frames in a one-second window
    # (mutant M63 survived without this; the other gap test empties a window).
    r = vp.analyze(staged_clip.write(clips["dir"] / "half-gap.webm", (("still", 2.0), ("gap", 0.5), ("still", 1.5)),
                                     size=SMALL))
    thin = next(w for w in r["windows"] if w["t_start"] == 2.0)
    assert 0 < thin["frames"] and thin["coverage"] < vp.Config().min_coverage
    assert (thin["kind"], thin["reason"]) == ("insufficient_evidence", "frames_missing")


def test_jittered_timestamps_still_analyze(clips):
    r = vp.analyze(clips["jitter"])
    assert r["frames"]["timestamp_gap_count"] == 0 and kinds_at(r, 3.0) == "movement"


def test_background_movement_is_reported_as_pixels_changing_not_as_the_baby(clips):
    r = vp.analyze(clips["corner"])
    assert kinds_at(r, 3.0) in ("movement", "small_change")
    assert "baby" not in video_evidence.KIND_TEXT["movement"].lower()   # what moved isn't claimed


def test_small_drift_is_marked_uncertain(clips):
    r = vp.analyze(clips["scripted"])
    assert kinds_at(r, 10.5) in ("small_change", "movement")


@pytest.mark.parametrize("case,code", [
    ("empty", "EMPTY_FILE"), ("text", "NOT_A_VIDEO"), ("png", "NOT_A_VIDEO"), ("mpeg4", "UNSUPPORTED_CODEC"),
    ("garbage", {"UNREADABLE", "CORRUPT_STREAM", "NO_FRAMES", "NO_VIDEO_STREAM"})])
def test_files_that_cant_be_processed_fail_with_a_code(clips, case, code):
    d = clips["dir"]
    path = {"empty": d / "empty.webm", "text": d / "notes.webm", "png": d / "image.webm",
            "mpeg4": clips["mpeg4"], "garbage": d / "garbage.webm"}[case]
    if case == "empty":
        path.write_bytes(b"")
    elif case == "text":
        path.write_text("not a video at all", encoding="utf-8")
    elif case == "png":
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    elif case == "garbage":
        path.write_bytes(b"\x1a\x45\xdf\xa3" + os.urandom(4096))
    with pytest.raises(vp.MediaError) as err:
        vp.analyze(path)
    assert err.value.code in (code if isinstance(code, set) else {code})


def test_a_file_over_the_size_limit_is_refused_before_decoding(clips):
    with pytest.raises(vp.MediaError) as err:
        vp.inspect(clips["scripted"], vp.Limits(max_bytes=1000))
    assert err.value.code == "TOO_LARGE"


def test_a_truncated_file_never_invents_the_missing_part(clips):
    whole = clips["scripted"].read_bytes()
    cut = clips["dir"] / "truncated.webm"
    cut.write_bytes(whole[: len(whole) // 2])
    try:
        r = vp.analyze(cut)
    except vp.MediaError as err:
        assert err.code in ("CORRUPT_STREAM", "NO_FRAMES", "UNREADABLE")
        return
    tail = [w for w in r["windows"] if w["t_start"] >= r["frames"]["last_t"]]
    assert all(w["kind"] == "insufficient_evidence" for w in tail)   # never "no movement" where there are no frames


# ---------------------------------------------------------------- the domain, through the API

def milestones(client, device=seed.CAMERA):
    """The camera's plan includes video history (Milestones), from a store claim."""
    r = client.post("/v1/dev/claims", headers=PARENT, json={"device_id": device, "channel": "web",
                                                             "external_ref": f"web-ms-{device}",
                                                             "event_status": "active", "plan_code": "milestones"})
    assert r.status_code == 200, r.text


def sample(client):
    r = client.post("/v1/dev/video/sample", headers=PARENT, json={})
    assert r.status_code == 200, r.text
    return r.json()


def analyzed(client):
    milestones(client)
    s = sample(client)
    worker.run_once("test")
    return s["asset"]["id"]


def view(client, asset_id, headers=PARENT):
    return client.get(f"/v1/video/assets/{asset_id}", headers=headers)


def count(model):
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(model)).scalar_one()


def annotate(client, asset_id, label, t0, t1, rev, key, headers=PARENT, supersedes=None):
    return client.post(f"/v1/video/assets/{asset_id}/annotations", headers=headers | {"Idempotency-Key": key},
                       json={"label": label, "t_start": t0, "t_end": t1, "expected_revision": rev,
                             "supersedes": supersedes})


def test_the_sample_is_stored_once_and_analysed_by_the_worker(client, demo):
    milestones(client)
    first, again = sample(client), sample(client)
    assert first["asset"]["id"] == again["asset"]["id"] and first["created"] and not again["created"]
    assert first["run"]["id"] == again["run"]["id"] and first["queued"] and not again["queued"]
    assert first["asset"]["source_kind"] == "staged_sample"                    # tests never use the infant clip
    assert count(OutboxJob) == 1 and first["run"]["state"] == "REGISTERED"
    assert worker.run_once("test") == 1
    v = view(client, first["asset"]["id"]).json()
    assert v["run"]["state"] == "COMPLETED" and v["evidence_revision"] == 1
    assert v["run"]["summary"]["frames"]["decoded"] > 0 and v["run"]["input_sha256"] == first["asset"]["sha256"]
    assert len(v["observations"]) == count(VideoObservation) > 0
    assert all(o["source"] == "computer_measured" for o in v["observations"])
    with SessionLocal() as db:
        names = {e.name for e in db.execute(select(AnalyticsEvent)).scalars()}
    assert "video_analysis_completed" in names
    assert worker.run_once("test") == 0                                        # nothing left; no second run


def test_repeated_and_concurrent_analysis_requests_make_one_run(client, demo):
    milestones(client)
    asset_id = sample(client)["asset"]["id"]
    results = []

    def go():
        results.append(client.post(f"/v1/video/assets/{asset_id}/analysis", headers=PARENT).json())

    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len({r["run"]["id"] for r in results}) == 1 and not any(r["created"] for r in results)
    assert count(VideoAnalysisRun) == 1 and count(OutboxJob) == 1


def test_the_evidence_needs_a_plan_with_video_history(client, demo):
    asset_id = sample(client)["asset"]["id"]                                   # Nursery is on the Sleep trial
    worker.run_once("test")
    refused = view(client, asset_id)
    assert refused.status_code == 403 and refused.json()["code"] == "FEATURE_NOT_IN_PLAN"
    assert "observations" not in refused.text
    assert client.post(f"/v1/video/assets/{asset_id}/media-link", headers=PARENT).status_code == 403
    milestones(client)
    assert view(client, asset_id).status_code == 200


def media(client, asset_id, token=None):
    """Fetch clip bytes with an explicit media cookie (None: no cookie at all)."""
    client.cookies.clear()
    return client.get(f"/v1/video/media/{asset_id}", cookies={video_evidence.MEDIA_COOKIE: token} if token else None)


def token_for(client, asset_id, headers=PARENT):
    client.cookies.clear()
    r = client.post(f"/v1/video/assets/{asset_id}/media-link", headers=headers)
    assert r.status_code == 200, r.text
    assert "token" not in r.json()["url"]                                         # never in a URL or a log line
    return r.cookies[video_evidence.MEDIA_COOKIE]


def test_another_family_cant_see_annotate_or_stream_a_clip(client, demo):
    asset_id = analyzed(client)
    assert view(client, asset_id, headers=OTHER).status_code == 404
    assert annotate(client, asset_id, "not_sure", 0, 1, 1, "other-family-key", headers=OTHER).status_code == 404
    assert client.post(f"/v1/video/assets/{asset_id}/media-link", headers=OTHER).status_code == 404
    token = token_for(client, asset_id)
    assert media(client, "vas_other", token).status_code == 404                    # the same token on another id
    assert client.get("/v1/video/assets", headers=OTHER).json()["assets"] == []


def test_media_bytes_need_a_valid_unexpired_link(client, demo):
    asset_id = analyzed(client)
    assert media(client, asset_id).status_code == 404                              # no cookie at all
    assert media(client, asset_id, "forged.forged.1.abc").status_code == 404
    with pytest.raises(DomainError) as bad:                                     # not ASCII: 404, never a 500
        video_evidence.check_media_token("vas.mem.9999999999.\u00e9\u00e9", asset_id)
    assert bad.value.status == 404
    token = token_for(client, asset_id)
    ok = media(client, asset_id, token)
    assert ok.status_code == 200 and ok.headers["cache-control"] == "private, no-store"
    assert ok.content[:4] == b"\x1a\x45\xdf\xa3"
    client.cookies.clear()
    ranged = client.get(f"/v1/video/media/{asset_id}", headers={"Range": "bytes=0-99"},
                        cookies={video_evidence.MEDIA_COOKIE: token})
    assert ranged.status_code == 206 and len(ranged.content) == 100              # a refresh or seek is re-checked too
    with SessionLocal() as db:
        asset = db.get(VideoAsset, asset_id)
        from app.security import Principal
        stale, _ = video_evidence.media_token(Principal(seed.PARENT_A, seed.HOUSEHOLD, "owner", True, "A"), asset,
                                              now=time.time() - 3600)
    expired = media(client, asset_id, stale)
    assert expired.status_code == 401 and expired.json()["code"] == "MEDIA_LINK_EXPIRED"
    # A valid token names one clip: it doesn't open another clip of the same family.
    other = client.post("/v1/dev/video/upload", headers=PARENT,
                        content=staged_clip.write(Path(video_evidence.media_root()) / "second-src.webm",
                                                  (("still", 1.0),), size=SMALL).read_bytes()).json()["asset"]["id"]
    assert media(client, other, token).status_code == 404
    assert not any(Path(p).suffix == ".webm" for p in os.listdir(Path(__file__).parents[1] / "static"))


def test_a_contradicting_annotation_keeps_the_measurement(client, demo):
    asset_id = analyzed(client)
    before = view(client, asset_id).json()
    moving = next(o for o in before["observations"] if o["kind"] == "movement")
    with SessionLocal() as db:
        rows_before = [(o.id, o.kind, o.t_start, o.t_end, o.local_change_mean)
                       for o in db.execute(select(VideoObservation).order_by(VideoObservation.seq)).scalars()]
    r = annotate(client, asset_id, "infant_still", moving["t_start"], moving["t_end"], 1, "parent-note-0001")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["revision"] == 2 and body["measurement_changed"] is False
    assert body["before"]["reported"] == [] and body["after"]["reported"][0]["label"] == "infant_still"
    assert body["before"]["measured"] == body["after"]["measured"]
    after = view(client, asset_id).json()
    note = after["annotations"][0]
    assert note["source"] == "parent_reported" and moving["id"] in note["contradicts"]
    with SessionLocal() as db:
        rows_after = [(o.id, o.kind, o.t_start, o.t_end, o.local_change_mean)
                      for o in db.execute(select(VideoObservation).order_by(VideoObservation.seq)).scalars()]
    assert rows_after == rows_before
    replay = annotate(client, asset_id, "infant_still", moving["t_start"], moving["t_end"], 1, "parent-note-0001")
    assert replay.status_code == 200 and replay.json()["annotation_id"] == body["annotation_id"]
    reused = annotate(client, asset_id, "not_sure", 0, 1, 2, "parent-note-0001")
    assert reused.status_code == 409 and reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    stale = annotate(client, asset_id, "not_sure", 0, 1, 1, "parent-note-0002")
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_EVIDENCE_VIEW"
    outside = annotate(client, asset_id, "not_sure", 0, 999, 2, "parent-note-0003")
    assert outside.status_code == 422


def test_a_correction_is_a_new_revision_and_the_original_stays(client, demo):
    asset_id = analyzed(client)
    first = annotate(client, asset_id, "infant_moving", 0, 2, 1, "note-first-0001").json()
    fixed = annotate(client, asset_id, "caregiver_present", 0, 2, 2, "note-fix-0001",
                     supersedes=first["annotation_id"])
    assert fixed.status_code == 201 and fixed.json()["revision"] == 3
    # "What changed": the replaced report is before, only its correction is after.
    assert [x["label"] for x in fixed.json()["before"]["reported"]] == ["infant_moving"]
    assert [x["label"] for x in fixed.json()["after"]["reported"]] == ["caregiver_present"]
    v = view(client, asset_id).json()
    original = next(a for a in v["annotations"] if a["id"] == first["annotation_id"])
    assert original["label"] == "infant_moving" and original["superseded_by"] == fixed.json()["annotation_id"]
    twice = annotate(client, asset_id, "not_sure", 0, 2, 3, "note-fix-0002", supersedes=first["annotation_id"])
    assert twice.status_code == 409 and twice.json()["code"] == "ALREADY_CORRECTED"
    with SessionLocal() as db, pytest.raises(Exception):
        db.execute(text("UPDATE video_annotations SET label = 'not_sure'"))       # append-only in the database
        db.commit()


def test_a_caregivers_older_acknowledgment_keeps_its_scope(client, demo):
    asset_id = analyzed(client)
    assert client.post(f"/v1/video/assets/{asset_id}/acknowledge", headers=CAREGIVER,
                       json={"revision": 1}).status_code == 200
    annotate(client, asset_id, "caregiver_present", 1, 3, 1, "parent-note-ack1")
    v = view(client, asset_id).json()
    ack = next(a for a in v["acknowledgments"] if a["member_id"] == seed.CAREGIVER_B)
    assert ack["revision"] == 1 and ack["changed_since"] is True and v["evidence_revision"] == 2
    assert client.post(f"/v1/video/assets/{asset_id}/acknowledge", headers=CAREGIVER,
                       json={"revision": 9}).status_code == 422
    with SessionLocal() as db:
        assert db.execute(select(VideoAcknowledgment.revision)).scalars().all() == [1]
    caregiver_note = annotate(client, asset_id, "not_sure", 0, 1, 2, "caregiver-note-1", headers=CAREGIVER)
    assert caregiver_note.status_code == 201                                   # a caregiver can report, too


def test_a_worker_killed_mid_analysis_is_recovered_by_the_next(client, demo, monkeypatch):
    milestones(client)
    s = sample(client)

    def crash(n):
        if n == 20:
            raise worker.SimulatedCrash("killed mid-analysis")

    real = video_evidence.process
    monkeypatch.setattr(video_evidence, "process", lambda run_id, **kw: real(run_id, on_frame=crash, **kw))
    with pytest.raises(worker.SimulatedCrash):
        worker.run_once("doomed")
    with SessionLocal() as db:
        run = db.get(VideoAnalysisRun, s["run"]["id"])
        assert run.state == "ANALYZING" and db.execute(select(func.count()).select_from(VideoObservation)).scalar_one() == 0
        db.execute(text("UPDATE outbox SET lease_until = now() - interval '1 second'"))   # the lease runs out
        db.commit()
    monkeypatch.setattr(video_evidence, "process", real)
    assert worker.run_once("rescuer") == 1
    v = view(client, s["asset"]["id"]).json()
    assert v["run"]["state"] == "COMPLETED" and v["run"]["attempts"] == 2
    assert len({o["seq"] for o in v["observations"]}) == len(v["observations"]) == count(VideoObservation)


def test_a_failed_run_says_why_and_a_retry_succeeds(client, demo):
    milestones(client)
    s = sample(client)
    stored = video_evidence.media_root() / f"{s['asset']['sha256']}.webm"
    assert stored.is_file()
    moved = stored.with_suffix(".hidden")
    stored.replace(moved)
    try:
        worker.run_once("test")
        v = view(client, s["asset"]["id"]).json()
        assert v["run"]["state"] == "FAILED" and v["run"]["error_code"] == "MEDIA_UNAVAILABLE"
        assert v["observations"] == []
        token = token_for(client, s["asset"]["id"])
        assert media(client, s["asset"]["id"], token).status_code == 410
    finally:
        moved.replace(stored)
    assert client.post(f"/v1/video/assets/{s['asset']['id']}/analysis/retry", headers=PARENT).status_code == 202
    worker.run_once("test")
    assert view(client, s["asset"]["id"]).json()["run"]["state"] == "COMPLETED"


def test_uploads_are_limited_inspected_and_never_named(client, demo, clips):
    milestones(client)
    big = client.post("/v1/dev/video/upload", headers=PARENT, content=b"\x1a\x45\xdf\xa3" + b"\x00" * (9 * 1024 * 1024))
    assert big.status_code == 413 and big.json()["code"] == "TOO_LARGE"
    fake = client.post("/v1/dev/video/upload", headers=PARENT | {"X-Filename": "../../etc/passwd.webm"},
                       content=b"#!/bin/sh\necho hi\n")
    assert fake.status_code == 422 and fake.json()["code"] == "NOT_A_VIDEO"
    ok = client.post("/v1/dev/video/upload", headers=PARENT, content=clips["scripted"].read_bytes())
    assert ok.status_code == 200 and ok.json()["asset"]["source_kind"] == "local_import"
    assert "passwd" not in ok.text and list((video_evidence.media_root() / "incoming").iterdir()) == []
    assert client.post("/v1/dev/video/upload", headers=CAREGIVER, content=b"x").status_code == 403


def test_reading_the_evidence_writes_nothing(client, demo):
    asset_id = analyzed(client)
    tables = (AnalyticsEvent, VideoAnnotation, VideoAcknowledgment, VideoObservation, VideoAnalysisRun, OutboxJob)
    before = [count(t) for t in tables]
    for _ in range(3):
        assert view(client, asset_id).status_code == 200
        client.get("/v1/video/assets", headers=PARENT)
    assert [count(t) for t in tables] == before


def test_deleting_a_clip_removes_its_bytes_and_keeps_the_numbers(client, demo):
    asset_id = analyzed(client)
    token = token_for(client, asset_id)
    assert client.delete(f"/v1/video/assets/{asset_id}", headers=CAREGIVER).status_code == 403
    gone = client.delete(f"/v1/video/assets/{asset_id}", headers=PARENT).json()
    assert gone["deleted"] and gone["bytes_removed"]
    assert media(client, asset_id, token).status_code == 410
    assert count(VideoObservation) > 0


def test_the_access_demo_works_with_the_video_module_off(client, demo, monkeypatch):
    monkeypatch.setenv("PROOF_LAYER_VIDEO", "0")
    assert client.get("/v1/video/status", headers=PARENT).status_code == 404
    assert client.post("/v1/dev/video/sample", headers=PARENT, json={}).status_code == 404
    built = client.post("/v1/dev/access-scenario", headers=PARENT, json={"scenario": "renewal_reflected"})
    assert built.status_code == 200
    assert client.get(f"/v1/access/cameras/{seed.CAMERA}", headers=PARENT).json()["status"] == "ACTIVE_FEATURE_USABLE"


@pytest.mark.skipif(not video_evidence.LICENSED_SAMPLE["path"].is_file(), reason="the local CC0 sample isn't present")
def test_the_local_cc0_sample_decodes_as_recorded():
    path = video_evidence.LICENSED_SAMPLE["path"]
    assert vp.sha256_file(path) == video_evidence.LICENSED_SAMPLE["sha256"]
    probe = vp.inspect(path)
    assert (probe["codec"], probe["width"], probe["height"], probe["fps"]) == ("vp9", 1080, 1920, 30.0)
    assert round(probe["duration_s"], 3) == 33.901


def test_a_real_infant_recording_is_used_only_when_asked_for(clips, monkeypatch):
    # The page says "Synthetic child records"; a real baby must be an explicit choice.
    fake = clips["dir"] / "pretend-cc0.webm"
    fake.write_bytes(clips["mpeg4"].read_bytes())
    monkeypatch.setitem(video_evidence.LICENSED_SAMPLE, "path", fake)
    monkeypatch.setitem(video_evidence.LICENSED_SAMPLE, "sha256", vp.sha256_file(fake))
    monkeypatch.delenv("PROOF_LAYER_SAMPLE", raising=False)
    assert video_evidence.sample_source()[1] == "staged_sample"
    monkeypatch.setenv("PROOF_LAYER_SAMPLE", "cc0")
    path, kind, provenance = video_evidence.sample_source()
    assert (path, kind) == (fake, "licensed_sample")
    assert "Real infant recording" in provenance["caption"] and "Not synthetic" in provenance["caption"]


# ---------------------------------------------------------------- adversarial review of the video branch (2026-10-11)

def test_the_clip_list_carries_no_measurements_before_the_gate(client, demo):
    asset_id = sample(client)["asset"]["id"]                                   # Sleep trial: no video history
    worker.run_once("test")
    listed = client.get("/v1/video/assets", headers=PARENT).json()["assets"][0]
    assert listed["id"] == asset_id and listed["run"]["state"] == "COMPLETED"
    assert set(listed["run"]) == {"id", "state", "error_code"}                  # no windows, samples or summary


def test_audio_is_never_stored_or_served(client, demo, clips):
    milestones(client)
    with_audio = staged_clip.write(clips["dir"] / "with-audio.webm", (("still", 2.0), ("move", 2.0)), size=SMALL,
                                   audio=True)
    assert vp.inspect(with_audio)["has_audio"]
    r = client.post("/v1/dev/video/upload", headers=PARENT, content=with_audio.read_bytes())
    assert r.status_code == 200, r.text
    asset = r.json()["asset"]
    assert asset["provenance"]["audio_in_source"] is True and asset["provenance"]["audio_stored"] is False
    stored = video_evidence.media_root() / f"{asset['sha256']}.webm"
    assert vp.inspect(stored)["has_audio"] is False
    again = client.post("/v1/dev/video/upload", headers=PARENT, content=with_audio.read_bytes()).json()
    assert again["asset"]["id"] == asset["id"] and again["created"] is False      # the same source file, one clip


def test_frames_larger_than_the_header_are_refused(clips):
    import av
    from fractions import Fraction
    import numpy as np
    path = clips["dir"] / "grows.webm"

    def packets(w, h, n, start):
        cc = av.CodecContext.create("libvpx", "w")
        cc.width, cc.height, cc.pix_fmt, cc.time_base = w, h, "yuv420p", Fraction(1, 1000)
        out = []
        for i in range(n):
            f = av.VideoFrame.from_ndarray(np.full((h, w, 3), 128, np.uint8), format="rgb24").reformat(format="yuv420p")
            f.pts, f.time_base = start + i * 33, Fraction(1, 1000)
            out += list(cc.encode(f))
        return out + list(cc.encode(None))
    container = av.open(str(path), "w", format="webm")
    stream = container.add_stream("libvpx", rate=30)
    stream.width, stream.height, stream.pix_fmt, stream.time_base = 64, 64, "yuv420p", Fraction(1, 1000)
    for packet in packets(64, 64, 5, 0) + packets(400, 400, 2, 200):
        packet.stream = stream
        container.mux(packet)
    container.close()
    limits = vp.Limits(max_pixels=64 * 64 * 4)
    assert vp.inspect(path, limits)["width"] == 64                             # the header looks harmless
    with pytest.raises(vp.MediaError) as err:
        vp.analyze(path, limits=limits)
    assert err.value.code in ("TOO_MANY_PIXELS", "CORRUPT_STREAM")


def test_a_stale_attempt_never_overwrites_a_newer_one(client, demo):
    asset_id = analyzed(client)                                                # attempt 1 completed
    with SessionLocal() as db:
        run = db.execute(select(VideoAnalysisRun)).scalar_one()
    assert video_evidence._advance(run.id, 1, "ANALYZING") is False            # a late worker can't reopen it
    v = view(client, asset_id).json()
    assert v["run"]["state"] == "COMPLETED" and len(v["observations"]) == count(VideoObservation)


def test_a_clip_that_keeps_killing_the_worker_is_failed_not_retried_forever(client, demo):
    milestones(client)
    s = sample(client)
    with SessionLocal() as db:
        db.execute(text("UPDATE video_analysis_runs SET state = 'ANALYZING', attempts = :n"),
                   {"n": video_evidence.MAX_RUN_ATTEMPTS})
        db.commit()
    assert video_evidence.process(s["run"]["id"]) == "FAILED"
    v = view(client, s["asset"]["id"]).json()
    assert v["run"]["error_code"] == "WORKER_GAVE_UP"
    assert client.post(f"/v1/video/assets/{s['asset']['id']}/analysis/retry", headers=PARENT).status_code == 202
    assert worker.run_once("test") == 1
    assert view(client, s["asset"]["id"]).json()["run"]["state"] == "COMPLETED"   # a retry starts its count again


def test_h264_stillness_is_not_called_a_stalled_feed_when_things_move(clips):
    clip = staged_clip.write(clips["dir"] / "h264.mp4", (("still", 2.0), ("small", 2.0), ("move", 2.0)),
                             size=SMALL, codec="libx264", container="mp4", crf=23)
    r = vp.analyze(clip)
    assert kinds_at(r, 4.5) == "movement"
    assert kinds_at(r, 3.0) in ("small_change", "movement")                    # was "frames repeated"
    still = next(i for i in r["intervals"] if i["t_start"] <= 1.0 < i["t_end"])
    assert still["kind"] in ("insufficient_evidence", "no_significant_movement")  # never "movement"
    assert "look the same" in video_evidence.KIND_TEXT["insufficient_evidence"]


def test_a_lighting_change_is_not_movement(clips):
    clip = staged_clip.write(clips["dir"] / "dim.webm", (("still", 2.0), ("dim", 2.0), ("still", 2.0)), size=SMALL)
    r = vp.analyze(clip)
    assert all(i["kind"] != "movement" for i in r["intervals"])


def test_a_duration_a_hair_past_a_window_makes_no_empty_interval():
    frames = [{"t": i / 30, "brightness": 120.0, "sharpness": 300.0, "local_change": 0.0 if i >= 6 else None,
               "ref_t": (i - 6) / 30 if i >= 6 else None,
               "shift": 0.0 if i >= 6 else None, "peak": 1.0, "identical": False} for i in range(90)]
    r = vp._summarize(frames, {"fps": 30.0, "duration_s": 3.00003}, vp.Config(), 0, 1)
    assert all(i["t_end"] > i["t_start"] for i in r["intervals"])
    assert r["intervals"][-1]["t_end"] == 3.0


def test_a_clip_without_a_duration_header_can_still_be_annotated(client, demo, clips):
    milestones(client)
    live = staged_clip.write(clips["dir"] / "live.webm", (("still", 2.0), ("move", 2.0)), size=SMALL, live=True)
    assert vp.inspect(live)["duration_s"] is None
    asset_id = client.post("/v1/dev/video/upload", headers=PARENT, content=live.read_bytes()).json()["asset"]["id"]
    client.post(f"/v1/video/assets/{asset_id}/analysis", headers=PARENT)
    worker.run_once("test")
    v = view(client, asset_id).json()
    assert v["asset"]["duration_s"] and v["asset"]["duration_s"] >= 3.9
    assert annotate(client, asset_id, "not_sure", 0, 1, 1, "live-clip-note").status_code == 201


def test_only_the_author_corrects_a_report_and_acknowledging_needs_the_plan(client, demo):
    asset_id = analyzed(client)
    mine = annotate(client, asset_id, "infant_moving", 0, 1, 1, "owner-report-1").json()
    theirs = annotate(client, asset_id, "not_sure", 0, 1, 2, "caregiver-fix-1", headers=CAREGIVER,
                      supersedes=mine["annotation_id"])
    assert theirs.status_code == 403 and theirs.json()["code"] == "NOT_YOUR_REPORT"
    revoked = client.post("/v1/dev/claims", headers=PARENT, json={     # the store revokes the plan
        "device_id": seed.CAMERA, "channel": "web", "external_ref": f"web-ms-{seed.CAMERA}",
        "event_status": "revoked", "plan_code": "milestones"})
    assert revoked.status_code == 200, revoked.text
    lapsed = client.post(f"/v1/video/assets/{asset_id}/acknowledge", headers=CAREGIVER, json={"revision": 2})
    assert lapsed.status_code == 403 and lapsed.json()["code"] == "FEATURE_NOT_IN_PLAN"


def test_a_valid_media_link_stops_working_when_the_member_or_the_plan_goes(client, demo):
    # The link is checked again on every request, not only when it is minted (cold review: untested).
    asset_id = analyzed(client)
    owner, caregiver = token_for(client, asset_id), token_for(client, asset_id, headers=CAREGIVER)
    assert media(client, asset_id, owner).status_code == media(client, asset_id, caregiver).status_code == 200
    with SessionLocal() as db:
        db.execute(text("UPDATE members SET revoked_at = now() WHERE id = :m"), {"m": seed.CAREGIVER_B})
        db.commit()
    assert media(client, asset_id, caregiver).status_code == 404                   # removed from the family
    client.post("/v1/dev/claims", headers=PARENT, json={
        "device_id": seed.CAMERA, "channel": "web", "external_ref": f"web-ms-{seed.CAMERA}",
        "event_status": "revoked", "plan_code": "milestones"}).raise_for_status()
    lapsed = media(client, asset_id, owner)
    assert lapsed.status_code == 403 and lapsed.json()["code"] == "FEATURE_NOT_IN_PLAN"


def test_an_upload_from_someone_without_rights_writes_nothing(client, demo):
    incoming = video_evidence.media_root() / "incoming"
    before = set(incoming.iterdir()) if incoming.exists() else set()
    r = client.post("/v1/dev/video/upload", headers=CAREGIVER, content=b"\x1a\x45\xdf\xa3" + b"\x00" * 4096)
    assert r.status_code == 403
    assert (set(incoming.iterdir()) if incoming.exists() else set()) == before


# ---------------------------------------------------------------- final review: the pipeline as a backend engineer would check it

def test_the_stored_bytes_the_served_bytes_and_the_recorded_hash_agree(client, demo):
    asset_id = analyzed(client)
    v = view(client, asset_id).json()
    stored = video_evidence.media_root() / f"{v['asset']['sha256']}.webm"
    assert vp.sha256_file(stored) == v["asset"]["sha256"]                      # the file is what the row says
    assert v["run"]["input_sha256"] == v["asset"]["sha256"]                   # the run measured that file
    served = media(client, asset_id, token_for(client, asset_id))
    assert served.status_code == 200 and served.content == stored.read_bytes()


def test_intervals_cover_the_clip_from_start_to_end_without_gaps_or_overlaps(client, demo):
    asset_id = analyzed(client)
    v = view(client, asset_id).json()
    obs = sorted(v["observations"], key=lambda o: o["seq"])
    assert obs[0]["t_start"] == 0
    assert all(a["t_end"] == b["t_start"] for a, b in zip(obs, obs[1:]))       # contiguous, in order
    assert abs(obs[-1]["t_end"] - v["asset"]["duration_s"]) < 0.05             # to the end of the clip
    assert sum(o["frames"] for o in obs) == v["run"]["summary"]["frames"]["analyzed"]   # every frame counted once


def test_the_output_never_claims_a_sleep_state_or_a_confidence(client, demo):
    import re
    asset_id = analyzed(client)
    v = view(client, asset_id).json()
    keys = set()

    def walk(x):
        if isinstance(x, dict):
            for k, val in x.items():
                keys.add(k)
                walk(val)
        elif isinstance(x, list):
            for i in x:
                walk(i)
    walk(v["observations"])
    walk(v["run"])
    assert not {k for k in keys if re.search(r"sleep|wake|breath|confidence|probability|score", k)}, keys
    claims = " ".join(o["says"] for o in v["observations"]).lower()
    assert not re.search(r"\b(asleep|awake|rem|deep sleep|light sleep|breathing)\b", claims), claims
    assert {o["source"] for o in v["observations"]} == {"computer_measured"}
    assert "sleep or wake state" in v["not_claimed"]


@pytest.mark.parametrize("path", ["/v1/video/media/..%2F..%2Fapp%2Fmain.py", "/v1/video/media/..%5C..%5Capp%5Cmain.py",
                                  "/v1/video/media/%2e%2e", "/v1/video/media/vas_x%00.webm"])
def test_the_media_route_never_serves_a_path(client, demo, path):
    asset_id = analyzed(client)
    token = token_for(client, asset_id)
    client.cookies.clear()
    r = client.get(path, cookies={video_evidence.MEDIA_COOKIE: token})
    assert r.status_code == 404 and b"FastAPI" not in r.content and b"import" not in r.content


def test_each_report_is_attributed_to_the_person_who_made_it_and_older_acks_stay_old(client, demo):
    asset_id = analyzed(client)
    moving = next(o for o in view(client, asset_id).json()["observations"] if o["kind"] == "movement")
    assert client.post(f"/v1/video/assets/{asset_id}/acknowledge", headers=PARENT, json={"revision": 1}).status_code == 200
    r = annotate(client, asset_id, "infant_still", moving["t_start"], moving["t_end"], 1, "caregiver-says-still",
                 headers=CAREGIVER)
    assert r.status_code == 201, r.text
    for viewer in (PARENT, CAREGIVER):                                        # both see the same attribution
        v = view(client, asset_id, headers=viewer).json()
        note = v["annotations"][0]
        assert (note["author"], note["author_id"], note["source"]) == ("Caregiver B", seed.CAREGIVER_B, "parent_reported")
        assert moving["id"] in note["contradicts"]                             # disagreement is shown, not resolved
        assert next(o for o in v["observations"] if o["id"] == moving["id"])["kind"] == "movement"
        mine = next(a for a in v["acknowledgments"] if a["member_id"] == seed.PARENT_A)
        assert mine["revision"] == 1 and mine["changed_since"] is True         # Parent A saw revision 1, not 2
