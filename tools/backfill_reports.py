#!/usr/bin/env python3
"""KPI 可溯性回填·第 14 班：批量生成明账欠报告并回填 report_id。

对 m15177 报告识别的明账欠报告（report_owed=true 且无 report_id 也无 tensor_reports 行）
进行批量报告生成。

用法：
    python3 tools/backfill_reports.py [--dry-run] [--limit N]

验收判据：
    C1 明账欠报告 COUNT(*) = 0
    C2 生成的报告数 >= 100
    C3 每条报告含 mission_id 标签且可回溯
    C4 pytest 验证脚本全绿
    C5 零手抄：所有数字由 SQL 查询程序化注入
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

API_BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
TENSOR_USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
TENSOR_PASS = os.environ.get("TENSOR_AGENT_PASS", "")

RESULTS_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "kpi_backfill_m16984"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def login() -> str:
    login_data = json.dumps({"username": TENSOR_USER, "password": TENSOR_PASS}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/auth/login", data=login_data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=10)
    token = json.loads(resp.read())["access_token"]
    logger.info("Login OK, token=%s...", token[:20])
    return token


def fetch_report_owed_queue(token: str, limit: int = 500) -> list[dict]:
    req = urllib.request.Request(
        f"{API_BASE}/tensor/missions/report-owed/queue?limit={limit}",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read())
    items = data.get("items", [])
    logger.info("Fetched %d report-owed missions (total reported: %d)", len(items), data.get("total", "?"))
    return items


def fetch_mission_detail(token: str, mission_id: int) -> dict | None:
    try:
        req = urllib.request.Request(
            f"{API_BASE}/tensor/missions/{mission_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.warning("Failed to fetch mission %d: %s", mission_id, e)
        return None


def check_report_exists(token: str, mission_id: int) -> bool:
    try:
        req = urllib.request.Request(
            f"{API_BASE}/tensor/reports?mission_id={mission_id}&limit=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        return len(data.get("items", [])) > 0
    except urllib.error.HTTPError:
        return False


def generate_report_payload(mission: dict, detail: dict | None) -> dict:
    mid = mission["mission_id"]
    goal = (mission.get("goal") or "")[:180]
    repo = mission.get("repo") or "unknown"
    headline = mission.get("headline") or ""
    finished_at = mission.get("finished_at") or ""
    days = mission.get("days_without_report", 0)

    evidence_count = 0
    status = "succeeded"
    if detail:
        result = detail.get("result", {})
        if isinstance(result, dict):
            evidence_count = len(result.get("evidence_items", [])) or 0
            status = detail.get("status", "succeeded")

    title = f"KPI 可溯性回填：M{mid} ({repo})"
    if headline:
        title = f"KPI 可溯性回填：M{mid} — {headline[:140]}"
    if len(title) > 200:
        title = title[:197] + "..."

    summary = (
        f"明账欠报告自动回填。Mission {mid}，仓库 {repo}，"
        f"完结于 {finished_at[:10] if finished_at else 'N/A'}，"
        f"欠账 {days} 天。"
    )
    if headline:
        summary += f" 任务摘要：{headline[:200]}。"
    if len(summary) > 500:
        summary = summary[:497] + "..."

    markdown = f"""# KPI 可溯性回填报告 — Mission {mid}

## 案例介绍
- **Mission ID**: {mid}
- **仓库**: {repo}
- **状态**: {status}
- **完结时间**: {finished_at or 'N/A'}
- **欠账天数**: {days}
- **证据工件数**: {evidence_count}

## 任务目标
{goal or '（无详细描述）'}

## 回填说明
本报告由 KPI 可溯性回填程序（m16984）自动生成，用于消除明账欠报告债务。
所有数据由数据库查询程序化注入，零手抄。

## 结果
- 报告生成时间: {datetime.now(timezone.utc).isoformat()}
- 回填批次: m16984-batch-14
- 证据工件: {evidence_count} 项
"""

    return {
        "title": title,
        "summary": summary,
        "scenario_key": "kpi-backfill-m16984",
        "mission_id": mid,
        "tags": ["kpi-backfill", "m16984", f"mission-{mid}", repo, "auto-generated"],
        "markdown": markdown,
        "figures": [],
    }


def post_report(token: str, payload: dict) -> dict | None:
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        f"{API_BASE}/tensor/reports",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode()[:500]
        except Exception:
            pass
        logger.error("POST report failed for mission %s: %s — %s",
                     payload.get("mission_id"), e, err_body)
        return None


def link_report(token: str, mission_id: int, report_id: int) -> bool:
    body = json.dumps({"report_id": report_id}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/tensor/missions/{mission_id}/link-report",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        return result.get("report_owed") is False
    except urllib.error.HTTPError as e:
        logger.error("Link report failed for mission %d: %s", mission_id, e)
        return False


def run_backfill(dry_run: bool = False, limit: int = 500, batch_size: int = 50) -> dict:
    token = login()
    missions = fetch_report_owed_queue(token, limit=limit)

    results = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "total_missions": len(missions),
        "reports_created": 0,
        "reports_linked": 0,
        "skipped_already_has_report": 0,
        "failed": 0,
        "details": [],
    }

    for i, mission in enumerate(missions):
        mid = mission["mission_id"]

        if check_report_exists(token, mid):
            logger.info("[%d/%d] M%d: already has report, skipping", i + 1, len(missions), mid)
            results["skipped_already_has_report"] += 1
            results["details"].append({"mission_id": mid, "action": "skipped", "reason": "already_has_report"})
            continue

        detail = fetch_mission_detail(token, mid)
        payload = generate_report_payload(mission, detail)

        if dry_run:
            logger.info("[%d/%d] M%d: DRY RUN — would create report", i + 1, len(missions), mid)
            results["details"].append({"mission_id": mid, "action": "dry_run", "payload_preview": payload["title"]})
            continue

        report_resp = post_report(token, payload)
        if report_resp is None:
            results["failed"] += 1
            results["details"].append({"mission_id": mid, "action": "failed", "reason": "post_failed"})
            continue

        report_id = report_resp.get("id")
        if not report_id:
            results["failed"] += 1
            results["details"].append({"mission_id": mid, "action": "failed", "reason": "no_report_id_in_response"})
            continue

        results["reports_created"] += 1

        linked = link_report(token, mid, report_id)
        if linked:
            results["reports_linked"] += 1
            logger.info("[%d/%d] M%d: report %d created and linked", i + 1, len(missions), mid, report_id)
        else:
            logger.warning("[%d/%d] M%d: report %d created but link failed", i + 1, len(missions), mid, report_id)

        results["details"].append({
            "mission_id": mid,
            "action": "created",
            "report_id": report_id,
            "linked": linked,
        })

        if (i + 1) % batch_size == 0:
            logger.info("Batch %d/%d complete, pausing 1s...", (i + 1) // batch_size, (len(missions) + batch_size - 1) // batch_size)
            time.sleep(1)

    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    return results


def main():
    parser = argparse.ArgumentParser(description="KPI 可溯性回填·第 14 班")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no writes")
    parser.add_argument("--limit", type=int, default=500, help="Max missions to process")
    parser.add_argument("--batch-size", type=int, default=50, help="Pause every N missions")
    args = parser.parse_args()

    results = run_backfill(dry_run=args.dry_run, limit=args.limit, batch_size=args.batch_size)

    out_path = RESULTS_DIR / f"backfill_results_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Results saved to %s", out_path)

    print("\n=== Backfill Summary ===")
    print(f"Total missions: {results['total_missions']}")
    print(f"Reports created: {results['reports_created']}")
    print(f"Reports linked: {results['reports_linked']}")
    print(f"Skipped (already has report): {results['skipped_already_has_report']}")
    print(f"Failed: {results['failed']}")
    print(f"Completed at: {results['completed_at']}")

    if results["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
