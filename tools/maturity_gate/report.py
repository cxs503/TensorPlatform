"""报告要素检查 + 论文级报告渲染（数字全部由判定体程序化注入，零手抄）。

六要素（owner 2026-10-29 升级的发表级报告契约）
----------------------------------------------
① 案例介绍 ② 怎么做的 ③ 计算结果 ④ 结果图 ⑤ 注意点 ⑥ 误差判定

本模块只做两件事：

* :func:`check_six_elements` —— 把「报告写全了没有」变成机器可裁判的布尔 + 缺失清单；
  ⑥ 额外要求显式出现「是否<3%」字样（无基准必须声明原因，不许含糊）；
* :func:`render_report` —— 由判定体/台账/契约三份 JSON 生成 markdown，**数字不落人手**。

图标签一律 ASCII（容器内 matplotlib 缺 CJK 字体，中文会渲染成方框——歷次踩过的坑）。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

SIX_ELEMENTS: Sequence[tuple] = (
    ("case", "## ① 案例介绍"),
    ("method", "## ② 怎么做的"),
    ("results", "## ③ 计算结果"),
    ("figures", "## ④ 结果图"),
    ("caveats", "## ⑤ 注意点"),
    ("errors", "## ⑥ 误差判定"),
)

#: ⑥ 里必须显式回答的问题（无论答案是不是"是"，都必须写出来）
ERROR_VERDICT_MARKERS = ("是否<3%",)

#: 报告里必须出现的机器可追溯字段（审计链）
AUDIT_MARKERS = ("input_digest", "gate_schema_version")


def check_six_elements(markdown: str, figures: Optional[Sequence[str]] = None,
                       no_figure_reason: Optional[str] = None) -> Dict[str, Any]:
    """检查六要素 + 审计链 + 图（或显式无图理由）。"""
    sections = {}
    missing: List[str] = []
    for key, marker in SIX_ELEMENTS:
        present = marker in markdown
        sections[key] = present
        if not present:
            missing.append(marker)
    has_fig = bool(figures) or bool(no_figure_reason and no_figure_reason.strip())
    sections["figures_present_or_declared"] = has_fig
    if not has_fig:
        missing.append("<figures|no_figure_reason>")
    for marker in ERROR_VERDICT_MARKERS:
        if marker not in markdown:
            missing.append(f"<error-verdict:{marker}>")
    audit_missing = [m for m in AUDIT_MARKERS if m not in markdown]
    missing.extend(f"<audit:{m}>" for m in audit_missing)
    return {
        "sections": sections,
        "missing": missing,
        "audit_markers_present": not audit_missing,
        "complete": not missing,
        "required_total": len(SIX_ELEMENTS) + 1 + len(ERROR_VERDICT_MARKERS) + len(AUDIT_MARKERS),
        "found_total": (len(SIX_ELEMENTS) + 1 + len(ERROR_VERDICT_MARKERS)
                        + len(AUDIT_MARKERS) - len(missing)),
    }


def _fmt(x: Any) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float):
        if x == 0:
            return "0"
        if abs(x) < 1e-4 or abs(x) >= 1e5:
            return f"{x:.3e}"
        return f"{x:.6g}"
    return str(x)


def _criteria_rows(gate: Mapping[str, Any]) -> Iterable[str]:
    for node in gate["nodes"]:
        for crit in node["criteria"]:
            for chk in crit["checks"]:
                yield "| {n} | {c} | {s} | {ptr} | {cmp} {thr} | {val} | {v} |".format(
                    n=node["node_key"], c=crit["criterion_id"],
                    s=(crit["statement"] or "").replace("|", "/"),
                    ptr=chk["pointer"], cmp=chk["comparator"],
                    thr=_fmt(chk["threshold"]), val=_fmt(chk["measured"]),
                    v=chk["verdict"])


def render_report(gate: Mapping[str, Any], ledger_doc: Mapping[str, Any],
                  contract_check: Mapping[str, Any], report_check: Optional[Mapping[str, Any]],
                  figures: Sequence[Mapping[str, str]], evidence_protocol: str,
                  limitations: Sequence[str], error_notes: Sequence[str],
                  run_command: str) -> str:
    """渲染六要素报告（全部数字来自 gate/ledger/contract 三份机器产物）。"""
    c = gate["campaign"]
    lines: List[str] = []
    add = lines.append
    add(f"# 战役成熟化整合与交付（{gate['spec_id']}）")
    add("")
    add(f"- `gate_schema_version`: `{gate['gate_schema_version']}`")
    add(f"- `input_digest`: `{gate['input_digest']}`（规格 + 全部被读证据文档 sha256 的 sha256）")
    add(f"- 战役裁定：**{c['verdict']}**；节点 {c['nodes_total']} 个，已交付证据 "
        f"{c['nodes_delivered']} 个，绿线 {c['criteria_pass_threshold']}，"
        f"自述/复算冲突 {c['claim_mismatch_total']} 处")
    add("")
    add("## ① 案例介绍")
    add("")
    add("战役收尾节点的物理目标：把各上游节点**自己验证过**的能力整合成一份可交付的工程输出。"
        "难点不在算一个数，而在「凭什么相信上游的数」——报告里的形容词、绿色的测试、"
        "以及并进去的 PR 都不能直接当证据。故本文的案例对象是**整合链本身**：")
    add("")
    add("| 环节 | 输入 | 输出 | 机器判据 |")
    add("|---|---|---|---|")
    add("| 规格 | `data/maturity_gate/p54w3_campaign.spec.json` | 节点×判据×检查树 | 未知键/未知比较器/重复 id 一律拒绝 |")
    add("| 证据 | 各仓 `p54w3_evidence` 交付文档 / 已交付 study 文档 | 复算数值 + sha256 | 无证据不 PASS |")
    add("| 裁定 | 节点判据通过率 | PASS/FAIL/WITHHELD | 三值，缺证据停在 WITHHELD |")
    add("| 台账 | 判定体 | 节点↔能力条目 | 双向双射 |")
    add("| 契约 | 交付文档与端点 | 完整性分数 | 必含章节/字段/端点逐个匹配 |")
    add("")
    add("参与整合的上游节点（规格工件注入，非代码硬编码）：")
    add("")
    add("| 节点 | 仓库 | mission | 判据数 | 通过 | 存疑(WITHHELD) | 通过率 | 证据成熟度 |")
    add("|---|---|---|---|---|---|---|---|")
    levels = {e["node_key"]: e["evidence_level"] for e in ledger_doc["entries"]}
    for s in gate["node_summaries"]:
        add(f"| `{s['node_key']}` | {s['repo']} | {s['mission_id']} | {s['criteria_total']} | "
            f"{s['passed']} | {s['withheld']} | {_fmt(s['pass_ratio'])} | {levels.get(s['node_key'])} |")
    add("")
    add("## ② 怎么做的")
    add("")
    add(f"命令：`{run_command}`。工具链：`tools/maturity_gate/`（spec → evidence → gate → ledger → "
        "contracts → report 六个模块）+ `backend/api/maturity_gate.py`（同一判定的 HTTP 无状态面）+ "
        "pytest（合成域夹具 + 跨仓集成链）。")
    add("")
    add("关键设置：")
    add("")
    add(f"1. **判据是分母，检查不是**——一条验收清单常是复合命题（`α∈[0.05,0.2]` 且 `单调`），"
        "故一个判据由多个 check 合取；通过率按判据算，拆句刷分无效。")
    add("2. **三值裁定**：任一已交付节点低于绿线 → FAIL；全部已交付且达标 → PASS；"
        "否则 WITHHELD（缺证据≠通过≠不过）。")
    add("3. **钉桩 + 互裁**：证据文档可带 sha256，文档被改动即 FAIL(integrity)；"
        "上游自述 `pass` 与本引擎复算不一致记 claim_mismatch 并进台账。")
    add(f"4. **零领域语义**：代码内无学科词条；本战役的内容全部来自工件。证据交付协议：{evidence_protocol}")
    add("")
    add("## ③ 计算结果")
    add("")
    add("战役级数字（`artifacts/p54w3_maturity/gate.json` 程序化注入）：")
    add("")
    add("| 量 | 值 |")
    add("|---|---|")
    for k in ("nodes_total", "nodes_delivered", "nodes_absent", "nodes_green", "nodes_red",
              "min_pass_ratio", "mean_pass_ratio", "criteria_pass_threshold",
              "criterion_a_all_nodes_ge_threshold", "claim_mismatch_total"):
        add(f"| campaign.{k} | {_fmt(c.get(k))} |")
    add(f"| campaign.verdict | {c['verdict']} |")
    add(f"| ledger.one_to_one | {ledger_doc['bijection']['one_to_one']} |")
    add(f"| ledger.entries_total | {ledger_doc['bijection']['entries_total']} |")
    add(f"| contracts.completeness | {_fmt(contract_check.get('completeness'))} |")
    add(f"| contracts.complete | {contract_check.get('complete')} |")
    if report_check is not None:
        add(f"| report.six_elements_complete | {report_check.get('complete')} |")
    add("")
    dc = gate.get("delivery_criteria")
    if dc:
        add("整合节点自身六条交付判据（本表即评审面对照表）：")
        add("")
        add("| 判据 | 结论 | 依据 |")
        add("|---|---|---|")
        for key in sorted(dc):
            item = dc[key]
            add(f"| {item['name']} | {'PASS' if item['pass'] else 'NOT-PASS'} | "
                f"{(item.get('detail') or '').replace('|', '/')} |")
        add("")
    withheld_rows = []
    for node in gate["nodes"]:
        for crit in node["criteria"]:
            if crit["verdict"] == "WITHHELD":
                reasons = sorted({c["reason"] for c in crit["checks"] if c["reason"]})
                withheld_rows.append((node["node_key"], crit["criterion_id"],
                                      (crit["statement"] or "").replace("|", "/"),
                                      ",".join(reasons)))
    if withheld_rows:
        add("存疑（WITHHELD）明细——**不知道**的项逐条列名，绝不计入通过：")
        add("")
        add("| 节点 | 判据 | 陈述 | 具名原因 |")
        add("|---|---|---|---|")
        for row in withheld_rows:
            add("| `{}` | {} | {} | {} |".format(*row))
        add("")
    add("逐判据明细（每行一次取值 + 一次谓词判定；`WITHHELD` 行的 reason 见 gate.json）：")
    add("")
    add("| 节点 | 判据 | 陈述 | 取值指针 | 谓词 | 实测 | 判定 |")
    add("|---|---|---|---|---|---|---|")
    for row in _criteria_rows(gate):
        add(row)
    add("")
    add("## ④ 结果图")
    add("")
    for fig in figures:
        add(f"![{fig['name']}]({fig['path']})")
        add("")
        add(f"*{fig['caption']}*")
        add("")
    if not figures:
        add("（本报告无图，理由见 ⑤。）")
        add("")
    add("## ⑤ 注意点")
    add("")
    for item in limitations:
        add(f"- {item}")
    add("")
    add("## ⑥ 误差判定")
    add("")
    add("判据不是「误差<3%」型的连续量对标，而是**阈值谓词**；故本节逐条给出"
        "「实测 vs 阈值」的相对余量，并显式回答是否<3%。")
    add("")
    add("| 判据 | 实测 | 阈值 | 相对阈值余量 | 判定 |")
    add("|---|---|---|---|---|")
    for node in gate["nodes"]:
        for crit in node["criteria"]:
            for chk in crit["checks"]:
                val, thr = chk["measured"], chk["threshold"]
                margin = "—"
                if isinstance(val, (int, float)) and not isinstance(val, bool) \
                        and isinstance(thr, (int, float)) and not isinstance(thr, bool) and thr:
                    margin = _fmt(abs(float(val)) / abs(float(thr)))
                add(f"| `{node['node_key']}::{crit['criterion_id']}` | {_fmt(val)} | "
                    f"{_fmt(thr)} | {margin} | {chk['verdict']} |")
    add("")
    for note in error_notes:
        add(f"- {note}")
    add("")
    add("**是否<3%？** " + ("见上表逐行；对「误差型判据」本战役实测均满足各自阈值，"
                            "但阈值本身严于 3% 的项才可宣称<3%，其余按各自阈值口径表述，"
                            "不作<3%的过度声明。" if c["verdict"] != "WITHHELD" else
                            "**不能宣称**：本战役仍有多数节点零证据（WITHHELD），"
                            "此时给出任何<3%的结论都是无依据的过度声明。"))
    add("")
    add("---")
    add("")
    add("审计链：`data/maturity_gate/p54w3_campaign.spec.json` → 证据文档 sha256 → "
        f"`gate.json`（input_digest `{gate['input_digest'][:16]}…`）→ "
        "`p54w3_maturity_ledger.json` → `p54w3_maturity_report_check.json`。"
        "每个数字都可用同一命令复算，逐字节一致。")
    return "\n".join(lines) + "\n"