#!/usr/bin/env python3
"""Generate E3 artifacts: case summary, injection receipt, comparison doc, figures.

Everything here is derived from the case store / comparison JSON, so no number in the
generated documentation is hand-copied. Figures are plain-English labelled (the build
image ships no CJK font; domain names stay in the data, not in the plot).

    python3 -m tools.precedent_library.make_artifacts [--bootstrap /path/BOOTSTRAP.md]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from tools.precedent_library import goal_quality as gq  # noqa: E402
from tools.precedent_library import library as lib  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs" / "precedent_library"
ARTIFACTS = REPO_ROOT / "artifacts" / "precedent_library"
DEFAULT_BOOTSTRAP = Path("/root/tp-agent/AGENT_BOOTSTRAP.md")


def _warp_colors(n: int) -> list[str]:
    base = ["#2b6cb0", "#2f855a", "#6b46c1", "#b7791f", "#c53030"]
    return [base[i % len(base)] for i in range(n)]


def figure_grades(summary: dict, out: Path) -> Path:
    grades = summary["by_grade"]
    order = ["S1", "S2", "S3", "S4", "anti"]
    labels = [g for g in order if g in grades]
    counts = [grades[g] for g in labels]
    fig, ax = plt.subplots(figsize=(6.2, 3.4), dpi=150)
    bars = ax.bar(labels, counts, color=_warp_colors(len(labels)), width=0.6)
    for bar, count in zip(bars, counts):
        ax.annotate(str(count), (bar.get_x() + bar.get_width() / 2, count),
                    ha="center", va="bottom", fontsize=10)
    ax.set_ylabel("cases")
    ax.set_title(f"Precedent library by grade (n={summary['total']}, S4 track marked vacant)")
    ax.set_ylim(0, max(counts) + 1)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def figure_goal_quality(comparison: dict, out: Path) -> Path:
    rows = comparison["rows"]
    labels = [r["sample_id"] for r in rows]
    before = [r["before"]["score"] for r in rows]
    after = [r["after"]["score"] for r in rows]
    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(6.6, 3.6), dpi=150)
    w = 0.36
    b1 = ax.bar([i - w / 2 for i in x], before, w, label="before (goal as issued)", color="#a0aec0")
    b2 = ax.bar([i + w / 2 for i in x], after, w, label="after (+ precedent injection)", color="#2b6cb0")
    for bar in list(b1) + list(b2):
        ax.annotate(f"{bar.get_height():.2f}", (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                    ha="center", va="bottom", fontsize=8)
    ax.axhline(0.875, color="#c53030", lw=1, ls="--")
    ax.annotate("excellent band (>=0.875)", (len(labels) - 0.5, 0.878), ha="right",
                va="bottom", fontsize=7, color="#c53030")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("falsifiability score")
    ax.set_title(f"Goal quality before/after injection (mean {comparison['summary']['mean_before']:.3f} -> "
                 f"{comparison['summary']['mean_after']:.3f})", fontsize=9)
    ax.legend(fontsize=8, frameon=False, loc="lower right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def write_injection_doc(meta: dict, cases, lines: list[str], receipt: dict, bootstrap: Path) -> Path:
    table = []
    for case in cases:
        if case.grade == "anti":
            continue
        table.append(f"| `{case.id}` | {case.grade} | {case.title} | {len(case.injection_line)} |")
    doc = f"""# E3 注入块（PLAYBOOK append-only 通道）

> 机器生成：`python3 -m tools.precedent_library.make_artifacts`
> 目标文件：`{bootstrap}`；落位锚点：`{lib.PLAYBOOK_ANCHOR}` 之前；单条上限 {lib.MAX_LINE_CHARS} 字符。

## 1. 渲染块（{len(lines)} 条）

```text
{chr(10).join(lines)}
```

## 2. 注入语义（测试可失败）

| 不变式 | 行为 | 验收测试 |
|---|---|---|
| append-only | 既有字节与顺序原样保留，新行插在锚点前 | `test_injection_is_append_only_and_idempotent` |
| 幂等 | 同一 case token 已存在即跳过，重跑插入 0 条 | 同上 |
| 锚点缺失即报错 | 绝不静默写到错误小节 | `test_injection_refuses_missing_anchor` |
| 字符预算 | 单条 ≤{lib.MAX_LINE_CHARS} 字符（CJK 计 1） | `test_injection_lines_within_budget_and_unique` |
| 回执 | `apply` 后把新行逐条读回，缺失即抛错 | 注入函数内置 |

## 3. 注入回执（对目标文件的现场记录）

```json
{json.dumps(receipt, ensure_ascii=False, indent=2)}
```

读法：`apply` = 首次落地写入的行数；`rerun` = 紧接着再执行一次的干跑结果（`inserted_count=0`
即幂等）；`verified_in_target` = 目标文件中逐条读回命中的行数。

## 4. 判例 → 注入行对应

| case | 级别 | 标题 | 行字符数 |
|---|---|---|---|
{chr(10).join(table)}

## 5. 用法

```bash
# 预演（不写盘）
python3 -m tools.precedent_library.library inject --target <BOOTSTRAP.md>
# 落地（append-only + 幂等，可反复执行）
python3 -m tools.precedent_library.library inject --target <BOOTSTRAP.md> --apply
```

## 6. 局限

- 注入只保证"开工前看见判据与反例"；它不能替代独立评审（S4 仍空缺即为此故）。
- 目标文件在仓库之外（agent 运行时文件），本仓只提交渲染块与回执；跨环境部署需再执行一次
  `--apply`（幂等，重复执行安全）。
"""
    path = DOCS / "INJECTION_PLAYBOOK_BLOCK.md"
    path.write_text(doc, encoding="utf-8")
    return path


def write_comparison_doc(comparison: dict) -> Path:
    rows = []
    for r in comparison["rows"]:
        rows.append(
            f"| `{r['sample_id']}` | {r['source']} | {r['before']['score']:.3f} ({r['before']['band']}) | "
            f"{r['after']['score']:.3f} ({r['after']['band']}) | {r['delta']:+.3f} | "
            f"{', '.join(r['recovered_checks']) or '—'} |"
        )
    s = comparison["summary"]
    doc = f"""# 注入后新单 goal 质量对比样本

> 机器生成：`python3 -m tools.precedent_library.goal_quality` + `make_artifacts`
> 样本源：`tools/precedent_library/data/goal_quality_samples.json`（真实 mission goal 原文节录，
> 带 mission id 与抓取时间戳；由 `build_goal_samples.py` 从控制面重取，不手抄）。

## 1. 打分口径（8 项可证伪属性，等权，代码即定义）

| # | 属性 | 机检正则（摘要） |
|---|---|---|
{chr(10).join(f"| {i+1} | {label} | `{pattern}` |" for i, (_name, pattern, label) in enumerate(gq.CHECKS))}

分档：`excellent ≥ 0.875`、`adequate ≥ 0.625`、`weak < 0.625`。
`after` = 同一段 goal 原文 + 注入块（`injection_suffix`，确定性拼接）。

## 2. 样本结果

| 样本 | 来源 | before | after | Δ | 新覆盖的属性 |
|---|---|---|---|---|---|
{chr(10).join(rows)}

## 3. 汇总

| 指标 | 值 |
|---|---|
| 样本数 | {s['n']} |
| 平均 before | {s['mean_before']:.4f} |
| 平均 after | {s['mean_after']:.4f} |
| 平均 Δ | {s['mean_delta']:+.4f} |
| Δ 区间 | {s['min_delta']:+.3f} … {s['max_delta']:+.3f} |
| 通道 | {s['channel']} |

图：`artifacts/precedent_library/fig2_goal_quality.png`；原始 JSON：
`artifacts/precedent_library/goal_quality_comparison.json`。

## 4. 怎么读这张表（诚实边界）

1. **Δ=0 的样本是饱和证据**：`m5101` 的 goal 本就含数字判据/工件路径/重跑命令/基线对照，
   注入无从增益——说明评分器不是"给注入块放水"的橡皮图章。
2. **这不是行为改变实验**：`after` 是文本改写，测的是注入块对"可证伪属性"的覆盖能力；
   真实行为改变需要后续单的对照（n={s['n']}，不作统计推断）。
3. **正则只看表面特征**：它无法判断引用的基线是否**正确**——那仍是独立评审的职责
   （与 S4 空缺同一原因）。
"""
    path = DOCS / "GOAL_QUALITY_COMPARISON.md"
    path.write_text(doc, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", default=str(DEFAULT_BOOTSTRAP))
    parser.add_argument("--apply", action="store_true",
                        help="actually append to the bootstrap PLAYBOOK section")
    args = parser.parse_args()

    DOCS.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    meta, cases = lib.load_cases()
    errors = lib.validate_cases(cases)
    if errors:
        raise SystemExit(f"case store invalid: {errors}")

    lines = lib.render_playbook_block(cases, gate_line=meta.get("injection_gate_line"))
    comparison = gq.build_comparison(gq.load_samples(), lines)
    summary = lib.summarize(cases)

    (ARTIFACTS / "case_summary.json").write_text(
        json.dumps({"summary": summary, "cases": [c.id for c in cases]}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (ARTIFACTS / "goal_quality_comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    (ARTIFACTS / "injection_block.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    fig1 = figure_grades(summary, ARTIFACTS / "fig1_grades.png")
    fig2 = figure_goal_quality(comparison, ARTIFACTS / "fig2_goal_quality.png")

    bootstrap = Path(args.bootstrap)
    receipt_path = ARTIFACTS / "injection_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
    if bootstrap.exists():
        if args.apply:
            applied = lib.inject_playbook(bootstrap, lines, apply=True)
            receipt["apply"] = {
                "at": applied.get("target") and __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc).replace(microsecond=0).isoformat(),
                "target": str(bootstrap), "anchor": lib.PLAYBOOK_ANCHOR,
                "inserted_count": applied["inserted_count"],
                "skipped_count": applied["skipped_count"],
            }
        dry = lib.inject_playbook(bootstrap, lines)
        target_text = bootstrap.read_text(encoding="utf-8")
        receipt["rerun"] = {
            "target": str(bootstrap), "anchor": lib.PLAYBOOK_ANCHOR,
            "inserted_count": dry["inserted_count"], "skipped_count": dry["skipped_count"],
            "verified_in_target": sum(1 for line in lines if line in target_text),
            "lines_total": len(lines),
        }
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    write_injection_doc(meta, cases, lines, receipt, bootstrap)
    write_comparison_doc(comparison)

    print(json.dumps({
        "cases": summary["total"], "lines": len(lines),
        "figures": [str(fig1), str(fig2)],
        "injection": receipt.get("rerun", {}),
        "goal_quality": comparison["summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())