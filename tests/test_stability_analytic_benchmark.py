"""
稳性解析基准测试（verification-debt 守护）
==========================================

三组测试：
A. 参考解自洽性 —— 三路独立参考解（wall-sided 闭式 / 剖面矩 Simpson 积分 /
   多边形裁剪几何）互校 + 恒定排水体积守恒 + 精确静水力闭式值。
B. 平台现役实现的**定性缺陷守护**（xfail(strict=True)）：一旦平台侧修复，
   测试会变 XPASS 而失败，强制更新本基准——避免"修好了但基准没跟上"。
C. 在线端点契约测试（:8001 不可达时 skip）：复核 serving 端点返回值与
   源码逐字复刻的一致性（审计结论的可复算性）。

运行：python3 -m pytest tests/test_stability_analytic_benchmark.py -q
"""

from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.stability_bench import reference as ref  # noqa: E402

SHIP_API = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")


# ---------------------------------------------------------------------------
# A. 参考解自洽性
# ---------------------------------------------------------------------------
BOX = ref.BoxBarge(L=100.0, B=20.0, T=8.0, D=25.0, KG=6.0)


def test_box_barge_exact_hydrostatics_closed_form():
    """矩形剖面闭式静水力：KB=T/2、BM=B²/(12T)、∇=L·B·T。"""
    assert BOX.volume == pytest.approx(100.0 * 20.0 * 8.0, rel=0)
    assert BOX.kb == pytest.approx(4.0, rel=0)
    assert BOX.bm == pytest.approx(400.0 / 96.0, rel=1e-15)
    assert BOX.gm == pytest.approx(4.0 + 400.0 / 96.0 - 6.0, rel=1e-15)
    assert BOX.displacement_t == pytest.approx(16400.0, rel=1e-12)


@pytest.mark.parametrize("phi", [5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0])
def test_three_reference_paths_agree(phi):
    """wall-sided 闭式解 / 剖面矩积分 / 多边形裁剪 三路互校（有效角域内）。

    矩形剖面舷侧垂直，wall-sided 公式在甲板入水与舭部出水之前是**精确**解，
    故三路偏差应在数值精度量级（< 1e-6 m）。
    """
    assert phi <= ref.box_valid_heel_limit_deg(BOX)
    gz_general = ref.heeled_box_gz(BOX, phi)
    assert ref.wall_sided_gz(phi, BOX.gm, BOX.bm) == pytest.approx(gz_general, abs=1e-6)
    assert ref.polygon_gz(BOX, phi) == pytest.approx(gz_general, abs=1e-6)


@pytest.mark.parametrize("phi", [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 59.0])
def test_constant_displacement_volume_conservation(phi):
    """倾斜至 φ 时浸水剖面面积必须严格等于 B·T（恒定排水量前提）。"""
    area, _, _ = ref.heeled_box_centroid(BOX, phi)
    assert area == pytest.approx(BOX.B * BOX.T, rel=1e-9)


def test_wall_sided_invalid_beyond_limit():
    """超出 wall-sided 有效角域后，闭式解与几何精确解必须显著分歧——
    这是"只在有效域内使用闭式解"这一纪律的机器化守护。"""
    limit = ref.box_valid_heel_limit_deg(BOX)
    assert limit == pytest.approx(math.degrees(math.atan(2 * min(BOX.T, BOX.D - BOX.T) / BOX.B)), rel=1e-12)
    gap = abs(ref.wall_sided_gz(60.0, BOX.gm, BOX.bm) - ref.heeled_box_gz(BOX, 60.0))
    assert gap > 0.5, f"60° 处闭式解应与精确解分歧，实测 {gap:.3f} m"


def test_area_integration_matches_analytic_sine():
    """面积积分器自检：∫_0^40 sinφ dφ = 1 − cos40°（rad 口径）。"""
    xs = [i * 0.25 for i in range(0, 161)]
    ys = [math.sin(math.radians(x)) for x in xs]
    got = ref.area_under_curve(xs, ys, 0.0, 40.0)
    want = 1.0 - math.cos(math.radians(40.0))
    assert got == pytest.approx(want, rel=2e-5)


def test_gz_max_angle_of_box_barge():
    """BOX-A 精确 GZ 极值应落在甲板入水角附近（59.5°），量级 4.3 m。"""
    deck = ref.full_submersion_angle_deg(BOX)
    xs = [i * 0.25 for i in range(0, int(deck * 4) + 1)]
    ys = [ref.heeled_box_gz(BOX, x) for x in xs]
    assert max(ys) == pytest.approx(4.3346, abs=0.10)
    assert xs[ys.index(max(ys))] > 55.0


# ---------------------------------------------------------------------------
# B. 平台现役实现的缺陷守护（xfail strict：修复后必须更新本基准）
# ---------------------------------------------------------------------------
def _platform_box():
    kb = ref.platform_kb(BOX.T, 1.0)
    bm = ref.platform_bm(BOX.B, BOX.T, 1.0)
    return kb, bm, kb + bm - BOX.KG


def test_platform_bm_is_exact_for_rectangular_section():
    """BM 分支在 Cb=Cw=1 处恰好退化为精确解 B²/(12T)：偏差 < 0.01%。"""
    _, bm, _ = _platform_box()
    assert abs(ref.rel_err(bm, BOX.bm)) < 1e-4


@pytest.mark.xfail(strict=True, reason=(
    "已知缺陷：KB=T(1−Cb/3) 在矩形剖面给出 0.667T，精确值 0.5T（+33%）；"
    "应改用 Morrish KB=T(5/6−Cb/(3Cw)) 或中剖面式 KB=T(0.53+0.085(1−Cm))。"))
def test_platform_kb_within_15pct_of_exact():
    kb, _, _ = _platform_box()
    assert abs(ref.rel_err(kb, BOX.kb)) <= 0.15


@pytest.mark.xfail(strict=True, reason=(
    "已知缺陷：大倾角项用 sin²φ 替代 wall-sided 的 tan²φ，且 φ>40° 乘人为收敛因子 "
    "(60−φ)/20，导致 GZ@50° 偏低 45%、GZ_max 偏低 35%。"))
def test_platform_gz_within_15pct_of_exact_up_to_40deg():
    kb, bm, gm = _platform_box()
    for phi in (10.0, 20.0, 30.0, 40.0):
        gz_plat = ref.platform_gz(phi, gm, kb, bm, BOX.KG, BOX.B)
        assert abs(ref.rel_err(gz_plat, ref.heeled_box_gz(BOX, phi))) <= 0.15


@pytest.mark.xfail(strict=True, reason=(
    "已知缺陷：IMO 面积判据基于上述 GZ 曲线，Area_0_40 高估 41%（+30~+51%）；"
    "同时 5° 粗梯形积分的区间判据在端部格点处不闭合。"))
def test_platform_imo_area_0_40_within_15pct_of_exact():
    kb, bm, gm = _platform_box()
    gz = {float(a): ref.platform_gz(a, gm, kb, bm, BOX.KG, BOX.B) for a in range(0, 61, 5)}
    _, _, a0_40 = ref.platform_imo_areas(gz)
    xs = [i * 0.25 for i in range(0, 161)]
    ys = [ref.heeled_box_gz(BOX, x) for x in xs]
    assert abs(ref.rel_err(a0_40, ref.area_under_curve(xs, ys, 0.0, 40.0))) <= 0.15


def test_platform_vanishing_angle_is_hardcoded():
    """缺陷特征化：serving 端点把稳性消失角硬编码为 60°（GZ 被 (60−φ)/20 收敛因子
    人工压到 0），而矩形驳船精确解在 60° 内 GZ 单调上升、并不存在消失角。

    本测试守护"缺陷形态"：平台侧一旦改为真实求根消失角，本测试应随之改写。"""
    kb, bm, gm = _platform_box()
    gz = {float(a): ref.platform_gz(a, gm, kb, bm, BOX.KG, BOX.B) for a in range(0, 61, 5)}
    # 精确解不消失 ⇒ 平台应报告"未消失"而非固定 60°
    assert all(ref.heeled_box_gz(BOX, a) > 0 for a in range(0, 60))
    assert not all(gz[a] > 0 for a in range(0, 61, 5))


# ---------------------------------------------------------------------------
# C. 在线端点复核（不可达则 skip）
# ---------------------------------------------------------------------------
def _api(body: dict):
    req = urllib.request.Request(f"{SHIP_API}/calculations/stability",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        pytest.skip(f"ship API 不可达：{SHIP_API}")

def test_api_matches_source_replica():
    """审计结论的可复算性：serving 端点返回值必须与源码逐字复刻一致（≤0.1%）。"""
    resp = _api({"length": BOX.L, "beam": BOX.B, "draft": BOX.T,
                 "displacement": BOX.displacement_t, "cb": 1.0, "kg": BOX.KG})
    kb, bm, gm = _platform_box()
    assert resp["kb"] == pytest.approx(kb, abs=0.01)
    assert resp["bm"] == pytest.approx(bm, abs=0.01)
    assert resp["gm"] == pytest.approx(gm, abs=0.01)
    gz = {float(a): ref.platform_gz(a, gm, kb, bm, BOX.KG, BOX.B) for a in range(0, 61, 5)}
    _, _, a0_40 = ref.platform_imo_areas(gz)
    assert resp["area_0_40"] == pytest.approx(a0_40, abs=1e-3)


def test_api_has_no_gz_curve_field():
    """审计事实：serving 端点不返回 GZ 曲线本身（仅聚合量），
    使曲线级复核必须依赖源码复刻——这是可审计性缺口。"""
    resp = _api({"length": BOX.L, "beam": BOX.B, "draft": BOX.T,
                 "displacement": BOX.displacement_t, "cb": 1.0, "kg": BOX.KG})
    assert "gz_curve" not in resp