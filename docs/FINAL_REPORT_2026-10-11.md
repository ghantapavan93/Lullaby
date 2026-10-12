# Final report: purchase → access → real evidence (2026-10-11)

Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.

## Architecture diff

**Reused, not rewritten:**
- the PostgreSQL schema and migrations 0001–0009;
- the household lock and tenancy (`load_owned`), roles, and idempotency keys;
- the append-only triggers;
- the outbox and worker (leases, retries, crash recovery);
- the entitlement derivation and the access decision;
- Scenario Studio, the Growth Lab funnel, Engineering X-Ray, and certification.

**Added:**

| Layer | Addition |
|---|---|
| Migration | `0010_video_evidence`: `video_assets`, `video_analysis_runs`, `video_observations`, `video_annotations`, `video_acknowledgments` (the last three append-only) |
| Processing | `app/video_processing.py`: real decoding (PyAV) and frame measurement (numpy). `app/staged_clip.py`: generated clips for the public copy and the tests |
| Domain | `app/video_evidence.py`: registration, durable runs, the plan gate, annotations and revisions, acknowledgments, signed media links, deletion |
| Worker | Job kind `analyze_video`, using the existing outbox |
| API | `/v1/video/*` (switch: `PROOF_LAYER_VIDEO`), plus development controls `/v1/dev/video/*` |
| UI | A "Recorded evidence" page (player, timeline, interval list, interval explanation, the parent's report with before and after, who has seen what), an X-Ray video section, a Growth Lab "Recorded evidence" funnel, a studio scenario, and a link from the access journey |

**Rejected:**

| Option | Why |
|---|---|
| A second job queue | The outbox already gives dedupe, leases and retries |
| Putting the clip into the eight-hour night | That would present 34 seconds as a night |
| A machine-learning model | None installed; unvalidated on newborns; one clip can't evaluate one |
| OpenCV | Not needed: PyAV and numpy were enough |
| Public uploads | No privacy design for real family video |

## Data provenance

The clip is Wikimedia Commons `Infantactivesleep.webm` (CC0, uploader Bagelpigeon):
- SHA-256 `1f7b7e2d…f147`, 17,240,568 bytes;
- VP9, 1080×1920 at 30 fps, 1,016 frames, 33.901 s;
- downloaded 2026-10-11 with approval.

**Local controlled demo only:** CC0 isn't consent. The public copy and the tests use a generated staged clip with no person in it. Full details are in `docs/VIDEO_EVIDENCE.md` §1.

## Real measurement evidence

The real clip was measured twice, with identical results:
- movement for 0–6 s and 7–33.9 s, at a mean of 22–26% of pixels changed;
- camera motion for 6–7 s.

**Limitation, from a manual check:** the clip is a handheld phone recording of a baby in a car seat. Pixel change mixes the baby's movement with camera motion, and this baseline can't separate them.

On the shipped staged clip, all 17 one-second windows match their script: still, movement, small change, dark, pan and frozen. A timestamp gap is tested on a separate clip. The cold review found that an earlier version misread the frozen segment; it was fixed (processor `motion-baseline-2`), and the test now runs on the shipped clip.

## ML decision

Not attempted (reasons in `docs/VIDEO_EVIDENCE.md` §4). The deterministic baseline makes no claim about sleep, breathing, health or identity.

## Full journey (as tested in the browser)

1. Milestones ended. The camera says "This plan has ended", and the clip is refused with "7-day video history isn't on this camera's plan". No offer.
2. The store's renewal arrives. The same camera is renewed through Oct 2027, and 7-day video history is verified.
3. The parent opens the recorded evidence; the analysis is queued.
4. The worker, a separate OS process, analyses the clip. The page polls until it reads "Analysed".
5. The video plays through a signed link. The parent selects "Movement measured, 0:02–0:06" and reports "The baby was still". This creates revision 2: the report disagrees with the measurement, both are kept, and before and after are shown.
6. The Growth Lab shows each event separately:
   - access verified for video history;
   - evidence opened;
   - analysis completed, with its time;
   - intervals without enough evidence;
   - reports and corrections.

## Tests

The counts, with their dates and files, are in [RELEASE_PROOF_2026-10-11.md](RELEASE_PROOF_2026-10-11.md). What the video work added:

- **Processing.** The shipped demo clip is checked window by window against its script. Missing frames, dark video and a stalled feed are never stillness; a lighting change is not movement; H.264 stillness is not a stall; oversized frames, truncated files and non-video files fail with a code.
- **Access.** The plan gate covers every read and every media request. A link minted before a member is removed, or before the plan lapses, stops working. Another family can't see, annotate or stream a clip.
- **Durability.** One run per clip, processor and config, even under concurrent requests. A worker killed mid-analysis is recovered; a clip that keeps killing the worker is failed after three attempts.
- **Reports.** A contradicting report keeps the measurement. A correction is a new revision. A caregiver's older acknowledgment keeps its scope.
- **Switch.** The access demo works with the video module off.
- **Browser.** The renewal, then the recorded evidence journey, end to end.
- **Mutation.** Mutants M59–M67 remove one video guarantee each.

## Product differentiation

| | |
|---|---|
| **Nanit already offers** | Sleep analytics, video history, Memories, Sleep Score, NextNap, caregivers, subscription management |
| **This prototype adds** | One inspectable chain: why a purchase and access disagree, and how that is reconciled; which plan rule gates a feature; what a clip's pixels actually show; what is missing; what a person said, kept apart from what was measured; and the stored proof for each step |
| **Only a hypothesis** | That proactive explanations, or evidence provenance, improve resolution or trust. It needs real data (`docs/ACCESS_EXPERIMENT_DESIGN.md`) |

## Release verdict

**Ready for a controlled review, not for a public link.** The weakest category is visitor isolation: one shared synthetic household, which any visitor can reset. The one next step: give each reviewer session its own synthetic household. Details are in [RELEASE_PROOF_2026-10-11.md](RELEASE_PROOF_2026-10-11.md).

## Three-minute video outline

| Time | Beat | What is shown |
|---|---|---|
| 0:00–0:40 | The problem | "I paid for this. Why can't I use it?" The access journey: Milestones ended; five stages; no paywall |
| 0:40–1:20 | Recovery | The store's renewal reaches the same camera. Video history is verified, and the Growth Lab shows it as a separate event |
| 1:20–2:10 | Real evidence | The clip is analysed by the worker process. The timeline shows movement, camera motion and missing evidence. Select an interval: the measured numbers, and what isn't known |
| 2:10–2:40 | A failure case | Remove the stored bytes, and the run fails with `MEDIA_UNAVAILABLE`. Retry, and it is analysed. Or: a stalled feed reads as "not enough video", never as "still" (on the shipped clip, 13–15 s) |
| 2:40–3:00 | Proof | X-Ray rows (hash, run, attempts, trace), the append-only report, the tests and the mutants. The failed four-process soak is still shown as failed |
