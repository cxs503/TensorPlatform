"""推进域敞水性能验证的回归测试（Wageningen B 系列三方对账）

参考侧：tools/verification/propulsion/b_series_reference.py（真 Oosterveld 1975 多项式）
验证器：tools/verification/propulsion/verify_open_water.py（分级判据）

本文件锁三件事：
1. 参考实现自身满足物理不变量与文献带锚点（REAL 侧的守门）；
2. 三个被测对象的失效签名可被复现（MOCK 侧的守门）——若平台日后修复，相应
   用例会失败并提示「签名已消失，请重新判级」，避免验证结论悄悄失效；
3. 若线上端点可达，则实时复探一次，确认端点行为未变。
"""

from __future__ import annotations

import math
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERIFY_DIR = ROOT / "tools" / "verification" / "propulsion"
sys.path.insert(0, str(VERIFY_DIR))

import b_series_reference as ref  # noqa: E402
import verify_open_water as v  # noqa: E402

B4_70 = dict(P_D=1.0, Ae_Ao=0.70, Z=4)


# ---------------------------------------------------------------------------
# 1. 参考实现（REAL 侧守门）
# ---------------------------------------------------------------------------
def test_reference_has_full_oosterveld_tables():
    """真表规模：K_T 39 项、K_Q 47 项（伪表常见为凑数/缩放）。"""
    assert len(ref.CT_TERMS) == 39
    assert len(ref.CQ_TERMS) == 47
    assert ref.provenance()["reference"].startswith("Oosterveld")


def test_reference_kt_monotone_decreasing_in_J():
    kts = [ref.kt_kq(0.05 * i, **B4_70)[0] for i in range(1, 21)]
    assert all(kts[i] > kts[i + 1] for i in range(len(kts) - 1))


def test_reference_efficiency_below_ideal_disc_bound():
    """η0 必须严格小于 Rankine–Froude 上界。"""
    for i in range(2, 22):
        p = ref.open_water(0.05 * i, **B4_70)
        if p.eta0 is None:
            continue
        assert p.eta0 < p.eta_ideal, f"J={p.J} η0={p.eta0} ≥ η_ideal={p.eta_ideal}"


@pytest.mark.parametrize("anchor,band,tol", [
    ("KT_J0", 0.455, 0.02),
    ("J0", 1.06, 0.03),
    ("eta_max", 0.690, 0.03),
])
def test_reference_matches_literature_band(anchor, band, tol):
    """B4-70 P/D=1.0 的三个文献量级锚点（带校验，2–3% 容差）。"""
    got = {
        "KT_J0": ref.kt_kq(0.0, **B4_70)[0],
        "J0": ref.zero_thrust_J(**B4_70),
        "eta_max": max(ref.open_water(0.02 * i, **B4_70).eta0 or 0.0 for i in range(1, 60)),
    }[anchor]
    assert abs(got - band) / band <= tol, f"{anchor}: got {got:.4f}, band {band}±{tol * 100:.0f}%"


def test_reference_zero_thrust_point_consistency():
    """零推力点处 K_T 应为 0（二分收敛一致性）。"""
    J0 = ref.zero_thrust_J(**B4_70)
    assert abs(ref.kt_kq(J0, **B4_70)[0]) < 1e-4


# ---------------------------------------------------------------------------
# 2. 被测对象失效签名（MOCK 侧守门；修复后应重判）
# ---------------------------------------------------------------------------
def test_o1_serving_signature_is_fabricated():
    """线上端点：η0 = 10J/(2π) 截断 0.85、K_Q ≡ K_T/10 → 判级 MOCK。"""
    g = v.grade_o1(v.SERVING_SAMPLES)
    assert g["verdict"] == "MOCK", "线上端点已不再呈现 MOCK 签名——请重新判级并更新报告"
    # K_Q ≡ K_T/10 的常数比签名
    ratios = {round(s["KT"] / s["KQ"], 9) for s in v.SERVING_SAMPLES if s["KQ"]}
    assert ratios == {10.0}
    # η0 只由 J 决定（与 P/D、Z、Ae/Ao 无关）且被截断
    eta_by_J = {}
    for s in v.SERVING_SAMPLES:
        eta_by_J.setdefault(round(s["J"], 3), set()).add(round(s["eta0"], 4))
    assert len(eta_by_J[0.5]) == 1, "同一 J 下 η0 应恒等（输入被旁路）"
    # J>0：η0 恒等于 min(10J/2π, 0.85)（因 K_Q≡K_T/10 代入定义式）
    for j, es in eta_by_J.items():
        if j == 0:
            continue
        for e in es:
            assert abs(e - min(10 * j / (2 * math.pi), 0.85)) < 5e-4, f"J={j} η0={e} 不符合伪造式"
    # J=0（系柱）：η0 理论值为 0，端点给 0.5 —— 另一个人工常数
    assert eta_by_J[0.0] == {0.5}


def test_o1_violates_momentum_theory_bound():
    """端点工况中有多处 η0 > η_ideal（定理级不可能）。"""
    viol = [i for i in v.grade_o1(v.SERVING_SAMPLES)["items"] if i["id"] == "C3"][0]["violations"]
    assert len(viol) >= 2
    assert all(x["eta0"] > x["eta_ideal"] for x in viol)


def test_o2_repo_coefficient_table_is_scaled_duplicate():
    """仓内方法的「Oosterveld 表」= K_Q 表逐项 ×10，且求值给出负推力。"""
    ratios = [ct[0] / cq[0] for ct, cq in zip(v.O2_SUSPECT_CT_HEAD, v.O2_SUSPECT_CQ_HEAD)]
    assert max(ratios) - min(ratios) < 1e-6 and all(abs(r - 10.0) < 1e-9 for r in ratios)
    g = v.grade_o2()
    assert g["verdict"] == "MOCK"
    t2 = [i for i in g["items"] if i["id"] == "T2"][0]
    assert t2["got"] < 0 < t2["ref"], "伪表应给出符号错误的 K_T"


def test_o3_dataset_internal_inconsistency_pinpointed():
    """基准数据集 η0 列在 J=0.8 行与自身 K_T/K_Q 不自洽（录入错误）。"""
    g = v.grade_o3()
    d2 = [i for i in g["items"] if i["id"] == "D2"][0]
    assert d2["pass"] is False
    assert d2["got"]["J"] == 0.8
    assert abs(d2["got"]["rel_err_pct"]) > 15.0
    assert g["verdict"] == "MOCK"


def test_o4_propulsion_chain_reports_impossible_efficiency():
    """/calculations/propulsion 回报 η0>1、η_D>1 且反算 K_T 小两个数量级。"""
    g = v.grade_o4()
    assert g["verdict"] == "MOCK"
    assert all(r["eta0_reported"] > 1.0 for r in g["table"])
    assert all(r["etaD_reported"] > 1.0 for r in g["table"])
    assert all(r["Kt_implied"] < 0.01 for r in g["table"])
    # 内部乘法自洽 → 失效点在物理模型而非算术
    assert [i for i in g["items"] if i["id"] == "P6"][0]["pass"] is True


def test_verifier_artifacts_are_reproducible():
    """验证器能端到端跑通并写出 JSON 产物（无图依赖断言，避免 CI 缺 matplotlib 假红）。"""
    rc = v.main.__wrapped__ if hasattr(v.main, "__wrapped__") else None
    _ = rc
    out = ROOT / "artifacts" / "propulsion_verification" / "propulsion_ow_verification.json"
    assert out.exists(), "先跑一次 tools/verification/propulsion/verify_open_water.py 生成产物"


# ---------------------------------------------------------------------------
# 3. 线上实时复探（端点不可达则跳过）
# ---------------------------------------------------------------------------
def _endpoint_up(host: str = "127.0.0.1", port: int = 8001, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _endpoint_up(), reason="船舶平台 :8001 不可达，跳过实时复探")
def test_live_serving_endpoint_still_fabricated():
    got = v._live_probe(timeout=10.0)
    if got is None:
        pytest.skip("端点可达但返回体不合预期（可能已改接口）——需人工复核")
    assert v.grade_o1(got)["verdict"] == "MOCK", "线上端点行为已变，请重新判级"