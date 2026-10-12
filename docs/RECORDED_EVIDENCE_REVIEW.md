# Recorded evidence: a final review (2026-10-11)

**Independent engineering prototype. Synthetic child records. Simulated payments. Not affiliated with Nanit.**

## 1. Two screenshots that disagreed

| | Desktop screenshot | Mobile screenshot |
|---|---|---|
| Clip | 15 s, 450 frames | 17 s, 510 frames |
| Processor | `motion-baseline-1` | `motion-baseline-2` |
| Selected interval | 0:02–0:06 | 0:02–0:05 |
| Captured at | `41b9f1d` (06:33 CDT) | after `4bd15b1` (07:03 CDT) |

**Verdict: two valid runs on different code, not drift within one version.** Between the two captures, the first cold review changed two things:
- **The generated clip** (`staged-v1` → `staged-v2`). Its segments moved to whole seconds, and its first "move" became 3 s, which is why the interval is now 0:02–0:05.
- **The processor** (`motion-baseline-1` → `2`): the stall check now ignores change carried over from the previous window.

The desktop screenshot also shows the pre-selected report answer that was later removed. The originals are kept in `artifacts/visual-qa/history/`.

**Controlled comparison** (`proof-layer/scripts/media_consistency.py`):
- **Held fixed:** one run, captured at 1440×900 and 390×844. Same clip hash, processor, config digest, scenario (`evidence_journey`), evidence revision, interval (sequence 1, 0:02–0:05) and commit.
- **Result:** all 14 checks pass. Both widths show the same heading, kind, facts, summary, caption, hash, run and revision, and the page matches the API. The latest report is in `artifacts/visual-qa/consistency/report.json`.

## 2. Is the screen useful to a parent?

| Question | Before | Answer |
|---|---|---|
| Why open it? | Unclear: it opened on a player and algorithm numbers | To see what a stretch of video shows, and to say what you saw |
| What question does it answer? | "What was measured?", in engineering terms | "What does this clip show, second by second, and what can't it show?" |
| Which observations are understandable? | Kinds, yes; "pixels changed (mean, peak)", "coverage 100.0%" and "frames decoded" were metadata | Plain words; the method stays named |
| What does the parent do? | Pick an interval; report | The same, with a question ("What did you see between 0:02 and 0:05?") |
| Movement versus sleep? | The page said "not sleep" in two places | Kept, and the clip summary ends with "Not a sleep record, and not who or what moved" |
| Does staged footage make "The baby was still" misleading? | Yes: a report about a baby, on a clip of shapes | The form says the clip has no baby and a report only tests the flow; a report reads "Parent A reported: …" |

**Three layouts considered:**
1. **Answer first** *(chosen)*: a short "What this clip shows" summary of seconds per kind under the player. The algorithm's bookkeeping (frames decoded, time to measure) moves to the engineers' table.
2. **Timeline first**: hide the player until an interval is chosen. Rejected, because a parent can't check the measurement against the picture.
3. **Question first**: "Was there movement at 2:14?" as a lookup. Rejected, because it needs a time the parent rarely has, and it hides the missing-evidence intervals.

Option 1 was chosen for clarity. It answers the parent's first question with the stored numbers, and it changes the least.

## 3. Playback, tested in a browser

| Check | Before | Now | Evidence |
|---|---|---|---|
| Starts at the interval | Yes | Yes (seek to 2.000 s) | Media events: `seeked` 2.0, `playing` 2.001 |
| Stops at the boundary | 0.19 s late (`timeupdate`) | 5.001 s for a 5.0 s end (checked every frame) | `test_play_this_part_plays_exactly_the_selected_interval[desktop, mobile]` |
| Media endpoint gone | Silent (error code 4, nothing shown) | "Couldn't play 0:02–0:05 … Nothing was changed" | `test_playback_failure_is_said_and_a_lapsed_link_is_renewed` |
| Link older than 5 minutes | Not renewed; would fail silently | Renewed before playing | Same test (a new `POST …/media-link` is observed) |
| Leave the page and come back | Selection lost | Kept | The desktop and mobile tests |
| Keyboard only | Not checked | Tab reaches the interval (2 px focus ring), Enter selects, Enter on Play plays and stops on the boundary | Run by hand at 1440 and 390 px |

All three browser tests failed against the old `app.js` and pass now.

## 4. The pipeline, as a backend engineer would check it

| Check | Result | Test |
|---|---|---|
| Stored bytes, served bytes and recorded hash agree | Pass | `test_the_stored_bytes_the_served_bytes_and_the_recorded_hash_agree` |
| Intervals cover 0 to the end, contiguous, every frame counted once | Pass | `test_intervals_cover_the_clip_from_start_to_end_without_gaps_or_overlaps` |
| Missing frames, partly missing windows | Pass | `test_missing_frames_are_missing_evidence_not_stillness`, `test_a_window_missing_half_its_frames_is_missing_evidence` |
| Variable frame timing | Pass | `test_jittered_timestamps_still_analyze` |
| Low light | Pass (dark is `low_quality`; dim isn't movement) | `test_the_shipped_demo_clip_…`, `test_a_lighting_change_is_not_movement` |
| Frozen feed versus still scene | Pass | `test_h264_stillness_is_not_called_a_stalled_feed_when_things_move`, the shipped-clip test |
| Camera versus object movement | Pass | Shipped-clip test (`camera_motion`) |
| Duplicate processing | Pass | `test_repeated_and_concurrent_analysis_requests_make_one_run` |
| Interruption, worker crash, safe retry | Pass | `test_a_worker_killed_mid_analysis_is_recovered_by_the_next`, `test_a_failed_run_says_why_and_a_retry_succeeds`, `test_a_clip_that_keeps_killing_the_worker_is_failed_not_retried_forever` |
| Same file and config, same result | Pass | `test_the_same_file_and_config_give_the_same_result` |
| No sleep state, confidence or score in the output | Pass | `test_the_output_never_claims_a_sleep_state_or_a_confidence` |
| The media route never serves a path | Pass | `test_the_media_route_never_serves_a_path` (4 traversal forms) |

The new tests found no new defect.

## 5. People's reports

A report is evidence of what someone said, not proof that it happened.

| Rule | Test |
|---|---|
| Report and measurement both stay visible; the measurement never changes | `test_a_contradicting_annotation_keeps_the_measurement` |
| Before and after are described | Same test (`before`, `after`, `measurement_changed: false`) |
| The right person is named | `test_each_report_is_attributed_to_the_person_who_made_it_and_older_acks_stay_old` |
| An old acknowledgment doesn't carry to a new revision | Same test, and `test_a_caregivers_older_acknowledgment_keeps_its_scope` |
| Another family can't report or acknowledge | `test_another_family_cant_see_annotate_or_stream_a_clip` |
| A stale screen is refused | `STALE_EVIDENCE_VIEW` in the first test |

"Nothing reported yet" became "No reports from people yet. The bars above are measurements."

## 6. Media access is checked on the bytes

The page checks access, and so does every byte request, separately:
- Each `GET /v1/video/media/{id}` re-checks the cookie's signature, its expiry, the clip it names, the member, the family, deletion and the plan, Range requests included. Tests cover a forged token, an expired one, another clip, another family, a removed member, a lapsed plan, a path traversal, and `Cache-Control: private, no-store`.
- **Not claimed:** bytes a browser has already buffered or saved can't be taken back. A revoked member keeps what was already downloaded; only new requests are refused.
