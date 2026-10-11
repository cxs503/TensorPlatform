# -*- coding: utf-8 -*-
"""Tests for drift z-score math and inference helpers (api/inference, api/audit).

Pure-function / fake-DB tests only — no running server, no real DB.
"""
import json
import math
from pathlib import Path

import pytest

import api.inference as inference
from api.audit import record_audit


# ----------------------------------------------------------- drift z-score math
def _baseline(mean, std, name="f"):
    return [{"name": name, "mean": float(mean), "std": float(std)}]


def test_row_drift_flags_value_beyond_3_sigma():
    bl = _baseline(0.0, 1.0)
    # value 4.0 -> z = 4 > 3 -> drifted
    drifted = inference._row_drift(bl, [4.0])
    assert len(drifted) == 1
    assert drifted[0]["name"] == "f"
    assert drifted[0]["z"] == pytest.approx(4.0, abs=0.01)


def test_row_drift_no_flag_within_3_sigma():
    bl = _baseline(0.0, 1.0)
    assert inference._row_drift(bl, [2.5]) == []  # z=2.5 < 3


def test_row_drift_uses_std_floor_to_avoid_div_by_zero():
    bl = _baseline(5.0, 0.0)  # std=0 -> code uses 1e-8
    # value 5.0 + 1e-8 -> z ~ 1, not drifted
    assert inference._row_drift(bl, [5.0]) == []
    # value far from mean -> z huge -> drifted
    assert len(inference._row_drift(bl, [10.0])) == 1


def test_row_drift_respects_positional_alignment_and_short_row():
    # two features; row only supplies one value -> only first checked
    bl = _baseline(0.0, 1.0, "f0") + _baseline(0.0, 1.0, "f1")
    drifted = inference._row_drift(bl, [10.0])
    assert [d["name"] for d in drifted] == ["f0"]


# --------------------------------------------------------- _load_baseline / _find_model_path
def test_load_baseline_reads_feature_baseline_from_run_json(tmp_path, monkeypatch):
    # place a run json whose name matches the model_path transform logic:
    # model_path "/.../models/X_model.pkl" -> run "/.../runs/X.json"
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    run_file = runs_dir / "demo.json"
    run_file.write_text(json.dumps({
        "config": {"feature_baseline": [{"name": "x", "mean": 1.0, "std": 2.0}]}
    }))

    model_path = str(tmp_path / "models" / "demo_model.pkl")
    bl = inference._load_baseline(model_path)
    assert bl == [{"name": "x", "mean": 1.0, "std": 2.0}]


def test_load_baseline_missing_file_returns_empty(tmp_path):
    model_path = str(tmp_path / "models" / "none_model.pkl")
    assert inference._load_baseline(model_path) == []


def test_find_model_path_picks_latest_completed(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()

    def _write(name, model_id, status, model_path, mtime_offset):
        # NOTE: _find_model_path reads model_path at TOP LEVEL (not in config),
        # and model_id from config.
        f = runs / name
        f.write_text(json.dumps({
            "status": status,
            "model_path": model_path,
            "config": {"model_id": model_id},
        }))
        import os, time
        ts = f.stat().st_mtime + mtime_offset
        os.utime(f, (ts, ts))

    # older completed record for model 5
    _write("train_old.json", 5, "completed", "/old/model.pkl", -1000)
    # newer completed record for model 5 -> should win
    _write("train_new.json", 5, "completed", "/new/model.pkl", 0)
    # a non-completed record (must be ignored)
    _write("train_failed.json", 5, "failed", "/failed/model.pkl", 1000)
    # a record for a different model (must be ignored)
    _write("train_other.json", 6, "completed", "/other/model.pkl", 2000)

    monkeypatch.setattr(inference, "RUNS_DIR", runs)
    found = inference._find_model_path(5, db=None)
    assert found == "/new/model.pkl"


def test_find_model_path_no_match_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(inference, "RUNS_DIR", tmp_path)
    assert inference._find_model_path(999, db=None) is None


# --------------------------------------------------------------- _log_inference / _raise_drift_alert
def test_log_inference_is_noop_when_db_is_none():
    # should not raise even with None db
    assert inference._log_inference(None, 1, [[1.0]], [{"index": 0}], 1.5) is None


def test_raise_drift_alert_noop_on_empty_or_none_db(fake_db):
    assert inference._raise_drift_alert(None, 1, ["f"]) is None
    assert inference._raise_drift_alert(fake_db, 1, []) is None


def test_raise_drift_alert_inserts_alert_when_db_present(fake_db):
    inference._raise_drift_alert(fake_db, 7, ["feat_a", "feat_b"])
    # at least one INSERT executed
    stmts = [s for s, _ in fake_db.executed if isinstance(s, str)]
    assert any("INSERT INTO alerts" in s for s in stmts)
    assert any("__commit__" == s for s, _ in fake_db.executed)


# --------------------------------------------------------------- record_audit
def test_record_audit_noop_when_db_is_none():
    assert record_audit(None, "inference", "model", "1") is None


def test_record_audit_handles_db_errors_gracefully(fake_db):
    # force execute() to raise
    fake_db.execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    # must NOT propagate the exception
    record_audit(fake_db, "inference", "model", "1", detail="x")
    assert ("__rollback__", None) in fake_db.executed
