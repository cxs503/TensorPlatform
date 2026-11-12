#!/usr/bin/env python3
"""由结构化输出生成证据表（JSON + Markdown），数字零手抄。

前置：先跑 CLI 生成 ``artifacts/audit_findings_tracker/out/*.findings.json``（见 README）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "audit_findings_tracker"
OUT_DIR = SAMPLE_DIR / "out"


def _rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(OUT_DIR.glob("*.findings.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows.append({
            "report": path.name.replace(".findings.json", ".json"),
            "layout_mode": doc["layout"]["mode"],
            "finding_pointers": doc["layout"]["finding_pointers"],
            "status": doc["status"],
            "exit_code": doc["exit_code"],
            "total": doc["counts"]["total"],
            "counts": {k: v for k, v in doc["counts"].items() if k != "total" and v},
            "recognized": doc["summary"]["recognized"],
            "withheld": doc["summary"]["withheld"],
            "derived": doc["summary"]["derived"],
            "report_sha256": doc["inputs"]["report"]["sha256"],
            "schema_sha256": doc["inputs"]["schema"]["sha256"],
            "reasons": [r["code"] for r in doc["reasons"]],
        })
    return rows


def render() -> Dict[str, Any]:
    rows = _rows()
    md = ["| report | layout | pointers | status | rc | total | counts | sha256(report) |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(r["counts"].items())) or "-"
        md.append(f"| {r['report']} | {r['layout_mode']} | {', '.join(r['finding_pointers'])} | "
                  f"{r['status']} | {r['exit_code']} | {r['total']} | {counts} | "
                  f"`{r['report_sha256'][:12]}…` |")
    return {"schema": "audit-findings-evidence/1.0", "rows": rows, "markdown_table": "\n".join(md)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="build evidence table from structured findings output")
    p.add_argument("--json", default=str(SAMPLE_DIR / "evidence_table.json"))
    p.add_argument("--md", default=str(SAMPLE_DIR / "evidence_table.md"))
    args = p.parse_args(argv)
    doc = render()
    Path(args.json).write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    Path(args.md).write_text(doc["markdown_table"] + "\n", encoding="utf-8")
    print(args.json, args.md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())