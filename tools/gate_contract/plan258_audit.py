#!/usr/bin/env python3
"""plan 258 九节点 gate.json 回填 + 复算 + 负控 + 台账 + 汇总（一条命令可复算）。

产物（全部落 artifacts/gate_contract/）：
  plan258/<gate_id>/gate.json          九份门证据契约
  plan258/criteria_registry.json       权威判据登记表（算子/阈值/单位）
  verify_all.json                      九门复算判定（PASS/FAIL/WITHHELD + 摘要）
  negcontrol_summary.json              五类伪证据 ×12 的假通过率
  ledger.db                            逐判据判定台账（SQL 可回溯）
  summary.json                         机器验收判据 C1–C6 的数字与工件路径
"""

from __future__ import annotations

import copy
import glob
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gate_contract.backfill import backfill
from tools.gate_contract.crossarch import report as crossarch_report
from tools.gate_contract.ledger import append_verdict, connect, rows, trace_back
from tools.gate_contract.negcontrol import build_cases, measure_cases
from tools.gate_contract.verify import verify_gate

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "gate_contract"
GATE_ROOT = OUT / "plan258"
CLAIMS = ROOT / "config" / "gate_contract" / "plan258_claims.json"
RUN_CMD = "python3 tools/gate_contract/plan258_audit.py"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    bf = backfill(CLAIMS, GATE_ROOT, issued_utc="2026-11-10T00:00:00Z")
    (OUT / "backfill_summary.json").write_text(
        json.dumps(bf, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    registry = json.loads((GATE_ROOT / "criteria_registry.json").read_text(encoding="utf-8"))
    gate_paths = sorted(glob.glob(str(GATE_ROOT / "*" / "gate.json")))
    results = [verify_gate(p, registry=registry) for p in gate_paths]
    grades = {g: sum(1 for r in results if r["grade"] == g) for g in ("PASS", "FAIL", "WITHHELD")}
    verify_all = {"n": len(results),
                  "grades": grades,
                  "digests": {r["gate_id"]: r["verdict_digest"] for r in results},
                  "gates": results}
    (OUT / "verify_all.json").write_text(
        json.dumps(verify_all, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    db = OUT / "ledger.db"
    if db.is_file():
        db.unlink()
    con = connect(db)
    for res in results:
        append_verdict(con, res, run_cmd=RUN_CMD)
    ledger_total = len(rows(con))
    trace = trace_back(con, limit=20)
    con.close()

    cases = build_cases([Path(p).parent for p in gate_paths], OUT / "negcontrol", per_class=12)
    nc = measure_cases(cases, registry, positive_dirs=[Path(p).parent for p in gate_paths])
    (OUT / "negcontrol_summary.json").write_text(
        json.dumps(nc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    # C3：结构门不崩 —— 无 gate / 空对象 / 坏 JSON 三形态都必须恒等降级为 WITHHELD
    structural = []
    for label, kwargs in (("absent", {"gate_path": OUT / "nonexistent.json"}),
                          ("empty", {"gate_json_text": "{}"}),
                          ("bad_json", {"gate_json_text": "{not json"})):
        try:
            res = verify_gate(**kwargs, registry=registry)
            structural.append({"case": label, "grade": res["grade"], "raised": False,
                               "codes": sorted({i["code"] for i in res["issues"]})})
        except Exception as exc:  # noqa: BLE001 - 记异常即失败
            structural.append({"case": label, "grade": None, "raised": True,
                               "error": type(exc).__name__})

    # C4：跨架构一致性（异构指纹回填 + libc 往返 + 远端实测件比对）
    cross = crossarch_report(GATE_ROOT, GATE_ROOT / "criteria_registry.json", CLAIMS, OUT,
                             remote_artifact=OUT / "verify_all_cluster.json")

    (OUT / "crossarch.json").write_text(
        json.dumps(cross, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    # C7：硬化探针 —— 每条 fail-closed 判据都要有一个「应当 withhold」的最小反例
    probes = []
    gate0 = Path(gate_paths[0])
    base_doc = json.loads(gate0.read_text(encoding="utf-8"))

    def _drop(doc):
        doc["criteria"].pop()

    def _dup(doc):
        doc["criteria"].append(copy.deepcopy(doc["criteria"][0]))

    def _escape(doc):
        doc["criteria"][0]["artifact"]["path"] = "../../../etc/passwd"

    def _identity(doc):
        return None

    probe_specs = (
        ("registry_omitted", _identity, None, None),
        ("criterion_dropped", _drop, "registry", None),
        ("criterion_duplicate", _dup, "registry", None),
        ("path_escape", _escape, "registry", None),
        ("gate_ambiguous", _identity, "registry", [{"code": "gate_ambiguous", "detail": "dup"}]),
        ("registry_tampered", _identity, "registry",
         [{"code": "registry_tampered", "detail": "pin"}]),
    )
    for label, mutate, reg_mode, pre in probe_specs:
        doc = copy.deepcopy(base_doc)
        mutate(doc)
        reg = None if reg_mode is None else registry
        res = verify_gate(gate_doc=doc, registry=reg, evidence_root=gate0.parent,
                          pre_issues=pre)
        probes.append({"probe": label, "grade": res["grade"],
                       "withheld": res["grade"] == "WITHHELD",
                       "codes": sorted({i["code"] for i in res["issues"]})})
    hardening = {
        "probes": probes,
        "n_probes": len(probes),
        "n_withheld": sum(1 for p in probes if p["withheld"]),
        "fail_open": [p["probe"] for p in probes if not p["withheld"]],
        "artifact": "artifacts/gate_contract/summary.json#C7_hardening",
    }

    domain_scan = OUT / "domain_scan_new_files.json"
    ds = json.loads(domain_scan.read_text(encoding="utf-8")) if domain_scan.is_file() else {}
    summary = {
        "plan_id": 258,
        "node_gates": bf["nodes"],
        "C1_backfill": {"coverage": bf["coverage"], "n_nodes": len(bf["nodes"]),
                        "n_gates_written": len(glob.glob(str(GATE_ROOT / "*" / "gate.json"))),
                        "grades": grades,
                        "silent_pass": sum(1 for r in results if r["grade"] == "PASS"
                                           and not r["registry_checked"]),
                        "gate_paths": [str(Path(p).resolve().relative_to(ROOT))
                                       if Path(p).resolve().is_relative_to(ROOT) else p
                                       for p in gate_paths],
                        "artifact": "artifacts/gate_contract/backfill_summary.json"},
        "C2_negative_control": {"classes": sorted(nc["by_class"]),
                                "n_cases": nc["n_cases"],
                                "false_pass": nc["false_pass"],
                                "false_pass_rate": nc["false_pass_rate"],
                                "detection_matrix_sha256": nc["detection_matrix_sha256"],
                                "artifact": "artifacts/gate_contract/negcontrol_summary.json"},
        "C3_structural_degradation": {"cases": structural,
                                      "exceptions": sum(1 for s in structural if s["raised"]),
                                      "all_withheld": all(s["grade"] == "WITHHELD" for s in structural)},
        "C4_cross_arch": {**cross,
                          "artifact": "artifacts/gate_contract/crossarch.json"},
        "C5_ledger": {"rows": ledger_total, "sampled": trace["checked"],
                      "evidence_rows": trace["evidence_rows"],
                      "traceable": trace["traceable"], "ratio": trace["ratio"],
                      "artifact": "artifacts/gate_contract/ledger.db"},
        "C6_domain_scan": {"scanned_files": ds.get("scanned_files"), "hits": ds.get("hits"),
                           "n_hits": len(ds.get("hits") or []),
                           "artifact": "artifacts/gate_contract/domain_scan_new_files.json"},
        "C7_hardening": hardening,
        "positive_controls": {"n": len(nc["positive_controls"]), "pass": nc["positive_pass"],
                              "fail": nc["positive_fail"]},
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("C1_backfill", "C2_negative_control",
                                              "C3_structural_degradation", "C4_cross_arch",
                                              "C5_ledger", "C6_domain_scan")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
