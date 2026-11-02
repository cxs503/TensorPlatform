"""Guards for the MMG code-to-code attribution harness (m2076).

These tests pin the *diagnosis* recorded in
``docs/audit/mmg_crosscheck_attribution.md``: the 18-32 % gap between the
digital-twin MMG model and the W3-N08 MMG model is carried by the hull
*yaw-moment family* (``N'_v``, ``N'_r``), and the naive repair (inject the
potential Munk moment) makes the hull course-unstable instead of closing the
gap.  They are cheap because they run the knockout scans at a coarse time step.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_TOOL = ROOT / "tools" / "benchmarks" / "mmg_crosscheck_attribution.py"


def _load():
    spec = importlib.util.spec_from_file_location("mmg_attrib", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # dataclasses resolve cls.__module__
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return _load()


@pytest.fixture(scope="module")
def turns(tool):
    dtt = tool.dt_turn(dt=0.4)
    dtw = tool.w3n08_turn(dt=1.0)
    return dtt, dtw


@pytest.fixture(scope="module")
def derivs(tool, turns):
    dtt, dtw = turns
    core = tool._MMGCore(tool._hull(), tool.U0)
    return tool.block_b(core, dtt, dtw)


def test_harness_reproduces_the_m1651_gap(turns):
    """The reproduction block must still show a > 20 % tactical-diameter gap;
    if it does not, the two models changed and this whole note is stale."""
    dtt, dtw = turns
    a = _load().block_a(dtt, dtw)
    tac = a["rows"][0]
    assert tac["rel_diff"] == pytest.approx(0.30, abs=0.08)
    assert a["worst_rel_diff"] > 0.2


def test_w3n08_extractor_matches_its_stated_derivatives(derivs):
    """The finite-difference extractor is validated against a model whose
    derivatives are stated in code - otherwise the DT numbers would be
    uninterpretable."""
    dw = derivs["linear_derivatives"]["w3n08"]
    table = derivs["closed_form"]["w3n08_table"]
    for key in ("Y_v", "N_v", "Y_r", "N_r"):
        assert dw[key] == pytest.approx(table[key], rel=0.10), key


def test_dt_model_has_no_drift_induced_yaw_moment(derivs):
    """Documented defect: the DT hull model's N'_v and N'_r are ~0, two orders
    below the closed-form Munk moment of its own added masses."""
    d = derivs["linear_derivatives"]["dt"]
    cf = derivs["closed_form"]
    assert abs(d["N_v"]) < 0.05
    assert abs(d["N_r"]) < 0.02
    assert abs(cf["munk_N_v_dt"]) > 0.3           # the term that is missing
    # sway force is *not* the problem: within 40 % of the W3-N08 value
    dw = derivs["linear_derivatives"]["w3n08"]
    assert abs(d["Y_v"] - dw["Y_v"]) / abs(dw["Y_v"]) < 0.40


def test_knockout_zero_injection_is_bit_identical(tool, turns):
    """The knockout core with all injections off must reproduce the shipped
    turning circle - otherwise the attribution is measuring its own artefact."""
    dtt, _ = turns
    base = tool._extra_turn("baseline", dt=0.4)
    assert base["tactical_D_L"] == pytest.approx(dtt["tactical_diameter_L"], rel=1e-6)


def test_naive_munk_patch_destabilises_the_course(tool):
    """A pure potential (Munk) injection must NOT be accepted as the repair:
    it drives the hull course-unstable and the turn does not complete."""
    lam = [tool._extra_turn(f"munk={l}", dt=0.4, munk_lambda=l) for l in (0.0, 0.5, 1.0)]
    assert lam[0]["turned_180"] is True
    assert lam[1]["turned_180"] is False
    assert lam[1]["max_heading_deg"] < 90.0
    # the trend of the drift angle is downward: the injection is *not* neutral
    assert lam[1]["tactical_D_L"] != lam[0]["tactical_D_L"]


def test_required_linear_moment_closes_the_diameter_gap(tool, turns):
    """Injecting a linear stabilizing moment N'_v (with N'_r = 0.55 N'_v)
    reproduces the W3-N08 tactical diameter to a few percent, and the response
    is monotone - the yaw-moment family is the dominant contributor."""
    _, dtw = turns
    w_tac = dtw["tactical_diameter_over_L"]
    scan = [tool._extra_turn(f"nv={nv}", dt=0.4, n_v_add=nv, n_r_add=0.55 * nv)
            for nv in (-0.05, -0.137, -0.25)]
    assert all(r["turned_180"] for r in scan)
    d_l = [r["tactical_D_L"] for r in scan]
    assert d_l[0] > d_l[1] > d_l[2]                     # monotone in N'_v
    best = min(scan, key=lambda r: abs(r["tactical_D_L"] - w_tac))
    assert abs(best["tactical_D_L"] - w_tac) / w_tac < 0.10
    # honest limit: the same injection does not fix the drift angle, so the
    # rudder/thrust side of the two models differs as well
    assert best["drift_deg"] > dtw["steady_drift_angle_deg"] + 3.0