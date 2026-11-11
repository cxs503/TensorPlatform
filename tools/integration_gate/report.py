"""战役播种报告生成 (结构化 JSON + markdown 渲染，零领域语义)。

报告契约（供下一波仲裁者机器消费）::

    schema            tp.integration.seed_report/v1
    campaign/base_commit/generated_at
    nodes[]           每节点: 自证有无、判据统计、等级、合并态、缺口
    pairs[]           互裁对: 结论 + 每检查明细 + 允许的等级上限
    ledger            台账摘要 (条目数、等级分布、内容 sha256)
    gaps[]            遗留缺口 (结构化，含 node/pair 与建议动作)
    next_wave_seeds[] 下一波可播种节点 (id/理由/验收提示/来源缺口)
    integration_criteria  I1..I5 机器自检 (数字 + 是否满足)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .model import Attestation, PairResult, rank
from .provenance import sha256_obj, now_utc

REPORT_SCHEMA = "tp.integration.seed_report/v1"


def _node_block(node: dict[str, Any], att: Attestation | None,
                alignment: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    node_entries = [e for e in entries if e["node_ext_id"] == node["ext_id"]]
    levels = sorted({e["level"] for e in node_entries}, key=rank)
    if att is not None:
        counts = att.counts()
        verdict = att.verdict()
        attestation_state = "present"
    else:
        counts = {"PASS": 0, "FAIL": 0, "WITHHELD": 0, "SKIP": 0, "total": 0}
        verdict = "WITHHELD"
        attestation_state = "absent"
    return {
        "node_ext_id": node["ext_id"],
        "label": node.get("label", ""),
        "repo": node.get("repo", ""),
        "mission_id": node.get("mission_id"),
        "attestation": attestation_state,
        "attestation_source": (att.source_ref if att else None),
        "attestation_sha256": (att.source_sha256 if att else None),
        "verdict": verdict,
        "criteria_counts": counts,
        "pass_rate": (att.pass_rate() if att else None),
        "honest_fail": bool(att.honest_fail()) if att else False,
        "case_verdict_fails": (att.case_verdict_fails() if att else []),
        "capabilities": [{"id": e["capability_id"], "level": e["level"],
                          "status": e["status"], "merge_state": e["merge_state"]}
                         for e in node_entries],
        "levels": levels,
        "baseline": {k: alignment.get(k) for k in
                     ("state", "branch", "base_commit", "aligned",
                      "contract_required", "contract_present", "note")},
        "gaps": node.get("gaps") or [],
    }


def build_gaps(nodes_block: list[dict[str, Any]], pairs: list[PairResult],
               alignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for nb in nodes_block:
        if nb["attestation"] == "absent":
            gaps.append({
                "id": f"gap:{nb['node_ext_id']}:no_attestation",
                "kind": "missing_evidence",
                "node_ext_id": nb["node_ext_id"],
                "detail": "节点在本门运行时没有可机器复算的验收自证工件",
                "action": "节点产出 acceptance.json/self-attestation 后重跑本门",
            })
        elif nb["verdict"] == "FAIL":
            gates_fail = nb["honest_fail"]
            gaps.append({
                "id": f"gap:{nb['node_ext_id']}:honest_fail",
                "kind": "honest_fail" if gates_fail else "case_verdict_fail",
                "node_ext_id": nb["node_ext_id"],
                "detail": ("验证实现本身的门判据 FAIL（honest FAIL 不可覆盖，等级 ≤ MOCK）"
                           if gates_fail else
                           f"工程判决 FAIL（算例结论，非实现缺陷）: "
                           f"{', '.join(nb.get('case_verdict_fails') or [])}"),
                "action": "按失败判据修复并附新证据 sha256，再回填等级"
                          if gates_fail else "在报告中如实呈报，不粉饰；不需要回填等级",
            })
        for g in nb.get("gaps") or []:
            gaps.append({"id": f"gap:{nb['node_ext_id']}:{g.get('id', 'note')}",
                         "kind": g.get("kind", "node_note"),
                         "node_ext_id": nb["node_ext_id"],
                         "detail": g.get("detail", ""),
                         "action": g.get("action", "")})
    for a in alignments:
        if a.get("contract_required") and not a.get("contract_present"):
            gaps.append({
                "id": f"gap:{a['node_ext_id']}:contract_missing",
                "kind": "interface_contract_missing",
                "node_ext_id": a["node_ext_id"],
                "detail": f"{a['state']} 节点未声明接口契约",
                "action": "在自证中补 interface_contract（导出符号/端口单位/稳定性承诺）",
            })
    for p in pairs:
        for c in p.checks:
            if c.verdict in ("DISAGREE", "DEGENERATE"):
                gaps.append({
                    "id": f"gap:{p.id}:{c.check}",
                    "kind": "inter_adjudication_conflict",
                    "pair_id": p.id,
                    "detail": f"{c.check} 结论 {c.verdict}: {c.note or ''}".strip(),
                    "action": "仲裁者复核两侧口径；冲突未消解前相关能力不得高于 MOCK",
                })
            elif c.verdict == "WITHHELD":
                gaps.append({
                    "id": f"gap:{p.id}:{c.check}:withheld",
                    "kind": "counterpart_unavailable",
                    "pair_id": p.id,
                    "detail": f"{c.check} 无可用对照侧: {c.detail}",
                    "action": "补齐对照节点/工件后重跑互裁",
                })
    return gaps


def next_wave_seeds(gaps: list[dict[str, Any]], pairs: list[PairResult]) -> list[dict[str, Any]]:
    """把缺口转成下一波可播种节点提案 (机器可读，仲裁者据此建节点)。"""
    seeds: list[dict[str, Any]] = []
    seen = set()
    for g in gaps:
        kind = g["kind"]
        key = (kind, g.get("node_ext_id") or g.get("pair_id"))
        if key in seen:
            continue
        seen.add(key)
        if kind == "missing_evidence":
            seeds.append({
                "seed_id": f"attest:{g['node_ext_id']}",
                "kind": "attestation_backfill",
                "rationale": "无自证 → 本门无法定级（WITHHELD）",
                "acceptance_hint": "acceptance.json 含逐条判据 measured/threshold/op + 输入 sha256",
                "source_gap": g["id"],
            })
        elif kind == "inter_adjudication_conflict":
            seeds.append({
                "seed_id": f"adjudicate:{g['pair_id']}",
                "kind": "cross_domain_reconcile",
                "rationale": g["detail"],
                "acceptance_hint": "冲突两侧给出统一口径推导 + 数值复算 ≤1e-9",
                "source_gap": g["id"],
            })
        elif kind == "counterpart_unavailable":
            seeds.append({
                "seed_id": f"counterpart:{g['pair_id']}",
                "kind": "reference_node_build",
                "rationale": "互裁缺对照侧，单侧证据无法定性",
                "acceptance_hint": "对照节点给出同量纲同口径数值轨迹 + 出处 sha256",
                "source_gap": g["id"],
            })
        elif kind == "interface_contract_missing":
            seeds.append({
                "seed_id": f"contract:{g['node_ext_id']}",
                "kind": "interface_contract",
                "rationale": g["detail"],
                "acceptance_hint": "声明导出符号/端口单位/稳定性承诺 + 兼容性测试",
                "source_gap": g["id"],
            })
    return seeds


def evaluate_integration_criteria(nodes_block: list[dict[str, Any]],
                                  pairs: list[PairResult],
                                  ledger: dict[str, Any],
                                  alignments: list[dict[str, Any]],
                                  pytest_summary: dict[str, Any] | None,
                                  coupling: dict[str, Any] | None = None,
                                  archive: dict[str, Any] | None = None,
                                  ) -> dict[str, Any]:
    """I1..I5 机器自检：每项都给数字，不满足即 False（honest FAIL 不粉饰）。"""
    n_nodes = len(nodes_block)
    registered = sum(1 for nb in nodes_block if nb["capabilities"])
    levels_declared = sum(1 for nb in nodes_block
                          if nb["capabilities"] and all(c["level"] for c in nb["capabilities"]))
    pair_ok = sum(1 for p in pairs if p.verdict() in ("AGREE", "DISAGREE", "DEGENERATE"))
    contracts = [a for a in alignments if a.get("contract_required")]
    contracts_ok = [a for a in contracts if a.get("contract_present")]
    pytest_ok = bool(pytest_summary and pytest_summary.get("failed", 1) == 0
                     and pytest_summary.get("passed", 0) >= 5)
    crit: dict[str, Any] = {
        "I1_capability_registration": {
            "value": {"nodes": n_nodes, "registered": registered,
                      "levels_complete": levels_declared},
            "passed": registered == n_nodes and levels_declared == n_nodes,
            "criterion": "7 节点能力条目全部登记，证据等级标注完整",
        },
        "I2_inter_adjudication_pairs": {
            "value": {"pairs": len(pairs), "with_verdict": pair_ok,
                      "verdicts": {p.id: p.verdict() for p in pairs}},
            "passed": len(pairs) >= 3 and pair_ok == len(pairs),
            "criterion": "3 对互裁面一致性报告生成",
        },
        "I3_seed_report": {
            "value": {"schema": REPORT_SCHEMA,
                      "gaps": None, "nodes": n_nodes},
            "passed": True,
            "criterion": "结构化 JSON 报告含节点验收状态/能力增量/遗留缺口",
        },
        "I4_baseline_alignment": {
            "value": {"nodes": n_nodes, "contracts_required": len(contracts),
                      "contracts_present": len(contracts_ok),
                      "states": {a["node_ext_id"]: a["state"] for a in alignments}},
            "passed": len(alignments) == n_nodes,
            "criterion": "所有节点基线/合并态判定完成，未合并分支接口契约状态已注明",
        },
        "I5_pytest": {
            "value": pytest_summary or {"passed": None},
            "passed": pytest_ok,
            "criterion": "pytest ≥5 全绿",
        },
    }
    # I6..I8 只在战役提供了耦合/归档声明时才评估（对不含这些声明的战役保持向后兼容）
    if coupling is not None:
        iface = (coupling.get("interfaces") or {}).get("summary") or {}
        cons = (coupling.get("assumptions") or {}).get("summary") or {}
        crit["I6_interface_contracts"] = {
            "value": {"interfaces": iface.get("total"),
                      "documented": iface.get("documented"),
                      "validated": iface.get("validated"),
                      "mismatch": iface.get("mismatch"),
                      "unattested": iface.get("unattested"),
                      "undocumented": iface.get("undocumented"),
                      "mismatch_ids": iface.get("mismatch_ids")},
            "passed": bool(iface.get("total")) and iface.get("documented") == iface.get("total")
            and iface.get("mismatch", 1) == 0,
            "criterion": "全部跨节点接口契约有文档且单位/区间/节点声明机械核验通过（无 CONTRACT_MISMATCH）",
        }
        crit["I7_assumption_consistency"] = {
            "value": {"keys": cons.get("keys"), "agreed": cons.get("agreed"),
                      "contradicted": cons.get("contradicted"),
                      "implied_mismatch": cons.get("implied_mismatch"),
                      "undetermined": cons.get("undetermined"),
                      "violations": cons.get("violations"),
                      "mitigated": cons.get("mitigated"),
                      "unmitigated": cons.get("unmitigated"),
                      "clean": cons.get("clean")},
            "passed": bool(cons.get("keys")) and cons.get("unmitigated", 1) == 0,
            "criterion": "跨节点共享假设全部机械检查：冲突必须显式降级/引用仲裁（零未处置冲突），"
                         "声明不全记 UNDETERMINED 不算一致",
        }
    if archive is not None:
        verified = archive.get("verification") or {}
        crit["I8_archive_snapshot"] = {
            "value": {"n_files": archive.get("n_files"), "n_bytes": archive.get("n_bytes"),
                      "content_sha256": archive.get("content_sha256"),
                      "verified": verified.get("ok"), "checked": verified.get("checked"),
                      "missing": verified.get("missing"), "changed": verified.get("changed")},
            "passed": bool(archive.get("n_files")) and bool(archive.get("content_sha256"))
            and verified.get("ok") is True,
            "criterion": "全部工件快照逐文件 sha256 + 清单内容 sha256，可独立复算通过",
        }
    return crit


def build_report(campaign: str,
                 base_commit: str | None,
                 nodes: list[dict[str, Any]],
                 attestations: list[Attestation],
                 pairs: list[PairResult],
                 ledger: dict[str, Any],
                 alignments: list[dict[str, Any]],
                 pytest_summary: dict[str, Any] | None = None,
                 generated_at: str | None = None,
                 coupling: dict[str, Any] | None = None,
                 archive: dict[str, Any] | None = None,
                 extra: dict[str, Any] | None = None) -> dict[str, Any]:
    by_ext = {a.node_ext_id: a for a in attestations}
    nodes_block = [_node_block(n, by_ext.get(n["ext_id"]),
                              next((a for a in alignments
                                    if a["node_ext_id"] == n["ext_id"]), {}),
                              ledger["entries"])
                   for n in nodes]
    gaps = build_gaps(nodes_block, pairs, alignments)
    crit = evaluate_integration_criteria(nodes_block, pairs, ledger, alignments, pytest_summary,
                                        coupling=coupling, archive=archive)
    crit["I3_seed_report"]["value"]["gaps"] = len(gaps)
    doc: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "campaign": campaign,
        "generated_at": generated_at or now_utc(),
        "base_commit": base_commit,
        "nodes": nodes_block,
        "pairs": [p.as_dict() for p in pairs],
        "ledger": {
            "schema": ledger.get("schema"),
            "content_sha256": ledger.get("content_sha256"),
            "counts": ledger.get("counts"),
            "entries": [{"capability_id": e["capability_id"], "node_ext_id": e["node_ext_id"],
                         "level": e["level"], "status": e["status"],
                         "merge_state": e["merge_state"], "honest_fail": e["honest_fail"]}
                        for e in ledger["entries"]],
        },
        "baseline": alignments,
        "gaps": gaps,
        "next_wave_seeds": next_wave_seeds(gaps, pairs),
        "integration_criteria": crit,
        "coupling": coupling or {},
        "archive": archive or {},
        "extra": extra or {},
    }
    doc["content_sha256"] = sha256_obj({k: v for k, v in doc.items() if k != "generated_at"})
    return doc


def render_coupling_sections(doc: dict[str, Any], labels: list[str] | None = None,
                            heading: str = "##") -> list[str]:
    """接口契约 / 假设一致性 / 工件快照三节（无数据则整节省略）。

    ``labels`` 控制小节编号（缺省 7/8/9），按实际存在的节顺序消费，因此上层标题
    编号不会因为缺节而错位。
    """
    labels = list(labels or ["7", "8", "9"])
    L: list[str] = []
    A = L.append
    coup = doc.get("coupling") or {}
    k = 0
    if coup.get("interfaces"):
        iface = coup["interfaces"]
        A(f"{heading} {labels[k]} 跨节点数据交换接口契约")
        k += 1
        A("")
        sm = iface["summary"]
        A(f"- 接口 {sm['total']} 条 · 已文档化 {sm['documented']} · 契约核验通过 "
          f"{sm['validated']} · 契约不符 {sm['mismatch']} · 证据不足 {sm['unattested']} · "
          f"无文档 {sm['undocumented']}")
        A("")
        A("| 接口 | 生产方 → 消费方 | 载荷(单位) | 生产方认证区间 | 消费方需求区间 | 状态 |")
        A("|---|---|---|---|---|---|")
        for r in iface["interfaces"]:
            pl, q = r["payload"], r["requirement"]
            A(f"| `{r['id']}` | `{r['producer']}` → `{r['consumer']}` | "
              f"{pl['id']} ({pl['unit']}) | [{pl['coverage']['min']:g}, "
              f"{pl['coverage']['max']:g}] | [{q['min']:g}, {q['max']:g}] ({q['unit']}) | "
              f"**{r['status']}** |")
        A("")
        for r in iface["interfaces"]:
            if r.get("note"):
                A(f"- `{r['id']}` 契约备注：{r['note']}")
        for r in iface["interfaces"]:
            bad = [c for c in r["checks"] if c["verdict"] != "PASS"]
            if bad:
                A(f"- `{r['id']}` 未通过：" + "; ".join(
                    f"{c['check']}={c['verdict']}" for c in bad))
        for r in iface["interfaces"]:
            cov = next((c for c in r["checks"] if c["check"] == "range_coverage"), None)
            if cov and cov["detail"].get("guarded"):
                A(f"- `{r['id']}` 区间缺口 "
                  f"{max(cov['detail']['shortfall_low'], cov['detail']['shortfall_high']):.6g} "
                  f"由 `domain_status` 护栏承接（clamp_with_status，禁止沉默外推）")
        A("")
    if coup.get("assumptions"):
        cons = coup["assumptions"]
        A(f"{heading} {labels[k]} 跨节点共享假设一致性")
        k += 1
        A("")
        sm = cons["summary"]
        A(f"- 假设键 {sm['keys']} · 一致 {sm['agreed']} · 跨节点冲突 {sm['contradicted']} · "
          f"律自洽冲突 {sm['implied_mismatch']} · 声明不全 {sm['undetermined']} · "
          f"未处置冲突 {sm['unmitigated']} · 口径洁净 `{sm['clean']}`")
        A("")
        A("| 假设键 | 类型 | 状态 | 关键数字 | 处置 |")
        A("|---|---|---|---|---|")
        for r in cons["keys"]:
            d = r["detail"]
            if r["kind"] == "law":
                bits = (f"implied_max_abs_dev={d.get('implied_max_abs_dev'):.6g}; "
                        f"shared_max_rel_dev={d.get('max_rel_dev_shared'):.6g}; tol={d.get('tol')}")
            elif r["kind"] == "value":
                bits = f"max_rel_dev={d.get('max_rel_dev'):.6g}; tol={d.get('rel_tol')}"
            elif r["kind"] == "edition":
                bits = f"editions={d.get('editions')}"
            else:
                bits = d.get("reason", "")
            mit = r.get("mitigation") or {}
            A(f"| `{r['key']}` | {r['kind']} | **{r['status']}** | {bits} | "
              f"{'—' if not mit else ('已处置: ' if mit.get('accepted') else '未处置: ') + str(mit.get('reason'))} |")
        A("")
        for v in cons["violations"]:
            A(f"- 冲突 `{v['key']}`（{v['status']}）：节点 {', '.join(v['nodes'])}；"
              f"处置 = {v['mitigation_reason']}")
        for r in cons["keys"]:
            for imp in (r["detail"].get("implied") or []):
                A(f"- 律自洽 `{r['key']}` / {imp['node']}：由 `{imp['expr']}` 推得 "
                  f"{imp['expected']:.6f}，声明 {imp['declared']:.6f}，"
                  f"差 {imp['abs_dev']:.6f}（容差 {imp['tol']}）→ {imp['verdict']}")
        A("")
    if doc.get("archive"):
        arc = doc["archive"]
        ver = arc.get("verification") or {}
        A(f"{heading} {labels[k]} 工件快照与可复现性")
        A("")
        A(f"- 快照 {arc.get('n_files')} 个文件 / {arc.get('n_bytes')} 字节；"
          f"清单内容 sha256 `{arc.get('content_sha256')}`；独立复算 "
          f"{'通过' if ver.get('ok') else '未通过'}（checked={ver.get('checked')}, "
          f"missing={ver.get('missing')}, changed={ver.get('changed')}）")
        A("")
    return L


def render_markdown(doc: dict[str, Any]) -> str:
    L: list[str] = []
    A = L.append
    crit = doc["integration_criteria"]
    npass = sum(1 for k, v in crit.items() if v["passed"])
    A(f"# 集成与互裁门 · 战役 {doc['campaign']} 播种报告")
    A("")
    A(f"- schema: `{doc['schema']}`")
    A(f"- 生成时间 (UTC): {doc['generated_at']}")
    A(f"- 基线 commit: `{doc['base_commit']}`")
    A(f"- 内容 sha256: `{doc['content_sha256']}`")
    A(f"- 机器自检 I1–I{len(crit)}: **{npass}/{len(crit)} 满足**")
    A("")
    A("## 1 节点验收状态")
    A("")
    A("| 节点 | 仓 | mission | 自证 | 判据统计 | 通过率 | 总体判定 | 能力等级 | 基线态 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for nb in doc["nodes"]:
        c = nb["criteria_counts"]
        pr = "—" if nb["pass_rate"] is None else f"{nb['pass_rate'] * 100:.1f}%"
        A(f"| `{nb['node_ext_id']}` | {nb['repo']} | {nb['mission_id']} | "
          f"{nb['attestation']} | P{c['PASS']}/F{c['FAIL']}/W{c['WITHHELD']} | {pr} | "
          f"{nb['verdict']} | {', '.join(nb['levels']) or '—'} | {nb['baseline'].get('state')} |")
    A("")
    A("## 2 互裁结论")
    A("")
    for p in doc["pairs"]:
        A(f"### {p['id']} · {p['title']}")
        A("")
        A(f"- 类型: `{p['kind']}` · 结论: **{p['verdict']}** · 对照侧状态: "
          f"`{p['counterpart_status']}` · 等级上限: `{p['cap_level']}`")
        if p.get("note"):
            A(f"- {p['note']}")
        A("")
        A("| 检查 | 结论 | 严重度 | 关键数字 |")
        A("|---|---|---|---|")
        for c in p["checks"]:
            d = c["detail"]
            keys = ["max_rel_dev", "rel_dev", "value", "threshold", "max_abs_dev",
                    "max_ratio", "driver", "response"]
            bits = [f"{k}={d[k]:.6g}" if isinstance(d.get(k), (int, float)) else f"{k}={d[k]}"
                    for k in keys if k in d]
            A(f"| {c['check']} | {c['verdict']} | {c['severity']} | {'; '.join(bits)} |")
        A("")
    A("## 3 能力台账增量")
    A("")
    counts = doc["ledger"]["counts"]
    A(f"- 条目数: **{len(doc['ledger']['entries'])}** · 等级分布: " +
      ", ".join(f"{k}={v}" for k, v in counts.items() if v))
    A(f"- 台账内容 sha256: `{doc['ledger']['content_sha256']}`")
    A("")
    A("| 能力 | 节点 | 等级 | 状态 | 合并态 | honest_FAIL |")
    A("|---|---|---|---|---|---|")
    for e in doc["ledger"]["entries"]:
        A(f"| `{e['capability_id']}` | `{e['node_ext_id']}` | {e['level']} | {e['status']} | "
          f"{e['merge_state']} | {'Y' if e['honest_fail'] else '—'} |")
    A("")
    A("## 4 基线对齐")
    A("")
    A("| 节点 | 分支 | base_commit | 状态 | 契约必需 | 契约已有 |")
    A("|---|---|---|---|---|---|")
    for a in doc["baseline"]:
        A(f"| `{a['node_ext_id']}` | {a['branch']} | `{(a['base_commit'] or '')[:12]}` | "
          f"{a['state']} | {'Y' if a['contract_required'] else '—'} | "
          f"{'Y' if a['contract_present'] else '—'} |")
    A("")
    A("## 5 遗留缺口")
    A("")
    A(f"共 **{len(doc['gaps'])}** 条。")
    A("")
    A("| 缺口 | 类型 | 说明 | 建议动作 |")
    A("|---|---|---|---|")
    for g in doc["gaps"]:
        A(f"| `{g['id']}` | {g['kind']} | {(g['detail'] or '').replace('|', '/')[:120]} | "
          f"{(g.get('action') or '').replace('|', '/')[:80]} |")
    A("")
    A("## 6 下一波播种提案")
    A("")
    A("| seed | 类型 | 理由 | 验收提示 |")
    A("|---|---|---|---|")
    for s in doc["next_wave_seeds"]:
        A(f"| `{s['seed_id']}` | {s['kind']} | {s['rationale'][:100]} | "
          f"{s['acceptance_hint'][:100]} |")
    A("")
    L.extend(render_coupling_sections(doc))
    nseq = 7 + sum(1 for k in ("interfaces", "assumptions")
                   if (doc.get("coupling") or {}).get(k)) + (1 if doc.get("archive") else 0)
    A(f"## {nseq} 机器自检 I1–I{len(crit)}")
    A("")
    A("| 判据 | 是否满足 | 数字 | 说明 |")
    A("|---|---|---|---|")
    for k, v in crit.items():
        A(f"| {k} | {'✅' if v['passed'] else '❌'} | "
          f"`{str(v['value'])[:160]}` | {v['criterion']} |")
    A("")
    return "\n".join(L)