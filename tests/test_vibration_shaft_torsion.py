"""Verification suite for the shafting torsional-vibration engine (mission m1694).

Evidence-grade rationale
------------------------
``振动噪声`` had *no* computational capability on the platform before this
mission (no engine in ``backend/services/``, no entry in the 21-capability
audit, no report in the console).  The engine added in
``backend/services/shaft_torsion.py`` claims REAL grade, so it must be pinned to
analytic benchmarks rather than to its own output:

1. ``TestTwoInertiaExact`` -- a two-inertia chain has exactly one flexible mode,
   ``f1 = sqrt(k (J1+J2)/(J1 J2)) / 2 pi``; the eigensolver must hit it.
2. ``TestUniformRodLimit`` -- a free-free uniform rod has the exact spectrum
   ``f_n = n c / (2 L)``, ``c = sqrt(G/rho)``; the lumped chain must converge onto
   it at second order in the station spacing.
3. ``TestHolzerCrossCheck`` -- the Holzer tabulation shares no code with the
   eigensolver; their frequencies must agree, which is what makes the pair an
   independent cross-check instead of one method counted twice.
4. ``TestForcedResponseSDOF`` -- for a two-inertia chain driven by a
   self-equilibrated torque pair the modal superposition must reproduce the
   textbook single-DOF amplification ``1/sqrt((1-r^2)^2 + (2 zeta r)^2)``.
5. ``TestInvariants`` -- rigid-body mode at 0 Hz, mass orthonormality, nodal
   counts of a uniform rod, ``omega ∝ sqrt(k/J)`` scaling.
6. ``TestCaseStudyAndDetuning`` -- resonance rpm arithmetic, detuning verdict and
   the reported case study of a direct-drive diesel plant (the numbers quoted in
   the mission report).
7. ``TestApiEndpoint`` -- ``POST /calculations/vibration/shaft_torsion`` wiring,
   including the 400 path for an inconsistent chain.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

# ``api.calculations`` transitively builds the SQLAlchemy engine; the test host
# has no psycopg2, so pin a SQLite URL *before* any platform import happens.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from services.shaft_torsion import (  # noqa: E402
    ChainError,
    TorsionalChain,
    detuning_margin,
    evaluate_chain,
    forced_response,
    forced_response_stress,
    holzer_frequencies,
    holzer_residual,
    matrices,
    mode_shapes,
    natural_frequencies,
    nodal_stations,
    resonance_table,
    two_inertia_frequency,
    uniform_shaft_analytic_frequencies,
    uniform_shaft_chain,
)

G_STEEL = 79.3e9       # Pa, shear modulus of steel
RHO_STEEL = 7850.0     # kg/m^3


# ---------------------------------------------------------------------------
# 1. two-inertia exact
# ---------------------------------------------------------------------------

class TestTwoInertiaExact:
    @pytest.mark.parametrize(
        "j1,j2,k",
        [(1200.0, 3000.0, 2.4e7), (500.0, 500.0, 1.0e6), (15000.0, 1400.0, 8.0e7)],
    )
    def test_single_flexible_mode(self, j1, j2, k):
        chain = TorsionalChain((j1, j2), (k,))
        f = natural_frequencies(chain)
        assert f[0] == pytest.approx(0.0, abs=1e-9)
        exact = two_inertia_frequency(j1, j2, k)
        assert f[1] == pytest.approx(exact, rel=1e-12)

    def test_series_combination_interpretation(self):
        """The two-inertia frequency is that of the series stiffness acting on
        the reduced inertia ``J1 J2/(J1+J2)`` -- a second, independent formula."""
        j1, j2, k = 800.0, 2600.0, 1.5e7
        reduced = j1 * j2 / (j1 + j2)
        f_alt = math.sqrt(k / reduced) / (2.0 * math.pi)
        chain = TorsionalChain((j1, j2), (k,))
        assert natural_frequencies(chain, include_rigid=False)[0] == pytest.approx(
            f_alt, rel=1e-12
        )


# ---------------------------------------------------------------------------
# 2. uniform rod limit
# ---------------------------------------------------------------------------

class TestUniformRodLimit:
    length, diameter = 30.0, 0.40

    def _chain(self, n):
        return uniform_shaft_chain(n, self.length, self.diameter, G_STEEL, RHO_STEEL)

    def test_wave_speed_spectrum(self):
        ana = uniform_shaft_analytic_frequencies(3, self.length, G_STEEL, RHO_STEEL)
        c = math.sqrt(G_STEEL / RHO_STEEL)
        assert ana[0] == pytest.approx(c / (2 * self.length), rel=1e-15)
        assert ana[2] / ana[0] == pytest.approx(3.0, rel=1e-15)

    @pytest.mark.parametrize("n_stations,bar", [(21, 1.0e-2), (41, 3.0e-3), (81, 1.0e-3)])
    def test_converges_onto_analytic(self, n_stations, bar):
        ana = uniform_shaft_analytic_frequencies(3, self.length, G_STEEL, RHO_STEEL)
        num = natural_frequencies(self._chain(n_stations), include_rigid=False)[:3]
        assert np.allclose(num, ana, rtol=bar), (num, ana)

    def test_second_order_convergence(self):
        """Halving the spacing must quarter the error (O(h^2) lumping)."""
        ana = uniform_shaft_analytic_frequencies(2, self.length, G_STEEL, RHO_STEEL)
        e21 = abs(natural_frequencies(self._chain(21), include_rigid=False)[0] - ana[0])
        e41 = abs(natural_frequencies(self._chain(41), include_rigid=False)[0] - ana[0])
        e81 = abs(natural_frequencies(self._chain(81), include_rigid=False)[0] - ana[0])
        assert e21 > e41 > e81
        assert e41 / e81 == pytest.approx(4.0, rel=0.15)
        assert e21 / e41 == pytest.approx(4.0, rel=0.15)

    def test_total_inertia_conserved(self):
        chain = self._chain(17)
        jp = math.pi * self.diameter ** 4 / 32.0
        assert chain.total_inertia == pytest.approx(RHO_STEEL * jp * self.length, rel=1e-12)


# ---------------------------------------------------------------------------
# 3. Holzer cross-check
# ---------------------------------------------------------------------------

class TestHolzerCrossCheck:
    def test_residual_vanishes_at_eigenfrequency(self):
        chain = TorsionalChain((1200.0, 900.0, 3000.0), (2.4e7, 6.0e7))
        f1 = natural_frequencies(chain, include_rigid=False)[0]
        assert abs(holzer_residual(chain, 2 * math.pi * f1)) < 1e-6 * 2.4e7

    def test_holzer_reproduces_eigensolver(self):
        chain = TorsionalChain((1200.0, 900.0, 3000.0), (2.4e7, 6.0e7))
        f_eig = natural_frequencies(chain, include_rigid=False)
        f_hol = holzer_frequencies(chain, n_modes=len(f_eig))
        assert f_hol.size == f_eig.size
        assert np.allclose(f_hol, f_eig, rtol=1e-8)

    def test_holzer_on_uniform_rod(self):
        chain = uniform_shaft_chain(21, 30.0, 0.40, G_STEEL, RHO_STEEL)
        f_eig = natural_frequencies(chain, include_rigid=False)
        f_hol = holzer_frequencies(chain, n_modes=3)
        assert np.allclose(f_hol, f_eig[:3], rtol=1e-8)

    def test_rigid_body_is_not_a_holzer_root(self):
        """Holzer's residual is ``omega^2 * sum(J theta)``; at ``omega -> 0`` it is
        strictly positive, so no spurious 0 Hz root appears in the flexible list."""
        chain = uniform_shaft_chain(11, 30.0, 0.40, G_STEEL, RHO_STEEL)
        assert holzer_residual(chain, 1e-9) > 0.0
        assert holzer_frequencies(chain, n_modes=1)[0] > 1.0


# ---------------------------------------------------------------------------
# 4. forced response vs single-DOF analytic
# ---------------------------------------------------------------------------

class TestForcedResponseSDOF:
    @pytest.mark.parametrize("zeta", [0.005, 0.02, 0.05])
    @pytest.mark.parametrize("ratio", [0.4, 0.8, 1.0, 1.6])
    def test_amplification_matches_closed_form(self, zeta, ratio):
        j1, j2, k = 1200.0, 3000.0, 2.4e7
        chain = TorsionalChain((j1, j2), (k,), damping_ratio=zeta)
        f1 = natural_frequencies(chain, include_rigid=False)[0]
        omega = 2 * math.pi * f1 * ratio
        q = 1.0e4
        res = forced_response(chain, omega, (q, -q))
        twist = abs(res["theta_complex"][0] - res["theta_complex"][1])
        amp = 1.0 / math.sqrt((1 - ratio ** 2) ** 2 + (2 * zeta * ratio) ** 2)
        assert twist == pytest.approx(q / k * amp, rel=1e-10)

    def test_net_torque_is_rejected(self):
        chain = TorsionalChain((1200.0, 3000.0), (2.4e7,))
        with pytest.raises(ChainError, match="self-equilibrated"):
            forced_response(chain, 10.0, (1.0, 0.0))

    def test_static_limit(self):
        """``omega -> 0`` must return the static twist ``Q/k``."""
        j1, j2, k = 1200.0, 3000.0, 2.4e7
        chain = TorsionalChain((j1, j2), (k,), damping_ratio=0.02)
        res = forced_response(chain, 1e-6, (1.0e4, -1.0e4))
        twist = abs(res["theta_complex"][0] - res["theta_complex"][1])
        assert twist == pytest.approx(1.0e4 / k, rel=1e-9)
        assert abs(res["torque_complex"][0]) == pytest.approx(1.0e4, rel=1e-9)

    def test_stress_payload(self):
        j1, j2, k = 1200.0, 3000.0, 2.4e7
        chain = TorsionalChain((j1, j2), (k,), damping_ratio=0.02)
        d, wt = 0.40, math.pi * 0.40 ** 3 / 16.0
        res = forced_response_stress(
            chain, 1e-3, (1.0e5, -1.0e5), (wt,), stress_concentration_factor=1.6,
            fatigue_strength_pa=1.0e8,
        )
        assert res["tau_amplitude_pa"][0] == pytest.approx(1.6 * 1.0e5 / wt, rel=1e-9)
        assert res["verdict"] in {"acceptable", "exceeds"}
        assert float(np.max(res["stress_ratio"])) == pytest.approx(
            1.6 * 1.0e5 / wt / 1.0e8, rel=1e-9
        )


# ---------------------------------------------------------------------------
# 5. invariants
# ---------------------------------------------------------------------------

class TestInvariants:
    def test_mass_orthonormality(self):
        chain = uniform_shaft_chain(15, 20.0, 0.30, G_STEEL, RHO_STEEL)
        phi = mode_shapes(chain)
        J, _ = matrices(chain)
        gram = phi.T @ J @ phi
        assert np.allclose(gram, np.eye(gram.shape[0]), atol=1e-10)

    def test_stiffness_scaling_law(self):
        base = TorsionalChain((1000.0, 2000.0, 500.0), (2.0e7, 1.0e7))
        scaled = TorsionalChain((1000.0, 2000.0, 500.0), (8.0e7, 4.0e7))
        f1 = natural_frequencies(base, include_rigid=False)
        f2 = natural_frequencies(scaled, include_rigid=False)
        # omega ~ sqrt(k/J): 4x stiffness -> 2x frequency
        assert np.allclose(f2, 2.0 * f1, rtol=1e-12)

    def test_nodal_counts_of_uniform_rod(self):
        """Mode n of a free-free rod must contain exactly n interior nodes."""
        chain = uniform_shaft_chain(41, 30.0, 0.40, G_STEEL, RHO_STEEL)
        for mode in (1, 2, 3):
            assert len(nodal_stations(chain, mode)) == mode

    def test_invalid_chains_rejected(self):
        with pytest.raises(ChainError):
            TorsionalChain((1000.0,), (1.0e7,))
        with pytest.raises(ChainError):
            TorsionalChain((1000.0, 2000.0), (1.0e7, 2.0e7))
        with pytest.raises(ChainError):
            TorsionalChain((1000.0, 0.0), (1.0e7,))
        with pytest.raises(ChainError):
            TorsionalChain((1000.0, 2000.0), (-1.0e7,))
        with pytest.raises(ChainError):
            TorsionalChain((1000.0, 2000.0), (1.0e7,), damping_ratio=1.5)


# ---------------------------------------------------------------------------
# 6. case study, resonance screening, detuning
# ---------------------------------------------------------------------------

def _case_chain() -> TorsionalChain:
    """Direct-drive 6-cylinder two-stroke plant used in the mission report.

    propeller (with entrained water) - intermediate shaft - engine rotating mass.
    """
    j_prop = 4800.0       # kg m^2, propeller + entrained water
    j_int = 700.0         # kg m^2, intermediate shafting
    j_eng = 12000.0       # kg m^2, engine rotating parts + flywheel
    k_tail = 1.55e7       # N m/rad, tail shaft (d=0.50 m, L=14 m)
    k_int = 3.10e7        # N m/rad, intermediate shaft
    return TorsionalChain((j_prop, j_int, j_eng), (k_tail, k_int), damping_ratio=0.02)


class TestCaseStudyAndDetuning:
    def test_resonance_rpm_arithmetic(self):
        chain = _case_chain()
        f = natural_frequencies(chain, include_rigid=False)
        table = resonance_table(chain, orders=(4.0,), rpm_range=(0.0, 400.0), max_mode=1)
        assert table, "blade-rate crossing must fall in the scanned band"
        for row in table:
            assert row["rpm"] == pytest.approx(60.0 * row["frequency_hz"] / 4.0, rel=1e-12)
            assert row["frequency_hz"] == pytest.approx(f[0], rel=1e-12)

    def test_detuning_verdict(self):
        chain = _case_chain()
        f1 = natural_frequencies(chain, include_rigid=False)[0]
        n_res = 60.0 * f1 / 4.0
        # sitting 20 % below the crossing is "detuned"
        ok = detuning_margin(chain, order=4.0, rpm=n_res * 0.80, mode=1, threshold=0.10)
        assert ok["status"] == "detuned"
        assert ok["margin"] == pytest.approx(0.25, rel=1e-12)
        # sitting on the crossing is flagged
        bad = detuning_margin(chain, order=4.0, rpm=n_res, mode=1, threshold=0.10)
        assert bad["status"] == "resonant-risk"
        assert bad["margin"] == pytest.approx(0.0, abs=1e-12)

    def test_case_study_numbers(self):
        chain = _case_chain()
        payload = evaluate_chain(
            chain,
            rpm_range=(0.0, 300.0),
            orders=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0),
            service_rpm=102.0,
            service_order=4.0,
        )
        f = np.asarray(payload["flexible_frequencies_hz"])
        assert f.size == 2
        assert payload["cross_check"]["max_relative_difference"] < 1e-6
        # first two modes of the plant, anchored by the closed-form chain values
        assert 6.0 < f[0] < 10.0
        assert 30.0 < f[1] < 60.0
        assert "resonances" in payload and "detuning" in payload


# ---------------------------------------------------------------------------
# 7. API endpoint
# ---------------------------------------------------------------------------

class TestApiEndpoint:
    @staticmethod
    def _client():
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.calculations import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_endpoint_returns_engine_payload(self):
        chain = _case_chain()
        body = {
            "inertias_kg_m2": list(chain.inertias),
            "stiffnesses_Nm_rad": list(chain.stiffnesses),
            "damping_ratio": 0.02,
            "excitation_orders": [4.0, 6.0],
            "rpm_range": [0.0, 400.0],
            "service_rpm": 102.0,
            "service_order": 4.0,
        }
        r = self._client().post("/calculations/vibration/shaft_torsion", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["evidence_grade"] == "REAL"
        expected = natural_frequencies(chain, include_rigid=False)
        assert np.allclose(data["flexible_frequencies_hz"], expected, rtol=1e-12)
        assert data["cross_check"]["max_relative_difference"] < 1e-6
        assert data["detuning"]["mode"] == 1

    def test_endpoint_rejects_inconsistent_chain(self):
        body = {"inertias_kg_m2": [1000.0, 2000.0], "stiffnesses_Nm_rad": [1.0e7, 2.0e7]}
        r = self._client().post("/calculations/vibration/shaft_torsion", json=body)
        assert r.status_code == 400
        assert "stiffnesses" in r.text