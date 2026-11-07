#!/usr/bin/env python3
"""adoption_stall 体检/修复器（IN27 影子诊断单 a151356df79cb336 的执行面）。

只做两件事，都用 `backend/api/queue_adoption.py` 的纯判定函数给出：

1. **体检（默认只读）**：把 queued 池拆成 adoptable / parked，报
   `oldest_adoptable_minutes`（真正的「入池无人领」年龄）、按 model 引脚
   报车道活性与漂移嫌疑、列出孤儿引脚。
2. **修复（`--apply`）**：只执行两类可逆动作——
   * `unpark`：`suspended_at IS NOT NULL` 却停在 `status='queued'` 的单恢复成
     `status='suspended'`（恢复 `/resume` 可逆性，关掉停挂单自己写下的冻结洞）；
   * `unpin`：孤儿引脚（超阈值排队且车道近期零认领）解钉成 `model=NULL`，
     原 model 写进 `result.model_unpinned_from` 可回滚。

阈值不在本工具里改（`--threshold-minutes` 默认 240，与器官同值，只用于判定）。

用法：
    python3 tools/queue_adoption_audit.py --json-out /tmp/adoption.json
    python3 tools/queue_adoption_audit.py --apply --json-out /tmp/adoption_healed.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_adoption_module():
    """按路径加载纯逻辑模块：backend/api 是 FastAPI 包，import 包会拉起整栈
    （psycopg2/DB 连接），审计工具只需要那几个纯函数。"""
    import importlib.util
    path = REPO / "backend" / "api" / "queue_adoption.py"
    spec = importlib.util.spec_from_file_location("queue_adoption", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_qa = _load_adoption_module()
LANE_LIVENESS_HOURS = _qa.LANE_LIVENESS_HOURS
THRESHOLD_MINUTES = _qa.THRESHOLD_MINUTES
heal_plan, pin_audit, split_backlog = _qa.heal_plan, _qa.pin_audit, _qa.split_backlog

DB = os.environ.get("TP_PG_DB", "tensorplatform")
SEP = "\x02"
FIELDS = ["id", "status", "created_at", "suspended_at", "model"]


def _psql(sql: str) -> list[list[str]]:
    """读：只读 SELECT。写：调用方保证只走 --apply 的窄 SQL。"""
    base = ["psql", "-d", DB, "-t", "-A", "-F", SEP, "-v", "ON_ERROR_STOP=1", "-c", sql]
    if os.environ.get("TP_PG_PASSWORD") or os.environ.get("PGPASSWORD"):
        cmd = base
    else:  # 与影子协调官同一条只读通道（runuser 到 postgres）
        cmd = ["runuser", "-u", "postgres", "--"] + base
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"psql 失败: {r.stderr.strip()[:400]}")
    return [ln.split(SEP) for ln in r.stdout.splitlines() if ln.strip()]


def _dicts(rows, cols):
    return [dict(zip(cols, row)) for row in rows]


def collect() -> dict:
    rows = _dicts(_psql("SELECT id, status, created_at, suspended_at, model "
                        "FROM tensor_dev_missions WHERE status = 'queued'"), FIELDS)
    residents = _dicts(_psql("SELECT worker_id, model, role, enabled, state "
                             "FROM factory_resident_workers"),
                       ["worker_id", "model", "role", "enabled", "state"])
    claims = _dicts(_psql("SELECT claimed_by, model, COALESCE(finished_at, created_at) "
                          "FROM tensor_dev_missions WHERE claimed_by IS NOT NULL "
                          "AND COALESCE(finished_at, created_at) > now() - interval '7 days'"),
                    ["claimed_by", "model", "claimed_at"])
    now = _psql("SELECT now()")[0][0]
    return {"now": now, "rows": rows, "residents": residents, "claims": claims}


def snapshot(snap: dict, threshold: float, liveness_hours: float) -> dict:
    now = snap["now"]
    return {
        "as_of": now,
        "sql": {"queued": "SELECT id, status, created_at, suspended_at, model "
                          "FROM tensor_dev_missions WHERE status = 'queued'"},
        "threshold_minutes": threshold,
        "liveness_hours": liveness_hours,
        "backlog": split_backlog(snap["rows"], now, threshold),
        "pins": pin_audit(snap["rows"], snap["residents"], snap["claims"],
                          now, threshold, liveness_hours),
        "plan": heal_plan(snap["rows"], snap["residents"], snap["claims"],
                          now, threshold, liveness_hours),
    }


def _log(mission_id: int, action: str, detail: dict) -> None:
    """mission_lifecycle_log 审计行（action 取现有词表内的值，op 说明具体动作）。"""
    payload = json.dumps(detail, ensure_ascii=False).replace("'", "''")
    _psql("INSERT INTO mission_lifecycle_log (mission_id, action, detail) VALUES "
          f"({int(mission_id)}, '{action}', '{payload}'::jsonb)")


def apply_plan(plan: dict) -> dict:
    """执行可逆修复：unpark + unpin；每步都落 mission_lifecycle_log 审计。"""
    done = {"unparked": [], "unpinned": [], "skipped": []}
    for item in plan.get("unpark", []):
        mid = int(item["mission_id"])
        rows = _psql(
            "WITH u AS ("
            "  UPDATE tensor_dev_missions SET status='suspended', claim_token=NULL, claimed_by=NULL "
            f"  WHERE id={mid} AND status='queued' AND suspended_at IS NOT NULL RETURNING id"
            ") SELECT id FROM u")
        if not rows:
            done["skipped"].append({"mission_id": mid, "action": "unpark", "why": "state changed"})
            continue
        _log(mid, "suspend", {
            "op": "restore_parked_status",
            "reason": "IN27 adoption_stall: suspended_at set but status=queued "
                      "(not resumable, not claimable) -> restore suspended",
        })
        done["unparked"].append(mid)

    for item in plan.get("unpin", []):
        mid, model = int(item["mission_id"]), item["from_model"]
        rows = _psql(
            "WITH u AS ("
            "  UPDATE tensor_dev_missions SET model = NULL, "
            "    result = COALESCE(result, '{}'::jsonb) || jsonb_build_object("
            "      'model_unpinned_from', model, 'model_unpinned_at', now()::text,"
            "      'model_unpinned_reason', 'orphan pin (IN27 adoption_stall): no live claimant lane')"
            f"  WHERE id={mid} AND status='queued' AND model = '{model}' RETURNING id"
            ") SELECT id FROM u")
        if not rows:
            done["skipped"].append({"mission_id": mid, "action": "unpin", "why": "state changed"})
            continue
        _log(mid, "orphan_detect", {
            "op": "unpin_orphan_model", "model": model,
            "reason": "lane has no recent claim evidence (IN27 adoption_stall)",
        })
        done["unpinned"].append({"mission_id": mid, "from_model": model})
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--apply", action="store_true", help="执行可逆修复（unpark/unpin）")
    ap.add_argument("--threshold-minutes", type=float, default=THRESHOLD_MINUTES)
    ap.add_argument("--liveness-hours", type=float, default=LANE_LIVENESS_HOURS)
    args = ap.parse_args()

    snap = collect()
    out = {"before": snapshot(snap, args.threshold_minutes, args.liveness_hours)}
    if args.apply:
        out["applied"] = apply_plan(out["before"]["plan"])
        out["after"] = snapshot(collect(), args.threshold_minutes, args.liveness_hours)
        out["healed"] = out["after"]["backlog"]["violating"] is False
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out.get("after", out["before"]), ensure_ascii=False, indent=2))
    tail = out.get("applied")
    if tail:
        print(f"applied: unparked={len(tail['unparked'])} unpinned={len(tail['unpinned'])} "
              f"skipped={len(tail['skipped'])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())