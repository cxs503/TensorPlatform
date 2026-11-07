#!/usr/bin/env python3
"""IN59-A3 五元证据链判据矩阵测量（原始结果文件生成器）。

对 (S 级 × 申报 E × 链形态) 全矩阵跑 ``tools.outcome_evaluations.decide``，
把每格的状态/E 级/命中码写成 ``artifacts/in59_a3/results.json``。图与报告只读
这份文件（数字零手抄）。

链形态：
  complete       五元齐全
  miss_data      缺 data（原始计算结果）—— 红队主控
  miss_theory    缺 theory（文献依据）
  bad_code       code 形如 "main"（无 commit sha）
  absent         未给链

用法： python3 scripts/in59_a3_measure.py --out artifacts/in59_a3/results.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools import evidence_chain_link as ecl  # noqa: E402
from tools import outcome_evaluations as oe  # noqa: E402

REASON = (
    "测量夹具理由：五元引用逐个可机器解析，代码版本取自 commit，配置摘要由文件复算，"
    "原始结果与报告锚均可在工作树内定位，适用边界为平台治理面。"
)

FULL = {
    "conclusion_ref": "claim-in59-a3",
    "theory": "doi:10.5555/in59.a3",
    "code": "TensorPlatform@" + "0" * 7,
    "config": "sha256:" + "a" * 64,
    "data": "artifacts/in59_a3/results.json",
    "report": "6330",
}
VARIANTS = {
    "complete": dict(FULL),
    "miss_data": {k: v for k, v in FULL.items() if k != "data"},
    "miss_theory": {k: v for k, v in FULL.items() if k != "theory"},
    "bad_code": {**FULL, "code": "main"},
    "absent": None,
}
GRADES = ("S1", "S2", "S3", "S4", "S5", "S-N")
E_LEVELS = ("E1", "E2", "E3", "E4")


def payload(grade: str, e_level: str, chain) -> dict:
    return {
        "artifact_id": f"in59-a3:{grade}:{e_level}",
        "mission_id": 6330,
        "contributor": "agent/m6330",
        "grade": grade,
        "e_level": e_level,
        "grade_reason": REASON,
        "evidence_refs": [{"kind": "report", "ref": "6330"}],
        "reviewer": "in59-a3-redteam",
        "reviewer_kind": "independent",
        "baseline_or_scope": "基线：无链封顶前的 §7.1 判据",
        "boundary": "仅平台治理面",
        "evidence_chain": chain,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/in59_a3/results.json")
    ap.add_argument("--config", default="artifacts/in59_a3/run_config.json")
    args = ap.parse_args(argv)

    rows = []
    for grade in GRADES:
        for variant, chain in VARIANTS.items():
            report = ecl.analyze_chain(chain, grade)
            for e_level in E_LEVELS:
                verdict = oe.decide(payload(grade, e_level, chain))
                rows.append({
                    "grade": grade,
                    "e_level_declared": e_level,
                    "chain_variant": variant,
                    "chain_status": report.status,
                    "completeness": report.completeness,
                    "e_level_granted": verdict.e_level,
                    "status": verdict.status,
                    "counted": verdict.counted,
                    "codes": list(verdict.codes),
                })

    summary = {
        "rows": len(rows),
        "confirmed": sum(1 for r in rows if r["status"] == "confirmed"),
        "candidate": sum(1 for r in rows if r["status"] == "candidate"),
        "rejected": sum(1 for r in rows if r["status"] == "rejected"),
        "capped_rows": sum(1 for r in rows if any(c.startswith("CHAIN_CAPS_E_TO_") for c in r["codes"])),
        "declared_above_granted": sum(
            1 for r in rows
            if ecl.E_ORDER[r["e_level_granted"] or "E0"] < ecl.E_ORDER[r["e_level_declared"]]
        ),
        "granted_above_declared": sum(
            1 for r in rows
            if ecl.E_ORDER[r["e_level_granted"] or "E0"] > ecl.E_ORDER[r["e_level_declared"]]
        ),
    }
    # 红队关键判据：S3 缺 data → 必须 candidate/E1 且带 CHAIN_RAW_DATA_MISSING
    redteam = [r for r in rows if r["grade"] == "S3" and r["chain_variant"] == "miss_data"]
    summary["s3_missing_data_all_downgraded"] = bool(redteam) and all(
        r["status"] == "candidate" and r["e_level_granted"] == "E1"
        and "CHAIN_RAW_DATA_MISSING" in r["codes"] for r in redteam
    )
    # 完整链不被无故压级
    complete = [r for r in rows if r["chain_variant"] == "complete"]
    summary["complete_chain_grade_preserved"] = all(
        r["e_level_granted"] == r["e_level_declared"] for r in complete)

    cfg_path = pathlib.Path(args.config)
    config_hash = None
    if cfg_path.exists():
        config_hash = "sha256:" + hashlib.sha256(cfg_path.read_bytes()).hexdigest()

    out = {
        "mission_id": 6330,
        "node": "IN59-A3",
        "config_path": args.config,
        "config_hash": config_hash,
        "elements": [k for k, _ in ecl.ELEMENTS],
        "variants": list(VARIANTS),
        "grades": list(GRADES),
        "e_levels": list(E_LEVELS),
        "summary": summary,
        "matrix": rows,
    }
    path = pathlib.Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["s3_missing_data_all_downgraded"] and summary["complete_chain_grade_preserved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())