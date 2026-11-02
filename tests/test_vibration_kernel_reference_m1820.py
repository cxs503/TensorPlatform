"""m1820 回归测试：振动噪声核独立复核的两层钉子。

第一层（参考侧自钉）：``tools/validation/vibration_reference_m1820.py`` 的文献表值 /
异离散 / 时域积分必须自身成立——否则「参考」不可信。
第二层（被审侧现状钉）：把 2026-11-02 实测的判决钉住。将来核被修好（或接线补齐）时
这些测试会失败，强制更新台账——**不允许静默漂移**。

跑法：``python3 -m pytest tests/test_vibration_kernel_reference_m1820.py -q``
（约 3 s，纯解析 + 小网格差分；重型面板检查由 verify_vibration_kernel_m1820.py 负责）
"""
from __future__ import annotations

import importlib.util
import json
import math
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys_path = ROOT / "tools" / "validation"

import sys  # noqa: E402

sys.path.insert(0, str(sys_path))
import vibration_reference_m1820 as ref  # noqa: E402

KERNEL_PATH = ROOT / "backend" / "api" / "domain" / "ship_vibration.py"
ARTIFACTS = ROOT / "artifacts" / "m1820"


@pytest.fixture(scope="module")
def kernel():
    spec = importlib.util.spec_from_file_location("ship_vibration_ut", KERNEL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)      # 按文件路径加载，绕过 api/__init__ 的 DB 依赖
    return mod


# ---------------------------------------------------------------------------
# 第一层：参考侧自身
# ---------------------------------------------------------------------------
def test_reference_blevins_table_is_internally_consistent():
    """文献表值必须满足各自的超越方程（表值抄错会在此暴露）."""
    lams = ref.blevins_lambda("clamped-clamped", 4)
    for lam in lams:
        assert abs(math.cos(lam) * math.cosh(lam) - 1.0) < 1e-9
    for lam in ref.blevins_lambda("clamped-free", 4):
        assert abs(math.cos(lam) * math.cosh(lam) + 1.0) < 1e-9
    # C-P：文献值只保证落在渐近式 lambda_n ~ (n + 1/4) pi 的窄带内（该 BC 的
    # 超越方程存在 tan lambda = +/- tanh lambda 两种书写约定，故此处不钉残差，
    # 只钉"不是抄错的数"这一必要条件）
    for n, lam in enumerate(ref.blevins_lambda("clamped-pinned", 4), start=1):
        assert abs(lam - (n + 0.25) * math.pi) < 0.01 * lam
    pp = ref.blevins_lambda("pinned-pinned", 4)
    assert all(abs(l - n * math.pi) < 1e-12 for n, l in enumerate(pp, start=1))


def test_reference_fd_laplacian_matches_analytic_small_grid():
    """有限差分 Laplacian 特征值在小网格上应逼近解析值（5% 内）."""
    a, b = 2.0, 1.5
    mu_fd = ref.plate_ssss_fd_eigenvalues(a, b, n_grid=12, n_modes=1)[0]
    mu_ex = ref.plate_ssss_analytic_mu(1, 1, a, b)
    assert abs(mu_fd - mu_ex) / mu_ex < 0.05


def test_reference_rk4_matches_closed_form_sdof():
    """时域 RK4 积分与传递率闭式在非共振点应一致（2%）."""
    T_num = ref.sdof_transmissibility_rk4(20.0, 10.0, 0.05)
    r = 2.0
    T_ana = math.sqrt((1 + (2 * 0.05 * r) ** 2) / ((1 - r * r) ** 2 + (2 * 0.05 * r) ** 2))
    assert abs(T_num - T_ana) / T_ana < 0.02


def test_reference_iec_table_spot_values():
    assert ref.aw_iec_table(1000.0) == 0.0
    assert ref.aw_iec_table(2000.0) == 1.2
    assert len(ref.IEC_61672_TABLE_3) == 34


# ---------------------------------------------------------------------------
# 第二层：被审核现状（与 artifacts/m1820/verdicts.json 同源判决）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bc", ["clamped-clamped", "clamped-free",
                               "clamped-pinned", "pinned-pinned"])
def test_kernel_beam_eigenvalues_vs_literature(kernel, bc):
    got = kernel.beam_eigenvalues(bc, 4)
    lit = ref.blevins_lambda(bc, 4)
    assert max(abs(g - l) / l for g, l in zip(got, lit)) < 1e-5


def test_kernel_beam_frequency_uses_literature_lambda(kernel):
    got = kernel.beam_natural_frequencies("clamped-free", 2.1e11, 1.6e-4, 7850.0, 0.02, 6.0, 1)[0]
    want = ref.beam_frequency_from_lambda("clamped-free", 2.1e11, 1.6e-4, 7850.0, 0.02, 6.0, 1)[0]
    assert abs(got - want) / want < 1e-5


def test_kernel_plate_frequency_matches_independent_writing(kernel):
    got = kernel.plate_natural_frequency(1, 1, 2.0, 1.5, 2.1e11, 0.3, 7850.0, 0.008)
    want = ref.plate_ssss_frequency(1, 1, 2.0, 1.5, 2.1e11, 0.3, 7850.0, 0.008)
    assert abs(got - want) / want < 1e-9


def test_kernel_a_weighting_vs_iec_table(kernel):
    worst = max(abs(kernel.a_weighting(f) - lit)
                for f, lit in ref.IEC_61672_TABLE_3.items())
    assert worst < 0.3, f"A 计权与 IEC 61672-1 Table 3 最大偏差 {worst:.3f} dB"


def test_kernel_a_weighting_zero_at_1khz(kernel):
    assert abs(kernel.a_weighting(1000.0)) < 0.02


def test_kernel_mass_law_matches_textbook(kernel):
    mpp = 39.25
    for f in (250.0, 500.0, 1000.0, 2000.0):
        assert abs(kernel.panel_tl(f, mpp)["tl_normal_db"]
                   - ref.tl_normal_incidence_exact(f, mpp)) < 1e-9


def test_kernel_coincidence_frequency_steel_rule(kernel):
    """钢的工程经验式 f_c[kHz] ≈ 12/h[mm]（5% 内）."""
    for h_mm in (5.0, 8.0):
        got = kernel.coincidence_frequency(2.1e11, 0.3, 7850.0, h_mm / 1000.0)
        assert abs(got - 12.0 / h_mm * 1000.0) / (12.0 / h_mm * 1000.0) < 0.05


def test_kernel_db_combination_identities(kernel):
    assert abs(kernel.combine_levels_db([60.0, 60.0]) - 63.010299956639813) < 1e-9
    assert abs(kernel.combine_levels_db([50.0] * 10) - 60.0) < 1e-9


def test_kernel_octave_band_energy_conserved(kernel):
    out = kernel.octave_band_levels(80.0, [63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0])
    assert abs(ref.combine_db(out["band_levels_db"]) - 80.0) < 1e-9


def test_kernel_isolation_unit_transmissibility_at_sqrt2(kernel):
    for zeta in (0.0, 0.05, 0.2):
        T = kernel.isolation_transmissibility(math.sqrt(2.0) * 10.0, 10.0, zeta)["transmissibility"]
        assert abs(T - 1.0) < 1e-12


def test_kernel_excitation_frequencies(kernel):
    ex = kernel.blade_rate_orders(4, 120.0, 3)
    fs, bpf = ref.shaft_and_blade_freq(120.0, 4)
    assert abs(ex["shaft_hz"] - fs) < 1e-12 and abs(ex["bpf_hz"] - bpf) < 1e-12
    for stroke in (2, 4):
        got = kernel.diesel_firing_frequencies(120.0, 6, stroke)["firing_hz"]
        assert abs(got - ref.diesel_firing_freq(120.0, 6, stroke)) < 1e-12


def test_kernel_resonance_margin_semantics(kernel):
    r = kernel.resonance_margin(12.0, 10.0, 0.15)
    assert r["passed"] is True and abs(r["margin_pct"] - 20.0) < 1e-9
    r2 = kernel.resonance_margin(10.5, 10.0, 0.15)
    assert r2["passed"] is False


def test_kernel_mode_shape_satisfies_clamped_bc(kernel):
    """clamped 端 w=0 必须精确成立（构造性质）；斜率残差随加密单调下降."""
    res = []
    for npts in (4001, 16001):
        ms = kernel.beam_mode_shape("clamped-clamped", 1, 6.0, n_points=npts)
        r = ref.bc_residual(ms["x"], ms["w"], 6.0, ends="both")
        assert max(v for k, v in r.items() if k.startswith("w_")) < 1e-12
        res.append(max(v for k, v in r.items() if k.startswith("slope_")))
    assert res[1] < res[0], "端点斜率残差未随网格加密下降"


# ---------------------------------------------------------------------------
# 第三层：接线缺口（infrastructure 事实，来自 artifacts/m1820/wiring_evidence.json）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def wiring():
    path = ARTIFACTS / "wiring_evidence.json"
    if not path.exists():
        pytest.skip("wiring_evidence.json 未生成：先跑 probe_vibration_wiring_m1820.py")
    return json.loads(path.read_text())


def test_wiring_module_not_in_domain_all():
    init = (ROOT / "backend" / "api" / "domain" / "__init__.py").read_text()
    assert 'ship_vibration' not in init


def test_wiring_no_vibration_router_in_main():
    main_py = (ROOT / "backend" / "main.py").read_text()
    assert "vibration" not in main_py.lower()
    assert len(re.findall(r"app\.include_router\(", main_py)) > 5


def test_wiring_no_production_import_sites():
    """全仓 *.py 中除「本模块自身 / 其自带测试 / 本单 tools」外，无任何引用点."""
    allowed = {
        "backend/api/domain/ship_vibration.py",
        "backend/tests/feat/test_ship_vibration.py",
        "tools/validation/vibration_reference_m1820.py",
        "tools/validation/probe_vibration_wiring_m1820.py",
        "tools/validation/verify_vibration_kernel_m1820.py",
        "tests/test_vibration_kernel_reference_m1820.py",
    }
    hits = []
    for p in ROOT.rglob("*.py"):
        if ".git" in p.parts:
            continue
        rel = str(p.relative_to(ROOT))
        if rel in allowed:
            continue
        try:
            if "ship_vibration" in p.read_text(errors="ignore"):
                hits.append(rel)
        except OSError:
            continue
    assert hits == [], f"出现新的引用点（接线可能已变更，需更新台账）: {hits}"


def test_wiring_live_probe_evidence(wiring):
    """在线证据：:8001 上 6 条候选路径全 404，而控制组 405（证明 404 非假阴性）."""
    live = wiring["live"]["ship_platform_8001"]
    assert [r["status"] for r in live["candidates"]] == [404] * len(live["candidates"])
    assert [r["status"] for r in live["controls"]] == [405, 405]


# ---------------------------------------------------------------------------
# 第四层：判据账本完整性（防「无条件判据」与静默漂移）
# ---------------------------------------------------------------------------
def test_verdict_ledger_is_complete_and_all_pass():
    path = ARTIFACTS / "verdicts.json"
    if not path.exists():
        pytest.skip("verdicts.json 未生成：先跑 verify_vibration_kernel_m1820.py")
    data = json.loads(path.read_text())
    checks = data["checks"]
    assert len(checks) >= 40, "判据数异常"
    for c in checks:
        assert c["basis"].strip(), f"{c['id']} 缺基准说明"
        assert c["tol"] > 0, f"{c['id']} 缺容差"
    fails = [c["id"] for c in checks if not c["passed"]]
    assert fails == [], f"台账出现 FAIL（核或基准已变，需重审）: {fails}"