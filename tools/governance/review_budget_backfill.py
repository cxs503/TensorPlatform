#!/usr/bin/env python3
"""SPEC §6.5 审查预算账本：近 7 天真实审查活动回填 + 首份周报 + 图。

数据源（全部真实记录、带时间戳；不造数、不补零）：
  S1 审批决策  GET /api/v1/tensor/approvals
      窗口内 decided_at 的每条审批 = 一次审查决策；耗时 = decided_at - created_at
      （observed，含排队等待，故取作「审查占用」的上界）；kind 关键词映射到四类。
  S2 任务墙钟  GET /api/v1/tensor/missions
      窗口内 finished_at 的任务 = 生产活动；耗时 = finished_at - created_at（observed）。
  S3 门禁日志  /root/tp-agent/governor.log 的 pr-gate / ratchet 行
      机器判定的 Technical Review（pr-gate 合入决策）与 Execution Audit（ratchet 回归锁）。
      日志行无耗时时长 → 只计事件数，duration_source=not_observable（不计入分钟）。
四类中窗口内无真实记录的类型记 0（honest zero）。

输出：artifacts/review_budget_ledger.jsonl（只增不改账本）、
      artifacts/m5019/review_budget_backfill.json（判据数字 + 溯源）、
      docs/governance/review_budget_weekly_<ISO>.md（首份周报）、
      docs/governance/review_budget_ledger.md（口径与实测表）、
      artifacts/m5019/review_budget_backfill.png（图）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.review_budget_ledger import (  # noqa: E402
    CAP_RATIO,
    REVIEW_TYPES,
    ReviewBudgetLedger,
    aggregate_window,
    isoformat,
    render_weekly_markdown,
    week_window,
)

API_BASE = os.getenv("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
GOVERNOR_LOG = Path(os.getenv("TP_GOVERNOR_LOG", "/root/tp-agent/governor.log"))
LEDGER_PATH = ROOT / "artifacts" / "review_budget_ledger.jsonl"
OUT_DIR = ROOT / "artifacts" / "m5019"
DOCS_DIR = ROOT / "docs" / "governance"
CAP_PER_SOURCE = 300
WINDOW_DAYS = 7

# kind 关键词 → 四类审查（SPEC §6.1）。未命中的 kind 不猜测，记入 unmapped。
KIND_KEYWORDS: Tuple[Tuple[str, str], ...] = (
    ("accept", "outcome"),
    ("release", "technical"),
    ("gate", "technical"),
    ("deploy", "technical"),
    ("review", "technical"),
    ("budget", "execution"),
    ("dispatch", "execution"),
    ("claim", "execution"),
    ("innov", "innovation"),
    ("proposal", "innovation"),
    ("experiment", "innovation"),
)


def classify_kind(kind: str) -> str | None:
    text = (kind or "").lower()
    for token, review_type in KIND_KEYWORDS:
        if token in text:
            return review_type
    return None


def api_get(path: str, token: str) -> Dict[str, Any]:
    req = urllib.request.Request(API_BASE + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def login() -> str:
    payload = json.dumps(
        {
            "username": os.getenv("TENSOR_AGENT_USER", "eco_admin"),
            "password": os.getenv("TENSOR_AGENT_PASS", ""),
        }
    ).encode()
    req = urllib.request.Request(
        API_BASE + "/auth/login", data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def existing_refs(ledger: ReviewBudgetLedger) -> set[str]:
    return {
        str((row.get("evidence") or {}).get("source_ref"))
        for row in ledger.entries()
        if (row.get("evidence") or {}).get("source_ref")
    }


def backfill_approvals(ledger: ReviewBudgetLedger, token: str, lo, hi, refs) -> Dict[str, int]:
    stats = {"scanned": 0, "inserted": 0, "duplicate": 0, "unmapped": 0, "outside_window": 0}
    items = api_get("/tensor/approvals?limit=500", token).get("items", [])
    for item in items:
        stats["scanned"] += 1
        decided = parse_ts(item.get("decided_at"))
        created = parse_ts(item.get("created_at"))
        if decided is None or not (lo <= decided < hi):
            stats["outside_window"] += 1
            continue
        review_type = classify_kind(str(item.get("kind")))
        if review_type is None:
            stats["unmapped"] += 1
            continue
        ref = f"approvals:{item.get('id')}"
        if ref in refs:
            stats["duplicate"] += 1
            continue
        duration = max((decided - created).total_seconds(), 0.0) if created else 0.0
        status = str(item.get("status") or "").lower()
        conclusion = {
            "approved": "approved",
            "rejected": "changes_requested",
            "pending": "inconclusive",
        }.get(status, "inconclusive")
        ledger.record_review(
            review_type,
            subject=f"mission:{item.get('mission_id')}/approval:{item.get('id')}",
            duration_s=duration,
            conclusion=conclusion,
            reviewer=str(item.get("decided_by") or "unspecified"),
            duration_source="observed" if created else "not_observable",
            evidence={
                "source_ref": ref,
                "source": "S1:/tensor/approvals",
                "kind": item.get("kind"),
                "approval_status": status,
                "requested_by": item.get("requested_by"),
                "mission_id": item.get("mission_id"),
            },
            recorded_at=decided,
        )
        refs.add(ref)
        stats["inserted"] += 1
    return stats


def backfill_production(ledger: ReviewBudgetLedger, token: str, lo, hi, refs) -> Dict[str, int]:
    stats = {"scanned": 0, "inserted": 0, "duplicate": 0, "outside_window": 0, "no_duration": 0}
    items = api_get("/tensor/missions?limit=500", token).get("items", [])
    for item in items:
        stats["scanned"] += 1
        finished = parse_ts(item.get("finished_at"))
        created = parse_ts(item.get("created_at"))
        if finished is None or not (lo <= finished < hi):
            stats["outside_window"] += 1
            continue
        ref = f"missions:{item.get('id')}"
        if ref in refs:
            stats["duplicate"] += 1
            continue
        duration = max((finished - created).total_seconds(), 0.0) if created else 0.0
        if duration <= 0:
            stats["no_duration"] += 1
        ledger.record_production(
            subject=f"mission:{item.get('id')}",
            duration_s=duration,
            actor=str(item.get("model") or item.get("claimed_by") or "unknown"),
            duration_source="observed" if duration > 0 else "not_observable",
            evidence={
                "source_ref": ref,
                "source": "S2:/tensor/missions",
                "repo": item.get("repo"),
                "mission_status": item.get("status"),
            },
            recorded_at=finished,
        )
        refs.add(ref)
        stats["inserted"] += 1
    return stats


LINE_RE = re.compile(r"^(?P<md>\d{2}-\d{2})T(?P<hms>\d{2}:\d{2}:\d{2})Z\s+(?P<body>.*)$")


def backfill_governor(ledger: ReviewBudgetLedger, lo, hi, refs, year: int) -> Dict[str, Any]:
    stats = {
        "path": str(GOVERNOR_LOG),
        "exists": GOVERNOR_LOG.exists(),
        "scanned": 0,
        "in_window": 0,
        "inserted": 0,
        "duplicate": 0,
        "by_rule": {},
        "sha256": None,
    }
    if not GOVERNOR_LOG.exists():
        return stats
    raw = GOVERNOR_LOG.read_bytes()
    stats["sha256"] = hashlib.sha256(raw).hexdigest()
    candidates: List[Tuple[datetime, str, str]] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        match = LINE_RE.match(line.strip())
        if not match:
            continue
        stats["scanned"] += 1
        try:
            ts = datetime.strptime(
                f"{year}-{match.group('md')}T{match.group('hms')}+00:00", "%Y-%m-%dT%H:%M:%S%z"
            )
        except ValueError:
            continue
        if not (lo <= ts < hi):
            continue
        body = match.group("body")
        if "pr-gate" in body:
            rule, review_type = "pr-gate", "technical"
            conclusion = "rejected" if ("block" in body or "fail" in body) else "approved"
        elif "ratchet:" in body:
            rule, review_type = "ratchet", "execution"
            conclusion = "changes_requested" if "green=False" in body else "approved"
        else:
            continue
        stats["in_window"] += 1
        candidates.append((ts, rule, f"{review_type}|{conclusion}|{body[:160]}"))

    candidates.sort()
    truncated = max(len(candidates) - CAP_PER_SOURCE, 0)
    for index, (ts, rule, packed) in enumerate(candidates[-CAP_PER_SOURCE:]):
        review_type, conclusion, body = packed.split("|", 2)
        ref = "governor:" + hashlib.sha256(f"{ts.isoformat()}|{body}".encode()).hexdigest()[:16]
        if ref in refs:
            stats["duplicate"] += 1
            continue
        ledger.record_review(
            review_type,
            subject=f"gate-event:{rule}",
            duration_s=0.0,
            conclusion=conclusion,
            reviewer=f"watchline:{rule}",
            duration_source="not_observable",
            evidence={
                "source_ref": ref,
                "source": "S3:governor.log",
                "rule": rule,
                "log_line": body[:200],
            },
            recorded_at=ts,
        )
        refs.add(ref)
        stats["inserted"] += 1
        stats["by_rule"][rule] = stats["by_rule"].get(rule, 0) + 1
    stats["truncated"] = truncated
    stats["cap_per_source"] = CAP_PER_SOURCE
    return stats


def figure(report: Dict[str, Any], path: Path) -> Dict[str, Any]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "reason": f"matplotlib unavailable: {exc}"}

    per_type = report["governance"]["per_type"]
    labels = [bucket["review_type"] for bucket in per_type]
    reviews = [bucket["reviews"] for bucket in per_type]
    exemptions = [bucket["exemptions"] for bucket in per_type]
    minutes = [bucket["duration_s"] / 60.0 for bucket in per_type]
    ratios = report["ratios"]
    duration_ratio = ratios["duration_ratio_governance_over_production"]

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))
    axes[0].bar(labels, reviews, color="#2b6cb0", label="reviews")
    axes[0].bar(labels, exemptions, bottom=reviews, color="#dd6b20", label="exemptions")
    axes[0].set_title("Review events by type (real, 7d backfill)")
    axes[0].set_ylabel("events")
    axes[0].legend()
    axes[1].bar(labels, minutes, color="#2f855a")
    axes[1].set_title("Governance minutes by type")
    axes[1].set_ylabel("minutes (observed)")
    axes[2].axhline(CAP_RATIO, color="#c53030", linestyle="--", label="cap 25% (SPEC 6.5)")
    if duration_ratio is not None:
        axes[2].bar(["observed ratio"], [duration_ratio], color="#553c9a")
        axes[2].annotate(
            f"{duration_ratio:.3f}\ncoverage {ratios['governance_duration_coverage']:.2f}",
            xy=(0, duration_ratio),
            xytext=(0, min(duration_ratio * 1.6, CAP_RATIO * 2)),
            ha="center",
        )
    axes[2].set_ylim(0, max(CAP_RATIO * 2.2, (duration_ratio or 0) * 1.5, 0.1))
    axes[2].set_title("Governance / production (minute basis)")
    axes[2].legend()
    for ax in axes:
        ax.grid(alpha=0.25, axis="y")
    fig.suptitle(
        f"Review budget ledger — {report['iso_week']} ({report['week_start'][:10]}..{report['week_end'][:10]})",
        fontsize=12,
    )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return {"ok": True, "path": str(path), "bytes": path.stat().st_size}


def main() -> int:
    now = datetime.now(timezone.utc)
    lo, hi = now - timedelta(days=WINDOW_DAYS), now
    ledger = ReviewBudgetLedger(LEDGER_PATH)
    refs = existing_refs(ledger)
    before = ledger.verify_chain()["entries"]

    token = login()
    stats = {
        "S1_approvals": backfill_approvals(ledger, token, lo, hi, refs),
        "S2_missions_production": backfill_production(ledger, token, lo, hi, refs),
        "S3_governor_gate": backfill_governor(ledger, lo, hi, refs, now.year),
    }
    chain = ledger.verify_chain()
    entries = ledger.entries()

    # 尾随 7 天窗口与 ISO 周窗口（周报口径）
    week_start = week_window(now)[0]
    week_report = aggregate_window(entries, week_start)
    previous_week_report = aggregate_window(entries, week_start - timedelta(days=7))
    week_report["ledger"] = {"path": str(LEDGER_PATH), "chain": chain}

    summary = {
        "generated_at": isoformat(now),
        "window": {"start": isoformat(lo), "end": isoformat(hi), "days": WINDOW_DAYS},
        "ledger": {
            "path": str(LEDGER_PATH),
            "entries_before": before,
            "entries_after": chain["entries"],
            "appended": chain["entries"] - before,
            "chain": chain,
        },
        "sources": stats,
        "iso_week_report": week_report,
        "previous_iso_week_report": previous_week_report,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "review_budget_backfill.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    weekly_md = DOCS_DIR / f"review_budget_weekly_{week_report['iso_week']}.md"
    weekly_md.write_text(render_weekly_markdown(week_report) + provenance_section(summary), encoding="utf-8")
    previous_md = DOCS_DIR / f"review_budget_weekly_{previous_week_report['iso_week']}.md"
    previous_md.write_text(render_weekly_markdown(previous_week_report), encoding="utf-8")
    write_design_doc(summary, week_report, lo, hi)
    fig = figure(week_report, OUT_DIR / "review_budget_backfill.png")
    summary["figure"] = fig
    (OUT_DIR / "review_budget_backfill.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ratios = week_report["ratios"]
    print(json.dumps({
        "appended": summary["ledger"]["appended"],
        "entries": chain["entries"],
        "chain_ok": chain["ok"],
        "governance_events": week_report["governance"]["events"],
        "per_type": {b["review_type"]: b["reviews"] for b in week_report["governance"]["per_type"]},
        "production_events": week_report["production"]["events"],
        "ratio_minutes": ratios["duration_ratio_governance_over_production"],
        "ratio_events": ratios["event_ratio_governance_over_production"],
        "coverage": ratios["governance_duration_coverage"],
        "cap_status": ratios["cap_status"],
        "figure": fig.get("ok"),
        "weekly_md": str(weekly_md),
    }, ensure_ascii=False, indent=2))
    return 0


def provenance_section(summary: Dict[str, Any]) -> str:
    stats = summary["sources"]
    lines = ["", "## 数据溯源（真实活动回填，零手抄）", ""]
    lines.append("| 源 | 扫描 | 窗口内新增 | 已存在去重 | 截断 | 说明 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    s1 = stats["S1_approvals"]
    lines.append(
        f"| S1 `/tensor/approvals` | {s1['scanned']} | {s1['inserted']} | {s1['duplicate']} | 0 | "
        f"审批决策，kind 未映射 {s1['unmapped']} 条（不猜测类型） |"
    )
    s2 = stats["S2_missions_production"]
    lines.append(
        f"| S2 `/tensor/missions`（生产侧） | {s2['scanned']} | {s2['inserted']} | {s2['duplicate']} | 0 | "
        f"窗口内完成任务墙钟 finished_at−created_at |"
    )
    s3 = stats["S3_governor_gate"]
    lines.append(
        f"| S3 `governor.log` 门禁行 | {s3['scanned']} | {s3['inserted']} | {s3['duplicate']} | "
        f"{s3.get('truncated', 0)} | pr-gate→technical，ratchet→execution；sha256 `{str(s3['sha256'])[:16]}…` |"
    )
    lines.append("")
    lines.append(
        f"- 账本：`{summary['ledger']['path']}`，条目 {summary['ledger']['entries_before']} → "
        f"{summary['ledger']['entries_after']}，链校验 `ok={summary['ledger']['chain']['ok']}`（只增不改）。"
    )
    lines.append(f"- 回填窗口：`{summary['window']['start']}` → `{summary['window']['end']}`（UTC，7 天）。")
    lines.append(
        "- 口径声明：审批耗时含排队等待，任务墙钟含等待派发，两侧同为墙钟口径；"
        "门禁日志行无耗时字段，只计事件数（duration_source=not_observable），不得计入分钟比例。"
    )
    return "\n".join(lines) + "\n"


def write_design_doc(summary: Dict[str, Any], week_report: Dict[str, Any], lo, hi) -> None:
    gov = week_report["governance"]
    ratios = week_report["ratios"]
    per_type_rows = "\n".join(
        f"| {b['review_type']} | {b['reviews']} | {b['exemptions']} | {b['duration_s']:.1f} | "
        f"{b['observed_duration_s']:.1f} |"
        for b in gov["per_type"]
    )
    doc = f"""# 审查预算账本（SPEC §6.5 测量先行）

本文件由 `tools/governance/review_budget_backfill.py` 程序化生成，数字全部来自真实记录，
零手抄；账本 `artifacts/review_budget_ledger.jsonl` 只增不改（sha256 链可校验）。

## 1. 口径

- **四类审查**（SPEC §6.1）：Execution Audit / Technical Review / Outcome Assessment / Innovation Review。
  每条记录：审查对象（subject）、类型（review_type）、耗时（duration_s）、结论（conclusion）、
  审查者（reviewer），并附来源（evidence.source_ref）与耗时来源（duration_source）。
- **耗时来源分级**：`observed`（由原始记录时间戳相减）／`reported`（审查者申报）／
  `not_observable`（只有事件、没有耗时，计入事件数但不计入分钟数）。
- **比例定义**：`治理耗时 / 生产耗时`（§6.5 建议 ≤{CAP_RATIO:.2f}）。另给事件口径比例与
  治理耗时占活动总耗时份额，三者并列，避免单一指标误读。
- **限额状态**：本期**只测量不限额**。`cap_status=insufficient_duration_coverage` 表示
  耗时覆盖率低于 0.5，此时不得据此判定是否达标；`measured` 才可读比例与上限对比。
- **豁免留痕**（§6.5「不得静默免审」）：`record_exemption` 与审查同账本同链，
  必须带 reason，结论固定 `exempted`，在周报里单列。

## 2. 接口（`/api/v1/governance/review-budget/*`）

| 端点 | 方法 | 用途 |
| --- | --- | --- |
| `/types` | GET | 四类审查定义与合法取值 |
| `/reviews` | POST | 记一笔审查（类型/对象/耗时/结论/审查者/证据） |
| `/exemptions` | POST | 记一笔豁免（必填 reason） |
| `/production` | POST | 记一笔生产活动（比例分母） |
| `/entries` | GET | 按 ISO 周/类型/种类查账 |
| `/weekly` | GET | 周聚合报告（JSON） |
| `/verify` | GET | 链校验（只增不改证据） |

后台周聚合任务：`review_budget.weekly_aggregate`（Celery，周期 604800 s），
把周报落 `docs/governance/review_budget_weekly_<ISO周>.md`，摘要落 `artifacts/review_budget/`。
聚合逻辑在 `backend/services/review_budget_weekly.py`，不依赖 Celery 可直接复算。

## 3. 实测（真实回填窗口 {summary['window']['start'][:10]} → {summary['window']['end'][:10]}，周 {week_report['iso_week']}）

| 类型 | 审查数 | 豁免数 | 耗时 s | 可观测耗时 s |
| --- | --- | --- | --- | --- |
{per_type_rows}

- 治理事件 {gov['events']}（审查 {gov['reviews']} + 豁免 {gov['exemptions']}），累计 {gov['duration_s']:.1f} s；
  生产事件 {week_report['production']['events']}，累计 {week_report['production']['duration_s']:.1f} s。
- 分钟口径比例 {ratios['duration_ratio_governance_over_production']}；事件口径比例
  {ratios['event_ratio_governance_over_production']}；耗时覆盖率 {ratios['governance_duration_coverage']:.4f}；
  `cap_status={ratios['cap_status']}`。

## 4. 局限（如实声明）

- 审批耗时含排队等待、任务墙钟含等待派发，两者都是「占用墙钟」而非纯算力，比例只能作上界参考。
- 门禁日志（pr-gate/ratchet）无耗时字段，故 Technical/Execution 两类真实事件只进事件口径；
  分钟口径覆盖率因此被压低，`cap_status` 如实标为覆盖率不足，不做达标结论。
- Innovation Review 在窗口内无真实记录，记 0（honest zero），不补造数据。
- 回填来源为平台自身记录（审批表、任务表、门禁日志），只能反映「记录的审查」，不覆盖离线/人工未入账的审查。
"""
    (DOCS_DIR / "review_budget_ledger.md").write_text(doc, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())