"""报告门禁 v2（论文级六要素）验证欠账回归测试。

背景（详见 docs/reports/report_gate_validation_2026-11-01.md）
------------------------------------------------------------
生产门禁 v1 是 keyword-only 实现，存在三类误放行：
  A. 组间 AND 被写成组内 OR：正文只出现「参数表」即算满足「物理背景」；
  B. 「图」字出现即算满足图表（figures 列表为空也过）；
  C. 完全没有第 ⑥ 要素（误差判定）检查。

本文件直接 import ``services.report_gate``（纯函数、无 DB/FastAPI 依赖），
若哪天有人把判据重新内联进 API 模块，``test_api_module_does_not_refork_gate``
会立刻变红——防止"测试副本 vs 生产实现"再次漂移。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from services.report_gate import (  # noqa: E402
    REQUIRED_SECTIONS,
    check_report_quality,
)

FIG = [{"name": "fig1", "caption": "阻力曲线 vs ITTC-1957"}]


def codes(result: dict) -> list[str]:
    return result["issue_codes"]


# ── A. 组间 AND 语义 ────────────────────────────────────────────────────────
def test_physics_requires_both_problem_and_geometry():
    """只给参数、不给物理问题描述 → 不算满足①（v1 会判过，这是误放行）。"""
    md = """
## 参数表
表1 给出 L=10m, B=2m, Cb=0.6。
## 方法
LBM，网格 200 万。
## 验证
与 ITTC-1957 对标。
## 局限
仅适用于 Fr<0.35。
## 误差
关键量误差 1.8%<3%。
"""
    r = check_report_quality(md, FIG)
    assert "missing_physics_statement" in codes(r)
    assert r["checks"]["physics_statement"] is False


def test_physics_passes_with_problem_and_geometry():
    md = """
## 案例介绍
某 3000 TEU 集装箱船（物理背景：低速兴波阻力主导），几何 L=180m, B=28m, Cb=0.62。
## 方法
RANS k-ω SST，网格 1200 万。
## 验证
与 ITTC-1957 对标。
## 局限
仅 Fr=0.20 单工况。
## 误差判定
总阻力误差 2.4%<3%，可接受。
"""
    r = check_report_quality(md, FIG)
    assert r["checks"]["physics_statement"] is True, r["issues"]


# ── B. 图：真图 or 显式无图理由 ─────────────────────────────────────────────
def test_figure_keyword_mention_alone_is_rejected():
    """正文写「见图1」但 figures 为空、也没写无图理由 → 拒绝（v1 会判过）。"""
    md = """
## 物理背景
圆柱绕流，几何 D=1m。
## 方法
LBM D2Q9。
## 验证
与 Schäfer-Turek 对标。
## 结果
阻力系数见图1 所示曲线。
## 局限
Re=100 单点。
## 误差
Cd 误差 1.2%<3%。
"""
    r = check_report_quality(md, [])
    assert "missing_figures" in codes(r)
    assert r["checks"]["figures"] is False


def test_explicit_no_figure_justification_is_accepted():
    """纯文档/后端任务：显式写明无图理由 → ④ 通过（v1 反而会误拒这类）。"""
    md = """
## 物理背景
本单为后端接口修复，不涉及仿真，无图（纯文档任务）。
## 方法
pytest 回归覆盖。
## 验证
与接口契约对标。
## 局限
仅覆盖 v1 接口。
## 误差判定
接口时延误差 0.9%<3%。
"""
    r = check_report_quality(md, [])
    assert r["checks"]["figures"] is True, r["issues"]
    assert r["details"]["no_figure_justification"] is True


def test_figures_require_caption():
    r = check_report_quality(_full_md(), [{"name": "fig1", "caption": ""}])
    assert "figures_missing_caption" in codes(r)


# ── C. 第 ⑥ 要素：误差判定 ─────────────────────────────────────────────────
def test_error_verdict_rejects_numbers_without_judgement():
    """列了误差数字但没有任何结论词 → 缺第 ⑥ 要素（v1 完全没这项检查）。"""
    md = """
## 物理背景
圆柱绕流，几何 D=1m。
## 方法
LBM D2Q9。
## 验证
与 Schäfer-Turek 对标。
## 局限
Re=100 单点。
## 结果
Cd 误差 0.57%, Cl 误差 2.03%。
"""
    r = check_report_quality(md, FIG)
    assert "missing_error_verdict" in codes(r)
    assert r["details"]["error_number"] is True
    assert r["details"]["verdict_word"] is False


def test_error_verdict_accepts_explicit_over_threshold_verdict():
    """显式回答「是否<3%」即为合规——即便结论是不达标，也胜过含糊。"""
    md = _full_md() + "\n关键量误差 18.9%，判据 <3%，不通过（如实记录）。\n"
    r = check_report_quality(md, FIG)
    assert r["checks"]["error_verdict"] is True, r["issues"]


@pytest.mark.parametrize("phrase", [
    "误差 2.5%<3%", "误差 0.74%，✅ 一致", "误差 1.62%，与解析解吻合",
    "误差 3.1%（略超判据）", "相对误差 0.5%，达标",
])
def test_calibrated_verdict_phrasings(phrase):
    """实测校准：一线报告用「一致/吻合/达标/判据」给结论（见审计 JSON）。"""
    md = _full_md() + "\n" + phrase + "\n"
    assert check_report_quality(md, FIG)["checks"]["error_verdict"] is True


def test_no_baseline_declaration_accepted():
    md = _full_md(validation="本算例无基准：无解析解、无实验数据可对标。")
    r = check_report_quality(md, FIG)
    assert r["checks"]["error_verdict"] is True
    assert r["details"]["no_baseline_declared"] is True


# ── 全要素 & 结构守卫 ───────────────────────────────────────────────────────
def test_full_six_element_report_passes():
    r = check_report_quality(_full_md(), FIG)
    assert r["passed"] is True, r["issues"]
    for k in ("physics_statement", "method", "validation", "figures",
              "limitations", "error_verdict"):
        assert r["checks"][k] is True, k
    assert r["issue_codes"] == []


def test_empty_report_rejected_with_all_codes():
    r = check_report_quality("", [])
    assert r["passed"] is False
    assert "missing_physics_statement" in codes(r)
    assert "missing_error_verdict" in codes(r)


def test_api_module_does_not_refork_gate():
    """结构守卫：API 层必须 import 共享实现，不得再内联一份判据。"""
    src = (BACKEND / "api" / "tensor_reports.py").read_text(encoding="utf-8")
    assert "from services.report_gate import" in src
    # 判据关键词表不得在 API 模块里重复定义（否则又会出现副本漂移）
    assert not re.search(r"^REQUIRED_SECTIONS\s*=\s*\{", src, re.M)


def _full_md(validation: str = "与 ITTC-1957 及 Schäfer-Turek DNS 对标。") -> str:
    return f"""
## ① 案例介绍
3000 TEU 集装箱船阻力预报（物理背景：低速兴波 + 粘压阻力），
几何 L=180m, B=28m, T=10.5m, Cb=0.62，工况 Fr=0.20。

## ② 方法与流程
RANS k-ω SST 求解器，网格 1200 万，y+≈1，迭代 3000 步收敛（残差 1e-5）。
工具链：TensorFVM + matplotlib。

## ③ 验证
{validation}

## ④ 结果
总阻力系数 Ct=4.32e-3（见图中比较曲线）。

## ⑤ 注意点
仅覆盖 Fr=0.20 单工况，未做尺度效应修正。

## ⑥ 误差判定
总阻力误差 2.4%<3%，可接受。
"""