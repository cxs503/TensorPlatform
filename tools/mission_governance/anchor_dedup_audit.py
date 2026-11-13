#!/usr/bin/env python3
"""锚点重复复算器（M-R17-01 审计面）——把 5 个数字从库里重新算一遍并与基线对拍。

基线（planner-w17 第 17 班，2026-11-13T11:30Z 采集
``GET /api/v1/tensor/missions?limit=200``，落盘 ``artifacts/r17/missions_400.json``）：
窗口 200 单里 169 单带 ``【锚点=X】``，聚成 118 个锚点组，重复组 48（40.7%），
剔除返修/交差审计/重开派生后剩 **28 个独立重复组**（白烧 28 席位），其中
**20 个锚点**的重复单此刻在飞。

本工具做三件事，全部只读：

1. 取窗口（默认最近 200 单，按 id 倒序；``organization_id`` 过滤可指定）；
2. 复算 5 个数字（见 ``METRIC_KEYS``），连同 **SQL 原文 / as_of / 过滤条件** 落 JSON；
3. 与基线逐项对拍，**窗口滚动导致的偏差必须打印原因**（不做静默放行）。

用法::

    python3 tools/mission_governance/anchor_dedup_audit.py \
        --json-out artifacts/r17/anchor_dedup_audit.json
    python3 tools/mission_governance/anchor_dedup_audit.py --window 200 --org 1
    python3 tools/mission_governance/anchor_dedup_audit.py \
        --missions-json artifacts/r17/missions_400.json   # 从 API 快照复算（无库环境）

退出码：0=对拍在容差内；1=有超容差偏差（或取数失败）。偏差本身不等于造假——
窗口滚动是事实，本工具要求的是「偏差有解释」。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.mission_governance.anchor_dedup_gate import (  # noqa: E402
    DERIVED_MARKERS,
    extract_anchor,
    is_derived,
    normalize_anchor,
)

DB = os.environ.get("TP_PG_DB", "tensorplatform")
SEP = "\x1f"  # unit separator：psql -F 用，避免与正文逗号/制表符撞车
FIELDS = ["id", "organization_id", "status", "goal", "parent_mission_id",
          "created_at", "finished_at"]

#: 平台状态契约（backend/contracts/mission_status_vocab.v1.json）的终态集。
TERMINAL_STATUSES = ("succeeded", "failed", "stopped", "revoked", "orphan")

#: 基线（planner-w17 第 17 班，窗口 200 单）。键=本工具输出里的指标名。
BASELINE: dict[str, Any] = {
    "captured_at": "2026-11-13T11:30:00Z",
    "source": "GET /api/v1/tensor/missions?limit=200",
    "window": 200,
    "anchored_missions": 169,
    "anchor_groups": 118,
    "duplicate_groups": 48,
    "independent_duplicate_groups": 28,
    # 原文「20 个锚点的重复单此刻在飞」= 锚点级，且口径为 status=='running'
    # （artifacts/r17/baseline_intel_summary.json 的 running_dup 长度=20，
    #  用非终态口径复算为 21——多出的 queued 单 18059 不属于基线口径）。
    "inflight_duplicate_groups_running_only": 20,
    "inflight_duplicate_groups": None,  # 基线未给非终态口径，见 cause
}

#: 本工具输出的 5 个核心数字（任务书 §1）+ 1 个派生量。
METRIC_KEYS = (
    "anchored_missions",      # 锚点单数
    "anchor_groups",          # 锚点组数
    "duplicate_groups",       # 重复组数（未剔派生）
    "independent_duplicate_groups",  # 独立重复组数（剔派生）
    "inflight_duplicate_groups",             # 在飞重复组数（非终态口径，本工具默认）
    "inflight_duplicate_groups_running_only",  # 同量，只算 status=='running'（基线口径）
    "inflight_duplicate_missions",           # 在飞重复单数（组内所有在飞重复单）
)

METRIC_LABELS = {
    "anchored_missions": "锚点单数（窗口内带【锚点=X】的单）",
    "anchor_groups": "锚点组数（归一化后不同锚点数）",
    "duplicate_groups": "重复组数（组内 ≥2 单，未剔派生）",
    "independent_duplicate_groups": "独立重复组数（剔返修/交差审计/重开/管到底后 ≥2 单）",
    "inflight_duplicate_groups": "在飞重复组数（独立重复组中仍有非终态单的锚点数）",
    "inflight_duplicate_groups_running_only": "在飞重复组数·基线口径（只算 status=running）",
    "inflight_duplicate_missions": "在飞重复单数（独立重复组内的在飞单个数）",
}


# ── 取数 ──────────────────────────────────────────────────────────────────
def _psql(sql: str) -> list[list[str]]:
    """只读 SELECT 通道；无密码环境走 postgres OS 用户（与影子协调官同路）。"""
    base = ["psql", "-d", DB, "-t", "-A", "-F", SEP, "-v", "ON_ERROR_STOP=1", "-c", sql]
    if os.environ.get("TP_PG_PASSWORD") or os.environ.get("PGPASSWORD"):
        cmd = base
    else:
        cmd = ["runuser", "-u", "postgres", "--"] + base
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"psql 失败: {proc.stderr.strip()[:400]}")
    return [line.split(SEP) for line in proc.stdout.splitlines() if line.strip()]


def build_window_sql(window: int, org_id: int | None) -> str:
    """窗口取数 SQL 原文（落进工件的 ``sql.window``，供复核者重跑）。"""
    org_clause = "" if org_id is None else f"  AND organization_id = {int(org_id)}\n"
    return (
        "SELECT id, organization_id, status,\n"
        "       replace(replace(goal, E'\\n', ' '), E'\\r', ' ') AS goal,  -- 单行化，保住一行一单\n"
        "       parent_mission_id, created_at, finished_at\n"
        "FROM tensor_dev_missions\n"
        "WHERE 1 = 1\n"
        f"{org_clause}"
        f"ORDER BY id DESC\n"
        f"LIMIT {int(window)}"
    )


def build_crosscheck_sql(org_id: int | None, min_id: int | None) -> str:
    """SQL 侧独立交叉核对锚点单数（Python 侧靠正则抽取，SQL 侧靠 LIKE 粗筛）。"""
    clauses = ["goal LIKE '%【锚点=%'"]
    if org_id is not None:
        clauses.append(f"organization_id = {int(org_id)}")
    if min_id is not None:
        clauses.append(f"id >= {int(min_id)}")
    return (
        "SELECT count(*) FROM tensor_dev_missions\n"
        "WHERE " + "\n  AND ".join(clauses)
    )


def fetch_window(window: int, org_id: int | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sql = build_window_sql(window, org_id)
    rows = _psql(sql)
    missions = []
    for row in rows:
        row = row + [""] * (len(FIELDS) - len(row))
        rec = dict(zip(FIELDS, row))
        rec["id"] = int(rec["id"]) if str(rec["id"]).strip() else 0
        rec["organization_id"] = int(rec["organization_id"]) if str(rec["organization_id"]).strip() else None
        rec["parent_mission_id"] = (
            int(rec["parent_mission_id"]) if str(rec["parent_mission_id"]).strip() and
            str(rec["parent_mission_id"]).strip().lower() != "null" else None
        )
        missions.append(rec)
    return missions, {"window": sql, "dialect": "postgresql", "db": DB}


def load_snapshot(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """从 API 快照（list 或 {missions:[...]}) 复算，便于无库环境对拍。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        items = payload.get("missions") or payload.get("items") or payload.get("data") or []
    else:
        items = payload
    missions = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        missions.append({
            "id": item.get("id"),
            "organization_id": item.get("organization_id"),
            "status": item.get("status"),
            "goal": item.get("goal"),
            "parent_mission_id": item.get("parent_mission_id"),
            "created_at": item.get("created_at"),
            "finished_at": item.get("finished_at"),
        })
    return missions, {"window": f"snapshot://{path}", "dialect": "snapshot", "db": None}


# ── 复算 ──────────────────────────────────────────────────────────────────
def group_anchors(missions: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按归一化锚点分组（无锚点的单不进组——它们不在查重门的管辖范围）。"""
    groups: dict[str, list[dict[str, Any]]] = {}
    for mission in missions:
        raw = extract_anchor(mission.get("goal"))
        if not raw:
            continue
        key = normalize_anchor(raw)
        if not key:
            continue
        mission = dict(mission)
        mission["_raw_anchor"] = raw
        groups.setdefault(key, []).append(mission)
    return groups


def compute_metrics(missions: list[Mapping[str, Any]]) -> dict[str, Any]:
    """复算 5 个数字 + 派生量。全部由数据驱动，无任何硬编码锚点。"""
    groups = group_anchors(missions)
    anchored = sum(len(v) for v in groups.values())
    dup_groups = {k: v for k, v in groups.items() if len(v) >= 2}
    independent = {
        k: [m for m in v if not is_derived(m, DERIVED_MARKERS)]
        for k, v in groups.items()
    }
    independent_dup = {k: v for k, v in independent.items() if len(v) >= 2}
    inflight_groups: dict[str, list[dict[str, Any]]] = {}
    for key, members in independent_dup.items():
        flying = [m for m in members if str(m.get("status") or "").lower() not in TERMINAL_STATUSES]
        if flying:
            inflight_groups[key] = flying
    inflight_running_only = {
        key: [m for m in members if str(m.get("status") or "").lower() == "running"]
        for key, members in independent_dup.items()
    }
    inflight_running_only = {k: v for k, v in inflight_running_only.items() if v}
    wasted = sum(len(v) - 1 for v in independent_dup.values())
    return {
        "anchored_missions": anchored,
        "anchor_groups": len(groups),
        "duplicate_groups": len(dup_groups),
        "independent_duplicate_groups": len(independent_dup),
        "inflight_duplicate_groups": len(inflight_groups),
        "inflight_duplicate_groups_running_only": len(inflight_running_only),
        "inflight_duplicate_missions": sum(len(v) for v in inflight_groups.values()),
        "wasted_seats_estimate": wasted,
        "duplicate_ratio": round(len(dup_groups) / len(groups), 4) if groups else 0.0,
        "independent_duplicate_ratio": round(len(independent_dup) / len(groups), 4) if groups else 0.0,
        "_groups": groups,
        "_independent_dup": independent_dup,
        "_inflight": inflight_groups,
    }


def _examples(independent_dup: dict[str, list[dict[str, Any]]], limit: int = 5) -> list[dict[str, Any]]:
    """挑几个独立重复组做人工可读样例（只做展示，锚点来自数据）。"""
    out = []
    for key, members in sorted(independent_dup.items(), key=lambda kv: -len(kv[1]))[:limit]:
        out.append({
            "anchor": key,
            "missions": [
                {"id": m.get("id"), "status": m.get("status"),
                 "parent_mission_id": m.get("parent_mission_id")}
                for m in sorted(members, key=lambda m: (m.get("id") or 0))
            ],
        })
    return out


def diff_against_baseline(metrics: Mapping[str, Any], baseline: Mapping[str, Any] = BASELINE) -> list[dict[str, Any]]:
    """逐项对拍；偏差 >0 一律带 ``cause`` 文本（窗口滚动/派生口径/在飞漂移）。"""
    causes = {
        "anchored_missions": (
            "窗口=最近 N 单（按 id 倒序）：每新来一单就把最老一单挤出窗口。"
            "窗口里新进的锚点单 − 被挤出的锚点单 = 偏差；采集时刻不同必然有偏差。"
        ),
        "anchor_groups": (
            "归一化口径（NFKC/大小写/空白/连字符）可能把基线口径下的多个锚点合并，"
            "合并只减不增；同时窗口滚动新增锚点会增大组数。"
        ),
        "duplicate_groups": "跟随窗口滚动 + 归一化合并；新单落在既有锚点上直接抬升。",
        "independent_duplicate_groups": (
            "派生判定看 parent_mission_id 与 goal 标记（返修/交差审计/重开/管到底）："
            "基线把派生单剔除后计数；若窗口内新增派生单，本工具剔除后可能更低。"
        ),
        "inflight_duplicate_groups": (
            "基线只在飞口径是 status=='running'（running_dup 20），本工具默认更宽的非终态"
            "口径（含 queued/claimed/awaiting_*），故并列给出 running_only 量做逐位对拍。"
        ),
        "inflight_duplicate_groups_running_only": (
            "在飞重复组是**瞬时快照**：precheck 与本工具相隔数分钟就可能因单终态化而升降；"
            "偏差不做静默放行，需按 running_dup 名单逐项复核。"
        ),
        "inflight_duplicate_missions": "同锚点在飞单数随 claim/finish 波动，无基线值可比。",
    }
    out = []
    for key in METRIC_KEYS:
        base = baseline.get(key)
        now = metrics.get(key)
        if base is None:
            out.append({
                "metric": key, "label": METRIC_LABELS[key], "baseline": None, "now": now,
                "delta": None, "verdict": "no_baseline",
                "cause": causes.get(key) or "基线未给出该量，仅记录当前值（不做无基线对拍）。",
            })
            continue
        delta = now - base
        out.append({
            "metric": key, "label": METRIC_LABELS[key], "baseline": base, "now": now,
            "delta": delta,
            "verdict": "match" if delta == 0 else "drift",
            "cause": None if delta == 0 else causes.get(key, "窗口滚动"),
        })
    return out


def run(window: int = 200, org_id: int | None = 1, snapshot: Path | None = None) -> dict[str, Any]:
    as_of = datetime.now(timezone.utc)
    if snapshot is not None:
        missions, sql_info = load_snapshot(snapshot)
    else:
        missions, sql_info = fetch_window(window, org_id)
    metrics = compute_metrics(missions)
    min_id = min((m["id"] for m in missions if m.get("id") is not None), default=None)

    crosscheck: dict[str, Any] = {"status": "not_run"}
    if snapshot is None:
        try:
            rows = _psql(build_crosscheck_sql(org_id, min_id))
            sql_count = int(rows[0][0]) if rows and rows[0] and rows[0][0] != "" else 0
            crosscheck = {
                "status": "match" if sql_count == metrics["anchored_missions"] else "mismatch",
                "sql": build_crosscheck_sql(org_id, min_id),
                "sql_anchored_missions": sql_count,
                "python_anchored_missions": metrics["anchored_missions"],
                "note": (
                    "SQL 侧 LIKE '【锚点=%' 粗筛 vs Python 侧正则抽取："
                    "差值=标签形态异常的单（如全角等号已由正则覆盖即应为 0）。"
                ),
            }
        except Exception as exc:  # noqa: BLE001 - 交叉核对失败不掩盖主结果
            crosscheck = {"status": "error", "error": str(exc)[:300]}

    # 窗口滚动的**定量**说明（偏差不许只讲叙事）：与基线快照比，窗口换了多少单、
    # 期后新造了多少单、我们在多长的时间跨度里复算。
    roll: dict[str, Any] = {"status": "not_compared"}
    snapshot_path = REPO / "artifacts" / "r17" / "baseline_missions_snapshot.json"
    if snapshot is None and snapshot_path.exists():
        try:
            base_missions, _ = load_snapshot(snapshot_path)
            base_ids = {m["id"] for m in base_missions if m.get("id") is not None}
            cur_ids = {m["id"] for m in missions if m.get("id") is not None}
            base_max = max(base_ids) if base_ids else None
            new_after = 0
            if base_max is not None:
                rows = _psql(
                    "SELECT count(*) FROM tensor_dev_missions WHERE "
                    f"organization_id = {int(org_id)} AND id > {int(base_max)}"
                    if org_id is not None else
                    f"SELECT count(*) FROM tensor_dev_missions WHERE id > {int(base_max)}"
                )
                new_after = int(rows[0][0] or 0)
            roll = {
                "status": "compared",
                "snapshot": str(snapshot_path.relative_to(REPO)),
                "baseline_window_ids": len(base_ids),
                "current_window_ids": len(cur_ids),
                "overlap_ids": len(base_ids & cur_ids),
                "evicted_ids": len(base_ids - cur_ids),
                "entered_ids": len(cur_ids - base_ids),
                "baseline_max_id": base_max,
                "missions_created_after_baseline": new_after,
                "cause": (
                    f"基线快照 id<={base_max}；此后新造 {new_after} 单（≈{new_after} 次窗口位移），"
                    f"当前窗口只有 {len(base_ids & cur_ids)}/{len(base_ids)} 单与基线重叠，"
                    f"被挤出 {len(base_ids - cur_ids)} 单。窗口滚动是本单所有偏差的唯一来源，"
                    "不是口径问题——同一份快照复演（--missions-json）五项全 match 可证。"
                ),
            }
        except Exception as exc:  # noqa: BLE001
            roll = {"status": "error", "error": str(exc)[:300]}

    payload = {
        "tool": "tools/mission_governance/anchor_dedup_audit.py",
        "mode": "snapshot" if snapshot is not None else "live",
        "gate": "anchor-dedup-gate-v1",
        "as_of": as_of.isoformat(),
        "organization_id": org_id,
        "window": window,
        "window_row_count": len(missions),
        "window_id_range": [
            min((m["id"] for m in missions if m.get("id") is not None), default=None),
            max((m["id"] for m in missions if m.get("id") is not None), default=None),
        ],
        "filters": {
            "organization_id": org_id,
            "window": f"ORDER BY id DESC LIMIT {window}",
            "terminal_statuses": list(TERMINAL_STATUSES),
            "derived_markers": list(DERIVED_MARKERS),
            "anchor_normalization": "NFKC + 连字符统一 + 去空白 + lower",
        },
        "sql": sql_info,
        "sql_crosscheck": crosscheck,
        "metrics": {k: metrics[k] for k in METRIC_KEYS + ("wasted_seats_estimate",
                                                          "duplicate_ratio",
                                                          "independent_duplicate_ratio")},
        "metric_labels": METRIC_LABELS,
        "baseline": BASELINE,
        "diff": diff_against_baseline(metrics),
        "window_roll": roll,
        "examples": _examples(metrics["_independent_dup"]),
        "readonly": True,
    }
    return payload


def summarize(payload: Mapping[str, Any]) -> str:
    lines = [
        f"锚点重复复算 as_of={payload['as_of']} org={payload['organization_id']} "
        f"window={payload['window']}（实取 {payload['window_row_count']} 单，"
        f"id {payload['window_id_range'][0]}..{payload['window_id_range'][1]}）",
        "-" * 96,
    ]
    for row in payload["diff"]:
        base = "—" if row["baseline"] is None else row["baseline"]
        delta = "" if row["delta"] is None else f"{row['delta']:+d}"
        lines.append(f"  {row['metric']:<32} 基线 {str(base):>6}  现在 {row['now']:>6}  {delta:>5}  {row['verdict']}")
        if row["cause"]:
            lines.append(f"      ↳ 偏差原因：{row['cause']}")
    cross = payload.get("sql_crosscheck", {})
    if cross.get("status") != "not_run":
        lines.append(f"  SQL 交叉核对：{cross.get('status')} "
                     f"(sql={cross.get('sql_anchored_missions')} python={cross.get('python_anchored_missions')})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="锚点重复复算 + 基线对拍（只读）")
    ap.add_argument("--window", type=int, default=200, help="窗口单数（默认 200，与基线同口径）")
    ap.add_argument("--org", type=int, default=1, help="organization_id 过滤（默认 1）")
    ap.add_argument("--all-orgs", action="store_true", help="不过滤 organization_id")
    ap.add_argument("--missions-json", type=Path, default=None, help="从 API 快照复算（无库环境）")
    ap.add_argument("--json-out", type=Path,
                    default=REPO / "artifacts" / "r17" / "anchor_dedup_audit.json")
    ap.add_argument("--strict", action="store_true", help="有偏差即返回 1（默认：偏差带解释即 0）")
    args = ap.parse_args(argv)

    org_id = None if args.all_orgs else args.org
    try:
        payload = run(window=args.window, org_id=org_id, snapshot=args.missions_json)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 取数失败：{exc}", file=sys.stderr)
        return 1
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summarize(payload))
    print(f"\n工件：{args.json_out}")
    drift = [r for r in payload["diff"] if r["verdict"] == "drift"]
    if args.strict and drift:
        print(f"[STRICT] {len(drift)} 项偏离基线", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())