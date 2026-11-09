"""repro_audit.cli —— 抽样式重跑复证的编排层（只读、幂等、可被后续波次复用）。

用法::

    # 1) 快照战役节点（一次性，后续波次可直接用快照复算）
    python3 -m tools.repro_audit.cli --snapshot-nodes artifacts/repro_audit/nodes_snapshot.json

    # 2) 抽样复证（默认 N=32，repo×年 分层；--n 可调）
    python3 -m tools.repro_audit.cli --audit --n 32 --out artifacts/repro_audit.json

    # 3) 把实测 repro_rate 落库（API → psql → pending 工件三级降级）
    python3 -m tools.repro_audit.cli --audit --n 32 --persist

纪律：本工具**只读**被审计仓（``git archive`` 到临时目录执行，不写工作树、不改 .git）、
**幂等**（同输入同输出）、**不写库**除 KPI 回写（:mod:`persist`）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import core
from .persist import persist_kpi

DEFAULT_WORK_ROOT = os.environ.get("TP_WORK_ROOT", "/root/tp-agent/work")
DEFAULT_OUT = "artifacts/repro_audit.json"
DEFAULT_NODES_SNAPSHOT = "artifacts/repro_audit/nodes_snapshot.json"
PER_NODE_TIMEOUT = 600          # 判据 e：单节点重跑成本上限 10 min
PYTEST_TIMEOUT = 300            # 单次测试执行的墙钟上限（留出导出/锚定时间）
MAX_TEST_FILES = 30             # 单节点最多跑 30 个测试文件（成本上界，超出的记 subset）


# ─────────────────────────── 节点快照 ───────────────────────────

def fetch_nodes_via_api(base: str | None = None) -> list[dict[str, Any]]:
    """从平台 API 拉全部战役节点（plans → nodes），零直连 DB 依赖。"""
    import urllib.request

    base = (base or os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")).rstrip("/")

    def _call(path: str, body: dict[str, Any] | None = None, token: str | None = None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(base + path, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.load(resp)

    token = _call("/auth/login", {"username": os.environ["TENSOR_AGENT_USER"],
                                  "password": os.environ["TENSOR_AGENT_PASS"]}).get("access_token")
    plans = _call("/tensor/campaigns/plans?limit=500", token=token)["items"]
    nodes: list[dict[str, Any]] = []
    for plan in plans:
        payload = _call(f"/tensor/campaigns/plans/{plan['id']}/nodes", token=token)
        items = payload if isinstance(payload, list) else payload.get("items", [])
        for node in items:
            node["_plan"] = plan.get("name")
            node["_plan_id"] = plan.get("id")
            nodes.append(node)
    return nodes


# ─────────────────────────── 候选（带可执行工件） ───────────────────────────

_TEST_HINT_RE = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|(^|/)[^/]*_test\.py$")


def _is_test_file(path: str) -> bool:
    return bool(_TEST_HINT_RE.search(path)) and path.endswith(".py")


def node_candidates(nodes: list[dict[str, Any]], work_root: str = DEFAULT_WORK_ROOT) -> list[dict[str, Any]]:
    """筛出「带可执行工件」的 done 节点：worktree 存在 + 干净检出里有测试文件 + 有数值判据。

    全部判据都是**结构性**的（存在性/正则），不含任何学科词或仓名常量。
    """
    todo: list[tuple[dict[str, Any], str]] = []
    for node in nodes:
        if str(node.get("status")) != "done":
            continue
        mid = node.get("mission_id")
        if not mid:
            continue
        workdir = os.path.join(work_root, f"m{mid}")
        if not os.path.isdir(workdir):
            continue
        todo.append((node, workdir))

    def _probe(pair: tuple[dict[str, Any], str]) -> dict[str, Any] | None:
        node, workdir = pair
        try:
            files = core.committed_files(workdir)
        except Exception:
            return None
        tests = [f for f in files if _is_test_file(f)]
        if not tests:
            return None
        meta = node.get("metadata") or {}
        return {"node": node, "workdir": workdir, "n_files": len(files),
                "n_test_files": len(tests),
                "n_criteria": len(meta.get("acceptance_criteria") or []),
                "repo": meta.get("repo")}

    with ThreadPoolExecutor(max_workers=16) as pool:
        probed = list(pool.map(_probe, todo))
    return [r for r in probed if r]


# ─────────────────────────── 单节点复证 ───────────────────────────

def _select_tests(files: list[str], node: dict[str, Any], export: str) -> tuple[list[str], bool]:
    """优先跑「提到本节点工件/编号」的测试文件；不足则按名字补齐，上限 MAX_TEST_FILES。"""
    tests = [f for f in files if _is_test_file(f)]
    if not tests:
        return [], False
    mid = node.get("mission_id")
    needles = [f"m{mid}", f"artifacts/m{mid}"]
    ext = str((node.get("metadata") or {}).get("ext_id") or "")
    if ext:
        needles.append(ext)
    hot: list[str] = []
    for rel in tests:
        path = os.path.join(export, rel)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                blob = fh.read(200_000)
        except OSError:
            continue
        if any(n and n in blob for n in needles):
            hot.append(rel)
    chosen = hot[:MAX_TEST_FILES]
    for rel in sorted(tests):
        if len(chosen) >= MAX_TEST_FILES:
            break
        if rel not in chosen:
            chosen.append(rel)
    return chosen, len(tests) > len(chosen)


def _anchor_artifacts(workdir: str, files: list[str], mid: int) -> list[dict[str, Any]]:
    """锚定**节点级**报告/工件 sha256（判据 a）：路径含 mission 编号的 JSON/CSV/MD。"""
    pats = (f"artifacts/m{mid}/", f"report_m{mid}", f"report{mid}", f"m{mid}/")
    anchored = []
    for rel in files:
        low = rel.lower()
        if not low.endswith((".json", ".csv", ".md")):
            continue
        if not any(p in rel for p in pats):
            continue
        try:
            blob = core.git(workdir, "show", f"HEAD:{rel}", timeout=120).stdout
        except Exception:
            continue
        if not blob:
            continue
        import hashlib
        anchored.append({"path": rel, "sha256": hashlib.sha256(blob.encode()).hexdigest(),
                         "bytes": len(blob)})
    return sorted(anchored, key=lambda x: x["path"])


def _numeric_tier(workdir: str, export: str, anchored: list[dict[str, Any]],
                  rel_tol: float, run_started_at: float | None = None) -> dict[str, Any]:
    """数值复算层：干净检出里被测试**重新生成**的节点级 JSON，逐数比对报告值。

    只比「两侧都存在的路径」；未重新生成 → 该节点数值层 blocked（不进通过率分母）。
    """
    json_files = [a for a in anchored if a["path"].endswith(".json")]
    if not json_files:
        return {"status": "blocked", "reason": "no_node_scoped_numeric_artifact", "checked": 0}
    compared: list[dict[str, Any]] = []
    regenerated = 0
    for item in json_files[:5]:
        rel = item["path"]
        path = os.path.join(export, rel)
        if not os.path.exists(path):
            continue
        # 「重新生成」的判据是 mtime 越过执行起点，而不是文件存在：
        # 干净检出里本来就有该工件，存在性不能当复算证据。
        if run_started_at is not None and os.path.getmtime(path) <= run_started_at:
            continue
        try:
            current = core.load_json(path)
            reported = json.loads(core.git(workdir, "show", f"HEAD:{rel}", timeout=120).stdout)
        except Exception:
            continue
        bit_identical = core.sha256_file(path) == item["sha256"]
        regenerated += 1
        for cmp_item in core.compare_number_maps(core.flatten_numbers(reported),
                                                 core.flatten_numbers(current), rel_tol=rel_tol):
            compared.append({**cmp_item, "artifact": rel, "bit_identical": bit_identical})
    if not regenerated:
        return {"status": "blocked", "reason": "artifact_not_regenerated_by_tests", "checked": 0}
    agg = core.agreement(compared)
    return {"status": "pass" if agg["drifted"] == 0 else "drift", **agg,
            "items": compared[:80], "regenerated_files": regenerated}


def audit_node(cand: dict[str, Any], run: int = 1, keep: bool = False) -> dict[str, Any]:
    """复证单个节点：干净检出 → 跑测试（超时/依赖/采集失败 → blocked）→ 数值层比对。"""
    node = cand["node"]
    mid = int(node["mission_id"])
    workdir = cand["workdir"]
    meta = node.get("metadata") or {}
    started = time.time()
    record: dict[str, Any] = {
        "node_id": node.get("id"), "mission_id": mid, "name": node.get("name"),
        "repo": meta.get("repo"), "plan": node.get("_plan"),
        "stratum": core.stratum_of(node), "run": run,
        "criteria": list(meta.get("acceptance_criteria") or []),
    }
    tmp = core.make_temp()
    try:
        try:
            record["commit"] = core.head_commit(workdir)
            record["tree_sha"] = core.tree_sha(workdir)
            files = core.committed_files(workdir)
            export = os.path.join(tmp, "export")
            export_info = core.clean_export(workdir, export, timeout=PER_NODE_TIMEOUT // 3)
            record["export_bytes"] = export_info["bytes"]
        except Exception as exc:  # 导出失败 = 环境不可得 → blocked
            record.update({"verdict": "blocked", "blocked_reason": f"clean_export_failed: {exc}"[:300],
                           "wall_sec": round(time.time() - started, 2)})
            return record

        anchored = _anchor_artifacts(workdir, files, mid)
        record["anchored_artifacts"] = anchored
        record["anchored_count"] = len(anchored)

        tests, subset = _select_tests(files, node, export)
        record["test_files"] = tests
        record["test_subset"] = subset
        if not tests:
            record.update({"verdict": "blocked", "blocked_reason": "no_test_files",
                           "wall_sec": round(time.time() - started, 2)})
            return record

        rel_tol = core.parse_tolerance(meta.get("acceptance_criteria"))
        record["rel_tol"] = rel_tol
        env = {"PYTHONPATH": os.pathsep.join(
            p for p in (export, os.path.join(export, "src")) if os.path.isdir(p))}
        cmd = [sys.executable, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider",
               "--timeout", str(PYTEST_TIMEOUT)]
        run_started_at = time.time()
        exec1 = core.run_command(cmd, export, timeout=PYTEST_TIMEOUT + 30, env=env)
        parsed1 = core.parse_pytest(exec1["stdout"], exec1["stderr"])
        execution = {"run": exec1, "parsed": parsed1}

        # 非通过且非 blocked 征兆 → 二次重跑，判 drift 可复现性（判据 c：≥2 次）
        blob = f"{exec1['stdout']}\n{exec1['stderr']}"
        needs_rerun = (exec1["exit_code"] != 0 and not exec1["timed_out"]
                       and not re.search(r"ModuleNotFoundError|ERROR collecting|error during collection", blob))
        rerun = None
        if needs_rerun:
            exec2 = core.run_command(cmd, export, timeout=PYTEST_TIMEOUT + 30, env=env)
            parsed2 = core.parse_pytest(exec2["stdout"], exec2["stderr"])
            rerun = {"run": exec2, "parsed": parsed2}
            execution["reproducible"] = (core.failing_signature(parsed1["failing_cases"])
                                         == core.failing_signature(parsed2["failing_cases"])
                                         and parsed1["failing_cases"] == parsed2["failing_cases"])
            execution["rerun"] = exec2
            record["rerun"] = {"exit_code": exec2["exit_code"], "counts": parsed2["counts"],
                               "failing_cases": parsed2["failing_cases"][:20],
                               "duration_sec": exec2["duration_sec"], "timed_out": exec2["timed_out"]}

        current_env = {"python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
                       "machine": os.uname().machine, "cpu_count": os.cpu_count()}
        try:
            import numpy
            current_env["numpy"] = numpy.__version__
        except Exception:
            pass
        recorded_env: dict[str, Any] = {}
        for item in anchored:
            if item["path"].endswith(".json"):
                try:
                    data = json.loads(core.git(workdir, "show", f"HEAD:{item['path']}").stdout)
                    for key in ("environment", "env", "python_version", "numpy"):
                        if isinstance(data, dict) and key in data:
                            val = data[key]
                            recorded_env.update(val if isinstance(val, dict) else {key: val})
                except Exception:
                    pass
                break
        if needs_rerun:
            execution["attribution"] = core.attribute_drift(exec1, rerun["run"] if rerun else None,
                                                            recorded_env, current_env)

        verdict = core.verdict_of(execution)
        record.update({
            "verdict": verdict,
            "exit_code": exec1["exit_code"], "timed_out": exec1["timed_out"],
            "counts": parsed1["counts"],
            "failing_cases": parsed1["failing_cases"][:20],
            "duration_sec": exec1["duration_sec"],
        })
        if needs_rerun:
            record["attribution"] = execution.get("attribution")
            record["reproducible"] = execution.get("reproducible")
        if verdict == "blocked":
            record["blocked_reason"] = ("timeout" if exec1["timed_out"] else
                                        "dependency_or_collection_error" if
                                        re.search(r"ModuleNotFoundError|ERROR collecting|error during collection", blob)
                                        else "no_test_executed")
        record["numeric"] = _numeric_tier(workdir, export, anchored, rel_tol, run_started_at)
        record["wall_sec"] = round(time.time() - started, 2)
        return record
    finally:
        if not keep:
            core.cleanup(tmp)
        else:
            record["kept_dir"] = tmp


# ─────────────────────────── 汇总 ───────────────────────────

def summarise(items: list[dict[str, Any]], sample: list[dict[str, Any]], seed: str,
              n_requested: int) -> dict[str, Any]:
    """聚合审计结论：KPI（逐项可复算）+ drift 归因表 + blocked 量化 + 分层覆盖。"""
    kpi = core.kpi_from_items(items)
    drifts = [{"mission_id": i["mission_id"], "repo": i["repo"],
               "attribution": i.get("attribution"),
               "reproducible": i.get("reproducible"),
               "failing_cases": i.get("failing_cases")}
              for i in items if i["verdict"] == "drift"]
    blocked = [{"mission_id": i["mission_id"], "repo": i["repo"],
                "reason": i.get("blocked_reason")} for i in items if i["verdict"] == "blocked"]
    fails = [{"mission_id": i["mission_id"], "repo": i["repo"],
              "failing_cases": i.get("failing_cases")} for i in items if i["verdict"] == "fail"]
    repos = sorted({i["repo"] for i in items if i["repo"]})
    years = sorted({(i["stratum"][1] if isinstance(i.get("stratum"), (list, tuple)) else "")
                    for i in items})
    return {
        "seed": seed, "n_requested": n_requested, "n_sampled": len(sample), "n_audited": len(items),
        "kpi": kpi,
        "drift": {"n": len(drifts), "items": drifts,
                  "attribution_counts": _count_labels(drifts)},
        "blocked": {"n": len(blocked), "items": blocked,
                    "reason_counts": _count_reasons(blocked)},
        "fail": {"n": len(fails), "items": fails},
        "coverage": {"repos": repos, "n_repos": len(repos), "years": years,
                     "strata": sorted({f"{i['repo']}|{i['stratum'][1]}" for i in items})},
    }


def _count_labels(drifts: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in drifts:
        label = (d.get("attribution") or {}).get("label") or "unattributed"
        counts[label] = counts.get(label, 0) + 1
    return counts


def _count_reasons(blocked: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for b in blocked:
        reason = str(b.get("reason") or "unknown").split(":")[0]
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def run_audit(nodes: list[dict[str, Any]], n: int, seed: str, workers: int, work_root: str) -> dict[str, Any]:
    cands = node_candidates(nodes, work_root)
    by_mid = {int(c["node"]["mission_id"]): c for c in cands}
    sample = core.sample_stratified([c["node"] for c in cands], n, seed=seed)
    picked = [by_mid[int(nd["mission_id"])] for nd in sample if int(nd["mission_id"]) in by_mid]
    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        items = list(pool.map(audit_node, picked))
    items.sort(key=lambda x: (x["repo"] or "", x["mission_id"]))
    summary = summarise(items, [c["node"] for c in picked], seed, n)
    summary["wall_sec_total"] = round(time.time() - started, 2)
    summary["candidates_available"] = len(cands)
    summary["items"] = items
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="历史验收声明抽样式重跑复证")
    ap.add_argument("--snapshot-nodes", nargs="?", const=DEFAULT_NODES_SNAPSHOT,
                    help="拉取战役节点并写快照 JSON")
    ap.add_argument("--nodes-json", default=None, help="已有节点快照（默认 artifacts/repro_audit/nodes_snapshot.json）")
    ap.add_argument("--audit", action="store_true", help="执行抽样复证")
    ap.add_argument("--n", type=int, default=32)
    ap.add_argument("--seed", default="repro-audit-v1")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--work-root", default=DEFAULT_WORK_ROOT)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--persist", action="store_true", help="把 repro_rate 落库（三级降级）")
    ap.add_argument("--sql-out", default="artifacts/repro_audit/kpi_upsert.sql")
    args = ap.parse_args(argv)

    if args.snapshot_nodes:
        nodes = fetch_nodes_via_api()
        core.dump_json(args.snapshot_nodes, {"generated_for": "repro_audit", "nodes": nodes})
        print(f"snapshot: {len(nodes)} nodes -> {args.snapshot_nodes}")
        if not args.audit:
            return 0

    if not args.audit:
        ap.print_help()
        return 2

    nodes_path = args.nodes_json or DEFAULT_NODES_SNAPSHOT
    nodes = core.load_json(nodes_path)["nodes"]
    summary = run_audit(nodes, args.n, args.seed, args.workers, args.work_root)
    if args.persist:
        summary["kpi_persist"] = persist_kpi(summary, sql_out=args.sql_out)
    core.dump_json(args.out, summary)
    kpi = summary["kpi"]
    print(json.dumps({"kpi": kpi, "coverage": summary["coverage"],
                      "drift": summary["drift"]["n"], "blocked": summary["blocked"]["n"],
                      "persist": summary.get("kpi_persist", {}).get("status")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())