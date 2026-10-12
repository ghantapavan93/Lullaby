"""Test harness: a real PostgreSQL pair (app + provider), reset per test.

Tests never use SQLite. The guarantees under test (row locks, ON CONFLICT,
SKIP LOCKED, append-only triggers, composite foreign keys) are PostgreSQL
behavior, so they are tested against PostgreSQL.
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://proof@127.0.0.1:54329/proof_layer_test")
os.environ.setdefault("PROVIDER_DATABASE_URL", "postgresql+psycopg://proof@127.0.0.1:54329/provider_sim_test")
os.environ["PROOF_LAYER_DEV"] = "1"
# Video evidence: tests use the generated staged clip and their own private
# media store, never the local CC0 infant sample (kept separate on purpose).
import tempfile  # noqa: E402
os.environ.setdefault("PROOF_LAYER_MEDIA_ROOT", tempfile.mkdtemp(prefix="pl-media-"))
os.environ["PROOF_LAYER_SAMPLE"] = "staged"
os.environ.setdefault("PROOF_LAYER_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024))

import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import provider, seed  # noqa: E402
from app.db import transaction  # noqa: E402
from app.main import app  # noqa: E402
from app.security import issue_token  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def pytest_sessionstart(session):
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True,
                   capture_output=True, env=os.environ.copy())
    provider.create_schema()


@pytest.fixture(scope="session", autouse=True)
def _report_names_its_code(record_testsuite_property):
    """The JUnit report records the code it ran on; certification compares it."""
    from app import proof
    record_testsuite_property("code_digest", proof.code_digest())
    record_testsuite_property("commit", proof.git("rev-parse", "--short", "HEAD") or "unknown")


@pytest.fixture
def demo():
    with transaction() as db:
        return seed.load(db)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def auth(member_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(member_id)}"}


PARENT = auth(seed.PARENT_A)
CAREGIVER = auth(seed.CAREGIVER_B)
OTHER = auth(seed.OTHER_OWNER)
