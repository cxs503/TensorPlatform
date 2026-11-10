#!/usr/bin/env python3
"""Compose the paper-level report of an interoperation gate run.

Every number in the generated document is read from ``verdict.json`` /
``nodes.json`` / ``provenance.json``; the prose comes from an injected
``doc_prose.json``.  Nothing is typed twice, so the report cannot drift away from
the machine verdict.

    python3 -m tools.xsolver_interop.docgen \
        --verdict artifacts/p106w2_interop/verdict.json \
        --data data/campaigns/p106w2 \
        --prose data/campaigns/p106w2/doc_prose.json \
        --figures artifacts/p106w2_interop/figures \
        --out docs/P106W2-N11_INTEGRATION_GATE.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

PASS, FAIL, WITHHELD = "PASS", "FAIL", "WITHHELD"


def _load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _checks(verdict: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {c["id"]: c for item in verdict["items"] for c in item["checks"]}


def _key_numbers(check: Mapping[str, Any]) -> str:
    detail = check.get("detail") or {}
    parts: List[str] = []
    if "max_rel_diff" in detail:
        tol = detail.get("tolerance")
        parts.append(f"最大相对差 {detail['max_rel_diff'] * 100:.2f}%" +
                     (f"（容差 {tol * 100:.2f}%）" if isinstance(tol, (int, float)) else ""))
    if isinstance(detail.get("points"), list) and detail["points"]:
        points = detail["points"]
        if isinstance(points[0], Mapping) and "factor_below_lo" in points[0]:
            for point in points:
                parts.append(f"{point['label']}={point['value']:.4g}（参考下限的 1/{point['factor_below_lo']:.1f}）")
        elif isinstance(points[0], Mapping) and "rel_diff" in points[0]:
            parts.append(f"逐点互裁 {len(points)} 点，最大 {detail.get('max_rel_diff', 0) * 100:.2f}%")
    if isinstance(detail.get("entries"), list) and detail["entries"]:
        parts.append(", ".join(f"{e['label']}={e['value']:.6g}" for e in detail["entries"]
                               if e.get("value") is not None))
    if "n_violations" in detail:
        kinds = sorted({v["type"] for v in detail.get("violations") or []})
        parts.append(f"违约 {detail['n_violations']} 处（{', '.join(kinds) or '无'}）")
    if "missing_orders" in detail:
        parts.append(f"缺阶次 {detail['missing_orders']}（已含 {detail['provided_orders']}）")
    if "band_a" in detail:
        parts.append(f"工作带 [{detail['band_a'][0]:.4g}, {detail['band_a'][1]:.4g}] vs "
                     f"禁带 [{detail['band_b'][0]:.4g}, {detail['band_b'][1]:.4g}]，重叠 {detail['overlap_fraction'] * 100:.0f}%")
        return "; ".join(parts)
    if "record" in detail and isinstance(detail["record"], Mapping):
        record = detail["record"]
        parts.append(f"上游 pass={record.get('pass')}, cases_run={record.get('cases_run')}")
    if "spearman" in detail:
        parts.append(f"Spearman={detail['spearman']:.3f}")
    if "reason" in detail and not parts:
        parts.append(str(detail["reason"])[:120])
    if "error" in detail:
        parts.append(str(detail["error"])[:120])
    return "; ".join(parts) if parts else "—"


def _item_table(verdict: Mapping[str, Any]) -> List[str]:
    rows = ["| 验收项 | 判定 | PASS | FAIL | WITHHELD |", "|---|---|---|---|---|"]
    for item in verdict["items"]:
        counts = item["counts"]
        rows.append(f"| `{item['id']}` {item['title']} | **{item['status']}** | "
                    f"{counts[PASS]} | {counts[FAIL]} | {counts[WITHHELD]} |")
    return rows


def _check_tables(verdict: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for item in verdict["items"]:
        out += [f"#### {item['id']} — {item['title']}", "",
                "| 检查 | 类型 | 判定 | 关键数字 |", "|---|---|---|---|"]
        for check in item["checks"]:
            out.append(f"| `{check['id']}` | `{check['kind']}` | {check['status']} | {_key_numbers(check)} |")
        out.append("")
    return out


def _fracture_table(verdict: Mapping[str, Any]) -> List[str]:
    rows = ["| 验收项 | 检查 | 断裂类型 | 说明 |", "|---|---|---|---|"]
    for fracture in verdict["fractures"]:
        rows.append(f"| `{fracture['item']}` | `{fracture['check']}` | `{fracture['type']}` | "
                    f"{fracture['message']} |")
    return rows


def _artifact_table(verdict: Mapping[str, Any], provenance: Mapping[str, Any]) -> List[str]:
    by_node: Dict[str, Mapping[str, Any]] = {}
    for record in (provenance or {}).get("records") or []:
        by_node.setdefault(record.get("snapshot") or record.get("expected_snapshot") or "", record)
    rows = ["| 工件 | 来源节点 | 来源仓 | git commit | sha256(前12) | 可读 | 说明 |",
            "|---|---|---|---|---|---|---|"]
    for name, art in verdict["artifacts"].items():
        record = next((r for r in (provenance or {}).get("records") or []
                       if r.get("snapshot") == art["path"]), {})
        rows.append(
            f"| `{name}` | {record.get('node', '—')} | {record.get('repo', '—')} | "
            f"`{(record.get('git_commit') or '—')[:12]}` | `{(art['sha256'] or '')[:12]}` | "
            f"{'是' if art['present'] else '否'} | {art['reason'] or ''} |")
    return rows


def _node_table(nodes: Mapping[str, Any]) -> List[str]:
    rows = ["| 节点 | 仓 | 平台任务 | 状态 | 自评判据数 |", "|---|---|---|---|---|"]
    for node in (nodes or {}).get("nodes") or []:
        rows.append(f"| `{node['node']}` | {node.get('repo') or '—'} | {node.get('mission_id')} | "
                    f"{node.get('status')} | {len(node.get('acceptance_criteria') or [])} |")
    return rows


def _error_verdict_table(verdict: Mapping[str, Any]) -> List[str]:
    """Key quantities vs their reference with an explicit '<3 %?' answer."""
    rows = ["| 关键量 | 判定对象 | 数值/偏差 | 参考/容差 | 误差 | 是否 <3% |", "|---|---|---|---|---|---|"]
    checks = _checks(verdict)
    for check in checks.values():
        detail = check.get("detail") or {}
        if "max_rel_diff" in detail and isinstance(detail.get("tolerance"), (int, float)):
            dev = detail["max_rel_diff"]
            answer = "是" if dev < 0.03 else "否"
            rows.append(f"| `{check['id']}` | 跨节点共享参数 | {dev * 100:.2f}% | "
                        f"容差 {detail['tolerance'] * 100:.2f}% | 相对差 | {answer} |")
        if isinstance(detail.get("points"), list) and detail["points"] and "factor_below_lo" in detail["points"][0]:
            for point in detail["points"]:
                answer = "是" if point["inside"] else f"否（差 {point['factor_below_lo']:.1f} 倍）"
                rows.append(f"| `{check['id']}` {point['label']} | 对文献带 | {point['value']:.4g}% | "
                            f"[{detail['band'][0]:g}, {detail['band'][1]:g}]% | 带外 | {answer} |")
        if "n_violations" in detail:
            answer = "是" if detail["n_violations"] == 0 else "否（契约违约，非数值误差）"
            rows.append(f"| `{check['id']}` | 接口契约 | {detail['n_violations']} 处违约 | 0 处 | 结构性 | {answer} |")
        if "missing_orders" in detail:
            answer = "是" if not detail["missing_orders"] else "否（激励阶次缺失，非数值误差）"
            rows.append(f"| `{check['id']}` | 激励阶次覆盖 | 已含 {detail['provided_orders']} | "
                        f"需含 {detail['required_orders']} | 结构性 | {answer} |")
        if "record" in detail and isinstance(detail["record"], Mapping):
            record = detail["record"]
            rows.append(f"| `{check['id']}` | 上游自评判据 | pass={record.get('pass')} | "
                        f"cases_run={record.get('cases_run')} | 未交付 | 无基准可判（上游未交付） |")
    rows.append("")
    rows.append("> 判定口径：`是否 <3%` 列对数值型互裁量按 3% 硬线回答；结构性断裂（契约违约、激励阶次缺失、"
                "上游未交付）如实标注“无基准可判”或“非数值误差”，不折算成百分比来凑线。")
    return rows


def _figure_section(figures_dir: Path, figures: List[Mapping[str, str]]) -> List[str]:
    out: List[str] = []
    for figure in figures:
        out += [f"![{figure['name']}]({figures_dir.as_posix()}/{figure['name']})", "",
                f"*{figure['caption']}*", ""]
    return out


def compose(verdict: Mapping[str, Any], nodes: Mapping[str, Any], provenance: Mapping[str, Any],
            prose: Mapping[str, Any], figures: List[Mapping[str, str]], *,
            figures_dir: Path, pytest_summary: Optional[str] = None) -> str:
    doc: List[str] = [f"# {prose['title']}", ""]
    doc += [
        f"- 战役 `{verdict['campaign']}` · 节点 `{prose.get('node')}` · 仓 `TensorPlatform`",
        f"- schema `{verdict['schema']}` · 生成时间 {verdict['generated_at']}",
        f"- **门总体判定 `{verdict['gate_status']}`**（honest_fail={verdict['honest_fail']}，"
        f"upgrade_forbidden={verdict['upgrade_forbidden']}）— 本门为 honest FAIL，不粉饰",
        f"- manifest sha256 `{verdict['manifest_digest']}` · verdict 内容摘要 `{verdict['verdict_digest']}`"
        "（跨运行稳定：同一 manifest+工件复跑同摘要，见测试 `test_verdict_is_reproducible`）",
        f"- 断裂点 {len(verdict['fractures'])} 条，按类型：`{json.dumps(verdict['fracture_summary']['by_type'], ensure_ascii=False)}`",
        "",
    ]

    doc += ["## 1 案例介绍", ""]
    doc += [f"{paragraph}\n" for paragraph in prose["intro"]]
    doc += ["### 1.1 战役节点（平台 plan 499 机器抽取）", ""]
    doc += _node_table(nodes) + [""]
    doc += ["### 1.2 被互裁工件的出处（来源 worktree + commit + sha256）", ""]
    doc += _artifact_table(verdict, provenance) + [""]

    doc += ["## 2 怎么做的（方法 / 流程 / 工具链）", ""]
    doc += [f"{paragraph}\n" for paragraph in prose["method"]]
    doc += ["### 2.1 检查类型使用统计（机器统计）", ""]
    kinds: Dict[str, Dict[str, int]] = {}
    for item in verdict["items"]:
        for check in item["checks"]:
            bucket = kinds.setdefault(check["kind"], {PASS: 0, FAIL: 0, WITHHELD: 0})
            bucket[check["status"]] += 1
    doc += ["| 检查类型 | 使用次数 | PASS | FAIL | WITHHELD |", "|---|---|---|---|---|"]
    for kind, bucket in sorted(kinds.items()):
        doc.append(f"| `{kind}` | {sum(bucket.values())} | {bucket[PASS]} | {bucket[FAIL]} | {bucket[WITHHELD]} |")
    doc += [""]
    doc += prose["toolchain"] + [""]

    doc += ["## 3 计算结果（全部由 verdict.json 程序化注入）", ""]
    doc += ["### 3.1 四项验收总体判定", ""]
    doc += _item_table(verdict) + [""]
    doc += ["### 3.2 逐检查数字", ""]
    doc += _check_tables(verdict)
    doc += ["### 3.3 断裂点清单（门输出的具体断裂点）", ""]
    doc += _fracture_table(verdict) + [""]

    doc += ["## 4 结果图", ""]
    if figures:
        doc += ["图由数据包内的 `runners/figures.py` 从 verdict.json 与快照工件生成，随报告入库。", ""]
        doc += _figure_section(figures_dir, figures)
    else:
        doc += ["（本次运行未生成图；纯文档/后端任务的图示理由：门输出为结构化判定表，图只在有可画量的验收项上有意义。）", ""]

    doc += ["## 5 注意点（踩坑 / 局限性 / 适用范围）", ""]
    doc += [f"{paragraph}\n" for paragraph in prose["notes"]]
    doc += ["### 5.1 解锁条件（下一波由谁补什么）", ""]
    doc += [f"- {paragraph}" for paragraph in prose["unblock"]] + [""]

    doc += ["## 6 误差判定：关键量 vs 基准，显式回答“是否 <3%”", ""]
    doc += _error_verdict_table(verdict)
    doc += [""]
    passed = [c["id"] for item in verdict["items"] for c in item["checks"] if c["status"] == PASS]
    failed_items = [item["id"] for item in verdict["items"] if item["status"] == FAIL]
    doc += ["### 6.1 结论", "",
            f"- 本门总体判定为 **{verdict['gate_status']}**：{len(failed_items)} 项验收项判 FAIL"
            f"（{', '.join(failed_items)}），断裂点 {len(verdict['fractures'])} 条，"
            "均有机器证据（工件 sha256 + 上游判据记录 + 数值偏差）。",
            f"- 达到 PASS 的检查共 {len(passed)} 条：{', '.join(f'`{cid}`' for cid in passed) or '无'}。"
            "它们只证成各自那条关系（趋势单调 / 量纲自洽 / 桨叶数一致 / 工作带确实穿越禁带），"
            "**不构成任何验收项的放行**——单项判定取最差检查。",
            "- 因此**不存在**“关键量误差 <3%”的合格声明可用于整体放行。",
            "- honest FAIL 不可覆盖：即使后续把某一条断裂点补齐，本报告的判定也不会被回写为 PASS；"
            "新的裁决必须由新一次运行产生新的 verdict.json 与新的摘要。",
            ""]
    if pytest_summary:
        doc += ["## 7 测试证据", "", "```", pytest_summary.strip(), "```", ""]
    return "\n".join(doc) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="compose the interoperation gate report")
    parser.add_argument("--verdict", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--prose", required=True)
    parser.add_argument("--figures", default=None)
    parser.add_argument("--figures-rel", default=None,
                        help="display path of the figure directory inside the document")
    parser.add_argument("--pytest-summary", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    verdict = _load(Path(args.verdict))
    data_dir = Path(args.data)
    nodes = _load(data_dir / "nodes.json", {})
    provenance = _load(data_dir / "provenance.json", {})
    prose = _load(Path(args.prose))
    figures: List[Mapping[str, str]] = []
    figures_dir = Path(args.figures_rel or (args.figures or "figures"))
    if args.figures and (Path(args.figures) / "figures_metadata.json").exists():
        figures = _load(Path(args.figures) / "figures_metadata.json", [])
    pytest_summary = Path(args.pytest_summary).read_text(encoding="utf-8") if args.pytest_summary else None

    markdown = compose(verdict, nodes, provenance, prose, figures,
                       figures_dir=figures_dir, pytest_summary=pytest_summary)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    print(f"wrote {out} ({len(markdown)} bytes, {len(figures)} figure(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())