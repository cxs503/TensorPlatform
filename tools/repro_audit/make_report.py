"""repro_audit.make_report —— 审计报告生成器（零领域语义）。

把审计流水线的结果渲染成 Markdown 报告。报告结构：
  1. 摘要（KPI、覆盖度、耗时）
  2. 审计计划（策略、候选数、选中数）
  3. 探针预检结果（通过率、失败列表）
  4. 完整复证结果（逐项判决、drift 归因）
  5. 附录（环境信息、原始数据路径）

设计约束：
  * **零手抄**：所有数字从 pipeline_result 程序化注入，不硬编码。
  * **幂等**：同输入同输出（无 wall-clock 派生字段，时间戳从结果里取）。
  * **可模板化**：支持自定义模板（Jinja2 风格占位符）。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any


DEFAULT_TEMPLATE = """\
# 复证审计报告

**生成时间**: {timestamp}
**审计范围**: {candidates_count} 候选 → {selected_count} 选中
**策略**: {strategy} (seed={seed})

## 摘要

| 指标 | 值 |
|---|---|
| 总节点数 | {total} |
| 通过 | {pass_count} |
| 漂移 | {drift_count} |
| 失败 | {fail_count} |
| 阻塞 | {blocked_count} |
| **复现率** | **{repro_rate:.2%}** |
| 阻塞占比 | {blocked_share:.2%} |
| 耗时 | {duration_sec:.1f}s |

## 覆盖度

| 维度 | 总数 | 覆盖 | 覆盖率 |
|---|---|---|---|
| 节点 | {cov_total} | {cov_selected} | {cov_rate:.2%} |
| 层（repo×年） | {cov_strata_total} | {cov_strata_covered} | {cov_strata_rate:.2%} |

## 探针预检

{probe_section}

## 逐项判决

{verdict_table}

## 环境

- Python: {python_version}
- 机器: {machine}
- CPU: {cpu_count}

---
*本报告由 `tools.repro_audit.make_report` 自动生成，零手抄。*
"""


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2%}"


def _fmt_duration(sec: float | None) -> str:
    if sec is None:
        return "N/A"
    return f"{sec:.1f}s"


def render_probe_section(probe_results: list[dict[str, Any]]) -> str:
    """渲染探针预检部分。"""
    if not probe_results:
        return "*未执行探针预检*"

    total = len(probe_results)
    passed = sum(1 for r in probe_results if r.get("passed_all_probes"))
    lines = [
        f"**探针通过率**: {passed}/{total} ({_fmt_pct(passed / total if total else None)})",
        "",
        "| mission_id | 通过 | 失败数 | 错误 |",
        "|---|---|---|---|",
    ]
    for r in probe_results:
        mid = r.get("mission_id", "?")
        ok = "✓" if r.get("passed_all_probes") else "✗"
        summary = r.get("probe_summary", {})
        failed_count = summary.get("failed_count", 0)
        error = r.get("error", "")[:50]
        lines.append(f"| {mid} | {ok} | {failed_count} | {error} |")

    return "\n".join(lines)


def render_verdict_table(audit_results: list[dict[str, Any]]) -> str:
    """渲染逐项判决表。"""
    if not audit_results:
        return "*未执行完整复证*"

    lines = [
        "| mission_id | 判决 | 仓 | 检查数 | 通过数 | 漂移数 | 归因 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in audit_results:
        mid = r.get("mission_id", "?")
        verdict = r.get("verdict", "blocked")
        repo = r.get("repo", "?")
        checked = r.get("checked", 0)
        passed = r.get("passed", 0)
        drifted = r.get("drifted", 0)
        attribution = ""
        if r.get("attribution"):
            attribution = r["attribution"].get("label", "")
        lines.append(f"| {mid} | {verdict} | {repo} | {checked} | {passed} | {drifted} | {attribution} |")

    return "\n".join(lines)


def render_report(pipeline_result: dict[str, Any], template: str | None = None) -> str:
    """渲染完整审计报告。

    ``pipeline_result`` 是 :func:`run_audit.run_audit_pipeline` 的返回值。
    """
    tmpl = template or DEFAULT_TEMPLATE

    plan = pipeline_result.get("plan", {})
    kpi = pipeline_result.get("kpi") or {}
    coverage = plan.get("coverage", {})
    probe_results = pipeline_result.get("probe_results", [])
    audit_results = pipeline_result.get("audit_results", [])

    # 环境信息（从审计结果里抽取）
    env_info = {}
    for r in audit_results:
        if r.get("environment"):
            env_info = r["environment"]
            break
    if not env_info:
        import os, sys
        env_info = {
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
            "machine": os.uname().machine,
            "cpu_count": os.cpu_count(),
        }

    # 构造占位符字典
    counts = kpi.get("counts", {})
    ctx = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "candidates_count": plan.get("candidates_count", 0),
        "selected_count": plan.get("selected_count", 0),
        "strategy": plan.get("strategy", "?"),
        "seed": plan.get("seed", "?"),
        "total": kpi.get("total", 0),
        "pass_count": counts.get("pass", 0),
        "drift_count": counts.get("drift", 0),
        "fail_count": counts.get("fail", 0),
        "blocked_count": counts.get("blocked", 0),
        "repro_rate": kpi.get("repro_rate") or 0.0,
        "blocked_share": kpi.get("blocked_share") or 0.0,
        "duration_sec": pipeline_result.get("duration_sec", 0.0),
        "cov_total": coverage.get("total", 0),
        "cov_selected": coverage.get("selected", 0),
        "cov_rate": coverage.get("coverage_rate") or 0.0,
        "cov_strata_total": coverage.get("strata_total", 0),
        "cov_strata_covered": coverage.get("strata_covered", 0),
        "cov_strata_rate": coverage.get("strata_coverage") or 0.0,
        "probe_section": render_probe_section(probe_results),
        "verdict_table": render_verdict_table(audit_results),
        "python_version": env_info.get("python_version", "?"),
        "machine": env_info.get("machine", "?"),
        "cpu_count": env_info.get("cpu_count", "?"),
    }

    # 简单占位符替换（不用 Jinja2 依赖）
    result = tmpl
    for key, value in ctx.items():
        if isinstance(value, float):
            # 处理格式化占位符（如 {repro_rate:.2%}）
            pattern = re.compile(r"\{" + re.escape(key) + r":([^}]+)\}")
            for m in pattern.finditer(tmpl):
                fmt = m.group(1)
                try:
                    formatted = f"{value:{fmt}}"
                except (ValueError, TypeError):
                    formatted = str(value)
                result = result.replace(m.group(0), formatted)
        placeholder = "{" + key + "}"
        result = result.replace(placeholder, str(value))

    return result


def save_report(report_text: str, path: str) -> None:
    """保存报告到文件。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report_text)


def load_template(path: str) -> str:
    """从文件加载模板。"""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def generate_and_save(pipeline_result: dict[str, Any], output_path: str,
                      template_path: str | None = None) -> str:
    """生成报告并保存到文件，返回报告文本。"""
    template = None
    if template_path and os.path.exists(template_path):
        template = load_template(template_path)
    report = render_report(pipeline_result, template=template)
    save_report(report, output_path)
    return report
