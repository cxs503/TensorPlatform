"""证据汇总：把 artifacts/harness_gateway/ 下的门链报告程序化汇总成一份 JSON。

报告里的每个数字都必须能被这个脚本复算出来（零手抄）：
    python3 -m harness_gateway.experiments.summarize --artifacts artifacts/harness_gateway
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

RUNS = {
    "0001_real_patch": "真实修补①（饱和调节器加固）",
    "0002_gate_only_after_patch": "门体自检（补丁后 AHEAD 态）",
    "0004_redteam_NC1": "红队负例①语法错（编译门）",
    "0004_redteam_NC2": "红队负例②运行期崩（金丝雀门）",
    "0004_redteam_NC3": "红队负例③明文凭据回注（凭据扫描）",
    "0003_order_patch_0002": "真实修补②（战史官钟 psql 超时，经诊断单执行）",
    "0003_order_NC1_syntax": "诊断单内负例①",
    "0003_order_NC2_runtime": "诊断单内负例②",
    "0005_final_state": "终态门体自检（两补丁 AHEAD，待部署）",
}


def summarize(artifacts: Path) -> dict:
    runs: dict[str, dict] = {}
    for run_id, label in RUNS.items():
        path = artifacts / run_id / "report.json"
        if not path.exists():
            continue
        rep = json.loads(path.read_text(encoding="utf-8"))
        stages = [
            {"gate": s["gate"], "status": s["status"], "duration_ms": s["duration_ms"], "required": s.get("required")}
            for s in rep["stages"]
        ]
        rb = rep.get("rollback") or {}
        rb_ev = rb.get("evidence", {}) if isinstance(rb, dict) else {}
        runs[run_id] = {
            "label": label,
            "verdict": rep["verdict"],
            "caught_by": rep.get("caught_by"),
            "stages": stages,
            "stage_count": len(stages),
            "gate_duration_ms_total": sum(s["duration_ms"] for s in stages),
            "rollback_status": rb.get("status") if rb else None,
            "rollback_files_matched": sum(1 for f in rb_ev.get("files", []) if f.get("match")),
            "rollback_files_total": len(rb_ev.get("files", [])),
            "git_diff_after_rollback": rb_ev.get("git_diff_vs_head_after_rollback", []),
            "patch_diff": (rep.get("artifacts") or {}).get("patch_diff"),
            "preflight_ahead": (rep["stages"][0]["evidence"].get("units_ahead_of_live_pending_deploy")
                                if rep["stages"] else []),
        }

    canary_units: dict[str, dict] = {}
    for run_id in ("0001_real_patch", "0002_gate_only_after_patch"):
        rep = json.loads((artifacts / run_id / "report.json").read_text(encoding="utf-8"))
        for stage in rep["stages"]:
            if stage["gate"] == "canary_single_process":
                for unit in stage["evidence"].get("units", []):
                    canary_units[unit["unit"]] = {
                        "mode": unit["mode"],
                        "declared_mode": unit.get("declared_mode"),
                        "status": unit["status"],
                        "degraded": unit.get("degraded"),
                        "shim_calls": unit.get("shim_calls"),
                        "returncode": unit.get("returncode"),
                    }

    orders: dict[str, dict] = {}
    diag_dir = artifacts / "diagnostics"
    if diag_dir.exists():
        for path in sorted(diag_dir.glob("*_execution.json")):
            rep = json.loads(path.read_text(encoding="utf-8"))
            orders[rep["order_id"]] = {
                "verdict": rep["verdict"],
                "counts": rep["counts"],
                "items": [(i["id"], i["op"], i["status"], i["duration_ms"]) for i in rep["items"]],
            }

    timeout_path = artifacts / "timeout_bound" / "measurement.json"
    timeout = json.loads(timeout_path.read_text(encoding="utf-8")) if timeout_path.exists() else None

    manifest_path = Path("harness_gateway/mirror/MIRROR_MANIFEST.json")
    mirror = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None

    deploy = None
    block_only = artifacts / "0005_final_state" / "report.json"
    if not block_only.exists():
        block_only = artifacts / "0002_gate_only_after_patch" / "report.json"
    if block_only.exists():
        rep = json.loads(block_only.read_text(encoding="utf-8"))
        plan = rep.get("deploy_plan") or {}
        deploy = {"actions": plan.get("actions"),
                  "units": [(u["unit"], u["action"], u.get("carries_redaction_placeholder")) for u in plan.get("units", [])]}

    return {
        "schema": "harness-gateway/mission-evidence/1",
        "runs": runs,
        "canary_units": canary_units,
        "diagnostic_orders": orders,
        "timeout_experiment": timeout,
        "mirror_manifest": {
            "unit_count": mirror["unit_count"],
            "redaction_total": mirror["redaction_total"],
            "staged_patch_units": mirror.get("staged_patch_units", []),
            "units": [(u["name"], u["role"], u["source_bytes"], len(u["redactions"])) for u in mirror["units"]],
        } if mirror else None,
        "deploy_plan": deploy,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", default="artifacts/harness_gateway")
    ap.add_argument("--out", default="artifacts/harness_gateway/MISSION_EVIDENCE.json")
    args = ap.parse_args()
    ev = summarize(Path(args.artifacts))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ev, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in ev.items() if k in ("runs", "diagnostic_orders")}, indent=2,
                     ensure_ascii=False)[:3000])
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())