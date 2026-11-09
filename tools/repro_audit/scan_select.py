"""抽样器：从 campaign_nodes 的 done 节点里分层抽样「带可执行工件」的验收声明，并锚定工件。

判据（任务书 P70-N08 实现路径 (1)）：
* 候选 = ``campaign_nodes.status='done'`` 且挂了 mission、有 work 目录的节点；
* 「带可执行工件」= 该节点的分支 diff 里同时具备
  (a) 测试文件（``tests/**/test_*.py`` 或 ``test_*.py``）与
  (b) 可重放的 JSON 数值工件（``artifacts/**/*.json`` 且展平后数值叶 ≥ ``MIN_NUMBERS``）；
  只有 (b) 时若无生成脚本也可入选（记为 no_generator，重跑阶段判 blocked）；
* 分层维度 = repo × 学科 × 报告年份；`N=36` 按层轮转分配（每层至少 1，受 ``--per-repo-cap`` 约束）；
* 锚定 = 对每个入选节点的报告 md / JSON 工件 / 生成脚本 / 测试文件，
  取 ``git show <commit>:<path>`` 的字节算 sha256（**提交版本**，可被任何人复核），
  并记录工作树版本 sha256 以暴露「提交≠作者工作树」的漂移源。

只读：不写库、不改任何被审计仓库；输出 ``artifacts/repro_audit/sample.json``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

WORK_ROOT = os.environ.get("TP_WORK_ROOT", "/root/tp-agent/work")
MIN_NUMBERS = 10
DEFAULT_N = 36

DISCIPLINE_RULES = [
    ("冰区", ("冰区", "碎冰", "ice", "icebreaker")),
    ("振动噪声", ("振动", "噪声", "声学", "vibrat", "acoustic", "noise", "舱室空气声")),
    ("绿色航行", ("绿色", "eedi", "cii", "排放", "碳", "能效", "氢", "燃料", "空气润滑", "润滑减阻")),
    ("稳性", ("稳性", "stabilit", "gz(", "横倾", "复原力矩", "破损")),
    ("操纵", ("操纵", "manoeuvr", "maneuver", "回转", "舵", "z形", "航向")),
    ("耐波", ("耐波", "seakeep", "波浪", "横摇", "纵摇", "垂荡", "谱", "运动响应")),
    ("推进", ("推进", "propuls", "螺旋桨", "自航", "推力", "敞水", "伴流")),
    ("阻力", ("阻力", "resistance", "holtrop", "摩擦", "形阻", "兴波")),
    ("结构", ("结构", "structur", "强度", "屈曲", "疲劳", "应力")),
    ("求解器", ("lbm", "fem", "fvm", "bem", "dem", "sph", "dg", "cad", "求解器", "数值方法",
               "解析解", "收敛阶", "守恒")),
    ("平台治理", ("平台", "harness", "campaign", "审计", "复现", "台账", "治理", "派发",
                "mission", "kpi", "能力")),
]

REPO_ALIAS = {"ship-platform": "ship-performance-platform"}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: str) -> Optional[str]:
    if not os.path.exists(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def classify_discipline(text: str) -> str:
    low = text.lower()
    for name, keys in DISCIPLINE_RULES:
        if any(k in low for k in keys):
            return name
    return "其他"


def pg_query(sql: str) -> List[List[str]]:
    """psql 通道（与 tools/guide_kpi_ledger.py 同一路线）。密码只从环境/凭据文件取，不进 argv。"""
    env = dict(os.environ, PATH="/usr/bin:/bin")
    pw = os.environ.get("TP_PG_PASSWORD")
    if not pw:
        cred = os.environ.get("TP_PG_CREDS", "/root/.pg_rot_20261101.json")
        try:
            with open(cred, "r", encoding="utf-8") as f:
                d = json.load(f)
            pw = d.get("new") or d.get("password")
            env.setdefault("TP_PG_USER_RESOLVED", d.get("user", "postgres"))
        except Exception:
            pw = ""
    env["PGPASSWORD"] = pw or ""
    user = os.environ.get("TP_PG_USER") or env.get("TP_PG_USER_RESOLVED") or "postgres"
    cp = subprocess.run(["psql", "-h", os.environ.get("TP_PG_HOST", "127.0.0.1"),
                         "-U", user, "-d", os.environ.get("TP_PG_DB", "tensorplatform"),
                         "-tAF", "\x1f", "-c", sql], capture_output=True, text=True,
                        env=env, timeout=180)
    if cp.returncode != 0:
        raise RuntimeError("psql failed: %s" % cp.stderr.strip()[:300])
    return [line.split("\x1f") for line in cp.stdout.strip().split("\n") if line.strip()]


def git(workdir: str, *args: str) -> str:
    cp = subprocess.run(["git", "-C", workdir, *args], capture_output=True, text=True,
                        timeout=120)
    return cp.stdout if cp.returncode == 0 else ""


def changed_files(workdir: str) -> List[str]:
    out = git(workdir, "diff", "--name-only", "main...HEAD")
    if not out.strip():
        out = git(workdir, "show", "--pretty=", "--name-only", "HEAD")
    return [x.strip() for x in out.strip().split("\n") if x.strip()]


def git_show_bytes(workdir: str, commit: str, path: str) -> Optional[bytes]:
    cp = subprocess.run(["git", "-C", workdir, "show", "%s:%s" % (commit, path)],
                        capture_output=True, timeout=120)
    return cp.stdout if cp.returncode == 0 else None


def count_json_numbers(payload: bytes) -> int:
    try:
        obj = json.loads(payload.decode("utf-8", "replace"))
    except Exception:
        return 0

    def walk(o, d=0):
        if d > 12:
            return 0
        if isinstance(o, bool):
            return 0
        if isinstance(o, (int, float)):
            return 1
        if isinstance(o, dict):
            return sum(walk(v, d + 1) for v in o.values())
        if isinstance(o, list):
            return sum(walk(v, d + 1) for v in o)
        return 0

    return walk(obj)


GEN_HINT = re.compile(r"json\.dump|json\.dumps")


def describe_node(mission_id: int, name: str, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    workdir = os.path.join(WORK_ROOT, "m%d" % mission_id)
    if not os.path.isdir(os.path.join(workdir, ".git")) and not os.path.exists(
            os.path.join(workdir, ".git")):
        return None
    commit = git(workdir, "rev-parse", "HEAD").strip()
    if not commit:
        return None
    branch = git(workdir, "rev-parse", "--abbrev-ref", "HEAD").strip()
    date = git(workdir, "log", "-1", "--format=%cI", commit).strip()
    files = changed_files(workdir)
    tests, artifacts, generators, reports = [], [], [], []
    numbers = {}
    for path in files:
        if path.startswith("artifacts/") and path.endswith(".json"):
            blob = git_show_bytes(workdir, commit, path)
            if blob is None:
                continue
            n = count_json_numbers(blob)
            if n >= MIN_NUMBERS:
                artifacts.append(path)
                numbers[path] = n
        elif re.search(r"(^|/)tests?/.*\.py$", path) or re.search(r"(^|/)test_[^/]*\.py$", path):
            tests.append(path)
        elif path.endswith(".py") and (path.startswith(("scripts/", "tools/", "benchmarks/",
                                                       "artifacts/")) or "/" not in path):
            blob = git_show_bytes(workdir, commit, path)
            if blob and GEN_HINT.search(blob.decode("utf-8", "replace")):
                generators.append(path)
        elif path.endswith(".md") and path.startswith("docs/"):
            reports.append(path)
    if not artifacts and not tests:
        return None
    repo = REPO_ALIAS.get(str(meta.get("repo") or ""), str(meta.get("repo") or ""))
    return {
        "mission_id": mission_id,
        "name": name,
        "ext_id": meta.get("ext_id"),
        "repo": repo,
        "discipline": classify_discipline("%s %s" % (name, str(meta.get("goal", ""))[:400])),
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "branch": branch,
        "commit": commit,
        "commit_date": date,
        "workdir": workdir,
        "files": {"tests": tests, "json_artifacts": artifacts, "generators": generators,
                  "reports": reports},
        "json_numbers": numbers,
        "eligible": bool(artifacts) and bool(tests),
        "has_generator": bool(generators),
    }


def anchor(node: Dict[str, Any]) -> Dict[str, Any]:
    """锚定交付工件：提交版本 sha256 + 工作树版本 sha256（暴露未提交改动）。"""
    anchors = []
    for kind in ("reports", "json_artifacts", "generators", "tests"):
        for path in node["files"][kind]:
            blob = git_show_bytes(node["workdir"], node["commit"], path)
            if blob is None:
                continue
            anchors.append({
                "kind": kind,
                "path": path,
                "sha256_commit": sha256_bytes(blob),
                "sha256_workdir": sha256_file(os.path.join(node["workdir"], path)),
                "bytes": len(blob),
            })
    node["anchors"] = anchors
    node["anchors_ok"] = bool(anchors) and all(
        a["sha256_workdir"] is None or a["sha256_workdir"] == a["sha256_commit"] for a in anchors)
    return node


def _stratum_key(node: Dict[str, Any], seed: int) -> str:
    return hashlib.sha256(("%d:%d" % (seed, node["mission_id"])).encode()).hexdigest()


def _pick_within(stratum_nodes: List[Dict[str, Any]], quota: int, seed: int) -> List[Dict[str, Any]]:
    """层内轮转：按 (学科 × 年份) 分组，每轮每组取 1 个，组内按稳定散列排序（可复现）。"""
    groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for c in stratum_nodes:
        groups[(c["discipline"], c["year"])].append(c)
    for g in groups.values():
        g.sort(key=lambda x: _stratum_key(x, seed))
    keys = sorted(groups, key=lambda k: (str(k[0]), str(k[1])))
    out: List[Dict[str, Any]] = []
    while len(out) < quota:
        progressed = False
        for k in keys:
            if len(out) >= quota:
                break
            if groups[k]:
                out.append(groups[k].pop(0))
                progressed = True
        if not progressed:
            break
    return out


def stratified_sample(cands: List[Dict[str, Any]], n: int, per_repo_cap: int,
                      seed: int = 70) -> List[Dict[str, Any]]:
    """分层抽样 = repo 配额（按候选量比例，下限 2，上限 per_repo_cap）× 层内 (学科 × 年份) 轮转。

    这样保证小仓不被字母序饿死（首版实现的 bug：sorted 分层键轮转让 `ship-*` 永远轮不到），
    同时样本仍按候选量比例覆盖各仓。
    """
    by_repo: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in cands:
        by_repo[c["repo"]].append(c)
    total = sum(len(v) for v in by_repo.values())
    n = min(n, total)
    quotas = {r: max(2, int(round(n * len(v) / total))) for r, v in by_repo.items()}
    quotas = {r: min(q, per_repo_cap, len(by_repo[r])) for r, q in quotas.items()}
    while sum(quotas.values()) > n:                     # 超配额 → 砍最大的
        r = max(quotas, key=lambda k: quotas[k])
        quotas[r] -= 1
    while sum(quotas.values()) < n:                     # 欠配额 → 补还有余量的
        spare = [r for r in quotas if quotas[r] < min(per_repo_cap, len(by_repo[r]))]
        if not spare:
            break
        r = max(spare, key=lambda k: len(by_repo[k]) - quotas[k])
        quotas[r] += 1
    picked: List[Dict[str, Any]] = []
    for r in sorted(quotas, key=lambda k: (-len(by_repo[k]), k)):
        picked.extend(_pick_within(by_repo[r], quotas[r], seed))
    return picked[:n] if len(picked) > n else picked


def find_generators(workdir: str, commit: str, artifact_paths: List[str]) -> Dict[str, List[str]]:
    """在给定提交里反查「谁写出了这个 JSON 工件」：``git grep`` 工件基名，只留非测试 py。

    比「只看 diff 里改过的脚本」更完整：很多节点的生成脚本早前已入库，本节点只是重跑它。
    """
    mapping: Dict[str, List[str]] = {}
    for art in artifact_paths:
        base = os.path.basename(art)
        out = git(workdir, "grep", "-l", "--full-name", "-F", base, commit, "--", "*.py")
        cands = [x.strip().split(":", 2)[-1] for x in out.strip().split("\n") if x.strip()]
        cands = [c for c in cands if c.endswith(".py") and "test" not in os.path.basename(c).lower()]
        keep = []
        for c in sorted(set(cands)):
            blob = git_show_bytes(workdir, commit, c)
            if not blob:
                continue
            text = blob.decode("utf-8", "replace")
            # 必须是「真的写 JSON 的脚本」；排除起服务的 API 模块（跑起来会挂住）
            if not (("json.dump" in text) or ("json.dumps" in text)):
                continue
            if "uvicorn.run" in text or "IF __NAME__" in text.upper().replace("__name__", "__NAME__"):
                continue
            keep.append(c)
        # 同目录脚本优先（更可能是本节点的工件生成器）
        # 补一路发现：工件同目录内的 *.py（最典型形态：artifacts/<node>/make_artifacts.py）
        adir = os.path.dirname(art)
        ls = git(workdir, "ls-tree", "--name-only", commit, adir + "/")
        for c in [x.strip() for x in ls.strip().split("\n") if x.strip().endswith(".py")]:
            if c in keep or "test" in os.path.basename(c).lower():
                continue
            blob = git_show_bytes(workdir, commit, c)
            if not blob:
                continue
            text = blob.decode("utf-8", "replace")
            if ("json.dump" in text or "json.dumps" in text) and "uvicorn.run" not in text:
                keep.append(c)
        keep.sort(key=lambda c: (0 if os.path.dirname(c) == os.path.dirname(art) else 1, c))
        mapping[art] = keep[:3]
    return mapping


def build_spec(node: Dict[str, Any]) -> Dict[str, Any]:
    """生成集群侧可执行的探针 spec（数值重放 + 测试重放）。"""
    arts = node["files"]["json_artifacts"]
    mapping = find_generators(node["workdir"], node["commit"], arts)
    numeric = []
    for art, cands in mapping.items():
        for gen in cands:
            outs = [a for a in arts
                    if os.path.dirname(a) == os.path.dirname(art)
                    or os.path.dirname(gen) == os.path.dirname(a)]
            if outs:
                numeric.append({"script": gen, "outputs": sorted(set(outs))})
                break
        node.setdefault("generator_map", {})[art] = cands
    # 去重（同一脚本覆盖多个工件时合并输出集）
    merged: Dict[str, List[str]] = {}
    for probe in numeric:
        merged.setdefault(probe["script"], [])
        merged[probe["script"]].extend(probe["outputs"])
    numeric = [{"script": k, "outputs": sorted(set(v))} for k, v in merged.items()]
    return {
        "mission_id": node["mission_id"],
        "commit": node["commit"],
        "repo": node["repo"],
        "reps": 2,
        "gen_timeout": 210,
        "pytest_timeout": 240,
        "import_stubs": ["matplotlib", "fastapi", "pydantic"],
        "node_timeout": 540,
        "numeric": numeric,
        "pytest": node["files"]["tests"][:6],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=DEFAULT_N)
    ap.add_argument("--per-repo-cap", type=int, default=14)
    ap.add_argument("--limit-scan", type=int, default=0)
    ap.add_argument("--out", default="artifacts/repro_audit/sample.json")
    ap.add_argument("--include-used", action="store_true",
                    help="允许已出现在历史 verified 案例清单里的节点（默认允许，仅统计）")
    a = ap.parse_args(argv)
    rows = pg_query(
        "SELECT n.id, n.mission_id, n.name, n.metadata::text FROM campaign_nodes n "
        "WHERE n.status='done' AND n.mission_id IS NOT NULL ORDER BY n.id DESC")
    if a.limit_scan:
        rows = rows[:a.limit_scan]
    scanned, skipped, cands = 0, Counter(), []
    for node_id, mid, name, meta_s in rows:
        try:
            meta = json.loads(meta_s or "{}")
        except Exception:
            meta = {}
        info = describe_node(int(mid), name, meta)
        scanned += 1
        if info is None:
            skipped["no_workdir_or_no_artifact"] += 1
            continue
        info["node_id"] = int(node_id)
        if not info["eligible"]:
            skipped["no_json_artifact_with_tests"] += 1
            continue
        cands.append(info)
    picked = stratified_sample(cands, a.n, a.per_repo_cap)
    for node in picked:
        anchor(node)
        node["spec"] = build_spec(node)
    out = {
        "generated_by": "tools/repro_audit/scan_select.py",
        "criteria": {"min_json_numbers": MIN_NUMBERS, "eligible": "json_artifact AND test_files",
                     "n_target": a.n, "per_repo_cap": a.per_repo_cap},
        "scan": {"done_nodes_seen": scanned, "candidates": len(cands),
                 "skipped": dict(skipped)},
        "strata": {
            "repos": dict(Counter(n["repo"] for n in picked)),
            "disciplines": dict(Counter(n["discipline"] for n in picked)),
            "years": dict(Counter(n["year"] for n in picked)),
        },
        "nodes": picked,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({"sample_size": len(picked), "candidates": len(cands),
                      "repos": out["strata"]["repos"],
                      "disciplines": len(out["strata"]["disciplines"]),
                      "anchors_ok": sum(1 for n in picked if n["anchors_ok"]),
                      "with_generator": sum(1 for n in picked if n["spec"]["numeric"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())