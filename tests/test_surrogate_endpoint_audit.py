"""m1828 · 代理端点核查的离线复算测试。

两层：
  1. 工件层（hermetic）：从 artifacts/m1828/surrogate_audit.json 出发，用独立算式
     重算全部判据量（相对误差、斜率、离差计数），确认裁决可被第三方离线复现；
  2. 裁决逻辑层：用合成证据测试 verdicts() 的判据→分级映射（全过=REAL，任一失效=MOCK），
     保证分级不是字符串常量。
现网探针测试在服务不可达时 skip，绝不以"跑通了"代替证据。
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.audit_surrogate_endpoints import verdicts  # noqa: E402

ART = os.path.join(ROOT, "artifacts", "m1828", "surrogate_audit.json")


@pytest.fixture(scope="module")
def audit():
    assert os.path.exists(ART), f"缺工件 {ART}"
    with open(ART, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# 1. 工件层：独立复算
# --------------------------------------------------------------------------

def test_artifact_declares_criteria_and_service(audit):
    assert audit["health_status"] == 200
    assert set(audit["criteria"]) == {f"S{i}" for i in range(1, 9)}
    assert audit["base_ship"]["Cb"] == 0.82


def test_pinn_spurious_relative_error_recomputed(audit):
    rows = audit["pinn_resistance"]["S4_vs_holtrop"]
    assert len(rows) == 6
    for row in rows:
        recomputed = 100.0 * abs(row["pinn"] - row["holtrop"]) / row["holtrop"]
        assert recomputed == pytest.approx(row["rel_err_pct"], rel=1e-9)
        # 平台精度标准：>15% 不可接受 —— PINN 全线越过
        assert recomputed > 15.0
    worst = max(r["rel_err_pct"] for r in rows)
    assert worst == pytest.approx(audit["pinn_resistance"]["S4_worst_rel_err_pct"])
    # 误差随航速单调收敛但仍远高于门限（不是噪声，是系统性偏大）
    assert rows[0]["rel_err_pct"] > rows[-1]["rel_err_pct"] > 15.0


def test_pinn_displacement_input_is_decorative(audit):
    sens = audit["pinn_resistance"]["S1_sensitivity"]
    assert sens["displacement"]["resistance"][0] == sens["displacement"]["resistance"][1]
    assert sens["displacement"]["varies"] is False
    # 反证：其余声明输入确实生效，说明探测本身有分辨力
    for key in ("L", "B", "T", "Cb", "water_density"):
        assert sens[key]["varies"] is True, key
    assert audit["pinn_resistance"]["S1_ignored_inputs"] == ["displacement"]


def test_pinn_speed_law_and_frozen_data_loss(audit):
    curve = audit["pinn_resistance"]["speed_curve"]
    r = [c["resistance"] for c in curve]
    assert audit["pinn_resistance"]["S3_monotone"] is True
    slope = math.log(r[-1] / r[0]) / math.log(curve[-1]["speed"] / curve[0]["speed"])
    assert slope == pytest.approx(audit["pinn_resistance"]["S3_loglog_slope"], rel=1e-12)
    assert 1.5 <= slope <= 3.5          # 速度律本身不违规
    # 但自陈 data_loss 全程恒定 => 占位数字，不是训练残差
    assert len({c["data_loss"] for c in curve}) == 1


def test_pinn_network_info_is_closed_form_coefficients(audit):
    info = audit["pinn_resistance"]["base_response"]["network_info"]
    assert "base_resistance" in info["architecture"]  # 架构串里就是固定系数
    assert "friction_factor" in info["architecture"]
    assert "residual_factor" in info["architecture"]


def test_pinn_seakeeping_heading_invariance_and_magnitude(audit):
    sea = audit["pinn_seakeeping"]
    heads = [c["heading"] for c in sea["heading_curve"]]
    assert heads == [0.0, 45.0, 90.0, 135.0, 180.0]
    assert len({c["heave_rao"] for c in sea["heading_curve"]}) == 1
    assert sea["S1_heading_sensitive"] is False
    assert sea["S6_heave_rao_max"] > 3.0          # 物理量级越界
    assert sea["S6_physics_loss_distinct"] == 1   # 损失恒定
    # 周期输入仍生效，说明不是"端点整体不动"
    assert sea["S6_period_sensitive"] is True


def test_transformer_error_exceeds_gate(audit):
    trf = audit["transformer_resistance"]
    rows = trf["S4_vs_holtrop"]
    for row in rows:
        recomputed = 100.0 * abs(row["transformer"] - row["holtrop"]) / row["holtrop"]
        assert recomputed == pytest.approx(row["rel_err_pct"], rel=1e-9)
    assert trf["S4_worst_rel_err_pct"] > 15.0
    assert trf["S1_geometry_sensitive"] is True   # 几何有响应，但精度不达标


def test_rl_energy_is_hardcoded(audit):
    rows = audit["rl"]["energy_curve"]
    assert len({r["energy_savings"] for r in rows}) == 1
    assert audit["rl"]["S1_energy_sensitive"] is False
    assert all(abs(r["allocation_sum"] - 1.0) > 1e-6 for r in rows)  # 0.8+0.3=1.1
    assert audit["rl"]["S8_allocation_sum_ok"] is False


def test_rl_optimizer_history_has_no_progress(audit):
    runs = audit["rl"]["optimize_runs"]
    assert runs and all(r["distinct_rewards"] == 1 for r in runs)
    assert audit["rl"]["S7_history_has_progress"] is False


def test_recorded_verdicts_match_recompute(audit):
    recomputed = verdicts(audit)
    recorded = {r["endpoint"]: r for r in audit["verdicts"]}
    assert set(recorded) == {r["endpoint"] for r in recomputed}
    for row in recomputed:
        assert recorded[row["endpoint"]]["grade"] == row["grade"]
    assert recorded["/pinn/resistance"]["grade"] == "MOCK"
    assert recorded["/pinn/seakeeping"]["grade"] == "MOCK"
    assert recorded["/transformer/resistance"]["grade"] == "MOCK"
    assert recorded["/rl/*"]["grade"] == "MOCK"
    assert recorded["/vibration/predict"]["grade"] == "WITHHELD"


# --------------------------------------------------------------------------
# 2. 裁决逻辑层：合成证据
# --------------------------------------------------------------------------

def _clean_evidence():
    return {
        "pinn_resistance": {"reachable": True, "S1_ignored_inputs": [], "S3_monotone": True,
                            "S4_worst_rel_err_pct": 3.0, "S5_data_loss_frozen": False,
                            "S5_physics_loss_frozen": False},
        "pinn_seakeeping": {"reachable": True, "S1_heading_sensitive": True,
                            "S6_heave_pitch_distinct": True, "S6_heave_rao_max": 1.2,
                            "S6_physics_loss_distinct": 4},
        "transformer_resistance": {"reachable": True, "S4_worst_rel_err_pct": 5.0,
                                   "S1_geometry_sensitive": True},
        "rl": {"reachable": True, "S1_energy_sensitive": True, "S8_allocation_sum_ok": True,
               "S7_history_has_progress": True},
        "vibration_predict": {"post_without_ship_id": {"status": 200}},
    }


def test_verdict_logic_promotes_clean_evidence():
    rows = {r["endpoint"]: r for r in verdicts(_clean_evidence())}
    assert rows["/pinn/resistance"]["grade"] == "REAL"
    assert rows["/pinn/seakeeping"]["grade"] == "REAL"
    assert rows["/transformer/resistance"]["grade"] == "REAL"
    assert rows["/rl/*"]["grade"] == "REAL"
    assert not rows["/pinn/resistance"]["failing_criteria"]


def test_verdict_logic_degrades_on_single_failure():
    ev = _clean_evidence()
    ev["pinn_resistance"]["S4_worst_rel_err_pct"] = 16.0
    rows = {r["endpoint"]: r for r in verdicts(ev)}
    assert rows["/pinn/resistance"]["grade"] == "MOCK"
    assert "S4" in rows["/pinn/resistance"]["failing_criteria"][0]
    # 单点失效不污染其他端点
    assert rows["/pinn/seakeeping"]["grade"] == "REAL"


def test_unreachable_endpoint_is_stub_not_real():
    rows = {r["endpoint"]: r for r in verdicts({"pinn_resistance": {"reachable": False}})}
    assert rows["/pinn/resistance"]["grade"] == "STUB"


# --------------------------------------------------------------------------
# 3. 现网探针（服务不可达即 skip）
# --------------------------------------------------------------------------

def _service_alive() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8001/health", timeout=5):
            return True
    except Exception:
        return False


@pytest.mark.skipif(not _service_alive(), reason="ship service :8001 unreachable")
def test_live_pinn_ignores_displacement():
    base = dict(L=180.0, B=30.0, T=11.0, Cb=0.82, displacement=45000.0, speed=14.0,
                water_density=1025.0)
    vals = []
    for disp in (15000.0, 120000.0):
        body = json.dumps(dict(base, displacement=disp)).encode()
        req = urllib.request.Request("http://127.0.0.1:8001/api/v1/pinn/resistance",
                                     data=body, headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            vals.append(json.load(r)["resistance"])
    assert vals[0] == vals[1], "现网行为已变化：PINN 开始响应 displacement，请重跑核查"