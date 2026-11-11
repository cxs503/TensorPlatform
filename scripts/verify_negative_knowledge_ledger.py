#!/usr/bin/env python3
"""否定性知识台账验证脚本。

验证判据：
1. SELECT count(*) FROM negative_knowledge_ledger ≥ 30
2. 每条有证据指针（falsification_evidence 非空）
3. failure_kind 在允许值内
4. status 在允许值内
5. confidence 在 [0,1] 内

用法：
    python3 scripts/verify_negative_knowledge_ledger.py \
        --input artifacts/negative_knowledge_ledger/negative_knowledge_ledger.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

VALID_FAILURE_KINDS = {"hypothesis_falsified", "method_failed", "blocked", "infeasible", "regression"}
VALID_STATUSES = {"active", "superseded", "retracted"}
MIN_ENTRIES = 30


def verify(input_path: str) -> dict:
    """验证台账文件。"""
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    entries = data.get("entries", [])
    report = {
        "total_entries": len(entries),
        "meets_minimum": len(entries) >= MIN_ENTRIES,
        "all_have_evidence": True,
        "all_valid_failure_kind": True,
        "all_valid_status": True,
        "all_valid_confidence": True,
        "all_have_source_artifacts": True,
        "all_have_keywords": True,
        "all_have_differentiation": True,
        "issues": [],
        "by_failure_kind": {},
        "by_status": {},
    }

    for i, entry in enumerate(entries):
        eid = entry.get("entry_id", f"index_{i}")

        # 证据指针
        if not entry.get("falsification_evidence"):
            report["all_have_evidence"] = False
            report["issues"].append(f"{eid}: 缺少 falsification_evidence")

        # failure_kind
        fk = entry.get("failure_kind")
        if fk not in VALID_FAILURE_KINDS:
            report["all_valid_failure_kind"] = False
            report["issues"].append(f"{eid}: 非法 failure_kind={fk}")
        report["by_failure_kind"][fk] = report["by_failure_kind"].get(fk, 0) + 1

        # status
        st = entry.get("status", "active")
        if st not in VALID_STATUSES:
            report["all_valid_status"] = False
            report["issues"].append(f"{eid}: 非法 status={st}")
        report["by_status"][st] = report["by_status"].get(st, 0) + 1

        # confidence
        conf = entry.get("confidence", 0)
        if not (0 <= conf <= 1):
            report["all_valid_confidence"] = False
            report["issues"].append(f"{eid}: confidence 超出 [0,1] 范围: {conf}")

        # source_artifacts
        if not entry.get("source_artifacts"):
            report["all_have_source_artifacts"] = False
            report["issues"].append(f"{eid}: 缺少 source_artifacts")

        # keywords
        if not entry.get("keywords"):
            report["all_have_keywords"] = False
            report["issues"].append(f"{eid}: 缺少 keywords")

        # differentiation_required
        if not entry.get("differentiation_required"):
            report["all_have_differentiation"] = False
            report["issues"].append(f"{eid}: 缺少 differentiation_required")

    # 总体判定
    report["passed"] = (
        report["meets_minimum"]
        and report["all_have_evidence"]
        and report["all_valid_failure_kind"]
        and report["all_valid_status"]
        and report["all_valid_confidence"]
        and report["all_have_source_artifacts"]
    )

    return report


def main():
    parser = argparse.ArgumentParser(description="验证否定性知识台账")
    parser.add_argument("--input", required=True, help="JSON 台账文件路径")
    args = parser.parse_args()

    report = verify(args.input)
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if report["passed"]:
        print(f"\n✅ 验证通过：{report['total_entries']} 条条目，全部有证据指针")
        print(f"   失败类型分布: {report['by_failure_kind']}")
        print(f"   状态分布: {report['by_status']}")
    else:
        print(f"\n❌ 验证失败：{len(report['issues'])} 个问题", file=sys.stderr)
        for issue in report["issues"]:
            print(f"   - {issue}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
