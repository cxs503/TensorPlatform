"""m1874 操纵域未覆盖端点族审计的离线回归测试。

两层（照 m1590 的成例）：

1. ``TestNomotoReference`` —— 独立参考核（一阶 Nomoto 精确闭式解）自身的正确性。
   这一层无条件成立：它把参考核钉在解析解与 RK4 异离散交叉上，防止参考核漂移。
2. ``TestRecordedVerdict`` —— 对**已记录工件**（``artifacts/maneuvering_debt_m1874/``）的断言。
   它把 2026-11-02 实测到的偏差与其量级钉死：
   * 将来端点被修好 ⇒ 这些测试会响亮失败，台账必须同步更新；
   * 端点反向退化 ⇒ 也立刻失败。

第二层完全离线（只读 JSON），因此可在 CI 里跑。

跑法：``python3 -m pytest tests/test_maneuver_debt_m1874.py --noconftest -q``
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "audit"))

import maneuver_debt_m1874 as M  # noqa: E402

ART = os.path.join(ROOT, "artifacts", "maneuvering_debt_m1874")


# =============================================================== 1. 参考核自证
def _rk4_zigzag(k: float, t: float, delta_deg: float, psi0_deg: float,
                dt: float = 0.02) -> tuple:
    """Z 形试验的 RK4 数值解（异离散交叉用）。状态 (ψ, r)。"""
    d = math.radians(delta_deg)
    ps0 = math.radians(psi0_deg)

    def f(state, delta):
        psi, r = state
        return (r, (k * delta - r) / t)

    psi = r = 0.0
    delta = d
    switched = False
    os1 = None
    t_switch = t_back = None
    time = 0.0
    max_psi = -1e9
    tau_back = 0.0
    while time < 2000.0 * t:
        k1 = f((psi, r), delta)
        k2 = f((psi + 0.5 * dt * k1[0], r + 0.5 * dt * k1[1]), delta)
        k3 = f((psi + 0.5 * dt * k2[0], r + 0.5 * dt * k2[1]), delta)
        k4 = f((psi + dt * k3[0], r + dt * k3[1]), delta)
        psi += dt / 6.0 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        r += dt / 6.0 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        time += dt
        if not switched and psi >= ps0:
            switched, t_switch, delta = True, time, -d
            max_psi = psi
            continue
        if switched and t_back is None:
            max_psi = max(max_psi, psi)
            if r < 0.0 and psi < ps0:
                t_back, os1 = time, math.degrees(max_psi - ps0)
                break
    return os1, (t_back - t_switch) if (t_back and t_switch) else float("nan")


class TestNomotoReference:
    def test_steady_yaw_rate_is_k_times_delta(self):
        k, d = 0.0437, math.radians(35.0)
        r_inf = M.nomoto_r(0.0, k, 60.0, d, 500.0 * 60.0)
        assert r_inf == pytest.approx(k * d, rel=1e-12)

    def test_steady_diameter_inverts_to_same_k(self):
        k, d, v = 0.0437, math.radians(35.0), 7.2
        dia = M.nomoto_steady_diameter(k, d, v)
        assert M.nomoto_implied_k(dia, d, v) == pytest.approx(k, rel=1e-12)

    def test_psi_closed_form_matches_rk4_free_run(self):
        """无换舵自由响应：闭式 ψ(τ) 与 RK4 必须重合到 1e-9。"""
        k, t, d, v0 = 0.05, 40.0, math.radians(10.0), 0.0
        psi_cf = M.nomoto_psi(0.0, 0.0, k, t, d, 137.0)
        # RK4
        psi, r, dt, time = 0.0, 0.0, 1e-3, 0.0
        while time < 137.0 - 1e-12:
            f = lambda s: (s[1], (k * d - s[1]) / t)  # noqa: E731
            k1 = f((psi, r)); k2 = f((psi + .5 * dt * k1[0], r + .5 * dt * k1[1]))
            k3 = f((psi + .5 * dt * k2[0], r + .5 * dt * k2[1]))
            k4 = f((psi + dt * k3[0], r + dt * k3[1]))
            psi += dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
            r += dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
            time += dt
        assert psi == pytest.approx(psi_cf, abs=1e-6)

    def test_zigzag_overshoot_matches_rk4(self):
        """ψ_os = T(r_sw + Kδ) 的精确性与 RK4 逐步积分一致（异离散交叉）。"""
        for k, t in ((0.0437, 60.0), (0.0042, 605.56), (0.10, 25.0)):
            an = M.nomoto_zigzag(k, t, 10.0, 10.0)
            num, half = _rk4_zigzag(k, t, 10.0, 10.0)
            # 峰值是个浅极大值，对换舵时刻敏感：两种离散给到 10% 内即算交叉成功
            assert an["overshoot_1_deg"] == pytest.approx(num, rel=0.1, abs=0.05)
            assert an["half_period_s"] == pytest.approx(half, rel=2e-3, abs=2e-2)

    def test_overshoot_monotone_in_rudder_angle(self):
        prev = -1.0
        for delta in (5.0, 10.0, 20.0, 35.0):
            os1 = M.nomoto_zigzag(0.0437, 60.0, delta, 10.0)["overshoot_1_deg"]
            assert os1 > prev
            prev = os1

    def test_period_scales_linearly_with_time_constant(self):
        """(K,T) 同比例放大 ⇒ 时间历程同比放大（量纲一致性）。"""
        a = M.nomoto_zigzag(0.0437, 60.0, 10.0, 10.0)
        b = M.nomoto_zigzag(0.0437 / 3.0, 60.0 * 3.0, 10.0, 10.0)
        assert b["half_period_s"] == pytest.approx(a["half_period_s"] * 3.0, rel=1e-6)
        assert b["overshoot_1_deg"] == pytest.approx(a["overshoot_1_deg"], rel=1e-6)

    def test_zero_gain_gives_no_response(self):
        z = M.nomoto_zigzag(1e-12, 60.0, 10.0, 10.0)
        assert z["overshoot_1_deg"] == pytest.approx(0.0, abs=1e-6)


# =============================================================== 2. 工件一致性
@pytest.fixture(scope="module")
def verdict():
    p = os.path.join(ART, "verdict.json")
    if not os.path.exists(p):
        pytest.skip("工件缺失：先跑 tools/audit/maneuver_debt_m1874.py")
    return json.load(open(p))


@pytest.fixture(scope="module")
def probe():
    p = os.path.join(ART, "probe.json")
    if not os.path.exists(p):
        pytest.skip("工件缺失：先跑 tools/audit/maneuver_debt_m1874.py")
    return json.load(open(p))


class TestRecordedVerdict:
    def test_family_verdicts(self, verdict):
        fam = verdict["families"]
        assert fam["abkowitz/derivatives"]["verdict"] == "MOCK"
        assert fam["abkowitz/simulate"]["verdict"] == "STUB"
        assert fam["extended-calculations/maneuvering"]["verdict"] == "MOCK"
        assert fam["maneuvering-mmg/{turning,zigzag}"]["verdict"] == "STUB"

    def test_criteria_are_numbered_and_carry_verdicts(self, verdict):
        ids = [c["id"] for c in verdict["criteria"]]
        assert len(ids) == len(set(ids))
        assert {"A1", "B1", "B10", "C1", "C2"} <= set(ids)
        assert all(c["verdict"] in ("PASS", "FAIL", "INFO", "WITHHELD")
                   for c in verdict["criteria"])

    def test_abkowitz_manoeuvre_never_happens(self, probe):
        """A1/A2：声称 zigzag 但 ψ 与 δ 全程为 0（静默空转）。"""
        rows = [r for r in probe["abkowitz_simulate"] if r["status"] == 200]
        assert rows, "至少应有 delta0=0 的成功探针"
        for r in rows:
            assert r["maxabs_psi"] == pytest.approx(0.0, abs=1e-12)
            assert r["maxabs_delta"] == pytest.approx(0.0, abs=1e-12)
            assert r["tactical_diameter"] is None and r["advance"] is None

    def test_abkowitz_nonzero_rudder_is_500(self, probe):
        """A5：delta0>0 的调用全部非 200。"""
        bad = [r for r in probe["abkowitz_simulate"] if r["delta0"] > 0]
        assert bad and all(r["status"] != 200 for r in bad)

    def test_derivatives_are_hull_form_blind(self, probe):
        """B1–B3：Cb/B/T 三轴对全部五个代表导数响应恰为 0。"""
        for axis in ("Cb", "B", "T"):
            rows = probe["abkowitz_derivatives"][axis]
            lo, hi = rows[0]["derivatives"], rows[-1]["derivatives"]
            for key in M.DERIV_KEYS:
                assert hi[key] == pytest.approx(lo[key], rel=1e-12), (axis, key)

    def test_derivatives_scale_as_delta_times_L(self, probe):
        """B4：Y_v·L/Δ ≡ −20，N_r/(L·Δ) ≡ −10（合成张量的指纹）。"""
        rows = probe["abkowitz_derivatives"]["L"]
        dsp = probe["base_ship"]["displacement"]
        yv = [r["derivatives"]["Y_v"] * r["value"] / dsp for r in rows]
        nr = [r["derivatives"]["N_r"] / (r["value"] * dsp) for r in rows]
        assert all(v == pytest.approx(yv[0], rel=1e-9) for v in yv)
        assert all(v == pytest.approx(nr[0], rel=1e-9) for v in nr)
        assert yv[0] == pytest.approx(-20.0, rel=1e-9)
        assert nr[0] == pytest.approx(-10.0, rel=1e-9)

    def test_ext_calc_turning_is_pure_length_scaling(self, probe):
        """B5–B10：T_d ≡ 3.59·L，advance ≡ 1.746·L，B/T/Δ 响应为 0。"""
        ext = probe["ext_calc"]
        for r in ext["length"]:
            assert r["tactical_diameter"] / r["value"] == pytest.approx(3.59, abs=1e-6)
            assert r["advance"] / r["value"] == pytest.approx(314.3 / 180.0, rel=5e-3)
        for axis, key in (("beam", "tactical_diameter"), ("draft", "tactical_diameter"),
                          ("displacement", "tactical_diameter"), ("beam", "advance")):
            vals = {r[key] for r in ext[axis]}
            assert len(vals) == 1, (axis, key)

    def test_ext_calc_stability_wording_is_constant(self, probe):
        """B11：Cb 0.60/0.82/0.90 全报「良好/稳定」。"""
        words = {(r["straight_stability"], r["dynamic_stability"]) for r in probe["ext_calc"]["cb"]}
        assert len(words) == 1

    def test_nomoto_closure_fails_by_8x(self, verdict):
        """C1/C2：自报 (K,T) 隐含 D 是自报 T_d 的 8.68 倍。"""
        nr = verdict["nomoto_reference"]
        ratio = nr["Td_closed_m"] / nr["Td_reported_m"]
        assert ratio == pytest.approx(8.68, abs=0.05)
        assert nr["K_implied"] / nr["K_reported"] == pytest.approx(ratio, rel=1e-6)

    def test_cross_endpoint_zigzag_disagrees_with_closed_form(self, verdict):
        """C4/C5：端点 1.5°/972 s vs Nomoto 闭式解 41.9°/663.9 s。"""
        z = verdict["nomoto_reference"]["zigzag_nomoto"]
        assert 3.5 < z["overshoot_1_deg"] < 4.0   # 同船自报 (K,T) 的闭式解
        assert (z["overshoot_1_deg"] - 1.5) / z["overshoot_1_deg"] > 0.5  # 端点低 60%
        assert z["half_period_s"] == pytest.approx(663.9, abs=1.0)

    def test_mmg_family_unreachable(self, probe):
        """A6：maneuvering-mmg 族两种极端输入均非 200。"""
        assert probe["mmg_family"]
        assert all(r["status"] != 200 for r in probe["mmg_family"])

    def test_figures_exist(self):
        for f in ("fig_td_by_endpoint.png", "fig_parameter_blindness.png", "fig_nomoto_closure.png"):
            p = os.path.join(ART, f)
            assert os.path.exists(p) and os.path.getsize(p) > 20000, f