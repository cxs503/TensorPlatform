#!/usr/bin/env python3
"""动态升级机制核心逻辑（IN59-A6）。

当任务谱系连续失败或席位无进展时，自动生成升级菜单供协调者/策略席处置。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

# ─────────────────────────────── 常量 ───────────────────────────────

SCHEMA = "campaign-upgrade/1"

UPGRADE_OPTIONS: tuple[str, ...] = (
    "request_stronger_agent",       # 请求更强Agent（模型升级）
    "request_expert_knowledge",     # 请求领域专家知识包
    "reallocate_seat",              # 重分配席位
    "change_technical_route",       # 换技术路线（联 hypothesis_registry 立新假设）
)

TRIGGER_REASONS: tuple[str, ...] = (
    "consecutive_failures",         # 连续失败 ≥ 3
    "seat_no_progress",             # 席位心跳正常但无进展 ≥ 2 轮
)

DISPOSITION_ACTIONS: tuple[str, ...] = (
    "upgrade_agent",
    "inject_knowledge",
    "reallocate",
    "new_route",
    "dismiss",                      # 协调者判定无需升级
)

HYPOTHESIS_MAX_FALSIFICATIONS = 2  # 同一假设被证伪 2 次后不得第三次重跑

# ─────────────────────────────── DDL ───────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS upgrade_menus (
    menu_id           TEXT PRIMARY KEY,
    lineage_key       TEXT NOT NULL,
    seat_id           TEXT,
    trigger_reason    TEXT NOT NULL CHECK (trigger_reason IN ('consecutive_failures', 'seat_no_progress')),
    trigger_detail    TEXT,
    options_json      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'disposed', 'dismissed')),
    created_at        TEXT NOT NULL,
    disposed_at       TEXT,
    disposition       TEXT,
    disposition_note  TEXT
);

CREATE TABLE IF NOT EXISTS hypothesis_registry (
    hypothesis_id       TEXT PRIMARY KEY,
    lineage_key         TEXT NOT NULL,
    hypothesis_text     TEXT NOT NULL,
    falsification_count INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'blocked', 'superseded')),
    created_at          TEXT NOT NULL,
    blocked_at          TEXT,
    superseded_by       TEXT
);

CREATE TABLE IF NOT EXISTS seat_heartbeat_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    seat_id         TEXT NOT NULL,
    lineage_key     TEXT NOT NULL,
    heartbeat_at    TEXT NOT NULL,
    progress_flag   INTEGER NOT NULL DEFAULT 0,
    round_number    INTEGER NOT NULL DEFAULT 0,
    detail          TEXT
);
"""

# ─────────────────────────────── 谱系失败检测 ───────────────────────────────

def check_lineage_failures(
    conn,
    *,
    lineage_key: str,
    threshold: int = 3,
) -> dict[str, Any]:
    """检查某任务谱系是否连续失败达到阈值。

    从 ``tensor_dev_missions`` 表按 lineage_key 分组，取最近 ``threshold`` 条
    状态为 failed 的记录。返回 ``{"triggered": bool, "consecutive_fails": int, "recent_statuses": [...]}``。
    """
    from sqlalchemy import text

    rows = conn.execute(text("""
        SELECT id, status, finished_at, fail_stage, task_key
        FROM tensor_dev_missions
        WHERE task_key = :lk AND status IN ('failed', 'succeeded', 'accepted')
        ORDER BY finished_at DESC
        LIMIT :n
    """), {"lk": lineage_key, "n": threshold + 5}).mappings().all()

    if not rows:
        return {"triggered": False, "consecutive_fails": 0, "recent_statuses": [], "lineage_key": lineage_key}

    consecutive = 0
    recent = []
    for r in rows:
        recent.append({"id": r["id"], "status": r["status"], "finished_at": str(r["finished_at"]),
                       "fail_stage": r.get("fail_stage")})
        if r["status"] == "failed":
            consecutive += 1
        else:
            break

    triggered = consecutive >= threshold
    return {
        "triggered": triggered,
        "consecutive_fails": consecutive,
        "threshold": threshold,
        "recent_statuses": recent[:threshold],
        "lineage_key": lineage_key,
    }


# ─────────────────────────────── 席位无进展检测 ───────────────────────────────

def check_seat_no_progress(
    conn,
    *,
    seat_id: str,
    lineage_key: str,
    threshold: int = 2,
) -> dict[str, Any]:
    """检查某席位是否心跳正常但无进展达到阈值轮。

    从 ``seat_heartbeat_log`` 表取该席位最近记录，检查连续 progress_flag=0 的轮数。
    """
    from sqlalchemy import text

    rows = conn.execute(text("""
        SELECT seat_id, lineage_key, heartbeat_at, progress_flag, round_number, detail
        FROM seat_heartbeat_log
        WHERE seat_id = :sid AND lineage_key = :lk
        ORDER BY round_number DESC, heartbeat_at DESC
        LIMIT :n
    """), {"sid": seat_id, "lk": lineage_key, "n": threshold + 5}).mappings().all()

    if not rows:
        return {"triggered": False, "consecutive_no_progress": 0, "lineage_key": lineage_key, "seat_id": seat_id}

    consecutive_no_progress = 0
    recent = []
    for r in rows:
        recent.append({
            "seat_id": r["seat_id"],
            "round_number": r["round_number"],
            "heartbeat_at": str(r["heartbeat_at"]),
            "progress_flag": r["progress_flag"],
        })
        if r["progress_flag"] == 0:
            consecutive_no_progress += 1
        else:
            break

    triggered = consecutive_no_progress >= threshold
    return {
        "triggered": triggered,
        "consecutive_no_progress": consecutive_no_progress,
        "threshold": threshold,
        "recent_heartbeats": recent[:threshold],
        "lineage_key": lineage_key,
        "seat_id": seat_id,
    }


# ─────────────────────────────── 升级菜单构建 ───────────────────────────────

def build_upgrade_menu(
    conn,
    *,
    lineage_key: str,
    trigger_reason: str,
    seat_id: str | None = None,
    trigger_detail: dict[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """构建并持久化一条升级菜单。

    返回菜单完整记录（含 menu_id）。
    """
    from sqlalchemy import text

    menu_id = f"um-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    options = []
    for opt in UPGRADE_OPTIONS:
        options.append({
            "option": opt,
            "priority": UPGRADE_OPTIONS.index(opt) + 1,
            "description": _option_description(opt),
        })

    detail_json = json.dumps(trigger_detail or {}, ensure_ascii=False)
    options_json = json.dumps(options, ensure_ascii=False)

    conn.execute(text("""
        INSERT INTO upgrade_menus
            (menu_id, lineage_key, seat_id, trigger_reason, trigger_detail,
             options_json, status, created_at)
        VALUES (:mid, :lk, :sid, :tr, :td, :oj, 'pending', :ts)
    """), {
        "mid": menu_id, "lk": lineage_key, "sid": seat_id,
        "tr": trigger_reason, "td": detail_json, "oj": options_json, "ts": now,
    })

    if commit:
        conn.commit()

    return {
        "menu_id": menu_id,
        "lineage_key": lineage_key,
        "seat_id": seat_id,
        "trigger_reason": trigger_reason,
        "trigger_detail": trigger_detail or {},
        "options": options,
        "status": "pending",
        "created_at": now,
        "schema": SCHEMA,
    }


def _option_description(opt: str) -> str:
    descriptions = {
        "request_stronger_agent": "请求更强Agent（模型升级）：切换到更高能力的推理模型",
        "request_expert_knowledge": "请求领域专家知识包：注入领域先验/参考解/约束条件",
        "reallocate_seat": "重分配席位：将任务重新分配给其他Agent席位",
        "change_technical_route": "换技术路线：联 hypothesis_registry 立新假设，放弃当前技术路径",
    }
    return descriptions.get(opt, opt)


def record_disposition(
    conn,
    *,
    menu_id: str,
    disposition: str,
    note: str = "",
    commit: bool = True,
) -> dict[str, Any]:
    """记录协调者对升级菜单的处置决定。"""
    from sqlalchemy import text

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    conn.execute(text("""
        UPDATE upgrade_menus
        SET status = CASE WHEN :disp = 'dismiss' THEN 'dismissed' ELSE 'disposed' END,
            disposed_at = :ts,
            disposition = :disp,
            disposition_note = :note
        WHERE menu_id = :mid
    """), {"mid": menu_id, "disp": disposition, "ts": now, "note": note})

    if commit:
        conn.commit()

    return {
        "menu_id": menu_id,
        "disposition": disposition,
        "note": note,
        "disposed_at": now,
    }


# ─────────────────────────────── 假设注册与证伪 ───────────────────────────────

def register_hypothesis(
    conn,
    *,
    lineage_key: str,
    hypothesis_text: str,
    commit: bool = True,
) -> dict[str, Any]:
    """注册一条技术假设到 hypothesis_registry。"""
    from sqlalchemy import text

    hypothesis_id = f"hyp-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    conn.execute(text("""
        INSERT INTO hypothesis_registry
            (hypothesis_id, lineage_key, hypothesis_text, falsification_count, status, created_at)
        VALUES (:hid, :lk, :ht, 0, 'active', :ts)
    """), {"hid": hypothesis_id, "lk": lineage_key, "ht": hypothesis_text, "ts": now})

    if commit:
        conn.commit()

    return {
        "hypothesis_id": hypothesis_id,
        "lineage_key": lineage_key,
        "hypothesis_text": hypothesis_text,
        "falsification_count": 0,
        "status": "active",
        "created_at": now,
    }


def record_hypothesis_falsification(
    conn,
    *,
    hypothesis_id: str,
    commit: bool = True,
) -> dict[str, Any]:
    """记录假设被证伪一次。若达到 HYPOTHESIS_MAX_FALSIFICATIONS 次则自动标记为 blocked。"""
    from sqlalchemy import text

    row = conn.execute(text("""
        SELECT falsification_count, status FROM hypothesis_registry WHERE hypothesis_id = :hid
    """), {"hid": hypothesis_id}).mappings().first()

    if row is None:
        raise ValueError(f"hypothesis_id {hypothesis_id} not found")

    new_count = int(row["falsification_count"]) + 1
    new_status = str(row["status"])
    blocked_at = None

    if new_count >= HYPOTHESIS_MAX_FALSIFICATIONS and new_status == "active":
        new_status = "blocked"
        blocked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    conn.execute(text("""
        UPDATE hypothesis_registry
        SET falsification_count = :fc, status = :st, blocked_at = :ba
        WHERE hypothesis_id = :hid
    """), {"fc": new_count, "st": new_status, "ba": blocked_at, "hid": hypothesis_id})

    if commit:
        conn.commit()

    return {
        "hypothesis_id": hypothesis_id,
        "falsification_count": new_count,
        "status": new_status,
        "blocked": new_status == "blocked",
        "blocked_at": blocked_at,
    }


def is_hypothesis_blocked(
    conn,
    *,
    hypothesis_id: str,
) -> dict[str, Any]:
    """检查假设是否已被阻止（证伪次数达到上限）。"""
    from sqlalchemy import text

    row = conn.execute(text("""
        SELECT hypothesis_id, lineage_key, hypothesis_text, falsification_count, status, blocked_at
        FROM hypothesis_registry WHERE hypothesis_id = :hid
    """), {"hid": hypothesis_id}).mappings().first()

    if row is None:
        return {"found": False}

    blocked = str(row["status"]) == "blocked"
    return {
        "found": True,
        "hypothesis_id": str(row["hypothesis_id"]),
        "lineage_key": str(row["lineage_key"]),
        "hypothesis_text": str(row["hypothesis_text"]),
        "falsification_count": int(row["falsification_count"]),
        "status": str(row["status"]),
        "blocked": blocked,
        "blocked_at": str(row["blocked_at"]) if row.get("blocked_at") else None,
    }


# ─────────────────────────────── CLI 入口 ───────────────────────────────

def main():
    import argparse
    from sqlalchemy import create_engine

    parser = argparse.ArgumentParser(description="动态升级机制 CLI（IN59-A6）")
    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="初始化表结构")
    p_init.add_argument("--dsn", required=True)

    # check-failures
    p_cf = sub.add_parser("check-failures", help="检查谱系连续失败")
    p_cf.add_argument("--dsn", required=True)
    p_cf.add_argument("--lineage", required=True)
    p_cf.add_argument("--threshold", type=int, default=3)

    # check-no-progress
    p_np = sub.add_parser("check-no-progress", help="检查席位无进展")
    p_np.add_argument("--dsn", required=True)
    p_np.add_argument("--seat", required=True)
    p_np.add_argument("--lineage", required=True)
    p_np.add_argument("--threshold", type=int, default=2)

    # build-menu
    p_bm = sub.add_parser("build-menu", help="构建升级菜单")
    p_bm.add_argument("--dsn", required=True)
    p_bm.add_argument("--lineage", required=True)
    p_bm.add_argument("--reason", required=True, choices=TRIGGER_REASONS)
    p_bm.add_argument("--seat", default=None)

    # register-hypothesis
    p_rh = sub.add_parser("register-hypothesis", help="注册假设")
    p_rh.add_argument("--dsn", required=True)
    p_rh.add_argument("--lineage", required=True)
    p_rh.add_argument("--text", required=True)

    # falsify
    p_fal = sub.add_parser("falsify", help="证伪假设")
    p_fal.add_argument("--dsn", required=True)
    p_fal.add_argument("--hypothesis-id", required=True)

    # check-blocked
    p_cb = sub.add_parser("check-blocked", help="检查假设是否被阻止")
    p_cb.add_argument("--dsn", required=True)
    p_cb.add_argument("--hypothesis-id", required=True)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    engine = create_engine(args.dsn)
    with engine.connect() as conn:
        if args.command == "init":
            conn.execute(text(DDL))
            conn.commit()
            print("Tables initialized.")
            return

        if args.command == "check-failures":
            result = check_lineage_failures(conn, lineage_key=args.lineage, threshold=args.threshold)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return

        if args.command == "check-no-progress":
            result = check_seat_no_progress(conn, seat_id=args.seat, lineage_key=args.lineage, threshold=args.threshold)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return

        if args.command == "build-menu":
            result = build_upgrade_menu(conn, lineage_key=args.lineage, trigger_reason=args.reason, seat_id=args.seat)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return

        if args.command == "register-hypothesis":
            result = register_hypothesis(conn, lineage_key=args.lineage, hypothesis_text=args.text)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return

        if args.command == "falsify":
            result = record_hypothesis_falsification(conn, hypothesis_id=args.hypothesis_id)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return

        if args.command == "check-blocked":
            result = is_hypothesis_blocked(conn, hypothesis_id=args.hypothesis_id)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return


if __name__ == "__main__":
    main()
