#!/usr/bin/env python3
"""v0 影子协调官退役 + 写者唯一性审计（IN28-N06）。

背景
----
`coordinator_shadow` v0（``/root/tp-agent/coordinator_shadow.py``）与 v1
（``tools.coordinator_shadow.daemon``）曾在同一张表上共存：v0 用
``id = (SELECT max(id)+1)`` 手算主键、**不写** ``beat_id``/``schema``；v1 用序列 + 30 min 拍槽。
两条腿一起写 → 每次 v0 插入都可能与序列撞主键，且账本里出现两种格式的行。

写者指纹（本模块的判据）
------------------------
| 指纹 | 行特征 | 归属 |
|---|---|---|
| ``v1`` | ``beat_id`` 非空 **且** ``schema='coordinator-shadow-beat/1'`` | v1 常驻 |
| ``legacy`` | 任一为空 | v0（或任何未走 v1 账本层的写者） |

退役判据是**双条件**，缺一条都不算 PASS：
1. 进程面：``/proc`` 里不存在 v0 进程（cmdline 命中 v0 脚本名，且不是 ``tools.coordinator_shadow``）；
2. 账本面：退役时刻之后再无 ``legacy`` 行写入，且 v1 常驻进程仍在（唯一写者）。

为什么不用"杀了就算完"
----------------------
v0 是一个 ``while True: sleep(1800)`` 的自续进程，可以有多实例（本单实跑发现有 2 个）。
只杀一个，另一条腿继续写——所以 ``retire`` 是**按指纹全杀 + 复扫确认**，并把每次 SIGTERM/SIGKILL
与复扫结果都写进 artifact。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import time
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

SCHEMA = "coordinator-shadow-retirement/1"
V1_SCHEMA = "coordinator-shadow-beat/1"
V0_SCRIPT_MARK = "coordinator_shadow.py"
V1_CMD_MARK = "tools.coordinator_shadow.daemon"


def _iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 进程面
# ---------------------------------------------------------------------------


def _cmdline(pid: int, proc_root: str = "/proc") -> list[str]:
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as fh:
            raw = fh.read()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _start_time(pid: int, proc_root: str = "/proc") -> float | None:
    try:
        return os.stat(os.path.join(proc_root, str(pid))).st_mtime
    except OSError:
        return None


def _alive(pid: int, proc_root: str = "/proc") -> bool:
    """活着 = /proc 条目在 **且** 不是僵尸（Z 态进程不再写任何东西）。"""
    path = os.path.join(proc_root, str(pid))
    try:
        with open(os.path.join(path, "stat")) as fh:
            data = fh.read()
        return data[data.rindex(")") + 2] != "Z"
    except (OSError, IndexError, ValueError):
        return os.path.exists(path)


def scan_processes(proc_root: str = "/proc") -> dict[str, Any]:
    """按 cmdline 指纹分类影子进程：v0（legacy 脚本）/ v1（daemon 模块）。"""
    v0: list[dict[str, Any]] = []
    v1: list[dict[str, Any]] = []
    for name in sorted(os.listdir(proc_root)):
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == os.getpid():
            continue
        cmd = _cmdline(pid, proc_root)
        if not cmd:
            continue
        joined = " ".join(cmd)
        if V1_CMD_MARK in joined:
            v1.append({"pid": pid, "cmd": joined[:300],
                       "started": _iso(_from_epoch(_start_time(pid, proc_root)))})
        elif any(V0_SCRIPT_MARK in part for part in cmd) and "tools.coordinator_shadow" not in joined:
            v0.append({"pid": pid, "cmd": joined[:300],
                       "started": _iso(_from_epoch(_start_time(pid, proc_root)))})
    return {"v0": v0, "v1": v1, "v0_alive": len(v0) > 0, "v1_alive": len(v1) > 0}


def _from_epoch(value: float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def retire_processes(pids: Sequence[int], grace_seconds: float = 10.0,
                     proc_root: str = "/proc") -> list[dict[str, Any]]:
    """SIGTERM → 等待 → 仍在则 SIGKILL；返回每个 PID 的动作与结局（永不静默）。"""
    out: list[dict[str, Any]] = []
    for pid in pids:
        entry: dict[str, Any] = {"pid": pid, "actions": []}
        try:
            os.kill(pid, signal.SIGTERM)
            entry["actions"].append("SIGTERM")
        except ProcessLookupError:
            entry["actions"].append("already-gone")
            entry["gone"] = True
            out.append(entry)
            continue
        except PermissionError as exc:
            entry["error"] = f"PermissionError: {exc}"
        deadline = time.time() + grace_seconds
        while time.time() < deadline:
            if not _alive(pid, proc_root):
                break
            time.sleep(0.1)
        if _alive(pid, proc_root):
            try:
                os.kill(pid, signal.SIGKILL)
                entry["actions"].append("SIGKILL")
            except ProcessLookupError:
                pass
            except PermissionError as exc:
                entry["error"] = f"PermissionError: {exc}"
            time.sleep(0.2)
        entry["gone"] = not _alive(pid, proc_root)
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# 账本面
# ---------------------------------------------------------------------------


def writer_of(row: Mapping[str, Any]) -> str:
    """写者指纹：v1 需要 ``beat_id`` 与 ``schema`` 同时就位（缺一即 legacy）。"""
    if row.get("beat_id") and row.get("schema") == V1_SCHEMA:
        return "v1"
    return "legacy"


def audit_writes(rows: Sequence[Mapping[str, Any]], retired_at: datetime | None = None,
                 ) -> dict[str, Any]:
    """按写者指纹统计账本行；``retired_at`` 之后出现的 legacy 行即退役失败。"""
    counts = {"v1": 0, "legacy": 0}
    after = {"v1": 0, "legacy": 0}
    legacy_after: list[dict[str, Any]] = []
    first_seen: dict[str, Any] = {}
    for row in rows:
        w = writer_of(row)
        counts[w] += 1
        ts = _ts(row.get("ts"))
        if ts is not None:
            first_seen.setdefault(w, _iso(ts))
        if retired_at is not None and ts is not None and ts >= retired_at:
            after[w] += 1
            if w == "legacy":
                legacy_after.append({"id": row.get("id"), "ts": _iso(ts),
                                     "model": row.get("model")})
    total = sum(counts.values())
    return {
        "rows": total,
        "counts": counts,
        "after_retirement": after,
        "legacy_after_retirement": legacy_after,
        "legacy_after_retirement_count": len(legacy_after),
        "v1_share": None if total == 0 else counts["v1"] / total,
    }


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------


def load_ledger(dsn: str, since: datetime | None = None) -> list[dict[str, Any]]:
    import psycopg

    sql = ("SELECT id, ts, model, beat_id, schema, urgent, verdict, error FROM coordinator_shadow")
    params: dict[str, Any] = {}
    if since is not None:
        sql += " WHERE ts >= %(since)s"
        params["since"] = since
    sql += " ORDER BY ts"
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [{"id": r[0], "ts": r[1], "model": r[2], "beat_id": r[3], "schema": r[4],
                     "urgent": r[5], "verdict": r[6], "error": r[7]} for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# 总装
# ---------------------------------------------------------------------------


def audit(dsn: str | None, retired_at: datetime | None, *,
          do_retire: bool = False, grace_seconds: float = 10.0,
          settle_seconds: float = 0.0, proc_root: str = "/proc") -> dict[str, Any]:
    """退役 + 双条件审计；``settle_seconds`` 用于杀掉后等一拍再复扫账本。"""
    before = scan_processes(proc_root)
    actions: list[dict[str, Any]] = []
    if do_retire and before["v0"]:
        actions = retire_processes([p["pid"] for p in before["v0"]], grace_seconds, proc_root)
    if settle_seconds:
        time.sleep(settle_seconds)
    after = scan_processes(proc_root)

    ledger_rows: list[dict[str, Any]] = []
    ledger_error = None
    if dsn:
        try:
            ledger_rows = load_ledger(dsn)
        except Exception as exc:  # pragma: no cover - 生产库不可达
            ledger_error = f"{type(exc).__name__}: {exc}"
    writes = audit_writes(ledger_rows, retired_at)
    stamp = retired_at or datetime.now(timezone.utc)
    criteria = {
        "v0_process_absent": not after["v0_alive"],
        "v1_alive": after["v1_alive"],
        "v0_writes_after_retirement": writes["legacy_after_retirement_count"],
        "v0_writes_zero": writes["legacy_after_retirement_count"] == 0,
        "sole_writer": bool(after["v1_alive"] and not after["v0_alive"]),
    }
    criteria["pass"] = bool(criteria["v0_process_absent"] and criteria["v0_writes_zero"]
                            and criteria["sole_writer"] and ledger_error is None)
    return {
        "schema": SCHEMA,
        "generated_at": _iso(datetime.now(timezone.utc)),
        "retired_at": _iso(stamp),
        "processes_before": before,
        "processes_after": after,
        "retire_actions": actions,
        "retired": do_retire,
        "writes": writes,
        "ledger_error": ledger_error,
        "criteria": criteria,
        "writer_fingerprint_rule": {
            "v1": "beat_id is not null and schema = 'coordinator-shadow-beat/1'",
            "legacy": "otherwise (v0 wrote max(id)+1 with no beat_id/schema)",
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="retire coordinator_shadow v0 + audit writers")
    ap.add_argument("--dsn", default=os.environ.get("TP_DSN")
                    or "dbname=tensorplatform user=postgres host=/var/run/postgresql")
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--retire", action="store_true", help="真的发 SIGTERM（默认只审计）")
    ap.add_argument("--retired-at", default=None, help="退役时刻 ISO（缺省=现在）")
    ap.add_argument("--grace-seconds", type=float, default=10.0)
    ap.add_argument("--settle-seconds", type=float, default=0.0)
    ap.add_argument("--out", default="artifacts/coordinator_shadow/v0_retirement.json")
    args = ap.parse_args(list(argv) if argv is not None else None)
    retired_at = _ts(args.retired_at) if args.retired_at else datetime.now(timezone.utc)
    report = audit(None if args.no_db else args.dsn, retired_at, do_retire=args.retire,
                   grace_seconds=args.grace_seconds, settle_seconds=args.settle_seconds)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({"criteria": report["criteria"],
                      "writes": report["writes"]["counts"],
                      "v0_before": len(report["processes_before"]["v0"]),
                      "v1_alive": report["processes_after"]["v1_alive"],
                      "artifact": os.path.realpath(args.out)}, ensure_ascii=False, indent=2))
    return 0 if report["criteria"]["pass"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())