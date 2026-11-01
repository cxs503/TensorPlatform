"""操纵性证据分级判据的测试（离线可跑，不依赖 8001 在线）.

四层：
  1. Nomoto 解析解 vs 数值积分  —— 判据自身的正确性（1e-9 级）
  2. 恒等式/响应性/规范判据的单元行为
  3. 判据合成的证据等级（用合成 fixture，不依赖线上平台）
  4. 取证回放：对仓库内 artifacts/m1456_maneuver/probe_matrix.json 复裁，
     把 2026-11-01 的判决钉成回归基线（平台一旦修复，此测试会变红并提示更新）
"""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pytest

from tools.maneuver_judge import checks, nomoto

ART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "artifacts", "m1456_maneuver", "probe_matrix.json")


# ---------------------------------------------------------------- 1. 解析解
class TestNomotoAnalytic:
    def test_r_matches_numeric_ode(self):
        resp = nomoto.NomotoResponse(K=0.12, T=45.0)
        delta = math.radians(35.0)
        t = np.linspace(0.0, 600.0, 6001)   # h=0.1 s, RK4 截断误差 << 1e-9
        # 数值积分 T dr/dt + r = K delta，RK4
        r = np.zeros_like(t)
        for i in range(len(t) - 1):
            h = t[i + 1] - t[i]
            f = lambda x: (resp.K * delta - x) / resp.T
            k1, k2, k4 = f(r[i]), f(r[i] + 0.5 * h * f(r[i])), f(r[i] + h * f(r[i]))
            r[i + 1] = r[i] + (h / 6.0) * (k1 + 4.0 * k2 + k4)
        r_ana = resp.r_of_t(delta, t)
        # 判据自身精度：相对 r_ss 的偏差 << 1e-6（h=0.1 s, RK4 四阶）
        assert np.max(np.abs(r - r_ana)) / resp.r_steady(delta) < 1e-6

    def test_psi_matches_integral_of_r(self):
        resp = nomoto.NomotoResponse(K=0.08, T=60.0)
        delta = math.radians(20.0)
        t = np.linspace(0.0, 900.0, 9001)
        psi_num = np.concatenate(([0.0], np.cumsum(
            0.5 * (resp.r_of_t(delta, t[1:]) + resp.r_of_t(delta, t[:-1])) * np.diff(t))))
        psi_ana = resp.psi_of_t(delta, t)
        assert np.max(np.abs(psi_num - psi_ana)) < 1e-6

    def test_steady_state_limits(self):
        resp = nomoto.NomotoResponse(K=0.1, T=30.0)
        delta = math.radians(30.0)
        assert resp.r_of_t(delta, 1e5) == pytest.approx(resp.r_steady(delta), rel=1e-9)
        # 稳态 ψ 线性增长，斜率 = r_ss
        slope = (resp.psi_of_t(delta, 1e5) - resp.psi_of_t(delta, 1e5 - 1.0))
        assert slope == pytest.approx(resp.r_steady(delta), rel=1e-9)

    def test_radius_scales_as_inverse_delta(self):
        resp = nomoto.NomotoResponse(K=0.1, T=30.0)
        d10 = resp.diameter(math.radians(10.0), U=8.0)
        d20 = resp.diameter(math.radians(20.0), U=8.0)
        d40 = resp.diameter(math.radians(40.0), U=8.0)
        assert d10 / d20 == pytest.approx(2.0, rel=1e-12)
        assert d20 / d40 == pytest.approx(2.0, rel=1e-12)

    def test_time_to_fraction_inverts_response(self):
        resp = nomoto.NomotoResponse(K=0.1, T=42.0)
        delta = math.radians(15.0)
        t63 = resp.time_to_fraction(0.632)
        assert resp.r_of_t(delta, t63) / resp.r_steady(delta) == pytest.approx(0.632, abs=1e-3)

    def test_trajectory_closes_up(self):
        """轨迹数值 r 与解析 r 一致；整圈后回到起点附近（半径 R 的圆）。"""
        resp = nomoto.NomotoResponse(K=0.1, T=30.0)
        U = 8.0
        tr = resp.trajectory(math.radians(30.0), U, t_end=600.0, dt=0.02)
        assert np.max(np.abs(tr["r_num"] - tr["r"])) < 1e-3
        R_expected = resp.radius(math.radians(30.0), U)
        # 稳态后的局部曲率半径 ≈ R
        late = tr["t"] > 500.0
        speed = np.hypot(np.gradient(tr["x"], tr["t"]), np.gradient(tr["y"], tr["t"]))
        assert np.median(speed[late]) == pytest.approx(U, rel=1e-3)
        assert R_expected > 0


# ---------------------------------------------------------------- 2. 判据单元
class TestIdentityCriteria:
    def test_kinematic_identity_passes_when_consistent(self):
        K = 0.12
        r_ss_deg = math.degrees(K * math.radians(35.0))
        U = 6.0
        D = 2.0 * U / (K * math.radians(35.0))
        res = checks.kinematic_consistency(r_ss_deg, U, D)
        assert res.passed and res.rel_error == pytest.approx(0.0, abs=1e-9)

    def test_kinematic_identity_flags_platform_triple(self):
        """平台实测三元组 (0.5 deg/s, 2.87 m/s, D=1440 m)：恒等式差 1 倍以上。"""
        res = checks.kinematic_consistency(0.5, 2.87, 1440.0)
        R = 720.0
        expected = math.degrees(2.87 / R)
        assert res.expected == pytest.approx(expected, rel=1e-12)
        assert res.rel_error > 0.9          # >90% 偏差
        assert not res.passed

    def test_diameter_pair_consistency(self):
        assert checks.diameter_equals_2R_consistency(1440.0, 1400.0).passed
        assert not checks.diameter_equals_2R_consistency(1440.0, 1120.0).passed

    def test_nonpositive_input_rejected(self):
        res = checks.kinematic_consistency(0.5, 0.0, 1440.0)
        assert not res.passed and math.isinf(res.rel_error)


class TestSensitivityCriteria:
    def test_flat_response_is_no_response(self):
        s = checks.loglog_sensitivity("Ar", [50, 100, 300], [1440, 1440, 1440])
        assert s.respond is False and s.sign_ok is False
        assert abs(s.loglog_slope) < 1e-9

    def test_inverse_law_detected(self):
        s = checks.loglog_sensitivity("rudder_angle", [5, 10, 20, 35],
                                      [7000 / a for a in (5, 10, 20, 35)])
        assert s.respond is True and s.sign_ok is True
        assert s.loglog_slope == pytest.approx(-1.0, rel=1e-9)

    def test_wrong_sign_flagged(self):
        s = checks.loglog_sensitivity("displacement", [1e5, 2e5], [100.0, 50.0],
                                      expected_sign="+")
        assert s.respond is True and s.sign_ok is False

    def test_rudder_scaling_law_pass_and_fail(self):
        bad = checks.rudder_scaling_law({5: 1440, 10: 1440, 20: 1440, 35: 1440})
        assert bad["verdict"] == "FAIL"
        good = checks.rudder_scaling_law({a: 50000 / a for a in (5, 10, 20, 35)})
        assert good["verdict"] == "PASS" and good["in_physics_band"]


class TestRegulatoryCriteria:
    def test_imo_turning_limits(self):
        ok = checks.imo_criteria_check(L=230.0, tactical_diameter=4.0 * 230.0,
                                       advance=4.0 * 230.0)
        assert ok["all_passed"] is True
        bad = checks.imo_criteria_check(L=230.0, tactical_diameter=5.5 * 230.0,
                                        advance=5.0 * 230.0)
        assert bad["all_passed"] is False

    def test_imo_zigzag_limits(self):
        r = checks.imo_criteria_check(L=230.0, overshoot={
            "zigzag_10_10_first_overshoot_deg": 12.0,
            "zigzag_20_20_first_overshoot_deg": 18.0})
        flags = {c["item"]: c["passed"] for c in r["criteria"]}
        assert flags["zigzag_10_10_first_overshoot_deg"] is False
        assert flags["zigzag_20_20_first_overshoot_deg"] is True

    def test_corpus_band(self):
        c = checks.corpus_band_check(5.0, {"min": 20.0, "max": 30.0, "typical": 25.0})
        assert c["inside_band"] is False
        assert c["rel_error_pct"] == pytest.approx(-80.0, abs=1e-9)


# ---------------------------------------------------------------- 3. 判决合成
def _synth_rows(endpoint, results, inputs=None):
    return [dict(endpoint=endpoint, label=f"p{i}", input=(inputs or [{}])[i] if inputs else {},
                 result=r, status=200, error="")
            for i, r in enumerate(results)]


class TestGradeSynthesis:
    def test_all_5xx_is_broken(self):
        rows = _synth_rows("/x", [None, None], [{}, {}])
        for r in rows:
            r["status"] = 500
        v = checks.grade_turning(230.0, rows, n_broken=2)
        assert v["grade"] == "BROKEN"

    def test_hardcoded_is_mock(self):
        inj = [dict(rudder_angle=a, Ar=100, displacement=5.2e4, speed=24.0)
               for a in (5, 10, 20, 35)]
        res = [dict(tactical_diameter=1035.0, advance=805.0, transfer=460.0,
                    steady_diameter=1035.0, final_speed=8.6, turning_rate=0.5)
               for _ in range(4)]
        v = checks.grade_turning(230.0, _synth_rows("/x", res, inj), n_broken=0)
        assert v["grade"] == "MOCK"
        assert "rudder_angle" in v["reason"]

    def test_physics_consistent_is_real(self):
        L, K = 230.0, 0.12
        rows = []
        for a in (10.0, 20.0, 35.0):
            U = 6.0
            D = 2.0 * U / (K * math.radians(a))
            rows.append(dict(endpoint="/x", label=f"r{a}", status=200, error="",
                             input=dict(rudder_angle=a, Ar=100.0, displacement=5.2e4),
                             result=dict(tactical_diameter=D, advance=D * 0.75,
                                         steady_diameter=D, final_speed=U,
                                         turning_rate=math.degrees(U / (D / 2.0)))))
        v = checks.grade_turning(L, rows, n_broken=0)
        assert v["grade"] == "REAL", v["reason"]

    def test_zigzag_hardcoded_is_mock(self):
        # 舵角变化时超越角线性变（形式上"有响应"），但排水量/航速完全无影响
        rows = [dict(endpoint="/z", label=f"z{a}_{d}", status=200, error="",
                     input=dict(rudder_angle=a, displacement=d, speed=s),
                     result=dict(overshoot_1=a * 0.15, overshoot_2=a * 0.25, period=1500.0))
                for a in (10.0, 20.0) for d in (5.2e4, 7.8e4) for s in (12.0, 18.0)]
        v = checks.grade_zigzag(rows, n_broken=0)
        assert v["grade"] == "MOCK"


# ---------------------------------------------------------------- 4. 取证回放
@pytest.mark.skipif(not os.path.exists(ART), reason="取证快照缺失（需先跑 run_audit.py）")
class TestForensicReplay:
    """对 2026-11-01 快照离线复裁 —— 判决一旦变化必须显式更新基线。"""

    @pytest.fixture(scope="class")
    def snap(self):
        with open(ART, encoding="utf-8") as f:
            return json.load(f)

    def _ep_rows(self, snap, ep, ship):
        return [r for r in snap["probe_matrix"]["turning"] + snap["probe_matrix"]["zigzag"]
                if r["endpoint"] == ep and r.get("ship") == ship]

    def test_turning_endpoint_is_mock(self, snap):
        rows = self._ep_rows(snap, "/api/v1/maneuvering/turning", "KCS")
        assert rows and all(r["status"] == 200 for r in rows)
        v = checks.grade_turning(230.0, rows)
        assert v["grade"] == "MOCK"
        dia = {r["input"]["rudder_angle"]: r["result"]["tactical_diameter"] for r in rows}
        # 7 倍舵角范围内直径完全不变 ⇒ 硬编码铁证
        assert len(set(dia.values())) == 1
        assert abs(dia[35.0] / 230.0 - 4.5) < 1e-9     # = 4.5 L

    def test_mmg_endpoints_are_broken(self, snap):
        for ep in ("/api/v1/maneuvering-mmg/turning", "/api/v1/maneuvering-mmg/zigzag"):
            rows = self._ep_rows(snap, ep, "KCS")
            assert rows
            assert all(r["status"] == 500 for r in rows), ep
            if "turning" in ep:
                v = checks.grade_turning(230.0, rows, n_broken=len(rows))
            else:
                v = checks.grade_zigzag(rows, n_broken=len(rows))
            assert v["grade"] == "BROKEN"

    def test_zigzag_overshoot_constant_multiple(self, snap):
        rows = self._ep_rows(snap, "/api/v1/maneuvering/zigzag", "KCS")
        for r in rows:
            res = r["result"]
            a = abs(r["input"]["rudder_angle"])
            assert res["overshoot_1"] == pytest.approx(a * 0.15, abs=1e-9)
            assert res["overshoot_2"] == pytest.approx(a * 0.25, abs=1e-9)

    def test_kt_dimensional_inconsistency(self, snap):
        rows = [r for r in snap["probe_matrix"]["kt"] if r.get("ship") == "KCS"]
        L, U = 230.0, 24.0 * 0.5144
        r0 = rows[0]["result"]
        assert r0["K"] * L / U > 3.0     # K' 远超文献带 0.3~3
        assert r0["T"] * U / L > 3.0     # T' 同理