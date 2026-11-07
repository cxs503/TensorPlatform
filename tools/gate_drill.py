#!/usr/bin/env python3
"""G13–G20 六门放门演练（IN20-R2 验收）。

三段，全部可复算：

  A. 生产矩阵（只读）：对 campaign_nodes 里 plan 13..20 的 blocked 门下判，
     记录 upstream / 判据工件 / owner_only 三项检查与 action，不写库；
     前后对全表 (id,status,node_type) 快照做 sha256，证明「演练不动生产」。
  B. 沙箱写入路径（隔离 schema `gate_drill`）：把六门 + 其上游节点复制进
     gate_drill.campaign_nodes，判据工件落到临时 staging 目录（内容标注
     synthetic），跑同一份 run_once → 满足者释放、G16（上游在飞）继续等、
     G20（治理门）只标 owner_only；随后逐条 rollback 回 blocked。
  C. 审计链核对：沙箱审计表按 action 计数 + 逐行字段完整（node/from/to/reason/
     actor/run_id/detail），并核对 release→rollback 配对。

用法：
    runuser -u postgres -- python3 tools/gate_drill.py \
        --json-out artifacts/gate_auto_release_drill.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from ops.taskplane.gate_auto_release import (  # noqa: E402
    ACTION_HOLD_OWNER_ONLY,
    ACTION_RELEASE,
    ACTION_ROLLBACK,
    ACTION_WAIT,
    FROM_STATUS,
    RELEASED_STATUS,
    PsqlStore,
    evaluate_gate,
    parse_criteria,
    run_once,
)

PLAN_RANGE = (13, 20)          # G13..G20 所在 plan
SANDBOX = "gate_drill"


def _psql(store: PsqlStore, sql: str) -> str:
    return store._query(sql)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_prod(store: PsqlStore) -> list[dict]:
    return store.nodes()


def select_gates(nodes: list[dict], gate_ids: list[int] | None) -> list[dict]:
    gates = [n for n in nodes if n.get("node_type") == "gate"
             and n.get("status") == FROM_STATUS
             and (n.get("plan_id") or 0) >= PLAN_RANGE[0]
             and (n.get("plan_id") or 0) <= PLAN_RANGE[1]]
    if gate_ids:
        gates = [g for g in gates if int(g["id"]) in gate_ids]
    return sorted(gates, key=lambda n: int(n["id"]))


def production_matrix(store: PsqlStore, nodes: list[dict], gates: list[dict],
                      roots: list[str]) -> dict:
    by_id = {int(n["id"]): n for n in nodes}
    before = store.snapshot()
    rows = []
    for gate in gates:
        decision = evaluate_gate(gate, by_id, roots)
        rec = decision.to_dict()
        rec["upstream_status"] = {
            str(d): (by_id[int(d)].get("status") if int(d) in by_id else "missing")
            for d in (gate.get("dependencies") or [])
        }
        rows.append(rec)
    after = store.snapshot()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "roots": roots,
        "snapshot_before_sha256": _sha(before),
        "snapshot_after_sha256": _sha(after),
        "production_untouched": before == after,
        "gates": rows,
        "counts": {
            ACTION_RELEASE: sum(1 for r in rows if r["action"] == ACTION_RELEASE),
            ACTION_WAIT: sum(1 for r in rows if r["action"] == ACTION_WAIT),
            ACTION_HOLD_OWNER_ONLY: sum(1 for r in rows
                                        if r["action"] == ACTION_HOLD_OWNER_ONLY),
        },
    }


def rehearsal(store: PsqlStore, prod_nodes: list[dict], gates: list[dict]) -> dict:
    """在 gate_drill schema 里跑真写入（同一份 SQL/逻辑），随后全量回滚。"""
    gate_ids = [int(g["id"]) for g in gates]
    dep_ids = sorted({int(d) for g in gates for d in (g.get("dependencies") or [])})
    keep = gate_ids + dep_ids

    _psql(store, f"DROP SCHEMA IF EXISTS {SANDBOX} CASCADE;")
    _psql(store, f"CREATE SCHEMA {SANDBOX};")
    _psql(store, f"CREATE TABLE {SANDBOX}.campaign_nodes AS "
                 f"SELECT * FROM campaign_nodes WHERE id IN ({','.join(map(str, keep))});")
    _psql(store, f"ALTER TABLE {SANDBOX}.campaign_nodes ADD PRIMARY KEY (id);")

    sandbox = PsqlStore(table=f"{SANDBOX}.campaign_nodes",
                        audit=f"{SANDBOX}.gate_release_audit",
                        host=store.conn[0] == "-h" and store.conn[1] or "",
                        user=store.conn[store.conn.index("-U") + 1],
                        dbname=store.conn[store.conn.index("-d") + 1])
    sandbox.ensure_audit_table()

    sb_nodes = sandbox.nodes()
    by_id = {int(n["id"]): n for n in sb_nodes}
    sb_gates = [n for n in sb_nodes if n.get("node_type") == "gate"
                and n.get("status") == FROM_STATUS]

    # 判据工件 staging：把每门声明的路径在临时根下造出「合成证据」
    staged: list[dict] = []
    tmpdir = tempfile.mkdtemp(prefix="gate_drill_artifacts_")
    for gate in sb_gates:
        meta = gate.get("metadata") or {}
        crit = parse_criteria(meta, gate.get("name") or "")
        repo = str(meta.get("repo") or "").strip()
        for pattern in crit.artifacts:
            # 证据落点按门声明的仓分层（daemon 的 repo 归属校验据此识别，
            # 与生产 /root/tp-agent/work/<mission>/<repo>/artifacts/... 同形）
            target = Path(tmpdir) / repo / pattern if repo else Path(tmpdir) / pattern
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "synthetic": True,
                "purpose": "sandbox write-path rehearsal for gate_auto_release",
                "gate_node_id": int(gate["id"]),
                "gate_name": gate.get("name"),
                "declared_pattern": pattern,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            staged.append({"node_id": int(gate["id"]), "pattern": pattern,
                           "staged_path": str(target)})

    # 解析根：临时根本身 + 每门声明的仓子根（与生产 work/<mission>/<repo> 同形）
    staged_repos = sorted({str((Path(tmpdir) / str((g.get("metadata") or {}).get("repo") or "")))
                           for g in sb_gates if (g.get("metadata") or {}).get("repo")})
    sandbox_roots = [tmpdir] + staged_repos
    run = run_once(sandbox, roots=sandbox_roots, actor="gate-drill",
                   run_id="gate-drill-sandbox")

    after_run = {int(n["id"]): n.get("status") for n in sandbox.nodes()}
    released = run["released"]
    audit_after_run = sandbox.audit_rows(limit=500)

    rollbacks = []
    for audit_row in sorted(audit_after_run, key=lambda r: int(r["id"])):
        if audit_row["action"] == ACTION_RELEASE:
            rollbacks.append(sandbox.rollback(int(audit_row["id"]),
                                              actor="gate-drill-manual",
                                              run_id="gate-drill-rollback"))
    final_status = {int(n["id"]): n.get("status") for n in sandbox.nodes()}
    audit_final = sandbox.audit_rows(limit=500)

    # 审计链体检
    def _complete(row: dict) -> bool:
        return all(row.get(k) is not None for k in
                   ("node_id", "action", "from_status", "to_status", "reason", "actor", "run_id")) \
            and isinstance(row.get("detail"), dict)

    by_action: dict[str, int] = {}
    for row in audit_final:
        by_action[row["action"]] = by_action.get(row["action"], 0) + 1

    rollback_pairs_ok = all(
        any(r["rollback_of"] == rel["id"] for r in audit_final if r["action"] == ACTION_ROLLBACK)
        for rel in audit_final if rel["action"] == ACTION_RELEASE
    )
    restored = all(final_status[nid] == FROM_STATUS for nid in released)

    _psql(store, f"DROP SCHEMA {SANDBOX} CASCADE;")

    return {
        "sandbox_schema": SANDBOX,
        "artifact_root": tmpdir,
        "artifact_roots": sandbox_roots,
        "staged_artifacts": staged,
        "run": run,
        "status_after_run": {str(k): v for k, v in after_run.items()},
        "rollbacks": rollbacks,
        "status_after_rollback": {str(k): v for k, v in final_status.items()},
        "audit_action_counts": by_action,
        "audit_rows_complete": all(_complete(r) for r in audit_final),
        "audit_rows": audit_final,
        "release_rollback_pairs_ok": rollback_pairs_ok,
        "all_released_restored_to_blocked": restored,
        "pass": bool(released) and restored and rollback_pairs_ok
                and all(_complete(r) for r in audit_final)
                and by_action.get(ACTION_ROLLBACK, 0) == len(released),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="G13–G20 六门放门演练")
    ap.add_argument("--json-out", default="artifacts/gate_auto_release_drill.json")
    ap.add_argument("--gate-ids", default="", help="逗号分隔；默认取 plan 13..20 全部 blocked 门")
    ap.add_argument("--artifact-roots", default=None)
    ap.add_argument("--pg-host", default="")
    ap.add_argument("--pg-user", default="postgres")
    ap.add_argument("--pg-db", default="tensorplatform")
    args = ap.parse_args(argv)

    roots = ([p for p in args.artifact_roots.split(os.pathsep) if p]
             if args.artifact_roots
             else [str(REPO), "/root/tp-agent/work/*"])
    gate_ids = [int(x) for x in args.gate_ids.split(",") if x.strip()] or None

    store = PsqlStore(host=args.pg_host, user=args.pg_user, dbname=args.pg_db)
    nodes = load_prod(store)
    gates = select_gates(nodes, gate_ids)

    matrix = production_matrix(store, nodes, gates, roots)
    rehearse = rehearsal(store, nodes, gates)

    # 生产侧「等」的审计链证据：daemon 真跑一轮（--once）落在生产审计表，
    # 由 CLI 负责；本演练只读，故把两者分开记录。
    out = {
        "schema": "gate_auto_release_drill/1.0",
        "mission_id": 5046,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gate_ids": [int(g["id"]) for g in gates],
        "plan_range": list(PLAN_RANGE),
        "production": matrix,
        "rehearsal": rehearse,
        "verdict": {
            "gate_count": len(gates),
            "prod_release_candidates": matrix["counts"][ACTION_RELEASE],
            "prod_waiting": matrix["counts"][ACTION_WAIT],
            "prod_owner_only": matrix["counts"][ACTION_HOLD_OWNER_ONLY],
            "prod_untouched": matrix["production_untouched"],
            "sandbox_released": len(rehearse["run"]["released"]),
            "sandbox_owner_only": rehearse["run"]["counts"][ACTION_HOLD_OWNER_ONLY],
            "sandbox_waiting": rehearse["run"]["counts"][ACTION_WAIT],
            "sandbox_rollback_ok": rehearse["all_released_restored_to_blocked"],
            "audit_chain_complete": rehearse["audit_rows_complete"],
        },
    }
    out_path = Path(args.json_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["verdict"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())