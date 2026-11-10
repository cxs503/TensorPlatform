"""报告渲染：JSON（机器消费）+ Markdown（人读/论文附录），含逐能力证据链。"""
from __future__ import annotations

import json
from pathlib import Path

LEVEL_ORDER = ("REAL", "WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED")


def render_markdown(report: dict, gate: dict | None = None, checks: dict | None = None) -> str:
    s = report["summary"]
    v = report["validation"]
    lines = [
        "# 能力台账自动审计报告（capability_audit_gate）",
        "",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 规则集：`{report['ruleset_version']}`  spec sha256：`{report['inputs']['spec_sha256'][:16]}…`",
        f"- 能力数：**{s['capabilities']}**，可审计：**{s['audited']}**（覆盖 {s['coverage']:.1%}）",
        f"- 运行耗时：**{s['runtime_sec']}s**（缓存命中率 {s['cache']['hit_rate']:.1%}）",
        "",
        "## 1. 五级分布",
        "",
        "| 等级 | 数量 |",
        "|---|---|",
    ]
    for lv in LEVEL_ORDER:
        lines.append(f"| {lv} | {s['level_tally'].get(lv, 0)} |")
    lines += ["", "## 2. 自动判定 vs 标注真值", ""]
    if v.get("agreement") is None:
        lines.append("未提供标注语料：真实台账无机器真值，一致率不计算（不以「都判 WITHHELD」充数）。")
    else:
        lines += [
            f"- 标注能力数：**{v['labeled']}**",
            f"- 一致率：**{v['agreement']:.1%}**（阈值 90%）",
            f"- 多数类基线：**{v['majority_baseline']:.1%}**（全判 `{v['majority_level']}`）",
            f"- 相对基线增益：**{v['agreement_gain']:+.1%}**",
        ]
        if v["disagreements"]:
            lines += ["", "| 能力 | 真值 | 判定 | 理由码 |", "|---|---|---|---|"]
            for d in v["disagreements"]:
                lines.append(f"| `{d['id']}` | {d['expected']} | {d['detected']} | {', '.join(d['reasons'])} |")
    contrast = report.get("contrast")
    if contrast and contrast.get("agreement") is not None:
        lines += ["", "### 2b. 外部真值台账对照（contrast）", "",
                  f"- 可比能力数：**{contrast['compared']}**，一致率 **{contrast['agreement']:.1%}**"
                  f"（多数类基线 {contrast['majority_baseline']:.1%}，增益 {contrast['agreement_gain']:+.1%}）"]
        if contrast.get("disagreements"):
            lines += ["", "| 能力 | 外部真值 | 本管线判定 | 理由码 |", "|---|---|---|---|"]
            for d in contrast["disagreements"]:
                lines.append(f"| `{d['id']}` | {d['truth']} | {d['detected']} | {', '.join(d['reasons'])} |")
    lines += ["", "## 3. 逐能力判定", "",
              "| 能力 | 等级 | 置信 | 理由码 | 实现文件 | 测试名 | 基准误差% | 链完整 |",
              "|---|---|---|---|---|---|---|---|"]
    for cap in report["capabilities"]:
        ev = cap["evidence"]
        bench = ev.get("benchmark") or {}
        tests = ", ".join(ev["test_names"][:2]) or "—"
        impl_n = len(ev["impl_files"])
        if impl_n > 1:
            impl_cell = f"{ev['impl_files'][0]['path']} +{impl_n - 1}"
        else:
            impl_cell = ev["impl_files"][0]["path"] if impl_n else "—"
        worst = bench.get("worst_error_pct")
        lines.append(
            f"| `{cap['capability']}` | **{cap['level']}** | {cap['confidence']:.2f} | "
            f"{', '.join(cap['reasons'])} | {impl_cell} | {tests} | "
            f"{'—' if worst is None else worst} | {'✓' if cap['chain_sufficient'] else '✗'} |")
    lines += ["", "## 4. 回归防护（基线对比）", ""]
    if gate is None:
        lines.append("未提供基线：本次仅审计，不做回归判定。")
    else:
        c = gate["counts"]
        lines += [
            f"- 基线规则集：`{gate['baseline_ruleset']}` → 当前 `{gate['current_ruleset']}`"
            f"（变更={'是' if gate['ruleset_changed'] else '否'}，ack={'是' if gate['ruleset_acked'] else '否'}）",
            f"- 稳定 {c['stable']} / 升级 {c['upgrades']} / 降级违规 {c['violations']}（其中反证 {c['refutations']}）"
            f" / 已 ack {c['acked']} / 消失 {c['dropped']}",
            f"- **判定：{'FAIL（CI 红）' if gate['fail'] else 'PASS'}**",
        ]
        if gate["violations"]:
            lines += ["", "| 能力 | 基线 | 当前 | 变化 |", "|---|---|---|---|"]
            for row in gate["violations"]:
                lines.append(f"| `{row['id']}` | {row['baseline']} | {row['current']} | {row['change']} |")
    if checks:
        lines += ["", "## 5. 验收判据", "", "| 判据 | 值 | 阈值 | 通过 |", "|---|---|---|---|"]
        for k, c in checks.items():
            if k == "all_pass":
                continue
            lines.append(f"| {k} | {c['value']} | {c['threshold']} | {'✓' if c['pass'] else '✗'} |")
        lines.append(f"| **全部** | — | — | **{'✓' if checks['all_pass'] else '✗'}** |")
    lines += ["", "## 6. 局限与适用范围", "",
              "- 判定基于**静态结构信号 + 落盘基准工件**，不执行代码；运行期才暴露的缺陷（数值发散、"
              "随机种子未固定、并发竞态）不在覆盖范围，需配合在线探针（tools/capability_audit）。",
              "- 基准工件是历史快照（含 sha256 冻结）；代码改动而工件未重跑时，REAL 结论只代表"
              "「该工件所测版本」。工件与实现 sha256 的对应由 CI 增量流水重跑保证。",
              "- 未绑定证据的能力一律 WITHHELD（拒绝下判决），不据此推断实现不存在。",
              ""]
    return "\n".join(lines)


def write_reports(report: dict, out_dir: str | Path, gate: dict | None = None,
                  checks: dict | None = None) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = {"report": report, "gate": gate, "checks": checks}
    (out / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                                sort_keys=True) + "\n", encoding="utf-8")
    md = render_markdown(report, gate, checks)
    (out / "report.md").write_text(md + "\n", encoding="utf-8")
    (out / "gate.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2,
                                              sort_keys=True) + "\n", encoding="utf-8") if gate else None
    (out / "criteria.json").write_text(
        json.dumps({"checks": checks, "summary": report["summary"],
                    "validation": report["validation"]}, ensure_ascii=False, indent=2,
                   sort_keys=True) + "\n", encoding="utf-8") if checks else None
    return {"report_json": str(out / "report.json"), "report_md": str(out / "report.md")}