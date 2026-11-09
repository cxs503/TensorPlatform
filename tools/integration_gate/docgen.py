"""论文级报告生成器：把 gate 产物 + 域内叙述 (数据注入) 拼成六要素 markdown。

六要素骨架固化在代码里（案例/方法/结果/图/注意点/误差判定），**所有数字从
``seed_report.json`` 读取后格式化注入**，域内叙述从
``data/campaigns/<c>/doc_prose.json`` 注入（平台代码零领域语义）。

    python -m tools.integration_gate.docgen --report artifacts/.../seed_report.json \
        --prose data/campaigns/p158w2/doc_prose.json --out docs/XXX.md \
        --figures artifacts/.../figures
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _fmt(x: Any, sig: int = 4) -> str:
    if isinstance(x, float):
        if x == 0.0:
            return "0"
        if abs(x) < 1e-3 or abs(x) >= 1e5:
            return f"{x:.{sig - 1}e}"
        return f"{x:.{sig}g}"
    return str(x)


def _criteria_table(report: dict[str, Any]) -> list[str]:
    L = ["| 判据 | 是否满足 | 数字 | 判据文字 |", "|---|---|---|---|"]
    for k, v in report["integration_criteria"].items():
        L.append(f"| {k} | {'PASS' if v['passed'] else 'FAIL'} | "
                 f"`{json.dumps(v['value'], ensure_ascii=False)}` | {v['criterion']} |")
    return L


def _pair_tables(report: dict[str, Any]) -> list[str]:
    L: list[str] = []
    for p in report["pairs"]:
        L.append(f"### {p['id']} · {p['title']}")
        L.append("")
        L.append(f"- 类型 `{p['kind']}` · 结论 **{p['verdict']}** · 能力等级上限 "
                 f"`{p['cap_level']}` · 对照侧 `{p['counterpart_status']}`")
        if p.get("note"):
            L.append(f"- {p['note']}")
        L.append("")
        L.append("| 检查 | 结论 | 严重度 | 关键数字 |")
        L.append("|---|---|---|---|")
        for c in p["checks"]:
            d = c["detail"]
            keys = ["max_rel_dev", "rel_dev", "max_ratio", "raw_ratio", "value",
                    "threshold", "max_abs_dev", "scaled_dev", "driver", "response",
                    "a", "b", "param", "ratio_to_threshold", "n_points", "tol"]
            bits = [f"{k}={_fmt(d[k])}" for k in keys if isinstance(d.get(k), (int, float))]
            L.append(f"| `{c['check']}` | {c['verdict']} | {c['severity']} | "
                     f"{'; '.join(bits)} |")
        L.append("")
    return L


def _error_section(report: dict[str, Any]) -> list[str]:
    """误差判定：把每个互裁检查的关键量与容差逐项列出并回答"是否 <3%"。"""
    L = ["| 互裁检查 | 关键量 | 参考/容差 | 相对偏差 | 是否 <3% |", "|---|---|---|---|---|"]
    for p in report["pairs"]:
        for c in p["checks"]:
            d = c["detail"]
            if c["check"] == "metric_below" and isinstance(d.get("ratio_to_threshold"),
                                                            (int, float)):
                key = ("ratio_to_threshold", d["ratio_to_threshold"])
            elif c["check"] == "degenerate":
                key = None
            else:
                key = None
                for k in ("max_rel_dev", "rel_dev", "scaled_dev", "max_abs_dev",
                          "max_ratio"):
                    if isinstance(d.get(k), (int, float)):
                        key = (k, d[k])
                        break
            ref = d.get("tol") if isinstance(d.get("tol"), (int, float)) else d.get("threshold")
            if key is None:
                L.append(f"| `{c['check']}` | 定性检查 | {_fmt(ref)} | — | 不适用（定性） |")
                continue
            k, v = key
            if k in ("max_abs_dev", "max_ratio"):
                verdict = "见上（非相对量）"
            elif k == "ratio_to_threshold":
                verdict = "是" if v < 1.0 else "否（未达约定门）"
            else:
                verdict = "是" if v < 0.03 else "否"
            L.append(f"| `{c['check']}` | `{k}` | {_fmt(ref)} | {_fmt(v)} | {verdict} |")
    L.append("")
    L.append("说明：`max_ratio` 是**分支切换处的跳变比**（不是数值误差），1 表示完全连续；"
             "`ratio_to_threshold` = 实测值/约定阈值，<1 表示优于约定门。"
             "未达门的 honest FAIL 由下文逐条列出，不粉饰。")
    return L


def render_doc(report: dict[str, Any], prose: dict[str, Any], fig_dir: str | None) -> str:
    L: list[str] = []
    A = L.append
    npass = sum(1 for v in report["integration_criteria"].values() if v["passed"])
    A(f"# {prose.get('title', report['campaign'] + ' 集成与互裁门报告')}")
    A("")
    A(f"- 战役 `{report['campaign']}` · schema `{report['schema']}` · "
      f"生成 {report['generated_at']}")
    A(f"- 基线 commit `{report['base_commit']}` · 报告内容 sha256 "
      f"`{report['content_sha256']}`")
    A(f"- 台账内容 sha256 `{report['ledger']['content_sha256']}` · "
      f"等级分布 `{json.dumps(report['ledger']['counts'], ensure_ascii=False)}`")
    A(f"- 机器自检 I1–I5：**{npass}/{len(report['integration_criteria'])} 满足**")
    A("")
    A("## 1 案例介绍（物理背景 / 战役几何 / 参数表）")
    A("")
    for para in prose.get("case", []):
        A(para)
        A("")
    A("| 节点 | 仓 | 判据统计 (P/F/W) | 通过率 | 总体判定 | 能力等级 | 基线态 |")
    A("|---|---|---|---|---|---|---|")
    for nb in report["nodes"]:
        c = nb["criteria_counts"]
        pr = "—" if nb["pass_rate"] is None else f"{nb['pass_rate'] * 100:.1f}%"
        A(f"| `{nb['node_ext_id']}` | {nb['repo']} | {c['PASS']}/{c['FAIL']}/{c['WITHHELD']} | "
          f"{pr} | **{nb['verdict']}** | {', '.join(nb['levels']) or '—'} | "
          f"{nb['baseline'].get('state')} |")
    A("")
    A("## 2 怎么做的（方法 / 流程 / 工具链）")
    A("")
    for para in prose.get("method", []):
        A(para)
        A("")
    A("复现命令：")
    A("")
    A("```bash")
    for cmd in prose.get("repro", []):
        A(cmd)
    A("```")
    A("")
    A("## 3 计算结果（程序化注入，零手抄）")
    A("")
    A("### 3.1 互裁结论与逐检查数字")
    A("")
    L.extend(_pair_tables(report))
    A("### 3.2 能力台账增量（证据定级）")
    A("")
    A("| 能力 | 节点 | 等级 | 状态 | 合并态 | honest_FAIL | 备注 |")
    A("|---|---|---|---|---|---|---|")
    for e in report["ledger"]["entries"]:
        A(f"| `{e['capability_id']}` | `{e['node_ext_id']}` | **{e['level']}** | {e['status']} | "
          f"{e['merge_state']} | {'Y' if e['honest_fail'] else '—'} | {e.get('summary', '')[:60]} |")
    A("")
    A("### 3.3 基线对齐（I4）")
    A("")
    A("| 节点 | 分支 | commit | 领先 main | fork 后 main 漂移 | 契约必需/已有 |")
    A("|---|---|---|---|---|---|")
    for a in report["baseline"]:
        f = a.get("facts") or {}
        A(f"| `{a['node_ext_id']}` | {a['branch']} | `{(a['base_commit'] or '')[:12]}` | "
          f"{f.get('ahead_of_main')} | {f.get('drift_on_main')} | "
          f"{'Y' if a['contract_required'] else '—'}/{'Y' if a['contract_present'] else '—'} |")
    A("")
    A("### 3.4 遗留缺口与下一波播种提案")
    A("")
    A("| 缺口 | 类型 | 说明 |")
    A("|---|---|---|")
    for g in report["gaps"]:
        A(f"| `{g['id']}` | {g['kind']} | {(g['detail'] or '').replace('|', '/')[:110]} |")
    A("")
    A("| seed | 类型 | 理由 | 验收提示 |")
    A("|---|---|---|---|")
    for s in report["next_wave_seeds"]:
        A(f"| `{s['seed_id']}` | {s['kind']} | {s['rationale'][:90]} | "
          f"{s['acceptance_hint'][:90]} |")
    A("")
    A("### 3.5 机器自检 I1–I5")
    A("")
    L.extend(_criteria_table(report))
    A("")
    A("## 4 结果图")
    A("")
    figs = prose.get("figures") or []
    if figs:
        for f in figs:
            base = (Path(fig_dir) / f["file"]) if fig_dir else Path(f["file"])
            A(f"![{f['caption']}]({base})")
            A("")
            A(f"*{f['caption']}*")
            A("")
    else:
        A("（本战役未生成图；理由见 prose.figures_reason）")
        A("")
    A("## 5 注意点（踩坑 / 局限 / 适用范围）")
    A("")
    for para in prose.get("caveats", []):
        A(para)
        A("")
    A("## 6 误差判定（关键量 vs 基准，逐项回答是否 <3%）")
    A("")
    L.extend(_error_section(report))
    A("")
    for para in prose.get("error_notes", []):
        A(para)
        A("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="integration_gate.docgen")
    ap.add_argument("--report", required=True)
    ap.add_argument("--prose", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--figures", default=None)
    args = ap.parse_args(argv)
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    prose = json.loads(Path(args.prose).read_text(encoding="utf-8"))
    text = render_doc(report, prose, args.figures)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())