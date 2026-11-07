#!/usr/bin/env python3
"""一键审计：台账 → 探针 → 三刀判决 → 混淆矩阵 → 变异体自检 → 图 → 报告 JSON。

用法：
    python3 tools/audit/falsification/run_audit.py                # 打在线 :8001
    python3 tools/audit/falsification/run_audit.py --base-url http://127.0.0.1:8001
    python3 tools/audit/falsification/run_audit.py --no-probe     # 只重建台账与变异体自检

产物（全部落在 artifacts/capability_falsification/）：
    capabilities.json      单一真值台账（21 项，含 callable_spec）
    probe_trace.json       在线探针原始证据（每条样本、每次调用、每把刀）
    verdicts.json          逐能力声明 vs 证伪
    confusion_matrix.json  混淆矩阵 + 假阳性率
    mutant_selftest.json   刀具检出能力/误报率
    report_data.json       判据数字 + 产物路径（报告与验收引用此文件）
    fig_confusion_matrix.png / fig_knife_profiles.png / fig_mutant_selftest.png
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys
from typing import Any, Dict, List

ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit.falsification import (  # noqa: E402
    figures, grade, runner, selftest, spec,
)

OUT = ROOT / spec.OUT_DIR_REL


def tool_capability_ids() -> Dict[str, Any]:
    """审计工具（audit_capabilities）能返回的能力 id 集合。

    真值来源按可用性排列，全部记录下来（不静默择一）：
      1. 仓内对账台账 resolved（ops/tp_worker.py::_cap_sources 的首选仓内来源）；
      2. agent 工具快照 tests/fixtures/capability_ledger/capabilities_snapshot.json
         （若存在）；
      3. 环境变量 TP_CAP_LEDGER 指定的台账。
    注意：本会话调用 audit_capabilities 工具本身返回「读取能力清单失败」
    （硬编码路径 /root/ship-performance-platform-incoming/... 在 incoming 树中只剩
    .pyc），故以工具的仓内首选真值来源取 id 集合，并如实记录该事实。
    """
    sources: Dict[str, List[str]] = {}
    resolved_path = ROOT / spec.LEDGER_REL
    if resolved_path.is_file():
        d = json.loads(resolved_path.read_text(encoding="utf-8"))
        sources["resolved_ledger"] = sorted(c["id"] for c in d.get("capabilities", []))
    snap_path = ROOT / spec.SNAPSHOT_REL
    if snap_path.is_file():
        try:
            d = json.loads(snap_path.read_text(encoding="utf-8"))
            caps = d.get("capabilities") or []
            if isinstance(caps, dict):
                ids = sorted(caps.keys())
            else:
                ids = sorted(str(c.get("id")) for c in caps)
            sources["tool_snapshot"] = ids
        except Exception as exc:  # 快照坏了要留痕，不能当没有
            sources["tool_snapshot"] = [f"<unreadable: {exc}>"]
    primary = "resolved_ledger" if "resolved_ledger" in sources else next(iter(sources), None)
    ids = sources.get(primary, []) if primary else []
    return {"source": primary, "sources": sources, "ids": ids,
            "tool_call_error": ("audit_capabilities 工具返回读取失败（硬编码路径缺文件）；"
                                "改用其仓内首选真值来源 resolved_ledger")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=runner.DEFAULT_BASE)
    ap.add_argument("--no-probe", action="store_true", help="跳过在线探针（离线重建台账/自检）")
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    ledger = spec.build_ledger()
    grade.write_json(OUT / "capabilities.json", ledger)
    print(f"[1/6] capabilities.json: {ledger['n_capabilities']} capabilities")

    tool_ids = tool_capability_ids()
    sym = sorted({c["id"] for c in ledger["capabilities"]} ^ set(tool_ids["ids"]))
    print(f"[2/6] tool ids {len(tool_ids['ids'])} / ledger ids "
          f"{len(ledger['capabilities'])} / symmetric diff {len(sym)}")

    trace_path = OUT / "probe_trace.json"
    if args.no_probe and trace_path.is_file():
        probe = json.loads(trace_path.read_text(encoding="utf-8"))
        probe.pop("_meta", None)
        print(f"[3/6] 复用已有 probe_trace.json（--no-probe）")
    else:
        transport = runner.HttpTransport(args.base_url, timeout=args.timeout)
        probe = runner.probe_ledger(ledger, transport)
        probe["_meta"] = {"base_url": args.base_url,
                          "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}
        grade.write_json(trace_path, probe)
        print(f"[3/6] probe_trace.json: {probe['n_http_calls']} HTTP calls "
              f"({len(probe['capabilities'])} capabilities)")

    verdicts = grade.grade_traces(ledger, probe)
    grade.write_json(OUT / "verdicts.json", verdicts)
    matrix = grade.confusion_matrix(verdicts)
    grade.write_json(OUT / "confusion_matrix.json", matrix)
    print(f"[4/6] verdicts/matrix: over-claim rate={matrix['overclaim_rate']}")

    mutants = selftest.run_mutants()
    grade.write_json(OUT / "mutant_selftest.json", mutants)
    print(f"[5/6] mutant selftest: detection={mutants['detection_rate']} "
          f"false_alarm={mutants['false_alarm_rate']}")

    report = grade.build_report(ledger, probe, verdicts, matrix, mutants, tool_ids)
    report["artifacts"] = {
        "ledger": f"{spec.OUT_DIR_REL}/capabilities.json",
        "probe_trace": f"{spec.OUT_DIR_REL}/probe_trace.json",
        "verdicts": f"{spec.OUT_DIR_REL}/verdicts.json",
        "confusion_matrix": f"{spec.OUT_DIR_REL}/confusion_matrix.json",
        "mutant_selftest": f"{spec.OUT_DIR_REL}/mutant_selftest.json",
        "report_data": f"{spec.OUT_DIR_REL}/report_data.json",
        "fig_confusion_matrix": f"{spec.OUT_DIR_REL}/fig_confusion_matrix.png",
        "fig_knife_profiles": f"{spec.OUT_DIR_REL}/fig_knife_profiles.png",
        "fig_mutant_selftest": f"{spec.OUT_DIR_REL}/fig_mutant_selftest.png",
    }
    figs = {}
    try:
        figs["confusion_matrix"] = figures.confusion_matrix_figure(
            matrix, str(ROOT / report["artifacts"]["fig_confusion_matrix"]),
            title="Capability evidence: claimed vs falsified (W18-N09)")
        figs["knife_profiles"] = figures.knife_profiles_figure(
            probe, str(ROOT / report["artifacts"]["fig_knife_profiles"]))
        figs["mutant_selftest"] = figures.mutant_figure(
            mutants, str(ROOT / report["artifacts"]["fig_mutant_selftest"]))
    except Exception as exc:  # 图失败不得掩盖判决结果
        figs["error"] = f"{type(exc).__name__}: {exc}"
    report["figures"] = figs
    grade.write_json(OUT / "report_data.json", report)
    print(f"[6/6] report_data.json + figures: {sorted(k for k in figs if k != 'error')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())