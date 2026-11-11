#!/usr/bin/env python3
"""Acceptance harness for the terminal-attribution mechanism (plan-226 replay study).

Runs the pre-registered criteria on *real* platform data and writes a single
machine-readable artifact so no number in the report is hand-copied:

(a) coverage      — classify the whole terminal population, ``UNKNOWN`` must be 0;
(b) agreement     — compare the classifier with a hand-labelled, output-redacted sample;
(c) replay study  — dispatch the replay plan and measure how many replays reach a live
                    worker (pre-registered threshold, see ``PREREG.json``);
(d) budget ledger — one budget unit per node, replays stamped with ``requeue_class``;
(e) neutrality    — injected lexicon must find zero hits in the mechanics + tests;
(f) idempotency   — two passes must hash identically, order must not matter;
(g) regression    — the module's pytest file must be green with >= 15 cases.

Usage
-----
    python3 -m tools.validation.terminal_attribution_acceptance --snapshot \
        artifacts/p226w3_n09/terminal_missions.jsonl \
        --prereg artifacts/p226w3_n09/PREREG.json \
        --out artifacts/p226w3_n09/acceptance.json \
        [--dispatch] [--observe-minutes 40]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import terminal_attribution as ta  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SNAPSHOT = os.path.join(ROOT, "artifacts", "p226w3_n09", "terminal_missions.jsonl.gz")
DEFAULT_PREREG = os.path.join(ROOT, "artifacts", "p226w3_n09", "PREREG.json")
DEFAULT_OUT = os.path.join(ROOT, "artifacts", "p226w3_n09", "acceptance.json")
SNAPSHOT_DIR = os.path.join(ROOT, "artifacts", "p226w3_n09", "snapshots")


# ───────────────────────── population selection ──────────────────────────
def attempts_of(records: Sequence[Mapping[str, Any]]) -> Dict[str, List[Mapping[str, Any]]]:
    """Group terminal records by node key, sorted by attempt number."""
    out: Dict[str, List[Mapping[str, Any]]] = {}
    for rec in records:
        task_key = str(rec.get("task_key") or "")
        head, sep, tail = task_key.rpartition("-a")
        attempt = int(tail) if sep and tail.isdigit() else 1
        enriched = dict(rec)
        enriched["_attempt"] = attempt
        enriched["_node"] = ta.node_key_of(rec)
        out.setdefault(enriched["_node"], []).append(enriched)
    for node in out:
        out[node].sort(key=lambda r: r["_attempt"])
    return out


def completed_attempts(attempts: Sequence[Mapping[str, Any]]) -> int:
    """Attempts that consumed the cap: reached work or reached ``finish``.

    An attempt that was administratively stopped before any evidence of work does not
    consume the cap — otherwise a regulator stop would freeze a node forever, which is
    precisely the deadlock this mechanism exists to remove.
    """
    n = 0
    for rec in attempts:
        result = ta.attribute(rec)
        worked = bool(result.get("tool_calls") and result["tool_calls"] > 0)
        if worked or result.get("finish_called"):
            n += 1
    return n


def select_experiment_nodes(records: Sequence[Mapping[str, Any]], population_regex: str,
                            count: int = 6) -> List[Dict[str, Any]]:
    """First ``count`` nodes (by node code) in the population whose latest attempt is INFRA_*."""
    pattern = re.compile(population_regex) if population_regex else None
    groups = attempts_of(records)
    selected: List[Dict[str, Any]] = []
    for node in sorted(groups):
        if pattern and not pattern.search(node):
            continue
        attempts = groups[node]
        latest = ta.attribute(attempts[-1])
        if not latest["class"].startswith("INFRA_"):
            continue
        selected.append({
            "node_key": node,
            "attempts": len(attempts),
            "completed_attempts": completed_attempts(attempts),
            "latest_attempt": attempts[-1]["_attempt"],
            "latest_mission_id": attempts[-1].get("mission_id"),
            "latest_class": latest["class"],
            "classes": [ta.attribute(a)["class"] for a in attempts],
            "goal": str(attempts[0].get("goal") or ""),
            "repo": str(attempts[0].get("repo") or ""),
            "base_ref": str(attempts[0].get("base_ref") or "main"),
        })
        if len(selected) >= count:
            break
    return selected


# ───────────────────────────── dispatch ─────────────────────────────────
def _psql(sql: str, timeout: int = 180) -> str:
    """Run one SQL statement through psql (the sandbox has no usable DB driver)."""
    env = {"PGPASSWORD": ta._pg_password(), "PATH": "/usr/bin:/bin:/usr/local/bin"}
    proc = subprocess.run(
        ["psql", "-h", os.environ.get("TP_PG_HOST", "127.0.0.1"),
         "-U", os.environ.get("TP_PG_USER", "foundation"),
         "-d", os.environ.get("TP_PG_DB", "tensorplatform"), "-tAc", sql],
        capture_output=True, text=True, env=env, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr[:400]}")
    return proc.stdout


def _lit(text: Any) -> str:
    """Dollar-quoted SQL literal; the tag is chosen so it cannot appear in the text."""
    body = str(text)
    tag = "$q$"
    i = 0
    while tag in body:
        i += 1
        tag = f"$q{i}$"
    return f"{tag}{body}{tag}"


def dispatch_replays(selected: Sequence[Mapping[str, Any]], policy: ta.RequeuePolicy,
                     budget: Mapping[str, Any], execute: bool) -> List[Dict[str, Any]]:
    """Insert one replay mission per node (or only render the plan when not executing)."""
    receipts: List[Dict[str, Any]] = []
    for node in selected:
        target_attempt = int(node["latest_attempt"]) + 1
        backoff = policy.backoff_for(target_attempt)
        snapshot = ta.snapshot_artifacts(
            [os.path.join("/root/tp-agent/work", f"m{node['latest_mission_id']}")],
            SNAPSHOT_DIR, node["node_key"], int(node["latest_attempt"]))
        receipt = {
            "node_key": node["node_key"],
            "parent_mission_id": node["latest_mission_id"],
            "target_attempt": target_attempt,
            "task_key": f"{node['node_key']}-a{target_attempt}",
            "requeue_class": node["latest_class"],
            "backoff_seconds": backoff,
            "snapshot_digest": snapshot["digest"],
            "snapshot_entries": len(snapshot["entries"]),
            "snapshot_status": sorted({e.get("status", "copied") for e in snapshot["entries"]}),
            "dispatched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        if execute:
            # Idempotent dispatch: a replay already recorded for this task key is reused,
            # never duplicated (a crashed dispatcher must not double-spend a node).
            existing = _psql("SELECT id FROM tensor_dev_missions WHERE organization_id = 1 "
                             f"AND task_key = {_lit(receipt['task_key'])}").strip()
            out = _psql(
                "INSERT INTO tensor_dev_missions "
                "(organization_id, goal, repo, base_ref, budget, status, runner, origin, "
                " parent_mission_id, task_key, created_by) VALUES "
                f"(1, {_lit(node['goal'])}, {_lit(node['repo'])}, {_lit(node['base_ref'])}, "
                f"{_lit(json.dumps(dict(budget)))}::jsonb, 'queued', 'native', 'agent', "
                f"{int(receipt['parent_mission_id'])}, {_lit(receipt['task_key'])}, 1) "
                "ON CONFLICT DO NOTHING RETURNING id")
            if existing:
                new_id, reused = int(existing.splitlines()[0]), True
            else:
                new_id, reused = int(out.strip().splitlines()[0]), False
            receipt["reused_existing"] = reused
            # Audit action: migration 111 (repo) adds 'infra_replay' to the vocabulary but
            # the deployed public schema has not applied it and this role does not own the
            # table, so the write falls back to the existing 'custody_requeue' action with an
            # explicit requeue_kind stamp (see docs: schema-drift finding).
            if not reused:
                _psql("INSERT INTO mission_lifecycle_log (mission_id, action, actor_user_id, detail) "
                      f"VALUES ({new_id}, 'custody_requeue', 1, "
                      f"{_lit(json.dumps({'requeue_kind': 'infra_replay', 'parent_mission_id': int(receipt['parent_mission_id']), 'requeue_class': node['latest_class'], 'attempt': target_attempt, 'backoff_seconds': backoff, 'policy': policy.as_dict()}, ensure_ascii=False))}::jsonb)")
            receipt["mission_id"] = new_id
        receipts.append(receipt)
    return receipts


def observe(receipts: Sequence[Mapping[str, Any]], budget_wait_s: float,
            poll_s: float = 60.0) -> List[Dict[str, Any]]:
    """Poll the dispatched replays until each is live (work evidence) or time runs out."""
    ids = [int(r["mission_id"]) for r in receipts if r.get("mission_id")]
    if not ids:
        return []
    deadline = time.time() + budget_wait_s
    state: Dict[str, Dict[str, Any]] = {str(i): {} for i in ids}
    while True:
        rows = _psql(
            "SELECT m.id, m.status, coalesce(m.claimed_by,''), "
            "coalesce(to_char(m.heartbeat_at,'YYYY-MM-DD HH24:MI:SS'),''), "
            "coalesce(m.result->>'tool_calls',''), "
            "(SELECT count(*) FROM tensor_agent_session_events e WHERE e.session_id=m.session_id), "
            "(SELECT count(*) FROM tensor_agent_session_events e WHERE e.session_id=m.session_id "
            " AND e.kind='note' AND e.payload ? 'round') "
            f"FROM tensor_dev_missions m WHERE m.id IN ({','.join(str(i) for i in ids)})")
        for line in rows.splitlines():
            if not line.strip():
                continue
            mid, status, claimed_by, heartbeat, tc, events, rounds = line.split("|")
            state[mid] = {
                "status": status, "claimed_by": claimed_by or None, "heartbeat_at": heartbeat or None,
                "tool_calls": int(tc) if tc.strip() else None,
                "session_events": int(events or 0), "rounds": int(rounds or 0),
                "reached_live": bool((tc.strip() and int(tc) > 0) or int(rounds or 0) > 0),
                "claimed": bool(claimed_by),
            }
        if all(v.get("reached_live") or v.get("status") in
               ("succeeded", "failed", "stopped", "revoked") for v in state.values()):
            break
        if time.time() >= deadline:
            break
        time.sleep(poll_s)
    return [{"mission_id": int(mid), **state.get(str(mid), {})} for mid in ids]


# ───────────────────────────── criteria ─────────────────────────────────
def criterion_a(records, results) -> Dict[str, Any]:
    sample_size = min(200, len(results))
    unknown_all = sum(1 for r in results if r["class"] == ta.CLASS_UNKNOWN)
    return {
        "pass": unknown_all == 0,
        "terminals_classified": len(results),
        "unknown": unknown_all,
        "attribution_coverage": ta.attribution_coverage(results),
        "sample_ge_200": sample_size >= 200,
        "sample_size": sample_size,
    }


def criterion_b(labels_path: str, results: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if not os.path.exists(labels_path):
        return {"pass": False, "reason": "hand_labels_missing", "path": labels_path}
    with open(labels_path, encoding="utf-8") as fh:
        labels = json.load(fh)
    by_id = {str(r["mission_id"]): r["class"] for r in results}
    rows, agree = [], 0
    for item in labels["labels"]:
        human = item["label"]
        machine = by_id.get(str(item["mission_id"]))
        ok = human == machine
        agree += int(ok)
        rows.append({"mission_id": item["mission_id"], "human": human,
                     "machine": machine, "agree": ok})
    total = len(rows)
    return {
        "pass": total >= 30 and agree == total,
        "labelled": total,
        "agreement": (agree / total) if total else None,
        "protocol": labels.get("protocol"),
        "disagreements": [r for r in rows if not r["agree"]],
        "rows": rows,
    }


def criterion_c(experiment: Mapping[str, Any], threshold: float = 4 / 6) -> Dict[str, Any]:
    nodes = experiment.get("nodes") or []
    live = sum(1 for n in nodes if n.get("reached_live"))
    total = len(nodes)
    rate = (live / total) if total else None
    return {
        "pass": bool(total and rate is not None and rate >= threshold),
        "nodes": total,
        "reached_live": live,
        "rate": rate,
        "threshold": threshold,
        "measurement": experiment.get("measurement"),
        "pending": [n["node_key"] for n in nodes if not n.get("claimed")],
        "digest": ta.sha256_of(nodes),
    }


def criterion_d(experiment: Mapping[str, Any], policy: ta.RequeuePolicy) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    for node in experiment.get("nodes") or []:
        for idx, klass in enumerate(node.get("classes") or []):
            attempts.append({"node_key": node["node_key"], "attempt": idx + 1, "class": klass})
    ledger = ta.settle_attempts(attempts, policy)
    return {
        "pass": ledger["single_settlement_holds"],
        "nodes": ledger["nodes"],
        "rows": len(ledger["rows"]),
        "replays": ledger["replays"],
        "budget_used_total": ledger["budget_used_total"],
        "budget_used_per_node": ledger["budget_used_per_node"],
        "every_replay_stamped": all(r["requeue_class"] for r in ledger["rows"]
                                    if not r["billable"]),
    }


def criterion_e(lexicon_path: str, scan_paths: Sequence[str]) -> Dict[str, Any]:
    if not os.path.exists(lexicon_path):
        return {"pass": False, "reason": "lexicon_missing", "path": lexicon_path}
    with open(lexicon_path, encoding="utf-8") as fh:
        lexicon = json.load(fh)
    out = ta.scan_domain_words([p for p in scan_paths if os.path.exists(p)], lexicon)
    return {"pass": out["hits"] == 0, **out}


def criterion_f(records) -> Dict[str, Any]:
    out = ta.idempotency_check(records)
    return {"pass": out["identical"] and out["order_invariant"], **out}


def criterion_g() -> Dict[str, Any]:
    path = os.path.join(ROOT, "tests", "test_terminal_attribution.py")
    proc = subprocess.run([sys.executable, "-m", "pytest", path, "-q", "--no-header"],
                          capture_output=True, text=True, cwd=ROOT)
    tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    passed = failed = 0
    m = re.search(r"(\d+) passed", proc.stdout or "")
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+) failed", proc.stdout or "")
    if m:
        failed = int(m.group(1))
    return {"pass": proc.returncode == 0 and passed >= 15, "returncode": proc.returncode,
            "passed": passed, "failed": failed, "tail": tail[0][:200]}


def replay_baseline(records) -> Dict[str, Any]:
    """Platform-wide a1-INFRA -> replay yield: the counterfactual this gate improves on."""
    pairs = ta.replay_pairs(records)
    out = ta.requeue_yield([{**p["target"], "node_key": p["node_key"]} for p in pairs])
    per_node = {k: v["reached_worker"] > 0 for k, v in out["per_node"].items()}
    return {
        **out,
        "nodes": len(out["per_node"]),
        "nodes_reached": sum(per_node.values()),
        "node_rate": (sum(per_node.values()) / len(per_node)) if per_node else None,
    }


def emit_label_sample(records: Sequence[Mapping[str, Any]], path: str, n: int = 30,
                      seed: int = 226) -> Dict[str, Any]:
    """Emit a redacted labelling sheet: raw evidence only, no classifier output."""
    import random

    rng = random.Random(seed)
    idx = sorted(rng.sample(range(len(records)), min(n, len(records))))
    rows = []
    for i in idx:
        rec = records[i]
        rows.append({
            "mission_id": rec.get("mission_id"),
            "status": rec.get("status"),
            "result": rec.get("result"),
            "session": rec.get("session"),
        })
    payload = {
        "protocol": ("single-author blind labelling: the sheet carries raw terminal evidence "
                     "only (status, result, session) with no classifier output; labels were "
                     "written from that evidence alone, then compared mechanically. Independence "
                     "is limited by having one author and this limitation is reported."),
        "seed": seed,
        "sample": rows,
        "classes": list(ta.ALL_CLASSES),
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return {"path": path, "rows": len(rows), "digest": ta.sha256_of(rows)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    ap.add_argument("--prereg", default=DEFAULT_PREREG)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--hand-labels",
                    default=os.path.join(ROOT, "artifacts", "p226w3_n09", "hand_labels.json"))
    ap.add_argument("--experiment",
                    default=os.path.join(ROOT, "artifacts", "p226w3_n09", "replay_experiment.json"))
    ap.add_argument("--lexicon", default=os.path.join(ROOT, "artifacts", "p226w3_n09",
                                                      "domain_lexicon.json"))
    ap.add_argument("--plan-json", default=os.path.join(ROOT, "artifacts", "p226w3_n09",
                                                        "requeue_plan.json"))
    ap.add_argument("--dispatch", action="store_true", help="really insert the replay missions")
    ap.add_argument("--observe-minutes", type=float, default=0.0)
    ap.add_argument("--emit-sample", help="write the redacted labelling sheet here")
    args = ap.parse_args(argv)

    with open(args.prereg, encoding="utf-8") as fh:
        prereg = json.load(fh)
    policy = ta.RequeuePolicy(**prereg.get("policy", {}))
    records = ta.load_snapshot(args.snapshot)
    results = ta.attribute_all(records)
    summary = ta.summarize(records, results)
    if args.emit_sample:
        print(json.dumps(emit_label_sample(records, args.emit_sample), ensure_ascii=False))

    nodes = select_experiment_nodes(records, prereg["population_regex"],
                                    prereg.get("node_count", 6))
    plan = ta.plan_replays([r for r in records if ta.node_key_of(r) in {n["node_key"] for n in nodes}],
                           policy=policy)
    os.makedirs(os.path.dirname(os.path.abspath(args.plan_json)), exist_ok=True)
    with open(args.plan_json, "w", encoding="utf-8") as fh:
        json.dump({"plan": plan, "nodes": nodes, "policy": policy.as_dict()},
                  fh, ensure_ascii=False, indent=2, sort_keys=True)

    experiment: Dict[str, Any] = {"nodes": [], "measurement": prereg.get("measurement")}
    if os.path.exists(args.experiment):
        with open(args.experiment, encoding="utf-8") as fh:
            experiment.update(json.load(fh))

    receipts: List[Dict[str, Any]] = []
    if args.dispatch:
        receipts = dispatch_replays(nodes, policy, prereg["probe_budget"], execute=True)
        deduped = {r["task_key"]: r for r in list(experiment.get("receipts") or []) + receipts}
        experiment["receipts"] = [deduped[k] for k in sorted(deduped)]
        experiment["nodes"] = []          # one row per node per round
        if args.observe_minutes > 0:
            observed = observe(receipts, args.observe_minutes * 60)
            by_id = {str(o["mission_id"]): o for o in observed}
            for node, receipt in zip(nodes, receipts):
                obs = by_id.get(str(receipt.get("mission_id")), {})
                node = dict(node)
                node.update({"replay_mission_id": receipt.get("mission_id"),
                             "reached_live": bool(obs.get("reached_live")),
                             "claimed": bool(obs.get("claimed")),
                             "observed": obs})
                experiment["nodes"].append(node)
        else:
            for node, receipt in zip(nodes, receipts):
                experiment["nodes"].append({**node,
                                            "replay_mission_id": receipt.get("mission_id"),
                                            "reached_live": False, "claimed": False})
    elif not any(n.get("replay_mission_id") for n in experiment.get("nodes") or []):
        for node in nodes:
            experiment["nodes"].append({**node, "reached_live": False, "claimed": False,
                                        "dry_run": True})
    experiment["receipts"] = experiment.get("receipts", [])
    with open(args.experiment, "w", encoding="utf-8") as fh:
        json.dump(experiment, fh, ensure_ascii=False, indent=2, sort_keys=True)

    criteria = {
        "a_coverage": criterion_a(records, results),
        "b_agreement": criterion_b(args.hand_labels, results),
        "c_replay": criterion_c(experiment),
        "d_ledger": criterion_d(experiment, policy),
        "e_neutrality": criterion_e(args.lexicon, [
            os.path.join(ROOT, "tools", "validation", "terminal_attribution.py"),
            os.path.join(ROOT, "tools", "validation", "terminal_attribution_acceptance.py"),
            os.path.join(ROOT, "tests", "test_terminal_attribution.py"),
        ]),
        "f_idempotency": criterion_f(records),
        "g_regression": criterion_g(),
    }
    # What would a naive "retry every failed mission" policy do compared with the gate?
    failure_records = [r for r, res in zip(records, results) if res["is_failure"]]
    gate_plan = ta.plan_replays(failure_records, policy=policy)
    reasons: Dict[str, int] = {}
    for entry in gate_plan["entries"]:
        reasons[entry["reason"]] = reasons.get(entry["reason"], 0) + 1
    payload = {
        "schema": ta.SCHEMA + "/acceptance",
        "gate_decisions": {
            "candidate_failures": len(failure_records),
            "nodes": gate_plan["plan_nodes"],
            "eligible": gate_plan["plan_replays_used"],
            "reason_counts": reasons,
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "snapshot": {"path": args.snapshot, "records": len(records),
                     "sha256": ta.sha256_file(args.snapshot)},
        "prereg": prereg,
        "summary": summary,
        "criteria": criteria,
        "all_pass": all(c.get("pass") for c in criteria.values()),
        "replay_baseline": replay_baseline(records),
        "experiment": {"nodes": experiment.get("nodes"), "receipts": len(receipts)},
        "replay_yield_measured": ta.requeue_yield([
            {"node_key": n["node_key"], "status": "failed",
             "result": {"tool_calls": 1 if n.get("reached_live") else 0}}
            for n in experiment.get("nodes", [])]),
    }
    payload["acceptance_digest"] = ta.sha256_of({k: payload[k] for k in
                                                 ("summary", "criteria", "replay_baseline")})
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps({k: payload[k] for k in ("criteria", "all_pass", "acceptance_digest")},
                     ensure_ascii=False, indent=2)[:4000])
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())