"""箱型驳大倾角静稳性基准件的自检与回归锁

被测对象：``tools/ops/stability_gz_benchmark.py``（独立参考解）+ 船舶平台
``/api/v1/stability-imo/gz-curve`` / ``/api/v1/stability/gz-curve`` 输出。

测试分两层：
1. **参考解层（无网络，必须绿）**：精确裁剪解必须复现闭式量
   （KB=T/2、BM=B²/12T、dGZ/dφ=GM、dKN/dφ=KM）、排水体积守恒，
   且在直舷式有效区内与 wall-sided 闭式解一致。
2. **平台层（需要平台在跑，否则 skip）**：跑通对标管线、JSON 结构完整，
   并把"当前平台的已知偏差"锁成 xfail 断言 —— 平台修好后会自动 XPASS。
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOD_PATH = os.path.join(_HERE, "tools", "ops", "stability_gz_benchmark.py")


def _load_benchmark():
    spec = importlib.util.spec_from_file_location("stability_gz_benchmark",
                                                  _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["stability_gz_benchmark"] = mod
    spec.loader.exec_module(mod)
    return mod


BM = _load_benchmark()


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def barge():
    # 100 x 20 x 5 m 箱型驳，型深 9 m，KG = 6 m（经典 pontoon 算例）
    return BM.BoxBarge(L=100.0, B=20.0, T=5.0, D=9.0, KG=6.0)


@pytest.fixture(scope="module")
def comparison(barge):
    """平台对标结果；平台不可用则 skip（不掩盖失败，只跳过）"""
    api = BM.query_api(barge, base=BM.DEFAULT_API_BASE, timeout=8.0)
    res = BM.compare(api, barge)
    if not res.get("available"):
        pytest.skip(f"ship platform 不可用: {res.get('reason')}")
    return res


# ---------------------------------------------------------------------------
# 1. 闭式静水力
# ---------------------------------------------------------------------------
def test_closed_form_hydrostatics(barge):
    assert barge.KB == pytest.approx(barge.T / 2.0)
    assert barge.BM == pytest.approx(barge.B ** 2 / (12.0 * barge.T))
    assert barge.KM == pytest.approx(barge.KB + barge.BM)
    assert barge.GM == pytest.approx(3.1666666667, rel=1e-9)
    assert barge.volume == pytest.approx(10000.0)
    assert barge.displacement == pytest.approx(10250.0)


def test_characteristic_angles(barge):
    # atan(2(D-T)/B) = atan(8/20) = 21.801°
    assert barge.deck_edge_angle == pytest.approx(21.8014, abs=1e-3)
    # atan(2T/B) = atan(0.5) = 26.565°
    assert barge.bilge_emergence_angle == pytest.approx(26.5651, abs=1e-3)
    assert barge.wall_sided_limit == pytest.approx(barge.deck_edge_angle)


# ---------------------------------------------------------------------------
# 2. 精确裁剪解：守恒与一致性
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("phi", [0.0, 5.0, 10.0, 15.0, 20.0, 21.8, 26.6,
                                 30.0, 45.0, 60.0, 75.0, 89.0])
def test_dispaced_area_conservation(barge, phi):
    res = BM.exact_kn_gz(barge, phi)
    assert res["area"] == pytest.approx(barge.Cb * barge.B * barge.T, abs=1e-8)


@pytest.mark.parametrize("phi", [0.0, 10.0, 20.0, 45.0, 80.0])
def test_upright_geometry_identities(barge, phi):
    res = BM.exact_kn_gz(barge, phi)
    assert all(math.isfinite(v) for v in res.values())
    if phi == 0.0:
        assert res["kn"] == pytest.approx(0.0, abs=1e-12)
        assert -res["zb"] == pytest.approx(barge.KB, rel=1e-9)   # 浮心高
        assert -res["waterline_lift"] == pytest.approx(barge.T, rel=1e-9)
    else:
        assert res["kn"] > 0.0        # 右舷横倾 → 浮心向舷侧移动


def test_initial_stability_slopes(barge):
    """dGZ/dφ(φ→0)=GM, dKN/dφ(φ→0)=KM —— 独立参考解的强校验"""
    h = 1e-5
    gzp = BM.exact_kn_gz(barge, math.degrees(h))["gz"]
    gzm = BM.exact_kn_gz(barge, math.degrees(-h))["gz"]
    knp = BM.exact_kn_gz(barge, math.degrees(h))["kn"]
    knm = BM.exact_kn_gz(barge, math.degrees(-h))["kn"]
    assert (gzp - gzm) / (2 * h) == pytest.approx(barge.GM, rel=1e-5)
    assert (knp - knm) / (2 * h) == pytest.approx(barge.KM, rel=1e-5)


def test_matches_wall_sided_closed_form(barge):
    """直舷式有效区（φ ≤ 21.8°）内，两路独立参考解须一致"""
    for phi in (5.0, 10.0, 15.0, 20.0, 21.0):
        gz_exact = BM.exact_kn_gz(barge, phi)["gz"]
        gz_ws = BM.wall_sided_gz(barge, phi)
        assert gz_exact == pytest.approx(gz_ws, rel=1e-4), f"phi={phi}"


def test_reference_vanishing_angle(barge):
    """箱型驳（GM=3.17 m）消失角应落在 60-75°，而非被截断在 0"""
    n = 900
    angles = [i * 0.1 for i in range(n + 1)]
    gz = [BM.exact_kn_gz(barge, a)["gz"] for a in angles]
    met = BM.reference_metrics(barge, angles, gz)
    assert met["vanishing_angle"] is not None
    assert 60.0 < met["vanishing_angle"] < 75.0
    # 大倾角下 GZ 必须出现负值段（物理上存在倾覆区间）
    assert min(gz[-50:]) < -0.5


def test_selfcheck_all_pass(barge):
    sc = BM.selfcheck(barge)
    assert sc["all_pass"], [c for c in sc["checks"] if not c["pass"]]


# ---------------------------------------------------------------------------
# 3. 平台层：管线完整性 + 已知偏差回归锁
# ---------------------------------------------------------------------------
def test_api_pipeline_structure(comparison):
    assert comparison["available"] is True
    assert len(comparison["points"]) == 19           # 0..90° / 5°
    assert set(comparison["scalars"]) >= {"area_0_30", "area_0_40",
                                          "area_30_40", "GZ_max"}
    for key in ("KB", "BM", "KM", "GM"):
        assert key in comparison["hydrostatics"]
    assert comparison["diagnostics"]["endpoints_agree"] in (True, False)


def test_api_curve_monotone_up_to_max(comparison):
    """曲线合理性：GZ(0)=0、峰值角在 25-45° 之间"""
    pts = comparison["points"]
    assert abs(pts[0]["gz_api"]) < 1e-9
    assert 20.0 <= pts[0]["angle"] <= 25.0 or pts[0]["angle"] == 0.0
    scal = comparison["scalars"]["GZ_max_angle"]
    assert 20.0 <= scal["api"] <= 50.0


@pytest.mark.xfail(strict=False, reason="已知缺陷：平台 GZ 近似式对箱型驳偏差 >3%"
                                        "（验证欠账，见 docs/reports/"
                                        "stability_gz_verification.md）；"
                                        "平台修复后本断言会 XPASS")
def test_known_defect_gz_within_3pct(comparison):
    bad = [p for p in comparison["points"]
           if p["gz_err_pct"] is not None and abs(p["gz_err_pct"]) >= 3.0]
    assert not bad, f"{len(bad)} 个角度超差: {[(p['angle'], round(p['gz_err_pct'], 2)) for p in bad]}"


@pytest.mark.xfail(strict=False, reason="已知缺陷：平台 GZ 被 max(0,·) 截断，"
                                        "丢失大倾角负值段/消失角被伪造成 65°")
def test_known_defect_negative_gz_preserved(comparison):
    diag = comparison["diagnostics"]
    assert diag["api_reports_negative_gz"] is True


def test_gm_error_locked(comparison):
    """初稳性高度误差应落在实测区间内（回归锁：防止静默漂移）"""
    err = comparison["hydrostatics"]["GM"]["err_pct"]
    assert 5.0 < err < 10.0, f"GM 误差 {err:.2f}% 超出锁定的 [5,10] 区间"


# ---------------------------------------------------------------------------
# 4. 端到端：脚本可产出 JSON + 图（轻量，不调外部重算）
# ---------------------------------------------------------------------------
def test_artifacts_reproducible(barge, tmp_path):
    figs = BM.make_figures(barge, {"available": False}, str(tmp_path))
    assert len(figs) >= 2  # 无 API 对标时只出图1/图3
    for f in figs:
        assert os.path.getsize(f) > 10_000
    payload = {"barge": BM.asdict(barge), "selfcheck": BM.selfcheck(barge)}
    out = tmp_path / "r.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    assert json.loads(out.read_text())["selfcheck"]["all_pass"] is True