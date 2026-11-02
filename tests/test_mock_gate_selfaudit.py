"""MOCK 指纹探测器自审（m2035）：口径级盲区的判负用例 + 合成语料混淆矩阵。

三条纪律：
  1. 每条"漏判通道"都要有一个**必须被判负**的合成 MOCK（否则加固只是空转）；
  2. 每条加固都要有**必须保持静默**的合成 REAL（否则把 REAL 打成 MOCK 更糟）；
  3. legacy / hardened 两级口径在同一份语料上的差值，就是加固的真实增益。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.audit import mock_fingerprint as mf  # noqa: E402
from tools.audit import mock_gate_selfaudit as sa  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_profile():
    yield
    mf.set_detector_profile("hardened")


# ── ① 口径开关 ────────────────────────────────────────────────────────────────
def test_profile_switch_roundtrip():
    mf.set_detector_profile("legacy")
    assert mf.ACCEPT_NUMERIC_STRINGS is False and mf.QUANTUM_MAX_SPREAD == 0
    mf.set_detector_profile("hardened")
    assert mf.ACCEPT_NUMERIC_STRINGS is True and mf.QUANTUM_MAX_SPREAD > 0
    with pytest.raises(ValueError):
        mf.set_detector_profile("nonsense")


# ── ② 四条曾被走掉的逃逸通道（每条都必须判负）────────────────────────────────
def test_string_encoded_mock_is_read():
    """字符串编码的数值过去被整条丢弃（verdict=NO_KEY），现在必须解析成 INVARIANT。"""
    mf.set_detector_profile("legacy")
    assert mf.as_floats("1.260") is None
    mf.set_detector_profile("hardened")
    assert mf.as_floats("1.260") == [1.26]
    assert mf.as_floats(["0.5", "0.50"]) == [0.5, 0.5]
    assert mf.as_floats("n/a") is None
    assert mf.as_floats(["0.5", "n/a"]) is None
    assert mf.classify_series([1.26] * 5)["verdict"] == "INVARIANT"


def test_echo_branch_does_not_shadow_output():
    """响应回显请求体时，DFS 过去先取到 input.gm（随扫参变）⇒ 恒定输出伪装成 RESPONSIVE。"""
    body = lambda v: {"input": {"gm": v}, "results": {"gm": 1.0}}
    mf.set_detector_profile("legacy")
    assert mf.find_key(body(9.0), "gm") == 9.0          # legacy 命中回显
    mf.set_detector_profile("hardened")
    assert mf.find_key(body(9.0), "gm") == 1.0          # 加固跳过回显
    assert mf.find_key(body(9.0), "input.gm") == 9.0     # 显式路径仍权威
    vals = [mf.as_floats(mf.find_key(body(v), "gm"))[0] for v in [1.0, 2.0, 3.0]]
    assert mf.classify_series(vals)["verdict"] == "INVARIANT"


def test_quantized_rigid_ratio_is_caught():
    """恒比但两端 round(x,4)：按 1e-9 判会判 RESPONSIVE，按分辨率误差带必须判 RIGID_RATIO_QUANTIZED。"""
    kt = [0.4879, 0.4610, 0.4323, 0.4019, 0.3699]
    kq = [round(0.1 * k, 4) for k in kt]
    mf.set_detector_profile("legacy")
    assert mf.classify_ratio(kq, kt)["verdict"] == "RESPONSIVE"
    mf.set_detector_profile("hardened")
    res = mf.classify_ratio(kq, kt)
    assert res["verdict"] == "RIGID_RATIO_QUANTIZED"
    assert res["ratio_rel_band"] > res["relative_spread"] >= 0.0


def test_independent_polynomials_still_not_rigid():
    """加固不能把独立的粗糙多项式误判成恒比（过去单测钉住的假阳性边界）。"""
    kt = [0.30, 0.25, 0.20, 0.14, 0.07]
    kq = [0.032, 0.030, 0.026, 0.020, 0.012]
    assert mf.classify_ratio(kq, kt)["verdict"] == "RESPONSIVE"


def test_blind_jitter_is_quantized_invariant():
    """硬编码值 × (1+1e-8·i) 的伪抖动：浮点判据看不见，分辨率判据必须看见。"""
    vals = [1.26 * (1.0 + 1e-8 * i) for i in range(5)]
    mf.set_detector_profile("legacy")
    assert mf.classify_series(vals, None)["verdict"] == "RESPONSIVE"
    mf.set_detector_profile("hardened")
    res = mf.classify_series(vals, None)
    assert res["verdict"] == "INVARIANT_QUANTIZED"
    assert res["resolution_kind"] == "grid"


# ── ③ 方向量尾部夹紧 + 物理零位豁免 ──────────────────────────────────────────
def test_clamped_nonzero_tail_is_plateau():
    """d==0 逃过方向违背、又未声明 plateau_keys：过去判 RESPONSIVE。"""
    seq = [3.53, 3.17, 2.94, 2.94, 2.94]
    mf.set_detector_profile("legacy")
    assert mf.classify_series(seq, "dec")["verdict"] == "RESPONSIVE"
    mf.set_detector_profile("hardened")
    assert mf.classify_series(seq, "dec")["verdict"] == "PLATEAU"


def test_zero_floor_is_not_plateau():
    """K_T = max(0, ·) 的物理零位不是截断夹紧，不得误杀。"""
    assert mf.plateau_tail([0.42, 0.31, 0.18, 0.0, 0.0])["verdict"] == "RESPONSIVE"
    assert mf.classify_series([0.42, 0.31, 0.18, 0.0, 0.0], "dec")["verdict"] == "RESPONSIVE"


def test_float_noise_is_not_direction_violation():
    """相邻近等值点上的 1e-13 浮点噪声过去构成"方向违背"（把真曲线判成 MOCK）。"""
    base = [2.0, 2.0, 1.8, 1.5, 1.1]
    noisy = [b * (1 + 1e-13 * (-1) ** (i + 1)) for i, b in enumerate(base)]
    mf.set_detector_profile("legacy")
    assert mf.classify_series(noisy, "dec")["verdict"] == "DIRECTION_VIOLATION"
    mf.set_detector_profile("hardened")
    res = mf.classify_series(noisy, "dec")
    assert res["verdict"] != "DIRECTION_VIOLATION"
    assert res["direction_violation"] is False
    assert res["direction_mag_floor"] > 0.0


# ── ④ 语料级混淆矩阵（灵敏度/特异度）────────────────────────────────────────
def test_corpus_labels_are_balanced_and_distinct():
    corpus = sa.build_corpus()
    ids = [c["id"] for c in corpus]
    assert len(ids) == len(set(ids))
    assert sum(1 for c in corpus if c["truth"] == "MOCK") == 10
    assert sum(1 for c in corpus if c["truth"] == "REAL") == 4
    assert {c["truth"] for c in corpus} == {"MOCK", "REAL"}


def test_hardened_profile_has_full_sensitivity_and_specificity():
    corpus = sa.build_corpus()
    hard = sa.run_profile(corpus, "hardened")
    assert hard["sensitivity"] == 1.0, hard["missed"]
    assert hard["specificity"] == 1.0, hard["false_alarms"]


def test_legacy_profile_reproducibly_misses_five_channels():
    """legacy 口径的漏判清单是本单的证据基线：它必须稳定复现，否则差值不可信。"""
    legacy = sa.run_profile(sa.build_corpus(), "legacy")
    assert sorted(legacy["missed"]) == [
        "mock_blind_jitter", "mock_clamped_nonzero", "mock_echo_shadow",
        "mock_quantized_ratio", "mock_string_encoded"]
    assert legacy["specificity"] == 1.0        # 原判据不误杀，只是看不见伪装
    assert legacy["sensitivity"] <= 0.5


def test_audit_artifact_is_current():
    """落盘工件必须与当前代码一致（防"报告数字与代码脱钩"）。"""
    path = sa.OUT_PATH
    if not os.path.exists(path):
        pytest.skip("audit artifact not generated in this worktree")
    doc = json.load(open(path, encoding="utf-8"))
    assert doc["hardened"]["sensitivity"] == 1.0
    assert doc["hardened"]["specificity"] == 1.0
    assert doc["legacy"]["sensitivity"] < doc["hardened"]["sensitivity"]