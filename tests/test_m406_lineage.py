#!/usr/bin/env python3
"""M406 — execution lineage (provenance) verification.

Revival note
------------
Until this revision the module held its entire verification inside ``main()``:
pytest collected ``tests/test_m406_lineage.py`` (name matches ``test_*.py``),
found **zero items**, and reported green.  ``main()`` also rebound the global
``database.config.engine`` / ``SessionLocal`` and set ``os.environ
["DATABASE_URL"]`` at *import* time, i.e. every collection run silently
retargeted the platform's database for the whole pytest session.

The checks are now real test functions on a private in-memory engine (no global
mutation), and ``main()`` is retained only as the artifact writer that produces
``artifacts/lineage_test.json``.

What is verified
----------------
1. Ten representative executions produce ten *complete* lineage records (all
   four SHA-256 fingerprints present, execution ids unique).
2. ``_compute_hash`` is deterministic, discriminating and has a fixed contract
   for empty input (``sha256(b"{}")``).
3. Lineage records group into per-scenario chains (the traceability property
   that ``GET /lineage/{execution_id}`` relies on).
4. The execution-id uniqueness constraint is actually enforced by the schema.

Detector that keeps this from regressing: ``tools/audit/test_surface_census.py``
(``vacuous_module`` / ``import_side_effect``).
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND = Path(__file__).resolve().parents[1] / "backend"


def _load_lineage_model():
    """Load ``backend/models/lineage.py`` without building the platform engine.

    Importing ``models.lineage`` the normal way executes
    ``backend/models/__init__.py`` -> ``database.config``, which constructs a
    PostgreSQL engine at import time and therefore requires ``psycopg2`` (absent
    in this image).  The original version of this file worked around that by
    setting ``DATABASE_URL`` and rebinding the global engine at import time,
    which contaminated every other test in the pytest session.

    We only need the ORM model, so the module file is loaded directly with a
    throwaway ``database.config`` stub that owns its own declarative ``Base``.
    The stub is removed again immediately, so nothing else can inherit it.
    """
    import importlib.util
    import types

    from sqlalchemy.orm import declarative_base

    injected = "database.config" not in sys.modules
    if injected:
        stub = types.ModuleType("database.config")
        stub.Base = declarative_base()
        sys.modules["database.config"] = stub
    try:
        spec = importlib.util.spec_from_file_location("m406_lineage_model", BACKEND / "models" / "lineage.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if injected:
            sys.modules.pop("database.config", None)
    return module


_lineage_model = _load_lineage_model()
LineageRecord = _lineage_model.LineageRecord
_compute_hash = _lineage_model._compute_hash

G = 9.80665

#: Ten representative platform executions (resistance / propulsion / seakeeping /
#: maneuvering / stability / green voyage), as used by the original M406 probe.
SCENARIOS = [
    {"scenario_key": "resistance-calibration", "stage": "S2", "config": {"speed_kn": 12.0, "wave_height": 1.5}, "params": {"Re": 1e8, "Fr": 0.28}},
    {"scenario_key": "resistance-calibration", "stage": "S2", "config": {"speed_kn": 14.0, "wave_height": 2.0}, "params": {"Re": 1.2e8, "Fr": 0.32}},
    {"scenario_key": "resistance-calibration", "stage": "S2", "config": {"speed_kn": 16.0, "wave_height": 2.5}, "params": {"Re": 1.4e8, "Fr": 0.36}},
    {"scenario_key": "propulsion-optimization", "stage": "S3", "config": {"rpm": 120, "pitch_ratio": 0.85}, "params": {"Kt": 0.45, "Kq": 0.04}},
    {"scenario_key": "propulsion-optimization", "stage": "S3", "config": {"rpm": 140, "pitch_ratio": 0.90}, "params": {"Kt": 0.48, "Kq": 0.042}},
    {"scenario_key": "seakeeping-assessment", "stage": "S2", "config": {"wave_period": 8.0, "heading": 180}, "params": {"RAO_max": 2.3}},
    {"scenario_key": "seakeeping-assessment", "stage": "S2", "config": {"wave_period": 10.0, "heading": 135}, "params": {"RAO_max": 3.1}},
    {"scenario_key": "maneuvering-test", "stage": "S1", "config": {"rudder_angle": 35, "speed_kn": 10}, "params": {"tactical_diameter": 450}},
    {"scenario_key": "stability-check", "stage": "S0", "config": {"heel_angle": 15, "displacement": 50000}, "params": {"GZ_max": 1.2}},
    {"scenario_key": "green-voyage", "stage": "S3", "config": {"speed_kn": 11, "weather_factor": 0.95}, "params": {"EEDI": 3.2}},
]

ENV_FINGERPRINT = {"python": "3.11", "os": "linux", "gpu": "none"}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def make_session():
    """Private in-memory engine — never touches ``database.config``."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    LineageRecord.__table__.create(bind=engine)
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE tensor_executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    organization_id INTEGER DEFAULT 1,
                    execution_key TEXT,
                    scenario_key TEXT,
                    stage TEXT,
                    status TEXT DEFAULT 'queued'
                )
                """
            )
        )
        conn.commit()
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False)()


@pytest.fixture()
def session():
    engine, db = make_session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def record_scenarios(db, scenarios=None) -> list[dict]:
    """Insert executions + lineage records exactly as the platform does."""
    scenarios = SCENARIOS if scenarios is None else scenarios
    out: list[dict] = []
    for index, scenario in enumerate(scenarios):
        execution_key = f"{scenario['scenario_key']}-{scenario['stage']}-20260101-{index + 1:03d}"
        db.execute(
            text(
                "INSERT INTO tensor_executions (organization_id, execution_key, scenario_key, stage, status)"
                " VALUES (:org, :ekey, :skey, :stage, 'succeeded')"
            ),
            {"org": 1, "ekey": execution_key, "skey": scenario["scenario_key"], "stage": scenario["stage"]},
        )
        execution_id = db.execute(text("SELECT last_insert_rowid()")).scalar()

        record = LineageRecord(
            execution_id=execution_id,
            config_hash=_compute_hash(scenario["config"]),
            env_hash=_compute_hash(ENV_FINGERPRINT),
            data_hash=_compute_hash({"dataset": f"ds_{scenario['scenario_key']}", "version": "1.0"}),
            param_hash=_compute_hash(scenario["params"]),
            scenario_key=scenario["scenario_key"],
            stage=scenario["stage"],
            status="succeeded",
            extra_metadata={"test_case": index + 1, "execution_key": execution_key},
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        out.append(
            {
                "execution_id": execution_id,
                "execution_key": execution_key,
                "scenario_key": scenario["scenario_key"],
                "stage": scenario["stage"],
                "config_hash": record.config_hash,
                "env_hash": record.env_hash,
                "data_hash": record.data_hash,
                "param_hash": record.param_hash,
                "record_id": record.id,
            }
        )
    return out


def verify_lineage(db) -> dict:
    records = db.query(LineageRecord).order_by(LineageRecord.execution_id).all()
    chains: dict[str, list[int]] = {}
    for record in records:
        chains.setdefault(record.scenario_key, []).append(record.execution_id)
    return {
        "total_records": len(records),
        "all_have_config_hash": all(bool(r.config_hash) for r in records),
        "all_have_env_hash": all(bool(r.env_hash) for r in records),
        "all_have_data_hash": all(bool(r.data_hash) for r in records),
        "all_have_param_hash": all(bool(r.param_hash) for r in records),
        "unique_execution_ids": len({r.execution_id for r in records}) == len(records),
        "chains": chains,
        "records": [r.to_dict() for r in records],
    }


# --------------------------------------------------------------------------- #
# Verifications
# --------------------------------------------------------------------------- #
def test_ten_executions_yield_complete_lineage(session):
    """Every execution gets one complete, uniquely identified lineage record."""
    written = record_scenarios(session)
    assert len(written) == 10, f"expected 10 executions, wrote {len(written)}"

    verification = verify_lineage(session)
    assert verification["total_records"] == 10
    for key in ("all_have_config_hash", "all_have_env_hash", "all_have_data_hash", "all_have_param_hash"):
        assert verification[key] is True, f"incomplete fingerprint set: {key}"
    assert verification["unique_execution_ids"] is True

    for record in verification["records"]:
        for field in ("config_hash", "env_hash", "data_hash", "param_hash"):
            value = record[field]
            assert isinstance(value, str) and len(value) == 64, f"{field} is not a SHA-256 hex digest: {value!r}"
            int(value, 16)  # hexadecimal
        assert record["status"] == "succeeded"


def test_lineage_record_links_back_to_execution(session):
    """The lineage row must point at the execution row it describes."""
    written = record_scenarios(session)
    for entry in written:
        row = session.execute(
            text("SELECT scenario_key, stage FROM tensor_executions WHERE id = :id"),
            {"id": entry["execution_id"]},
        ).mappings().one()
        assert row["scenario_key"] == entry["scenario_key"]
        assert row["stage"] == entry["stage"]
        stored = session.query(LineageRecord).filter_by(execution_id=entry["execution_id"]).one()
        assert stored.config_hash == entry["config_hash"]


def test_hashes_are_deterministic_and_discriminating():
    """Same input -> same fingerprint; different input -> different fingerprint."""
    config = {"speed_kn": 12.0, "wave_height": 1.5}
    assert _compute_hash(dict(config)) == _compute_hash(dict(config))
    # key order must not matter (canonical JSON)
    assert _compute_hash({"a": 1, "b": 2}) == _compute_hash({"b": 2, "a": 1})
    assert _compute_hash(config) != _compute_hash({"speed_kn": 12.5, "wave_height": 1.5})
    # documented contract for empty input
    assert _compute_hash(None) == hashlib.sha256(b"{}").hexdigest()
    assert _compute_hash({}) == _compute_hash(None)


def test_lineage_chain_groups_by_scenario(session):
    """Records must group into per-scenario chains with the expected lengths."""
    record_scenarios(session)
    chains = verify_lineage(session)["chains"]
    expected = {
        "resistance-calibration": 3,
        "propulsion-optimization": 2,
        "seakeeping-assessment": 2,
        "maneuvering-test": 1,
        "stability-check": 1,
        "green-voyage": 1,
    }
    assert {k: len(v) for k, v in chains.items()} == expected
    assert sum(len(v) for v in chains.values()) == 10
    # chain order follows execution order
    for ids in chains.values():
        assert ids == sorted(ids)


def test_duplicate_execution_id_is_rejected(session):
    """Schema enforces one lineage record per execution (provenance integrity)."""
    record_scenarios(session)
    first = session.query(LineageRecord).first()
    clone = LineageRecord(
        execution_id=first.execution_id,
        config_hash=first.config_hash,
        env_hash=first.env_hash,
        data_hash=first.data_hash,
        param_hash=first.param_hash,
        scenario_key=first.scenario_key,
        stage=first.stage,
        status="succeeded",
    )
    session.add(clone)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    assert session.query(LineageRecord).count() == 10


# --------------------------------------------------------------------------- #
# Artifact writer (kept from the original script)
# --------------------------------------------------------------------------- #
def main() -> int:
    engine, db = make_session()
    try:
        executions = record_scenarios(db)
        verification = verify_lineage(db)
    finally:
        db.close()
        engine.dispose()

    payload = {
        "mission_id": "M406",
        "test_name": "谱系追溯端到端验证",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "sqlite in-memory (private to this run)",
        "total_executions": len(executions),
        "total_lineage_records": verification["total_records"],
        "provenance_complete": all(
            verification[k]
            for k in ("all_have_config_hash", "all_have_env_hash", "all_have_data_hash", "all_have_param_hash")
        ),
        "executions": executions,
        "chains": {k: v for k, v in verification["chains"].items()},
        "verdict": "PASS" if verification["total_records"] == len(executions) == 10 else "FAIL",
    }
    out = Path("artifacts/lineage_test.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("total_executions", "total_lineage_records", "provenance_complete", "verdict")}, ensure_ascii=False))
    return 0 if payload["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())