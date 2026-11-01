"""Tests for the m1413 propulsion verification-debt harness.

Two layers:

* anchor tests — the literature-free judges must be internally exact
  (definitional identities / invariances to ~1e-12, momentum bound holds over a
  whole J sweep).  These are hard assertions.
* judge tests — feed *doctored* platform payloads to the verdict functions and
  assert that each anomaly class is actually detected.  This proves the judge
  has teeth without requiring the ship service to be running or healthy.
* live audit — skipped when the ship service is unreachable; when it is up the
  test only asserts that the audit produced a well-formed verdict, never that
  the platform passes (the platform's grade is a finding, not a gate).
"""

import math
import os
import socket
import sys

import pytest

_BENCH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "tools", "benchmarks")
sys.path.insert(0, _BENCH)

import propulsion_anchors as pa            # noqa: E402
import platform_propulsion_judge as judge   # noqa: E402


# --------------------------------------------------------------- anchors
class TestAnchors:
    def test_all_anchors_pass(self):
        rows = pa.run_anchors()
        failed = [(r["id"], r["rel_error"]) for r in rows if not r["pass"]]
        assert not failed, failed

    def test_anchor_count_and_coverage(self):
        rows = pa.run_anchors()
        ids = {r["id"] for r in rows}
        assert ids == {"P-A1", "P-A2", "P-A3", "P-B1", "P-B2", "P-B3",
                       "P-C1", "P-D1", "P-D2", "P-E1", "P-F1"}
        kinds = {r["kind"] for r in rows}
        assert kinds == {"exact", "derivation", "bound"}

    def test_worst_error_is_machine_precision(self):
        summ = pa.summary(pa.run_anchors())
        assert summ["all_pass"]
        assert summ["worst_rel_error"] < 1e-12
        assert summ["within_3pct"]

    def test_ittc57_closed_form_pins(self):
        # At Re = 10^k the ITTC-57 line collapses to the exact rational 0.075/(k-2)^2.
        assert pa.ittc57_cf(1e6) == pytest.approx(0.075 / 16.0, rel=1e-15)
        assert pa.ittc57_cf(1e7) == pytest.approx(0.003, rel=1e-15)
        assert pa.ittc57_cf(1e9) == pytest.approx(0.075 / 49.0, rel=1e-15)

    def test_ittc57_monotone_decreasing(self):
        cf = [pa.ittc57_cf(10.0 ** k) for k in range(5, 10)]
        assert all(a > b for a, b in zip(cf, cf[1:]))

    def test_momentum_quadratic_matches_closed_form(self):
        # Independent algebra: solving T = 2 rho A0 (Va+w) w must reproduce
        # eta_i = 2/(1+sqrt(1+C_T)) to machine precision for a range of loadings.
        D, n = 0.3048, 10.0
        for J in (0.15, 0.35, 0.55, 0.75, 0.95):
            for KT in (0.10, 0.20, 0.30):
                Va = J * n * D
                T = KT * pa.RHO_SEAWATER * n ** 2 * D ** 4
                CT = pa.thrust_loading(T, Va, D)
                assert pa.ideal_efficiency_from_momentum(T, Va, D) == pytest.approx(
                    pa.ideal_efficiency(CT), rel=1e-12)

    def test_ideal_efficiency_limits(self):
        assert pa.ideal_efficiency(0.0) == pytest.approx(1.0)
        assert pa.ideal_efficiency(1.0) == pytest.approx(2.0 / (1.0 + math.sqrt(2.0)))
        assert pa.ideal_efficiency(1e6) < 0.01
        # strictly decreasing in loading
        vals = [pa.ideal_efficiency(c) for c in (0.0, 1.0, 4.0, 9.0)]
        assert all(a > b for a, b in zip(vals, vals[1:]))

    def test_froude_scaling_invariance(self):
        # J, K_T, K_Q are dimensionless -> invariant under lambda scaling with
        # V -> V sqrt(l), n -> n/sqrt(l), D -> l D, T -> l^3 T, Q -> l^4 Q.
        D, n, J = 0.3048, 10.0, 0.7
        KT, KQ = 0.22, 0.032
        Va = J * n * D
        T = KT * pa.RHO_SEAWATER * n ** 2 * D ** 4
        Q = KQ * pa.RHO_SEAWATER * n ** 2 * D ** 5
        for lam in (2.0, 7.5, 12.0, 40.0):
            s = math.sqrt(lam)
            assert pa.advance_ratio(Va * s, n / s, D * lam) == pytest.approx(J, rel=1e-12)
            assert pa.thrust_coefficient(T * lam ** 3, n / s, D * lam) == pytest.approx(KT, rel=1e-12)
            assert pa.torque_coefficient(Q * lam ** 4, n / s, D * lam) == pytest.approx(KQ, rel=1e-12)

    def test_wrong_torque_exponent_is_detected(self):
        # Regression guard: K_Q ~ D^5 means torque scales as lambda^4. Using
        # lambda^3 (the K_T exponent) must break invariance -> the anchor has teeth.
        D, n, J = 0.3048, 10.0, 0.7
        KQ = 0.032
        Q = KQ * pa.RHO_SEAWATER * n ** 2 * D ** 5
        lam, s = 12.0, math.sqrt(12.0)
        with pytest.raises(AssertionError):
            assert pa.torque_coefficient(Q * lam ** 3, n / s, D * lam) == pytest.approx(KQ, rel=1e-6)

    def test_admissibility_sweep_holds(self):
        sw = pa.sweep_admissibility()
        assert sw["all_bound_ok"], sw["tightest_point"]
        assert sw["all_eta_below_unity"]
        assert sw["n_points"] >= 25
        assert sw["min_margin"] > 0.0
        # the illustrative family must place its peak in a physically sensible band
        assert 0.35 <= sw["peak"]["J"] <= 0.85
        assert 0.45 <= sw["peak"]["eta_o"] <= 0.75

    def test_propulsion_chain_identity(self):
        t, w, eta_R = 0.18, 0.28, 1.02
        J, KT, KQ = 0.833, 0.285, 0.060
        eta_o = pa.open_water_efficiency(J, KT, KQ)
        eta_H = pa.hull_efficiency(t, w)
        Va = J * 10.0 * 0.3048          # advance speed implied by J = 0.833
        V_ship = Va / (1.0 - w)
        R = KT * pa.RHO_SEAWATER * 10.0 ** 2 * 0.3048 ** 4 * (1.0 - t)
        Q_behind = KQ * pa.RHO_SEAWATER * 10.0 ** 2 * 0.3048 ** 5 / eta_R
        eta_D = pa.effective_power(R, V_ship) / pa.delivered_power(10.0, Q_behind)
        assert eta_D == pytest.approx(eta_o * eta_H * eta_R, rel=1e-12)


# ----------------------------------------------------------------- judges
class TestJudges:
    def _case(self, J=0.6, KT=0.189, KQ=0.0189, eta0=0.85, cid="T"):
        return judge.judge_open_water_case(cid, J, KT, KQ, eta0)

    def test_consistent_point_passes(self):
        # Physically coherent point: eta_o = J KT/(2 pi KQ) and below the ceiling.
        J, KT = 0.6, 0.20
        KQ = J * KT / (2 * math.pi * 0.55)      # eta_o = 0.55 by construction
        c = self._case(J=J, KT=KT, KQ=KQ, eta0=0.55)
        assert c["J1_identity_ok"] and c["J2_bound_reported_ok"]
        assert c["J2_bound_implied_ok"] and c["J3_physical"]
        assert c["failures"] == []

    def test_identity_violation_flagged(self):
        # reported eta0 inconsistent with the returned K_T / K_Q
        c = self._case(J=0.6, KT=0.189, KQ=0.0189, eta0=0.85)
        assert not c["J1_identity_ok"]
        assert any("J1" in f for f in c["failures"])
        assert c["identity_error"] > 0.10

    def test_momentum_bound_violation_flagged(self):
        # eta_o = 1.59 is above the actuator-disk ceiling and above unity.
        c = self._case(J=1.0, KT=0.105, KQ=0.0105, eta0=0.85)
        assert not c["J2_bound_implied_ok"]
        assert not c["J3_physical"]

    def test_saturation_detected(self):
        rows = [{"J": J, "P_D": 1.0, "Z": 4, "Ae_Ao": 0.55,
                 "KT": 0.3 - 0.15 * J, "KQ": (0.3 - 0.15 * J) * 0.1,
                 "eta0": min(0.318 * J / 0.2, 0.85), "_sweep": "J"}
                for J in (0.2, 0.4, 0.6, 0.8, 1.0)]
        st = judge.judge_b_series_structure(rows)
        assert not st["J4_structure_ok"], "constant K_Q/K_T must be flagged"
        assert not st["J5_no_saturation_ok"], "eta0 saturation must be flagged"
        assert st["kq_over_kt"] == [0.1]

    def test_healthy_structure_accepted(self):
        # A genuine regression: K_Q/K_T varies and eta0 peaks then decays.
        pts = [(0.2, 0.28, 0.035), (0.4, 0.24, 0.034), (0.6, 0.19, 0.031),
               (0.8, 0.13, 0.026), (1.0, 0.05, 0.018)]
        rows = [{"J": J, "P_D": 1.0, "Z": 4, "Ae_Ao": 0.55, "KT": KT, "KQ": KQ,
                 "eta0": pa.open_water_efficiency(J, KT, KQ), "_sweep": "J"}
                for J, KT, KQ in pts]
        st = judge.judge_b_series_structure(rows)
        assert st["J4_structure_ok"] and st["J5_no_saturation_ok"]

    def test_chain_above_unity_flagged(self):
        res = {"open_water_eff": 1.369, "hull_eff": 1.1, "relative_rot_eff": 1.0,
               "propulsive_eff": 1.506, "delivered_power": 12696.67, "torque": 1010.369}
        ch = judge.judge_propulsion_chain(res)
        assert ch["J6_chain_identity_ok"]          # chain is internally consistent ...
        assert not ch["eta_D_below_unity"]         # ... but the result is impossible
        assert not ch["eta_o_below_unity"]
        assert "J6 eta_D < 1" in ch["failures"]

    def test_chain_break_detected(self):
        res = {"open_water_eff": 0.60, "hull_eff": 1.05, "relative_rot_eff": 1.0,
               "propulsive_eff": 0.90, "delivered_power": 1.0, "torque": 1.0}
        assert not judge.judge_propulsion_chain(res)["J6_chain_identity_ok"]

    def test_displacement_decoupling_detected(self):
        a = {"thrust": 3016.48, "propulsive_eff": 1.506}
        out = judge.judge_displacement_coupling([{"a": a, "b": dict(a)}])
        assert not out["J7_coupling_ok"]
        out2 = judge.judge_displacement_coupling(
            [{"a": a, "b": {"thrust": 40000.0, "propulsive_eff": 0.62}}])
        assert out2["J7_coupling_ok"]

    def test_overall_verdict_grades(self):
        bad = self._case()
        ch = judge.judge_propulsion_chain(
            {"open_water_eff": 1.369, "hull_eff": 1.1, "relative_rot_eff": 1.0,
             "propulsive_eff": 1.506, "delivered_power": 1.0, "torque": 1.0})
        cp = judge.judge_displacement_coupling([{"a": {"x": 1}, "b": {"x": 1}}])
        st = {"failures": [], "J4_structure_ok": True, "J5_no_saturation_ok": True}
        v = judge.overall_verdict([bad], st, ch, cp)
        assert v["evidence_grade"] == "MOCK" and v["n_failures"] >= 3
        assert "downgrade" in v["recommendation"]
        good = self._case(J=0.6, KT=0.20, KQ=0.6 * 0.20 / (2 * math.pi * 0.55), eta0=0.55)
        v2 = judge.overall_verdict([good], st, judge.judge_propulsion_chain(
            {"open_water_eff": 0.6, "hull_eff": 1.0, "relative_rot_eff": 1.0,
             "propulsive_eff": 0.6, "delivered_power": 1.0, "torque": 1.0}),
            judge.judge_displacement_coupling([{"a": {"x": 1}, "b": {"x": 2}}]))
        assert v2["evidence_grade"] == "REAL"


# -------------------------------------------------------------- live audit
def _ship_service_up(host="127.0.0.1", port=8001, timeout=1.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _ship_service_up(), reason="ship service :8001 unreachable")
class TestLiveAudit:
    def test_live_audit_is_well_formed(self):
        import verify_propulsion as vp
        probe = vp.probe_ship_service()
        live = vp.audit_live(probe["base"])
        for key in ("b_series_rows", "open_water_cases", "structure",
                    "propulsion_chain", "displacement_coupling", "verdict"):
            assert key in live
        if live["open_water_cases"]:
            c = live["open_water_cases"][0]
            for key in ("eta_reported", "eta_implied", "eta_ideal", "identity_error"):
                assert key in c
        if live["verdict"]:
            assert live["verdict"]["evidence_grade"] in ("REAL", "MOCK")

    def test_probe_reports_honestly(self):
        import verify_propulsion as vp
        probe = vp.probe_ship_service()
        assert probe["status"] in ("AVAILABLE", "WITHHELD")
        if probe["status"] == "AVAILABLE":
            assert probe["base"]
        else:
            assert "not a pass" in probe["detail"]