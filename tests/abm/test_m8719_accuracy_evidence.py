"""m8719 六维功效精度内核：证据闸门（红/绿可复算 + 文档数字不漂移）。

背景：``docs/P143W2-N06_t_quantile_accuracy.md`` 引用的探针与工件在前序 main 上
不存在，「修复前后」无法复算。本测试把 `runs/m8719/defect_probe.py` 当仪器跑一遍，
(a) 断言修复后的精度判据，(b) 断言基线缺陷仍可复现（红必须真红，不是空口），
(c) 断言文档里的数字与 `docs/m8719_evidence_summary.json` 逐字一致
    （artifacts/ 可能被交付门禁滤除，故证据副本放 docs/ 并由测试锁定）。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = ROOT / "runs" / "m8719" / "defect_probe.py"
BUILD_DOC_PATH = ROOT / "runs" / "m8719" / "build_doc.py"
MODULE_PATH = ROOT / "tools" / "abm" / "six_dim_power.py"
DOC_MD = ROOT / "docs" / "P143W2-N06b_evidence_restore.md"
DOC_SUMMARY = ROOT / "docs" / "m8719_evidence_summary.json"
PRIOR_DOC = ROOT / "docs" / "P143W2-N06_t_quantile_accuracy.md"

# 测试档位（全量探针 16 s，测试内缩到 ~4 s）
DF_GRID = (1, 2, 3, 4, 5, 8, 10, 15, 20, 30, 50, 200, 5000)
COVERAGE_REPS = 1500
HOLM_TRIALS = 400


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"无法加载 {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def probe():
    return _load(PROBE_PATH, "m8719_defect_probe")


@pytest.fixture(scope="module")
def sdp():
    return _load(MODULE_PATH, "m8719_six_dim_power")


@pytest.fixture(scope="module")
def measured(probe, sdp):
    return {
        "tq": probe.measure_t_quantile(sdp, df_grid=DF_GRID),
        "down": probe.measure_downstream(sdp),
        "holm": probe.measure_holm(sdp, trials=HOLM_TRIALS),
        "cov": probe.measure_coverage(sdp, reps=COVERAGE_REPS),
    }


# ---------------------------------------------------------------------------
# 0. 证据文件齐备（探针 / 构建器 / 文档 / 机器可核证据副本）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [PROBE_PATH, BUILD_DOC_PATH, DOC_MD, DOC_SUMMARY, PRIOR_DOC])
def test_evidence_paths_exist(path):
    assert path.is_file(), f"证据路径缺失：{path}"


# ---------------------------------------------------------------------------
# 1. D1：t 分位数精度（修复后绿 / 基线红仍可复现）
# ---------------------------------------------------------------------------

def test_t_ppf_fixed_within_tight_tolerance(measured):
    tq = measured["tq"]
    assert tq["reference"]["scipy_available"] is True or tq["max_fixed_rel_err"] < 1e-9
    # 文档自述「df∈[1,5000] 实测最大相对误差 9.4e-11」；留 5x 余量防浮点平台差异
    assert tq["max_fixed_rel_err"] < 5e-10, tq["max_fixed_rel_err"]


def test_reference_is_cross_checked_against_scipy(measured):
    ref = measured["tq"]["reference"]
    if not ref["scipy_available"]:
        pytest.skip("环境无 scipy，独立积分基准无法与外部参照互校")
    assert ref["max_ref_vs_scipy_rel_err"] < 1e-8, ref["max_ref_vs_scipy_rel_err"]


def test_baseline_cornish_fisher_defect_still_reproducible(measured):
    """红必须真红：基线公式在 df=5 上偏 5.30%（原文档 D1 数字）。"""
    from statistics import NormalDist

    df, p = 5, 0.975
    z = NormalDist().inv_cdf(p)
    legacy = z + (z ** 3 + z) / (4.0 * df)
    row = next(r for r in measured["tq"]["rows_p975"] if r["df"] == df)
    assert row["baseline"] == pytest.approx(legacy), "基线复刻与旧公式口径不一致"
    err = abs(row["baseline"] - row["ref"]) / abs(row["ref"])
    assert 0.04 < err < 0.06, err  # 原文档记 5.297%
    assert row["fixed_rel_err"] < 1e-9


def test_baseline_worst_case_grows_for_small_df(measured):
    rows = {r["df"]: r for r in measured["tq"]["rows_p975"]}
    # 一阶展开在 df→1 完全崩掉（df=1 为 Cauchy，真值 12.706）
    assert rows[1]["baseline_rel_err"] > 0.5
    assert rows[1]["fixed_rel_err"] < 1e-9


# ---------------------------------------------------------------------------
# 2. D1 下游：解析 MDE / 所需样本对 / 往返功率
# ---------------------------------------------------------------------------

def test_analytic_mde_matches_independent_reference(measured):
    ds = measured["down"]["analytic_mde_n9"]
    assert ds["fixed_rel_err"] < 1e-9, ds["fixed_rel_err"]
    # 基线偏差须复现（原文档 1.573%）
    assert 1e-3 < ds["baseline_rel_err"] < 5e-2, ds["baseline_rel_err"]


def test_min_n_for_effect_boundary_flip_fourteen_to_fifteen(measured):
    nn = measured["down"]["min_n_for_effect_d0.8"]
    assert nn["baseline"] == 14, nn["baseline"]
    assert nn["fixed"] == nn["ref"] == 15


def test_min_n_round_trip_power_meets_target(measured):
    rt = measured["down"]["round_trip_power_at_n"]
    assert rt is not None
    assert rt["n"] == 15
    assert rt["power"] >= 0.8 - 1e-12
    assert rt["meets_target"] is True


# ---------------------------------------------------------------------------
# 3. D2：Holm 调整 p 与教科书定义一致（且基线缺陷可复现）
# ---------------------------------------------------------------------------

def test_holm_matches_textbook_definition(measured):
    hm = measured["holm"]
    assert hm["fixed_inconsistent_rate"] == 0.0
    assert hm["fixed_max_abs_diff"] < 1e-12
    assert hm["baseline_inconsistent_rate"] > 0.3, hm["baseline_inconsistent_rate"]


def test_holm_hand_case_equals_textbook(measured, sdp):
    hc = measured["holm"]["hand_case"]
    assert hc["p"] == [0.001, 0.04, 0.05]
    assert hc["fixed"] == pytest.approx(hc["textbook"])
    assert hc["fixed"] == pytest.approx([0.003, 0.08, 0.08])
    # 基线缺陷：首个不显著之后一律 1.0
    assert hc["baseline"][-1] == pytest.approx(1.0)
    assert sdp._bonferroni_holm([0.001, 0.04, 0.05], 0.05) == pytest.approx([0.003, 0.08, 0.08])


# ---------------------------------------------------------------------------
# 4. CI 覆盖率
# ---------------------------------------------------------------------------

def test_ci95_coverage_near_nominal(measured):
    by_n = measured["cov"]["by_n"]
    assert "9" in by_n
    assert abs(by_n["9"]["fixed_gap_pp"]) < 2.0, by_n["9"]
    for n in by_n:
        assert abs(by_n[n]["fixed_gap_pp"]) < 2.0, (n, by_n[n])


# ---------------------------------------------------------------------------
# 5. 判据集合自洽 + 文档数字不漂移（doc↔evidence 闸门）
# ---------------------------------------------------------------------------

def test_all_verdicts_pass(probe, measured):
    """探针自身的判据集合须全绿（与文档 PASS 一致）。"""
    res = {"t_quantile": measured["tq"], "downstream": measured["down"],
           "holm": measured["holm"], "coverage": measured["cov"]}
    verdicts = probe.verdicts(res)
    assert len(verdicts) == 10
    failed = [k for k, v in verdicts.items() if not v]
    assert not failed, f"未通过判据：{failed}"


def test_t_ppf_tail_df_still_accurate(probe, sdp):
    """补测大 df 尾部（dfs 1000/2500/5000）：修复后仍 <1e-9，基线在此区已 <1e-3。"""
    tail = probe.measure_t_quantile(sdp, df_grid=(1000, 2500, 5000))
    assert tail["max_fixed_rel_err"] < 1e-9, tail["max_fixed_rel_err"]
    assert tail["max_baseline_rel_err"] < 5e-3, tail["max_baseline_rel_err"]


def test_doc_summary_is_consistent_and_green():
    summary = json.loads(DOC_SUMMARY.read_text(encoding="utf-8"))
    assert summary["all_pass"] is True
    assert all(summary["verdicts"].values())
    assert summary["module_sha256"], "缺模块溯源"
    assert len(summary["t_quantile"]["rows_p975"]) >= 10
    assert summary["t_quantile"]["max_fixed_rel_err"] < 5e-10
    assert summary["downstream"]["min_n_for_effect_d0.8"]["fixed"] == 15


def test_doc_markdown_carries_summary_numbers_verbatim():
    """文档表格里的数字必须能被机器在 summary.json 里找到（防手抄/漂移）。"""
    summary = json.loads(DOC_SUMMARY.read_text(encoding="utf-8"))
    md = DOC_MD.read_text(encoding="utf-8")
    tq = summary["t_quantile"]
    needles = [
        f"{tq['max_fixed_rel_err']:.3e}",
        f"{tq['max_baseline_rel_err']:.3e}",
        f"{summary['downstream']['analytic_mde_n9']['fixed_rel_err']:.3e}",
        f"{summary['holm']['uniform']['baseline_inconsistent_rate']:.2%}",
        f"{summary['holm']['wide']['baseline_inconsistent_rate']:.2%}",
    ]
    for df, row in tq["rows_p975"].items():
        needles.append(f"{row['baseline_rel_err']:.3e}")
        needles.append(f"{row['fixed_rel_err']:.3e}")
    missing = [n for n in needles if n not in md]
    assert not missing, f"文档缺少/漂移的数字：{missing[:5]}"


def test_doc_states_error_judgement_explicitly():
    md = DOC_MD.read_text(encoding="utf-8")
    assert "## ⑥ 误差判定" in md
    assert "<3%" in md
    assert "无基准项：无" in md
    # 六要素齐备（①…⑥）
    for marker in ("## ① 案例介绍", "## ② 怎么做的", "## ③ 计算结果",
                   "## ④ 结果图", "## ⑤ 注意点", "## ⑥ 误差判定"):
        assert marker in md, marker


def test_prior_defect_doc_links_now_resolve():
    """前序文档引用的探针路径现在必须存在（本轮修复的核心）。"""
    prior = PRIOR_DOC.read_text(encoding="utf-8")
    assert "runs/m8719/defect_probe.py" in prior
    assert (ROOT / "runs/m8719/defect_probe.py").is_file()