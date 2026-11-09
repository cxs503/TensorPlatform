#!/usr/bin/env python3
"""collect_evidence —— 从战役节点账本 + 各节点工作树采集证据包（域无关）。

输入
----
* ``--plan-nodes``：战役节点账本 JSON（平台 ``/tensor/campaigns/plans/{id}/nodes``
  的响应，含每个节点的 ``metadata.ext_id / mission_id / repo / acceptance_criteria /
  dependencies / actual_start``）。域内容全部来自此数据文件，代码里零硬编码。
* ``--work-root``：工作树根（默认 ``/root/tp-agent/work``），约定每个节点的工作树为
  ``<work-root>/m<mission_id>``。
* ``--missions``（可选）：``/tensor/missions/{id}`` 快照，用于取运行状态/异常信息。

输出（bundle JSON，供 ``campaign_distill.py`` 消费）
--------------------------------------------------
::

    {"bundle_version":"1.0","campaign":{...},"snapshot_utc":"...","nodes":[
      {"node_key":"N0k","mission_id":..., "repo":..., "title":..., "status":"in-flight",
       "actual_start":"...","depends_on":["N0k+1"],
       "criteria":[{"id":"C1","text":"..."}],
       "evidence":{"files":[{path,sha256,bytes,kind}],"probes":[{name,exit_code,summary}],
                   "claims":[]}}]}

采集规则（可复核）
------------------
1. 证据只认**工作树内新增/修改的文件**（``git status --porcelain -uall``），
   逐文件算 sha256；已跟踪且未修改的旧文件不算本节点证据。
2. 探针只对新增 ``test*`` 文件跑一次 pytest（``PYTHONDONTWRITEBYTECODE=1``、
   ``-p no:cacheprovider``，因此不污染对方工作树），记录退出码与末行摘要。
3. **不产生结论**（``claims`` 恒为空，除非报告 JSON 显式给出）：本采集器只搬运证据，
   判据是否达成由下游 ``campaign_distill.build_coverage`` 依"结论+来源"机裁；
   没有结论就老实记 ``EVIDENCE_ONLY``/``NO_EVIDENCE``，禁止推测。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

TEXTISH = {".py", ".json", ".md", ".yaml", ".yml", ".txt", ".csv"}
BINARYISH = {".png", ".pdf", ".npz", ".npy", ".h5"}
MAX_HASH_BYTES = 8 * 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(tree: Path, *args: str, timeout: int = 60) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(tree), capture_output=True, text=True, timeout=timeout
        )
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def classify(path: str) -> str:
    low = path.lower()
    name = Path(path).name.lower()
    if name.startswith("test") or "/tests/" in low or low.endswith("_test.py"):
        return "test"
    if low.endswith(".md") and "docs" in low:
        return "report"
    suffix = Path(path).suffix.lower()
    if suffix in BINARYISH:
        return "figure"
    if suffix in TEXTISH:
        return "impl" if suffix == ".py" else "artifact"
    return "other"


def node_key_from_ext(ext_id: str) -> str:
    """``P66-W2-N05`` → ``N05``（只取末段，与具体战役前缀无关）。"""
    return str(ext_id).rsplit("-", 1)[-1]


def parse_criteria(items: list[str]) -> list[dict]:
    out = []
    for raw in items or []:
        text = str(raw).strip()
        head, _, rest = text.partition(" ")
        cid = head if head[:1] in ("C", "c") and head[1:].isdigit() else f"C{len(out) + 1}"
        out.append({"id": cid.upper(), "text": rest.strip() or text})
    return out


def probe_pytest(tree: Path, rel: str, timeout: int) -> dict:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    name = f"pytest::{rel}"
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", rel, "-q", "-p", "no:cacheprovider"],
            cwd=str(tree), capture_output=True, text=True, timeout=timeout, env=env,
        )
        tail = [ln for ln in (out.stdout or "").strip().splitlines() if ln.strip()]
        summary = tail[-1][:200] if tail else (out.stderr or "").strip().splitlines()[-1][:200] if out.stderr.strip() else ""
        return {"name": name, "exit_code": out.returncode, "summary": summary}
    except subprocess.TimeoutExpired:
        return {"name": name, "exit_code": -9, "summary": f"timeout>{timeout}s"}


def collect(plan_nodes: dict, work_root: Path, missions: dict | None, do_probe: bool,
            probe_timeout: int, snapshot_utc: str) -> dict:
    nodes_raw = plan_nodes.get("nodes") or plan_nodes
    plan = plan_nodes.get("plan") or {}
    out_nodes = []
    for node in nodes_raw:
        md = node.get("metadata") or {}
        ext = md.get("ext_id") or node.get("name", "")[:8]
        key = node_key_from_ext(ext)
        mission_id = node.get("mission_id")
        tree = work_root / f"m{mission_id}"
        mission = (missions or {}).get(str(mission_id)) or {}
        status = mission.get("status") or node.get("status") or "unknown"
        depends = []
        id2key = {
            n["id"]: node_key_from_ext((n.get("metadata") or {}).get("ext_id", ""))
            for n in nodes_raw
        }
        for dep in node.get("dependencies") or []:
            if dep in id2key and id2key[dep]:
                depends.append(id2key[dep])

        files, probes, claims = [], [], []
        git_head = ""
        if tree.exists():
            git_head = git(tree, "rev-parse", "HEAD").strip()
            porcelain = git(tree, "status", "--porcelain", "-uall")
            for line in porcelain.splitlines():
                if len(line) < 4:
                    continue
                rel = line[3:].strip().strip('"')
                path = tree / rel
                if not path.is_file():
                    continue
                if path.stat().st_size > MAX_HASH_BYTES:
                    continue
                kind = classify(rel)
                files.append({
                    "path": str(path), "rel_path": rel, "kind": kind,
                    "bytes": path.stat().st_size, "sha256": sha256_file(path),
                })
            files.sort(key=lambda f: f["rel_path"])
            if do_probe:
                tests = [f for f in files if f["kind"] == "test"][:2]
                for t in tests:
                    probes.append(probe_pytest(tree, t["rel_path"], probe_timeout))
            # 显式结论（若节点自带机器可读结论工件则读入，否则保持空=不推测）
            for cand in ("claims.json", "criteria_claims.json"):
                p = tree / "artifacts" / cand
                if p.exists():
                    try:
                        loaded = json.loads(p.read_text(encoding="utf-8"))
                        claims.extend(loaded if isinstance(loaded, list) else [])
                    except Exception:
                        pass

        out_nodes.append({
            "node_key": key,
            "ext_id": ext,
            "mission_id": mission_id,
            "repo": md.get("repo") or mission.get("repo"),
            "title": node.get("name", ""),
            "physics_domain": md.get("physics_domain", ""),
            "status": status,
            "declared_node_status": node.get("status"),
            "actual_start": node.get("actual_start"),
            "depends_on": depends,
            "worktree": str(tree),
            "git_head": git_head,
            "criteria": parse_criteria(md.get("acceptance_criteria") or []),
            "evidence": {"files": files, "probes": probes, "claims": claims},
        })
    return {
        "bundle_version": "1.0",
        "campaign": {
            "plan_id": plan.get("id"),
            "title": plan.get("title") or plan.get("name") or "campaign",
            "label": f"plan {plan.get('id')}",
            "wave": (plan.get("metadata") or {}).get("wave"),
        },
        "snapshot_utc": snapshot_utc,
        "work_root": str(work_root),
        "generated_by": "tools/distill/collect_evidence.py",
        "nodes": out_nodes,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="采集战役节点证据包（域无关）")
    ap.add_argument("--plan-nodes", required=True)
    ap.add_argument("--missions", default=None)
    ap.add_argument("--work-root", default=os.environ.get("TP_WORK_ROOT", "work"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--probe-timeout", type=int, default=600)
    ap.add_argument("--snapshot-utc", default=None)
    args = ap.parse_args(argv)

    plan_nodes = json.loads(Path(args.plan_nodes).read_text(encoding="utf-8"))
    missions = json.loads(Path(args.missions).read_text(encoding="utf-8")) if args.missions else None
    snap = args.snapshot_utc or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    bundle = collect(plan_nodes, Path(args.work_root), missions, args.probe,
                     args.probe_timeout, snap.replace("+00:00", "Z"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    n_files = sum(len(n["evidence"]["files"]) for n in bundle["nodes"])
    n_probe = sum(len(n["evidence"]["probes"]) for n in bundle["nodes"])
    n_crit = sum(len(n["criteria"]) for n in bundle["nodes"])
    print(json.dumps({"nodes": len(bundle["nodes"]), "criteria": n_crit,
                      "evidence_files": n_files, "probes": n_probe,
                      "snapshot_utc": bundle["snapshot_utc"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())