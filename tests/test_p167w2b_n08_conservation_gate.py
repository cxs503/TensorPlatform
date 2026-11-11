"""P167W2B-N08 — cross-library field-exchange conservation gate.

The tests are written as *discrimination* tests, not smoke tests: for every
admissibility statement the gate makes, there is a negative case that must be
rejected for the expected reason, and for every conservation face there is a
shortcut operator that must fail the same criterion (otherwise the criterion
proves nothing).

Coverage
--------
1. ledger sealing / tamper detection / raw-value residuals / schema errors;
2. gate policy validation, fail-closed behaviour, provenance requirement;
3. seventeen negative controls + one positive control, code-specific;
4. FEM→DEM: four ledgers + inertia tensor under five fracture cases; exact
   rational re-evaluation (ℚ) proving the float residual is round-off; the
   sphere-equivalent surrogate fails the inertia ledger structurally;
5. LBM→FVM: inventory + interface flux; the inverse-distance shortcut fails the
   1e-6 flux criterion on the oblique plane;
6. clock: barrier over 100 LCM windows (zero skew, no stall, monotone) with the
   deadlock / future-read detectors validated by negative controls; optimistic
   rollback rate = latency/period identity, replay equality, drift without
   rollback (rollback is load-bearing);
7. zero-domain-semantics scan of the delivered package.
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.coupling.conservation_gate import (  # noqa: E402
    BOUND,
    ConservationGate,
    ConservationLedger,
    Criterion,
    GatePolicy,
    LedgerEntry,
    MUTATIONS,
    barrier_run,
    bundle_digest,
    clock_ledger,
    cross_library_policy,
    fem_dem_ledger,
    lbm_fvm_ledger,
    optimistic_run,
    rational_ledger_residuals,
    rel_residual,
    run_controls,
    run_mutator_substitution,
    sphere_surrogate_tensors,
)
from src.coupling.conservation_gate.controls import clone  # noqa: E402
from src.coupling.fem_to_dem_exact import (  # noqa: E402
    ExactFractureTransfer,
    cube_element,
    plane_cut_faces,
)
from src.coupling.lbm_to_fvm_conservative_remap import (  # noqa: E402
    lattice_cells,
    shear_field,
    structured_cells,
)

PKG = ROOT / "src" / "coupling" / "conservation_gate"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def element():
    return cube_element((0.0, 0.0, 0.0), half=0.5, density=2.0,
                        velocity=(1.0, 0.5, 0.0),
                        angular_velocity=(0.2, -0.4, 0.6),
                        elastic_strain_energy=0.3)


@pytest.fixture(scope="module")
def faces_single():
    return plane_cut_faces([(0.1, 0.0, 0.0)], [(1.0, 0.0, 0.0)])


@pytest.fixture(scope="module")
def faces_three():
    return plane_cut_faces([(0.1, 0.0, 0.0), (0.0, -0.05, 0.0), (0.0, 0.0, 0.2)],
                           [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)])


@pytest.fixture(scope="module")
def faces_dyadic():
    # quarter planes: every cut coordinate is exactly representable in binary
    return plane_cut_faces([(0.25, 0.0, 0.0), (0.0, 0.5, 0.0), (0.0, 0.0, 0.75)],
                           [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)])


@pytest.fixture(scope="module")
def flow_cells():
    source = lattice_cells((0.0, 0.0, 0.0), 0.1, (8, 8, 8), "src")
    target = structured_cells((0.0, 0.0, 0.0), (0.2, 0.2, 0.2), (4, 4, 4), "tgt")
    shear_field(source)
    shear_field(target)
    return source, target


@pytest.fixture(scope="module")
def bundle(element, faces_single, faces_three, flow_cells):
    source, target = flow_cells
    fem, _ = fem_dem_ledger(element, faces_three)
    lbm, _ = lbm_fvm_ledger(source, target, (0.4, 0.4, 0.4),
                            tuple(np.array([1.0, 1.0, 0.0]) / math.sqrt(2.0)))
    clk, _ = clock_ledger(barrier_run([10, 3], n_windows=100),
                          optimistic_run(periods=200, latency_steps=(0, 1, 2, 3, 4)))
    return [fem, lbm, clk]


# --------------------------------------------------------------------------- #
# 1. ledger document
# --------------------------------------------------------------------------- #
def test_ledger_seal_roundtrip_and_tamper_detection():
    entry = LedgerEntry("mass", "scalar", 1.0, 1.0, tolerance=1e-12)
    lg = ConservationLedger("face_a", [entry], producer={"name": "g", "version": "1"},
                            inputs=["abc"])
    seal = lg.seal_ledger()
    assert lg.verify_seal()
    restored = ConservationLedger.from_dict(json.loads(json.dumps(lg.as_dict())))
    assert restored.seal == seal and restored.verify_seal()
    restored.entries[0].target = 1.01
    assert not restored.verify_seal(), "edited payload must break the seal"


def test_rel_residual_is_computed_from_raw_values():
    assert rel_residual("scalar", 2.0, 2.0) == 0.0
    assert rel_residual("scalar", 0.0, 0.0) == 0.0      # balanced zero is zero
    # symmetric relative difference: |s-t| / ((|s|+|t|)/2), bounded by 2
    assert rel_residual("scalar", 1.0, 0.0) == 2.0
    assert rel_residual("scalar", 1.0, 0.5) == pytest.approx(2.0 / 3.0)
    assert rel_residual("vector", [1.0, 0.0, 0.0], [1.0, 0.0, 1e-9]) > 0.0
    assert rel_residual("flag", True, True) == 0.0
    assert rel_residual("flag", True, False) == 1.0
    with pytest.raises(ValueError):
        LedgerEntry("bad", "quaternion", 1.0, 1.0)
    with pytest.raises(ValueError):
        rel_residual("vector", [1.0, 0.0, 0.0], [1.0, 0.0])


def test_bundle_digest_is_order_independent(bundle):
    assert bundle_digest(bundle) == bundle_digest(list(reversed(bundle)))


# --------------------------------------------------------------------------- #
# 2. policy / fail-closed structure
# --------------------------------------------------------------------------- #
def test_policy_without_criteria_is_rejected():
    with pytest.raises(ValueError):
        GatePolicy(name="empty", criteria=())


def test_gate_rejects_empty_bundle_and_missing_provenance(bundle):
    gate = ConservationGate(cross_library_policy())
    assert not gate.adjudicate([]).accepted
    assert "NO_LEDGER" in gate.adjudicate([]).codes()
    stripped = clone(bundle)
    for lg in stripped:
        lg.producer = {}
        lg.seal_ledger()
    codes = gate.adjudicate(stripped).codes()
    assert "MISSING_PROVENANCE" in codes


def test_gate_ignores_document_tolerance(bundle):
    """A producer cannot relax its way in: the policy owns the tolerance."""
    gate = ConservationGate(cross_library_policy())
    cand = clone(bundle)
    fem = cand[0]
    for e in fem.entries:
        e.tolerance = 1.0
    fem.entry("momentum").target = [v * 1.02 for v in fem.entry("momentum").target]
    fem.seal_ledger()
    verdict = gate.adjudicate(cand)
    assert not verdict.accepted
    assert "BALANCE_VIOLATION" in verdict.codes()


def test_verdict_is_sealed_and_reproducible(bundle):
    gate = ConservationGate(cross_library_policy())
    v1, v2 = gate.adjudicate(bundle), gate.adjudicate(clone(bundle))
    assert v1.accepted and v2.accepted
    assert v1.seal == v2.seal
    assert v1.as_dict()["bundle_sha256"] == v2.as_dict()["bundle_sha256"]


# --------------------------------------------------------------------------- #
# 3. positive + negative controls
# --------------------------------------------------------------------------- #
def test_positive_control_accepts_the_sealed_bundle(bundle):
    gate = ConservationGate(cross_library_policy())
    verdict = gate.adjudicate(bundle)
    assert verdict.accepted, verdict.failures
    assert verdict.rows and all(r["status"] == "PASS" for r in verdict.rows)


def test_control_battery_shape():
    codes = [m[2] for m in MUTATIONS]
    assert len(MUTATIONS) >= 17
    assert len(set(m[0] for m in MUTATIONS)) == len(MUTATIONS), "duplicate control names"
    assert {"BALANCE_VIOLATION", "SEAL_MISMATCH", "NONFINITE", "MISSING_ENTRY",
            "BOUND_VIOLATION", "FLAG_FALSE", "DUPLICATE_ENTRY"} <= set(codes)


@pytest.mark.parametrize("name", [m[0] for m in MUTATIONS])
def test_each_negative_control_is_rejected_with_expected_code(bundle, name):
    gate = ConservationGate(cross_library_policy())
    result = run_controls(bundle, gate=gate)
    assert result["positive_accepted"], "positive control must stay admissible"
    row = next(r for r in result["controls"] if r["name"] == name)
    assert row["rejected"], f"mutation {name!r} was accepted (gate has no teeth)"
    assert row["code_matched"], f"{name!r}: expected {row['expected_code']}, " \
                                f"got {row['observed_codes']}"


def test_all_controls_pass_together(bundle):
    result = run_controls(bundle, gate=ConservationGate(cross_library_policy()))
    assert result["all_ok"]
    assert result["false_accept_rate"] == 0.0
    assert result["specificity"] == 1.0
    assert result["n_rejected_with_expected_code"] == result["n_controls"]


def test_substituting_the_real_shortcut_numbers_is_rejected(bundle, element, faces_single,
                                                            flow_cells):
    """Not a scaled fake: the surrogate model's *own* output must be refused."""
    gate = ConservationGate(cross_library_policy())
    surrogate = sphere_surrogate_tensors(element, faces_single)
    res = run_mutator_substitution(bundle, value=surrogate["inertia_tensor"],
                                   face="fem_dem", entry="inertia_tensor", gate=gate)
    assert res["rejected"] and res["code_matched"], res

    source, target = flow_cells
    normal = tuple(np.array([1.0, 1.0, 0.0]) / math.sqrt(2.0))
    _, base = lbm_fvm_ledger(source, target, (0.4, 0.4, 0.4), normal, baseline=True)
    assert base["flux_residual"] > 1e-6, "the shortcut must actually fail the criterion"
    lbm = next(lg for lg in bundle if lg.face == "lbm_fvm")
    scaled = lbm.entry("interface_mass_flux").source * (1.0 + base["flux_residual"])
    res2 = run_mutator_substitution(bundle, value=scaled, face="lbm_fvm",
                                    entry="interface_mass_flux", gate=gate)
    assert res2["rejected"] and res2["code_matched"], res2


# --------------------------------------------------------------------------- #
# 4. fracture hand-off
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("case", ["single", "three"])
def test_four_ledgers_close_to_round_off(element, faces_single, faces_three, case):
    faces = faces_single if case == "single" else faces_three
    ledger, detail = fem_dem_ledger(element, faces)
    assert ledger.face == "fem_dem" and ledger.verify_seal()
    for name, tol in (("mass", 1e-12), ("momentum", 1e-10), ("angular_momentum", 1e-10),
                      ("energy", 1e-10), ("inertia_tensor", 1e-10), ("volume", 1e-12)):
        assert detail["residuals"][name] < tol, (name, detail["residuals"])
    assert all(detail["fragment_inertia_spd"])
    assert abs(sum(detail["fragment_masses"]) - element.mass) <= 1e-12 * element.mass


def test_fragment_count_and_partition_identity(element, faces_three):
    frags, _ = ExactFractureTransfer().transfer(element, faces_three)
    assert len(frags) == 8
    assert abs(sum(f.polyhedron.volume for f in frags) - element.polyhedron.volume) < 1e-12


def test_rational_ledger_proves_float_residual_is_round_off(element, faces_dyadic):
    """ℚ arithmetic on the same coordinates must close the ledgers exactly."""
    frags, _ = ExactFractureTransfer().transfer(element, faces_dyadic)
    rational = rational_ledger_residuals(element, frags)
    assert all(rational["exact_zero"].values()), rational["exact_zero"]
    ledger, detail = fem_dem_ledger(element, faces_dyadic)
    # the float path is no worse than round-off for the same identity
    assert max(detail["residuals"][k] for k in ("mass", "momentum", "inertia_tensor")) < 1e-12


def test_sphere_surrogate_fails_inertia_ledger_structurally(element, faces_single):
    from src.coupling.fem_to_dem_exact import ExactFractureTransfer as _T

    cmp = _T().compare_with_sphere_surrogate(element, faces_single)
    assert cmp["legacy_is_isotropic_fragment_inertia"] is True
    assert cmp["legacy_full_tensor_inertia_residual"] > 1e-2
    assert cmp["legacy_angular_momentum_residual"] > 1e-2
    tensors = sphere_surrogate_tensors(element, faces_single)
    assert tensors["isotropic"] is True


# --------------------------------------------------------------------------- #
# 5. flow hand-off
# --------------------------------------------------------------------------- #
def test_inventory_and_flux_are_conservative(flow_cells):
    source, target = flow_cells
    normal = tuple(np.array([1.0, 1.0, 0.0]) / math.sqrt(2.0))
    ledger, detail = lbm_fvm_ledger(source, target, (0.4, 0.4, 0.4), normal)
    assert detail["residuals"]["mass_inventory"] < 1e-12
    assert detail["residuals"]["momentum_inventory"] < 1e-10
    assert detail["residuals"]["interface_mass_flux"] < 1e-6
    assert detail["coverage"] > 1.0 - 1e-12


def test_uniform_field_case_is_exact():
    """The mission's minimum case: uniform rho/u.  Everything must be an identity."""
    # local cells: never mutate the shared fixture (FieldCell fields are mutable)
    source = lattice_cells((0.0, 0.0, 0.0), 0.1, (8, 8, 8), "u_src")
    target = structured_cells((0.0, 0.0, 0.0), (0.2, 0.2, 0.2), (4, 4, 4), "u_tgt")
    for c in list(source) + list(target):
        c.density = 1.0
        c.velocity = np.array([1.0, 0.0, 0.0])
    normal = tuple(np.array([1.0, 1.0, 0.0]) / math.sqrt(2.0))
    _, detail = lbm_fvm_ledger(source, target, (0.4, 0.4, 0.4), normal)
    assert detail["residuals"]["mass_inventory"] == 0.0
    assert detail["residuals"]["interface_mass_flux"] < 1e-15
    assert detail["area_residual"] < 1e-15


def test_inverse_distance_shortcut_violates_the_flux_criterion(flow_cells):
    source, target = flow_cells
    normal = tuple(np.array([1.0, 1.0, 0.0]) / math.sqrt(2.0))
    _, exact = lbm_fvm_ledger(source, target, (0.4, 0.4, 0.4), normal)
    _, base = lbm_fvm_ledger(source, target, (0.4, 0.4, 0.4), normal, baseline=True)
    assert base["flux_residual"] > 1e-6 > exact["flux_residual"]
    assert base["flux_residual"] / max(exact["flux_residual"], 1e-300) > 1e6


# --------------------------------------------------------------------------- #
# 6. clock semantics
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("dts", [[10, 10], [10, 3], [7, 13], [1, 10], [10, 3, 7]])
def test_conservative_barrier_100_windows_no_deadlock(dts):
    rep = barrier_run(dts, n_windows=100)
    assert rep.deadlock is False and rep.stalls == 0
    assert rep.max_skew == 0.0                    # integer ticks, exact commit
    assert rep.future_reads == 0
    assert rep.monotone is True
    assert all(rep.committed.values())            # everyone reached 100 windows


def test_deadlock_detector_has_teeth():
    """A window shorter than a solver's step must be reported, not hidden."""
    bad = barrier_run([10, 7], n_windows=100, window_override=10)
    assert bad.deadlock is True and bad.stalls > 0
    assert bad.max_skew > 0.0
    # ... while the same ratios with the correct LCM window are clean
    good = barrier_run([10, 7], n_windows=100)
    assert good.deadlock is False and good.stalls == 0


def test_future_read_detector_has_teeth():
    peek = barrier_run([10, 3], n_windows=100, peek=True)
    assert peek.future_reads > 0
    assert barrier_run([10, 3], n_windows=100).future_reads == 0


def test_optimistic_rollback_rate_identity_and_replay_equality():
    # 5 steps of a 50-step period -> 5 %; 2 steps -> 4 %
    for latency, expected in (((2,), 0.04), ((5,), 0.10)):
        rep = optimistic_run(periods=20, latency_steps=latency)
        assert rep.rollback_rate == pytest.approx(expected, abs=1e-12)
        assert rep.replay_error == 0.0, "rollback must leave no speculative trace"


def test_optimistic_operating_point_is_below_five_percent():
    rep = optimistic_run(periods=200, latency_steps=(0, 1, 2, 3, 4))
    assert rep.rollback_rate < 0.05
    assert rep.replay_error < 1e-9
    # and the rollback is load-bearing: without it the same run drifts
    assert rep.no_rollback_error > 1e-4


def test_rollback_rate_grows_with_latency_and_the_gate_rejects_it(bundle):
    slow = optimistic_run(periods=200, latency_steps=tuple(range(11)))
    assert slow.rollback_rate > 0.05
    cand = clone(bundle)
    clk = next(lg for lg in cand if lg.face == "clock")
    clk.entry("optimistic_rollback_rate").source = slow.rollback_rate
    clk.seal_ledger()
    verdict = ConservationGate(cross_library_policy()).adjudicate(cand)
    assert not verdict.accepted
    assert "BOUND_VIOLATION" in verdict.codes()


# --------------------------------------------------------------------------- #
# 7. zero domain semantics
# --------------------------------------------------------------------------- #
BANNED = ["propeller", "vessel", "ship", "rudder", "keel", "ice", "wigley", "kcs",
          "kvlcc", "froude", "deadrise", "bulbous", "seakeeping", "cavitation"]


def test_package_carries_no_domain_semantics():
    offenders = []
    for path in sorted(PKG.glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for token in BANNED:
            if re.search(rf"\b{re.escape(token)}\b", text):
                offenders.append((path.name, token))
    assert not offenders, offenders