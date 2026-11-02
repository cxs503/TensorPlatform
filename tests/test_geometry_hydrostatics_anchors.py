"""Tests for the geometry/hydrostatics anchor suite (tools/validation/probe_geometry_hydrostatics.py).

Two layers:

1. *Synthetic* hulls with and without injected defects -- proves the anchors
   actually discriminate (a criterion that never fails is not a criterion).
2. *Recorded* adjudication -- re-adjudicates the raw bodies saved under
   ``artifacts/geometry_hydro_raw/`` and asserts the verdicts stored in
   ``artifacts/geometry_hydrostatics_verdicts.json`` are reproduced.  This runs
   fully offline and fails if either the criterion suite or the recorded evidence
   drifts.

No network access is used by any test.
"""
from __future__ import annotations

import importlib.util
import json
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MOD_PATH = os.path.join(ROOT, "tools", "validation", "probe_geometry_hydrostatics.py")
_spec = importlib.util.spec_from_file_location("probe_geom_hydro", MOD_PATH)
pg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pg)

RHO = pg.RHO


# ---------------------------------------------------------------------------
# synthetic fixtures
# ---------------------------------------------------------------------------
def _box_curves(scale_disp=1.0, scale_awp=1.0, vcb_mode="exact", quantize=False, n=11):
    """A wall-sided barge: every hydrostatic quantity has an exact closed form.

    ``quantize`` shrinks the barge to a model-scale body (~0.6 t displacement) and
    rounds the displacement to 0.1 t, reproducing the live SUBOFF artefact.
    """
    L, B = 100.0, 20.0
    T = 8.0
    if quantize:
        L, B, T = L / 30.0, B / 30.0, T / 30.0
    drafts = [T * (i + 1) / n for i in range(n)]
    awp = [L * B * scale_awp] * n
    disp = [RHO * L * B * t * scale_disp for t in drafts]
    if quantize:
        # fine curve forced onto a 0.1 t grid -> zero steps at low draft, exactly
        # the artefact seen on the live SUBOFF response
        disp = [round(d, 1) for d in disp]
    vcb = [t / 2.0 if vcb_mode == "exact" else 0.9 * t for t in drafts]
    return {
        "meta": {"L": L, "B": B, "T": T, "Cb": 1.0},
        "curves": {
            "drafts": drafts,
            "displacement": disp,
            "Cb": [d / (RHO * L * B * t) for t, d in zip(drafts, disp)],
            "Awp": awp,
            "VCB": vcb,
            "TPC": [RHO * a / 100.0 for a in awp],
            "LCB": [50.0] * n,
            "MTC": [1.0] * n,
            "wet_surface": [1.0] * n,
        },
    }


def _anchor(res, name):
    for a in res["anchors"]:
        if a["anchor"] == name:
            return a
    raise KeyError(name)


# ---------------------------------------------------------------------------
# 1. the anchors accept a physically exact hull
# ---------------------------------------------------------------------------
def test_exact_box_barge_is_real():
    res = pg.evaluate_hydro_curves(_box_curves())
    assert res["grade"] == "REAL", res["reason"]
    for a in res["anchors"]:
        assert a["pass"] is not False, f"{a['anchor']} failed on an exact hull: {a['detail']}"


def test_exact_box_barge_identities_are_machine_precision():
    res = pg.evaluate_hydro_curves(_box_curves())
    assert _anchor(res, "A4_fundamental_theorem")["rel_err_pct"] == pytest.approx(0.0, abs=1e-9)
    assert _anchor(res, "A5_wall_sided_vcb")["rel_err_pct"] == pytest.approx(0.0, abs=1e-9)
    assert _anchor(res, "A1_box_bound")["value"] == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 2. each injected defect is caught by the anchor that owns it
# ---------------------------------------------------------------------------
def test_inflated_displacement_breaks_box_bound_and_theorem():
    res = pg.evaluate_hydro_curves(_box_curves(scale_disp=1.2))
    assert res["grade"] == "MOCK"
    assert _anchor(res, "A1_box_bound")["pass"] is False
    assert _anchor(res, "A1_box_bound")["value"] == pytest.approx(1.2, rel=1e-9)
    assert _anchor(res, "A4_fundamental_theorem")["pass"] is False


def test_inflated_waterplane_breaks_theorem_and_tpc():
    res = pg.evaluate_hydro_curves(_box_curves(scale_awp=0.7))
    assert res["grade"] == "MOCK"
    assert _anchor(res, "A4_fundamental_theorem")["pass"] is False
    # TPC is recomputed by the fixture from Awp, so A6 stays consistent: the
    # discriminator must be A4, not A6 -- this asserts the anchors are not redundant.
    assert _anchor(res, "A6_tpc_identity")["pass"] is True


def test_vcb_contradiction_is_caught_by_wall_sided_anchor():
    res = pg.evaluate_hydro_curves(_box_curves(vcb_mode="wrong"))
    assert _anchor(res, "A5_wall_sided_vcb")["pass"] is False
    assert _anchor(res, "A5_wall_sided_vcb")["rel_err_pct"] == pytest.approx(80.0, abs=1e-6)


def test_quantisation_is_detected_as_zero_steps():
    """Rounding a fine curve onto a coarse decimal grid produces zero steps.

    A strictly increasing displacement curve cannot have equal consecutive
    values, so the hygiene anchor must fire -- this is the signature observed
    live on SUBOFF (displacement reported at 0.1 t resolution).
    """
    res = pg.evaluate_hydro_curves(_box_curves(quantize=True))
    a8 = _anchor(res, "A8_curve_hygiene")
    assert a8["pass"] is False, a8["detail"]
    assert a8["value"] > 0.0, "zero-step fraction must be positive"


def test_analytic_hull_without_rounding_has_no_zero_steps():
    res = pg.evaluate_hydro_curves(_box_curves())
    a8 = _anchor(res, "A8_curve_hygiene")
    assert a8["pass"] is True, a8["detail"]
    assert a8["value"] == 0.0


def test_short_payload_is_withheld_not_pass():
    res = pg.evaluate_hydro_curves({"meta": {"L": 1, "B": 1, "T": 1}, "curves": {"drafts": [1.0]}})
    assert res["grade"] == "WITHHELD"


# ---------------------------------------------------------------------------
# 3. A9: the official-form comparison endpoint
# ---------------------------------------------------------------------------
def test_zero_station_verdict_is_withheld():
    body = {"stations_checked": 0, "avg_error_pct": None, "max_error_pct": None, "results": []}
    assert pg.evaluate_verify(body, 200)["grade"] == "WITHHELD"


def test_verify_grade_tracks_the_15pct_gate():
    rows = [{"error_pct": 8.0}]
    assert pg.evaluate_verify(
        {"stations_checked": 1, "avg_error_pct": 8.0, "max_error_pct": 8.0, "results": rows}, 200
    )["grade"] == "REAL"
    assert pg.evaluate_verify(
        {"stations_checked": 1, "avg_error_pct": 141.1, "max_error_pct": 141.1, "results": rows}, 200
    )["grade"] == "MOCK"


def test_http_error_is_not_supported():
    assert pg.evaluate_verify({"detail": "not found"}, 404)["grade"] == "NOT_SUPPORTED"


# ---------------------------------------------------------------------------
# 4. recorded live evidence reproduces the stored verdicts (offline regression)
# ---------------------------------------------------------------------------
RECORDED = os.path.join(ROOT, "artifacts", "geometry_hydrostatics_verdicts.json")


@pytest.mark.skipif(not os.path.exists(RECORDED), reason="recorded verdicts absent")
def test_recorded_verdicts_are_reproducible_from_raw_bodies():
    stored = json.load(open(RECORDED))
    replay = pg.adjudicate(pg.collect_raw(tuple(stored["ships"])))
    for ship, rec in stored["ships"].items():
        assert replay["ships"][ship]["hydro_curves"]["grade"] == rec["hydro_curves"]["grade"]
        assert replay["ships"][ship]["verify_official"]["grade"] == rec["verify_official"]["grade"]
    assert replay["capability_grade"] == stored["capability_grade"]


@pytest.mark.skipif(not os.path.exists(RECORDED), reason="recorded verdicts absent")
def test_recorded_defects_are_present():
    """Pin the three canonical hulls' defects so a silent 'fix' cannot pass unnoticed."""
    stored = json.load(open(RECORDED))
    kcs = stored["ships"]["KCS"]["hydro_curves"]
    a1 = next(a for a in kcs["anchors"] if a["anchor"] == "A1_box_bound")
    assert a1["value"] > 1.0, "KCS displaced volume must exceed L*B*T (box bound)"
    assert kcs["grade"] == "MOCK"
    assert stored["ships"]["KCS"]["verify_official"]["stations_checked"] == 0