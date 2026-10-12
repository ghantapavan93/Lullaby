"""Experiment health on synthetic households: why exposure must be checked
before any outcome is read.

    python -m bench.experiment_check

1. Assigns every benchmark household to `availability_explanation` with the
   application's own sticky assignment.
2. Simulates exposure receipts: 90% of households render the surface, but one
   variant has an injected rendering bug that silently drops 6% of its
   receipts. This is a synthetic fault, not data about anyone.
3. Runs the sample-ratio check on assignments and on exposures.

Expected: assignment balanced; exposure flagged as a mismatch, so the
experiment's outcome must not be read. Results: artifacts/benchmarks/srm-*.json
"""
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://proof@127.0.0.1:54329/proof_layer_bench")
os.environ.setdefault("PROVIDER_DATABASE_URL", "postgresql+psycopg://proof@127.0.0.1:54329/provider_sim_bench")

from sqlalchemy import select, text  # noqa: E402

from app import growth  # noqa: E402
from app.db import transaction  # noqa: E402
from app.models import Household  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "artifacts" / "benchmarks"
KEY = "availability_explanation"
BROKEN_VARIANT, DROP_RATE, RENDER_RATE = "requirements_checklist", 0.06, 0.90


def main(seed: int = 11) -> dict:
    rng = random.Random(seed)
    with transaction() as db:
        db.execute(text("DELETE FROM render_receipts WHERE render_id LIKE 'srm-%'"))
        db.execute(text("DELETE FROM experiment_assignments WHERE experiment_key = :k"), {"k": KEY})
        households = list(db.execute(select(Household.id).order_by(Household.id)).scalars())
        variants = {hh: growth.assignment(db, hh, KEY).variant for hh in households}
        rows = []
        for hh, variant in variants.items():
            if rng.random() >= RENDER_RATE:
                continue
            if variant == BROKEN_VARIANT and rng.random() < DROP_RATE:
                continue                      # the injected bug: rendered, but the receipt never arrives
            member = "m_" + hh[3:] + "_a"
            rows.append({"id": f"rr_srm_{hh}", "household_id": hh, "member_id": member, "render_id": f"srm-{hh}",
                         "surface": "feature_availability", "subject_type": "decision", "subject_id": f"srm-{hh}",
                         "experiment_key": KEY, "config_version": growth.EXPERIMENTS[KEY]["config_version"],
                         "variant": variant})
        db.execute(text("INSERT INTO render_receipts (id, household_id, member_id, render_id, surface, subject_type, "
                        "subject_id, experiment_key, config_version, variant) VALUES (:id, :household_id, :member_id, "
                        ":render_id, :surface, :subject_type, :subject_id, :experiment_key, :config_version, :variant)"),
                   rows)
    with transaction() as db:
        health = growth.experiment_health(db, KEY)
    result = {"synthetic": True, "households": len(households), "seed": seed,
              "injected_fault": f"{DROP_RATE:.0%} of '{BROKEN_VARIANT}' receipts dropped",
              "render_rate": RENDER_RATE, **health,
              "executed_at": datetime.now(timezone.utc).isoformat()}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"srm-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))
    return result


if __name__ == "__main__":
    main()
