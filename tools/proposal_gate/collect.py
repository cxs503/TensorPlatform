"""播种供给链实况采集：真库战役节点 × 历史提案池 → 三门判定 → 账本 + 覆盖率 + 审计工件。

三条臂（arms）
-------------
1. ``proposal_pool``  —— in-repo 历史提案池（``**/proposals/**/*.json``）逐份过门：
   契约门（v1 经 compat 层）+ 去重门。产出：通过/隔离计数与逐条分值。
2. ``node_audit``     —— **真库** ``campaign_nodes`` 的 (name + metadata.goal) 作为「若今天
   重新提交会怎样」的留一法（leave-one-out）审计：把全量节点灌进索引，再逐条把
   该节点自身从索引中排除后过门。产出：真实数据上的重复率/复核率/隔离率，
   以及「按新契约会被拦下的节点占比」这一平台发现的定量结论。
3. ``coverage``       —— 播种覆盖率账本：对已被显式采纳（``adopt``）的提案，核验其在
   ``campaign_nodes`` 里有无真实播种落点（按 mission_id 关联），并给出分母/分子。

诚实纪律：API 不可达 / 无采纳记录时，对应臂写 ``status: unavailable|empty`` 并把
``rate`` 置 null，绝不编数值。

用法::

    python3 -m tools.proposal_gate.collect --out artifacts/proposal_gate
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import GateConfig, load_config
from .contract import validate_proposal
from .gate import ProposalGate
from .index import ProposalIndex
from .ledger import LedgerStore
from .sources import infer_repo, load_campaign_nodes, load_historical_proposals

API_ENV_BASE = "TENSOR_API_BASE"


# ── 平台 API（只读）────────────────────────────────────────────────────


def _post_json(url: str, body: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _get_json(url: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def fetch_campaign_nodes(limit_plans: int = 120) -> list[dict[str, Any]]:
    """从平台 API 拉战役节点（只读）。失败即抛，由调用方降级为 unavailable。"""
    base = os.environ.get(API_ENV_BASE, "http://127.0.0.1:8010/api/v1").rstrip("/")
    token = _post_json(
        f"{base}/auth/login",
        {
            "username": os.environ["TENSOR_AGENT_USER"],
            "password": os.environ["TENSOR_AGENT_PASS"],
        },
    )["access_token"]
    plans = _get_json(f"{base}/tensor/campaigns/plans?limit={int(limit_plans)}", token).get("items", [])
    out: list[dict[str, Any]] = []
    for plan in plans:
        try:
            nodes = _get_json(f"{base}/tensor/campaigns/plans/{plan['id']}/nodes", token).get("items", [])
        except Exception:  # noqa: BLE001 - 单 plan 失败不拖垮整体
            continue
        for node in nodes:
            meta = node.get("metadata") or {}
            goal = meta.get("goal") if isinstance(meta.get("goal"), str) else ""
            out.append(
                {
                    "id": node.get("id"),
                    "plan_id": node.get("plan_id"),
                    "mission_id": node.get("mission_id"),
                    "name": node.get("name") or "",
                    "goal": goal,
                    "status": node.get("status"),
                }
            )
    return out


# ── 节点 → 提案载荷（不发明数据：判据只从节点 goal 里"抽取"含比较符+数字的行）──


def extract_criteria(goal: str, cfg: GateConfig, *, max_items: int = 12) -> list[str]:
    numeric = re.compile(str(cfg.need("contract.numeric_pattern")))
    comparator = re.compile(str(cfg.need("contract.comparator_pattern")))
    units = tuple(cfg.get("contract.unit_symbols", []) or ())
    lines: list[str] = []
    for chunk in re.split(r"[\n。；;]+", goal or ""):
        text = chunk.strip(" \t-—*#")
        if not text or len(text) > 400:
            continue
        if numeric.search(text) and (comparator.search(text) or any(u in text for u in units)):
            lines.append(text)
        if len(lines) >= max_items:
            break
    return lines


def node_to_payload(
    node: dict[str, Any], cfg: GateConfig, *, repo_fallback: str | None = None
) -> dict[str, Any]:
    repo = (
        infer_repo(node["name"], cfg.repo_whitelist)
        or (repo_fallback if repo_fallback in cfg.repo_whitelist else "")
    )
    return {
        "schema_version": "2.0",
        "mission_id": node.get("mission_id"),
        "plan_id": node.get("plan_id"),
        "title": (node["name"] or "untitled")[:200],
        "repo": repo,
        "goal": {
            "objective": (node.get("goal") or node["name"] or "untitled")[:4000],
            "criteria": extract_criteria(node.get("goal") or "", cfg),
        },
        "dependencies_hint": [],
        "is_maturity": True,
    }


# ── 采集 ───────────────────────────────────────────────────────────────


@dataclass
class ArmResult:
    name: str
    status: str
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"arm": self.name, "status": self.status, **self.detail}


def run(
    *,
    out_dir: Path,
    limit_plans: int = 120,
    max_nodes: int | None = None,
    use_api: bool = True,
    repo_fallback: str | None = None,
    adopt_accepted: bool = True,
) -> dict[str, Any]:
    cfg = load_config()
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = out_dir / "ledger.sqlite"
    if ledger_path.exists():
        ledger_path.unlink()
    store = LedgerStore.for_sqlite(str(ledger_path), cfg)
    index = ProposalIndex(cfg)
    gate = ProposalGate(
        cfg,
        store,
        index,
        quarantine_dir=out_dir / "quarantine",
        events_file=out_dir / "events.jsonl",
    )

    arms: list[ArmResult] = []

    # ── 臂 0：真库节点索引 ──
    nodes: list[dict[str, Any]] = []
    node_status = "unavailable"
    node_error = None
    if use_api:
        try:
            nodes = fetch_campaign_nodes(limit_plans=limit_plans)
            node_status = "ok" if nodes else "empty"
        except Exception as exc:  # noqa: BLE001
            node_error = f"{type(exc).__name__}: {exc}"
    if max_nodes:
        nodes = nodes[: int(max_nodes)]
    for node in nodes:
        index.add_node(f"node:{node['id']}", (node["name"] + "\n" + node["goal"]).strip(),
                       repo=infer_repo(node["name"], cfg.repo_whitelist))
    arms.append(
        ArmResult(
            "node_index",
            node_status,
            {
                "nodes_indexed": len(nodes),
                "kinds": index.kinds(),
                "error": node_error,
                "nodes_with_mission": sum(1 for n in nodes if n.get("mission_id") is not None),
                "nodes_with_inferable_repo": sum(
                    1 for n in nodes if infer_repo(n["name"], cfg.repo_whitelist)
                ),
                "plans_covered": len({n.get("plan_id") for n in nodes}),
            },
        )
    )

    # ── 臂 1：历史提案池 ──
    pool_files = load_historical_proposals(cfg.resolve_path("."), "**/proposals/**/*.json")
    pool_results: list[dict[str, Any]] = []
    for path in pool_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            pool_results.append(
                {"path": str(path.relative_to(cfg.resolve_path("."))), "verdict": "unreadable",
                 "errors": [f"{type(exc).__name__}: {exc}"]}
            )
            continue
        res = gate.evaluate(
            payload,
            mission_id=int(cfg.need("ledger.historical_pool_mission_id")),
            source_path=str(path.relative_to(cfg.resolve_path("."))),
            persist_quarantine=False,
        )
        pool_results.append(
            {"path": str(path.relative_to(cfg.resolve_path("."))), **res.as_dict()}
        )
    arms.append(
        ArmResult(
            "proposal_pool",
            "ok" if pool_files else "empty",
            {
                "files": len(pool_files),
                "verdicts": _tally(pool_results, "verdict"),
                "items": pool_results,
            },
        )
    )

    # ── 臂 2：真库节点留一法审计 ──
    node_results: list[dict[str, Any]] = []
    if nodes:
        for node in nodes:
            payload = node_to_payload(node, cfg, repo_fallback=repo_fallback)
            # 留一法：复用已切分好的 IndexEntry（with_grams 幂等），避免 O(n²) 重切分
            probe = ProposalIndex(cfg)
            probe.add_many(e for e in index if e.key != f"node:{node['id']}")
            audit_gate = ProposalGate(
                cfg,
                store,
                probe,
                quarantine_dir=out_dir / "quarantine",
                events_file=out_dir / "events.jsonl",
            )
            res = audit_gate.evaluate(
                payload,
                mission_id=int(node["mission_id"] or 0),
                plan_id=node.get("plan_id"),
                source_path=f"campaign_node:{node['id']}",
                persist_quarantine=False,
            )
            node_results.append(
                {
                    "node_id": node["id"],
                    "plan_id": node.get("plan_id"),
                    "mission_id": node.get("mission_id"),
                    "status": node.get("status"),
                    **res.as_dict(),
                }
            )
    arms.append(
        ArmResult(
            "node_audit",
            "ok" if node_results else node_status,
            {
                "audited": len(node_results),
                "verdicts": _tally(node_results, "verdict"),
                "states": _tally(node_results, "state"),
                "top_reasons": _top_reasons(node_results, limit=8),
                "duplicate_examples": [
                    r for r in node_results if r["verdict"] in ("duplicate", "near_duplicate")
                ][:10],
            },
        )
    )

    # 采纳证据：被审计节点本身已存在于 campaign_nodes（真库播种落点）→ 把通过的提案
    # 记为 adopted。这是当前平台唯一客观存在的采纳证据（尚无显式 adopt 记账）。
    adopted_n = 0
    if adopt_accepted:
        for item in node_results:
            if item["verdict"] != "accepted":
                continue
            gate.adopt(
                mission_id=int(item["mission_id"] or 0),
                sha256=item["sha256"],
                plan_id=item.get("plan_id"),
            )
            adopted_n += 1

    # ── 臂 3：播种覆盖率账本 ──
    coverage = gate.coverage(index=index)
    mission_to_nodes: dict[int, list[int]] = {}
    for n in nodes:
        if n.get("mission_id") is not None:
            mission_to_nodes.setdefault(int(n["mission_id"]), []).append(int(n["id"]))
    adopted_rows = store.rows(state="adopted", limit=100_000)
    resolved = [r for r in adopted_rows if r.mission_id in mission_to_nodes]
    arms.append(
        ArmResult(
            "coverage",
            "ok" if adopted_rows else "empty",
            {
                "text_match_coverage": coverage,
                "adopted": len(adopted_rows),
                "adopted_this_run": adopted_n,
                "adopted_with_real_seed_point": len(resolved),
                "seed_point_rate": (len(resolved) / len(adopted_rows)) if adopted_rows else None,
                "nodes_with_mission": len(mission_to_nodes),
                "nodes_total": len(nodes),
                "mission_traceability_rate": (
                    len(mission_to_nodes) / len(nodes) if nodes else None
                ),
            },
        )
    )

    stats = store.stats(adopted_covered=len(resolved) if adopted_rows else 0)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_path": str(cfg.path),
        "repo_fallback": repo_fallback,
        "adopt_accepted": adopt_accepted,
        "arms": [a.as_dict() for a in arms],
        "ledger_stats": stats.as_dict(),
        "sql_trace": _sql_trace(ledger_path, cfg),
    }
    store.dispose()
    return summary


def _tally(items: list[dict[str, Any]], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        key = str(item.get(field))
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _top_reasons(items: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in items:
        for reason in item.get("reasons") or ():
            head = str(reason).split(":")[0].split(" ")[0]
            counts[head] = counts.get(head, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return [{"reason": k, "count": v} for k, v in ordered]


def _sql_trace(ledger_path: Path, cfg: GateConfig) -> dict[str, Any]:
    """把账本四态计数用 SQL 复算一遍（证明工件数字可 SQL 溯源）。"""
    table = cfg.table_name
    conn = sqlite3.connect(str(ledger_path))
    try:
        rows = conn.execute(
            f"SELECT state, count(*) FROM {table} GROUP BY state ORDER BY state"
        ).fetchall()
        total = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        dup = conn.execute(
            f"SELECT count(*) FROM {table} WHERE state = 'dedup_rejected'"
        ).fetchone()[0]
        verdicts = conn.execute(
            f"SELECT verdict, count(*) FROM {table} GROUP BY verdict ORDER BY verdict"
        ).fetchall()
    finally:
        conn.close()
    return {
        "db": str(ledger_path),
        "sql": f"SELECT state, count(*) FROM {table} GROUP BY state ORDER BY state",
        "state_counts": {str(k): int(v) for k, v in rows},
        "total": int(total),
        "dedup_rejected": int(dup),
        "dup_rate": round(dup / total, 6) if total else 0.0,
        "verdict_counts": {str(k): int(v) for k, v in verdicts},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="提案门实况采集（真库节点 × 历史提案池）")
    ap.add_argument("--out", default="artifacts/proposal_gate")
    ap.add_argument("--limit-plans", type=int, default=120)
    ap.add_argument("--max-nodes", type=int, default=0)
    ap.add_argument("--no-api", action="store_true")
    ap.add_argument(
        "--repo-fallback",
        default=None,
        help="节点名推不出仓库时使用的兜底仓（须在白名单内）；缺省=严格口径（空仓→隔离）",
    )
    ap.add_argument("--no-adopt", action="store_true", help="不做采纳（仅审计）")
    args = ap.parse_args(argv)
    summary = run(
        out_dir=Path(args.out),
        limit_plans=args.limit_plans,
        max_nodes=(args.max_nodes or None),
        use_api=not args.no_api,
        repo_fallback=args.repo_fallback,
        adopt_accepted=not args.no_adopt,
    )
    out = Path(args.out) / "collect_latest.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("ledger_stats",)}, ensure_ascii=False, indent=2))
    print(f"written {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())