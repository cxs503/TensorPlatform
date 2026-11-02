"""m1868 · 阻力残余项锚点审计的机器判据（pytest 门）。

两类测试：
 1. **锚点逻辑自体可裁**：对合成的 live 字典做正/负向（变异）检查，
    保证 A1/A3/A4 不是「恒真式」——篡改输入必须被抓到。
 2. **台账结论被钉住**：读 artifacts/m1868/resistance_anchor.json，
    验证三路分歧、低 Fr 判据的区分力、以及文档数字确实由台账注入。

台账由 `python3 -m tools.audit.resistance_anchor_m1868` 生成（毫秒级、纯 CPU）；
若端点不可用，台账仍可离线复判（`--offline`）。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.audit.resistance_anchor_m1868 import (  # noqa: E402
    RHO_SEA, anchor_additivity, anchor_cf, anchor_coeff_scale, anchor_form_factor,
    anchor_roughness, cf_ittc57,
)

LEDGER = ROOT / "artifacts" / "m1868" / "resistance_anchor.json"
DOC = ROOT / "docs" / "audit" / "m1868_residual_resistance_anchor.md"

LIVE_DESIGN = dict(rt=5513.5, rf=1517.02, rr=3996.48, cf=0.001401, cr=0.004744,
                   ct=0.006545, cf_total=0.001801, dcf=0.0004, speed_ms=7.7166666667,
                   wetted_surface=27603.33, reynolds_number=2078038744.42)
SHIP = dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098, displacement=320437.0, speed=15.0)


@pytest.fixture(scope="module")
def ledger() -> dict:
    if not LEDGER.exists():
        pytest.skip(f"台账缺失：先跑 python3 -m tools.audit.resistance_anchor_m1868（缺 {LEDGER}）")
    return json.loads(LEDGER.read_text())


# ── 1. 锚点逻辑：正向 + 变异（负向）检查 ──────────────────────
def test_cf_anchor_accepts_reported_line():
    out = anchor_cf(LIVE_DESIGN, SHIP)
    assert out["passed"], out
    assert out["rel_err"] < 1e-3


def test_cf_anchor_rejects_perturbed_cf():
    """变异：CF 偏 3% 必须被抓到（否则 A1 是恒真式）。"""
    bad = dict(LIVE_DESIGN, cf=LIVE_DESIGN["cf"] * 1.03)
    assert anchor_cf(bad, SHIP)["passed"] is False


def test_additivity_anchor_is_exact_and_mutation_sensitive():
    assert anchor_additivity(LIVE_DESIGN)["passed"]
    bad = dict(LIVE_DESIGN, rt=LIVE_DESIGN["rt"] * 1.2)
    assert anchor_additivity(bad)["passed"] is False


def test_coefficient_scale_anchor_uses_kilonewtons_and_catches_mutation():
    out = anchor_coeff_scale(LIVE_DESIGN)
    assert out["passed"], out
    # 单位若是 N 而不是 kN，误差会到 ~999 倍 —— 钳住这个历史坑
    assert out["worst_rel_err"] < 1e-3
    bad = dict(LIVE_DESIGN, rt=LIVE_DESIGN["rt"] * 2.0)
    assert anchor_coeff_scale(bad)["passed"] is False


def test_roughness_anchor_includes_ittc78_increment():
    assert anchor_roughness(LIVE_DESIGN)["passed"]
    # 去掉 dcf 必须失败：证明 rf 里确实含粗糙度增量
    bad = dict(LIVE_DESIGN, dcf=0.0)
    assert anchor_roughness(bad)["passed"] is False


def test_form_factor_anchor_brackets_full_hull_band():
    out = anchor_form_factor(LIVE_DESIGN)
    assert out["passed"] and 1.25 <= out["one_plus_k"] <= 1.55, out


def test_cf_ittc57_reference_value():
    """ITTC-57 数值锚点：Re=2.0780e9 时 CF≈1.4006e-3（解析式，独立于被测实现）。"""
    assert cf_ittc57(2.0780387444248085e9) == pytest.approx(1.4006e-3, rel=1e-4)


# ── 2. 台账结论（钉住发现）────────────────────────────────
def test_ledger_verdict_map(ledger):
    v = ledger["adjudication"]["verdicts"]
    # 粘性侧四项必须干净
    for key in ("A1", "A2", "A3", "A4", "A5"):
        assert v[key]["passed"] is True, (key, v[key])
    # 三路对撞必须不一致（本单的核心发现）
    assert v["A6"]["three_way_agree"] is False
    assert v["A6"]["passed"] is False
    # 低 Fr 判据：live 违反、参考实现满足 → 判据有区分力
    assert v["A8"]["passed"] is False
    assert v["A8"]["passed_reference_impl"] is True


def test_three_way_numbers_are_what_the_report_claims(ledger):
    a6 = ledger["design"]["A6"]
    assert a6["rt_live_kN"] == pytest.approx(5513.5, rel=1e-6)
    assert a6["rt_ref_kN"] == pytest.approx(1744.63, rel=1e-3)
    assert a6["rt_gate_kN"] == pytest.approx(2500.0)
    assert a6["err_live_vs_gate_pct"] == pytest.approx(120.54, abs=0.05)
    assert a6["err_ref_vs_gate_pct"] == pytest.approx(30.21, abs=0.05)
    # live 与参考实现的分歧远大于平台 15% 门槛
    assert a6["err_live_vs_ref_pct"] > 15.0


def test_residual_split_localises_the_defect(ledger):
    a7 = ledger["design"]["A7"]
    # 分歧全部落在剩余项：CT 差 3 倍，粘性项占比接近
    assert a7["ct_ratio"] == pytest.approx(3.05, abs=0.05)
    assert a7["live_res_share"] > 0.6
    assert a7["ref_res_share"] < 0.2
    assert a7["live_res_share"] / a7["ref_res_share"] > 4.0


def test_low_froude_anchor_has_teeth(ledger):
    a8 = ledger["A8"]
    # live 的 RR/RF 在 Fr→0 时几乎不塌缩 → 其 rr 不是造波项
    assert a8["live"]["collapse"] < 2.0
    # 参考实现塌缩 ≥5× → 行为符合兴波阻力物理
    assert a8["ref"]["collapse"] >= 5.0


def test_reference_kernel_self_consistency(ledger):
    sc = ledger["ref_selfcheck"]
    assert sc["cf_rel_err"] < 1e-9, sc
    assert sc["rt_additivity_rel_err"] < 1e-9, sc


def test_docs_numbers_are_injected_from_ledger(ledger):
    """文档里的关键数字必须能回溯到台账（零手抄检查）。"""
    if not DOC.exists():
        pytest.skip("文档未渲染")
    text = DOC.read_text()
    a6 = ledger["design"]["A6"]
    assert f"{a6['rt_live_kN']:,.1f}" in text
    assert f"{a6['err_live_vs_gate_pct']:+.1f}" in text
    assert f"{(a6['rt_ref_kN']):,.1f}" in text
    assert "THREE_WAY_DIVERGENCE" in text


def test_sweep_is_monotone_in_speed(ledger):
    """RT 随航速单调递增（连这都破就是端点崩了，不是算法细节）。"""
    pts = [r for r in ledger["sweep"] if "error" not in r]
    assert len(pts) >= 5, pts
    v = [r["rt"] for r in pts]
    assert all(b > a for a, b in zip(v, v[1:])), v
    # 摩擦系数随 Re 单调下降（ITTC-57 的性质）
    assert all(b <= a for a, b in zip([r["cf"] for r in pts], [r["cf"] for r in pts][1:]))