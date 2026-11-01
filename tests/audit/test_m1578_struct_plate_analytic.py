"""m1578 结构域解析基准回归测试。

两层：
  1) 参考实现自检（无外部依赖）：闭式解须复现教科书/规范常数，
     否则后续"端点偏差"判定没有意义。
  2) 活体端点对照（需 SHIP_API_LIVE=1 且 :8001 可达，否则 skip）：
     - 已达标分支按 3% 门直接断言（防回归）；
     - 已登记缺陷（DEF-1/3/4/5）用 xfail(strict=False) 标记"正确行为"，
       端点修好后自动变 xpass，便于跟踪欠账清零。

运行：
    pytest tests/audit/test_m1578_struct_plate_analytic.py -q
    SHIP_API_LIVE=1 pytest tests/audit/test_m1578_struct_plate_analytic.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "audit_struct"))
import plate_reference as ref  # noqa: E402

MPA = 1e6
SHIP_API = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001")
LIVE = os.environ.get("SHIP_API_LIVE", "0") == "1"


# ─────────────────────── 1. 参考实现自检 ───────────────────────
def test_uniaxial_buckling_coefficient_matches_timoshenko_table():
    # Timoshenko & Gere (1961) Table 9.2：四边简支单轴受压 k(a/b)
    assert ref.k_uniaxial_simply_supported(1.0, 1.0)[0] == pytest.approx(4.0, rel=1e-12)
    assert ref.k_uniaxial_simply_supported(1.5, 1.0)[0] == pytest.approx(4.3403, rel=1e-4)
    assert ref.k_uniaxial_simply_supported(2.0, 1.0)[0] == pytest.approx(4.0, rel=1e-12)
    assert ref.k_uniaxial_simply_supported(0.5, 1.0)[0] == pytest.approx(6.25, rel=1e-12)
    # 半波数：a/b=2 走 2 半波
    assert ref.k_uniaxial_simply_supported(2.0, 1.0)[1] == 2


def test_elastic_buckling_stress_square_panel():
    # σ_cr = 4·π²E/(12(1-ν²))·(t/b)², E=206 GPa, ν=0.3, t=10 mm, b=1 m
    assert ref.sigma_cr(4.0, ref.E_STEEL, 0.01, 1.0, ref.NU_STEEL) / MPA == pytest.approx(74.474, rel=1e-3)
    # 与解析成比例：t² 与 1/b²
    assert ref.sigma_cr(4.0, ref.E_STEEL, 0.02, 1.0, ref.NU_STEEL) == pytest.approx(
        4 * ref.sigma_cr(4.0, ref.E_STEEL, 0.01, 1.0, ref.NU_STEEL), rel=1e-12)
    assert ref.sigma_cr(4.0, ref.E_STEEL, 0.01, 2.0, ref.NU_STEEL) == pytest.approx(
        ref.sigma_cr(4.0, ref.E_STEEL, 0.01, 1.0, ref.NU_STEEL) / 4, rel=1e-12)
    # 固支方板 k=6.97
    assert ref.sigma_cr(ref.k_uniaxial_clamped(1.0, 1.0), ref.E_STEEL, 0.01, 1.0,
                        ref.NU_STEEL) / MPA == pytest.approx(129.77, rel=1e-3)


def test_shear_buckling_coefficient_and_stress():
    # Timoshenko Table 9.3：简支板纯剪 k_s = 5.34 + 4(b/a)²
    assert ref.k_shear_simply_supported(1.0, 1.0) == pytest.approx(9.34, rel=1e-12)
    assert ref.k_shear_simply_supported(3.0, 1.0) == pytest.approx(5.34 + 4 / 9, rel=1e-12)
    assert ref.tau_cr(1.0, 1.0, ref.E_STEEL, 0.01, ref.NU_STEEL) / MPA == pytest.approx(173.9, rel=2e-3)


def test_johnson_ostenfeld_yield_interaction():
    sy = 235e6
    # 弹性域：σ_cr <= σ_y/2 → 恒等
    assert ref.johnson_ostenfeld(74.47e6, sy) == pytest.approx(74.47e6, rel=1e-12)
    # 弹塑域：σ_cr = 670.27 MPa → σ_u = σ_y(1-σ_y/(4σ_cr))
    assert ref.johnson_ostenfeld(670.27e6, sy) / MPA == pytest.approx(214.40, rel=1e-3)
    # 单调性：σ_u 随 σ_cr 单调不减，且不超过 σ_y
    seq = [ref.johnson_ostenfeld(s * MPA, sy) for s in (100, 200, 400, 800, 1600)]
    assert all(b >= a for a, b in zip(seq, seq[1:]))
    assert max(seq) <= sy * 1.0000001


def test_dnv_c203_sn_curve_reference_points():
    # 曲线 C (in air)：Δσ@2e6 = 91.93 MPa，拐点 N=1e7 处 Δσ = 53.83 MPa
    assert ref.dnv_c_design_stress(2e6) == pytest.approx(91.93, rel=2e-3)
    assert ref.dnv_c_design_stress(1e7) == pytest.approx(53.83, rel=5e-3)
    # 双斜率逆解自洽：life(design_stress(N)) == N
    for n in (1e5, 2e6, 9e6, 3e7, 2e8):
        assert ref.dnv_c_life(ref.dnv_c_design_stress(n) * MPA) == pytest.approx(n, rel=1e-6)
    # m=3 段（N < 1e7）斜率：Δσ 增大 1.2 倍 → 寿命降 1.2^3
    d1, d2 = 100.0, 120.0
    assert ref.dnv_c_life(d1 * MPA) < 1e7 and ref.dnv_c_life(d2 * MPA) < 1e7
    assert ref.dnv_c_life(d2 * MPA) / ref.dnv_c_life(d1 * MPA) == pytest.approx(
        (d1 / d2) ** 3, rel=1e-9)
    # m=5 段（N > 1e7）斜率
    e1, e2 = 30.0, 40.0
    assert ref.dnv_c_life(e1 * MPA) > 1e7 and ref.dnv_c_life(e2 * MPA) > 1e7
    assert ref.dnv_c_life(e2 * MPA) / ref.dnv_c_life(e1 * MPA) == pytest.approx(
        (e1 / e2) ** 5, rel=1e-9)


# ─────────────────────── 2. 活体端点对照 ───────────────────────
def _post(path, body, timeout=30.0):
    import json
    import urllib.error
    import urllib.request
    req = urllib.request.Request(SHIP_API + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return json.loads(f.read().decode())
    except urllib.error.HTTPError as e:
        return {"_http": e.code}
    except Exception as e:  # pragma: no cover - 服务不可达
        pytest.skip(f"ship API unreachable: {type(e).__name__}")


def _alive():
    """服务可达性探测（重试 + 放宽超时：审计机负载高时 openapi 也可能 >5s）。"""
    import time
    import urllib.request
    for _ in range(3):
        try:
            urllib.request.urlopen(SHIP_API + "/openapi.json", timeout=15)
            return True
        except Exception:
            time.sleep(1.0)
    return False


pytestmark_live = pytest.mark.skipif(not LIVE or not _alive(),
                                     reason="set SHIP_API_LIVE=1 and start :8001 to run live checks")

PLATE = "/api/v1/plate-stability/plate"
HULL = dict(L=320.0, B=58.0, T=20.8, D=30.0, displacement=320000.0, Cb=0.81,
            frame_spacing=4.0, plate_thickness=0.022, yield_stress=235e6)


def _plate(**kw):
    b = dict(a=1.0, b=1.0, t=0.01, E=ref.E_STEEL, nu=ref.NU_STEEL, sigma_y=235e6,
             edge_condition="simply_supported", axial_load_a=0.0, axial_load_b=0.0,
             shear_load=0.0, lateral_pressure=0.0)
    b.update(kw)
    return b


@pytestmark_live
@pytest.mark.parametrize("a,b", [(1.0, 1.0), (1.5, 1.0), (2.0, 1.0), (3.0, 1.0)])
def test_live_uniaxial_buckling_within_3pct(a, b):
    r = _post(PLATE, _plate(a=a, b=b))
    k, _ = ref.k_uniaxial_simply_supported(a, b)
    expect = ref.sigma_cr(k, ref.E_STEEL, 0.01, b, ref.NU_STEEL) / MPA
    assert ref.rel_err(r["critical_stress_a"], expect) < 0.03


@pytestmark_live
@pytest.mark.parametrize("a,b", [(1.0, 1.0), (2.0, 1.0), (3.0, 1.0)])
def test_live_shear_buckling_within_3pct(a, b):
    r = _post(PLATE, _plate(a=a, b=b))
    expect = ref.tau_cr(a, b, ref.E_STEEL, 0.01, ref.NU_STEEL) / MPA
    assert ref.rel_err(r["critical_shear"], expect) < 0.03


@pytestmark_live
@pytest.mark.parametrize("t", [0.02, 0.025, 0.03, 0.04])
def test_live_johnson_ostenfeld_ultimate_strength(t):
    r = _post(PLATE, _plate(t=t))
    sig = ref.sigma_cr(4.0, ref.E_STEEL, t, 1.0, ref.NU_STEEL)
    assert ref.rel_err(r["ultimate_strength"], ref.johnson_ostenfeld(sig, 235e6) / MPA) < 0.03


@pytestmark_live
@pytest.mark.parametrize("which", ["axial_load_a", "axial_load_b"])
def test_live_safety_factor_uses_same_direction_load(which):
    r = _post(PLATE, _plate(**{which: 50e6}))
    sig = ref.sigma_cr(4.0, ref.E_STEEL, 0.01, 1.0, ref.NU_STEEL) / MPA
    key = "safety_factor_a" if which == "axial_load_a" else "safety_factor_b"
    assert ref.rel_err(r[key], sig / 50.0) < 0.03


@pytestmark_live
@pytest.mark.xfail(strict=False, reason="DEF-1: edge_condition 被忽略（clamped≡simply_supported）")
def test_live_clamped_edges_raise_buckling_coefficient():
    ss = _post(PLATE, _plate(edge_condition="simply_supported"))["critical_stress_a"]
    cl = _post(PLATE, _plate(edge_condition="clamped"))["critical_stress_a"]
    assert ref.rel_err(cl / ss, ref.k_uniaxial_clamped(1.0, 1.0) / 4.0) < 0.03


@pytestmark_live
@pytest.mark.xfail(strict=False, reason="DEF-4: 船体级板格隐含板宽取 3×肋距 + 单位混用")
def test_live_hull_panel_stress_uses_member_spacing():
    r = _post("/api/v1/structure/plate", HULL)
    pan = ref.panel_critical_stress(HULL["frame_spacing"], HULL["frame_spacing"],
                                    HULL["plate_thickness"])
    assert ref.rel_err(r["critical_stress"], pan["sigma_cr"] / MPA) < 0.03


@pytestmark_live
@pytest.mark.xfail(strict=False, reason="DEF-5: 疲劳端点 calculated_life 恒为 0（S-N 未实现）")
def test_live_fatigue_life_matches_dnv_c_order_of_magnitude():
    r = _post("/api/v1/structure/fatigue", HULL)
    expect = ref.dnv_c_life(r["stress_range"])
    assert r["calculated_life"] > 0
    assert 0.2 < r["calculated_life"] / expect < 5.0


@pytestmark_live
@pytest.mark.xfail(strict=False, reason="DEF-3: 泊松比未做参数域校验（ν=0.9 照算）")
def test_live_rejects_unphysical_poisson_ratio():
    r = _post(PLATE, _plate(nu=0.9), timeout=20.0)
    assert r.get("_http") in (400, 422)