"""
m1478 验证欠账：湿表面积模型 / wall-sided 复原力臂的解析基准复核。

判据（平台精度标准）：关键量 vs 解析基准误差 > 15% 不可接受。
- 规范形体（方盒/圆柱/球/胶囊体）：闭式解为真值，新增闭式分支必须 0%（<1e-9）；
- 旧经验式 S=pi*B*L*(0.5+0.25Cb) 对同一批形体误差必须**被证明** > 15%（回归护栏，
  防止有人把它改回去）；
- 复原力臂：wall-sided 闭式 vs 等排水量数值解，适用域内 < 0.5%；
- ITTC-1957 Cf：vs 标准表值 < 0.1%。
"""
from __future__ import annotations

import asyncio
import math

import pytest

# 直接按文件加载 api/calculations.py：避免 import api 包时连带拉起 DB/cryptography
# 等与本单无关的重依赖（平台整体依赖在本机缺 cryptography，属环境限制而非缺陷）。
import importlib.util
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from services import hull_geometry as hg  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "m1478_calculations", _BACKEND / "api" / "calculations.py"
)
_calc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _calc  # pydantic 前向引用解析需要模块已登记
_spec.loader.exec_module(_calc)
for _m in (_calc.ResistanceRequest, _calc.StabilityRequest):
    _m.model_rebuild()

ResistanceRequest = _calc.ResistanceRequest
StabilityRequest = _calc.StabilityRequest
calc_resistance = _calc.calc_resistance
calc_stability = _calc.calc_stability


# ---------------------------------------------------------------------------
# A. 湿表面积：闭式基准
# ---------------------------------------------------------------------------

CANONICAL = [
    ("box", 100.0, 20.0, 8.0),
    ("cylinder", 50.0, 10.0, 10.0),
    ("sphere", 10.0, 10.0, 10.0),
    ("capsule", 4.356, 0.508, 0.508),
]


@pytest.mark.parametrize("form,L,B,T", CANONICAL)
def test_exact_wetted_surface_matches_volume_identity(form, L, B, T):
    """闭式 S 与闭式体积（Cb·L·B·T）必须自洽，且体积等于形体的解析体积。"""
    cb = hg.form_cb(form, L, B, T)
    vol_api = cb * L * B * T
    R = B / 2.0
    vol_ref = {
        "box": L * B * T,
        "cylinder": math.pi * R * R * L,
        "sphere": 4.0 / 3.0 * math.pi * R ** 3,
        "capsule": math.pi * R * R * L + 4.0 / 3.0 * math.pi * R ** 3,
    }[form]
    assert vol_api == pytest.approx(vol_ref, rel=1e-12)


def test_exact_wetted_surface_closed_forms():
    """闭式湿表面积核对（教科书公式，逐项独立复算）。"""
    assert hg.exact_wetted_surface("box", 100, 20, 8) == pytest.approx(100 * 20 + 2 * 8 * (100 + 20))
    assert hg.exact_wetted_surface("cylinder", 50, 10, 10) == pytest.approx(
        2 * math.pi * 5 * 50 + 2 * math.pi * 25
    )
    assert hg.exact_wetted_surface("sphere", 10, 10, 10) == pytest.approx(math.pi * 100)
    assert hg.exact_wetted_surface("capsule", 4.356, 0.508, 0.508) == pytest.approx(
        2 * math.pi * 0.254 * 4.356 + 4 * math.pi * 0.254 ** 2
    )


@pytest.mark.parametrize("form,L,B,T", CANONICAL)
def test_legacy_formula_is_proven_wrong(form, L, B, T):
    """回归护栏：旧经验式误差 > 15%（这是本次修复的立论依据）。"""
    cb = hg.form_cb(form, L, B, T)
    s_exact = hg.exact_wetted_surface(form, L, B, T)
    s_legacy = hg.legacy_wetted_surface(L, B, T, cb)
    err = abs(s_legacy - s_exact) / s_exact * 100.0
    assert err > 15.0, f"{form}: legacy formula error only {err:.2f}% — 护栏失效"


def test_wetted_surface_dispatcher_prefers_exact_branch():
    s, model = hg.wetted_surface("box", 100, 20, 8, 1.0)
    assert model == "exact:box"
    assert s == pytest.approx(hg.exact_wetted_surface("box", 100, 20, 8))
    # auto: Cb=1 判为直壁方盒
    s2, model2 = hg.wetted_surface("auto", 100, 20, 8, 1.0)
    assert model2 == "exact:box"
    # auto: 商船走 Holtrop 1982
    s3, model3 = hg.wetted_surface("auto", 150, 25, 8, 0.7)
    assert model3 == "holtrop1982"
    assert s3 == pytest.approx(hg.holtrop_wetted_surface(150, 25, 8, 0.7))


def test_verify_geometry_rejects_inconsistent_cb():
    """几何一致性：球形体 Cb 只能是 π/6，输入 0.78 必须报错。"""
    with pytest.raises(ValueError):
        hg.verify_geometry("sphere", 10, 10, 10, 0.7854)
    # 自洽时通过
    info = hg.verify_geometry("sphere", 10, 10, 10, math.pi / 6)
    assert info["cb_rel_error"] < 1e-9


def test_verify_geometry_warns_outside_holtrop_domain():
    info = hg.verify_geometry("ship", 150, 25, 8, 0.45)
    assert any("Holtrop" in w for w in info["warnings"])
    with pytest.raises(ValueError):
        hg.verify_geometry("ship", 150, 25, 8, 1.5)


# ---------------------------------------------------------------------------
# B. 复原力臂：闭式 vs 独立数值解
# ---------------------------------------------------------------------------

BOX = dict(B=10.0, T=4.0, depth=8.0, KG=3.0)


def test_wall_sided_formula_matches_numeric_within_validity():
    hs = hg.box_hydrostatics(BOX["B"], BOX["T"], BOX["KG"])
    limit = math.degrees(hg.wall_sided_limit_rad(BOX["B"], BOX["T"], BOX["depth"]))
    assert limit == pytest.approx(38.66, abs=0.05)
    for phi in (2.0, 5.0, 10.0, 20.0, 30.0, 35.0, 38.0):
        gz_num = hg.box_gz_numeric(BOX["B"], BOX["T"], BOX["depth"], BOX["KG"], phi)
        gz_ws = hg.wall_sided_gz(hs["GM"], hs["BM"], math.radians(phi))
        assert gz_ws == pytest.approx(gz_num, rel=1e-4, abs=1e-6), f"phi={phi}"


def test_wall_sided_degrades_beyond_validity():
    """超出适用域（甲板边缘入水）后闭式解与数值解显著背离 —— 适用域必须外显。"""
    hs = hg.box_hydrostatics(BOX["B"], BOX["T"], BOX["KG"])
    phi = 55.0
    gz_num = hg.box_gz_numeric(BOX["B"], BOX["T"], BOX["depth"], BOX["KG"], phi)
    gz_ws = hg.wall_sided_gz(hs["GM"], hs["BM"], math.radians(phi))
    assert abs(gz_ws - gz_num) / abs(gz_num) * 100.0 > 5.0


def test_wall_sided_rejects_past_90deg():
    with pytest.raises(ValueError):
        hg.wall_sided_gz(1.0, 2.0, math.radians(95.0))


# ---------------------------------------------------------------------------
# C. ITTC-1957 摩擦阻力系数
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("re,cf_ref", [(1e6, 0.004688), (1e7, 0.003000), (1e8, 0.002083), (1e9, 0.001531)])
def test_ittc57_matches_published_table(re, cf_ref):
    cf = 0.075 / (math.log10(re) - 2.0) ** 2
    assert cf == pytest.approx(cf_ref, rel=1e-3)


# ---------------------------------------------------------------------------
# D. API 集成
# ---------------------------------------------------------------------------

def test_api_resistance_uses_geom_validated_wetted_surface():
    resp = asyncio.run(
        calc_resistance(
            ResistanceRequest(
                ship_type="barge", speed_ms=2.0, length=100, beam=20, draft=8, cb=1.0, hull_form="box"
            )
        )
    )
    assert resp.wetted_surface_model == "exact:box"
    assert resp.wetted_surface_m2 == pytest.approx(hg.exact_wetted_surface("box", 100, 20, 8))
    # Rf = 0.5*rho*S*Cf*V^2 必须与返回的 S 自洽（相对误差 <1e-3）
    s = resp.wetted_surface_m2
    re = 2.0 * 100 / 1.004e-6
    cf = 0.075 / (math.log10(re) - 2.0) ** 2
    rf_expect = 0.5 * 998.0 * s * cf * 4.0
    assert resp.frictional_resistance_n == pytest.approx(rf_expect, rel=1e-3)


def test_api_resistance_rejects_geometry_inconsistent_cb():
    with pytest.raises(Exception):
        asyncio.run(
            calc_resistance(
                ResistanceRequest(
                    ship_type="sphere", speed_ms=1.0, length=10, beam=10, draft=10, cb=0.8, hull_form="sphere"
                )
            )
        )


def test_api_stability_reports_validity_envelope():
    ok = asyncio.run(
        calc_stability(
            StabilityRequest(
                displacement_t=1000, gm_m=1.083, bm_m=2.083, heel_angle_deg=20,
                depth_m=8, draft_m=4, beam_m=10,
            )
        )
    )
    assert ok.wall_sided_valid is True
    assert ok.heel_limit_deg == pytest.approx(38.66, abs=0.05)

    over = asyncio.run(
        calc_stability(
            StabilityRequest(
                displacement_t=1000, gm_m=1.083, bm_m=2.083, heel_angle_deg=45,
                depth_m=8, draft_m=4, beam_m=10,
            )
        )
    )
    assert over.wall_sided_valid is False
    assert over.validity["warnings"]

    with pytest.raises(Exception):
        asyncio.run(
            calc_stability(StabilityRequest(displacement_t=1000, gm_m=1, bm_m=2, heel_angle_deg=95))
        )