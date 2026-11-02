"""Tests for the m1655 absolute-magnitude open-water benchmark.

What is being protected
-----------------------
``backend/services/propeller_open_water.py`` is graded REAL, but before m1655
every published criterion for it was *necessary but not sufficient* (analytic
efficiency bound, monotonicity, Froude invariance, BEM/momentum agreement).
``tools/benchmarks/open_water_absolute.py`` adds the missing leg: a
published-point-data baseline generated from the in-repo canonical Wageningen
B-series polynomial.  The tests below lock that harness down:

* the reference dataset on disk really is the evaluation of the in-repo
  polynomial (provenance cannot silently rot into hand-typed numbers),
* loading fails closed on a missing/corrupt dataset instead of returning nothing,
* the judge is *sensitive*: a deliberately perturbed reference must be scored
  FAIL, so a green ledger cannot be a rubber stamp,
* the currently measured absolute error is pinned as an explicit, visible debt
  bound (an improvement passes; a regression does not).

Run: ``python3 -m pytest tests/test_open_water_absolute_benchmark.py -q``
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO_ROOT, "backend")
BENCH_PATH = os.path.join(REPO_ROOT, "tools", "benchmarks", "open_water_absolute.py")

if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def _load_benchmark_module():
    spec = importlib.util.spec_from_file_location("_m1655_open_water_absolute", BENCH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = _load_benchmark_module()


# ---------------------------------------------------------------------------
# reference dataset provenance
# ---------------------------------------------------------------------------

class TestReferenceDataset:
    def test_committed_dataset_exists(self):
        assert os.path.isfile(bench.REFERENCE_DATA), (
            "backend/data/propulsion_benchmark_reference.json is missing; "
            "regenerate with --write-reference"
        )

    def test_case_count_matches_envelope(self):
        data = bench.load_reference_dataset()
        expected = 1
        for values in bench.ENVELOPE.values():
            expected *= len(values)
        assert data["case_count"] == expected == len(data["cases"])

    def test_committed_values_are_the_repo_polynomial(self):
        """The committed JSON must equal a fresh evaluation of the in-repo polynomial.

        This is the anti-rot guard: it makes it impossible to "fix" a benchmark
        failure by editing the JSON, and equally impossible for a transcription
        error to enter through the dataset.
        """
        committed = bench.load_reference_dataset()
        fresh = bench.build_reference_dataset()
        assert committed["case_count"] == fresh["case_count"]
        for got, want in zip(committed["cases"], fresh["cases"]):
            assert got["case_id"] == want["case_id"]
            for key in ("KT", "KQ", "eta_0"):
                assert got[key] == pytest.approx(want[key], rel=0, abs=1e-12), (
                    f"{got['case_id']}: {key} drifted from the in-repo polynomial"
                )

    def test_provenance_is_recorded(self):
        data = bench.load_reference_dataset()
        prov = data["provenance"]
        assert "Oosterveld" in prov["polynomial_source"]
        assert prov["in_repo_reference_implementation"].endswith("reference_bseries.py")
        assert len(data["generator_sha256"]) == 64

    def test_efficiency_identity_inside_dataset(self):
        """eta_0 = J KT / (2 pi KQ) must hold to machine precision in the dataset."""
        data = bench.load_reference_dataset()
        for case in data["cases"][::17]:
            if abs(case["KQ"]) < 1e-12:
                continue
            eta = case["J"] * case["KT"] / (2.0 * math.pi * case["KQ"])
            assert eta == pytest.approx(case["eta_0"], rel=0, abs=1e-12)


class TestFailClosed:
    def test_missing_dataset_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            bench.load_reference_dataset(str(tmp_path / "nope.json"))

    def test_empty_dataset_raises(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text(json.dumps({"schema_version": bench.SCHEMA_VERSION,
                                    "case_count": 0, "cases": []}))
        with pytest.raises(ValueError):
            bench.load_reference_dataset(str(path))

    def test_wrong_schema_raises(self, tmp_path):
        path = tmp_path / "odd.json"
        path.write_text(json.dumps({"schema_version": 999, "case_count": 1,
                                    "cases": [{"case_id": "x"}]}))
        with pytest.raises(ValueError):
            bench.load_reference_dataset(str(path))

    def test_missing_fields_raise(self, tmp_path):
        path = tmp_path / "partial.json"
        path.write_text(json.dumps({"schema_version": bench.SCHEMA_VERSION,
                                    "case_count": 1,
                                    "cases": [{"case_id": "x", "J": 0.5}]}))
        with pytest.raises(ValueError):
            bench.load_reference_dataset(str(path))


# ---------------------------------------------------------------------------
# judge sensitivity + measured debt bound
# ---------------------------------------------------------------------------

SMALL_ENVELOPE = {"J": [0.4, 0.6, 0.8], "P_D": [1.0, 1.2], "Ae_Ao": [0.55], "Z": [4]}


@pytest.fixture(scope="module")
def full_ledger():
    """One full-envelope run shared by the debt-bound tests (~10 s)."""
    return bench.run_benchmark()


@pytest.fixture(scope="module")
def small_ledger():
    dataset = bench.build_reference_dataset(SMALL_ENVELOPE)
    return bench.run_benchmark(dataset), dataset


class TestJudge:
    def test_ledger_structure(self, small_ledger):
        ledger, dataset = small_ledger
        assert ledger["reference_case_count"] == dataset["case_count"] == 6
        assert set(ledger["quantities"]) == {"KT", "KQ", "eta_0"}
        assert set(ledger["verdict"]) == {"KT", "KQ", "eta_0"}
        assert len(ledger["rows"]) == dataset["case_count"]
        assert 0 <= ledger["scored_case_count"] <= ledger["reference_case_count"]
        assert ledger["scored_case_count"] + ledger["excluded_case_count"] \
            == ledger["reference_case_count"]

    def test_judge_can_fail(self):
        """A 50 % inflated reference must be scored FAIL - the judge is sensitive."""
        dataset = bench.build_reference_dataset(SMALL_ENVELOPE)
        inflated = json.loads(json.dumps(dataset))
        for case in inflated["cases"]:
            case["KT"] *= 1.5
        ledger = bench.run_benchmark(inflated)
        assert ledger["verdict"]["KT"] == "FAIL"
        assert ledger["quantities"]["KT"]["n_over_accept"] > 0

    def test_judge_accepts_a_perfect_model(self):
        """Identical model and reference must pass the strict bar (no bias)."""
        dataset = bench.build_reference_dataset(SMALL_ENVELOPE)
        model = bench._load_backend_model()
        # Feed the BEM's own output back as the reference.
        for case in dataset["cases"]:
            geom = model.PropellerGeometry(diameter_m=5.0, pitch_ratio=case["P_D"],
                                          blade_area_ratio=case["Ae_Ao"],
                                          blade_count=case["Z"])
            pt = model.open_water_point(geom, case["J"])
            case["KT"], case["KQ"] = pt["KT"], pt["KQ"]
            case["eta_0"] = pt["eta0"]
        ledger = bench.run_benchmark(dataset)
        assert ledger["verdict"]["KT"] == f"PASS(<{bench.STRICT_PCT}%)"

    def test_clamp_probe_restores_section_model(self):
        model = bench._load_backend_model()
        original = model.blade_element_section
        geom = model.PropellerGeometry(diameter_m=5.0, pitch_ratio=1.0,
                                       blade_area_ratio=0.55, blade_count=4)
        with bench.ClampProbe(model) as probe:
            assert model.blade_element_section is not original
            model.open_water_point(geom, 0.4)
        assert model.blade_element_section is original
        assert probe.calls > 0
        assert 0.0 <= probe.occupancy <= 1.0


class TestMeasuredDebtBound:
    """Pins the *currently measured* absolute accuracy of the BEM model.

    These are upper bounds, not equalities: an improvement (smaller error) keeps
    passing, a regression breaks the suite.  The bound documents the honest
    state of ``propeller.open_water`` - it is materially worse than the 15 %
    acceptance line, which is why the capability grade must not be read as
    "absolutely verified".
    """

    def test_reference_data_geometry_is_physical(self):
        """Sanity of the reference itself, plus an explicit statement of its edge.

        Inside ``J <= 0.8`` the reference must be a working propeller: positive
        torque and an efficiency strictly below the actuator-disc level.  At
        ``J = 1.0`` with ``P/D = 0.8`` the polynomial has already crossed both the
        zero-thrust and the zero-torque points (windmilling), so ``KT`` and ``KQ``
        are negative there and ``eta_0`` is meaningless - exactly the points the
        relative-error floor excludes.  Asserting both halves keeps the domain
        boundary explicit instead of hiding it.
        """
        data = bench.load_reference_dataset()
        for case in data["cases"]:
            if case["J"] <= 0.8:
                assert case["KQ"] > 0.0
                assert case["KT"] > 0.0
                assert 0.0 < case["eta_0"] < 0.85
            else:
                # past the working range the reference may windmill; never silently NaN
                assert case["KT"] == case["KT"] and case["KQ"] == case["KQ"]
        windmilling = [c for c in data["cases"] if c["KQ"] <= 0.0]
        assert all(c["J"] >= 0.9 for c in windmilling)

    def test_full_envelope_absolute_error_bound(self, full_ledger):
        ledger = full_ledger
        q = ledger["quantities"]
        # Measured on the m1655 envelope: KT max 76.7 %, KQ 54.8 %, eta_0 31.3 %.
        assert q["KT"]["max_abs_pct"] <= 85.0
        assert q["KQ"]["max_abs_pct"] <= 65.0
        assert q["eta_0"]["max_abs_pct"] <= 40.0
        # ... and it is demonstrably *not* within the platform acceptance line:
        # the debt is real, so the ledger must not claim otherwise.
        assert q["KT"]["max_abs_pct"] > bench.ACCEPT_PCT
        assert q["KQ"]["max_abs_pct"] > bench.ACCEPT_PCT
        assert ledger["verdict"]["KT"] == "FAIL"

    def test_stall_branch_occupancy_is_nonzero(self, full_ledger):
        """Documents *why*: the strip model runs in its capped-lift branch."""
        ledger = full_ledger
        assert ledger["clamp_occupancy_max"] > 0.0

    def test_blade_area_sensitivity_mismatch(self):
        """The BEM's KT is ~20x more sensitive to Ae/A0 than the B-series."""
        diag = bench.structural_diagnostics()
        bem = diag["dlnKT_dlnAeAo"]["bem"]
        ref = diag["dlnKT_dlnAeAo"]["bseries"]
        assert bem > ref
        assert bem / max(ref, 1e-3) > 5.0
        assert 0.0 < bem < 1.5

# ---------------------------------------------------------------------------
# the batch wrapper must be the model, not the retired surrogate
# ---------------------------------------------------------------------------

class TestBatchWrapperIsNotTheSurrogate:
    """Falsification tests for the MOCK surrogate that shipped until m1655.

    The retired code was ``KT = max(0.05, 0.5-0.3J)``, ``KQ = max(0.01, 0.05+0.02J)``,
    ``eta0 = clip(J KT /(2 pi KQ), 0.3, 0.85)``.  Each test below attacks one of
    its signatures, so a silent regression back to it cannot pass the suite.
    """

    @staticmethod
    def _call(**kwargs):
        sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "batch_scan"))
        from batch_executor import ShipCalculationEngine
        params = {"diameter_m": 5.0, "pitch_ratio": 1.0, "blade_area_ratio": 0.55,
                  "blade_count": 4, "rpm": 120}
        params.update(kwargs)
        return ShipCalculationEngine.calc_propeller(params)

    def test_wrapper_reports_the_model_name(self):
        assert self._call(advance_speed_kn=14.0)["model"] == "bem"

    def test_torque_coefficient_falls_with_advance_ratio(self):
        """The surrogate's KQ *grew* with J (0.05 + 0.02J) - explicitly wrong."""
        kq = [self._call(advance_speed_kn=kn)["torque_coefficient"]
              for kn in (5.0, 10.0, 15.0)]
        assert kq[0] > kq[1] > kq[2] > 0.0

    def test_thrust_responds_to_pitch_ratio(self):
        """The surrogate ignored P/D entirely."""
        low = self._call(advance_speed_kn=14.0, pitch_ratio=0.8)["thrust_coefficient"]
        high = self._call(advance_speed_kn=14.0, pitch_ratio=1.2)["thrust_coefficient"]
        assert high > low * 1.2

    def test_efficiency_is_not_clipped_to_the_surrogate_plateau(self):
        """The surrogate pinned eta0 at 0.3/0.85 regardless of loading."""
        values = [self._call(advance_speed_kn=kn, pitch_ratio=1.2)["efficiency"]
                  for kn in (8.0, 10.0, 12.0, 14.0)]
        assert len({round(v, 3) for v in values}) > 1
        assert any(abs(v - 0.85) > 1e-3 for v in values)

    def test_wrapper_still_validates_geometry(self):
        with pytest.raises(ValueError):
            self._call(diameter_m=0.0)
        with pytest.raises(ValueError):
            self._call(rpm=0.0)
