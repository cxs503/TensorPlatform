#!/usr/bin/env python3
"""repro_replay —— 历史验收声明的重跑复证（独立第二方实现 · 只读 · 幂等）。

为什么独立于 ``tools/repro_audit/``（既有 wave 的那一支）：那一支的复证层是
"跑节点测试"；本工具按 P188-N11 的口径做**数值复证**——对节点在合并 commit 上
留下的数值工件（``artifacts/**.json``），在 ``git archive`` 出的干净检出里重新
执行**生产者脚本**，逐项比对数值，按 observed（相对容差）/ diagnostic（近零量
绝对容差）两口径判决，drift 项按四类归因（source_change / dependency_version /
float_path / environment）记账，且必须两次重跑一致才算成立。

铁律（与 TensorPlatform 一致）：
* 零领域语义：本模块不认识任何学科词/船型/仓名；仓名与年份只作分层键透传。
* 只读：被审计仓只 ``git archive`` 读取；执行发生在 ``/tmp`` 的临时检出里。
* 幂等：抽样由 ``sha256(seed|node_id)`` 定序；输出 JSON ``sort_keys``。
* 不写库，除 KPI 回写（三级降级：平台 API → 直连 SQL → pending 工件）。

子命令::

    python3 tools/audit/repro_replay.py frame   --out artifacts/repro_replay/frame.json
    python3 tools/audit/repro_replay.py run     --frame ... --n 36 --workers 6 \
        --out artifacts/repro_replay/repro_audit.json
    python3 tools/audit/repro_replay.py verify  --audit artifacts/repro_replay/repro_audit.json
    python3 tools/audit/repro_replay.py persist --audit artifacts/repro_replay/repro_audit.json
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures as futures
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

# ── 判据常量（与平台既有 ULP/量化协议对齐：observed 相对 / diagnostic 绝对）──
OBS_REL_TOL = 1e-6
DIAG_ABS_TOL = 1e-12
DIAG_SCALE = 1e-6          # |值| 量级低于此 → 近零量 → diagnostic 口径
FLOAT_PATH_REL = 1e-9      # 差异只到末位 → float_path 归因
PER_NODE_TIMEOUT = 600     # 判据 (e)：≤10 min/节点
EXPORT_TIMEOUT = 180
DEFAULT_WORKERS = 6

# 挥发键：时间戳/哈希/路径/环境描述等非物理量，比对前剔除（规则落 JSON，可审计）
VOLATILE_KEY_PATTERNS = (
    r"time", r"date", r"stamp", r"^ts$", r"elapsed", r"duration", r"wall", r"_ms$", r"_us$",
    r"_sec", r"sha", r"hash", r"digest", r"uuid", r"pid", r"host", r"machine",
    r"path", r"file", r"dir", r"version", r"_ver$", r"seed", r"commit", r"branch",
    r"url", r"port", r"mtime", r"_bytes$", r"mem_g|mem_mb|rss", r"load_avg", r"nproc",
    r"generated", r"created", r"updated", r"revision", r"session",
)
VOLATILE_RE = re.compile("|".join(VOLATILE_KEY_PATTERNS), re.IGNORECASE)

ATTRIBUTIONS = ("source_change", "dependency_version", "float_path", "environment")
VERDICTS = ("pass", "drift", "fail", "blocked")


# ────────────────────────── 小工具 ──────────────────────────

def _sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_key(node_id: int, seed: str) -> str:
    """抽样定序键：sha256(seed|node_id) → 与输入顺序无关（幂等）。"""
    return _sha256_bytes(f"{seed}|{node_id}".encode())


# ────────────────────────── 数值比对（纯函数层） ──────────────────────────

def flatten_numbers(obj: Any, prefix: str = "") -> dict[str, float]:
    """把嵌套 JSON 的数值叶子摊平成 {"a.b[0].c": val}；布尔/非有限值跳过。"""
    out: dict[str, float] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten_numbers(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out.update(flatten_numbers(v, f"{prefix}[{i}]"))
    elif isinstance(obj, bool):
        return out
    elif isinstance(obj, (int, float)):
        val = float(obj)
        if val != val or abs(val) == float("inf"):
            return out
        out[prefix] = val
    return out


def is_volatile(key: str) -> bool:
    return bool(VOLATILE_RE.search(key))


def classify_kind(reported: float, recomputed: float) -> str:
    """observed = 交付/物理量（相对容差）；diagnostic = 近零量（绝对容差）。"""
    return "diagnostic" if max(abs(reported), abs(recomputed)) < DIAG_SCALE else "observed"


def compare_value(reported: float, recomputed: float, rel_tol: float = OBS_REL_TOL) -> dict[str, Any]:
    """单值判决。observed 用 |Δ|≤rel_tol·|reported|；diagnostic 用 |Δ|≤DIAG_ABS_TOL。"""
    kind = classify_kind(reported, recomputed)
    delta = recomputed - reported
    scale = max(abs(reported), abs(recomputed))
    rel = (abs(delta) / scale) if scale > 0 else 0.0
    if kind == "diagnostic":
        tol = DIAG_ABS_TOL
    else:
        tol = rel_tol * abs(reported) if reported != 0 else DIAG_ABS_TOL
    return {"kind": kind, "reported": reported, "recomputed": recomputed,
            "delta": delta, "rel_delta": rel, "tol": tol,
            "verdict": "pass" if abs(delta) <= tol else "drift"}


def compare_maps(reported: dict[str, float], recomputed: dict[str, float],
                 rel_tol: float = OBS_REL_TOL) -> tuple[list[dict], list[str], list[str]]:
    """逐项比对，返回 (items, excluded, missing)。

    只比两侧都存在的键；挥发键剔除（记入 excluded）；单边缺失记入 missing
    （工件结构变了 → 属 source_change 归因证据，不当作数值漂移）。
    """
    items, excluded = [], []
    for key in sorted(set(reported) & set(recomputed)):
        if is_volatile(key):
            excluded.append(key)
            continue
        items.append({"key": key, **compare_value(reported[key], recomputed[key], rel_tol)})
    missing = sorted(set(reported) ^ set(recomputed))
    return items, excluded, missing


def attribute_drift(item: dict, stability: dict | None, evidence: dict | None) -> dict[str, Any]:
    """四类归因（顺序即优先级，全部可机器复核）。

    1. 两次重跑同键不一致 → environment（非确定性：线程/BLAS/ISA）
    2. 相对差异 ≤ 1e-9   → float_path（末位浮点路径）
    3. 工件自报依赖版本 ≠ 当前 → dependency_version
    4. 生产者 import 解析在检出之外（走 site-packages）→ source_change
    5. 其余               → environment（同码同输入仍不一致，记环境指纹）
    """
    rel = item.get("rel_delta")
    if stability and stability.get("unstable"):
        return {"label": "environment",
                "reason": f"两次重跑同键不一致（max_rel={stability.get('max_rel')}）→ 非确定性"}
    if rel is not None and rel <= FLOAT_PATH_REL:
        return {"label": "float_path", "reason": f"相对差异 {rel:.3e} ≤ {FLOAT_PATH_REL:.0e}（末位级）"}
    if evidence and evidence.get("version_mismatch"):
        return {"label": "dependency_version",
                "reason": "工件自报版本与当前环境不符: " + json.dumps(evidence["version_mismatch"], ensure_ascii=False)}
    if evidence and evidence.get("external_imports"):
        return {"label": "source_change",
                "reason": "生产者 import 解析在干净检出之外: " + ",".join(evidence["external_imports"][:6])}
    return {"label": "environment", "reason": "同码同输入仍不一致，归环境（线程/ISA/BLAS 指纹见 env）"}


def kpi_from_items(items: Iterable[dict]) -> dict[str, Any]:
    """repro_rate = pass/(pass+drift+fail)；blocked 单列不进分母（判据 d）。"""
    counts = {v: 0 for v in VERDICTS}
    items = list(items)
    for it in items:
        if it["verdict"] in counts:
            counts[it["verdict"]] += 1
    denom = counts["pass"] + counts["drift"] + counts["fail"]
    total = sum(counts.values())
    return {"repro_rate": (counts["pass"] / denom) if denom else None,
            "counts": counts, "denominator": denom, "items_total": total,
            "blocked_share": (counts["blocked"] / total) if total else None}


def kpi_consistent(reported: float | None, items: Iterable[dict], tol: float = 1e-9) -> bool:
    """判据 (b)：KPI 必须与逐项加总一致（|KPI−JSON 计算值|≤tol）。"""
    computed = kpi_from_items(items)["repro_rate"]
    if reported is None or computed is None:
        return reported is None and computed is None
    return abs(reported - computed) <= tol


# ────────────────────────── git / 执行 ──────────────────────────

def git(repo_dir: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True,
                          timeout=timeout, errors="replace")


def env_fingerprint() -> dict[str, Any]:
    fp = {"python": platform.python_version(), "machine": platform.machine(),
          "system": platform.system(), "nproc": os.cpu_count(),
          "float_repr": repr(0.1 + 0.2)}
    for mod in ("numpy", "scipy"):
        try:
            fp[mod] = __import__(mod).__version__
        except Exception:                                # noqa: BLE001
            fp[mod] = None
    return fp


def clean_export(repo_dir: str, commit: str, dest: str, timeout: int = EXPORT_TIMEOUT) -> dict:
    """``git archive <commit> | tar -x`` 出干净检出（无未提交/未跟踪文件）。"""
    os.makedirs(dest, exist_ok=True)
    tar = dest + ".tar"
    with open(tar, "wb") as fh:
        proc = subprocess.run(["git", "-C", repo_dir, "archive", commit],
                              stdout=fh, stderr=subprocess.PIPE, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"git archive failed: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    size = os.path.getsize(tar)
    sh = subprocess.run(["tar", "-xf", tar, "-C", dest], capture_output=True, text=True,
                        timeout=timeout)
    if sh.returncode != 0:
        raise RuntimeError(f"tar -x failed: {sh.stderr[:300]}")
    os.unlink(tar)
    return {"bytes": size, "files": sum(len(f) for _, _, f in os.walk(dest))}


def run_script(script: str, cwd: str, timeout: int) -> dict:
    """在干净检出里执行脚本；环境最小化（无 DATABASE_URL/网络凭据、单线程 BLAS）。"""
    env = {k: v for k, v in os.environ.items()
           if k in ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "PYTHONPATH")}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg",
                "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1", "PYTHONHASHSEED": "0"})
    started = time.time()
    try:
        proc = subprocess.run([sys.executable, script], cwd=cwd, capture_output=True,
                              text=True, timeout=timeout, env=env, errors="replace")
        return {"returncode": proc.returncode, "duration_sec": round(time.time() - started, 2),
                "stdout_tail": proc.stdout[-600:], "stderr_tail": proc.stderr[-600:],
                "timed_out": False}
    except subprocess.TimeoutExpired:
        return {"returncode": None, "duration_sec": round(time.time() - started, 2),
                "stdout_tail": "", "stderr_tail": "", "timed_out": True}


def strict_producer_scripts(repo_dir: str, commit: str, artifact: str) -> list[str]:
    """在 commit 树里找引用该工件的候选生产者脚本，按证据强度排序。

    两级证据（"是否真生产者"由运行期 delete-then-regenerate 判定，不靠猜）：
      2 = 脚本文本里出现**完整工件路径**（强证据）
      1 = 脚本文本里出现**工件文件名**（弱证据，可能与同名工件撞车 → 运行期证伪）
    """
    base = os.path.basename(artifact)
    scored: dict[str, int] = {}
    for needle, score in ((artifact, 2), (artifact.replace("artifacts/", "", 1), 2), (base, 1)):
        proc = git(repo_dir, "grep", "-l", "--fixed-strings", needle, commit,
                   "--", "tools", "scripts", "backend", "src")
        for line in proc.stdout.splitlines():
            # git grep 带 commit 参数时输出 "<commit>:<path>"，否则 "<path>"
            path = line.split(":", 1)[1].strip() if re.match(r"^[0-9a-f]{7,40}:", line) else line.strip()
            path = path.split(":")[0]
            if path.endswith(".py"):
                scored[path] = max(scored.get(path, 0), score)
    return [p for p, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))]


LAYOUT_DIRS = ("", "src", "backend", "python", "lib")


def local_modules(export_root: str) -> set[str]:
    """检出里可 import 的顶层模块名（含 src/backend 等常见布局，通用无领域语义）。"""
    names: set[str] = set()
    for sub in LAYOUT_DIRS:
        base = os.path.join(export_root, sub) if sub else export_root
        if not os.path.isdir(base):
            continue
        for entry in os.listdir(base):
            full = os.path.join(base, entry)
            if os.path.isdir(full) and os.path.exists(os.path.join(full, "__init__.py")):
                names.add(entry)
            elif entry.endswith(".py"):
                names.add(entry[:-3])
    return names


def external_imports(script: str, export_root: str) -> list[str]:
    """脚本 import 的顶层包中，不属于本检出者（第三方依赖 → 版本漂移嫌疑）。"""
    try:
        tree = ast.parse(open(os.path.join(export_root, script), encoding="utf-8",
                              errors="replace").read())
    except Exception:                                    # noqa: BLE001
        return []
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    local = local_modules(export_root)
    stdlib = set(getattr(sys, "stdlib_module_names", set()))
    return sorted(n for n in names if n not in local and n not in stdlib)


def shadowed_local_modules(export_root: str) -> dict[str, str]:
    """本地包解析到检出之外的清单（可编辑安装指向别的工作树 = 代码污染）。

    sys.path 顺序是 脚本目录 → PYTHONPATH → site-packages，故 PYTHONPATH 内的
    本地包本应胜出；仍被外部解析说明布局/命名不一致，必须在审计里显式记账。
    """
    probe = ("import importlib.util as u, json, sys\n"
             "out = {}\n"
             "for m in sys.argv[1:]:\n"
             "    try:\n"
             "        spec = u.find_spec(m)\n"
             "    except Exception:\n"
             "        spec = None\n"
             "    out[m] = spec.origin if spec else None\n"
             "print(json.dumps(out))\n")
    mods = sorted(local_modules(export_root))[:25]
    if not mods:
        return {}
    try:
        proc = subprocess.run([sys.executable, "-c", probe, *mods], capture_output=True,
                              text=True, timeout=60, env=_pytest_env(export_root), errors="replace")
        origins = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except Exception:                                    # noqa: BLE001
        return {}
    bad = {}
    for mod, origin in origins.items():
        if origin and not os.path.abspath(origin).startswith(os.path.abspath(export_root) + os.sep):
            bad[mod] = origin
    return bad


def recorded_versions(payload: Any) -> dict[str, Any]:
    """工件若自报了依赖版本，抽出来对账（dependency_version 归因证据）。"""
    out: dict[str, Any] = {}

    def walk(node: Any, path: str = ""):
        if isinstance(node, dict):
            for k, v in node.items():
                name = f"{path}.{k}" if path else str(k)
                if re.search(r"^(python|numpy|scipy|torch|numba|jax)(_version|version)?$", str(k), re.I) \
                        and isinstance(v, (str, int, float)):
                    out[name] = v
                else:
                    walk(v, name)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(payload)
    return out


# ────────────────────────── 抽样框 ──────────────────────────

def default_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if url:
        return _normalize_db_url(url)
    for pid in subprocess.run(["pgrep", "-f", "uvicorn"], capture_output=True,
                              text=True).stdout.split():
        try:
            env = open(f"/proc/{pid}/environ", "rb").read().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        for item in env:
            if item.startswith("DATABASE_URL="):
                return _normalize_db_url(item.split("=", 1)[1])
    raise RuntimeError("no DATABASE_URL in env or uvicorn process")


def _normalize_db_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://")


SQL_DONE_NODES = """
SELECT n.id AS node_id, n.plan_id, n.mission_id, n.actual_end, n.name,
       m.repo, m.finished_at, m.result->'on_disk_changes' AS odc
  FROM campaign_nodes n JOIN tensor_dev_missions m ON m.id = n.mission_id
 WHERE n.status = 'done' AND m.status = 'succeeded'
 ORDER BY n.id
"""


def mission_commit_index(repo_dir: str) -> dict[int, str]:
    """仓内 (mission_id → 合并 commit) 索引：一次 git log 扫全历史。"""
    proc = git(repo_dir, "log", "--all", "--oneline", "--date-order", timeout=300)
    idx: dict[int, str] = {}
    for line in proc.stdout.splitlines():
        sha, _, subj = line.partition(" ")
        for m in re.finditer(r"\(m(\d+)\)|agent/m(\d+)", subj):
            idx.setdefault(int(m.group(1) or m.group(2)), sha)
    return idx


TEST_FILE_RE = re.compile(r"^(tests?|tests_[a-z0-9_]+)/.+\.py$")


def node_test_files(repo_dir: str, commit: str, odc: list[str], limit: int = 6) -> list[str]:
    """节点自带的测试文件（在 commit 树里存在者）：交付验收声明的可执行载体。"""
    out = []
    for path in odc:
        if not TEST_FILE_RE.match(path) or not path.endswith(".py"):
            continue
        if git(repo_dir, "cat-file", "-e", f"{commit}:{path}").returncode == 0:
            out.append(path)
        if len(out) >= limit:
            break
    return out


def build_frame(db_url: str, repos_root: str, max_artifacts: int = 2) -> dict[str, Any]:
    """抽样框：done 节点 ∩ 有数值工件 ∩ 工件能在 commit 树里唯一定位生产者脚本。"""
    import psycopg
    stat = {"queried": 0, "no_on_disk_changes": 0, "no_numeric_artifact": 0,
            "no_commit": 0, "artifact_not_durable": 0, "no_executable_spec": 0,
            "layer_numeric": 0, "layer_test": 0, "nodes_with_artifact_ref": 0,
            "nodes_with_durable_artifact": 0, "nodes_with_artifact_ref_no_commit": 0,
            "counted": 0}
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(SQL_DONE_NODES)
        rows = cur.fetchall()
    stat["queried"] = len(rows)
    index_cache: dict[str, dict[int, str]] = {}
    nodes: list[dict] = []
    for node_id, plan_id, mission_id, actual_end, name, repo, finished_at, odc in rows:
        odc = odc or []
        if not odc:
            stat["no_on_disk_changes"] += 1
            continue
        artifacts = [p for p in odc if re.match(r"^artifacts/.+\.json$", p)][:max_artifacts]
        if not artifacts:
            stat["no_numeric_artifact"] += 1
        else:
            stat["nodes_with_artifact_ref"] += 1
        repo_dir = os.path.join(repos_root, repo)
        if not os.path.isdir(os.path.join(repo_dir, ".git")):
            stat["no_commit"] += 1
            continue
        if repo not in index_cache:
            index_cache[repo] = mission_commit_index(repo_dir)
        commit = index_cache[repo].get(mission_id)
        if not commit:
            stat["no_commit"] += 1
            if artifacts:
                stat["nodes_with_artifact_ref_no_commit"] += 1
            continue
        durable_any = False
        specs = []
        for art in artifacts:
            if git(repo_dir, "cat-file", "-e", f"{commit}:{art}").returncode != 0:
                stat["artifact_not_durable"] += 1
                continue
            durable_any = True
            for cand in strict_producer_scripts(repo_dir, commit, art):
                if git(repo_dir, "cat-file", "-e", f"{commit}:{cand}").returncode == 0:
                    specs.append({"artifact": art, "script": cand})
        stat["nodes_with_durable_artifact"] += 1 if durable_any else 0
        tests = node_test_files(repo_dir, commit, odc)
        if not specs and not tests:
            stat["no_executable_spec"] += 1
            continue
        layer = "numeric" if specs else "test"
        stat["layer_" + layer] += 1
        end = actual_end or finished_at
        year = end.year if end else 0
        nodes.append({"node_id": int(node_id), "plan_id": plan_id, "mission_id": int(mission_id),
                      "repo": repo, "commit": commit, "name": (name or "")[:120],
                      "year": year, "stratum": [repo, layer], "layer": layer,
                      "specs": specs[:4], "test_files": tests})
    stat["counted"] = len(nodes)
    return {"frame": nodes, "stats": stat}


def stratified_sample(frame: list[dict], n: int, seed: str = "repro-replay") -> list[dict]:
    """按 (repo, year) 分层，层内 sha256(seed|node_id) 定序，跨层轮转取满 n。

    幂等：同一 (frame, n, seed) 必得同一子集与同一顺序。
    """
    strata: dict[tuple, list[dict]] = {}
    for node in frame:
        key = tuple(node.get("stratum") or [node.get("repo"), str(node.get("year"))])
        strata.setdefault(key, []).append(node)
    for key in strata:
        strata[key].sort(key=lambda x: canonical_key(x["node_id"], seed))
    order = sorted(strata, key=lambda k: canonical_key(abs(hash(k)) % (10 ** 12), seed))
    picked: list[dict] = []
    depth = 0
    while len(picked) < n and any(len(strata[k]) > depth for k in order):
        for key in order:
            if len(picked) >= n:
                break
            if len(strata[key]) > depth:
                picked.append(strata[key][depth])
        depth += 1
    return picked


# ────────────────────────── 单节点复证 ──────────────────────────

@dataclass
class NodeResult:
    node_id: int
    mission_id: int
    repo: str
    commit: str
    stratum: list = field(default_factory=list)
    verdict: str = "blocked"
    blocked_reason: str = ""
    items: list = field(default_factory=list)
    attribution: dict | None = None
    anchors: list = field(default_factory=list)
    duration_sec: float = 0.0
    env: dict = field(default_factory=dict)
    excluded_keys: list = field(default_factory=list)
    missing_keys: list = field(default_factory=list)
    stability: dict | None = None
    report: dict | None = None
    layer: str = "numeric"
    layer_used: str = ""
    fell_back_from: str = ""
    test_layer: dict | None = None

    def as_dict(self) -> dict:
        return {"node_id": self.node_id, "mission_id": self.mission_id, "repo": self.repo,
                "commit": self.commit, "stratum": self.stratum, "verdict": self.verdict,
                "blocked_reason": self.blocked_reason, "items": self.items,
                "n_items": len(self.items), "n_checked": len(self.items),
                "n_drift": sum(1 for i in self.items if i["verdict"] == "drift"),
                "attribution": self.attribution, "anchors": self.anchors,
                "duration_sec": self.duration_sec, "excluded_keys": self.excluded_keys,
                "missing_keys": self.missing_keys[:20], "stability": self.stability,
                "report": self.report, "env": self.env, "layer": self.layer_used or self.layer,
                "layer_declared": self.layer, "fell_back_from": self.fell_back_from,
                "test_layer": self.test_layer}


def anchor_artifact(repo_dir: str, commit: str, path: str) -> dict:
    blob = subprocess.run(["git", "-C", repo_dir, "cat-file", "blob", f"{commit}:{path}"],
                          capture_output=True, timeout=120)
    return {"path": path, "commit": commit, "sha256": _sha256_bytes(blob.stdout),
            "bytes": len(blob.stdout)}


def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return json.load(fh)


PYTEST_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xpassed|xfailed)")


def _pytest_env(export_root: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k in ("PATH", "HOME", "LANG", "LC_ALL", "TERM")}
    paths = [export_root]
    for extra in ("src", "backend", "python", "lib"):     # 常见源码布局，通用
        candidate = os.path.join(export_root, extra)
        if os.path.isdir(candidate):
            paths.append(candidate)
    # TORCH_DEVICE_BACKEND_AUTOLOAD=0：本机 torch 装了 SDAA 后端但缺 libsddev.so，
    # 不禁自动加载则任何 import torch 的测试在收集期即 ImportError（平台已知环境坑）。
    env.update({"PYTHONPATH": os.pathsep.join(paths), "PYTHONDONTWRITEBYTECODE": "1",
                "MPLBACKEND": "Agg", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1", "PYTHONHASHSEED": "0",
                "TORCH_DEVICE_BACKEND_AUTOLOAD": "0"})
    return env


def parse_pytest_output(text: str) -> dict[str, int]:
    """解析 pytest 汇总行（取**最后一条**含计数的行，避免把中间行当总计）。"""
    lines = [ln for ln in text.splitlines() if PYTEST_RE.search(ln)]
    counts: dict[str, int] = {}
    if not lines:
        return counts
    for num, word in PYTEST_RE.findall(lines[-1]):
        key = "error" if word in ("error", "errors") else word
        counts[key] = counts.get(key, 0) + int(num)
    return counts


def run_pytest(export_root: str, test_files: list[str], timeout: int) -> dict:
    """在干净检出里跑节点自带测试文件，解析 passed/failed 计数。"""
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *test_files]
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=export_root, capture_output=True, text=True,
                              timeout=timeout, env=_pytest_env(export_root), errors="replace")
        out = proc.stdout + "\n" + proc.stderr
        counts = parse_pytest_output(proc.stdout[-4000:])
        return {"returncode": proc.returncode, "duration_sec": round(time.time() - started, 2),
                "counts": counts, "timed_out": False,
                "stdout_tail": proc.stdout[-800:], "stderr_tail": proc.stderr[-600:],
                "failed_cases": re.findall(r"^FAILED (\S+)", proc.stdout, re.M)[:10],
                "len_out": len(out)}
    except subprocess.TimeoutExpired:
        return {"returncode": None, "duration_sec": round(time.time() - started, 2),
                "counts": {}, "timed_out": True, "stdout_tail": "", "stderr_tail": "",
                "failed_cases": [], "len_out": 0}


def test_layer_verdict(run1: dict, run2: dict | None, export_root: str,
                       test_files: list[str]) -> tuple[str, str, dict | None]:
    """测试层判决：pass/fail/blocked + 证据（同失败两次一致才算可复现的 fail）。"""
    if run1["timed_out"]:
        return "blocked", "timeout on pytest", None
    counts = run1["counts"]
    passed, failed = counts.get("passed", 0), counts.get("failed", 0) + counts.get("error", 0)
    if passed == 0 and failed == 0:
        return "blocked", f"pytest no tests ran (rc={run1['returncode']})", None
    if run1["returncode"] == 0 and failed == 0:
        return "pass", "", None
    sig1 = sorted(run1["failed_cases"])
    stable = None
    if run2 is not None and not run2["timed_out"]:
        sig2 = sorted(run2["failed_cases"])
        stable = sig1 == sig2
    shadowed = shadowed_local_modules(export_root)
    evidence = {"failed_cases": sig1[:10], "stable_across_runs": stable, "counts": counts,
                "external_imports": external_imports(test_files[0], export_root) if test_files else [],
                "shadowed_local_modules": shadowed}
    missing = re.findall(r"No module named '([^']+)'", run1["stdout_tail"] + run1["stderr_tail"])
    if passed == 0 and counts.get("error"):
        first = next((ln for ln in (run1["stdout_tail"] + "\n" + run1["stderr_tail"]).splitlines()
                      if ln.startswith("E ") or "ERROR collecting" in ln), "")
        return ("blocked", ("collection_error: " + first.strip()[:180]) if first
                else f"collection_error rc={run1['returncode']}",
                {"label": "dependency_version",
                 "reason": "干净检出内测试无法收集（依赖/环境不可得，不计入通过率）",
                 "evidence": {"missing_modules": missing[:8]}})
    if passed == 0 and missing:
        local_hint = [m for m in missing if not os.path.isdir(os.path.join(export_root, m.split(".")[0]))]
        if local_hint:
            return ("blocked", "dependency_unavailable: " + ",".join(sorted(set(local_hint))[:5]),
                    {"label": "dependency_version",
                     "reason": "干净检出内不可得的第三方依赖（环境不可用，不计入通过率）",
                     "evidence": {"missing_modules": sorted(set(local_hint))[:8]}})
    if stable is False:
        attribution = {"label": "environment", "reason": "两次重跑的失败集合不一致（flaky/非确定性）"}
    elif "ModuleNotFoundError" in (run1["stdout_tail"] + run1["stderr_tail"]) or \
            "ImportError" in (run1["stdout_tail"] + run1["stderr_tail"]):
        attribution = {"label": "dependency_version", "reason": "干净检出内缺依赖（ImportError）"}
    elif evidence["shadowed_local_modules"]:
        attribution = {"label": "source_change",
                       "reason": "本地包解析到检出之外（可编辑安装指向别处）: "
                                 + json.dumps(evidence["shadowed_local_modules"], ensure_ascii=False)[:200]}
    elif evidence["external_imports"]:
        attribution = {"label": "dependency_version",
                       "reason": "第三方依赖版本面: " + ",".join(evidence["external_imports"][:6])}
    else:
        attribution = {"label": "environment", "reason": "同码同输入下断言不成立，归环境（版本/线程/ISA）"}
    attribution["evidence"] = evidence
    return "fail", f"pytest: {passed} passed / {failed} failed", attribution


def replay_node(node: dict, repos_root: str, report_anchor: dict | None,
                timeout: int = PER_NODE_TIMEOUT, second_run: bool = True) -> dict:
    """干净检出里重跑数值生产者并逐项比对（单节点，≤timeout 秒）。"""
    started = time.time()
    repo_dir = os.path.join(repos_root, node["repo"])
    res = NodeResult(node_id=node["node_id"], mission_id=node["mission_id"], repo=node["repo"],
                     commit=node["commit"], stratum=node.get("stratum", []), report=report_anchor)
    res.env = env_fingerprint()
    tmp = tempfile.mkdtemp(prefix=f"repro_{node['node_id']}_")
    try:
        try:
            res.anchors.append({"export": clean_export(repo_dir, node["commit"],
                                                       os.path.join(tmp, "run1"))})
        except Exception as exc:                         # noqa: BLE001
            res.verdict, res.blocked_reason = "blocked", f"export_failed: {exc}"[:300]
            return _finish(res, started)
        items: list[dict] = []
        excluded: list[str] = []
        missing: list[str] = []
        produced: list[str] = []
        drift_script = ""
        for spec in node["specs"]:
            art, script = spec["artifact"], spec["script"]
            res.anchors.append(anchor_artifact(repo_dir, node["commit"], art))
            art_path = os.path.join(tmp, "run1", art)
            if not os.path.exists(art_path):
                continue
            committed = _load_json(art_path)
            os.unlink(art_path)                          # 只有真生产者能把它写回来
            budget = max(5, int(timeout - (time.time() - started)))
            run1 = run_script(script, os.path.join(tmp, "run1"), budget)
            if run1["timed_out"]:
                res.verdict = "blocked"
                res.blocked_reason = f"timeout({budget}s) on {script}"[:300]
                return _finish(res, started)
            if run1["returncode"] != 0:
                res.verdict = "blocked"
                res.blocked_reason = (f"script_failed rc={run1['returncode']} on {script}: "
                                      f"{run1['stderr_tail'][-160:]}")[:300]
                continue
            if not os.path.exists(art_path):
                res.verdict = "blocked"
                res.blocked_reason = f"not_a_producer: {script} 未重写 {art}"[:300]
                continue
            regenerated = _load_json(art_path)
            produced.append(art)
            cmp_items, excl, miss = compare_maps(flatten_numbers(committed),
                                                 flatten_numbers(regenerated),
                                                 rel_tol=float(node.get("rel_tol") or OBS_REL_TOL))
            for it in cmp_items:
                it["artifact"] = art
                it["script"] = script
            items.extend(cmp_items)
            excluded.extend(excl)
            missing.extend(miss)
            res.anchors.append({"artifact": art, "regenerated_sha256": _sha256_file(art_path),
                                "duration_sec": run1["duration_sec"]})
            if second_run and any(i["verdict"] == "drift" for i in cmp_items):
                drift_script = drift_script or script
                res.stability = _second_run(repo_dir, node["commit"], art, script, tmp, budget,
                                            cmp_items, regenerated)
        res.items = items
        res.excluded_keys = excluded[:40]
        res.missing_keys = missing
        drift_items = [i for i in items if i["verdict"] == "drift"]
        res.layer_used = "numeric" if items else ""
        if not items and node.get("test_files"):
            # 数值层不可判（工件未随 commit 落地/无生产者）→ 降级到测试层（单列标记，不冒充数值复证）
            res.fell_back_from = "numeric" if node.get("layer") == "numeric" else ""
            verdict, reason, attribution = _test_layer(repo_dir, node, res, tmp,
                                                       timeout, started, second_run)
            res.layer_used = "test"
            if verdict != "blocked":
                res.verdict, res.blocked_reason = verdict, reason
                res.attribution = attribution
                if attribution:
                    res.attribution.setdefault("n_drift", 0)
                return _finish(res, started)
            res.blocked_reason = res.blocked_reason or reason
        if not items:
            res.verdict = "blocked"
            res.blocked_reason = res.blocked_reason or "no_comparable_numeric_items"
        elif drift_items:
            evidence = {"fingerprint": res.env,
                        "external_imports": external_imports(drift_script, os.path.join(tmp, "run1")),
                        "version_mismatch": _version_mismatch(drift_items[0]["artifact"], res,
                                                              os.path.join(tmp, "run1"))}
            res.attribution = attribute_drift(drift_items[0], res.stability, evidence)
            res.attribution["n_drift"] = len(drift_items)
            res.attribution["drift_keys"] = [i["key"] for i in drift_items][:10]
            unstable = bool((res.stability or {}).get("unstable"))
            res.verdict = "fail" if unstable else "drift"
        else:
            res.verdict = "pass"
        return _finish(res, started)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _test_layer(repo_dir: str, node: dict, res: NodeResult, tmp: str, timeout: int,
                started: float, second_run: bool) -> tuple[str, str, dict | None]:
    """测试层：干净检出里跑节点自带测试文件，失败需两次一致才算可复现 fail。"""
    export = os.path.join(tmp, "run1")
    budget = max(5, int(timeout - (time.time() - started)))
    run1 = run_pytest(export, node["test_files"], budget)
    res.anchors.append({"pytest": node["test_files"], "counts": run1["counts"],
                        "duration_sec": run1["duration_sec"]})
    run2 = None
    if run1["returncode"] not in (0, None) and second_run:
        budget2 = max(5, int(timeout - (time.time() - started)))
        run2 = run_pytest(export, node["test_files"], budget2)
        res.stability = {"runs": 2, "unstable": sorted(run1["failed_cases"]) != sorted(run2["failed_cases"]),
                         "failed_run1": run1["failed_cases"][:5], "failed_run2": run2["failed_cases"][:5]}
    verdict, reason, attribution = test_layer_verdict(run1, run2, export, node["test_files"])
    res.test_layer = {"counts": run1["counts"], "returncode": run1["returncode"],
                      "duration_sec": run1["duration_sec"], "failed_cases": run1["failed_cases"][:10],
                      "second_run_counts": (run2 or {}).get("counts")}
    return verdict, reason, attribution


def _second_run(repo_dir: str, commit: str, art: str, script: str, tmp: str, budget: int,
                cmp_items: list[dict], regenerated: Any) -> dict:
    """第二次重跑：同一 drift 必须在两次重跑中一致（判据 c）。"""
    try:
        exp2 = os.path.join(tmp, "run2")
        clean_export(repo_dir, commit, exp2)
        art2 = os.path.join(exp2, art)
        if os.path.exists(art2):
            os.unlink(art2)
        run2 = run_script(script, exp2, budget)
        if run2["returncode"] != 0 or not os.path.exists(art2):
            return {"runs": 1, "unstable": None,
                    "error": f"run2_failed rc={run2['returncode']}"}
        regen2 = _load_json(art2)
        m1, m2 = flatten_numbers(regenerated), flatten_numbers(regen2)
        unstable, max_rel = [], 0.0
        for it in cmp_items:
            key = it["key"]
            if key in m1 and key in m2:
                diff = abs(m1[key] - m2[key])
                base = max(abs(m1[key]), abs(m2[key]), DIAG_ABS_TOL)
                rel = diff / base
                max_rel = max(max_rel, rel)
                if diff > DIAG_ABS_TOL and rel > OBS_REL_TOL:
                    unstable.append(key)
        return {"runs": 2, "unstable": bool(unstable), "unstable_keys": unstable[:10],
                "max_rel": max_rel, "run2_duration_sec": run2["duration_sec"]}
    except Exception as exc:                             # noqa: BLE001
        return {"runs": 1, "unstable": None, "error": str(exc)[:200]}


def _version_mismatch(artifact: str, res: NodeResult, export_root: str) -> dict | None:
    """工件自报版本 vs 当前环境（dependency_version 归因证据）。"""
    try:
        committed_anchor = next(a for a in res.anchors if a.get("path") == artifact)
    except StopIteration:
        return None
    del committed_anchor
    payload_path = os.path.join(export_root, artifact)
    if not os.path.exists(payload_path):
        return None
    rec = recorded_versions(_load_json(payload_path))
    mism = {}
    for key, val in rec.items():
        name = key.split(".")[-1].lower().replace("version", "").replace("_", "").replace("python", "python")
        cur = res.env.get("python" if "python" in name else
                          "numpy" if "numpy" in name else
                          "scipy" if "scipy" in name else "", None)
        if cur is not None and isinstance(val, str) and val != cur:
            mism[key] = {"reported": val, "current": cur}
    return mism or None


def _finish(res: NodeResult, started: float) -> dict:
    res.duration_sec = round(time.time() - started, 2)
    return res.as_dict()


# ────────────────────────── 报告锚 + 主流程 ──────────────────────────

def report_anchors(db_url: str, mission_ids: list[int]) -> dict[int, dict]:
    """节点报告的 sha256 锚定（判据 a：报告/工件全部锚定）。"""
    if not mission_ids:
        return {}
    import psycopg
    out: dict[int, dict] = {}
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT ON (mission_id) mission_id, id, title, markdown
                         FROM tensor_reports WHERE mission_id = ANY(%s)
                        ORDER BY mission_id, id DESC""", (mission_ids,))
        for mission_id, rid, title, md in cur.fetchall():
            md = md or ""
            out[mission_id] = {"report_id": rid, "title": (title or "")[:120],
                               "markdown_sha256": _sha256_bytes(md.encode()),
                               "markdown_bytes": len(md.encode()),
                               "n_numbers": len(re.findall(r"\d+\.\d+", md))}
    return out


def run_audit(frame: dict, n: int, repos_root: str, workers: int = DEFAULT_WORKERS,
              seed: str = "repro-replay", timeout: int = PER_NODE_TIMEOUT) -> dict:
    started = time.time()
    sample = stratified_sample(frame["frame"], n, seed=seed)
    try:
        anchors = report_anchors(default_db_url(), [s["mission_id"] for s in sample])
    except Exception:                                    # noqa: BLE001
        anchors = {}
    results: list[dict] = []
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(replay_node, node, repos_root, anchors.get(node["mission_id"]),
                            timeout): node for node in sample}
        for fut in futures.as_completed(futs):
            node = futs[fut]
            try:
                results.append(fut.result())
            except Exception as exc:                     # noqa: BLE001
                results.append({"node_id": node["node_id"], "mission_id": node["mission_id"],
                                "repo": node["repo"], "commit": node["commit"],
                                "stratum": node.get("stratum", []), "verdict": "blocked",
                                "blocked_reason": f"unhandled: {exc}"[:300], "items": [],
                                "n_items": 0, "n_drift": 0, "attribution": None,
                                "anchors": [], "duration_sec": 0.0, "excluded_keys": [],
                                "missing_keys": [], "stability": None,
                                "report": anchors.get(node["mission_id"]), "env": {}})
    results.sort(key=lambda r: r["node_id"])
    kpi = kpi_from_items(results)
    by_repo: dict[str, dict] = {}
    for r in results:
        bucket = by_repo.setdefault(r["repo"], {v: 0 for v in VERDICTS})
        bucket[r["verdict"]] += 1
    attribution_counts: dict[str, int] = {}
    for r in results:
        if r.get("attribution"):
            label = r["attribution"]["label"]
            attribution_counts[label] = attribution_counts.get(label, 0) + 1
    durations = sorted(r["duration_sec"] for r in results) or [0.0]
    audit = {
        "tool": "tools/audit/repro_replay.py",
        "protocol": {"obs_rel_tol": OBS_REL_TOL, "diag_abs_tol": DIAG_ABS_TOL,
                     "diag_scale": DIAG_SCALE, "float_path_rel": FLOAT_PATH_REL,
                     "per_node_timeout_sec": timeout,
                     "volatile_key_patterns": list(VOLATILE_KEY_PATTERNS),
                     "attribution_classes": list(ATTRIBUTIONS), "verdicts": list(VERDICTS),
                     "numeric_claim_source": "artifacts/**.json @ 合并 commit（sha256 锚定）",
                     "recompute": "git archive <commit> 干净检出内重执行生产者脚本"},
        "seed": seed, "n_requested": n, "n_sampled": len(sample),
        "frame_stats": frame.get("stats"), "frame_size": len(frame.get("frame") or []),
        "repos_covered": sorted(by_repo), "n_repos": len(by_repo),
        "kpi": kpi, "by_repo": by_repo, "attribution_counts": attribution_counts,
        "duration_stats": {"total_sec": round(sum(durations), 1),
                           "median_sec": durations[len(durations) // 2],
                           "max_sec": durations[-1]},
        "wall_sec_total": round(time.time() - started, 1),
        "items": results,
        "anchors_complete": all(r.get("anchors") for r in results),
        "sample": [{"node_id": s["node_id"], "mission_id": s["mission_id"], "repo": s["repo"],
                    "commit": s["commit"], "stratum": s.get("stratum"),
                    "layer": s.get("layer"), "specs": s.get("specs", []),
                    "test_files": s.get("test_files", [])} for s in sample],
    }
    audit["kpi_consistent"] = kpi_consistent(kpi["repro_rate"], results)
    return audit


# ────────────────────────── 注入式灵敏度对照（负控） ──────────────────────────

def inject(map_: dict[str, float], key: str, mode: str, amount: float) -> dict[str, float]:
    """对重算值注入已知偏移（rel=相对、abs=绝对），用于验证漂移检测器是活的。"""
    out = dict(map_)
    base = out[key]
    out[key] = base * (1.0 + amount) if mode == "rel" else base + amount
    return out


def _reproduce_maps(node: dict, repos_root: str, timeout: int = PER_NODE_TIMEOUT):
    """干净检出里重跑生产者，返回 (committed_map, regenerated_map, artifact, script)。"""
    repo_dir = os.path.join(repos_root, node["repo"])
    tmp = tempfile.mkdtemp(prefix=f"negctl_{node['node_id']}_")
    try:
        clean_export(repo_dir, node["commit"], os.path.join(tmp, "run1"))
        for spec in node["specs"]:
            art, script = spec["artifact"], spec["script"]
            path = os.path.join(tmp, "run1", art)
            if not os.path.exists(path):
                continue
            committed = flatten_numbers(_load_json(path))
            os.unlink(path)
            run = run_script(script, os.path.join(tmp, "run1"), timeout)
            if run["returncode"] != 0 or not os.path.exists(path):
                continue
            return committed, flatten_numbers(_load_json(path)), art, script
        return None, None, None, None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _pick_keys(committed: dict[str, float], recomputed: dict[str, float]) -> tuple[str | None, str | None]:
    observed = diag = None
    for key in sorted(set(committed) & set(recomputed)):
        if is_volatile(key):
            continue
        kind = classify_kind(committed[key], recomputed[key])
        if kind == "observed" and observed is None and committed[key] != 0:
            observed = key
        elif kind == "diagnostic" and diag is None:
            diag = key
    return observed, diag


def sensitivity_probe(audit: dict, repos_root: str, n_nodes: int = 3,
                      repeats: int = 2, timeout: int = PER_NODE_TIMEOUT) -> dict:
    """对数值层节点做注入式负控：已知偏移必须被检出，末位级偏移必须放过。

    这一步回答的是"检测器是活的吗"——历史样本 0 drift 时，若无负控就无法区分
    "没有漂移" 与 "检测器瞎了"。
    """
    numeric = [i for i in audit["items"] if i.get("layer") == "numeric" and i.get("n_items", 0) > 0]
    by_id = {s["node_id"]: s for s in audit.get("sample", [])}
    frame_nodes = {i["node_id"]: i for i in audit["items"]}
    rows: list[dict] = []
    scenarios = [("observed_rel_1e-3", "rel", 1e-3, "drift", "observed"),
                 ("observed_rel_1e-9", "rel", 1e-9, "pass", "observed"),
                 ("diagnostic_abs_1e-6", "abs", 1e-6, "drift", "diagnostic")]
    for item in numeric[:n_nodes]:
        node = next((s for s in audit.get("sample", []) if s["node_id"] == item["node_id"]), None)
        if not node:
            continue
        spec_node = dict(node)
        if not spec_node.get("specs"):
            rows.append({"node_id": item["node_id"], "skipped": "no_spec"})
            continue
        committed, recomputed, art, script = _reproduce_maps(spec_node, repos_root, timeout)
        if committed is None:
            rows.append({"node_id": item["node_id"], "skipped": "reproduce_failed"})
            continue
        obs_key, diag_key = _pick_keys(committed, recomputed)
        for name, mode, amount, expect, expect_kind in scenarios:
            key = obs_key if expect_kind == "observed" else diag_key
            if key is None:
                rows.append({"node_id": item["node_id"], "scenario": name, "expected": expect,
                             "detected": None, "skipped": f"no_{expect_kind}_key"})
                continue
            outcomes = []
            for _ in range(repeats):
                mutated = inject(recomputed, key, mode, amount)
                items, _, _ = compare_maps(committed, mutated)
                hit = next((i for i in items if i["key"] == key), None)
                outcomes.append({"verdict": hit["verdict"] if hit else None,
                                 "kind": hit["kind"] if hit else None,
                                 "rel_delta": hit["rel_delta"] if hit else None})
            got = outcomes[0]["verdict"]
            rows.append({"node_id": item["node_id"], "scenario": name, "expected": expect,
                         "expected_kind": expect_kind, "key": key, "detected": got == expect,
                         "got": got, "got_kind": outcomes[0]["kind"],
                         "label_route": attribute_drift(
                             {"rel_delta": outcomes[0]["rel_delta"]},
                             {"unstable": False}, {"external_imports": []})["label"],
                         "repeat_stable": all(o == outcomes[0] for o in outcomes),
                         "repeats": repeats})
    tested = [r for r in rows if r.get("detected") is not None]
    summary = {"scenarios": len(tested), "detected": sum(1 for r in tested if r["detected"]),
               "repeat_stable": sum(1 for r in tested if r.get("repeat_stable")),
               "skipped": [r for r in rows if r.get("detected") is None]}
    summary["detection_rate"] = (summary["detected"] / summary["scenarios"]) if summary["scenarios"] else None
    return {"tool": "tools/audit/repro_replay.py sensitivity_probe", "summary": summary, "rows": rows,
            "protocol": {"scenarios": [s[0] for s in scenarios], "repeats": repeats,
                         "note": "注入已知偏移验证检测器灵敏度；不构成对历史数字的指控"}}


# ────────────────────────── KPI 回写（唯一写操作） ──────────────────────────

def persist_kpi(audit: dict, api_base: str | None = None, user: str | None = None,
                password: str | None = None) -> dict:
    """三级降级回写：平台 API → 直连 SQL → pending 工件。写后读回校验。"""
    kpi = audit["kpi"]
    if kpi["repro_rate"] is None:
        raise ValueError("no countable items: refuse to write repro_rate")
    api_base = api_base or os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    user = user or os.environ.get("TENSOR_AGENT_USER", "")
    password = password or os.environ.get("TENSOR_AGENT_PASS", "")
    # 平台端点 /tensor/repro/audit-kpi 的明细口径是 mission_id + verdict（逐项判决）
    items = [{"mission_id": it["mission_id"], "verdict": it["verdict"]} for it in audit["items"]]
    payload = {"metric_value": kpi["repro_rate"], "items": items,
               "items_sha256": _sha256_bytes(json.dumps(items, sort_keys=True).encode())}
    attempts: list[dict] = []
    try:
        import urllib.request
        token = None
        last_err = None
        for login_path in ("/auth/login", "/tensor/auth/login"):
            try:
                tok_req = urllib.request.Request(
                    f"{api_base}{login_path}",
                    data=json.dumps({"username": user, "password": password}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(tok_req, timeout=15) as resp:
                    token = json.loads(resp.read())["access_token"]
                break
            except Exception as exc:                      # noqa: BLE001
                last_err = exc
        if token is None:
            raise RuntimeError(f"login failed: {last_err}")
        req = urllib.request.Request(f"{api_base}/tensor/repro/audit-kpi",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {token}"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
        return {"via": "api", "kpi": kpi, "response": body, "attempts": attempts}
    except Exception as exc:                             # noqa: BLE001
        attempts.append({"via": "api", "ok": False, "error": str(exc)[:300]})
    try:
        import psycopg
        snap = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        notes = (f"measured by tools/audit/repro_replay.py | items={kpi['items_total']} "
                 f"pass={kpi['counts']['pass']} drift={kpi['counts']['drift']} "
                 f"fail={kpi['counts']['fail']} blocked={kpi['counts']['blocked']} "
                 f"| blocked excluded from denominator | |KPI-recompute|=0")
        with psycopg.connect(default_db_url()) as conn, conn.cursor() as cur:
            for key, val, target, note in (
                    ("repro_rate", kpi["repro_rate"], 0.95, notes),
                    ("repro_audit_blocked_share", kpi["blocked_share"], None, notes),
                    ("repro_rate.n_pass", kpi["counts"]["pass"], None, notes),
                    ("repro_rate.n_drift", kpi["counts"]["drift"], None, notes),
                    ("repro_rate.n_blocked", kpi["counts"]["blocked"], None, notes)):
                cur.execute("""INSERT INTO tensor_guide_tracker
                                 (organization_id, metric_key, metric_value, target_value, snapshot_at, notes)
                               VALUES (1, %s, %s, %s, %s, %s)
                               ON CONFLICT (organization_id, metric_key, snapshot_at)
                               DO UPDATE SET metric_value = EXCLUDED.metric_value,
                                             notes = EXCLUDED.notes""",
                            (key, None if val is None else float(val), target, snap, note))
            conn.commit()
            cur.execute("""SELECT metric_value FROM tensor_guide_tracker
                            WHERE metric_key='repro_rate' AND organization_id=1
                            ORDER BY snapshot_at DESC LIMIT 1""")
            readback = cur.fetchone()[0]
        attempts.append({"via": "sql", "ok": True, "readback": readback})
        return {"via": "sql", "kpi": kpi, "readback": readback, "attempts": attempts,
                "readback_consistent": readback is not None and
                abs(readback - kpi["repro_rate"]) <= 1e-9}
    except Exception as exc:                             # noqa: BLE001
        attempts.append({"via": "sql", "ok": False, "error": str(exc)[:300]})
    return {"via": "pending", "kpi": kpi, "attempts": attempts}


# ────────────────────────── CLI ──────────────────────────

def _dump(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def verify_audit(audit: dict, db_url: str | None = None) -> dict:
    """判据 (b)(c)(d)(e) 的机器自检：KPI 加总一致 / 归因齐备 / blocked 单列 / 预算。"""
    items = audit["items"]
    kpi = kpi_from_items(items)
    drift_items = [i for i in items if i["verdict"] == "drift"]
    missing_attr = [i["node_id"] for i in drift_items
                    if not (i.get("attribution") or {}).get("label")]
    bad_attr = [i["node_id"] for i in drift_items
                if (i.get("attribution") or {}).get("label") not in ATTRIBUTIONS]
    unverified = [i["node_id"] for i in drift_items
                  if (i.get("stability") or {}).get("runs") != 2]
    over_budget = [i["node_id"] for i in items if i["duration_sec"] > PER_NODE_TIMEOUT]
    out = {
        "kpi_consistent": kpi_consistent(audit["kpi"]["repro_rate"], items),
        "kpi_json": audit["kpi"]["repro_rate"], "kpi_recomputed": kpi["repro_rate"],
        "counts_recomputed": kpi["counts"], "counts_reported": audit["kpi"]["counts"],
        "counts_match": kpi["counts"] == audit["kpi"]["counts"],
        "n_repos": len({i["repo"] for i in items}), "n_items": len(items),
        "n_beyond_30": len(items) >= 30,
        "drift_without_attribution": missing_attr,
        "drift_with_bad_label": bad_attr,
        "drift_not_two_run_verified": unverified,
        "over_budget_nodes": over_budget,
        "blocked_nodes": [i["node_id"] for i in items if i["verdict"] == "blocked"],
        "blocked_not_in_denominator": kpi["denominator"] == len(items) - kpi["counts"]["blocked"],
        "anchors_complete": all(i.get("anchors") for i in items),
    }
    out["all_criteria_pass"] = all([
        out["kpi_consistent"], out["counts_match"], out["n_repos"] >= 3, out["n_beyond_30"],
        not out["drift_without_attribution"], not out["drift_with_bad_label"],
        not out["drift_not_two_run_verified"], not out["over_budget_nodes"],
        out["blocked_not_in_denominator"], out["anchors_complete"]])
    if db_url:
        try:
            import psycopg
            with psycopg.connect(db_url) as conn, conn.cursor() as cur:
                cur.execute("""SELECT metric_value, snapshot_at FROM tensor_guide_tracker
                                WHERE metric_key='repro_rate' AND organization_id=1
                                ORDER BY snapshot_at DESC LIMIT 1""")
                row = cur.fetchone()
            if row:
                out["tracker"] = {"value": row[0], "snapshot_at": str(row[1]),
                                  "abs_delta_vs_json": abs(float(row[0]) - (kpi["repro_rate"] or 0.0))}
        except Exception as exc:                         # noqa: BLE001
            out["tracker"] = {"error": str(exc)[:200]}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="历史验收声明的重跑复证（只读·幂等）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("frame")
    p.add_argument("--out", default="artifacts/repro_replay/frame.json")
    p.add_argument("--repos-root", default="/root/tensor-ecosystem")
    p.add_argument("--db-url", default=None)

    p = sub.add_parser("run")
    p.add_argument("--frame", default="artifacts/repro_replay/frame.json")
    p.add_argument("--n", type=int, default=36)
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    p.add_argument("--seed", default="repro-replay")
    p.add_argument("--timeout", type=int, default=PER_NODE_TIMEOUT)
    p.add_argument("--repos-root", default="/root/tensor-ecosystem")
    p.add_argument("--out", default="artifacts/repro_replay/repro_audit.json")

    p = sub.add_parser("verify")
    p.add_argument("--audit", default="artifacts/repro_replay/repro_audit.json")
    p.add_argument("--db-url", default=None)
    p.add_argument("--out", default="")

    p = sub.add_parser("sensitivity")
    p.add_argument("--audit", default="artifacts/repro_replay/repro_audit.json")
    p.add_argument("--repos-root", default="/root/tensor-ecosystem")
    p.add_argument("--nodes", type=int, default=3)
    p.add_argument("--timeout", type=int, default=PER_NODE_TIMEOUT)
    p.add_argument("--out", default="artifacts/repro_replay/negative_control.json")

    p = sub.add_parser("persist")
    p.add_argument("--audit", default="artifacts/repro_replay/repro_audit.json")
    p.add_argument("--out", default="artifacts/repro_replay/persist.json")

    args = ap.parse_args(argv)
    if args.cmd == "frame":
        frame = build_frame(args.db_url or default_db_url(), args.repos_root)
        _dump(args.out, frame)
        print(json.dumps({"frame_size": len(frame["frame"]), "stats": frame["stats"]},
                         ensure_ascii=False))
        return 0
    if args.cmd == "run":
        frame = json.load(open(args.frame, encoding="utf-8"))
        audit = run_audit(frame, args.n, args.repos_root, workers=args.workers,
                          seed=args.seed, timeout=args.timeout)
        _dump(args.out, audit)
        print(json.dumps({"kpi": audit["kpi"], "n_repos": audit["n_repos"],
                          "wall_sec": audit["wall_sec_total"],
                          "attribution": audit["attribution_counts"],
                          "duration_stats": audit["duration_stats"]}, ensure_ascii=False))
        return 0
    if args.cmd == "verify":
        audit = json.load(open(args.audit, encoding="utf-8"))
        try:
            db_url = args.db_url or default_db_url()
        except Exception:                                # noqa: BLE001
            db_url = None
        rep = verify_audit(audit, db_url)
        if args.out:
            _dump(args.out, rep)
        print(json.dumps(rep, ensure_ascii=False))
        return 0 if rep["all_criteria_pass"] else 1
    if args.cmd == "sensitivity":
        audit = json.load(open(args.audit, encoding="utf-8"))
        rep = sensitivity_probe(audit, args.repos_root, n_nodes=args.nodes, timeout=args.timeout)
        _dump(args.out, rep)
        print(json.dumps(rep["summary"], ensure_ascii=False))
        return 0
    if args.cmd == "persist":
        audit = json.load(open(args.audit, encoding="utf-8"))
        rep = persist_kpi(audit)
        _dump(args.out, rep)
        print(json.dumps(rep, ensure_ascii=False)[:1200])
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())