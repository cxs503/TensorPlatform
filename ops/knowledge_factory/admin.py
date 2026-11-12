#!/usr/bin/env python3
"""Knowledge Factory 管理面（人工/实验单用的显式操作，不做隐形调度）。

  export          → 导出泳道工作包分片 runs/knowledge_factory/tk_work_packages.json
                    （KBM-RCI-F1-ABC 等臂执行单按此分片派发；带契约与结果信道说明）
  seed-lane       → 给指定泳道建工包（A 臂整批/ B 臂对照）
  metrics         → 打印看板八指标并落快照
  gate-replay     → 用真实报告 markdown 重放双门（真语料 + 植入缺陷），证明门会拦
  status          → 工包状态矩阵
"""
import argparse
import json
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "knowledge_factory"

from . import contract as kc            # noqa: E402
from . import dashboard, discover, gates  # noqa: E402
from .scheduler import RUNS_DIR, Scheduler, Store  # noqa: E402


def cmd_export(sch, args):
    lane = args.lane
    pkgs = []
    for p in sch.store.packages(status="pending", lane=lane, limit=args.limit):
        report = sch.store.report(p["report_id"]) or {}
        if not report:
            continue
        pkg = kc.build_package(report, p["task_type"], p["lane"], sch.cfg, sch.contracts,
                               stratum=p["stratum"])
        pkgs.append(pkg)
    out = {
        "version": sch.cfg["contract"]["version"],
        "lane": lane,
        "generated_by": "ops/knowledge_factory/admin.py export",
        "count": len(pkgs),
        "result_channel": sch.cfg["result_channel"],
        "receipt_contract": {
            "path": "runs/<LANE>/<package_id>.json",
            "fields": ["arm", "package_id", "doc_ids", "agent_count", "wall_seconds",
                       "tokens", "compute_core_seconds", "docs_completed", "docs_passed",
                       "claims", "claims_with_locator", "locators_total", "locators_resolved"],
            "note": "locators_total/resolved 必须程序化反查所得，禁止自报；平台证据门会独立重算。",
        },
        "packages": pkgs,
    }
    path = os.path.join(RUNS_DIR, args.out or f"tk_work_packages_{lane}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    if args.out in (None, "tk_work_packages.json"):
        with open(os.path.join(RUNS_DIR, "tk_work_packages.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"export {lane}: {len(pkgs)} 包 → {path}")


def cmd_seed_lane(sch, args):
    pooled, _ = discover.prestratify(sch.store, args.count * 40,
                                     sch.cfg["pilot"].get("reconcile_min_chars", 200))
    made = 0
    for p in pooled:
        if made >= args.count:
            break
        if sch.store.package(kc.package_id(p["report_id"], args.task)):
            continue
        if sch.ensure_package(p["report_id"], args.task, lane=args.lane, stratum=p["stratum"]):
            made += 1
    print(f"seed-lane {args.lane}: 新建 {made} 包（task {args.task}）")


def cmd_metrics(sch, args):
    m = sch.snapshot("admin-metrics")
    print(dashboard.render(m))
    return m


def cmd_status(sch, args):
    rows = sch.store.q(
        "SELECT lane, task_type, status, count(*) FROM tk_work_packages "
        "GROUP BY 1,2,3 ORDER BY 1,2,3")
    print(f"{'lane':4} {'task':4} {'status':12} count")
    for r in rows:
        print(f"{r[0]:4} {r[1]:4} {r[2]:12} {r[3]}")
    return rows


def cmd_gate_replay(sch, args):
    """真语料重放：取真实报告 markdown，构造一个「全对」与四个「植入缺陷」结果，看门是否拦。

    这是门的自检，不是知识抽取产出：payload 只用于验证 g4/g5/g6 是否按字节判真，
    不进 tk_ingest_drafts（自检 payload 一律不落库）。
    """
    rid = int(args.report_id)
    rep = sch.store.report(rid)
    if not rep:
        print(f"报告 {rid} 不存在")
        return None
    md = rep["markdown"]
    quote = next((ln.strip() for ln in md.split("\n")
                  if len(ln.strip()) >= 40 and not ln.strip().startswith("#")), md[:80])
    good_hash = gates.sha256_text(md)
    base = {
        "package_id": kc.package_id(rid, "01"), "task_type": "01", "report_id": str(rid),
        "provenance": {"source_hash": good_hash, "source_field": "markdown",
                       "producer": "gate-selftest"},
        "outputs": {"asset_manifest.json": {"report_id": str(rid), "chars": len(md)}},
        "claims": [{"claim_id": f"c-{rid}-1", "text": quote[:80], "status": "supported",
                    "evidence": [{"quote": quote, "source_field": "markdown"}]}],
    }
    cases = {"control_all_good": base}

    import copy
    bad_quote = copy.deepcopy(base)
    bad_quote["claims"][0]["evidence"][0]["quote"] = "这句话不在原文里" * 3
    cases["defect_fake_quote"] = bad_quote

    bad_hash = copy.deepcopy(base)
    bad_hash["provenance"]["source_hash"] = "sha256:" + "0" * 64
    cases["defect_hash_mismatch"] = bad_hash

    dup = copy.deepcopy(base)
    dup["claims"].append(copy.deepcopy(dup["claims"][0]))
    cases["defect_duplicate_claim_id"] = dup

    weak = copy.deepcopy(base)
    weak["claims"][0]["evidence"] = []
    cases["defect_no_evidence"] = weak

    foreign = copy.deepcopy(base)
    foreign["report_id"] = str(rid + 1)
    foreign["package_id"] = kc.package_id(rid + 1, "01")
    cases["defect_wrong_report"] = foreign

    schema = gates.load_schema()
    pkg = {"package_id": kc.package_id(rid, "01"), "report_id": rid, "task_type": "01"}
    out = {"report_id": rid, "report_chars": len(md), "cases": {}}
    for name, obj in cases.items():
        sg, sok = gates.structure_gate(obj, schema, pkg)
        eg, eok, stats = gates.evidence_gate(obj, md, set(), sch.cfg["gates"]["min_quote_chars"],
                                             expected_report_id=rid)
        summary = gates.summarize(sg + eg, sok and eok)
        out["cases"][name] = {"ok": summary["ok"], "failed": summary["failed"],
                              "locator_rate": stats["locator_rate"]}
    path = os.path.join(RUNS_DIR, "gate_replay.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    for name, c in out["cases"].items():
        print(f"{name}: ok={c['ok']} failed={c['failed']}")
    print(f"→ {path}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--lane", default="C")
    e.add_argument("--limit", type=int, default=200)
    e.add_argument("--out", default=None)
    s = sub.add_parser("seed-lane")
    s.add_argument("--lane", default="B")
    s.add_argument("--count", type=int, default=2)
    s.add_argument("--task", default="01")
    s.add_argument("--manifest", default=None)
    sub.add_parser("metrics")
    sub.add_parser("status")
    g = sub.add_parser("gate-replay")
    g.add_argument("--report-id", required=True)
    a = ap.parse_args(argv)

    store = Store()
    store.ensure_schema()
    sch = Scheduler(store=store, verbose=True)
    fn = {"export": cmd_export, "seed-lane": cmd_seed_lane, "metrics": cmd_metrics,
          "status": cmd_status, "gate-replay": cmd_gate_replay}[a.cmd]
    fn(sch, a)
    return 0


if __name__ == "__main__":
    sys.exit(main())