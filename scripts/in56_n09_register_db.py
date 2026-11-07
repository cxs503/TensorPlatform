"""把 IN56-N09 的预注册与判据数字「落库」（governance_audit，只 INSERT 数据，不改表结构）。

纪律：库里不写凭据；DSN 从环境变量 TENSOR_DSN 读。重复执行用 (action, prereg_sha256)
去重，保证幂等。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts/IN56_N09"
ACTION = "IN56-N09_grader_robustness_rct"


def main() -> int:
    dsn = os.environ.get("TENSOR_DSN", "").replace("postgresql+psycopg://", "postgresql://")
    if not dsn:
        print("need TENSOR_DSN", file=sys.stderr)
        return 2
    dsn = dsn.split("?")[0]
    a = json.loads((ART / "analysis.json").read_text(encoding="utf-8"))
    pre = json.loads((ART / "preregistration.json").read_text(encoding="utf-8"))
    rep = json.loads((ART / "report.json").read_text(encoding="utf-8"))
    details = {
        "node": "IN56-N09",
        "repo": "TensorPlatform",
        "seed": pre["seed"],
        "reps": pre["reps"],
        "n_bases": pre["n_bases"],
        "n_gradings": pre["n_gradings"],
        "assignment_sha256": pre["assignment_sha256"],
        "prereg_sha256": pre["prereg_sha256"],
        "base_corpus_sha256": pre["base_corpus_sha256"],
        "interventions_sha256": pre["interventions_sha256"],
        "replay_consistency": a["replay"]["consistency_rate"],
        "mde": {"mde_dz": a["mde"]["mde_dz"], "mde_flip_rate": a["mde"]["mde_flip_rate"],
                "n_pairs": a["mde"]["n_pairs"], "alpha": a["mde"]["alpha"],
                "power": a["mde"]["power"]},
        "arms": {
            arm: {
                "flip_rate": a["arms"][arm]["flip"]["flip_rate"],
                "flip_ci95_exact": a["arms"][arm]["flip_ci95_exact"],
                "cohen_d": a["arms"][arm]["cohen_d"],
                "cohen_d_ci95": a["arms"][arm]["cohen_d_ci95"],
                "dz": a["arms"][arm]["dz"],
                "verdict": a["verdicts"]["arms"][arm]["verdict"],
            }
            for arm in ("T1", "T2", "T3")
        },
        "structural_findings": a["structural_findings"],
        "criteria_all_pass": rep["criteria"]["all_pass"],
        "artifacts": [rep["docs"], "artifacts/IN56_N09/analysis.json",
                      "artifacts/IN56_N09/preregistration.json",
                      "artifacts/IN56_N09/report.json"],
        "assignment_rows": len(pre["assignment"]),
    }
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM governance_audit WHERE action=%s AND details->>'prereg_sha256'=%s",
                (ACTION, pre["prereg_sha256"]),
            )
            row = cur.fetchone()
            if row:
                print(f"already registered id={row[0]}")
                return 0
            cur.execute(
                "INSERT INTO governance_audit (action, user_name, details) VALUES (%s, %s, %s) "
                "RETURNING id",
                (ACTION, os.environ.get("TENSOR_AGENT_USER", "agent"), json.dumps(
                    details, ensure_ascii=False)),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
    print(f"registered governance_audit id={new_id} prereg_sha256={pre['prereg_sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())