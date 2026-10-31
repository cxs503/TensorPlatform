"""E2E-1 修复 · 门缺陷 B1（样本库准入未验证谱系）· B 类负测试 CLI runner。

两类证据一次跑完（数字全部由本脚本产出，报告里零手抄）：

  A. 修复前（legacy）：直接调用 `tools/sample_db/backfill.py::backfill_file()`，
     即 m810 D3 的裸入口 —— 期望 **ADMITTED**（缺陷活体）。
  B. 修复后（gated）：`src.sample_db.ingest.ingest_artifact()` 三门准入 ——
     期望坏产物全部 REJECTED、正对照 ADMITTED，且每次判定都有审计留痕。

用法：
    python3 tests/e2e1_b1/run_negative_suite.py            # 写 outputs/e2e1_b1/
    python3 tests/e2e1_b1/run_negative_suite.py --root /tmp/x
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(TESTS_DIR))

from build_cases import build_all  # noqa: E402
from src.sample_db.admission import AdmissionRejected, StaticMissionResolver  # noqa: E402
from src.sample_db.ingest import ingest_artifact, init_db, load_backfill, row_count  # noqa: E402

OUT_DIR = REPO_ROOT / "outputs" / "e2e1_b1"


def _read_audit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_legacy(case: dict, workdir: Path) -> dict:
    """修复前路径：裸 backfill_file()（无 manifest / 无哈希 / 无 mission 门）。"""
    backfill = load_backfill()
    conn = sqlite3.connect(workdir / "legacy.sqlite")
    try:
        conn.executescript(backfill.SCHEMA_SQL)
        try:
            inserted = backfill.backfill_file(conn, str(case["artifact"]))
            conn.commit()
            rows = row_count(conn)
            return {
                "path": "legacy:tools/sample_db/backfill.py::backfill_file",
                "verdict": "ADMITTED" if rows else "REJECTED",
                "rows_inserted": rows,
                "inserted": inserted,
            }
        except Exception as exc:  # noqa: BLE001 - 显式拒绝路径
            return {
                "path": "legacy:tools/sample_db/backfill.py::backfill_file",
                "verdict": "REJECTED",
                "rows_inserted": 0,
                "errors": [f"E_INGEST: {type(exc).__name__}: {exc}"],
            }
    finally:
        conn.close()


def run_gated(case: dict, workdir: Path, audit_log: Path) -> dict:
    """修复后路径：三门准入 + 抽取入库。"""
    conn = sqlite3.connect(workdir / "gated.sqlite")
    try:
        init_db(conn)
        resolver = StaticMissionResolver(case["mission_status"], source="runner-static")
        try:
            decision, inserted = ingest_artifact(
                conn,
                case["artifact"],
                mission_resolver=resolver,
                audit_log=audit_log,
                manifest_path=case["manifest"],
                run_dir=case["run_dir"],
            )
            return {
                "path": "gated:src/sample_db/ingest.py::ingest_artifact",
                "verdict": "ADMITTED",
                "code": None,
                "rows_inserted": row_count(conn),
                "inserted": inserted,
                "gates": {g.gate: g.status for g in decision.gates},
                "verified_outputs": decision.verified_outputs,
            }
        except AdmissionRejected as exc:
            payload = exc.to_dict()
            return {
                "path": "gated:src/sample_db/ingest.py::ingest_artifact",
                "verdict": "REJECTED",
                "code": payload["code"],
                "message": payload["message"],
                "rows_inserted": row_count(conn),
                "gates": {g["gate"]: g["status"] for g in payload["gates"]},
                "audit_written": payload["audit_written"],
            }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path("/tmp") / "e2e1_b1_cases"))
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_log = out_dir / "admission_audit.jsonl"
    if audit_log.exists():
        audit_log.unlink()

    cases = build_all(root)
    results = []
    for case in cases:
        workdir = root / case["name"]
        workdir.mkdir(parents=True, exist_ok=True)
        legacy = run_legacy(case, workdir)
        gated = run_gated(case, workdir, audit_log)
        for leftover in workdir.glob("*.sqlite"):
            leftover.unlink()  # 证据只留 JSON/产物，不把临时库带进仓库
        expect = case["expect"]
        verdict_ok = gated["verdict"] == expect and (
            expect == "ADMITTED" or gated.get("code") == case["expect_code"]
        )
        results.append(
            {
                "name": case["name"],
                "attack": case["attack"],
                "gate": case["gate"],
                "expect": expect,
                "expect_code": case["expect_code"],
                "legacy": legacy,
                "gated": gated,
                "judgement": "PASS" if verdict_ok else "FAIL",
                "guard_triggered": legacy["verdict"] == "ADMITTED" and gated["verdict"] == "REJECTED",
            }
        )

    audit_rows = _read_audit(audit_log)
    rejected = [r for r in results if r["expect"] == "REJECTED"]
    admitted_ctrl = [r for r in results if r["expect"] == "ADMITTED"]
    summary = {
        "cases": len(results),
        "rejected_expected": len(rejected),
        "rejected_actual": sum(1 for r in results if r["gated"]["verdict"] == "REJECTED"),
        "admitted_expected": len(admitted_ctrl),
        "admitted_actual": sum(1 for r in results if r["gated"]["verdict"] == "ADMITTED"),
        "legacy_admitted_bad_products": sum(1 for r in rejected if r["legacy"]["verdict"] == "ADMITTED"),
        "gate_defect_live_before_fix": sum(1 for r in rejected if r["legacy"]["verdict"] == "ADMITTED") > 0,
        "audit_records": len(audit_rows),
        "audit_records_expected": len(results),
        "failures": [r["name"] for r in results if r["judgement"] == "FAIL"],
        "verdict": "PASS" if all(r["judgement"] == "PASS" for r in results) else "FAIL",
    }
    evidence = {
        "mission_id": 1085,
        "defect": "B1 / m810-D3 样本库入库无 RunManifest / 哈希 / mission 终态门",
        "gates": ["manifest", "mission", "checksum"],
        "audit_log": str(audit_log),
        "summary": summary,
        "results": results,
        "audit_records": audit_rows,
    }
    evidence_path = out_dir / "negative_evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())