"""战役集成与验收门运行器：`python3 -m tools.campaign_integration.cli --snapshot …`。

一次跑完、零手抄，产出（同一命令下可逐字节复算）

* ``<out>/presence/<node_key>.json`` —— 逐节点实测交付态（收集层产物）
* ``<out>/spec.json`` / ``data/campaign_integration/*.spec.json`` —— 证据规格（快照程序化生成）
* ``<out>/gate.json`` —— 判定体：证据层三值裁定 + 交付层 a–f + 契约摘要 + 台账摘要 + 审计链
* ``<out>/contracts.json`` —— 跨节点接口契约 + 注册表自洽逐条明细
* ``<out>/maturity_plan.json`` —— 成熟化单（REAL / deferred 及具名原因）
* ``<out>/feature_ledger.sql`` / ``<out>/feature_ledger_rollback.sql`` —— 台账单 + 回滚脚本
* ``<report>`` —— 论文级六要素报告（数字全部注入）
* ``<out>/figures/*.png`` —— 三张图（ASCII 标签）

设计要点

* 证据层裁定**复用** :mod:`tools.maturity_gate`（同一套三值语义），本模块只做编排；
* 交付层 a–f 是**战役级**机器判据，缺证据只能落 ``WITHHELD``，不会被其它绿项抵消；
* 硬失败（``FAIL``）**不可被覆盖**：哪怕其余五项全绿，交付层裁定仍是 ``FAIL``。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(REPO_ROOT))

from tools.campaign_integration import contracts as contracts_engine  # noqa: E402
from tools.campaign_integration import maturity as maturity_engine  # noqa: E402
from tools.campaign_integration import report as render_engine  # noqa: E402
from tools.campaign_integration import sql_smoke as sql_smoke_engine  # noqa: E402
from tools.campaign_integration import sources as sources_engine  # noqa: E402
from tools.maturity_gate import gate as gate_engine  # noqa: E402
from tools.maturity_gate import report as base_report  # noqa: E402
from tools.maturity_gate.evidence import EvidenceResolver  # noqa: E402
from tools.maturity_gate.spec import parse_spec  # noqa: E402

PASS, FAIL, WITHHELD = "PASS", "FAIL", "WITHHELD"


def _crit(name: str, status: str, detail: str,
          numbers: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    return {"name": name, "status": status, "pass": status == PASS, "detail": detail,
            "numbers": dict(numbers or {})}


def _run_pytest(targets: Sequence[str]) -> Dict[str, Any]:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", *targets],
                          cwd=str(REPO_ROOT), capture_output=True, text=True)
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-6:]
    return {"command": " ".join(targets), "returncode": proc.returncode,
            "green": proc.returncode == 0, "tail": tail}


def compute_delivery(gate: Mapping[str, Any], presence: Mapping[str, Mapping[str, Any]],
                     summary: Mapping[str, Any], contracts: Mapping[str, Any],
                     maturity: Mapping[str, Any], report_check: Optional[Mapping[str, Any]],
                     rollback_ok: bool, sql_written: Mapping[str, bool],
                     min_real: int = 5,
                     sql_smoke: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """战役验收判据 a–f（判据文字与阈值由任务契约给定，数字全部实测）。"""
    total = int(summary["nodes_total"])
    delivered = int(summary["nodes_delivered"])
    coverage = float(summary["artifact_coverage"])

    a = _crit("artifact coverage", PASS if delivered >= total else FAIL,
              f"delivered {delivered}/{total} (coverage {coverage:.4f}), "
              f"artifact_total {summary['artifact_total']}",
              {"delivered": delivered, "total": total, "coverage": coverage})

    b_status = {PASS: PASS, FAIL: FAIL}.get(str(contracts["verdict"]), WITHHELD)
    b = _crit("cross-node interface contracts 0 violations", b_status,
              f"verdict {contracts['verdict']}; checked {contracts['checked_total']}"
              f"/{contracts['contracts_total']} (interfaces "
              f"{contracts['interface_contracts_checked']}"
              f"/{contracts['interface_contracts_total']}), "
              f"violations {contracts['violations_total']}, "
              f"withheld {contracts['withheld_total']}",
              {"violations": contracts["violations_total"],
               "withheld": contracts["withheld_total"],
               "checked": contracts["checked_total"]})

    gate_keys = [s["node_key"] for s in gate["node_summaries"]]
    passed_nodes = [k for k in gate_keys
                    for s in gate["node_summaries"]
                    if s["node_key"] == k and s["criteria_total"]
                    and s["passed"] == s["criteria_total"]]
    unrecorded = [k for k in gate_keys if k not in presence]
    honest_absent = [k for k in gate_keys
                     if not ((presence.get(k) or {}).get("delivery") or {}).get("delivered")]
    withheld_criteria = sum(s["withheld"] for s in gate["node_summaries"])
    mismatch = int(gate["campaign"]["claim_mismatch_total"])
    c_status = PASS if (not unrecorded and not mismatch) else FAIL
    c = _crit("per-node acceptance pass-or-honest-record", c_status,
              f"passed {len(passed_nodes)}, honest-absent {len(honest_absent)} "
              f"(withheld criteria {withheld_criteria}), unrecorded {len(unrecorded)}, "
              f"claim_mismatch {mismatch}",
              {"passed": len(passed_nodes), "honest_absent": len(honest_absent),
               "withheld_criteria": withheld_criteria, "unrecorded": len(unrecorded),
               "claim_mismatch": mismatch})

    d_complete = bool(report_check and report_check.get("complete"))
    missing = list((report_check or {}).get("missing") or [])
    d = _crit("campaign report six elements", PASS if d_complete else FAIL,
              "all present" if d_complete else f"missing {missing}",
              {"missing": missing})

    real_total = int(maturity["real_total"])
    real_upstream = int(maturity.get("real_upstream_total", real_total))
    e = _crit(f"capability ledger REAL registrations >= {min_real}",
              PASS if real_total >= min_real else FAIL,
              f"REAL(upstream) {real_upstream}, REAL(all) {real_total} / required {min_real} "
              f"(deferred {maturity['deferred_total']})",
              {"real": real_upstream, "real_all": real_total,
               "deferred": maturity["deferred_total"], "required": min_real})

    smoke_ok = True if not (sql_smoke or {}).get("available") else bool(sql_smoke.get("ok"))
    f_ok = all(sql_written.values()) and rollback_ok and smoke_ok
    smoke_txt = ("not_run(no psql/TEMP)" if not (sql_smoke or {}).get("available")
                 else f"smoke={sql_smoke.get('ok')} (first={sql_smoke.get('after_first')},"
                      f" second={sql_smoke.get('after_second')},"
                      f" after_rollback={sql_smoke.get('after_rollback')})")
    f = _crit("maturity order SQL + rollback", PASS if f_ok else FAIL,
              f"insert {sql_written.get('insert')}, rollback {sql_written.get('rollback')}, "
              f"rollback_covers {rollback_ok}, inserted_entries {real_total}, {smoke_txt}",
              {"sql": dict(sql_written), "rollback_covers": rollback_ok,
               "inserted_entries": real_total, "smoke": dict(sql_smoke or {})})

    crit = {"a": a, "b": b, "c": c, "d": d, "e": e, "f": f}
    hard = [k for k, v in crit.items() if v["status"] == FAIL]
    if hard:
        verdict = FAIL
    elif all(v["status"] == PASS for v in crit.values()):
        verdict = PASS
    else:
        verdict = WITHHELD
    return {"criteria": crit, "verdict": verdict, "hard_fail": hard}


def deliver(snapshot_path: str | Path, sources_path: str | Path,
            contracts_path: str | Path, out_dir: Optional[str | Path] = None,
            report_path: Optional[str | Path] = None, spec_path: Optional[str | Path] = None,
            run_tests: bool = False, write_figures: bool = True,
            run_command: str = "python3 -m tools.campaign_integration.cli") -> Dict[str, Any]:
    snapshot = sources_engine.load_json(snapshot_path)
    cfg = sources_engine.load_sources(sources_path)
    out = Path(out_dir or cfg.get("integration_out") or
               (REPO_ROOT / "artifacts" / "campaign_integration"))
    if not out.is_absolute():
        out = REPO_ROOT / out
    spec_file = Path(spec_path or cfg.get("spec_path") or (out / "spec.json"))
    if not spec_file.is_absolute():
        spec_file = REPO_ROOT / spec_file
    rep_file = Path(report_path or cfg.get("report_path") or (out / "report.md"))
    if not rep_file.is_absolute():
        rep_file = REPO_ROOT / rep_file
    work_root = str(cfg["work_root"])

    # 1) 收集
    collected = sources_engine.collect(snapshot, cfg)
    presence = collected["presence"]
    summary = collected["summary"]
    provenance = collected["provenance"]
    sources_engine.write_presence(presence, out)

    # 2) 证据规格（快照程序化生成）+ 证据层三值裁定（复用 maturity_gate）
    spec = sources_engine.build_spec(snapshot, cfg)
    sources_engine.write_json(spec, spec_file)
    gate = gate_engine.evaluate_campaign(parse_spec(spec),
                                         EvidenceResolver(work_root=work_root))

    # 3) 契约层（接口契约 + 注册表自洽）
    contracts = contracts_engine.evaluate(
        contracts_engine.load_contracts(contracts_path), snapshot, presence)

    # 4) 成熟化单（自身机制正控需先拿到测试结论）
    self_cfg = cfg.get("self_capability") or {}
    self_tests = None
    if self_cfg.get("require_green_tests"):
        self_tests = _run_pytest(self_cfg.get("test_targets") or
                                 ["tests/test_campaign_integration_gate.py"])
    plan = snapshot.get("plan") or {}
    campaign_meta = {"plan_id": plan.get("plan_id"), "name": plan.get("name"),
                     "description": plan.get("description")}
    entries = maturity_engine.build_entries(gate, presence, cfg, repo_root=REPO_ROOT,
                                            self_tests=self_tests)
    maturity_doc = maturity_engine.plan_document(entries, campaign_meta, provenance)
    insert_sql = maturity_engine.render_insert_sql(entries, campaign_meta,
                                                   gate.get("input_digest"))
    rollback_sql = maturity_engine.render_rollback_sql(entries, campaign_meta)
    rollback_ok = maturity_engine.rollback_covers(insert_sql, rollback_sql)
    out.mkdir(parents=True, exist_ok=True)
    (out / "feature_ledger.sql").write_text(insert_sql, encoding="utf-8")
    (out / "feature_ledger_rollback.sql").write_text(rollback_sql, encoding="utf-8")
    sql_written = {"insert": (out / "feature_ledger.sql").is_file(),
                   "rollback": (out / "feature_ledger_rollback.sql").is_file()}
    sql_smoke = sql_smoke_engine.verify(insert_sql, rollback_sql)

    # 5) 报告与交付层判据（d 依赖报告自检 → 两轮渲染）
    withheld_total = sum(s["withheld"] for s in gate["node_summaries"])
    prelim = compute_delivery(gate, presence, summary, contracts, maturity_doc, None,
                              rollback_ok, sql_written,
                              int(((cfg.get("defaults") or {}).get("min_real_registrations")) or 5),
                              sql_smoke)
    def make_limitations(delivery_res: Mapping[str, Any]) -> List[str]:
        """限制条款按**最终**交付裁定渲染（不许出现「自检未跑完时的临时 FAIL」这类陈旧数字）。"""
        return [
        f"上游 {summary['nodes_total']} 个节点：任务状态分布 {summary['mission_status_counts']}，"
        f"其中 {len(summary['infra_blocked_keys'])} 个被基础设施阻塞（block_reason 含 infra）。"
        f"本报告不把『在飞/阻塞』写成『通过』：证据层裁定只能是 {gate['campaign']['verdict']}。",
        "证据协议是**新契约**（`docs/CAMPAIGN_EVIDENCE_PROTOCOL.md`）：节点须把可复算数字写进"
        "规范交付位 `artifacts/<evidence_dir>/<node_key>.json`。既有的零散 artifact 字段命名"
        "各异，只有显式映射进规格的字段能被取值复算；未映射一律 WITHHELD，不做模糊猜测。"
        f"当期实测：{summary['nodes_delivered']}/{summary['nodes_total']} 个节点有 artifact，"
        f"其中协议符合 {len(summary['protocol_conformant_keys'])} 个——『有东西』与『数可复算』"
        "刻意分开计分。",
        f"跨节点接口契约 {contracts['interface_contracts_total']} 条，当期真被检查 "
        f"{contracts['interface_contracts_checked']} 条：其余两侧均无接口证据 → WITHHELD。"
        "反空真规则刻意让它不产生『0 违反』的假绿。",
        f"交付层硬失败项 {delivery_res['hard_fail']} 是**实测缺口的直接后果**，不是判定器保守："
        f"覆盖率 {summary['artifact_coverage']:.4f}、REAL 条目 {maturity_doc['real_total']}"
        f"（其中上游 {maturity_doc.get('real_upstream_total', 0)}）。"
        "FAIL 不可被其它绿项抵消，也不允许被后续报告覆盖。",
        f"契约违反 {contracts['violations_total']} 条已逐条列明（含报告自述 mission 与索引 "
        "mission 不一致的报告链断裂）：这类问题不改变任何物理结论，但会让『按报告计分』的门失效，"
        "故报告只作辅助信号、不作判据。",
        "本节点不代跑上游仓库测试：上游测试属于其 own mission 的验收，平台不越权；"
        "本节点的 pytest 只覆盖集成机制（合成域夹具 + 真实战役工件重放）。",
        "特征化边界：机制层可复用于任意战役（节点/接口/单位全由工件注入），"
        "但不含领域判据推导——领域阈值仍须由各仓节点给出。",
        ]

    limitations = make_limitations(prelim)
    error_headline = (
        "本任务为**集成与门裁定**，不做场量求解，也没有实验/解析基准量可对标，"
        "故『<3%』**不适用**（不是没算准，是没有可对标的连续量）。可对标的只有计数类判据"
        "（产物覆盖率、契约违反数、台账条目数），逐项列于下：实测 vs 阈值均为整数计数，"
        "不冒充 3% 精度宣称。"
    )
    error_rows = [
        {"quantity": "artifact coverage", "measured": summary["artifact_coverage"],
         "predicate": f">= 1.0 ({summary['nodes_total']}/{summary['nodes_total']})",
         "note": f"实测交付 {summary['nodes_delivered']}/{summary['nodes_total']}，"
                 f"协议符合 {len(summary['protocol_conformant_keys'])} 个"},
        {"quantity": "contract violations", "measured": contracts["violations_total"],
         "predicate": "== 0",
         "note": f"真被检查 {contracts['checked_total']}/{contracts['contracts_total']} 条契约"
                 f"（接口 {contracts['interface_contracts_checked']}"
                 f"/{contracts['interface_contracts_total']}）"},
        {"quantity": "interface contracts checked",
         "measured": contracts["interface_contracts_checked"],
         "predicate": f"== {contracts['interface_contracts_total']}",
         "note": "两侧无证据即 WITHHELD（反空真）"},
        {"quantity": "REAL ledger entries (upstream)",
         "measured": maturity_doc.get("real_upstream_total", maturity_doc["real_total"]),
         "predicate": f">= {int(((cfg.get('defaults') or {}).get('min_real_registrations')) or 5)}",
         "note": "四道准入全过才计数；deferred 逐条附具名原因"},
        {"quantity": "withheld criteria", "measured": withheld_total,
         "predicate": "== 0",
         "note": "判据因无证据停在 WITHHELD（不知道≠不过），逐条见 gate.json#nodes"},
    ]

    ctx: Dict[str, Any] = {
        "spec_id": gate["spec_id"], "gate": gate, "plan": plan,
        "snapshot": {"fetched_at": snapshot.get("fetched_at")},
        "presence": presence, "presence_summary": summary, "provenance": provenance,
        "contracts": contracts, "maturity": maturity_doc,
        "delivery": {}, "delivery_verdict": WITHHELD, "delivery_hard_fail": [],
        "figures": [], "limitations": limitations,
        "error_headline": error_headline, "error_rows": error_rows,
        "run_command": run_command, "spec_path": str(spec_file),
        "out_dir": str(out), "rollback_covers": rollback_ok,
    }
    min_real = int(((cfg.get("defaults") or {}).get("min_real_registrations")) or 5)
    delivery_res = compute_delivery(gate, presence, summary, contracts, maturity_doc,
                                    None, rollback_ok, sql_written, min_real, sql_smoke)
    ctx["delivery"] = delivery_res["criteria"]
    if write_figures:
        ctx["figures"] = render_engine.build_figures(out / "figures", ctx)
    md = render_engine.render(ctx)
    report_check = base_report.check_six_elements(md, figures=ctx["figures"])

    delivery_res = compute_delivery(gate, presence, summary, contracts, maturity_doc,
                                    report_check, rollback_ok, sql_written, min_real, sql_smoke)
    ctx["delivery"] = delivery_res["criteria"]
    ctx["delivery_verdict"] = delivery_res["verdict"]
    ctx["delivery_hard_fail"] = delivery_res["hard_fail"]
    ctx["limitations"] = limitations = make_limitations(delivery_res)
    if write_figures:
        ctx["figures"] = render_engine.build_figures(out / "figures", ctx)
    md = render_engine.render(ctx)
    report_check = base_report.check_six_elements(md, figures=ctx["figures"])
    rep_file.parent.mkdir(parents=True, exist_ok=True)
    rep_file.write_text(md, encoding="utf-8")

    tests = _run_pytest(["tests/test_campaign_integration_gate.py"]) if run_tests else None

    # 6) 判定体落盘（判定体里给出机器验收判据 → 产物路径映射，便于自动复核）
    gate["delivery_criteria"] = delivery_res["criteria"]
    gate["delivery_verdict"] = delivery_res["verdict"]
    gate["delivery_hard_fail"] = delivery_res["hard_fail"]
    gate["presence_summary"] = summary
    gate["presence_provenance"] = provenance
    gate["contracts_summary"] = {k: contracts[k] for k in
                                 ("verdict", "contracts_total", "interface_contracts_total",
                                  "interface_contracts_checked", "checked_total",
                                  "withheld_total", "violations_total")}
    gate["maturity_summary"] = {k: maturity_doc[k] for k in
                                ("entries_total", "real_total", "real_upstream_total",
                                 "self_registered", "deferred_total", "levels", "real",
                                 "deferred")}
    gate["report_check"] = report_check
    gate["report_path"] = str(rep_file)
    gate["spec_path"] = str(spec_file)
    gate["snapshot"] = {"plan_id": plan.get("plan_id"), "name": plan.get("name"),
                        "status": plan.get("status"), "fetched_at": snapshot.get("fetched_at"),
                        "digest": provenance["snapshot_digest"]}
    gate["figures"] = [{"name": f["name"], "path": f["path"], "caption": f["caption"]}
                       for f in ctx["figures"]]
    gate["integration_tests"] = tests
    gate["sql_smoke"] = sql_smoke
    gate["limitations"] = limitations
    gate["artifacts"] = {
        "presence_dir": str(out / "presence"),
        "spec": str(spec_file), "gate": str(out / "gate.json"),
        "contracts": str(out / "contracts.json"),
        "maturity_plan": str(out / "maturity_plan.json"),
        "sql": str(out / "feature_ledger.sql"),
        "rollback_sql": str(out / "feature_ledger_rollback.sql"),
        "report": str(rep_file),
    }
    gate["acceptance_criteria"] = {
        k: {"criterion": v["name"], "measured": v["numbers"], "status": v["status"],
            "artifact": f"{out}/gate.json#delivery_criteria.{k}"}
        for k, v in delivery_res["criteria"].items()
    }

    gate_engine.dump_json(gate, out / "gate.json")
    gate_engine.dump_json(contracts, out / "contracts.json")
    gate_engine.dump_json(maturity_doc, out / "maturity_plan.json")
    return {"gate": gate, "contracts": contracts, "maturity": maturity_doc,
            "report_check": report_check, "report_path": str(rep_file),
            "insert_sql": insert_sql, "rollback_sql": rollback_sql,
            "presence": presence}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="战役集成与验收门运行器")
    ap.add_argument("--snapshot", required=True, help="战役注册表快照 JSON")
    ap.add_argument("--sources", required=True, help="来源配置 JSON")
    ap.add_argument("--contracts", required=True, help="契约工件 JSON")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--spec", default=None)
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--run-tests", action="store_true",
                    help="额外跑集成机制 pytest 并把结果写进判定体")
    args = ap.parse_args(argv)
    result = deliver(args.snapshot, args.sources, args.contracts, out_dir=args.out_dir,
                     report_path=args.report, spec_path=args.spec,
                     run_tests=args.run_tests, write_figures=not args.no_figures)
    gate = result["gate"]
    print(json.dumps({
        "evidence_layer": gate["campaign"]["verdict"],
        "delivery_layer": gate["delivery_verdict"],
        "delivery_criteria": {k: v["status"] for k, v in gate["delivery_criteria"].items()},
        "artifacts": gate["artifacts"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())