#!/usr/bin/env python3
"""能力台账消费者契约门（CI 入口，消费者契约门 A 的机器判据）。

判据（任一不满足即退出码 1，并写 ``artifacts/capability_ledger/ledger_contract_run.json``）：

C1 四消费者 sha256 全等（audit_capabilities / CLI / health / report_gate）；
C2 台账 ``schema_version`` 在 :data:`tools.audit.ledger_paths.SCHEMA_VERSIONS` 内；
C3 无漂移（台账内容 sha256 == pin 图章；等价副本之间无分歧）；
C4 四消费者状态均 OK，且无导入/执行错误。

用法：
    python3 tools/audit/ledger_contract_gate.py            # 人读摘要
    python3 tools/audit/ledger_contract_gate.py --check    # CI：红灯 → 1
    python3 tools/audit/ledger_contract_gate.py --json --out artifacts/...json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.audit import ledger_paths as LP  # noqa: E402

OUT = _ROOT / "artifacts" / "capability_ledger" / "ledger_contract_run.json"

CRITERIA = {
    "C1_consumer_sha256_equal": "四消费者必须解析到同一台账 sha256",
    "C2_schema_version_registered": "台账 schema_version 必须在 SCHEMA_VERSIONS 内",
    "C3_no_ledger_drift": "台账 sha256 必须与 pin 图章一致且等价副本无分歧",
    "C4_consumers_healthy": "四消费者状态必须均为 OK（无导入/执行错误）",
}


def run(root: str | None = None) -> dict:
    rep = LP.contract_report(root)
    shas = {n: c["sha256"] for n, c in rep["consumers"].items()}
    same_sha = bool(rep["canonical_sha256"]) and len(set(shas.values())) == 1 \
        and set(shas.values()) == {rep["canonical_sha256"]}
    healthy = all(c["status"] == LP.STATUS_OK and not c["error"] for c in rep["consumers"].values())
    drift = rep.get("drift") or []
    checks = {
        "C1_consumer_sha256_equal": same_sha,
        "C2_schema_version_registered": rep.get("schema_version") in LP.SCHEMA_VERSIONS,
        "C3_no_ledger_drift": (not drift) and "PIN_MISSING" not in (rep.get("reasons") or []),
        "C4_consumers_healthy": healthy,
    }
    failed = [k for k, v in checks.items() if not v]
    verdict = "PASS" if not failed else "FAIL"
    out = {
        "gate": "capability-ledger-consumer-contract",
        "contract_version": LP.CONTRACT_VERSION,
        "verdict": verdict,
        "checks": checks,
        "failed": failed,
        "criteria": CRITERIA,
        "canonical_sha256": rep["canonical_sha256"],
        "canonical_path": rep["canonical_path"],
        "ledger": rep["ledger"],
        "consumers": rep["consumers"],
        "drift": drift,
        "reasons": rep.get("reasons"),
        "relocated": rep.get("relocated"),
        "violations": rep.get("violations"),
        "checked_at": rep["checked_at"],
    }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="能力台账消费者契约门")
    ap.add_argument("--root", default=None)
    ap.add_argument("--check", action="store_true", help="红灯退出码 1（CI 用）")
    ap.add_argument("--json", action="store_true", help="打印完整 JSON")
    ap.add_argument("--out", default=str(OUT), help="落盘路径（--check 时写）")
    args = ap.parse_args(argv)

    res = run(args.root)
    if args.check or args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(f"verdict={res['verdict']} failed={res['failed'] or 'none'} "
              f"sha256={(res['canonical_sha256'] or '')[:12]}")
        for k, v in res["checks"].items():
            print(f"  [{'PASS' if v else 'FAIL'}] {k}: {CRITERIA[k]}")
    return 0 if res["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())