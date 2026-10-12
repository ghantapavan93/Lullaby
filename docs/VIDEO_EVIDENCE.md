# Video evidence: one real clip, measured honestly

This is an independent prototype. Nanit already offers video history, Memories, sleep analytics and more; nothing here claims otherwise.

What this adds is a chain you can inspect end to end:

1. Purchase.
2. Reconciliation.
3. Verified feature access.
4. Actual video decoding and measurement.
5. Stored evidence.
6. A parent's report.
7. Reproducible proof.

## 1. The clip and the decision to use it

| Field | Value |
|---|---|
| Source | https://commons.wikimedia.org/wiki/File:Infantactivesleep.webm |
| File | https://upload.wikimedia.org/wikipedia/commons/4/43/Infantactivesleep.webm |
| License | CC0 1.0 Universal (uploader: Bagelpigeon, "own work", 2024-07-30) |
| Downloaded | 2026-10-11T10:17:55Z, with the owner's approval |
| SHA-256 | `1f7b7e2d189b21c78cf8b46ff44c463bcd1aa87c01b3f0d10e5a20568877f147` |
| Size | 17,240,568 bytes |
| Streams | VP9 1080×1920 at 30 fps (1,016 frames, 33.901 s), plus Opus audio |
| Decoder | PyAV 19.0.1 (bundled FFmpeg), numpy 2.5.4 |
| Audio | Dropped when the clip is stored: the stored copy is a video-only remux (no re-encode), so audio is never served. The source hash above is the file as downloaded; the stored copy has its own hash |
| Alterations | None to the file. Frames are downscaled in memory for measurement |
| **Decision** | **Local controlled demo only.** CC0 covers copyright, not the infant's privacy or publicity, and the page carries no consent or personality-rights template |
| Where it lives | git-ignored `local-media/`, outside every served directory. Never committed, never in the public copy |
| Default demo, public copy and tests | A generated staged clip (`app/staged_clip.py`): shapes on a texture, no person, labelled "Staged test clip" |
| Using the real clip | Opt-in: `PROOF_LAYER_SAMPLE=cc0`. Its caption then says "Real infant recording (Wikimedia Commons, CC0), shown on this machine only. Not synthetic." The page's banner says the child records are synthetic, so the real baby is never the default (cold review, 2026-10-11) |

**Manual check (2026-10-11), three frames, viewed and then deleted:**
- The baby is in a **car seat**, not a crib.
- The clip was filmed on a **handheld phone**: the framing shifts between seconds 1 and 15.
- The arms move.

It is not fixed nursery-camera footage, and the analysis must not be read as if it were.

## 2. What the processor does

`app/video_processing.py`, processor `motion-baseline-2`. It is deterministic, and its config is recorded with every run.

Version 2 changed one rule: the stalled-feed check now counts only change measured inside the window. In version 1, a feed that stalled right after a pan was read as "small change". The cause was the 0.2 s comparison reaching back into the pan (cold review, 2026-10-11).

1. **Validate** before decoding:
   - Size: up to 64 MB.
   - Magic bytes: EBML or `ftyp`. The extension is never trusted.
   - Container: Matroska/WebM or MP4.
   - Codec: VP8, VP9 or H.264.
   - Frame size: up to 3840×2160.
   - Duration: up to 120 s.
2. **Decode every video frame** with its presentation timestamp. Limits: 5,000 frames and 60 s of decoding.
3. **Measure each frame** on a 160-pixel-wide grey copy:
   - brightness;
   - sharpness (Laplacian variance);
   - whether it repeats the previous frame;
   - compared with the frame 0.2 s earlier: the whole-image shift (phase correlation) and the share of pixels that changed after removing that shift.
4. **Classify 1-second windows**, first match wins:
   - `insufficient_evidence` (frames missing);
   - `low_quality` (too dark);
   - `insufficient_evidence` (frames repeated: a stalled feed);
   - `low_quality` (blurred or flat);
   - `camera_motion` (median whole-image shift);
   - `movement`;
   - `small_change`;
   - `no_significant_movement`.
5. **Merge** consecutive windows of the same kind into intervals.

It never outputs sleep or wake state, breathing, health, safety, or who or what moved.

"No significant movement" is a pixel measurement, not stillness of a person, and never sleep.

## 3. What it measured

### The real clip

Saved in `artifacts/video-evidence/cc0-sample-analysis.json`. It contains numbers only; no imagery.

| Interval | Kind | Mean pixels changed |
|---|---|---|
| 0–6 s | movement | 26.2% |
| 6–7 s | camera_motion | 17.8% |
| 7–33.9 s | movement | 22.4% |

- **Frames:** 1,016 decoded, 0 missing timestamps, 0 gaps. The last window's coverage is 96%.
- **Repeatable:** a second run gave identical windows and intervals. It took 12.1 s on a loaded laptop.
- **Version 2:** re-measured with `motion-baseline-2`. The intervals, and every window's kind and pixel change, were identical (15.7 s).

**Interpretation, and its limit:** pixels changed throughout. That says nothing about sleep, and the title "active sleep" is the uploader's, not a measurement. The camera is handheld. Whole-pixel shift compensation at 160 px removes only part of the camera motion, so this baseline **can't say how much of the change is the baby**. A fixed camera would be needed for that.

### The staged clip

Its segments are scripted in whole seconds, and every one-second window is checked against the script. The check is `test_the_shipped_demo_clip_is_measured_as_scripted_in_every_window`, on the clip the demo actually uses. All 17 windows match:

| Segment | Result |
|---|---|
| still | `no_significant_movement` |
| moving shape | `movement` |
| small drift | `small_change` |
| dark | `low_quality` (too dark) |
| pan | `camera_motion` |
| repeated frame | `insufficient_evidence` (frames repeated) |

A timestamp gap reads as `insufficient_evidence` (frames missing). It is tested on a separate clip (`test_missing_frames_are_missing_evidence_not_stillness`), because the demo clip has no gap.

**Boundaries.** Windows are fixed one-second slots. A real change of condition inside a slot is reported as whatever dominates that slot's measurements. A slot that is half dark and half pan reads as `movement`. The tested guarantee is weaker than "matches the script": a straddling slot is never read as still (`test_a_window_that_straddles_two_conditions_is_never_read_as_still`).

**Corrected (cold review, 2026-10-11).** An earlier version of this section said every segment was recovered. On the shipped clip that was false:
- The stalled feed read as "small change".
- Segment edges teleported the shape, so "still" windows measured movement.
- The small sway was too faint to register.

The processor fix and the generator fix are described above. The test that would have caught it now runs on the shipped clip.

## 4. Machine learning: not used

- **No model was installed.** Installing a large vision model wasn't justified before the backend chain worked.
- **Published work** reports that adult-trained pose models do poorly on newborns. One clip can't validate a model, and one video gives no accuracy figure.
- **A model could help** with locating the infant (a region of interest), so that local change is measured on the baby, not the frame. It would need labelled clips from fixed cameras and a written evaluation before any claim.

## 5. Where it lives in the system

**Reused:**
- the household lock;
- `load_owned` tenancy;
- the role policy;
- the outbox and worker (new job kind `analyze_video`; its dedupe key gives one job per run, and leases recover a dead worker);
- append-only triggers;
- server analytics;
- the entitlement rules: the gate is the camera's plan including "7-day video history" (an assumed public rule);
- Scenario Studio;
- the Growth Lab funnel;
- the X-Ray.

**Added** (migration 0010, five narrow tables):

| Table | Holds | Rule |
|---|---|---|
| `video_assets` | One clip per household and content hash; provenance, retention (30 days), deletion | Unique per (household, sha256) |
| `video_analysis_runs` | REGISTERED → VALIDATING → ANALYZING → COMPLETED, or FAILED with a code | Unique per (clip, processor version, config) |
| `video_observations` | Measured intervals | Append-only; written once, in the run's final transaction |
| `video_annotations` | What a person reported; a correction names the row it replaces | Append-only; a new evidence revision each time |
| `video_acknowledgments` | A member saw revision N | Append-only |

Measurements and reports are different tables, never flags on one row.

**Routes:**
- `GET /v1/video/assets` and `GET /v1/video/assets/{id}` (read-only);
- `POST …/media-link` (sets an HttpOnly, SameSite=Strict cookie scoped to this clip's media path), and `GET /v1/video/media/{id}` (reads the cookie; never a token in the URL);
- `POST …/analysis` and `…/analysis/retry`;
- `POST …/annotations` (with `Idempotency-Key` and `expected_revision`);
- `POST …/acknowledge`;
- `DELETE …`.

Development only: `/v1/dev/video/sample`, `/upload` and `/worker`.

**Switch:** `PROOF_LAYER_VIDEO=0` turns the module off, and the access demo is unaffected (tested).

## 6. Access, privacy and security

- **Every read checks the camera's plan.** Commands record the check; reads write nothing (tested).
- **The clip list** returns only each run's state. Measurements come only through the gated view.
- **Media bytes** are served only with a 5-minute signed token. It travels in an HttpOnly, SameSite=Strict cookie scoped to that clip's media path, never in a URL or log line. It is re-checked on every request, range requests included:
  - the member must be active and in the household;
  - the clip must not be deleted, and the plan must still include video history (both the member and the plan check are tested with a link minted before the change: `test_a_valid_media_link_stops_working_when_the_member_or_the_plan_goes`);
  - responses carry `Cache-Control: private, no-store`;
  - another clip or family gets 404, an expired token 401, and a malformed one 404 (never a 500).
- **Reports:** only their author can correct them. Acknowledging a revision needs the same plan gate as viewing it.
- **Uploads** (development only):
  - authenticated and authorised before any byte is written;
  - streamed with a byte limit, then inspected by content;
  - stored by hash in the private media root;
  - the client's file name is never kept;
  - temporary files are removed on every path.
- **Deletion** removes the bytes (unless another family's clip shares the same content), and the numeric measurements stay. Retention is recorded, but expiry isn't enforced by a job yet.
- **Not a child-data platform.** There is no public upload, no production storage, and no health or safety claim.

## 7. Tests

`tests/test_video_evidence.py` (39 tests) covers:
- the scripted clip;
- determinism;
- missing frames;
- jittered timestamps;
- background movement;
- small drift;
- empty, text, PNG, MPEG-4 (unsupported) and garbage files;
- oversize;
- truncation;
- idempotent and concurrent analysis requests;
- the plan gate;
- cross-household refusal;
- signed links (missing, forged, foreign, expired, range);
- a contradicting report that leaves the measurement unchanged;
- corrections;
- acknowledgment scope;
- a worker killed mid-analysis and recovered;
- failure and retry;
- uploads;
- reads that write nothing;
- deletion;
- the module switched off;
- the CC0 sample's decode, when present.

Other suites:
- **Browser:** `test_recorded_evidence_after_the_renewal_in_the_browser`.
- **Red team:** the route sweeps now include every `/v1/video` route.

## 8. Independent review (2026-10-11)

A read-only reviewer reproduced seven of these defects on generated clips; the rest were traced in the code. All twelve are fixed, each with a test.

| # | Defect | Fix |
|---|---|---|
| V1 | The clip list returned per-second measurements without the plan gate | The list returns run state only |
| V2 | The audio track was stored and served, while the copy said it wasn't | Stored as a video-only remux; deduplicated by the source file's hash |
| V3 | A 437 KB file declared 64×64 frames but decoded 16000×16000 keyframes (1.26 GB peak) | Decoder `max_pixels` cap, plus a size check on every frame |
| V4 | A worker whose lease was taken over could reopen a completed run and leave it broken | Each attempt numbers itself; every later write checks that number under a row lock. The worker extends its lease while decoding |
| V5 | Still H.264 scenes (no sensor noise after compression) read as a stalled feed, hiding a small drift | "Frames repeated" now needs no change over the 0.2 s lag either. The wording says a stalled feed and a perfectly still picture look the same |
| V6 | A clip that kills the worker was retried forever | Failed as `WORKER_GAVE_UP` after 3 attempts; a manual retry resets the count |
| V7 | A duration a few microseconds past a window made a zero-length interval that couldn't be stored | The sliver joins the window before it |
| V8 | A non-ASCII token caused a 500; tokens in the query string reached access logs | Tolerant parse; the token moved to a path-scoped HttpOnly cookie |
| V9 | Acknowledging skipped the plan gate | The gate is required |
| V10 | Any member could correct anyone's report | Author only (`NOT_YOUR_REPORT`) |
| V11 | Clips without a duration header couldn't be annotated | The remux writes a duration, and the measured duration is stored after analysis |
| V12 | A lighting change (60% dimming) read as movement | Each frame is matched to its reference's brightness and contrast before differencing |

These were also fixed:
- an upload wrote its bytes before authentication;
- a delete on an open file returned 500 on Windows;
- a delete was racing a registration of the same bytes;
- a retry after give-up kept the old attempt count.

**Not fixed:** an acknowledgment records that a member asked to mark a revision as seen. It isn't proof that the member watched it, which matches how acknowledgments work elsewhere in the app.
