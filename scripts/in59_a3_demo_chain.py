#!/usr/bin/env python3
"""IN59-A3 五元证据链 —— PostgreSQL 端到端演练（单事务，演练后回滚）。

做三件事，全部走**产品代码路径**（``tools.outcome_evaluations.decide`` +
``tools.evidence_chain_link``），不是 README 里的手写 SQL：

1. 完整五元链的 S3 声明 → 期望 ``confirmed`` / E3；
2. 红队对照：同一声明缺 ``data``（原始计算结果）→ 期望 ``candidate`` / E1，
   命中 ``CHAIN_RAW_DATA_MISSING``，链行 ``capped=true``；
3. 结算面守卫 ``settlement_guard_sql()``：完整链在放行集内，缺件行被
   ``outcome_eval_chain_audit.chain_blocked`` 拦下（只有 ``confirmed`` 行会被拦）。

演练写在 PG 事务里并**回滚**：平台分布不被演示数据污染（脚本末尾自检行数归零）。

用法（PG 本地 socket，需能读 tensorplatform）：

    runuser -u postgres -- python3 scripts/in59_a3_demo_chain.py \
        --dsn "postgresql+psycopg:///tensorplatform?host=/var/run/postgresql" \
        --code-sha "$(git rev-parse HEAD)" \
        --config-hash "sha256:<hex>" --data-ref artifacts/in59_a3/chain_demo_data.json \
        --report-id <report_id> --out artifacts/in59_a3/pg_demo.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from sqlalchemy import create_engine, text  # noqa: E402

from tools import evidence_chain_link as ecl  # noqa: E402
from tools import outcome_evaluations as oe  # noqa: E402

REASON = (
    "合成演练理由：结论的五元引用逐个可机器解析，代码版本取自 git commit，"
    "配置摘要由原文件复算，原始结果与图锚均在工作树内可复现，适用边界为平台治理面。"
)
REVIEWER = "in59-a3-redteam"
CONTRIBUTOR = "agent/m6330"


def _payload(artifact: str, mission: int, chain: dict | None, grade="S3", e_level="E3") -> dict:
    return {
        "artifact_id": artifact,
        "mission_id": mission,
        "contributor": CONTRIBUTOR,
        "grade": grade,
        "e_level": e_level,
        "grade_reason": REASON,
        "evidence_refs": [{"kind": "report", "ref": str(chain.get("report") if chain else "1")}],
        "reviewer": REVIEWER,
        "reviewer_kind": "independent",
        "baseline_or_scope": "基线：无链封顶前的 §7.1 判据（E 级不设上限）",
        "boundary": "仅平台治理面（结论可追溯性），不外推到领域数值结论",
        "repo": "TensorPlatform",
        "organization_id": 1,
        "evidence_chain": chain,
    }


def run(dsn: str, *, code_sha: str, config_hash: str, data_ref: str, report_id: int,
        mission: int) -> dict:
    engine = create_engine(dsn)
    out: dict = {"dsn": dsn.split("?")[0], "mission_id": mission,
                 "code_sha": code_sha, "config_hash": config_hash,
                 "data_ref": data_ref, "report_id": report_id}
    conn = engine.connect()
    tx = conn.begin()
    try:
        full = {
            "conclusion_ref": "in59-a3:evidence-chain-gate",
            "theory": "derivation:#evidence-chain-five-elements (AOG SPEC v1.2 §7.1 扩展)",
            "code": f"TensorPlatform@{code_sha}",
            "config": config_hash,
            "data": data_ref,
            "report": str(report_id),
        }
        redteam = {k: v for k, v in full.items() if k != "data"}

        rows = {}
        for tag, artifact, chain in (
            ("complete", "in59-a3:demo:complete", full),
            ("redteam_missing_data", "in59-a3:demo:redteam", redteam),
        ):
            payload = _payload(artifact, mission, chain)
            verdict = oe.decide(payload)
            record = oe.record_from_verdict(payload, verdict, reason_source="manual")
            record["organization_id"] = 1
            oe.store_record(conn, record, commit=False)
            report = ecl.analyze_chain(chain, verdict.grade)
            chain_row = ecl.chain_record(payload, report, evaluation_id=record["evaluation_id"],
                                         e_level_requested=payload["e_level"], grade=verdict.grade)
            ecl.store_chain(conn, chain_row, commit=False)
            rows[tag] = {
                "evaluation_id": record["evaluation_id"],
                "status": verdict.status,
                "grade": verdict.grade,
                "e_level_declared": payload["e_level"],
                "e_level_granted": verdict.e_level,
                "codes": list(verdict.codes),
                "chain_status": report.status,
                "completeness": report.completeness,
                "missing_elements": list(report.missing),
                "capped": bool(report.cap),
            }
        out["cases"] = rows

        # 第三例：模拟「链判据上线前已 confirmed 的 S3 行」（无链）——判据不会回溯改行，
        # 结算守卫必须把它拦下（防直接 SQL 写入/历史脏行按申报等级入账）。
        legacy_payload = _payload("in59-a3:demo:legacy", mission, None)
        legacy_verdict = oe.decide(legacy_payload)
        legacy_record = oe.record_from_verdict(legacy_payload, legacy_verdict)
        legacy_record["organization_id"] = 1
        legacy_record["status"] = "confirmed"          # 绕过判据的历史/直写形态
        legacy_record["grade"], legacy_record["e_level"] = "S3", "E3"
        oe.store_record(conn, legacy_record, commit=False)
        rows["legacy_confirmed_no_chain"] = {
            "evaluation_id": legacy_record["evaluation_id"],
            "status": "confirmed",
            "grade": "S3",
            "e_level_declared": "E3",
            "e_level_granted": "E3",
            "codes": [],
            "chain_status": "missing",
            "completeness": 0.0,
            "missing_elements": list(ecl.ELEMENT_KEYS),
            "capped": False,
        }
        demo_ids = {rows[k]["evaluation_id"] for k in
                    ("complete", "redteam_missing_data", "legacy_confirmed_no_chain")}

        guard = conn.execute(text(ecl.settlement_guard_sql())).mappings().all()
        out["settlement_guard_admitted_total"] = len(guard)
        out["settlement_guard_admitted_demo"] = [r["evaluation_id"] for r in guard
                                                 if r["evaluation_id"] in demo_ids]
        audit = conn.execute(text(
            "SELECT evaluation_id, grade, status, chain_status, chain_completeness, chain_blocked "
            "FROM outcome_eval_chain_audit WHERE evaluation_id IN :ids"
        ).bindparams(__import__("sqlalchemy").bindparam("ids", expanding=True)),
            {"ids": sorted(demo_ids)}).mappings().all()
        out["audit_view"] = [dict(r) for r in audit]
        blocked = {r["evaluation_id"] for r in audit if r["chain_blocked"]}
        out["checks"] = {
            "complete_confirmed": rows["complete"]["status"] == "confirmed"
            and rows["complete"]["e_level_granted"] == "E3",
            "redteam_downgraded": rows["redteam_missing_data"]["status"] == "candidate"
            and rows["redteam_missing_data"]["e_level_granted"] == "E1",
            "redteam_code_present": "CHAIN_RAW_DATA_MISSING"
            in rows["redteam_missing_data"]["codes"],
            "guard_admits_complete_only": out["settlement_guard_admitted_demo"]
            == [rows["complete"]["evaluation_id"]],
            "legacy_confirmed_blocked": blocked == {rows["legacy_confirmed_no_chain"]["evaluation_id"]},
        }
        out["checks"]["all_pass"] = all(out["checks"].values())
    finally:
        tx.rollback()
        conn.close()

    # 回滚自检：演示行不得留存
    with engine.connect() as c:
        out["rolled_back_rowcount"] = c.execute(text(
            "SELECT count(*) FROM outcome_evidence_chains WHERE artifact_id LIKE 'in59-a3:demo:%'"
        )).scalar()
        out["total_chain_rows"] = c.execute(
            text("SELECT count(*) FROM outcome_evidence_chains")).scalar()
        out["total_eval_rows"] = c.execute(
            text("SELECT count(*) FROM outcome_evaluations")).scalar()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default="postgresql+psycopg:///tensorplatform?host=/var/run/postgresql")
    ap.add_argument("--code-sha", required=True)
    ap.add_argument("--config-hash", required=True)
    ap.add_argument("--data-ref", required=True)
    ap.add_argument("--report-id", type=int, required=True)
    ap.add_argument("--mission", type=int, default=6330)
    ap.add_argument("--out", default="artifacts/in59_a3/pg_demo.json")
    args = ap.parse_args(argv)
    result = run(args.dsn, code_sha=args.code_sha.strip(), config_hash=args.config_hash.strip(),
                 data_ref=args.data_ref, report_id=args.report_id, mission=args.mission)
    path = pathlib.Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["checks"]["all_pass"] and result["rolled_back_rowcount"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())