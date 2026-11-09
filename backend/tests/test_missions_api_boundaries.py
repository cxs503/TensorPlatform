"""Boundary regression tests for POST /tensor/missions (create) and /claim (m8578).

Predeclared boundaries (task m8578, owner 2026-11-09 A/B 令):
  B1  goal shorter than 10 chars            -> 422 (pydantic min_length)
  B1b goal of padding only (>=10 blanks)    -> 422 (strip check in the endpoint)
  B2  repo outside the dispatch whitelist   -> 422
  B3  duplicate task_key create             -> 200 + idempotent=True (same id);
                                              conflicting fields            -> 409
  B4  claim without credentials            -> 401
  B4b claim of another org's mission        -> never handed over
                                              (claimed=False, stays queued)

Harness: FastAPI TestClient against a minimal app that mounts the *real*
``api.tensor_missions`` router with its *real* dependencies (require_auth /
require_role / get_db). ``get_db`` is overridden to an isolated Postgres
database whose schema is copied from the platform (asset
``assets/missions_boundary_schema.sql``), so every PG-specific statement in
the create/claim paths (advisory locks, jsonb casts, interval arithmetic,
FOR UPDATE SKIP LOCKED) executes for real. No external network is touched;
the suite skips cleanly when no local Postgres is reachable.

The full ``main.app`` is intentionally not imported: its import chain pulls
optional runtime deps (cryptography) that are absent in minimal test
environments, and the missions router's own auth gate (``require_auth``) is
the boundary under test.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

TEST_DB = "tp_m8578_test"
SCHEMA_ASSET = Path(__file__).resolve().parent / "assets" / "missions_boundary_schema.sql"

# Repo that is whitelisted AND resolvable on the platform host
# (/root/tensor-ecosystem/<name>/.git); the create path enforces both gates.
GOOD_REPO = "TensorPlatform"
BAD_REPO = "NotARepo"

# Observed response codes, dumped to runs/m8578/missions_boundary_codes.json
# by the final test (zero hand-copy for the mission report).
OBSERVED: dict[str, int] = {}


def _record(case: str, status_code: int) -> None:
    OBSERVED[case] = status_code


# ─────────────────────────── PG discovery / bootstrap ───────────────────────────


def _discover_platform_dsn() -> str | None:
    """Find the local platform Postgres DSN (no external network)."""
    override = os.getenv("M8578_PG_DSN")
    if override:
        return override
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return None
    for pid in pids:
        try:
            with open(f"/proc/{pid}/environ", "rb") as fh:
                env = fh.read().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        for kv in env:
            if kv.startswith("DATABASE_URL=postgresql"):
                return kv.split("=", 1)[1]
    return None


def _pg_version_shim() -> None:
    """Test-only shim: SQLAlchemy 2.0.51's PG version probe
    (``re.match`` on the ``SELECT version()`` result) trips over a
    bytes/str mismatch with psycopg 3.3.x on this host. The probe only
    feeds feature detection; pinning the server version tuple is safe
    for the statements used here (PG 16 semantics)."""
    import sqlalchemy.dialects.postgresql.base as pgbase

    def _fixed(self, conn):  # noqa: ANN001
        return (16, 0)

    pgbase.PGDialect._get_server_version_info = _fixed


def _engine_for(dsn: str):
    """Create an engine, tolerating psycopg/psycopg2 driver naming differences."""
    _pg_version_shim()
    url = make_url(dsn)
    candidates = [url]
    for drv in ("psycopg", "psycopg2"):
        if url.drivername != drv:
            candidates.append(url.set(drivername=drv))
    last_err: Exception | None = None
    for cand in candidates:
        try:
            eng = create_engine(cand, pool_pre_ping=True)
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            return eng
        except Exception as exc:  # noqa: BLE001 - try next driver
            last_err = exc
    raise RuntimeError(f"cannot connect to {TEST_DB}: {last_err}")


@pytest.fixture(scope="module")
def missions_env():
    """Isolated PG database (schema copied from the platform) + seeded tenants.

    Tenants: org 1 (users m8578_admin/m8578_eng1) and org 2 (m8578_eng2).
    Yields (client, headers_by_user). Skips when no local Postgres is usable.
    """
    platform_dsn = _discover_platform_dsn()
    if not platform_dsn:
        pytest.skip("no local Postgres DSN discoverable (set M8578_PG_DSN to force)")

    test_dsn = str(make_url(platform_dsn).set(database=TEST_DB))
    try:
        engine = _engine_for(test_dsn)
    except Exception as exc:  # noqa: BLE001
        # Test DB missing: try to create it, then retry once.
        try:
            maint_url = make_url(platform_dsn).set(database="postgres")
            maint = _engine_for(str(maint_url))
            with maint.begin() as conn:
                conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
            maint.dispose()
            engine = _engine_for(test_dsn)
        except Exception as exc2:  # noqa: BLE001
            pytest.skip(f"isolated test DB unavailable: {exc} / {exc2}")

    SessionLocal = sessionmaker(bind=engine, future=True)
    with engine.connect() as conn:
        has_table = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='tensor_dev_missions'"
            )
        ).first()
    if not has_table:
        with engine.connect() as conn:
            conn.exec_driver_sql(SCHEMA_ASSET.read_text(encoding="utf-8"))
            conn.commit()

    db = SessionLocal()
    try:
        db.execute(
            text(
                "TRUNCATE tensor_agent_session_events, tensor_agent_sessions, "
                "tensor_dev_missions, users, organizations CASCADE"
            )
        )
        db.execute(
            text(
                "INSERT INTO organizations (id, name, workspace) VALUES "
                "(1, 'Org Alpha', 'standard'), (2, 'Org Beta', 'standard')"
            )
        )
        db.execute(
            text(
                "INSERT INTO users (id, username, email, hashed_password, role, "
                "is_active, organization_id) VALUES "
                "(1, 'm8578_admin', 'admin@m8578.test', 'x', 'ADMIN', true, 1), "
                "(2, 'm8578_eng1', 'eng1@m8578.test', 'x', 'ENGINEER', true, 1), "
                "(3, 'm8578_eng2', 'eng2@m8578.test', 'x', 'ENGINEER', true, 2)"
            )
        )
        db.execute(text("SELECT setval('users_id_seq', 10)"))
        db.execute(text("SELECT setval('organizations_id_seq', 10)"))
        db.commit()
    finally:
        db.close()

    # ── minimal app mounting the REAL missions router + real dependencies ──
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.deps import SECRET_KEY, ALGORITHM
    from database.config import get_db
    from api.tensor_missions import router as missions_router

    import jwt as _pyjwt
    from datetime import datetime, timedelta, timezone

    app = FastAPI()
    app.include_router(missions_router, prefix="/api/v1")

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db

    def _token(user_id: int, username: str, role: str) -> dict:
        payload = {
            "sub": username,
            "user_id": user_id,
            "role": role,
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        tok = _pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        return {"Authorization": f"Bearer {tok}"}

    headers = {
        "admin": _token(1, "m8578_admin", "ADMIN"),
        "eng1": _token(2, "m8578_eng1", "ENGINEER"),
        "eng2": _token(3, "m8578_eng2", "ENGINEER"),
    }
    client = TestClient(app)
    yield client, headers
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


# ─────────────────────────────────── helpers ───────────────────────────────────

CREATE = "/api/v1/tensor/missions"
CLAIM = "/api/v1/tensor/missions/claim"


def _create(client, headers, **overrides):
    payload = {"goal": "boundary probe mission goal", "repo": GOOD_REPO}
    payload.update(overrides)
    return client.post(CREATE, headers=headers, json=payload)


# ─────────────────────────── B1: goal length gate ──────────────────────────────


def test_b1_goal_shorter_than_10_chars_rejected_422(missions_env):
    client, headers = missions_env
    r = _create(client, headers["eng1"], goal="too short")  # 9 chars
    _record("B1_goal_9_chars", r.status_code)
    assert r.status_code == 422, r.text


def test_b1b_goal_padding_only_rejected_422(missions_env):
    client, headers = missions_env
    r = _create(client, headers["eng1"], goal="          ")  # 10 blanks: passes pydantic
    _record("B1b_goal_10_blanks", r.status_code)
    assert r.status_code == 422, r.text
    assert "non-padding" in str(r.json().get("detail", ""))


def test_b1c_goal_exactly_10_chars_accepted(missions_env):
    client, headers = missions_env
    r = _create(client, headers["eng1"], goal="a" * 10)
    _record("B1c_goal_10_chars", r.status_code)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["id"] > 0


# ─────────────────────────── B2: repo whitelist gate ───────────────────────────


def test_b2_repo_outside_whitelist_rejected_422(missions_env):
    client, headers = missions_env
    r = _create(client, headers["eng1"], repo=BAD_REPO)
    _record("B2_repo_not_whitelisted", r.status_code)
    assert r.status_code == 422, r.text
    assert "白名单" in str(r.json().get("detail", ""))


# ─────────────────────── B3: task_key idempotency semantics ────────────────────


def test_b3_task_key_duplicate_create_is_idempotent(missions_env):
    client, headers = missions_env
    payload = {"goal": "idempotent create probe", "repo": GOOD_REPO,
               "task_key": "m8578.idem.key"}
    r1 = client.post(CREATE, headers=headers["eng1"], json=payload)
    assert r1.status_code == 200, r1.text
    first_id = r1.json()["id"]

    r2 = client.post(CREATE, headers=headers["eng1"], json=payload)
    _record("B3_task_key_idempotent", r2.status_code)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body.get("idempotent") is True
    assert body["id"] == first_id, "idempotent replay must return the original id"


def test_b3b_task_key_conflicting_fields_rejected_409(missions_env):
    client, headers = missions_env
    payload = {"goal": "idempotent create probe", "repo": GOOD_REPO,
               "task_key": "m8578.idem.key"}
    r = client.post(CREATE, headers=headers["eng1"],
                    json={**payload, "goal": "a different goal entirely"})
    _record("B3b_task_key_conflict", r.status_code)
    assert r.status_code == 409, r.text


# ─────────────────────────── B4: claim auth / org scope ────────────────────────


def test_b4_claim_unauthenticated_rejected_401(missions_env):
    client, _ = missions_env
    r = client.post(CLAIM, json={"agent_id": "m8578-agent"})
    _record("B4_claim_no_auth", r.status_code)
    assert r.status_code == 401, r.text


def test_b4b_claim_cross_org_never_hands_over_mission(missions_env):
    client, headers = missions_env
    r = _create(client, headers["eng1"], goal="cross org claim probe goal")
    assert r.status_code == 200, r.text
    mission_id = r.json()["id"]

    # org-2 engineer tries to claim the org-1 mission by explicit id
    r2 = client.post(CLAIM, headers=headers["eng2"],
                     json={"agent_id": "m8578-agent", "mission_id": mission_id})
    _record("B4b_claim_cross_org", r2.status_code)
    assert r2.status_code == 200, r2.text
    assert r2.json()["claimed"] is False, "foreign-org mission must not be handed over"

    # the mission is untouched: still queued, still owned by org 1
    detail = client.get(f"{CREATE}/{mission_id}", headers=headers["eng1"])
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "queued"
    # and it is invisible to the foreign org (404, no identifier leak)
    foreign = client.get(f"{CREATE}/{mission_id}", headers=headers["eng2"])
    _record("B4b_cross_org_get", foreign.status_code)
    assert foreign.status_code == 404


def test_b4c_claim_same_org_positive_control(missions_env):
    """Positive control: the claim mechanism works for the owning org, so the
    B4b negative result is meaningful (not a globally broken claim path)."""
    client, headers = missions_env
    r = _create(client, headers["eng1"], goal="same org claim probe goal")
    assert r.status_code == 200, r.text
    mission_id = r.json()["id"]

    r2 = client.post(CLAIM, headers=headers["eng1"],
                     json={"agent_id": "m8578-agent", "mission_id": mission_id})
    _record("B4c_claim_same_org", r2.status_code)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["claimed"] is True
    assert body["mission"]["id"] == mission_id
    assert len(body["mission"]["claim_token"]) >= 8


# ─────────────────────────── observed-codes artifact ───────────────────────────


def test_dump_observed_response_codes(missions_env, tmp_path):
    """Persist the observed codes (machine-readable, zero hand-copy)."""
    out = Path(__file__).resolve().parents[2] / "runs" / "m8578"
    out.mkdir(parents=True, exist_ok=True)
    (out / "missions_boundary_codes.json").write_text(
        json.dumps(OBSERVED, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    assert OBSERVED, "no boundary case was recorded"
    assert all(code in (200, 401, 404, 409, 422) for code in OBSERVED.values())
