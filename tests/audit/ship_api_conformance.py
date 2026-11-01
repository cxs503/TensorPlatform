#!/usr/bin/env python3
"""船舶总体性能 API 物理一致性 conformance 门（m1490，承接 m1473 审计 225 项结论）。

用法::

    python3 tests/audit/ship_api_conformance.py                    # ref + live 都跑
    python3 tests/audit/ship_api_conformance.py --target ref       # 只跑本仓正确物理（CI 门）
    python3 tests/audit/ship_api_conformance.py --target live --allow-pending
    python3 tests/audit/ship_api_conformance.py --json docs/m1490/conformance_live.json

退出码
------
0  所选 target 的 12 项 required 判据全部 PASS（``--allow-pending`` 时 live 的 FAIL 记为"待承接"）
1  有 required 判据 FAIL/ERROR
2  运行环境问题（src 不可导入等）

判据定义见 ``src/ship_perf/conformance.py``：7 项由 FAIL 转 PASS（A-CT / S-KB-BOX /
S-GZMAX / S-AREA-3040 / P-ETA-BOUND / P-KT / P-THRUST），5 项不得回归
（R-CF / R-RE / R-FN / S-GM / P-PD）；附加 2 项（S-VANISH / A-CR-SURF）单列。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

try:
    from ship_perf import conformance as cf  # noqa: E402
except Exception as e:  # pragma: no cover
    print(f"[FATAL] 无法导入 src/ship_perf: {e}", file=sys.stderr)
    sys.exit(2)


def _run(target, key: str) -> Dict[str, object]:
    results = cf.run_all(target)
    summary = cf.summarize(results)
    return {
        "target": key,
        "label": target.label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "checks": [r.to_dict() for r in results],
    }


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="船舶总体性能 API 物理一致性 conformance 门")
    ap.add_argument("--target", choices=["ref", "live", "both"], default="both")
    ap.add_argument("--base", default=os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001"))
    ap.add_argument("--json", default="")
    ap.add_argument(
        "--allow-pending",
        action="store_true",
        help="在线服务尚未承接修复时，live 侧 FAIL 只记录为待承接，不影响退出码",
    )
    args = ap.parse_args(argv)

    payload: Dict[str, object] = {"runs": []}
    rc = 0

    if args.target in ("ref", "both"):
        run = _run(cf.RefTarget(), "ref")
        payload["runs"].append(run)  # type: ignore[union-attr]
        print("=" * 118)
        print(f"[ref] {cf.RefTarget.label}")
        print(cf.format_table(cf.run_all(cf.RefTarget())))
        s = run["summary"]
        print(f"[ref] required {s['required_pass']}/{s['required_total']} PASS —— gate={'PASS' if s['gate_pass'] else 'FAIL'}")
        if not s["gate_pass"]:
            rc = 1

    if args.target in ("live", "both"):
        live = cf.LiveTarget(args.base)
        run = _run(live, "live")
        payload["runs"].append(run)  # type: ignore[union-attr]
        print("=" * 118)
        print(f"[live] {live.label} ({args.base})")
        print(cf.format_table(cf.run_all(live)))
        s = run["summary"]
        print(f"[live] required {s['required_pass']}/{s['required_total']} PASS —— gate={'PASS' if s['gate_pass'] else 'FAIL'}")
        if not s["gate_pass"]:
            if args.allow_pending:
                print("[live] 未通过项记为『待服务侧承接修复』（--allow-pending），不影响退出码")
            else:
                rc = 1

    if args.json:
        path = args.json if os.path.isabs(args.json) else os.path.join(ROOT, args.json)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"[json] 证据已写入 {path}")

    return rc


if __name__ == "__main__":
    sys.exit(main())