"""报告门禁灰度（off/warn/enforce）单测 —— 纯函数，无 DB / FastAPI 依赖。

被测对象：``backend/services/report_gate.py`` 的
``check_report_quality_v1`` / ``check_report_quality`` / ``gate_mode`` / ``evaluate_report``。

背景（详见 docs/reports/report_gate_rollout_2026-11-02.md）
------------------------------------------------------------
2026-11-01 的审计（docs/reports/report_gate_audit_2026-11-01.json）已量化：生产在用的
v1 keyword-only 门禁 **误放行率 35.84%**（v1 判过、六要素 v2 判否），而 v2 只活在审计
工具里——审计结论没有落到生产，是典型的「验证欠账」。本文件钉死灰度语义，保证：
  * off / warn 期 qa_status 与灰度前**逐位同构**（不打断在飞的报告流）；
  * warn 期 v2 只作 advisory，绝不改状态；
  * enforce 期由 v2 决定，且对四类对抗样本（参数表替代物理背景、只提「图」字、
    有数字无结论、图无 caption）全部打回，对合规样本不误拒。

运行：
    cd backend && python3 -m pytest tests/test_report_gate_rollout.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from services.report_gate import (  # noqa: E402
    DEFAULT_GATE_MODE,
    GATE_MODES,
    check_report_quality,
    check_report_quality_v1,
    evaluate_report,
    gate_mode,
)
from services.report_gate import GATE_MODE_ENV  # noqa: E402


# ── 夹具 ──────────────────────────────────────────────────────────────────────
COMPLIANT_MD = """
## ① 案例介绍
KCS 集装箱船阻力预报，物理背景：低速兴波 + 粘压阻力。
几何 L=230m, B=32.2m, T=10.8m, Cb=0.651，工况 Fr=0.26。

## ② 怎么做的
RANS k-ω SST，网格 1200 万，y+≈1，迭代 3000 步残差 1e-5；工具链 TensorFVM + matplotlib。

## ③ 计算结果
总阻力系数 CT=4.31e-3，ITTC-1957 摩擦线 Cf=2.62e-3，形状因子 1+Cf。

## ④ 结果图
见下图（波形等高线图与阻力收敛曲线）。

## ⑤ 注意点
仅覆盖 Fr<=0.30；自由面网格偏粗；不适用于浅水。

## ⑥ 误差判定
对标东京 2015 基准，关键量 CT vs 参考值 CT=4.36e-3，相对误差 1.15%，**误差 <3%**。
"""

GOOD_FIGS = [
    {"name": "wave_contour.png", "caption": "自由面波形等高线（Fr=0.26）"},
    {"name": "conv.png", "caption": "总阻力随迭代收敛曲线"},
]


def _codes(result: dict) -> list[str]:
    return [i.split(":")[0] for i in result["issues"]]


# ── 1. 模式解析 ───────────────────────────────────────────────────────────────
def test_gate_mode_default_is_warn(monkeypatch):
    monkeypatch.delenv(GATE_MODE_ENV, raising=False)
    assert DEFAULT_GATE_MODE == "warn"
    assert gate_mode() == "warn"


def test_gate_mode_env_override_and_fallback(monkeypatch):
    for m in GATE_MODES:
        monkeypatch.setenv(GATE_MODE_ENV, m.upper())
        assert gate_mode() == m
    monkeypatch.setenv(GATE_MODE_ENV, "yolo")  # 非法环境值不得把门禁打开/关死
    assert gate_mode() == DEFAULT_GATE_MODE


def test_gate_mode_explicit_invalid_raises():
    with pytest.raises(ValueError):
        gate_mode("strict")
    with pytest.raises(ValueError):
        evaluate_report(COMPLIANT_MD, GOOD_FIGS, mode="yolo")


# ── 2. off / warn：qa_status 与灰度前逐位同构 ─────────────────────────────────
@pytest.mark.parametrize("mode", ["off", "warn"])
def test_off_and_warn_preserve_v1_verdict(mode):
    corpus = [
        (COMPLIANT_MD, GOOD_FIGS),
        ("## 参数表\nL=230m B=32m", []),          # 只有参数表（v1 误放行）
        ("## 结果\n见图 3，误差 12%", []),          # 只提「图」字 + 有数字无结论
        ("", []),
    ]
    for md, figs in corpus:
        ev = evaluate_report(md, figs, mode=mode)
        v1 = check_report_quality_v1(md, figs)
        assert ev["decided_by"] == "v1"
        assert ev["passed"] == v1["passed"]
        assert ev["issues"] == v1["issues"]


def test_warn_exposes_advisory_but_never_flips_status():
    md = "## 背景 参数表\n方法 验证\n见图\n注意"          # v1 过、v2 不过（无真图/无误差判定）
    ev = evaluate_report(md, [], mode="warn")
    assert ev["v1"]["passed"] is True
    assert ev["v2"]["passed"] is False
    assert ev["passed"] is True                 # 状态仍由 v1 写
    assert ev["would_flip"] is True             # 但如实记账：enforce 后会打回
    assert ev["v2_advisory_issues"]            # advisory 非空，可落库取证


# ── 3. enforce：v2 决定，且对抗样本全打回 ────────────────────────────────────
def test_enforce_accepts_compliant_control():
    ev = evaluate_report(COMPLIANT_MD, GOOD_FIGS, mode="enforce")
    assert ev["decided_by"] == "v2"
    assert ev["passed"] is True, ev["issues"]
    assert ev["v2_advisory_issues"] == []


ADVERSARIAL = {
    "A_param_table_only": ("## 参数表\nL=230m B=32.2m T=10.8m Cb=0.651\n方法 验证 注意 图", []),
    "B_figure_word_only": ("## 物理背景 几何 参数\n方法 验证\n见图 3 的比较曲线\n注意 局限", []),
    "C_numbers_without_verdict": (
        "## 物理背景 几何 参数\n方法 验证\n见图 局限 注意\n误差 1.15% 与 12.4% 均列出", []
    ),
    "D_figure_without_caption": (COMPLIANT_MD, [{"name": "a.png", "caption": ""}]),
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_enforce_rejects_adversarial(name):
    md, figs = ADVERSARIAL[name]
    ev = evaluate_report(md, figs, mode="enforce")
    assert ev["passed"] is False, f"{name} 不应被放行"
    assert ev["v1"]["passed"] is True, "对抗样本的意义就是 v1 会误放行"


def test_enforce_faithful_honest_fail_without_baseline_passes_error_verdict():
    md = COMPLIANT_MD.replace(
        "对标东京 2015 基准，关键量 CT vs 参考值 CT=4.36e-3，相对误差 1.15%，**误差 <3%**。",
        "无基准可比对（该工况无实验数据），故不声明误差上限；这是 honest FAIL，不粉饰。",
    )
    v2 = check_report_quality(md, GOOD_FIGS)
    assert v2["checks"]["error_verdict"] is True
    assert v2["details"]["no_baseline_declared"] is True


# ── 4. 与灰度前生产判据的等价性 ───────────────────────────────────────────────
def test_v1_archive_matches_legacy_semantics():
    """v1 档案必须保留三处历史失效模式（否则 warn 期不再与线上同构）。"""
    # ① 组间 AND 被拍平：只写「参数表」即算有物理背景
    assert check_report_quality_v1("参数表", [])["checks"]["physics_statement"] is True
    # ④ 正文出现「图」字即算有图，figures 为空也过
    assert check_report_quality_v1("图", [])["checks"]["figures"] is True
    # ⑥ v1 根本没有误差判定要素
    assert "error_verdict" not in check_report_quality_v1("", [])["checks"]
    # 对照：v2 对同一输入判否
    assert check_report_quality("参数表", [])["passed"] is False


def test_api_module_delegates_to_shared_gate():
    src = (BACKEND / "api" / "tensor_reports.py").read_text(encoding="utf-8")
    assert "from services.report_gate import" in src
    assert "evaluate_report(" in src
    assert "REQUIRED_SECTIONS" not in src, "API 模块不得再持有判据关键词表（副本漂移）"


def test_default_env_is_read_once_per_call(monkeypatch):
    """模式是运行时可切换的：同一个报告在 warn/enforce 下判决可不同。"""
    md, figs = ADVERSARIAL["B_figure_word_only"]
    monkeypatch.setenv(GATE_MODE_ENV, "warn")
    warn = evaluate_report(md, figs)
    monkeypatch.setenv(GATE_MODE_ENV, "enforce")
    enforce = evaluate_report(md, figs)
    assert warn["passed"] is True and enforce["passed"] is False
    assert os.environ[GATE_MODE_ENV] == "enforce"