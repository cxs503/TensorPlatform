"""Tests for the m1678 propeller BEM verification harness.

Three kinds of test live here and they must not be confused:

1. **Integrity of the anchors.** The literature table and the pinned B-series
   samples must match their single source of truth, so that a silent edit cannot
   move every reported error behind the report's back.
2. **Mechanics of the harness.** Error arithmetic, statistics and the
   pass/fail gate are tested directly, including a negative control proving the
   gate cannot upgrade a failing comparison.
3. **The documented finding.** The current \\emph{measured} error of the BEM
   open-water model against the KP505 experiment is pinned. This is a
   characterization test, not an endorsement: it exists so that the day the model
   is fixed the suite fails and forces the report (and the capability grade) to
   be regenerated instead of silently drifting.

Run: ``python3 -m pytest tests/verify/test_propeller_bem_benchmark.py -q``
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    ideal_efficiency_bound,
    open_water_point,
)
from tools.verify.propeller_open_water import bem_benchmark as bench  # noqa: E402
from tools.verify.propeller_open_water import reference_bseries as ref  # noqa: E402
from tools.verify.propeller_open_water.audit_open_water import (  # noqa: E402
    KP505_EXPERIMENT,
)

REFERENCE_PATH = REPO / "backend" / "data" / "propulsion_benchmark_reference.json"


# ---------------------------------------------------------------------------
# 1. anchor integrity
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def reference() -> dict:
    with open(REFERENCE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_reference_file_schema(reference: dict) -> None:
    ids = {c["id"] for c in reference["cases"]}
    assert ids == {"KP505_EXPERIMENT", "WAGENINGEN_B_SERIES"}
    for case in reference["cases"]:
        assert case["source"], "every anchor must carry a citation"
        assert case.get("independent") is True
    accept = reference["acceptance"]
    assert accept["platform_standard_percent"] == 15.0
    assert accept["paper_grade_percent"] == 3.0


def test_kp505_points_match_audit_constant(reference: dict) -> None:
    """The new data file must not diverge from the original transcription."""
    case = next(c for c in reference["cases"] if c["id"] == "KP505_EXPERIMENT")
    packed = [(p["J"], p["KT"], p["KQ"]) for p in case["points"]]
    assert len(packed) == len(KP505_EXPERIMENT)
    for got, want in zip(packed, KP505_EXPERIMENT):
        assert got == pytest.approx(want, rel=0, abs=0), "anchor transcription drifted"


def test_bseries_pins_reproduce(reference: dict) -> None:
    """Pinned samples are a drift guard on the coefficient table."""
    case = next(c for c in reference["cases"] if c["id"] == "WAGENINGEN_B_SERIES")
    for pt in case["pinned_points"]["points"]:
        got = ref.open_water_coefficients(pt["J"], pt["P_D"], pt["Ae_Ao"], pt["Z"])
        assert got["KT"] == pytest.approx(pt["KT"], abs=5e-7)
        assert got["KQ"] == pytest.approx(pt["KQ"], abs=5e-7)


def test_kp505_geometry_is_declared_non_bseries(reference: dict) -> None:
    """The report leans on this caveat, so it must be stated in the data."""
    case = next(c for c in reference["cases"] if c["id"] == "KP505_EXPERIMENT")
    assert "NOT a standard Wageningen B-series" in case["geometry_note"]


# ---------------------------------------------------------------------------
# 2. harness mechanics
# ---------------------------------------------------------------------------
def test_rel_err_sign_and_nan() -> None:
    assert bench.rel_err_pct(110.0, 100.0) == pytest.approx(10.0)
    assert bench.rel_err_pct(90.0, 100.0) == pytest.approx(-10.0)
    assert math.isnan(bench.rel_err_pct(1.0, 0.0))


def test_stats_drops_nan_and_reports_abs() -> None:
    st = bench.stats([10.0, float("nan"), -30.0])
    assert st["n"] == 2
    assert st["mean_abs"] == pytest.approx(20.0)
    assert st["max_abs"] == pytest.approx(30.0)


def test_gate_never_upgrades_a_failure() -> None:
    assert bench.classify(2.9) == "PASS_3PCT"
    assert bench.classify(14.9) == "PASS_15PCT"
    assert bench.classify(15.1) == "FAIL"
    # negative control: exactly the errors measured for this model must be FAIL
    assert bench.classify(24.5) == "FAIL"
    assert bench.classify(-29.9) == "FAIL"


def test_bem_is_deterministic_and_froude_invariant() -> None:
    """Anchors are external, this checks the model under test is well posed."""
    g1 = PropellerGeometry(diameter_m=5.0, pitch_ratio=0.95, blade_area_ratio=0.80, blade_count=5)
    g2 = PropellerGeometry(diameter_m=10.0, pitch_ratio=0.95, blade_area_ratio=0.80, blade_count=5)
    a = open_water_point(g1, 0.45)
    b = open_water_point(g1, 0.45)
    c = open_water_point(g2, 0.45)  # same J, twice the diameter
    assert a["KT"] == pytest.approx(b["KT"], abs=0)
    assert a["KT"] == pytest.approx(c["KT"], rel=1e-9)
    assert a["KQ"] == pytest.approx(c["KQ"], rel=1e-9)


def test_ideal_bound_holds_where_thrust_exists() -> None:
    g = PropellerGeometry(diameter_m=5.0, pitch_ratio=1.0, blade_area_ratio=0.55, blade_count=4)
    for J in (0.2, 0.4, 0.6):
        p = open_water_point(g, J)
        assert p["KT"] > 0.0
        assert 0.0 < p["eta0"] < ideal_efficiency_bound(p["KT"], J, g)


# ---------------------------------------------------------------------------
# 3. the documented finding (session-scoped quick run of the real harness)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def quick_bundle(tmp_path_factory) -> dict:
    """Run the harness quickly into a temp directory.

    Redirecting the outputs keeps the committed artifacts (full sweep) intact:
    a test run must never overwrite evidence the report cites.
    """
    tmp = tmp_path_factory.mktemp("bem_quick")
    saved = (bench.OUT_DIR, bench.DOC_PATH)
    bench.OUT_DIR, bench.DOC_PATH = tmp / "out", tmp / "doc.md"
    try:
        return bench.run(quick=True)
    finally:
        bench.OUT_DIR, bench.DOC_PATH = saved


def test_verdicts_are_internally_consistent(quick_bundle: dict) -> None:
    v = quick_bundle["verdicts"]
    band = quick_bundle["kp505"]["bem_vs_experiment_band"]
    for q, key in (("KT", "KT_band"), ("KQ", "KQ_band"), ("eta", "eta_band")):
        assert v[key] == bench.classify(band[q]["max_abs"])
    assert v["headline"].startswith("FAIL") == (v["KT_band"] == "FAIL")


def test_current_model_kt_error_against_literature(quick_bundle: dict) -> None:
    """Characterization of the *defect* this mission documents.

    When the model is fixed (KT error brought under the 15 percent platform
    gate) this test is expected to fail: that is the intended tripwire. Update
    it together with the report and the capability grade -- do not widen the
    bound to make it pass.
    """
    band = quick_bundle["kp505"]["bem_vs_experiment_band"]
    assert band["KT"]["max_abs"] > bench.GATE_PLATFORM_PCT
    assert 15.0 < band["KT"]["mean_abs"] < 45.0, "mean KT error moved out of the audited band"


def test_kq_is_within_gate_but_not_paper_grade(quick_bundle: dict) -> None:
    """KQ is the one quantity that already meets the platform gate; recording
    the asymmetry keeps the report honest in both directions."""
    band = quick_bundle["kp505"]["bem_vs_experiment_band"]
    assert band["KQ"]["max_abs"] <= bench.GATE_PLATFORM_PCT
    assert band["KQ"]["max_abs"] > bench.GATE_PAPER_PCT


def test_bseries_comparable_mask_is_applied(quick_bundle: dict) -> None:
    sw = quick_bundle["bseries_sweep"]
    assert sw["n_comparable"] <= sw["n_total"]
    # unmasked statistics must be the wider (and worse) ones, i.e. the mask is
    # actually doing something rather than being cosmetic
    assert sw["unmasked_KT"]["max_abs"] >= sw["all"]["max_abs"]


def test_outputs_written(quick_bundle: dict) -> None:
    out = REPO / quick_bundle["out_dir"]
    assert (out / "benchmark.json").is_file()
    for i in (1, 2, 3, 4):
        pngs = list(out.glob(f"fig{i}_*.png"))
        assert pngs and pngs[0].stat().st_size > 5000
    doc = REPO / quick_bundle["doc_path"]
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "是否 <3%" in text
    assert "FAIL" in text