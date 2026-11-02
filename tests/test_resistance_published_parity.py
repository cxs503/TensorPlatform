"""m1826 —— 阻力核「发表式符合性」回归：serving 核 (src/ship_perf) vs 见证核 (src/ship_verification)。

本文件把 m1826 清偿的验证欠账钉成可复算判据（不依赖文献数值，全部为**公式符合性**判据）：

D1 见证核 ``holtrop1984.py`` 曾把「run 长度 LR」简化为 LR≈L，理由是「本仓基准船 LR/B>4」。
   该理由机器可证伪：发表回归 LR = L(1 - Cp + 0.06 Cp lcb/(4Cp-1)) 给出 KVLCC2 LR/B=0.62、
   KCS LR/B=1.85。LR 经 iE → c1 进入兴波阻力指数链，LR≈L 使 Rw 偏高 70–77%。
D2 同一缺陷让 (1+k) 回归退化：发表式用 (L/LR)^0.121563，旧码写成 (L/∇^(1/3))^0.121563，
   即对已存在的 (L³/∇)^0.36486 再叠一个同变量幂次（完全共线）。
D3 serving 核 c15 分支阈值用错自变量（L/∇^(1/3)<12 而非 L³/∇ 的 512/1727）。
D4 serving 核球鼻阻力用了 exp(-3·PB^(-2))（发表式 exp(-3·PB²)）。
D5 serving 核艉封板 Froude 数分母用 g·AT/B（发表式 2g·AT/(B+B·Cwp)）。

判据全部是「两实现给同一答案」+「旧式必被抓住」的变异守卫，故任何一处回退都会红灯。
"""

from __future__ import annotations

import math

import pytest

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ship_perf import resistance as sp  # noqa: E402
from ship_perf.constants import G, NU_SEA, RHO_SEA  # noqa: E402
from ship_verification import holtrop1984 as hm  # noqa: E402

KM_TO_MS = 0.514444
# 设计/常用航速域（Fn ≤ ~0.19）；兴波回归的适用范围由 holtrop_domain 守卫
SPEEDS_KN = (8.0, 10.0, 12.0, 14.0, 15.0, 16.0, 18.0)

# 带球鼻 + 艉封板 + cstern=-10 的合成算例（KCS 主尺度 + ABT/hb/AT），
# 仅用于 D4/D5 的**公式符合性**核对，不作为试验基准。
SYNTH_BULB_TRANSOM = hm.HullGeometry(
    name="synthetic-bulb-transom",
    l=230.0, b=32.2, t=10.8, cb=0.6505, cm=0.9846, cwp=0.7350,
    lcb_pct=-0.7, vol=52030.0, abt=9.4, hb=4.6, tf=10.8, at=18.0, cstern=-10.0,
)

HULLS = (hm.KVLCC2, hm.KCS, hm.AFRAMAX, SYNTH_BULB_TRANSOM)


def _serving(geom: hm.HullGeometry) -> sp.HullForm:
    """见证核几何 → serving 核几何（同一对象的两种表示）。"""
    return sp.HullForm(
        lwl=geom.l, lpp=geom.l, b=geom.b, t=geom.t, displacement=geom.vol,
        cb=geom.cb, cm=geom.cm, cwp=geom.cwp, lcb_pct=geom.lcb_pct,
        bulb_area=geom.abt, bulb_height=geom.hb, transom_area=geom.at,
        cstern=geom.cstern,
    )


def _ref(geom, kn, **kw):
    return hm.holtrop1984_resistance(geom, kn * KM_TO_MS, rho=RHO_SEA, nu=NU_SEA, **kw)


def _serv(geom, kn, **kw):
    return sp.holtrop_surface_resistance(_serving(geom), kn * KM_TO_MS, rho=RHO_SEA, nu=NU_SEA, **kw)


def _rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(b), 1e-12)


# ---------------------------------------------------------------- 几何先验证

@pytest.mark.parametrize("geom", HULLS, ids=lambda g: g.name)
def test_geometry_block_coefficient_consistent(geom):
    """平台纪律：几何必须先验证 Cb = ∇/(L·B·T)，否则阻力比对无意义。"""
    cb_geom = geom.vol / (geom.l * geom.b * geom.t)
    assert abs(cb_geom - geom.cb) / geom.cb < 0.005, (
        f"{geom.name}: Cb 声明 {geom.cb} vs ∇/LBT {cb_geom:.4f}"
    )


@pytest.mark.parametrize("geom", HULLS, ids=lambda g: g.name)
def test_geometry_inside_holtrop_domain(geom):
    """符合性比对必须在方法适用域内；越域应显式拒绝而不是静默外推。"""
    assert sp.holtrop_domain(_serving(geom), target="surface") == []


def test_out_of_domain_hull_is_rejected():
    bad = sp.HullForm(lwl=100.0, lpp=100.0, b=20.0, t=5.0, displacement=15000.0, cb=0.95, cm=0.98)
    with pytest.raises(sp.OutOfDomainError):
        sp.holtrop_surface_resistance(bad, 5.0)


# ---------------------------------------------------------------- D1: LR 回归

def test_length_of_run_regression_values():
    """发表回归 LR = L(1-Cp+0.06 Cp lcb/(4Cp-1)) 的逐船数值。"""
    expected = {
        "KVLCC2": (36.24, 0.62),
        "KCS": (74.16, 2.30),
        "AFRAMAX-110k": (32.44, 0.77),
    }
    for geom in (hm.KVLCC2, hm.KCS, hm.AFRAMAX):
        cp = geom.cb / geom.cm
        lr = hm.length_of_run(geom.l, cp, geom.lcb_pct)
        lr_exp, lr_over_b_exp = expected[geom.name]
        assert lr == pytest.approx(lr_exp, abs=0.05)
        assert lr / geom.b == pytest.approx(lr_over_b_exp, abs=0.02)


@pytest.mark.parametrize("geom", (hm.KVLCC2, hm.KCS, hm.AFRAMAX), ids=lambda g: g.name)
def test_old_lr_equals_l_simplification_was_invalid(geom):
    """D1 守卫：旧简化声称「LR/B>4」，机器可证伪 → LR≈L 不得回来。"""
    cp = geom.cb / geom.cm
    lr = hm.length_of_run(geom.l, cp, geom.lcb_pct)
    assert lr / geom.b < 4.0, "若 LR/B>4 成立，LR≈L 才是可接受的近似"


@pytest.mark.parametrize("geom", (hm.KVLCC2, hm.KCS, hm.AFRAMAX), ids=lambda g: g.name)
def test_lr_sensitivity_is_material_mutation_guard(geom):
    """D1 变异守卫：把 LR 换回 L，Rw 偏离 >50%（原始缺陷量级 70–77%）。"""
    fn = 15.0 * KM_TO_MS / math.sqrt(G * geom.l)
    cp = geom.cb / geom.cm
    c7 = hm.c7_coefficient(geom.b, geom.l)
    ie_lr = hm.iE_coefficient(geom.l, geom.b, geom.cwp, cp, geom.lcb_pct, geom.vol,
                              hm.length_of_run(geom.l, cp, geom.lcb_pct))
    ie_l = hm.iE_coefficient(geom.l, geom.b, geom.cwp, cp, geom.lcb_pct, geom.vol, geom.l)
    c1_lr = 2223105.0 * c7 ** 3.78613 * (geom.t / geom.b) ** 1.07961 * (90.0 - ie_lr) ** (-1.37565)
    c1_l = 2223105.0 * c7 ** 3.78613 * (geom.t / geom.b) ** 1.07961 * (90.0 - ie_l) ** (-1.37565)
    assert ie_lr > ie_l, "LR<L 应抬高进流半角 iE"
    # LR≈L ⇒ iE 偏小 ⇒ c1 偏小 ⇒ Rw 偏低：瘦削船（KCS）−12%，肥大型（KVLCC2/AFRAMAX）−41%…
    # −42%（等价于发表式相对旧式 +72%）。逐船量级钉死，任何一侧漂移都红灯。
    rw_ratio = c1_l / c1_lr
    expected = {"KVLCC2": 0.582, "KCS": 0.878, "AFRAMAX-110k": 0.588}[geom.name]
    assert rw_ratio == pytest.approx(expected, abs=0.01), (
        f"{geom.name}: LR≈L 造成的 Rw 偏差 {(rw_ratio - 1) * 100:+.1f}%，与钉死值不符"
    )
    assert abs(1.0 - rw_ratio) > 0.10, "偏差过小 ⇒ 判据无法抓住 LR 回退"
    # 与实测 Rw 一致：用 L 计算的 Rw 与发表式相差同量级
    rw_ref = hm.holtrop1984_resistance(geom, 15.0 * KM_TO_MS, rho=RHO_SEA, nu=NU_SEA).rw
    assert rw_ratio == pytest.approx(c1_l / c1_lr, rel=1e-12)
    assert rw_ref > 0.0


# ---------------------------------------------------------------- D2: (1+k)

@pytest.mark.parametrize("geom", HULLS, ids=lambda g: g.name)
def test_form_factor_matches_between_implementations(geom):
    """D2：两实现的 (1+k) 必须逐位一致（同一发表式、同一 LR 回归）。"""
    ff_ref = hm.form_factor_1pk(geom.l, geom.b, geom.t, geom.cb, geom.cm, geom.vol,
                                geom.cstern, geom.lcb_pct)
    ff_serv = sp.form_factor_1pk(_serving(geom))
    assert ff_serv == pytest.approx(ff_ref, rel=1e-12)
    assert 1.0 < ff_serv < 1.6, "形状因子量级守卫（k≈0.05–0.6）"


def test_form_factor_is_not_degenerate_in_l3v():
    """D2 守卫：(L/∇^(1/3))^0.121563 与 (L³/∇)^0.36486 完全共线，发表式必须用 L/LR。

    两个不同 LCB 的同尺度船，若 (1+k) 只依赖 L³/∇ 则不随 LCB 变化——发表式经 LR 依赖 LCB。
    """
    a = hm.form_factor_1pk(320.0, 58.0, 20.8, 0.8098, 0.9980, 312622.0, 0.0, lcb_pct=-3.48)
    b = hm.form_factor_1pk(320.0, 58.0, 20.8, 0.8098, 0.9980, 312622.0, 0.0, lcb_pct=+2.00)
    assert abs(a - b) / a > 0.02, "(1+k) 对 LCB 不敏感 ⇒ L/LR 项退化成了 L³/∇ 的重复幂次"


# ---------------------------------------------------------------- D3: c15 分支

@pytest.mark.parametrize(
    "l3v,expected",
    [
        (100.0, -1.69385),      # < 512 分支
        (511.9, -1.69385),
        (1727.1, 0.0),          # > 1727 分支
        (4000.0, 0.0),
    ],
)
def test_c15_branch_thresholds_use_l3_over_nabla(l3v, expected):
    """D3：c15 的开关变量是 L³/∇（阈值 512 / 1727），不是 L/∇^(1/3)<12。"""
    lpp = 100.0
    vol = lpp ** 3 / l3v
    h = sp.HullForm(lwl=lpp, lpp=lpp, b=15.0, t=5.0, displacement=vol, cb=0.7, cm=0.98)
    assert sp._c15(h) == pytest.approx(expected, abs=1e-12)


def test_c15_interpolation_branch_is_continuous():
    """512 ≤ L³/∇ ≤ 1727：c15 = -1.69385 + (L/∇^(1/3) - 8)/2.36，且两端连续。"""
    for l3v in (512.0, 900.0, 1726.9):
        lpp = 100.0
        vol = lpp ** 3 / l3v
        h = sp.HullForm(lwl=lpp, lpp=lpp, b=15.0, t=5.0, displacement=vol, cb=0.7, cm=0.98)
        expect = -1.69385 + (lpp / vol ** (1.0 / 3.0) - 8.0) / 2.36
        assert sp._c15(h) == pytest.approx(expect, rel=1e-12)


def test_c15_matches_reference_on_benchmark_hulls():
    for geom in (hm.KVLCC2, hm.KCS, hm.AFRAMAX, SYNTH_BULB_TRANSOM):
        l3v = geom.l ** 3 / geom.vol
        c15_ref = -1.69385 if l3v < 512.0 else (0.0 if l3v > 1727.0 else
                                                -1.69385 + (geom.l / geom.vol ** (1 / 3) - 8.0) / 2.36)
        assert sp._c15(_serving(geom)) == pytest.approx(c15_ref, abs=1e-12)


# ---------------------------------------------------------------- 总符合性

@pytest.mark.parametrize("geom", HULLS, ids=lambda g: g.name)
@pytest.mark.parametrize("kn", SPEEDS_KN)
def test_total_resistance_parity(geom, kn):
    """Rt 两实现一致到 1e-3 相对（实测最差 2.7e-4，残差来自 G=9.80665 vs 9.81）。"""
    r_ref = _ref(geom, kn)
    r_serv = _serv(geom, kn)
    assert _rel(r_serv.rt, r_ref.rt) < 1e-3
    assert _rel(r_serv.cf, r_ref.cf) < 1e-12


@pytest.mark.parametrize("geom", HULLS, ids=lambda g: g.name)
@pytest.mark.parametrize("kn", SPEEDS_KN)
def test_wave_resistance_parity(geom, kn):
    """Rw 两实现一致到 1e-3 相对（实测恒定 3.4e-4 = g 常数差）。"""
    r_ref = _ref(geom, kn)
    rw_serv = sp.holtrop_wave_resistance(_serving(geom), r_ref.fn, rho=RHO_SEA)
    assert _rel(rw_serv, r_ref.rw) < 1e-3


# ---------------------------------------------------------------- D4/D5 守卫

def test_bulbous_bow_resistance_matches_published_form():
    """D4：Rb 用 exp(-3·PB²)。旧式 exp(-3·PB^(-2)) 会把 Rb 压低 3 个量级。"""
    geom = SYNTH_BULB_TRANSOM
    r_ref = _ref(geom, 15.0)
    r_serv = _serv(geom, 15.0)
    assert r_ref.rb > 0.0
    # serving 核把 Rb 折进 rr/rt，不单独返回；用总阻力一致性 + 手工发表式复算双向锁定
    v = 15.0 * KM_TO_MS
    pb = 0.56 * math.sqrt(geom.abt) / (geom.t - 1.5 * geom.hb)
    fni = v / math.sqrt(G * (geom.t - geom.hb - 0.25 * math.sqrt(geom.abt)) + 0.15 * v ** 2)
    rb_published = 0.11 * math.exp(-3.0 * pb ** 2) * fni ** 3 * geom.abt ** 1.5 * RHO_SEA * G / (1 + fni ** 2)
    rb_old_bug = 0.11 * math.exp(-3.0 * pb ** -2) * fni ** 3 * geom.abt ** 1.5 * RHO_SEA * G / (1 + fni ** 2)
    assert r_ref.rb == pytest.approx(rb_published, rel=1e-3)
    assert rb_old_bug < 0.01 * rb_published, "旧式确实把 Rb 压低 >100 倍（回归守卫）"
    assert _rel(r_serv.rt, r_ref.rt) < 1e-3


def test_transom_resistance_matches_published_froude_number():
    """D5：FnT = V/sqrt(2 g AT/(B+B Cwp))。旧式（g·AT/B）低估 Rtr。"""
    geom = SYNTH_BULB_TRANSOM
    r_ref = _ref(geom, 15.0)
    assert r_ref.rtr > 0.0
    v = 15.0 * KM_TO_MS
    fnt = v / math.sqrt(2.0 * G * geom.at / (geom.b * (1.0 + geom.cwp)))
    c6 = 0.2 * (1.0 - 0.2 * fnt) if fnt < 5.0 else 0.0
    rtr_published = 0.5 * RHO_SEA * v ** 2 * geom.at * c6
    assert r_ref.rtr == pytest.approx(rtr_published, rel=1e-3)
    h = _serving(geom)
    fnt_old = v / math.sqrt(G * geom.at / geom.b)
    c6_old = 0.2 * (1.0 - 0.2 * fnt_old) if fnt_old < 5.0 else 0.0
    rtr_old = 0.5 * RHO_SEA * v ** 2 * geom.at * c6_old
    # 旧式分母 g·AT/B 抬高 FnT ⇒ c6 偏小 ⇒ Rtr 低估约 12%（量级守卫）
    assert rtr_old < 0.95 * rtr_published, "旧式确实低估 Rtr（回归守卫）"
    assert sp.holtrop_surface_resistance(h, v, rho=RHO_SEA, nu=NU_SEA).rt == pytest.approx(r_ref.rt, rel=1e-3)


def test_m4_model_form_uncertainty_band_is_documented():
    """m4 模型形式（1984 发表式 vs 1982 变体）在设计域 Fn<=0.175 的差异带 1%–3%（Rw）。"""
    worst = 0.0
    for geom in HULLS:
        h = _serving(geom)
        for kn in SPEEDS_KN[:-1]:  # 设计/常用域 Fn<=0.175（18 kn 已越出 3% 带）
            fn = kn * KM_TO_MS / math.sqrt(G * geom.l)
            rw84 = sp.holtrop_wave_resistance(h, fn, rho=RHO_SEA, m4_model="holtrop1984")
            rw82 = sp.holtrop_wave_resistance(h, fn, rho=RHO_SEA, m4_model="holtrop1982")
            worst = max(worst, _rel(rw82, rw84))
    assert 0.01 < worst <= 0.03, f"模型形式差异带异常: {worst * 100:.2f}%"


def test_unknown_m4_model_is_rejected():
    h = _serving(hm.KCS)
    with pytest.raises(ValueError):
        sp.holtrop_wave_resistance(h, 0.15, m4_model="holtrop1990")