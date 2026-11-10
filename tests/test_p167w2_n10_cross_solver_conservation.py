"""Tests for the P167W2-N10 cross-library conservation verification face.

Covers, with synthetic geometry only (zero domain vocabulary):

* exact convex-polytope mass properties (volume / centroid / inertia tensor) —
  the kernel every ledger depends on;
* FEM→DEM fracture hand-off: four ledgers (mass, momentum, angular momentum,
  energy) plus the full inertia tensor, the ``failure_faces.json`` artefact
  contract and its tamper detection, and the measured gap of the older
  sphere-equivalent surrogate;
* LBM→FVM flow exchange: supermesh inventory identity, interface-flux
  conservation for three plane orientations, and negative controls proving the
  gates can fail;
* clock-synchronisation semantics: conservative barrier invariants, optimistic
  speculation + rollback replay equivalence, the necessity of the rollback, and
  the quantified defect of the platform's legacy ``SyncState`` rollback.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from src.coupling.clock_sync_audit import (
    CheckpointedRollback,
    CoupledClockAudit,
    ScalarSolver,
    audit_legacy_rollback,
)
from src.coupling.fem_to_dem_exact import (
    ExactFractureTransfer,
    FractureFace,
    cube_element,
    dump_failure_faces,
    load_failure_faces,
    plane_cut_faces,
)
from src.coupling.geometry import box, intersect, split
from src.coupling.lbm_to_fvm_conservative_remap import (
    InterfaceFluxTransfer,
    SupermeshRemap,
    cross_section_area,
    cross_section_area_level_set,
    lattice_cells,
    structured_cells,
)


# --------------------------------------------------------------------------- #
# Geometry kernel
# --------------------------------------------------------------------------- #
class TestPolytopeKernel:
    def test_unit_cube_mass_properties(self):
        cube = box([0.0, 0.0, 0.0], [0.5, 0.5, 0.5])
        assert cube.volume == pytest.approx(1.0, abs=1e-15)
        assert np.allclose(cube.centroid, 0.0, atol=1e-15)
        # Solid cube of side 1: I = m/6 about a centroidal axis.
        assert np.allclose(cube.inertia(1.0), np.eye(3) / 6.0, atol=1e-15)

    def test_split_conserves_volume_and_inertia(self):
        cube = box([0.0, 0.0, 0.0], [0.5, 0.5, 0.5])
        pos, neg = split(cube, [0.13, 0.0, 0.0], [1.0, 0.3, 0.2])
        assert pos is not None and neg is not None
        assert pos.volume + neg.volume == pytest.approx(cube.volume, rel=1e-13)
        # Parallel-axis reconstruction of the parent tensor.
        total = np.zeros((3, 3))
        for part in (pos, neg):
            d = part.centroid - cube.centroid
            total += part.inertia(1.0) + part.volume * (
                float(d @ d) * np.eye(3) - np.outer(d, d))
        assert np.allclose(total, cube.inertia(1.0), atol=1e-13)

    def test_intersection_is_analytic_for_boxes(self):
        a = box([0.0, 0.0, 0.0], [0.5, 0.5, 0.5])
        b = box([0.3, 0.0, 0.0], [0.5, 0.5, 0.5])
        overlap = intersect(a, b)
        assert overlap.volume == pytest.approx(0.7, abs=1e-15)
        assert np.allclose(overlap.centroid, [0.15, 0.0, 0.0], atol=1e-15)

    def test_section_area_two_independent_routes(self):
        rng = np.random.default_rng(11)
        for _ in range(16):
            c = rng.uniform(-0.4, 0.4, 3)
            poly = box(c, rng.uniform(0.15, 0.5, 3))
            n = rng.normal(size=3)
            n /= np.linalg.norm(n)
            p = c + rng.uniform(-0.2, 0.2, 3)
            exact = cross_section_area(poly, p, n)
            approx = cross_section_area_level_set(poly, p, n)
            if max(exact, approx) < 1e-3:
                continue
            assert abs(exact - approx) / max(exact, approx) < 1e-5


# --------------------------------------------------------------------------- #
# FEM → DEM four ledgers
# --------------------------------------------------------------------------- #
class TestFEMtoDEMFourLedgers:
    def _faces(self, case: str):
        if case == "single":
            return plane_cut_faces([[0.10, 0.0, 0.0]], [[1.0, 0.0, 0.0]])
        if case == "three":
            return plane_cut_faces(
                [[0.10, 0.0, 0.0], [0.0, -0.15, 0.05], [0.05, 0.05, 0.20]],
                [[1.0, 0.25, 0.0], [0.0, 1.0, 0.3], [0.2, 0.0, 1.0]],
            )
        return plane_cut_faces(
            [[0.12, 0, 0], [-0.08, 0, 0], [0, 0.15, 0], [0, -0.1, 0], [0, 0, 0.18], [0, 0, -0.06]],
            [[1, 0.2, 0.1], [-1, 0, 0.2], [0.1, 1, 0], [0, -1, 0.1], [0.2, 0, 1], [0, 0.1, -1]],
        )

    @pytest.mark.parametrize("case", ["single", "three", "six"])
    def test_all_ledgers_close(self, case):
        element = cube_element(elastic_strain_energy=0.3)
        transfer = ExactFractureTransfer()
        frags, ledger = transfer.transfer(element, self._faces(case))
        summary = ledger.summary()
        assert len(frags) >= 2
        for key in ("mass", "momentum", "angular_momentum", "energy", "inertia", "volume"):
            assert summary[key]["residual"] <= 1e-11, (case, key, summary[key])
        assert summary["all_passed"]

    def test_mass_and_volume_telescope(self):
        element = cube_element()
        transfer = ExactFractureTransfer()
        frags, ledger = transfer.transfer(element, self._faces("three"))
        assert sum(f.mass for f in frags) == pytest.approx(element.mass, rel=1e-12)
        assert sum(f.volume for f in frags) == pytest.approx(element.polyhedron.volume, rel=1e-12)

    def test_fragments_are_anisotropic(self):
        """The point of the exact path: fragment inertia is a real tensor."""
        element = cube_element()
        frags, _ = ExactFractureTransfer().transfer(element, self._faces("three"))
        anisotropies = []
        for f in frags:
            eig = np.linalg.eigvalsh(f.inertia)
            anisotropies.append(eig.max() / max(eig.min(), 1e-300))
        assert max(anisotropies) > 1.05

    def test_energy_release_pollutes_neither_momentum_nor_spin(self):
        element = cube_element(elastic_strain_energy=0.5)
        zero_energy = cube_element(elastic_strain_energy=0.0)
        faces = self._faces("three")
        transfer = ExactFractureTransfer()
        frags_e, ledger_e = transfer.transfer(element, faces)
        frags_0, ledger_0 = transfer.transfer(zero_energy, faces)
        # Momentum and angular momentum are untouched by the energy investment.
        assert np.allclose(ledger_e.momentum_target, ledger_0.momentum_target, atol=1e-13)
        assert np.allclose(ledger_e.angular_target, ledger_0.angular_target, atol=1e-13)
        # Energy grew by exactly the released strain energy.
        assert ledger_e.energy_target - ledger_0.energy_target == pytest.approx(0.5, rel=1e-12)

    def test_failure_faces_roundtrip_and_validation(self, tmp_path):
        faces = self._faces("three")
        path = dump_failure_faces(faces, str(tmp_path / "faces.json"))
        consumed, report = load_failure_faces(path)
        assert len(consumed) == len(faces)
        assert report["all_valid"]
        assert report["max_area_rel_error"] <= 1e-12
        assert report["min_normal_alignment"] >= 1.0 - 1e-12
        payload = json.load(open(path, encoding="utf-8"))
        assert payload["schema"] == "failure_faces/v1"
        assert len(payload["faces"]) == len(faces)

    def test_tampered_artifact_is_rejected(self, tmp_path):
        faces = self._faces("three")
        tampered = [FractureFace(vertices=f.vertices, normal=f.normal, area=f.area * 1.5)
                    for f in faces]
        path = dump_failure_faces(tampered, str(tmp_path / "tampered.json"))
        _, report = load_failure_faces(path)
        assert not report["all_valid"]
        assert report["max_area_rel_error"] == pytest.approx(0.5, abs=1e-9)

    def test_sphere_surrogate_cannot_close_the_tensor_ledgers(self):
        """Regression guard: documents *why* the surrogate was replaced."""
        element = cube_element()
        faces = self._faces("three")
        transfer = ExactFractureTransfer()
        cmp = transfer.compare_with_sphere_surrogate(element, faces)
        assert cmp["legacy_is_isotropic_fragment_inertia"]
        assert cmp["legacy_full_tensor_inertia_residual"] > 0.1
        assert cmp["legacy_angular_momentum_residual"] > 0.1
        # ... while the exact transfer stays at round-off.
        _, ledger = transfer.transfer(element, faces)
        assert ledger.summary()["inertia"]["residual"] < 1e-11

    def test_partition_is_a_partition(self):
        element = cube_element()
        transfer = ExactFractureTransfer()
        cells = transfer.partition(element, self._faces("three"))
        assert len(cells) == 8
        assert sum(c.volume for c in cells) == pytest.approx(element.polyhedron.volume, rel=1e-13)


# --------------------------------------------------------------------------- #
# LBM → FVM inventory + interface flux
# --------------------------------------------------------------------------- #
def _tangential_field(cells) -> None:
    for c in cells:
        x, y, z = c.centroid
        c.density = 1.0 + 0.4 * np.sin(2.0 * np.pi * y) * np.cos(2.0 * np.pi * z)
        c.velocity = np.array([1.0 + 0.5 * np.sin(2.5 * y) * np.cos(3.0 * z),
                               0.2 * np.sin(3.0 * y), 0.0])


class TestLBMtoFVMConservation:
    def _pair(self, n=8, ratio=2):
        src = lattice_cells([0.0, 0.0, 0.0], 0.1, [n, n, n], "lbm")
        h = 0.1 * ratio
        tgt = structured_cells([0.0, 0.0, 0.0], [h, h, h], [n // ratio] * 3, "fvm")
        return src, tgt

    def test_inventory_conservation(self):
        src, tgt = self._pair()
        _tangential_field(src)
        result = SupermeshRemap().remap(src, tgt)
        assert result.mass_residual <= 1e-12
        assert result.momentum_residual <= 1e-10
        assert result.max_uncovered_fraction <= 1e-12
        assert result.summary()["passed"]

    def test_linear_field_is_reproduced_exactly(self):
        src, tgt = self._pair()
        remap = SupermeshRemap()
        remap.build(src, tgt)
        assert remap.linear_field_error(np.array([1.0, 2.0, -0.5])) <= 1e-12

    def test_gate_can_fail_on_a_shifted_patch(self):
        """Negative control: a target patch outside the source domain must fail."""
        src, tgt = self._pair(4, 2)
        _tangential_field(src)
        shifted = []
        for c in tgt:
            half = 0.5 * c.volume ** (1.0 / 3.0)
            shifted.append(type(c)(polyhedron=box(c.centroid + np.array([1.0, 0.0, 0.0]),
                                                  [half] * 3)))
        result = SupermeshRemap().remap(src, shifted)
        assert not result.summary()["passed"]
        assert result.max_uncovered_fraction > 0.9

    @pytest.mark.parametrize("point,normal", [
        ([0.4, 0.0, 0.0], [1.0, 0.0, 0.0]),     # plane on a cell face
        ([0.37, 0.0, 0.0], [1.0, 0.0, 0.0]),    # plane inside a cell
        ([0.25, 0.25, 0.25], [1.0, 1.0, 0.0]),  # oblique plane
    ])
    def test_interface_flux_conservation(self, point, normal):
        src, tgt = self._pair()
        _tangential_field(src)
        transfer = InterfaceFluxTransfer()
        exact = transfer.transfer(src, tgt, point, normal)
        baseline = transfer.transfer_by_cell_centres(src, tgt, point, normal)
        assert exact.flux_residual <= 1e-12
        assert exact.area_residual <= 1e-9
        assert exact.n_pairs > 0
        # The classical cell-centre transfer is measurably worse: the exact
        # operator would prove nothing if the baseline were also exact.
        assert baseline.flux_residual > exact.flux_residual * 1e6

    def test_interface_flux_on_a_divergent_field(self):
        """A field with non-zero u·n still conserves: this is a flux, not a sum."""
        src, tgt = self._pair(4, 2)
        for c in src:
            c.density = 1.0
            c.velocity = np.array([1.0, 0.0, 0.0])
        result = InterfaceFluxTransfer().transfer(src, tgt, [0.25, 0.0, 0.0], [1.0, 0.0, 0.0])
        assert result.flux_source > 0.0
        assert result.flux_residual <= 1e-12


# --------------------------------------------------------------------------- #
# Clock synchronisation semantics
# --------------------------------------------------------------------------- #
class TestClockSynchronisation:
    def test_conservative_invariants(self):
        audit = CoupledClockAudit().audit()
        cons = audit["conservative"]
        assert cons["max_barrier_skew"] <= 1e-12
        assert cons["future_reads"] == 0
        assert cons["global_time_monotone"] is True

    def test_optimistic_rollback_reproduces_replay(self):
        audit = CoupledClockAudit().audit()
        opt = audit["optimistic_rollback"]
        assert opt["invalidated_values"] > 0        # speculation really happened
        assert opt["rolled_back_steps"] > 0
        assert opt["deviation_from_replay"] <= 1e-12
        assert opt["max_lead"] > 0.0                # the fast solver led the slow one

    def test_rollback_is_necessary(self):
        audit = CoupledClockAudit().audit()
        naive = audit["optimistic_no_rollback"]
        assert naive["causal_violations"] > 0
        assert naive["deviation_from_replay"] > 1e-6

    def test_peek_ahead_is_detected(self):
        """Negative control: the invariant detector must be able to fire."""
        audit = CoupledClockAudit().audit()
        assert audit["negative_control_peek_ahead"]["detected"]
        assert audit["negative_control_peek_ahead"]["future_reads"] > 0

    def test_legacy_rollback_defect_is_quantified(self):
        report = audit_legacy_rollback()
        silent = report["silent_noop_case"]
        assert silent["reported_success"] is True
        assert silent["actually_moved"] is False      # claims success, does nothing
        rewind = report["rewind_case"]
        assert rewind["reported_success"] is True
        assert rewind["clock_inconsistency"] == pytest.approx(0.01, abs=1e-12)  # one dt
        assert report["fixed_path"]["restored"] is True
        assert report["fixed_path"]["state_error"] <= 1e-15

    def test_checkpointed_rollback_restores_state_exactly(self):
        solver = ScalarSolver("B", lam=1.0, dt=0.01, y0=1.0)
        ckpt = CheckpointedRollback(solver, checkpoint_every=5)
        for _ in range(8):
            solver.step(coupling=0.5)
            ckpt.maybe_checkpoint()
        for _ in range(4):
            solver.step(coupling=0.5)
        result = ckpt.rollback_to(0.08, replay=lambda s: s.step(coupling=0.5))
        assert result["restored"] is True
        assert result["discarded_steps"] == 7
        assert result["time"] == pytest.approx(0.08, abs=1e-15)
        # Reference trajectory from scratch.
        ref = ScalarSolver("B", lam=1.0, dt=0.01, y0=1.0)
        for _ in range(8):
            ref.step(coupling=0.5)
        assert result["state"] == pytest.approx(ref.y, abs=1e-15)

    def test_conservative_lag_scales_with_sample_period(self):
        deviations = []
        for sample_every in (2, 5, 10):
            audit = CoupledClockAudit(sample_every=sample_every).audit()
            deviations.append((sample_every, audit["conservative"]["deviation_from_replay"]))
        # Larger period -> larger lag -> larger deviation (monotone).
        assert deviations[0][1] < deviations[-1][1]