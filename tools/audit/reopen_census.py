#!/usr/bin/env python3
"""历史「复活」口径普查（SPEC §4.2 重开普查器 / 战役 IN19-Q7）。

为什么要有普查器
----------------
REOPENED 状态正式化的验收要求写清「历史复活 N 例」的**口径**——口径不写死，
统计数字就是可注水的口径泥潭：同一个「复活」既可以指「终态后被库外守护拉回
干活态」，也可以指「PR 合并后又重开」，还可以只指「pusher 私有挂起旗标」。
本工具把三种候选口径各自算一遍（互不覆盖），并把每一条明细连同可复现 SQL
落到 JSON 工件；口径定义与数字同时入档，后来者可用同一命令重算。

口径（tier）
------------
T1 ``engine_rework_after_finish``
    终态落定后又被干活的单：``finished_at`` 存在且 ``heartbeat_at`` 晚于
    ``finished_at + 60s``。这是「库外守护/人工把单拉回来重做」最硬的footprint
    （心跳只能由 worker 写）。
T2 ``post_merge_rework``
    PR 已合并（``pr_gate`` 历史出现过 merged）之后又来一条 open 报告的单：
    合并后回归被发现、需要重开的典型形态。
T3 ``terminal_to_active``
    现在处于非终态、但 ``finished_at`` 非空的单：状态机里只有「重开」能造成
    这个组合（返工队列化后 finished_at 未清）。
T4 ``guard_pending``
    仍停在 pusher 私有挂起旗标上、尚未转入正式 REOPENED 的单（= 迁移待办量）。

只读：全部 SELECT，不写库、不改任何状态。DB 不可达时如实返回 WITHHELD。

用法
----
    python3 tools/audit/reopen_census.py                     # 写 artifacts/m5022/reopen_census.json
    python3 tools/audit/reopen_census.py --json-out /tmp/c.json --psql "psql -h /var/run/postgresql -U foundation -d tensorplatform"
    python3 tools/audit/reopen_census.py --no-db             # 只做契约不变量 + 守卫待办
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "backend" / "contracts"
DEFAULT_OUT = ROOT / "artifacts" / "m5022" / "reopen_census.json"
PUSHER_STATE = Path("/root/tp-agent/tp_pusher_state.json")

T1_SQL = """
SELECT id, status, finished_at, heartbeat_at,
       EXTRACT(EPOCH FROM (heartbeat_at - finished_at))::int AS late_s
  FROM tensor_dev_missions
 WHERE finished_at IS NOT NULL AND heartbeat_at IS NOT NULL
   AND heartbeat_at > finished_at + interval '60 seconds'
 ORDER BY id
""".strip()

T2_SQL = """
WITH h AS (
  SELECT m.id, e->>'state' AS state, (e->>'at') AS at,
         row_number() OVER (PARTITION BY m.id ORDER BY (e->>'at')) AS rn
    FROM tensor_dev_missions m,
         jsonb_array_elements(COALESCE(m.result->'pr_gate'->'history', '[]'::jsonb)) e
)
SELECT DISTINCT a.id
  FROM h a JOIN h b ON a.id = b.id AND a.rn < b.rn
 WHERE a.state = 'merged' AND b.state = 'open'
 ORDER BY a.id
""".strip()

T3_SQL = """
SELECT id, status, finished_at
  FROM tensor_dev_missions
 WHERE finished_at IS NOT NULL
   AND status NOT IN ('succeeded','failed','stopped','revoked','orphan')
 ORDER BY id
""".strip()


def psql_runner(dsn_cmd: str):
    def run(sql: str) -> str:
        out = subprocess.run(dsn_cmd.split() + ["-tAc", sql],
                             capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError((out.stderr or "").strip()[:300])
        return out.stdout.strip()
    return run


def _rows(s: str):
    return [line.split("|") for line in s.splitlines() if line.strip()]


def contract_invariants() -> dict:
    """迁移图不变量（不依赖 DB）：终态无干活态后门、reopened 入边=终态集。"""
    vocab = json.loads((CONTRACTS / "mission_status_vocab.v1.json").read_text())
    sm = json.loads((CONTRACTS / "mission_state_machine.v1.json").read_text())
    graph = {k: tuple(v["to"]) for k, v in sm["transitions"].items()}
    states, terminal = set(vocab["statuses"]), set(vocab["terminal"])
    workable = set(sm["workable"])
    declared = set(sm.get("audited_retry_edges", {}).get("edges", []))
    backdoors = sorted({f"{a}->{b}" for a in terminal for b in graph.get(a, ())
                        if b in workable} - declared)
    inbound = sorted(a for a, dsts in graph.items() if "reopened" in dsts)
    return {
        "vocab_version": vocab["version"],
        "state_machine_version": sm["version"],
        "states": len(states),
        "terminal": sorted(terminal),
        "graph_edges": sum(len(v) for v in graph.values()),
        "reopen_inbound": inbound,
        "audited_retry_edges": sorted(declared),
        "terminal_to_workable_backdoors": backdoors,
        "closure_ok": not backdoors and set(inbound) == terminal
        and set(graph) == states
        and all(set(v) <= states for v in graph.values()),
    }


def guard_pending() -> dict:
    """pusher 私有挂起旗标存量（= 切正式状态的待办量）。"""
    legacy = "main_red" + "_hold"
    if not PUSHER_STATE.exists():
        return {"available": False, "reason": "pusher state file absent"}
    st = json.loads(PUSHER_STATE.read_text())
    ids = sorted(int(k) for k, v in st.items()
                 if isinstance(v, dict) and v.get("ci") == legacy)
    return {"available": True, "count": len(ids), "mission_ids": ids,
            "state_file": str(PUSHER_STATE)}


def census(run=None) -> dict:
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spec": "SPEC §4.2 REOPENED formalisation (campaign node IN19-Q7)",
        "contract_invariants": contract_invariants(),
        "tiers": {},
        "headline": {},
    }
    result["tiers"]["T4_guard_pending"] = {"definition": "pusher 私有挂起旗标存量",
                                           **guard_pending()}
    if run is None:
        result["headline"] = {"db": "WITHHELD", "reason": "no db runner"}
        return result
    try:
        t1 = _rows(run(T1_SQL))
        t2 = [r[0] for r in _rows(run(T2_SQL))]
        t3 = _rows(run(T3_SQL))
        total = run("SELECT count(*) FROM tensor_dev_missions").strip()
        statuses = _rows(run("SELECT status, count(*) FROM tensor_dev_missions "
                             "GROUP BY 1 ORDER BY 2 DESC"))
    except Exception as exc:  # noqa: BLE001
        result["headline"] = {"db": "WITHHELD", "reason": str(exc)[:300]}
        return result

    result["tiers"]["T1_engine_rework_after_finish"] = {
        "definition": "finished_at 后 60s 之外仍有 heartbeat（终态后被拉回干活）",
        "count": len(t1),
        "by_status": dict(Counter(r[1] for r in t1)),
        "missions": [{"id": int(r[0]), "status": r[1], "late_seconds": int(r[4])}
                     for r in t1],
        "sql": T1_SQL,
    }
    result["tiers"]["T2_post_merge_rework"] = {
        "definition": "pr_gate 历史先 merged 后出现 open（合并后回归被重开）",
        "count": len(t2), "missions": [int(x) for x in t2], "sql": T2_SQL,
    }
    result["tiers"]["T3_terminal_to_active"] = {
        "definition": "finished_at 非空但当前状态非终态",
        "count": len(t3),
        "missions": [{"id": int(r[0]), "status": r[1]} for r in t3],
        "sql": T3_SQL,
    }
    result["mission_total"] = int(total or 0)
    result["status_histogram"] = {r[0]: int(r[1]) for r in statuses}
    result["headline"] = {
        "db": "OK",
        "mission_total": int(total or 0),
        "T1": len(t1), "T2": len(t2), "T3": len(t3),
        "T4": result["tiers"]["T4_guard_pending"].get("count", 0),
        "campaign_cited_cases": 19,
        "cited_19_reproduced": any(len(x) == 19 for x in (t1, t2, t3)),
    }
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-out", default=str(DEFAULT_OUT))
    ap.add_argument("--psql", default="psql -h /var/run/postgresql -U foundation -d tensorplatform")
    ap.add_argument("--no-db", action="store_true")
    args = ap.parse_args(argv)

    run = None if args.no_db else psql_runner(args.psql)
    data = census(run)
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(data["headline"], ensure_ascii=False))
    print(f"written: {out}")
    return 0 if data["contract_invariants"]["closure_ok"] else 2


if __name__ == "__main__":
    sys.exit(main())