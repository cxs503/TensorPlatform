"""Tests for the seakeeping strip-theory capability verdict (offline, fixture-based).

Coverage:
  * control (specificity): a synthetic frequency-sensitive, limit-respecting RAO series
    must NOT be graded MOCK -- otherwise the detector would be a rubber stamp;
  * detection (sensitivity): a synthetic hardcoded table must be graded MOCK;
  * the frozen live fixture reproduces the published verdicts.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from tools.verifier.seakeeping_strip_verdict import (  # noqa: E402
    FIXTURE, PERTURBATIONS, _digest, adjudicate, series_digests, tables,
)

GRID = [round(0.2 + 0.1 * i, 3) for i in range(30)]


def _sdof_rao(omega: float, wn: float = 0.75, zeta: float = 0.25) -> float:
    """Long-wave-normalised SDOF heave RAO: -> 1 as omega -> 0, -> 0 as omega -> inf."""
    return wn ** 2 / np.hypot(wn ** 2 - omega ** 2, 2 * zeta * wn * omega)


def _case(label: str, delta: dict, payload: dict) -> dict:
    body = {"L": 320.0, "B": 58.0, "T": 20.8, "speed": 7.7, "wave_frequency": 0.5,
            "heading": 180.0}
    body.update(delta)
    return {"label": label, "request": body, "status": 200, "payload": payload,
            "_digest": _digest(payload)}


def _synthetic_record(real: bool) -> dict:
    """Real: RAO recomputed per wave frequency. Mock: one frozen table for all inputs."""
    frozen = [_sdof_rao(w) for w in GRID]
    cases = []
    for label, delta in PERTURBATIONS:
        wf = delta.get("wave_frequency", 0.5)
        # a real computation responds to the encounter frequency; a table does not
        series = ([_sdof_rao(w, wn=0.75 + 0.05 * wf) for w in GRID]
                  if real else list(frozen))
        payload = {"frequencies": GRID, "heave_rao": series,
                   "pitch_rao": [0.5 * v for v in series],
                   "roll_rao": [0.3 * v for v in series],
                   "rao_at_request": _sdof_rao(wf)}
        cases.append(_case(label, delta, payload))
    return {"base": "synthetic", "hull": {}, "endpoints": {"seakeeping/rao": cases}}


def test_control_frequency_sensitive_is_not_mock():
    v = adjudicate(_synthetic_record(real=True))["endpoints"]["seakeeping/rao"]
    codes = {f["code"] for f in v["findings"]}
    assert "MOCK_HARDCODED" not in codes, v["findings"]
    assert "MOCK_CONSTANT" not in codes
    locked = [k for k, d in v["series_invariance"].items()
              if d["invariant"] and "rao" in k]
    assert locked == [], locked


def test_control_limit_respecting_series_passes_exact_limits():
    v = adjudicate(_synthetic_record(real=True))["endpoints"]["seakeeping/rao"]
    assert not any("LIMIT_VIOLATION" in f["code"] for f in v["findings"]), v["findings"]
    lm = v["heave_limits"]
    assert lm["head_rao"] == pytest.approx(1.0, abs=0.15)
    assert lm["tail_rao"] < 0.25


def test_detection_hardcoded_table_is_mock():
    v = adjudicate(_synthetic_record(real=False))["endpoints"]["seakeeping/rao"]
    assert v["evidence_grade"] == "MOCK"
    assert any(f["code"] == "MOCK_HARDCODED" for f in v["findings"])


def test_series_digests_flags_only_locked_fields():
    rec = _synthetic_record(real=False)
    sd = series_digests(rec["endpoints"]["seakeeping/rao"])
    assert sd["heave_rao"]["invariant"] is True
    assert sd["heave_rao"]["n_distinct"] == 1
    rec_real = _synthetic_record(real=True)
    sd2 = series_digests(rec_real["endpoints"]["seakeeping/rao"])
    assert sd2["heave_rao"]["invariant"] is False


def test_missing_axis_is_flagged():
    frozen = list(np.linspace(0.5, 0.4, 20))
    cases = [_case(lbl, d, {"heave_rao": frozen, "pitch_rao": [0.02] * 20})
             for lbl, d in PERTURBATIONS]
    v = adjudicate({"endpoints": {"ep": cases}})["endpoints"]["ep"]
    codes = {f["code"] for f in v["findings"]}
    assert "AXIS_MISSING" in codes
    assert "MOCK_CONSTANT" in codes          # pitch frozen at 0.02


def test_tables_render_from_verdict_without_hand_typed_numbers():
    v = adjudicate(_synthetic_record(real=True))
    md = tables(v)
    assert "evidence grade" in md
    assert "REAL_UNVERIFIED" in md and "synthetic" not in md


@pytest.mark.skipif(not os.path.exists(FIXTURE), reason="live fixture not captured")
def test_frozen_fixture_reproduces_published_verdicts():
    with open(FIXTURE) as fh:
        rec = json.load(fh)
    v = adjudicate(rec)
    strip = v["endpoints"]["seakeeping-advanced/strip-theory"]
    rao = v["endpoints"]["seakeeping/rao"]
    assert strip["evidence_grade"] == "MOCK"
    assert rao["evidence_grade"] == "MOCK"
    # the endpoint labelled "strip theory" ships no frequency axis at all
    assert strip["heave_limits"]["axis_present"] is False
    assert any(f["code"] == "AXIS_MISSING" for f in strip["findings"])
    # /seakeeping/rao returns one table for both heave and pitch
    locked = [k for k, d in rao["series_invariance"].items()
              if d["invariant"] and "rao" in k]
    assert set(locked) == {"heave_rao", "pitch_rao", "roll_rao"}
    assert rao["series_invariance"]["heave_rao"]["digests"] == \
        rao["series_invariance"]["pitch_rao"]["digests"]