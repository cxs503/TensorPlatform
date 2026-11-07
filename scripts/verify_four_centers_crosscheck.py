#!/usr/bin/env python3
"""四中心载荷 × 生产库 SQL 对拍（PR-10 首期验收锚点）。

做法（不碰线上服务、不重启 8010）：
1. 用**本仓代码**的 ``api.tensor_centers`` 路由组一个最小 FastAPI app；
2. ``get_db`` 覆盖为**生产库**（只读查询）会话，``require_auth`` 覆盖为 admin；
3. 逐中心取载荷，然后用本文件里**独立手写**的 SQL（不是载荷自带的 source）
   把每一项指标重算一遍，逐项比对；
4. 结果落 ``artifacts/m5023/center_sql_crosscheck.json``（含逐项 endpoint_value /
   sql_value / match），任何一项不等则退出码 1——honest FAIL 不许粉饰。

用法：
    python scripts/verify_four_centers_crosscheck.py \
        --database-url postgresql+psycopg://user:pass@host:5432/db \
        --out artifacts/m5023/center_sql_crosscheck.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

# 独立手写 SQL（对拍基准）：key -> SQL 或 (分子 SQL, 分母 SQL, 小数位)
CHECKS: dict[str, dict[str, object]] = {
    "organization": {
        "missions_total": "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = :org",
        "missions_succeeded": "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = :org AND status = 'succeeded'",
        "missions_failed": "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = :org AND status = 'failed'",
        "missions_running": "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = :org AND status = 'running'",
        "sessions_total": "SELECT count(*) FROM tensor_agent_sessions WHERE organization_id = :org",
        "sessions_active": "SELECT count(*) FROM tensor_agent_sessions WHERE organization_id = :org AND status IN ('active','claimed','running','in_progress')",
        "executors_registered": "SELECT count(*) FROM tensor_executors",
        "approvals_pending": "SELECT count(*) FROM tensor_approval_requests WHERE organization_id = :org AND status = 'pending'",
        "campaign_plans_total": "SELECT count(*) FROM campaign_plans WHERE organization_id = :org",
        "campaign_plans_active": "SELECT count(*) FROM campaign_plans WHERE organization_id = :org AND status = 'active'",
        "campaign_nodes_total": "SELECT count(*) FROM campaign_nodes n JOIN campaign_plans p ON p.id = n.plan_id WHERE p.organization_id = :org",
        "campaign_nodes_in_flight": "SELECT count(*) FROM campaign_nodes n JOIN campaign_plans p ON p.id = n.plan_id WHERE p.organization_id = :org AND n.status = 'in-flight'",
        "campaign_nodes_blocked": "SELECT count(*) FROM campaign_nodes n JOIN campaign_plans p ON p.id = n.plan_id WHERE p.organization_id = :org AND n.status = 'blocked'",
        "campaign_nodes_done": "SELECT count(*) FROM campaign_nodes n JOIN campaign_plans p ON p.id = n.plan_id WHERE p.organization_id = :org AND n.status = 'done'",
        "campaign_milestones_total": "SELECT count(*) FROM campaign_milestones m JOIN campaign_plans p ON p.id = m.plan_id WHERE p.organization_id = :org",
        "campaign_milestones_done": "SELECT count(*) FROM campaign_milestones m JOIN campaign_plans p ON p.id = m.plan_id WHERE p.organization_id = :org AND m.status = 'done'",
    },
    "quality": {
        "reports_total": "SELECT count(*) FROM tensor_reports WHERE organization_id = :org",
        "reports_qa_pending": "SELECT count(*) FROM tensor_reports WHERE organization_id = :org AND qa_status = 'pending'",
        "reports_qa_passed": "SELECT count(*) FROM tensor_reports WHERE organization_id = :org AND qa_status = 'passed'",
        "reports_qa_rejected": "SELECT count(*) FROM tensor_reports WHERE organization_id = :org AND qa_status = 'rejected'",
        "reports_qa_undecided": "SELECT count(*) FROM tensor_reports WHERE organization_id = :org AND qa_status IS NULL",
        "reports_qa_pass_rate": (
            "SELECT count(*) FROM tensor_reports WHERE organization_id = :org AND qa_status = 'passed'",
            "SELECT count(*) FROM tensor_reports WHERE organization_id = :org AND qa_status IN ('passed','rejected')",
            4,
        ),
        "missions_report_owed": "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = :org AND report_owed",
        "missions_report_settled": "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = :org AND NOT report_owed",
        "campaign_milestones_total": "SELECT count(*) FROM campaign_milestones m JOIN campaign_plans p ON p.id = m.plan_id WHERE p.organization_id = :org",
        "campaign_milestones_done": "SELECT count(*) FROM campaign_milestones m JOIN campaign_plans p ON p.id = m.plan_id WHERE p.organization_id = :org AND m.status = 'done'",
        "quality_rule_checks": "SELECT count(*) FROM quality_check_results",
        "repro_verdicts": "SELECT count(*) FROM tensor_repro_verdicts WHERE organization_id = :org",
    },
    "capability": {
        "deltas_total": "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org",
        "substantive_deltas": "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org AND category <> 'no_substantive_delta'",
        "deltas_new_capability": "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org AND category = 'new_capability'",
        "deltas_capability_extension": "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org AND category = 'capability_extension'",
        "deltas_defect_fix": "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org AND category = 'defect_fix'",
        "deltas_reusable_knowledge": "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org AND category = 'reusable_knowledge'",
        "deltas_falsified_hypothesis": "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org AND category = 'falsified_hypothesis'",
        "deltas_no_substantive_delta": "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org AND category = 'no_substantive_delta'",
        "succeeded_missions": "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = :org AND status = 'succeeded'",
        "covered_succeeded": "SELECT count(DISTINCT d.mission_id) FROM tensor_capability_deltas d JOIN tensor_dev_missions m ON m.id = d.mission_id WHERE d.organization_id = :org AND m.status = 'succeeded'",
        "coverage_ratio": (
            "SELECT count(DISTINCT d.mission_id) FROM tensor_capability_deltas d JOIN tensor_dev_missions m ON m.id = d.mission_id WHERE d.organization_id = :org AND m.status = 'succeeded'",
            "SELECT count(*) FROM tensor_dev_missions WHERE organization_id = :org AND status = 'succeeded'",
            4,
        ),
    },
    "innovation": {
        "knowledge_deltas": "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org AND category IN ('new_capability','capability_extension','reusable_knowledge')",
        "capability_deltas_total": "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org",
        "absorption_rate": (
            "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org AND category IN ('new_capability','capability_extension','reusable_knowledge')",
            "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org",
            4,
        ),
        "falsified_hypothesis_deltas": "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org AND category = 'falsified_hypothesis'",
        "defect_fix_deltas": "SELECT count(*) FROM tensor_capability_deltas WHERE organization_id = :org AND category = 'defect_fix'",
    },
}


def _mask(url: str) -> str:
    """只留方言+库名，凭据与主机一律抹掉（对拍记录要能入库入报，不能带凭据）。"""
    import re
    host_db = url.rsplit("@", 1)[-1] if "@" in url else url
    host_db = re.sub(r"^[^/]+/", "", host_db)
    scheme = url.split("://", 1)[0]
    return f"{scheme}://***/{host_db}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", required=True)
    ap.add_argument("--out", default=str(BACKEND.parent / "artifacts" / "m5023" / "center_sql_crosscheck.json"))
    args = ap.parse_args()

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from api.deps import require_auth
    from api.tensor_centers import router as centers_router
    from database.config import get_db

    engine = create_engine(args.database_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, future=True)

    with engine.connect() as conn:
        org = conn.execute(text("SELECT MIN(id) FROM organizations")).scalar()
        if org is None:
            print("FAIL: organizations 表为空，无法确定 org 上下文", file=sys.stderr)
            return 1

    app = FastAPI()
    app.include_router(centers_router)
    app.dependency_overrides[require_auth] = lambda: {
        "id": 1, "username": "crosscheck", "role": "ADMIN", "organization_id": int(org)}
    app.dependency_overrides[get_db] = lambda: Session()
    client = TestClient(app)

    def q(sql: str):
        with engine.connect() as conn:
            return conn.execute(text(sql), {"org": int(org)}).scalar()

    items: list[dict] = []
    for center, checks in CHECKS.items():
        resp = client.get(f"/tensor/centers/{center}")
        if resp.status_code != 200:
            print(f"FAIL: /tensor/centers/{center} -> {resp.status_code} {resp.text[:200]}", file=sys.stderr)
            return 1
        payload = resp.json()
        metrics = {m["key"]: m for m in payload["metrics"]}
        for key, spec in checks.items():
            metric = metrics.get(key)
            if metric is None:
                items.append({"center": center, "key": key, "status": "missing_in_payload",
                              "match": False})
                continue
            if isinstance(spec, tuple):
                num_sql, den_sql, digits = spec
                num, den = q(num_sql), q(den_sql)
                expected = round(num / den, digits) if den else None
            else:
                expected = q(spec)
            items.append({
                "center": center,
                "key": key,
                "endpoint_value": metric["value"],
                "sql_value": expected,
                "source": metric.get("source"),
                "match": metric["value"] == expected,
            })

    matched = sum(1 for i in items if i.get("match"))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": _mask(args.database_url),
        "org_id": int(org),
        "centers": list(CHECKS),
        "items_total": len(items),
        "items_matched": matched,
        "all_match": matched == len(items),
        "criteria": {"required_min_items": 6, "satisfied": len(items) >= 6 and matched == len(items)},
        "items": items,
    }
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"对拍项 {matched}/{len(items)} 一致 → {out}")
    return 0 if report["all_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())