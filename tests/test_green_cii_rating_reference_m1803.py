"""绿色航行 L4「CII A–E 等级」验证欠账清偿（mission 1803，verifier 席）。

背景
----
`backend/services/green_navigation.py` 自陈四层证据边界（L1 化学计量 REAL / L2 达到值
REAL / L3 要求值 WITHHELD / L4 等级 NOT_SUPPORTED）。其中 L4 被显式判为
NOT_SUPPORTED，理由是「G4(MEPC.354(78)) 等级边界函数未取得规则原文」。

本文件做的是**验证**而不是功能开发（不改任何生产代码）：

  A 层  L4 边界的可闭合性证明
        在测试内建一个**独立影子参考实现** `shadow_cii_rating()`（G5 §4 的四个
        边界向量 dd = [0.86, 0.94, 1.06, 1.18] 与半开区间映射），并用
        边界精确性 / 单调性 / 标度不变性 / 全等级可达 / 带宽恒等 五类判据证明：
        该等级函数是一个 13 行的确定性规则，不含任何未定物理量。
        —— 结论：L4 的 NOT_SUPPORTED 是**证据来源缺失**（规则原文未回读），
        不是**数学不可实现**。两者必须区分，否则会把「欠一次转录」误判成「做不到」。

  B 层  L3 要求值的锚定与算术恒等
        参考线系数（MEPC.353(78) G2 表）与应用算术 required = ref·(1−Z) 分开判：
        算术部分可用恒等式独立复算（REAL 口径），系数部分仍是转录（WITHHELD）。
        本测试显式记录“系数锚定只能证明与转录表一致，不能证明转录本身正确”，
        因此 L3 证据等级**不得**被本测试升级。

  C 层  诚实性守卫（本单最主要的实质发现）
        模块设计原则写的是「不允许静默默认」，但 `required_cii()` 存在两处违反：
          C1  year ≥ 2027：Z 静默沿用 0.11（`CII_Z_FACTOR_LATEST`），无外推标记；
          C2  year < 2023：Z 静默取 0.0，无「规则尚未适用」标记。
        这两条以 `xfail(strict=True)` 钉住（当前行为违反原则 → 预期失败；
        一旦实现方修正，strict 会立刻转红提醒更新判据），并把修复派给
        ship-performance-platform（本仓不得再加船舶功能代码）。

运行：``python3 -m pytest tests/test_green_cii_rating_reference_m1803.py -q``
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import pytest

# 平台 API 包导入链会读数据库配置；钉住进程级环境，避免拉起 psycopg2。
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import green_navigation as gn  # noqa: E402


# ---------------------------------------------------------------------------
# A 层：独立影子参考实现（G5 §4 边界向量 dd1..dd4）
# ---------------------------------------------------------------------------

# 四个边界相对「要求值」的比值：superior / lower / upper / inferior
G5_DD: tuple[float, float, float, float] = (0.86, 0.94, 1.06, 1.18)
GRADE_ORDER: tuple[str, ...] = ("A", "B", "C", "D", "E")


def shadow_cii_rating(attained: float, required: float) -> str:
    """G5 §4 的半开区间映射（独立实现，不 import 生产模块的任何等级代码）。

    边界值本身归入**较优**等级：
        attained ≤ d1·R → A；d1·R < attained ≤ d2·R → B；d2·R < attained ≤ d3·R → C；
        d3·R < attained ≤ d4·R → D；attained > d4·R → E。
    """
    if required <= 0:
        raise ValueError("required 必须为正")
    if attained < 0:
        raise ValueError("attained 必须非负")
    ratio = attained / required
    for grade, dd in zip(GRADE_ORDER, G5_DD):
        # 浮点容差：dd·R / R 未必逐位等于 dd，边界判据必须按「≤」的语义实现
        if ratio < dd or math.isclose(ratio, dd, rel_tol=1e-12, abs_tol=0.0):
            return grade
    return "E"


def shadow_boundaries(required: float) -> dict[str, float]:
    return {
        "superior": G5_DD[0] * required,
        "lower": G5_DD[1] * required,
        "upper": G5_DD[2] * required,
        "inferior": G5_DD[3] * required,
    }


# 平台 `CII_REFERENCE_LINES` 的转录锚（MEPC.353(78) G2 表）。
# 注意：这是**转录值**，不是对规则 PDF 的回读 —— 只用于锁定“表未被静默改动”，
# 不能作为 L3 升级为 REAL 的依据。
CII_REF_LINE_TRANSCRIBED: dict[str, tuple[float, float]] = {
    "bulk_carrier": (4745.0, 0.622),
    "tanker": (5247.0, 0.610),
    "container_ship": (1984.0, 0.489),
    "gas_carrier": (8104.0, 0.639),
}

# MEPC.338(76) 年度削减率 Z（相对 2019 参考年）
Z_SCHEDULE = {2023: 0.05, 2024: 0.07, 2025: 0.09, 2026: 0.11}


# ---------------------------------------------------------------------------
# A：边界向量与映射
# ---------------------------------------------------------------------------

def test_g5_boundary_vector_is_the_grading_table():
    r = 5.0
    b = shadow_boundaries(r)
    assert b["superior"] == pytest.approx(0.86 * r, rel=0, abs=0)
    assert b["lower"] == pytest.approx(0.94 * r, rel=0, abs=0)
    assert b["upper"] == pytest.approx(1.06 * r, rel=0, abs=0)
    assert b["inferior"] == pytest.approx(1.18 * r, rel=0, abs=0)
    # 四个边界严格递增（映射因此是良定义的）
    assert b["superior"] < b["lower"] < b["upper"] < b["inferior"]


@pytest.mark.parametrize("required", [0.5, 3.7, 5.0, 12.3, 25.0])
def test_g5_boundaries_are_half_open_and_assign_better_grade_on_boundary(required: float):
    b = shadow_boundaries(required)
    eps = required * 1e-9
    assert shadow_cii_rating(b["superior"], required) == "A"
    assert shadow_cii_rating(b["superior"] + eps, required) == "B"
    assert shadow_cii_rating(b["lower"], required) == "B"
    assert shadow_cii_rating(b["lower"] + eps, required) == "C"
    assert shadow_cii_rating(b["upper"], required) == "C"
    assert shadow_cii_rating(b["upper"] + eps, required) == "D"
    assert shadow_cii_rating(b["inferior"], required) == "D"
    assert shadow_cii_rating(b["inferior"] + eps, required) == "E"


def test_g5_all_five_grades_reachable_and_band_widths_are_constant_fractions():
    required = 4.0
    ratios = [0.5, 0.9, 1.0, 1.1, 1.5]
    grades = [shadow_cii_rating(r * required, required) for r in ratios]
    assert grades == ["A", "B", "C", "D", "E"]

    # 带宽恒等：每档宽度固定为 required 的常数倍（不随船型/年份变化）
    widths = []
    prev = 0.0
    for dd in G5_DD:
        widths.append(dd - prev)
        prev = dd
    assert widths == pytest.approx([0.86, 0.08, 0.12, 0.12], rel=0, abs=1e-12)


def test_g5_monotone_no_grade_improves_as_attained_grows():
    required = 6.0
    samples = [required * (0.30 + 0.01 * i) for i in range(120)]  # 0.30R … 1.49R
    idx = [GRADE_ORDER.index(shadow_cii_rating(a, required)) for a in samples]
    assert all(b >= a for a, b in zip(idx, idx[1:])), "等级序号必须单调不减"
    # 且不跳档（相邻 1% 采样最多跨一档）
    assert all(b - a <= 1 for a, b in zip(idx, idx[1:]))


def test_g5_rating_is_scale_invariant_in_ratio():
    base_a, base_r = 5.2, 4.0
    ref = shadow_cii_rating(base_a, base_r)
    for f in (1e-3, 1e-1, 7.5, 1e3, 1e6):
        assert shadow_cii_rating(base_a * f, base_r * f) == ref


def test_shadow_reference_rejects_degenerate_inputs():
    with pytest.raises(ValueError):
        shadow_cii_rating(1.0, 0.0)
    with pytest.raises(ValueError):
        shadow_cii_rating(-1.0, 1.0)


# ---------------------------------------------------------------------------
# A→B：影子参考与平台达到值/要求值链的对接（端到端一致性）
# ---------------------------------------------------------------------------

def test_platform_pipeline_plus_shadow_rating_is_self_consistent():
    """平台给达到值/要求值，影子参考给等级；两者拼接后应满足手算判据。"""
    co2_g = 2.0e9            # 2 000 t CO2
    capacity_t = 80_000.0    # Capesize DWT
    distance_nm = 12_000.0
    year = 2024

    res = gn.evaluate_cii("bulk_carrier", co2_g, capacity_t, distance_nm, year)
    attained = co2_g / (capacity_t * distance_nm)          # 手算标量
    ref_2019 = 4745.0 * capacity_t ** (-0.622)             # 手算标量
    required = ref_2019 * (1.0 - 0.07)

    assert res["attained_value"] == pytest.approx(attained, rel=1e-12)
    assert res["reference_value"] if "reference_value" in res else True
    assert res["required_value"] == pytest.approx(required, rel=1e-12)
    assert res["ratio"] == pytest.approx(attained / required, rel=1e-12)

    grade = shadow_cii_rating(attained, required)
    # attained=2.083e-3 … 手算比值 = attained/required
    expect_ratio = attained / required
    assert grade == (
        "A" if expect_ratio <= 0.86 else
        "B" if expect_ratio <= 0.94 else
        "C" if expect_ratio <= 1.06 else
        "D" if expect_ratio <= 1.18 else "E"
    )


# ---------------------------------------------------------------------------
# B：L3 系数锚定 + 应用算术恒等
# ---------------------------------------------------------------------------

def test_cii_reference_lines_unchanged_vs_transcribed_table():
    for ship_type, (a, c) in CII_REF_LINE_TRANSCRIBED.items():
        got = gn.CII_REFERENCE_LINES[ship_type]
        assert got[0] == pytest.approx(a, rel=1e-12), f"{ship_type} 的 a 被改动"
        assert got[1] == pytest.approx(c, rel=1e-12), f"{ship_type} 的 c 被改动"


def test_z_schedule_matches_transcribed_values():
    assert {y: gn.CII_Z_FACTOR[y] for y in sorted(gn.CII_Z_FACTOR)} == Z_SCHEDULE


@pytest.mark.parametrize("year", [2023, 2024, 2025, 2026])
def test_required_cii_arithmetic_identity(year: int):
    """required = a·Cap^(-c)·(1−Z)。算术部分可独立复算（REAL 口径）。"""
    capacity_t = 55_000.0
    res = gn.required_cii("tanker", capacity_t, year)
    a, c = 5247.0, 0.610
    ref = a * capacity_t ** (-c)
    z = Z_SCHEDULE[year]
    assert res["reference_value_2019"] == pytest.approx(ref, rel=1e-12)
    assert res["z_factor"] == pytest.approx(z, rel=0, abs=0)
    assert res["required_value"] == pytest.approx(ref * (1.0 - z), rel=1e-12)
    assert res["evidence_grade"] == gn.EVIDENCE_WITHHELD, "L3 不得被静默升级为 REAL"


def test_required_cii_monotone_decreasing_over_regulated_years():
    capacity_t = 100_000.0
    vals = [gn.required_cii("container_ship", capacity_t, y)["required_value"]
            for y in (2023, 2024, 2025, 2026)]
    assert all(b < a for a, b in zip(vals, vals[1:])), "Z 加深 → 要求值必须逐年更严"


# ---------------------------------------------------------------------------
# C：诚实性守卫（当前 NOT_SUPPORTED 不得伪装；两处静默默认以 xfail 钉住）
# ---------------------------------------------------------------------------

def test_l4_rating_reports_not_supported_and_hides_no_grade():
    st = gn.cii_rating_status()
    assert st["evidence_grade"] == gn.EVIDENCE_NOT_SUPPORTED
    assert st["implemented"] is False
    assert "grade" not in st

    res = gn.evaluate_cii("bulk_carrier", 1.0e9, 80_000.0, 10_000.0, 2024)
    assert res["rating"]["implemented"] is False
    assert "grade" not in res, "evaluate_cii 不得凭空给出 A–E 等级"


def test_evidence_summary_keeps_l4_at_not_supported():
    layers = {row["layer"]: row for row in gn.evidence_summary()["layers"]}
    assert layers["L4_cii_rating_A_E"]["evidence_grade"] == gn.EVIDENCE_NOT_SUPPORTED
    assert layers["L3_required_reference_line"]["evidence_grade"] == gn.EVIDENCE_WITHHELD


@pytest.mark.xfail(
    strict=True,
    reason="缺陷 C1（静默默认）：year≥2027 时 required_cii 无标记地沿用 Z=0.11，"
           "违反模块自述‘不允许静默默认’原则；修复需在 ship-performance-platform 进行",
)
def test_c1_required_cii_beyond_2026_must_be_flagged_not_silent():
    res = gn.required_cii("bulk_carrier", 80_000.0, 2031)
    assert res.get("z_extrapolated") is True, (
        "2027+ 的 Z 属外推，必须带 z_extrapolated 标记或报错，不得静默沿用 0.11"
    )


@pytest.mark.xfail(
    strict=True,
    reason="缺陷 C2（静默默认）：year<2023 时 required_cii 静默返回 z=0.0，"
           "而规则尚未适用的年份应显式标注/报错",
)
def test_c2_required_cii_before_2023_must_not_silently_return_zero_z():
    with pytest.raises((KeyError, ValueError)):
        gn.required_cii("bulk_carrier", 80_000.0, 2019)