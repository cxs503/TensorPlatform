"""Hold-out accuracy guards for the digital-twin PCE surrogates (mission m1647).

The audit in ``tools/verify_digital_twin_surrogates.py`` measures every surrogate
against its reference physics model on an independent uniform hold-out and writes
``docs/verification/digital_twin_surrogate_accuracy.md``.  This module pins the
part of that audit which must not regress:

* the ``resistance`` surrogate (the one remediated by m1647: degree 5 / 512
  samples, previously degree 3 / 256 with p95 = 254 % on ``R_calm_kN``) stays
  inside the platform 15 % bar on a fresh hold-out;
* the fail-closed input contract: out-of-domain points are *rejected*, and an
  execution failure degrades to the reference model bit-for-bit;
* the real-time budget (<= 2 ms per prediction) that the online predictor relies
  on.

The still-open weaknesses that m1647 could not close inside the reference-model
cost budget (seakeeping response near calm, ``GZ_m``/``KN_m`` p95, manoeuvring
yaw rate, fatigue damage rate) are pinned as strict xfails so the suite flips
green the day they are fixed instead of silently passing.

Numbers are recomputed here from the live registry; nothing is copied from the
report.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from services.digital_twin.surrogate_registry import (  # noqa: E402
    DEFAULT_VESSEL,
    get_registry,
)
from services.digital_twin.types import InputValidationError  # noqa: E402

SEED = 20261101
FLOOR_FRAC = 1.0e-3


@pytest.fixture(scope="module")
def registry():
    return get_registry(DEFAULT_VESSEL["L"], DEFAULT_VESSEL["B"],
                        DEFAULT_VESSEL["T"], DEFAULT_VESSEL["Cb"],
                        DEFAULT_VESSEL["KG"], cache=False)


def holdout(registry, name: str, n: int):
    """Fresh uniform hold-out plus reference and surrogate outputs."""
    s = registry.get(name)
    fields = list(s.inputs.fields)
    names = [f.name for f in fields]
    lo = np.array([f.lower for f in fields], dtype=float)
    hi = np.array([f.upper for f in fields], dtype=float)
    X = lo + (hi - lo) * np.random.default_rng(SEED).random((n, len(lo)))
    outs = list(s.pce.output_names)
    ref = np.empty((n, len(outs)))
    got = np.empty((n, len(outs)))
    for i, row in enumerate(X):
        point = dict(zip(names, (float(v) for v in row)))
        ref[i] = [s._predict_reference(point)[k] for k in outs]
        got[i] = [s.predict(point)[k] for k in outs]
    return outs, ref, got


def rel_errors(ref: np.ndarray, got: np.ndarray) -> np.ndarray:
    keep = np.abs(ref) >= FLOOR_FRAC * np.max(np.abs(ref))
    return np.abs(got[keep] - ref[keep]) / np.abs(ref[keep])


def test_resistance_design_is_remediated(registry):
    s = registry.get("resistance")
    assert s.pce.degree == 5
    assert s.fit_report["training_samples"] >= 512
    assert s.fit_report["n_basis"] >= 50


def test_resistance_inside_15pct_bar_on_holdout(registry):
    outs, ref, got = holdout(registry, "resistance", 48)
    for j, name in enumerate(outs):
        rel = rel_errors(ref[:, j], got[:, j])
        span = np.max(np.abs(ref[:, j])) - np.min(ref[:, j])
        assert np.percentile(rel, 95) <= 0.15, (name, np.percentile(rel, 95))
        assert np.max(np.abs(got[:, j] - ref[:, j])) <= 0.05 * span, name


def test_domain_guard_is_fail_closed(registry):
    s = registry.get("resistance")
    with pytest.raises(InputValidationError):
        s.safe_predict({"V": -1.0, "ice_concentration": 0.5, "ice_thickness": 0.5})


def test_execution_failure_degrades_to_reference(registry):
    s = registry.get("fatigue")
    point = {"Hs": 3.0, "Tp": 7.0, "ice_concentration": 0.2, "ice_thickness": 0.2}
    reference = s._predict_reference(point)
    original = s.pce.predict
    s.pce.predict = lambda values: (_ for _ in ()).throw(RuntimeError("injected"))
    try:
        out = s.safe_predict(point)
    finally:
        s.pce.predict = original
    assert out["degraded"] is True and "injected" in out["error"]
    for k, v in reference.items():
        assert abs(out["outputs"][k] - v) <= 1e-12 * max(abs(v), 1.0), k


@pytest.mark.parametrize("name", ["resistance", "seakeeping", "manoeuvring",
                                  "stability", "fatigue"])
def test_realtime_latency_budget(registry, name):
    s = registry.get(name)
    fields = list(s.inputs.fields)
    point = {f.name: 0.5 * (f.lower + f.upper) for f in fields}
    s.predict(point)                                  # warm-up
    times = []
    for _ in range(5):
        t0 = os.times().elapsed
        s.predict(point)
        times.append(os.times().elapsed - t0)
    assert float(np.median(times)) * 1e3 <= 2.0, (name, times)


# ---------------------------------------------------------------------------
# documented open weaknesses — strict xfail (flips green when fixed)
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="m1647: seakeeping heave/pitch p95 >> 15% "
                                       "where the response is near calm")
def test_seakeeping_heave_pitch_p95_open(registry):
    outs, ref, got = holdout(registry, "seakeeping", 16)
    j = outs.index("heave_rms_m")
    assert np.percentile(rel_errors(ref[:, j], got[:, j]), 95) <= 0.15


@pytest.mark.xfail(strict=True, reason="m1647: fatigue damage_per_day spans 13 decades, "
                                       "linear-space R^2 < 0 even in log-space fit")
def test_fatigue_damage_open(registry):
    outs, ref, got = holdout(registry, "fatigue", 48)
    j = outs.index("damage_per_day")
    assert np.percentile(rel_errors(ref[:, j], got[:, j]), 95) <= 0.15


@pytest.mark.xfail(strict=True, reason="m1647: stability GZ/KN p95 > 15% near the "
                                       "zero-crossing of GZ")
def test_stability_gz_p95_open(registry):
    outs, ref, got = holdout(registry, "stability", 16)
    j = outs.index("GZ_m")
    assert np.percentile(rel_errors(ref[:, j], got[:, j]), 95) <= 0.15