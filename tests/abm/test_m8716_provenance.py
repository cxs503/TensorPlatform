"""m8716 六维功效门交付物的**溯源守卫**（doc ↔ artifact ↔ 代码 三方对齐）。

背景（m9713 重开）：`docs/P143W2-N04_six_dim_power.md` 里每一个数字都声称来自
`docs/m9713/six_dim_power_validation.json`（由 `docs/m9713/build_six_dim_power_validation.py` 生成），
但那份工件曾在主线缺位——文档数字退化为无出处的手抄，且解析参照列在
m8719（t 分位精确化）后与文档印值漂移了 0.4%。

本文件把「零手抄」做成**机器判据**：

1. `test_artifact_schema`：工件存在且结构完整（测量字段不被静默删改）；
2. `test_doc_claims_pinned_to_artifact`：文档印出的每个数字必须 == 工件重算值
   （印值出现在文档中，且与工件在给定小数位一致）——文档与工件不允许各自漂移；
3. `test_g25_scenario_reproduced_bitwise`：用**同一 seed 的独立复现**重跑 G25 族判决，
   p_perm / p_holm / cohen_dz 与工件逐位相等 → 工件不是手写/伪造的；
4. `test_calibration_bounds`：零效应族与 A/A 空对照的误报率结论受约束
   （族判决误报率 ≤ α，FWER 的 CI 覆盖 α）；
5. `test_provenance_chain_intact`：文档 → 构建脚本 → 工件路径链条完整。

运行成本：< 10 s（只重跑一个 12 对 × 6 维 × 9999 置换的族判决）。
"""

from __future__ import annotations

import json
import os
import random

import pytest

from tools.abm import six_dim_power as S

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARTIFACT = os.path.join(ROOT, "docs", "m9713", "six_dim_power_validation.json")
BUILD_SCRIPT = os.path.join(ROOT, "docs", "m9713", "build_six_dim_power_validation.py")
FIGURE = os.path.join(ROOT, "docs", "m9713", "six_dim_power_fig_g25_power.png")
DOC = os.path.join(ROOT, "docs", "P143W2-N04_six_dim_power.md")


@pytest.fixture(scope="module")
def artifact():
    with open(ARTIFACT, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def doc_text():
    with open(DOC, encoding="utf-8") as fh:
        return fh.read()


def _get(path):
    """按 ``a/b/0/c`` 取值（用 / 分隔，避免 rho_0.7 这类含小数点的键被切碎）。"""
    def _inner(a):
        cur = a
        for key in path.split("/"):
            cur = cur[int(key)] if key.lstrip("-").isdigit() else cur[key]
        return cur
    return _inner


# (文档中印出的数字, 工件取值路径, 小数位) —— 文档 §4/§5 的每一个数字
DOC_CLAIMS = [
    # §4.1 G25 判决（模拟值，seed 固定 → 必须逐位稳定）
    ("0.0024", "g25_two_dim_improvement/per_dim/accuracy/p_holm", 4),
    ("0.0025", "g25_two_dim_improvement/per_dim/efficiency/p_holm", 4),
    ("1.94", "g25_two_dim_improvement/per_dim/accuracy/cohen_dz", 2),
    ("1.65", "g25_two_dim_improvement/per_dim/efficiency/cohen_dz", 2),
    ("2", "g25_two_dim_improvement/n_significant_better", 0),
    # §4.2 检验自身校准
    ("0.060", "null_family_fwer/family_any/p_hat", 3),
    ("0.031", "null_family_fwer/family_any/lo", 3),
    ("0.102", "null_family_fwer/family_any/hi", 3),
    ("0.020", "null_family_fwer/per_dim_max_rate", 3),
    ("0.000", "null_family_fwer/family_ge_min_dims/p_hat", 3),
    ("0.000", "null_family_fwer/family_all/p_hat", 3),
    ("0.060", "aa_null_control/ci_upper_family", 3),
    # §4.3 功效正控与配对收益
    ("0.880", "power_positive_control/rho_0.7/detection_rate", 3),
    ("0.4325", "power_positive_control/rho_0.0/detection_rate", 4),
    ("0.457", "power_vs_rho/rows/0/power_greater", 3),
    ("0.560", "power_vs_rho/rows/1/power_greater", 3),
    ("0.707", "power_vs_rho/rows/2/power_greater", 3),
    ("0.863", "power_vs_rho/rows/3/power_greater", 3),
    ("0.993", "power_vs_rho/rows/4/power_greater", 3),
    # §4.4 族判决功效曲线与所需 n
    ("0.142", "family_decision_power/rows/0/power_ge_min_dims", 3),
    ("0.242", "family_decision_power/rows/1/power_ge_min_dims", 3),
    ("0.458", "family_decision_power/rows/2/power_ge_min_dims", 3),
    ("0.617", "family_decision_power/rows/3/power_ge_min_dims", 3),
    ("0.825", "family_decision_power/rows/4/power_ge_min_dims", 3),
    ("0.975", "family_decision_power/rows/5/power_ge_min_dims", 3),
    ("0.967", "family_decision_power/rows/6/power_ge_min_dims", 3),
    ("0.803", "family_decision_power/single_dim_power_at_holm_alpha_measured", 3),
    ("0.644", "family_decision_power/predicted_both_significant", 3),
    ("0.608", "family_decision_power/sim_n20_power_all_affected", 3),
    ("26", "family_decision_power/required_n_grid_hit", 0),
    # §4.5 MDE 引擎抽查
    ("0.6937", "mde_curve_check/points/0/mde", 4),
    ("0.6562", "mde_curve_check/points/0/mde_analytic", 4),
    ("0.4100", "mde_curve_check/points/1/mde", 4),
    ("0.4529", "mde_curve_check/points/1/mde_analytic", 4),
]


def test_artifact_schema(artifact):
    """工件结构完整：缺字段 = 文档溯源断链。"""
    required = {
        "g25_two_dim_improvement", "g25_null", "null_family_fwer",
        "aa_null_control", "power_positive_control", "power_vs_rho",
        "family_decision_power", "mde_curve_check",
    }
    assert required <= set(artifact)
    for path in ("null_family_fwer/family_any", "null_family_fwer/family_all",
                 "aa_null_control/family_ge_min_dims"):
        ci = _get(path)(artifact)
        assert {"k", "n", "p_hat", "lo", "hi", "method"} <= set(ci), path
        assert ci["method"] == "clopper-pearson"
    # 模拟规模必须与文档声明一致（改了规模而文档不写 = 隐性漂移）
    assert artifact["null_family_fwer"]["n_families"] == 200
    assert artifact["null_family_fwer"]["n_perm"] == 499
    assert artifact["family_decision_power"]["n_rep"] == 120
    assert artifact["power_positive_control"]["rho_0.7"]["n_sims"] == 400


@pytest.mark.parametrize("printed,path,decimals", DOC_CLAIMS,
                         ids=[f"{path}={printed}" for printed, path, _ in DOC_CLAIMS])
def test_doc_claims_pinned_to_artifact(artifact, doc_text, printed, path, decimals):
    """文档印值 ∈ 文档 且 == 工件重算值（同一小数位）。"""
    assert printed in doc_text, f"文档未印出 {printed!r}（{path}）"
    value = float(_get(path)(artifact))
    tol = 0.5 * 10 ** (-decimals) + 1e-12
    assert abs(value - float(printed)) <= tol, (
        f"文档印 {printed} 而工件 {path}={value}（允许半末位 {tol}）")


def test_g25_scenario_reproduced_bitwise(artifact):
    """独立复现 G25 族判决：证明工件是算出来的，不是编出来的。"""
    rng = random.Random(20261109)
    deltas = {d: [0.0] * 12 for d in S.DIMENSIONS}
    deltas["accuracy"] = [0.030 + rng.gauss(0.0, 0.015) for _ in range(12)]
    deltas["efficiency"] = [0.050 + rng.gauss(0.0, 0.020) for _ in range(12)]

    records = []
    for dim, ds in deltas.items():
        sgn = S.DIMENSION_DIRECTION[dim]
        for i, delta in enumerate(ds):
            for arm, value in (("control", 1.0), ("treatment", 1.0 + sgn * delta)):
                records.append({"lineage": "L1", "seat": f"S{i}", "arm": arm,
                                "dimension": dim, "value": value, "unit": "u",
                                "source": f"synthetic://L1/{arm}"})

    res = S.six_dim_advantage(records, n_perm=9999, seed=20261109)
    pinned = artifact["g25_two_dim_improvement"]
    assert res["decision_pass"] is pinned["decision_pass"] is True
    assert res["n_significant_better"] == pinned["n_significant_better"] == 2
    for dim in ("accuracy", "efficiency"):
        assert res["dimensions"][dim]["p_perm"] == pinned["per_dim"][dim]["p_perm"]
        assert res["dimensions"][dim]["p_holm"] == pinned["per_dim"][dim]["p_holm"]
        assert (res["dimensions"][dim]["effect"]["cohen_dz"]
                == pytest.approx(pinned["per_dim"][dim]["cohen_dz"], rel=0, abs=0))
        assert res["dimensions"][dim]["direction"] == "treatment_better"
    for dim in ("correctness", "stability", "reproducibility", "applicability"):
        assert res["dimensions"][dim]["p_perm"] == 1.0
        assert res["dimensions"][dim]["direction"] == "indistinguishable"

    # 全零对照同样复现：判不出优势，且最大 p_perm = 1.0
    null_records = [{"lineage": "L1", "seat": f"S{i}", "arm": arm, "dimension": dim,
                     "value": 1.0, "unit": "u", "source": f"synthetic://L1/{arm}"}
                    for dim in S.DIMENSIONS for i in range(12)
                    for arm in ("control", "treatment")]
    res0 = S.six_dim_advantage(null_records, n_perm=9999, seed=20261109)
    assert res0["decision_pass"] is False
    assert res0["n_significant_better"] == 0
    assert max(v["p_perm"] for v in res0["dimensions"].values()) == 1.0
    assert artifact["g25_null"]["max_p_perm"] == 1.0


def test_calibration_bounds(artifact):
    """校准结论的硬边界：族判决误报率 ≤ α；FWER 的 95% CI 覆盖 α。"""
    alpha = artifact["null_family_fwer"]["nominal_alpha"]
    assert alpha == 0.05
    any_ci = artifact["null_family_fwer"]["family_any"]
    decision_ci = artifact["null_family_fwer"]["family_ge_min_dims"]
    assert decision_ci["p_hat"] <= alpha
    assert any_ci["lo"] <= alpha <= any_ci["hi"], "FWER 的 CI 必须覆盖名义 α"
    assert artifact["null_family_fwer"]["per_dim_max_rate"] <= alpha
    aa = artifact["aa_null_control"]
    assert aa["pass"] is True
    assert aa["family_ge_min_dims"]["p_hat"] <= alpha
    assert aa["ci_upper_family"] <= 0.10  # 60 族的 CP 上界，留出抽样余量

    # §5 声明的误差判定不得被后续重算推翻
    pos = artifact["power_positive_control"]
    assert pos["rel_error_rho_0.7"] < 0.03
    rel0 = abs(pos["rho_0.0"]["detection_rate"]
               - pos["rho_0.0"]["analytic_power_normal_t"]) \
        / pos["rho_0.0"]["analytic_power_normal_t"]
    assert rel0 < 0.03
    # 族判决功效单调性（n 网格上不得反向超出 Monte-Carlo 噪声）
    powers = [r["power_ge_min_dims"] for r in artifact["family_decision_power"]["rows"]]
    assert powers[-1] >= powers[0]
    assert all(b - a >= -0.05 for a, b in zip(powers, powers[1:]))


def test_provenance_chain_intact(doc_text):
    """文档 → 构建脚本 → 工件 链条完整（任一环缺失=数字无出处）。"""
    assert "docs/m9713/build_six_dim_power_validation.py" in doc_text
    assert "docs/m9713/six_dim_power_validation.json" in doc_text
    assert os.path.exists(BUILD_SCRIPT)
    assert os.path.exists(ARTIFACT)
    assert os.path.getsize(ARTIFACT) > 2000
    with open(BUILD_SCRIPT, encoding="utf-8") as fh:
        script = fh.read()
    assert 'os.path.join(ROOT, "docs", "m9713")' in script
    assert "six_dim_power_validation.json" in script
    assert "six_dim_power_fig_g25_power.png" in script
    assert os.path.exists(FIGURE) and os.path.getsize(FIGURE) > 20000
    # 证据必须落在 docs/m<本单>/（artifacts/ 与 runs/ 被推送门禁整目录滤除 = m8716 工件丢失根因）：
    # 文档给出的复现命令只能指向 docs/m9713/，不得再指回被滤除的目录
    repro = [ln for ln in doc_text.splitlines() if "build_six_dim_power_validation.py" in ln]
    assert repro, "文档未给出复现命令"
    assert any(ln.strip().startswith("python docs/m9713/") for ln in repro)