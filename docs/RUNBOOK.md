# Runbook

How to diagnose the failures this system is designed to surface. Every response carries `X-Trace-Id`. The same id appears on revisions, acknowledgments, decisions, intents, transitions, outbox jobs and authorization records, so one id finds the whole story.

```sql
-- psql -h 127.0.0.1 -p 54329 -U proof proof_layer
SELECT 'revision' AS kind, id FROM briefing_revisions WHERE trace_id = :t
UNION ALL SELECT 'intent', id FROM purchase_intents WHERE trace_id = :t
UNION ALL SELECT 'auth:' || action, allowed::text FROM authorization_log WHERE trace_id = :t;
```

## A correction was rejected

| Code | Meaning | Action |
|---|---|---|
| `REVISION_CONFLICT` | The client's `expected_revision` is behind | Normal under concurrency. The client should show the newer revision, then retry. Check `detail.current_revision`. |
| `EVENT_KEY_REUSED` | The same `client_event_id` was sent with different content | A client bug: it must generate a new key per new correction. The original is kept (`detail.original_event_id`). |
| `ALREADY_CORRECTED` | The target was already superseded | Correct the newer record (`detail.superseded_by`). |
| `OUTSIDE_WINDOW` / `AMBIGUOUS_TIMESTAMP` | The correction leaves its window, or has no UTC offset | Fix the client input. Cross-window moves are not supported by design. |

Check that originals are intact: `SELECT id, supersedes_event_id FROM evidence_events WHERE child_id = :c ORDER BY received_at;`. Rows are never updated, and the trigger raises if anything tries.

## A caregiver's acknowledgment looks stale

That is usually correct. The acknowledgment binds to the revision they displayed. Inspect it:

```sql
SELECT member_id, revision, created_at FROM acknowledgments WHERE window_id = :w ORDER BY revision;
SELECT * FROM ack_heads WHERE window_id = :w;
```

`ack_heads.revision` only moves up. If a member says "I acknowledged it", compare their acknowledged revision with `briefing_windows.current_revision`. `GET /v1/windows/{id}/changes` shows per-fact status.

## Outbox backlog

```sql
SELECT kind, state, count(*), max(attempts), min(available_at) FROM outbox GROUP BY 1, 2;
SELECT id, kind, attempts, last_error FROM outbox WHERE state IN ('pending', 'dead') ORDER BY id;
```

- `pending` with old `available_at`: no worker is running. Start one with `python -m app.worker`.
- `in_progress` with an expired `lease_until`: a worker died. The next worker reclaims the job automatically.
- `dead`: 8 failed attempts. Read `last_error`, fix the cause, then `UPDATE outbox SET state='pending', attempts=0, available_at=now() WHERE id=:id`. Re-running a submit job is safe, because the provider deduplicates on the operation key.

## A purchase is UNKNOWN or stuck in SUBMITTED

1. `GET /v1/purchase-intents/{id}` shows the transitions, the outbox job and the provider's own record.
2. The provider record is `SUCCEEDED` or `FAILED`: run `python -m app.worker --once`. It reconciles first and records exactly that outcome.
3. The provider record is `PENDING`: leave it UNKNOWN. Never mark it confirmed by hand. The worker keeps asking every 5 seconds.
4. There is no provider record and the intent is SUBMITTED: the call never reached the provider. Reconciliation re-queues the **same** operation key.
5. Never create a new intent or a new key to "unstick" one. That is how double charges happen.

## Entitlement or eligibility errors

| Symptom | Check |
|---|---|
| `STALE_DECISION` | `detail.reasons`: `HOUSEHOLD_STATE_CHANGED` (a plan or mount changed: compare `households.state_revision`), `DECISION_EXPIRED` (15-minute TTL), `CATALOG_CHANGED` / `POLICY_CHANGED` (`commercial_config`). Ask for a fresh decision. |
| `NO_ELIGIBLE_OFFER` | A hardware blocker, or unknown inputs (for example, no device on record). Correct: never sell a plan that cannot help. |
| Plan didn't change after CONFIRMED | `SELECT * FROM subscriptions WHERE household_id=:h` should show `source = simulated_purchase:<intent>`. If not, check `intent_transitions` for the CONFIRMED row. The grant is in the same transaction. |
| Feature still blocked after upgrade | Re-evaluate. The decision shown before the purchase is an immutable snapshot of the old state. |

## Resetting

`python scripts/devdb.py reset` reloads the synthetic scenario. It truncates the app tables and the provider ledger.
