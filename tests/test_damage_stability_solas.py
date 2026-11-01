"""
破舱稳性域修复验收测试：SOLAS 2009 Reg.6 / Reg.7.2（F1~F5）
=============================================================
承接验证报告 id=1093（判决 MOCK）与 mission 1507 的 FIX-DAMAGE F1~F5。

判据（机器可裁）：
  F1  R(Ls) 与 Reg.6 闭式解偏差 < 0.1%，且不等于旧式 min(0.5Ls/100+0.5, 0.9)
  F2  s 恒等于 Reg.7.2 公式作用于**本次输出**的 GZmax/Range；请求体直传 s 被忽略；
      s 在中间带内连续（无二值跳变）
  F3  终吃水对损失浮力解析解 t'=nabla0/(Aw-mu*Ac) 偏差 < 3%；
      破损范围放大 10 倍必须改变输出（7 项逐项对比）
  F4  survival_probability 不再是二值（1.0/0.5），并恒等物理 s；不随 imo_compliant 翻转
  F5  衡准集对齐 Reg.7.2，旧的 freeboard>0.3 m 降级为诊断项

测试用独立闭式解复核，不复用被测实现的中间量（避免自证）。
"""
import logging  # noqa: F401  必须先生成 stdlib logging，避免 backend/api/logging.py 遮蔽
import importlib.util
import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_MODULE_PATH = ROOT / "backend" / "api" / "damage_stability.py"

_spec = importlib.util.spec_from_file_location("damage_stability_m1507", _MODULE_PATH)
ds = importlib.util.module_from_spec(_spec)
sys.modules["damage_stability_m1507"] = ds
_spec.loader.exec_module(ds)

RHO = 1.025


# ============================ 公共夹具 ============================

def _barge(**over):
    """盒式驳船 L100/B20/T6/D9，Cb=1（解析解基准工况）。"""
    base = dict(L=100.0, B=20.0, T=6.0, D=9.0, Cb=1.0,
                displacement=100.0 * 20.0 * 6.0 * RHO,
                LCG=50.0, TCG=0.0, VCG=4.0)
    base.update(over)
    return base


def _tank(name="C1", volume=2000.0, lcg=30.0, tcg=8.0, vcg=3.0, mu=0.95,
          length=16.6667, breadth=20.0, height=6.0):
    return dict(name=name, volume=volume, LCG=lcg, TCG=tcg, VCG=vcg,
                permeability=mu, location="stbd",
                length=length, breadth=breadth, height=height)


def _ship(ship_kw, comps):
    return ds.ShipData(**{**ship_kw,
                          "compartments": [ds.Compartment(**c) for c in comps]})


def _analyze(ship_kw, comps, damaged, extent):
    ship = _ship(ship_kw, comps)
    inp = ds.DamageStabilityInput(
        ship=ship,
        damage=ds.DamageScenario(damaged_compartments=damaged, damage_extent=extent))
    return ds.DamageStabilityCalculator().analyze(inp)


def _analytic_draft(L, B, T, Cb, Vc, Hc, mu):
    """独立复算：损失浮力法平行下沉 t' = nabla0 / (Aw - mu*A_c)。

    nabla0 = Cb*L*B*T（Cb=1 时与平台壁式等效体积一致），A_c = Vc/Hc，
    Aw = Cw*L*B，Cw = (1+2Cb)/3。此为验证报告 R3 的闭式解。
    """
    Cw = (1.0 + 2.0 * Cb) / 3.0
    nabla0 = Cb * L * B * T
    Aw = Cw * L * B
    Ac = Vc / Hc
    return nabla0 / (Aw - mu * Ac)


# ============================ F1: Reg.6 的 R ============================

@pytest.mark.parametrize("Ls,expected", [
    (100.0, 1.0 - 128.0 / 252.0),
    (150.0, 1.0 - 128.0 / 302.0),
    (200.0, 1.0 - 128.0 / 352.0),
    (250.0, 1.0 - 128.0 / 402.0),
    (80.0, 1.0 - 128.0 / 232.0),
])
def test_f1_required_index_matches_solas_reg6(Ls, expected):
    """F1: 货船 Ls>=80 m，R = 1 - 128/(Ls+152)，偏差 < 0.1%。"""
    got = ds.solas2009_required_index(Ls, "cargo")
    assert abs(got - expected) / expected < 1e-3
    assert got == pytest.approx(expected, rel=1e-12)


def test_f1_required_index_short_ship_branch():
    """F1: 货船 Ls<80 m 分支 R = 1 - 152/(Ls+152)。"""
    for Ls in (40.0, 60.0, 79.9):
        expected = 1.0 - 152.0 / (Ls + 152.0)
        assert ds.solas2009_required_index(Ls) == pytest.approx(expected, rel=1e-12)


def test_f1_required_index_via_api_and_legacy_formula_removed():
    """F1: /probabilistic 返回的 R 用 Reg.6；旧式 min(0.5Ls/100+0.5,0.9) 已删除。"""
    comps = [_tank()]
    out = ds.DamageStabilityCalculator()  # noqa: F841  仅确认类可实例化
    ship = _ship(_barge(), comps)
    payload = ds.ProbabilisticInput(ship=ship, damage_zones=[
        {"name": "Z1", "probability": 1.0, "damaged_compartments": ["C1"],
         "damage_extent": {"length": 16.6667, "width": 20.0, "height": 6.0}},
    ])
    res = ds.ProbabilisticDamageStability().calculate_A(payload)
    legacy = min(0.5 * ship.L / 100.0 + 0.5, 0.9)
    assert res.R_value == pytest.approx(1.0 - 128.0 / (ship.L + 152.0), abs=1e-4)
    assert abs(res.R_value - legacy) / legacy > 0.30        # Ls=100 时旧值高 82.9%
    assert res.R_value < legacy


# ============================ F3: 破损几何进入计算 ============================

def test_f3_final_draft_matches_lost_buoyancy_analytic():
    """F3: 全破口时终吃水 = nabla0/(Aw - mu*Ac)，偏差 < 3%（并要求 < 0.1%）。"""
    L, B, T, Cb = 100.0, 20.0, 6.0, 1.0
    Vc, Hc, mu = 2000.0, 6.0, 0.95
    out = _analyze(_barge(), [_tank(mu=mu)],
                   ["C1"], {"length": 16.6667, "width": 20.0, "height": 6.0})
    ref = _analytic_draft(L, B, T, Cb, Vc, Hc, mu)
    rel = abs(out.final_draft - ref) / ref
    assert rel < 0.03, f"终吃水 {out.final_draft} vs 解析 {ref:.4f}，偏差 {rel:.2%}"
    assert rel < 1e-3                                  # 壁式截面积分 + 闭式解应逐位吻合
    assert out.final_draft == pytest.approx(7.1287, abs=2e-3)


def test_f3_final_draft_scales_with_permeability():
    """F3: mu 0.95 -> 0.50，终吃水按同一解析式下降（趋势与量值同时可裁）。"""
    ext = {"length": 16.6667, "width": 20.0, "height": 6.0}
    hi = _analyze(_barge(), [_tank(mu=0.95)], ["C1"], ext)
    lo = _analyze(_barge(), [_tank(mu=0.50)], ["C1"], ext)
    ref_hi = _analytic_draft(100.0, 20.0, 6.0, 1.0, 2000.0, 6.0, 0.95)
    ref_lo = _analytic_draft(100.0, 20.0, 6.0, 1.0, 2000.0, 6.0, 0.50)
    assert abs(hi.final_draft - ref_hi) / ref_hi < 0.03
    assert abs(lo.final_draft - ref_lo) / ref_lo < 0.03
    assert lo.final_draft < hi.final_draft


def test_f3_damage_extent_changes_outputs():
    """F3: 破损范围 x10（10x5x6 -> 100x20x60）必须改变输出（旧实现 7 项逐项不变）。"""
    small = _analyze(_barge(), [_tank()], ["C1"],
                     {"length": 10.0, "width": 5.0, "height": 6.0})
    large = _analyze(_barge(), [_tank()], ["C1"],
                     {"length": 100.0, "width": 20.0, "height": 60.0})
    keys = ["final_draft", "final_heel", "flooded_volume", "GM_damage",
            "GZ_max", "area_GZ", "final_trim"]
    changed = [k for k in keys if abs(getattr(small, k) - getattr(large, k)) > 1e-6]
    assert len(changed) >= 6, f"破损范围变化未进入计算，仅 {changed} 改变"
    assert large.flooded_volume > small.flooded_volume
    assert large.final_draft > small.final_draft


def test_f3_flooded_volume_bounded_by_compartment():
    """F3: 进水体积不超过 mu * 舱容（几何与舱容双重约束）。"""
    Vc, mu = 2000.0, 0.95
    out = _analyze(_barge(), [_tank()], ["C1"],
                   {"length": 50.0, "width": 50.0, "height": 50.0})
    assert out.flooded_volume == pytest.approx(mu * Vc, rel=1e-6)


# ============================ F2: Reg.7.2 生存概率 s ============================

def _s_index(gz_max, rng):
    """独立复算 Reg.7.2 中间阶段 s（不复用被测实现）。"""
    if gz_max < 0.1 or rng < 16.0:
        return 0.0
    if gz_max >= 0.2 and rng >= 20.0:
        return 1.0
    return min(1.0, ((gz_max / 0.12) * (rng / 16.0)) ** 0.25)


@pytest.mark.parametrize("scenario", [
    ("barge_small", _barge(), {"length": 5.0, "width": 5.0, "height": 3.0}),
    ("barge_mid", _barge(), {"length": 10.0, "width": 5.0, "height": 6.0}),
    ("barge_full", _barge(), {"length": 16.6667, "width": 20.0, "height": 6.0}),
])
def test_f2_s_is_computed_from_gz_curve(scenario):
    """F2: 输出的 s 必须等于 Reg.7.2 公式作用于本次 GZmax/Range 的结果。"""
    _, ship_kw, ext = scenario
    out = _analyze(ship_kw, [_tank()], ["C1"], ext)
    assert out.s_factor == pytest.approx(_s_index(out.GZ_max, out.GZ_range), abs=5e-4)
    assert out.s_factor != 0.5 or _s_index(out.GZ_max, out.GZ_range) == 0.5


def test_f2_s_ignores_request_body_survival():
    """F2: 请求体直传 survival（0.0/1.0）不得影响 A —— 旧实现是纯回声。"""
    ship = _ship(_barge(), [_tank()])
    ext = {"length": 10.0, "width": 5.0, "height": 6.0}
    mk = lambda sv: ds.ProbabilisticInput(ship=ship, damage_zones=[  # noqa: E731
        {"name": "Z1", "probability": 0.3, "survival": sv,
         "damaged_compartments": ["C1"], "damage_extent": ext}])
    a0 = ds.ProbabilisticDamageStability().calculate_A(mk(0.0))
    a1 = ds.ProbabilisticDamageStability().calculate_A(mk(1.0))
    assert a0.A_value == pytest.approx(a1.A_value, abs=1e-9)
    assert a0.input_s_ignored and a1.input_s_ignored
    # A 必须由物理量决定：与 0.3*s(GZ) 一致
    z = a0.zone_contributions[0]
    assert z["s"] == pytest.approx(a0.A_value / 0.3, abs=1e-6)


def test_f2_s_varies_with_ship_stability():
    """F2: 同一破损几何、换 KG（GZ 曲线变化）后 s 与 A 必须改变。"""
    ext = {"length": 20.0, "width": 8.0, "height": 6.0}
    ship_kw = dict(L=120.0, B=18.0, T=6.0, D=13.0, Cb=0.65,
                   displacement=0.65 * 120.0 * 18.0 * 6.0 * RHO,
                   LCG=60.0, TCG=0.0, VCG=3.0)
    comp = _tank(volume=20.0 * 8.0 * 6.0, lcg=60.0, tcg=3.0, vcg=3.0,
                 length=20.0, breadth=8.0, height=6.0)
    vals = []
    for KG in (3.0, 6.0):   # KG=6.0：GM<0 且 Range<16deg -> 按 Reg.7.2 s=0
        ship = _ship({**ship_kw, "VCG": KG}, [comp])
        payload = ds.ProbabilisticInput(ship=ship, damage_zones=[
            {"name": "Z1", "probability": 0.5, "damaged_compartments": ["C1"],
             "damage_extent": ext}])
        res = ds.ProbabilisticDamageStability().calculate_A(payload)
        vals.append((res.A_value, res.zone_contributions[0]["s"]))
    assert vals[0][1] > vals[1][1]           # KG 升高 -> GZ 变小 -> s 变小
    assert len({round(v[0], 6) for v in vals}) == 2


def test_f2_s_continuous_in_intermediate_band():
    """F2: 中间带（0.10<=GZmax<0.12, Range=17）内 s 连续、单调，无二值跳变。"""
    rng = 17.0
    xs = [0.10 + 0.001 * i for i in range(21)]
    ss = [ds.solas2009_s_index(x, rng) for x in xs]
    assert all(0.0 < s <= 1.0 for s in ss)
    assert all(b >= a for a, b in zip(ss, ss[1:]))          # 单调不减
    assert max(abs(b - a) for a, b in zip(ss, ss[1:])) < 0.01   # 连续、无跳变
    assert ss[0] != ss[-1]                                   # 确实随 GZ 变化
    assert 0.5 not in [round(s, 6) for s in ss]


def test_f2_model_level_intermediate_s():
    """F2: 模型层出现 0<s<1 的物理中间值（船 L120/B12/T6/D7.7/KG4.05 单舱破损）。"""
    ship_kw = dict(L=120.0, B=12.0, T=6.0, D=7.70, Cb=0.7,
                   displacement=0.7 * 120.0 * 12.0 * 6.0 * RHO,
                   LCG=60.0, TCG=0.0, VCG=4.05)
    comp = _tank(volume=8.0 * 4.0 * 4.0, lcg=70.0, tcg=0.5, vcg=2.0,
                 length=8.0, breadth=4.0, height=4.0)
    out = _analyze(ship_kw, [comp], ["C1"],
                   {"length": 8.0, "width": 4.0, "height": 4.0})
    assert 0.5 < out.s_factor < 1.0
    assert out.s_factor == pytest.approx(_s_index(out.GZ_max, out.GZ_range), abs=5e-4)
    assert 16.0 <= out.GZ_range < 20.0


# ============================ F4: 删除二值 survival_probability ============================

def test_f4_survival_probability_is_physical_not_binary():
    """F4: survival_probability 恒等 s_factor，取值非 {1.0,0.5} 二值。"""
    ext_small = {"length": 5.0, "width": 5.0, "height": 3.0}
    ext_mid = {"length": 10.0, "width": 5.0, "height": 6.0}
    got = []
    for ext in (ext_small, ext_mid):
        out = _analyze(_barge(), [_tank()], ["C1"], ext)
        assert out.survival_probability == pytest.approx(out.s_factor, abs=1e-9)
        got.append(out.survival_probability)
    # 高稳性工况（s=1）与中间带工况（0<s<1）分别给出另外两个取值
    ship_kw = dict(L=120.0, B=18.0, T=6.0, D=13.0, Cb=0.65,
                   displacement=0.65 * 120.0 * 18.0 * 6.0 * RHO,
                   LCG=60.0, TCG=0.0, VCG=3.0)
    comp_hi = _tank(volume=960.0, lcg=60.0, tcg=3.0, vcg=3.0,
                    length=20.0, breadth=8.0, height=6.0)
    got.append(_analyze(ship_kw, [comp_hi], ["C1"],
                        {"length": 20.0, "width": 8.0, "height": 6.0}).survival_probability)
    # 中间带工况（见 test_f2_model_level_intermediate_s）给出第三个不同取值
    ship_kw = dict(L=120.0, B=12.0, T=6.0, D=7.70, Cb=0.7,
                   displacement=0.7 * 120.0 * 12.0 * 6.0 * RHO,
                   LCG=60.0, TCG=0.0, VCG=4.05)
    comp = _tank(volume=128.0, lcg=70.0, tcg=0.5, vcg=2.0,
                 length=8.0, breadth=4.0, height=4.0)
    got.append(_analyze(ship_kw, [comp], ["C1"],
                        {"length": 8.0, "width": 4.0, "height": 4.0}).survival_probability)
    assert len({round(v, 4) for v in got}) >= 3, got


def test_f4_survival_not_driven_by_imo_compliant():
    """F4: 旧实现 s=1.0 if imo_compliant else 0.5；现要求不合规工况也能有高 s。"""
    ship_kw = dict(L=120.0, B=12.0, T=6.0, D=13.0, Cb=0.65,
                   displacement=0.65 * 120.0 * 18.0 * 6.0 * RHO,
                   LCG=60.0, TCG=0.0, VCG=4.6)
    comp = _tank(volume=20.0 * 8.0 * 6.0, lcg=60.0, tcg=3.0, vcg=3.0,
                 length=20.0, breadth=8.0, height=6.0)
    ship_kw["B"] = 18.0
    out = _analyze(ship_kw, [comp], ["C1"],
                   {"length": 20.0, "width": 8.0, "height": 6.0})
    assert out.imo_compliant is False           # 横倾 > 7 deg 不合规
    assert out.s_factor > 0.9                   # 但 s 仍由 GZ 决定（旧实现会给 0.5）
    assert out.survival_probability != 0.5


# ============================ F5: 衡准集对齐 Reg.7 ============================

def test_f5_criteria_aligned_with_reg7():
    """F5: 衡准集为 Reg.7.2 五项，且每项标注规范出处。"""
    out = _analyze(_barge(), [_tank()], ["C1"],
                   {"length": 10.0, "width": 5.0, "height": 6.0})
    solas = {k: v for k, v in out.imo_criteria.items() if not k.startswith("_")}
    assert set(solas) == {"heel_angle", "GZ_max", "GZ_range",
                          "openings_immersion", "s_factor"}
    for k, v in solas.items():
        assert "Reg.7.2" in v["source"], k
        assert "passed" in v
    # Reg.7.2 阈值
    assert solas["heel_angle"]["required"] == 7.0
    assert solas["GZ_max"]["required"] == 0.10
    assert solas["GZ_range"]["required"] == 16.0
    # 旧的非 SOLAS 判据降级为诊断项，不参与合规
    diag = out.imo_criteria["_diagnostics"]
    assert diag["freeboard_legacy"]["source"].startswith("NON-SOLAS")
    assert "freeboard" not in solas
    assert out.imo_compliant == all(v["passed"] for v in solas.values())


def test_f5_imo_criteria_endpoint_payload():
    """F5: GET /imo-criteria 的衡准表同样对齐 Reg.7.2（异步端点直接调用）。"""
    import asyncio
    payload = asyncio.run(ds.get_imo_criteria())
    keys = {c["key"] for c in payload["criteria"]}
    assert keys == {"heel_angle", "GZ_max", "GZ_range",
                    "openings_immersion", "s_factor"}
    assert "Reg.7.2" in payload["regulation"] or "Reg.6" in payload["regulation"]
    deprecated = {d["name"] for d in payload["deprecated_non_solas"]}
    assert "freeboard" in deprecated
    assert all("0.3 m" not in c["requirement"] for c in payload["criteria"])


# ============================ 几何预检 ============================

def test_geometry_precheck_flags_inconsistent_input():
    """平台精度标准：Cb/L/B 几何必须可校验，异常输入给出告警而非静默。"""
    good = _analyze(_barge(), [_tank()], ["C1"],
                    {"length": 10.0, "width": 5.0, "height": 6.0})
    assert good.geometry_check["passed"] is True
    bad = _analyze(_barge(displacement=100.0 * 20.0 * 6.0 * RHO * 2.0), [_tank()],
                   ["C1"], {"length": 10.0, "width": 5.0, "height": 6.0})
    assert bad.geometry_check["passed"] is False
    assert bad.geometry_check["relative_deviation"] > 0.15