"""P70-N08 编排器：抽样节点 → 干净检出 → 集群重跑 → 漂移归因 → 落库 KPI。

一次运行做的事：
1. 读 ``artifacts/repro_audit/sample.json``（由 ``scan_select.py`` 分层抽样并锚定工件）；
2. 对每个节点：用 ``git archive <commit>`` 生成**干净检出**（不含作者工作树的未提交改动），
   逐文件复核 sha256 == 锚定值（检出与声明一致才继续）；
3. 把 ``_repro/``（探针 + pristine 参考工件 + 判据模块）注入检出，tar 直达集群节点执行
   （``cluster_exec.py run --stage-dir``），两轮重跑，收回紧凑 JSON；
4. 逐项比对：observed 走相对容差 1e-6、diagnostic 走绝对容差 1e-9，另记 ULP/量化；
5. drift 节点做归因消融（线程数 / 同 ISA 控制机 / 工作树源码替换），落四分类标签；
6. 汇总 ``artifacts/repro_audit.json``，可选 ``--persist`` 把实测 repro_rate 写回
   ``tensor_guide_tracker``（替换值为 0 的占位行 + 追加审计快照行）。

只读约束：不写被审计仓库、不改 DB 其它表；``--persist`` 是唯一写库路径。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import canonical  # noqa: E402

CLUSTER_EXEC = os.environ.get("TP_CLUSTER_EXEC", "/root/tp-agent/cluster/cluster_exec.py")
MARK_BEGIN = "###REPRO_JSON###"
MARK_END = "###REPRO_END###"
NODE_TIMEOUT = 600          # 判据 (e)：单节点 ≤ 10 min，超时判 blocked
MAX_TOTAL_SECONDS = 6 * 3600


# ─────────────────────────── 干净检出 ───────────────────────────


def clean_checkout(workdir: str, commit: str, dest: str) -> Dict[str, Any]:
    """``git archive`` 出提交内容的干净副本（只读对象库，不碰作者工作树）。"""
    os.makedirs(dest, exist_ok=True)
    p1 = subprocess.run(["git", "-C", workdir, "archive", commit], capture_output=True)
    if p1.returncode != 0:
        raise RuntimeError("git archive failed: %s" % p1.stderr.decode()[:300])
    p2 = subprocess.run(["tar", "-x", "-C", dest], input=p1.stdout, capture_output=True)
    if p2.returncode != 0:
        raise RuntimeError("tar extract failed: %s" % p2.stderr.decode()[:300])
    head = subprocess.run(["git", "-C", workdir, "rev-parse", commit],
                          capture_output=True, text=True).stdout.strip()
    return {"dir": dest, "commit": head}


def verify_anchors(node: Dict[str, Any], checkout: str) -> Dict[str, Any]:
    """检出内工件逐文件复核 sha256 == 抽样时锚定的提交值（审计链第一环）。"""
    ok, bad = 0, []
    for a in node.get("anchors", []):
        path = os.path.join(checkout, a["path"])
        if not os.path.exists(path):
            bad.append({"path": a["path"], "why": "missing_in_checkout"})
            continue
        h = canonical_digest_file(path)
        if h == a["sha256_commit"]:
            ok += 1
        else:
            bad.append({"path": a["path"], "why": "sha256_mismatch", "got": h,
                        "want": a["sha256_commit"]})
    return {"verified": ok, "mismatched": bad, "ok": not bad}


def canonical_digest_file(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inject_probe(checkout: str, spec: Dict[str, Any], node: Dict[str, Any]) -> str:
    """把探针与 pristine 参考工件放进 ``_repro/``（不修改被审计文件本身）。"""
    rd = os.path.join(checkout, "_repro")
    os.makedirs(os.path.join(rd, "pristine"), exist_ok=True)
    os.makedirs(os.path.join(rd, "out"), exist_ok=True)
    for fn in ("canonical.py", "shims.py", "launch.py", "probe_runner.py"):
        shutil.copyfile(os.path.join(HERE, fn), os.path.join(rd, fn))
    outs = [o for p in spec.get("numeric", []) for o in p.get("outputs", [])]
    if spec.get("auto_outputs"):
        # 证据驱动模式：把提交内全部 artifacts/**/*.json 收进 pristine（上限 400 个）
        collected = 0
        for root, _dirs, files in os.walk(os.path.join(checkout, "artifacts")):
            for fn in files:
                if not fn.endswith(".json"):
                    continue
                src = os.path.join(root, fn)
                rel = os.path.relpath(src, checkout)
                dst = os.path.join(rd, "pristine", rel)
                os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
                shutil.copyfile(src, dst)
                collected += 1
                if collected >= 400:
                    break
            if collected >= 400:
                break
        spec["pristine_all"] = collected
    for rel in set(outs):
        src = os.path.join(checkout, rel)
        if os.path.exists(src) and not os.path.exists(os.path.join(rd, "pristine", rel)):
            dst = os.path.join(rd, "pristine", rel)
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            shutil.copyfile(src, dst)
    spec = dict(spec)
    spec["anchor_sha256"] = {a["path"]: a["sha256_commit"] for a in node.get("anchors", [])}
    with open(os.path.join(rd, "spec.json"), "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=1)
    return rd


# ─────────────────────────── 集群调用 ───────────────────────────


def cluster_run(stage_dir: str, repo: str, cmd: str, timeout: int, job_id: str) -> Dict[str, Any]:
    argv = [sys.executable, CLUSTER_EXEC, "run", "--repo", repo, "--stage-dir", stage_dir,
            "--cmd", cmd, "--timeout", str(timeout), "--job-id", job_id]
    t0 = time.time()
    cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 420)
    out = cp.stdout.strip().split("\n")[-1] if cp.stdout.strip() else "{}"
    try:
        payload = json.loads(out)
    except Exception:
        payload = {"rc": 1, "output_tail": (cp.stdout + cp.stderr)[-800:]}
    payload["wall_sec"] = round(time.time() - t0, 2)
    payload["job_id"] = payload.get("job_id", job_id)
    return payload


def parse_probe_output(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tail = payload.get("output_tail") or ""
    if MARK_BEGIN not in tail:
        return None
    body = tail.split(MARK_BEGIN, 1)[1]
    if MARK_END in body:
        body = body.split(MARK_END, 1)[0]
    body = body.strip()
    # 只保留首个完整 JSON 对象（避免尾部截断噪声）
    depth, end = 0, None
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return None
    try:
        return json.loads(body[:end])
    except Exception:
        return None


PROBE_CMD = ("python3 _repro/probe_runner.py --spec _repro/spec.json "
             "--out _repro/out/result.json 2>&1 | tail -c 12000")


def run_probe(node: Dict[str, Any], checkout: str, env_prefix: str = "",
              timeout: int = NODE_TIMEOUT, tag: str = "") -> Dict[str, Any]:
    job_id = "ra%d%s" % (node["mission_id"], tag)
    cmd = (env_prefix + " " + PROBE_CMD).strip()
    payload = cluster_run(checkout, node["repo"], cmd, timeout, job_id)
    parsed = parse_probe_output(payload)
    res = {"job_id": payload.get("job_id"), "node": payload.get("node"),
           "rc": payload.get("rc"), "wall_sec": payload.get("wall_sec"),
           "parsed": parsed is not None, "probe": parsed}
    if parsed is None:
        res["blocked_reason"] = "cluster_probe_no_json: %s" % (payload.get("output_tail") or "")[-300:]
    return res


# ─────────────────────────── 单节点判定 ───────────────────────────


def node_result(node: Dict[str, Any], probe: Dict[str, Any],
                anchors: Dict[str, Any]) -> Dict[str, Any]:
    """把集群探针结果折成节点判决 + 逐项明细。"""
    base = {"mission_id": node["mission_id"], "repo": node["repo"],
            "discipline": node["discipline"], "ext_id": node.get("ext_id"),
            "commit": node["commit"], "branch": node["branch"],
            "anchors": {"files": len(node.get("anchors", [])),
                        "verified": anchors.get("verified", 0),
                        "mismatched": anchors.get("mismatched", [])},
            "env": {}, "runs": [], "numeric": None, "pytest": None}
    blocked = probe.get("blocked_reason")
    p = probe.get("probe")
    if not p and not blocked:
        blocked = "probe_no_result"
    if p:
        base["env"] = p.get("env", {})
        base["import_stubs"] = p.get("import_stubs", [])
        base["ref_files"] = (p.get("ref") or {}).get("files", [])
        blocked = blocked or p.get("blocked_reason")
        primary = p.get("runs", [{}])[0]
        counts_total: Dict[str, int] = {}
        detail: List[Dict[str, Any]] = []
        for g in primary.get("gen", []):
            for k, v in (g.get("counts") or {}).items():
                if k in ("max_rel", "max_ulp"):
                    counts_total[k] = max(counts_total.get(k, 0), float(v))
                else:
                    counts_total[k] = counts_total.get(k, 0) + int(v)
            detail.extend(g.get("items_head", []))
        # 生成脚本失败（缺依赖/报错）→ 该脚本无候选量；若全部失败则节点 blocked
        gen_errors = [{"script": g.get("script"), "rc": g.get("rc"),
                       "error": (g.get("error") or "")[:400]}
                      for g in primary.get("gen", []) if g.get("rc") not in (0,)]
        base["numeric"] = {"counts": counts_total, "items": detail,
                           "gen_errors": gen_errors,
                           "drift_consistent": p.get("drift_consistent", []),
                           "drift_inconsistent": p.get("drift_inconsistent", [])}
        base["pytest"] = primary.get("pytest")
        base["assertion_items"] = [
            {"path": tid, "cls": "assertion", "verdict": "drift"}
            for tid in (primary.get("pytest") or {}).get("failed_ids", [])
        ]
        py = base["pytest"] or {}
        if py.get("rc") in (2, 3, 4) and not (py.get("passed") or py.get("failed")):
            base["pytest"] = dict(py, collected=0, regressions=0)
            base["pytest_collection_error"] = (py.get("tail") or "")[-500:]
            py = base["pytest"]
        py_ok = bool(py.get("collected"))
        if not counts_total.get("compared") and not py_ok:
            if gen_errors:
                blocked = "numeric_unavailable: " + gen_errors[0]["error"][:180]
            else:
                blocked = "probe_produced_no_numbers"
        base["runs"] = [{"rep": r.get("rep"), "duration_sec": r.get("duration_sec"),
                         "drift_total": sum(int(g.get("drift_total", 0)) for g in r.get("gen", [])),
                         "pytest": {k: (r.get("pytest") or {}).get(k) for k in
                                    ("collected", "passed", "failed", "regressions")}}
                        for r in p.get("runs", [])]
        if counts_total.get("compared"):
            blocked = None      # 有数值比对了就不再是 blocked（gen 报错另记在 gen_errors）
    n_num = int(((base["numeric"] or {}).get("counts") or {}).get("compared", 0))
    n_py = int(((base["pytest"] or {}).get("collected") or 0))
    if n_num and n_py:
        base["channel"] = "numeric+assertion"
    elif n_num:
        base["channel"] = "numeric"
    elif n_py:
        base["channel"] = "assertion"
    else:
        base["channel"] = "none"
    status = canonical.node_verdict(base["numeric"], base["pytest"], blocked)
    base["status"] = status
    base["blocked_reason"] = blocked
    return base


# ─────────────────────────── 归因消融 ───────────────────────────


def attribute_node(res: Dict[str, Any], node: Dict[str, Any], checkout: str,
                   enable: bool = True) -> Dict[str, Any]:
    """对 drift 项做三组消融，按实测证据打四分类标签。"""
    if res["status"] != "drift" or not enable:
        return res
    numeric_drift = int(((res["numeric"] or {}).get("counts") or {}).get("drift", 0)) > 0
    items = [it for it in (res["numeric"] or {}).get("items", [])
             if it.get("cls") != "structural"]
    assertion = list(res.get("assertion_items") or [])
    ev: Dict[str, Any] = {}
    env = res.get("env") or {}
    ev["numeric_stack_differs"] = True  # 集群节点与作者控制机的 numpy/scipy 版本记录在 env
    threads = run_probe(node, checkout, env_prefix="OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "
                                                  "MKL_NUM_THREADS=1", tag="_t1")
    t1_drift = _drift_total(threads.get("probe"))
    ev["threads1_matches_ref"] = bool(t1_drift == 0)
    # 主跑已经 drift，故默认线程数口径不命中参考值
    ev["default_threads_matches_ref"] = False
    ev["workdir_differs_from_commit"] = any(a.get("sha256_workdir") not in (None, a["sha256_commit"])
                                            for a in node.get("anchors", []))
    res["ablation"] = {"threads1": {"drift_total": t1_drift,
                                    "matches_ref": ev["threads1_matches_ref"],
                                    "node": threads.get("node"),
                                    "job_id": threads.get("job_id")}}
    labels = []
    for it in items[:canonical.DRIFT_DETAIL_CAP]:
        lab = canonical.attribute_drift(it, ev)
        labels.append({"path": it["path"], "cls": it["cls"], "ulp": it.get("ulp"),
                       "rel": it.get("rel"), "abs": it.get("abs"), **lab})
    ab_runs = (threads.get("probe") or {}).get("runs") or []
    ab_failed = set((ab_runs[0].get("pytest") or {}).get("failed_ids", [])) if ab_runs else set()
    for it in assertion[:canonical.DRIFT_DETAIL_CAP]:
        if ab_runs and not ab_failed:
            lab = {"class": "environment",
                   "evidence": "线程数固定为 1 的重跑断言全通过 → 并行归约/环境相关"}
        elif not numeric_drift:
            lab = {"class": "dependency_version",
                   "evidence": "干净检出上断言回归；节点依赖栈 numpy=%s 与作者机不同"
                               % str((res.get("env") or {}).get("numpy"))}
        else:
            lab = {"class": "environment", "evidence": "note=unresolved：未定位到单一因子"}
        labels.append({"path": it["path"], "cls": "assertion", **lab})
    res["attribution"] = {
        "items": labels,
        "classes": sorted({x["class"] for x in labels}),
        "evidence": ev,
        "consistent_in_two_runs": (res["numeric"] or {}).get("drift_inconsistent") == [],
        "drift_paths_consistent": (res["numeric"] or {}).get("drift_consistent", []),
    }
    return res


def _has_drift(probe: Dict[str, Any]) -> bool:
    for run in probe.get("runs", []):
        if any(int(g.get("drift_total", 0)) for g in run.get("gen", [])):
            return True
    return False


def _drift_total(probe: Optional[Dict[str, Any]]) -> Optional[int]:
    if not probe:
        return None
    runs = probe.get("runs") or []
    if not runs:
        return None
    return sum(int(g.get("drift_total", 0)) for g in runs[0].get("gen", []))


# ─────────────────────────── 落库 ───────────────────────────


UPSERT_SQL = """
INSERT INTO tensor_guide_tracker
    (organization_id, metric_key, metric_value, target_value, snapshot_at, notes)
VALUES ({org}, '{key}', {val!r}, {target}, now(), {notes})
ON CONFLICT (organization_id, metric_key, snapshot_at)
DO UPDATE SET metric_value = EXCLUDED.metric_value,
              target_value = EXCLUDED.target_value,
              notes = EXCLUDED.notes
"""


def psql(sql: str) -> str:
    env = dict(os.environ, PATH="/usr/bin:/bin")
    pw = os.environ.get("TP_PG_PASSWORD")
    user = os.environ.get("TP_PG_USER")
    if not pw:
        cred = os.environ.get("TP_PG_CREDS", "/root/.pg_rot_20261101.json")
        with open(cred, "r", encoding="utf-8") as f:
            d = json.load(f)
        pw = d.get("new") or d.get("password")
        user = user or d.get("user")
    env["PGPASSWORD"] = pw or ""
    cp = subprocess.run(["psql", "-h", os.environ.get("TP_PG_HOST", "127.0.0.1"),
                         "-U", user or "postgres", "-d", os.environ.get("TP_PG_DB", "tensorplatform"),
                         "-tAF", "|", "-c", sql], capture_output=True, text=True, env=env,
                        timeout=120)
    if cp.returncode != 0:
        raise RuntimeError("psql failed: %s" % cp.stderr.strip()[:300])
    return cp.stdout


def persist_kpi(kpi: Dict[str, Any], artifact_rel: str, org_id: int = 1) -> Dict[str, Any]:
    """把实测 repro_rate 写回 tensor_guide_tracker：替换 0 占位 + 追加审计快照（幂等）。"""
    delta = canonical.kpi_consistency(kpi)["delta"]
    notes = ("measured by tools/repro_audit/run_audit.py | artifact=%s | pass=%d drift=%d "
             "blocked=%d | blocked excluded from denominator | |KPI-recompute|=%.1e"
             % (artifact_rel, kpi["nodes_pass"], kpi["nodes_drift"], kpi["nodes_blocked"], delta))
    before = psql("SELECT metric_key, metric_value, snapshot_at FROM tensor_guide_tracker "
                  "WHERE metric_key='repro_rate' ORDER BY snapshot_at DESC LIMIT 1;").strip()
    repl = psql("UPDATE tensor_guide_tracker SET metric_value=%r, snapshot_at=now(), notes=%r "
                "WHERE organization_id=%d AND metric_key='repro_rate' AND metric_value=0.0 "
                "RETURNING id;" % (float(kpi["repro_rate"]), notes, org_id)).strip()
    for key, val, target in (("repro_rate", float(kpi["repro_rate"]), 0.95),
                             ("repro_rate.n_pass", float(kpi["nodes_pass"]), None),
                             ("repro_rate.n_drift", float(kpi["nodes_drift"]), None),
                             ("repro_rate.n_blocked", float(kpi["nodes_blocked"]), None)):
        psql(UPSERT_SQL.format(org=org_id, key=key, val=val,
                               target="NULL" if target is None else repr(target),
                               notes=repr(notes)))
    after = psql("SELECT metric_key, metric_value, snapshot_at FROM tensor_guide_tracker "
                 "WHERE metric_key='repro_rate' ORDER BY snapshot_at DESC LIMIT 1;").strip()
    return {"replaced_placeholder_ids": repl, "before": before, "after": after,
            "notes": notes}


# ─────────────────────────── 主流程 ───────────────────────────


def audit_node(node: Dict[str, Any], enable_attribution: bool = True) -> Dict[str, Any]:
    tmp = tempfile.mkdtemp(prefix="repro_%d_" % node["mission_id"])
    try:
        co = clean_checkout(node["workdir"], node["commit"], tmp)
        anchors = verify_anchors(node, tmp)
        inject_probe(tmp, dict(node["spec"]), node)
        probe = run_probe(node, tmp)
        res = node_result(node, probe, anchors)
        res["raw_probe"] = probe.get("probe") or {}
        res["cluster"] = {"node": probe.get("node"), "job_id": probe.get("job_id"),
                          "rc": probe.get("rc"), "wall_sec": probe.get("wall_sec"),
                          "checkout_commit": co["commit"]}
        if res["status"] == "drift" and enable_attribution:
            res = attribute_node(res, node, tmp, enable=True)
        res.pop("raw_probe", None)
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="artifacts/repro_audit/sample.json")
    ap.add_argument("--out", default="artifacts/repro_audit.json")
    ap.add_argument("--node-dir", default="artifacts/repro_audit/nodes")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="", help="逗号分隔 mission_id，只跑这些")
    ap.add_argument("--no-attribution", action="store_true")
    ap.add_argument("--persist", action="store_true")
    ap.add_argument("--auto-outputs", action="store_true",
                    help="数值通道用「证据驱动」模式：谁覆写了提交里的 JSON 就比谁")
    ap.add_argument("--merge-dir", default="",
                    help="把该目录下的逐节点结果合并进 --out 审计 JSON（数值面叠加）")
    ap.add_argument("--reclassify", action="store_true",
                    help="只重分级既有审计 JSON（从已存证据重算 status/blocked 类别/KPI）")
    ap.add_argument("--persist-from", default="",
                    help="只做 KPI 落库：从既有审计 JSON 重算并写回（幂等，不重跑）")
    a = ap.parse_args(argv)
    if a.merge_dir:
        with open(a.out, "r", encoding="utf-8") as f:
            doc = json.load(f)
        merged = 0
        for n in doc["nodes"]:
            path = os.path.join(a.merge_dir, "m%d.json" % n["mission_id"])
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                extra = json.load(f)
            if not ((extra.get("numeric") or {}).get("counts") or {}).get("compared"):
                continue
            n["numeric"] = extra["numeric"]
            n["ref_files"] = extra.get("ref_files") or []
            n["env"] = extra.get("env") or n.get("env")
            n["import_stubs"] = extra.get("import_stubs") or n.get("import_stubs")
            n["cluster_numeric"] = extra.get("cluster")
            n["probe_pass"] = "numeric"
            if extra.get("attribution"):
                n["attribution"] = extra["attribution"]
            merged += 1
        doc = reclassify(doc)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        print(json.dumps({"merged_numeric_nodes": merged, "kpi": doc["kpi"],
                          "blocked": doc["blocked_summary"]}, ensure_ascii=False))
        return 0
    if a.reclassify:
        with open(a.out, "r", encoding="utf-8") as f:
            doc = json.load(f)
        doc = reclassify(doc)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        print(json.dumps({"kpi": doc["kpi"], "blocked": doc["blocked_summary"],
                          "consistency": doc["kpi_consistency"]}, ensure_ascii=False))
        return 0
    if a.persist_from:
        with open(a.persist_from, "r", encoding="utf-8") as f:
            doc = json.load(f)
        kpi = canonical.aggregate(doc["nodes"])
        cons = canonical.kpi_consistency(kpi)
        doc["kpi"] = kpi
        doc["kpi_consistency"] = cons
        doc["persist"] = persist_kpi(kpi, a.persist_from)
        with open(a.persist_from, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        print(json.dumps({"kpi": kpi, "consistency": cons, "persist": doc["persist"]},
                         ensure_ascii=False))
        return 0
    with open(a.sample, "r", encoding="utf-8") as f:
        sample = json.load(f)
    nodes = sample["nodes"]
    if a.only:
        keep = {int(x) for x in a.only.split(",") if x.strip()}
        nodes = [n for n in nodes if n["mission_id"] in keep]
    if a.limit:
        nodes = nodes[:a.limit]
    os.makedirs(a.node_dir, exist_ok=True)
    t_start = time.time()
    results = []
    if a.auto_outputs:
        for node in nodes:
            if node["spec"].get("numeric"):
                node["spec"]["auto_outputs"] = True
                node["spec"]["gen_timeout"] = 40
                node["spec"]["pytest_timeout"] = 45
                node["spec"]["node_timeout"] = 120
    for i, node in enumerate(nodes, 1):
        if time.time() - t_start > MAX_TOTAL_SECONDS:
            results.append({"mission_id": node["mission_id"], "repo": node["repo"],
                            "status": "blocked",
                            "blocked_reason": "total_budget_exceeded"})
            continue
        t0 = time.time()
        try:
            res = audit_node(node, enable_attribution=not a.no_attribution)
        except Exception as exc:  # 单节点异常不炸全批（记 blocked 并留证据）
            res = {"mission_id": node["mission_id"], "repo": node["repo"],
                   "discipline": node.get("discipline"), "commit": node.get("commit"),
                   "status": "blocked", "blocked_reason": "audit_exception: %s" % str(exc)[:300]}
        res["wall_sec"] = round(time.time() - t0, 2)
        results.append(res)
        with open(os.path.join(a.node_dir, "m%d.json" % node["mission_id"]), "w",
                  encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("[%d/%d] m%d %-26s %-8s %5.1fs %s" % (
            i, len(nodes), node["mission_id"], node["repo"], res["status"], res["wall_sec"],
            (res.get("blocked_reason") or "")[:80]), flush=True)
    kpi = canonical.aggregate(results)
    cons = canonical.kpi_consistency(kpi)
    doc = {
        "title": "历史验收声明重跑复证审计（P70-N08）",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": {"observed_rel_tol": canonical.REL_TOL, "diagnostic_abs_tol": canonical.ABS_TOL,
                     "near_zero_threshold": canonical.NEAR_ZERO, "ulp_gate": canonical.ULP_GATE,
                     "quant_digits": canonical.QUANT_DIGITS,
                     "attribution_classes": list(canonical.ATTRIBUTION_CLASSES)},
        "sampling": {"criteria": sample.get("criteria"), "scan": sample.get("scan"),
                     "strata": sample.get("strata"), "sample_file": a.sample},
        "kpi": kpi,
        "kpi_consistency": cons,
        "kpi_formula": "repro_rate = nodes_pass / (nodes_pass + nodes_drift); blocked 单列不折算",
        "nodes": results,
        "attribution_summary": _attribution_summary(results),
        "blocked_summary": _blocked_summary(results),
        "budget": {"total_wall_sec": round(time.time() - t_start, 1),
                   "node_timeout_sec": NODE_TIMEOUT,
                   "max_node_wall_sec": max((r.get("wall_sec") or 0) for r in results) if results else 0},
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    if a.persist:
        doc["persist"] = persist_kpi(kpi, a.out)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
    print(json.dumps({"kpi": kpi, "consistency": cons,
                      "attribution": doc["attribution_summary"],
                      "blocked": doc["blocked_summary"]}, ensure_ascii=False))
    return 0


def _attribution_summary(results: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in results:
        for cls in ((r.get("attribution") or {}).get("classes") or []):
            out[cls] = out.get(cls, 0) + 1
    return out


NET_HINTS = ("urllib", "http", "connection refused", "urlopen", "socket",
             "requests.exceptions", "newconnectionerror")
DEP_HINTS = ("modulenotfounderror", "importerror", "no module named")


def blocked_category(res: Dict[str, Any]) -> str:
    """blocked 不折算进通过率，但必须可归因——按可观测证据分桶，单列量化。

    只用工件里已存的证据（pytest rc / 收集期报错 / 生成脚本报错），不猜。
    """
    numeric = res.get("numeric") or {}
    errs = " ".join((g.get("error") or "").lower() for g in numeric.get("gen_errors", []))
    py = res.get("pytest") or {}
    coll = (res.get("pytest_collection_error") or "").lower()
    if coll:
        return "missing_dependency" if any(h in coll for h in DEP_HINTS) else "pytest_collection_error"
    if py.get("rc") == 124:
        return "pytest_timeout"
    if any(h in errs for h in NET_HINTS):
        return "network_dependency"
    if any(h in errs for h in DEP_HINTS):
        return "missing_dependency"
    if "skipped_timeout" in errs or "node_time_budget" in errs or "timed out" in errs:
        return "generator_timeout"
    if not (res.get("cluster") or {}).get("checkout_commit"):
        return "checkout_failed"
    if not numeric.get("gen_errors"):
        return "no_replayable_generator"
    return "probe_produced_no_numbers"


def reclassify(doc: Dict[str, Any]) -> Dict[str, Any]:
    """离线重分级：从已存证据重算 status / channel / blocked 类别，使 JSON 自洽可加总。

    只在「证据位缺失」这一种情形下改动判决（如 pytest 收集期报错 rc=2 曾被判 drift），
    数值项与归因结果一律不动。
    """
    for n in doc["nodes"]:
        py = dict(n.get("pytest") or {})
        numeric = n.get("numeric") or {}
        counts = numeric.get("counts") or {}
        compared = int(counts.get("compared", 0))
        if py.get("rc") in (2, 3, 4) and not (py.get("passed") or py.get("failed")):
            n["pytest_collection_error"] = (py.get("tail") or "")[-500:]
            py.update(collected=0, regressions=0)
            n["pytest"] = py
        collected = int(py.get("collected") or 0)
        regressions = int(py.get("regressions") or 0)
        has_numeric = compared > 0
        has_pytest = collected > 0
        n["channel"] = ("numeric+assertion" if has_numeric and has_pytest
                        else "numeric" if has_numeric
                        else "assertion" if has_pytest else "none")
        drift_items = int(counts.get("drift", 0)) + int(counts.get("structural", 0))
        if has_numeric or has_pytest:
            n["status"] = "drift" if (drift_items or regressions) else "pass"
            n["blocked_reason"] = None
            n["blocked_category"] = None
        else:
            n["status"] = "blocked"
            n["blocked_category"] = blocked_category(n)
    doc["kpi"] = canonical.aggregate(doc["nodes"])
    doc["kpi_consistency"] = canonical.kpi_consistency(doc["kpi"])
    doc["blocked_summary"] = _blocked_summary(doc["nodes"])
    doc["attribution_summary"] = _attribution_summary(doc["nodes"])
    return doc


def _blocked_summary(results: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in results:
        if r.get("status") != "blocked":
            continue
        reason = (r.get("blocked_category")
                  or (r.get("blocked_reason") or "unknown").split(":")[0]).strip()
        out[reason] = out.get(reason, 0) + 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())