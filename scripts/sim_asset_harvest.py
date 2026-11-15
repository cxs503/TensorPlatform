#!/usr/bin/env python3
"""仿真成果捕获守护 —— FEAT-SIM-DATAHOUSE 子件1（资产捕获）

owner 定谳：「每次成功计算的结果没存 = 浪费」。实测 TensorLBM work 树
.npz/.npy/.vtk 计数 = 0 —— 报告合并进主干，工作树被回收，场数据（不可再生的
证据）随 worktree 一起消失。本守护把「结果落盘」从**人的纪律**变成**进程的常态**：

  扫描 succeeded 的 mission → 找它的 worktree → 认领产出（.npz/.npy/.vtk/... 场文件、
  曲线 .csv、几何件、入口配置）→ 抢收到 /root/sim-assets/{event_id}/ → 登记入库
  simulation_assets（清单 + sha256 + 字节数 + 磁盘路径 + 报告链接）。

设计要点（都可被推翻，故写明理由）：
  * **只抢收产出**：worktree 是完整 git checkout，仓库自带的参考数据（benchmark
    CSV 等）不是本单成果。默认用 `git ls-files` 建 tracked 集合并排除之，否则
    1920 棵 worktree × 187 个仓库自带 CSV 会把 /root/sim-assets 灌成 15GB 噪声。
  * **幂等**：event_id = m{mission}-{内容摘要 sha12}，同 mission 同内容重复扫描只
    落一行（ON CONFLICT DO NOTHING）。守护 15min 一轮，不幂等就会灌表。
  * **有界**：单事件文件数/总字节/单文件大小三道上限；触限记 state=partial 并在
    capture_reason 里写清截了多少 —— 宁可记一笔「没收全」，不许静默丢。
  * **降级可查**：源 worktree 已被回收（pruner 清场）→ 记 state=missed 一行，
    原因写 worktree_absent；若此前已有存量快照，params.already_captured_event_id
    指回那一行（=数据没丢，只是这轮抢收落空）。
  * **不阻塞 mission 主流程**：只读源树（不改、不删、不加锁），单进程锁防并发，
    磁盘余量不足即放弃本轮（fail-safe），任何单 mission 异常只记日志继续下一单。
  * **零第三方依赖**：DB 走 psql 子进程（本机 loong64 沙箱的 psycopg2 是 stub，
    实测 `RuntimeError: psycopg2 stub: no real driver`），故不 import 任何驱动。

用法：
  python3 sim_asset_harvest.py --once                      # 跑一轮（daemon 的一拍）
  python3 sim_asset_harvest.py --daemon                    # 常驻，默认 900s 一轮
  python3 sim_asset_harvest.py --retro [--retro-limit N]   # 对残存 work 树做一次全扫
  python3 sim_asset_harvest.py --missions 21577            # 点名某几单
  python3 sim_asset_harvest.py --extra-worktree /path      # 把任意目录当 worktree 扫
  python3 sim_asset_harvest.py --once --dry-run            # 只清点不落盘不入库
"""
from __future__ import annotations

import argparse
import base64
import errno
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import traceback

# ─────────────────────────── 常量与配置 ───────────────────────────
TP_AGENT = pathlib.Path("/root/tp-agent")
AGENT_ENV = TP_AGENT / "agent.env"
ECOSYSTEM = pathlib.Path("/root/tensor-ecosystem")
# 非 ecosystem 下的仓（与 tp_worker.prepare_worktree 的 _ALIASES 同口径）
REPO_ALIASES = {
    "ship-platform": pathlib.Path("/root/ship-performance-platform-incoming/ship-performance-platform"),
    "ship-performance-platform": pathlib.Path(
        "/root/ship-performance-platform-incoming/ship-performance-platform"),
}

SIM_ROOT = pathlib.Path(os.environ.get("TP_SIM_ASSET_ROOT", "/root/sim-assets"))
STATE_PATH = SIM_ROOT / "harvest_state.json"
LOG_PATH = SIM_ROOT / "harvest.log"
LOCK_PATH = SIM_ROOT / ".harvest.lock"
STAGING = SIM_ROOT / ".staging"

MIN_BYTES = 1024                      # 规格：抢收 >1KB 的产物
DEFAULT_MAX_FILES = 3000              # 单事件文件数上限
DEFAULT_MAX_BYTES = 2 * 1024 ** 3     # 单事件总字节上限（2GiB）
DEFAULT_PER_FILE_MAX = 512 * 1024 ** 2
DISK_HEADROOM = 5 * 1024 ** 3         # 磁盘保底余量

EXT_FIELD = {".npz", ".npy", ".vtk", ".vtu", ".vti", ".xdmf", ".xmf", ".h5", ".hdf5", ".nc"}
EXT_CURVE = {".csv", ".tsv", ".dat"}
EXT_GEOM = {".stl", ".obj", ".step", ".stp", ".iges", ".igs", ".brep", ".msh", ".geo",
            ".gdf", ".su2", ".vtp", ".ply", ".off", ".xyz"}
EXT_CONFIG = {".yaml", ".yml", ".toml", ".ini", ".cfg", ".json"}
CONFIG_HINT = re.compile(r"(config|conf|param|settings|input|case|manifest|setup|solver|run)", re.I)

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "site-packages",
             ".mypy_cache", ".pytest_cache", ".ipynb_checkpoints", ".staging"}
SKIP_SUFFIX = (".pyc", ".pyo", ".so", ".o", ".a", ".lock")


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    if os.environ.get("TP_SIM_ASSET_QUIET") != "1":   # --json 模式下保 stdout 纯 JSON
        print(line, flush=True)
    try:
        SIM_ROOT.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_agent_env(path: pathlib.Path = AGENT_ENV) -> dict:
    cfg = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            k, v = raw.split("=", 1)
            cfg[k.strip()] = v.strip()
    except OSError:
        pass
    return cfg


# ─────────────────────────── 数据库（psql 子进程） ───────────────────────────
def psql(sql: str, db: str | None = None, timeout: int = 120) -> tuple[int, str]:
    """走 psql 子进程。默认以 postgres 角色连（本机 local trust；agent 的 root 角色
    在 public schema 上无 CREATE 权限，实测 permission denied）。"""
    cfg = load_agent_env()
    db = db or cfg.get("TP_PG_DB", "tensorplatform")
    user = os.environ.get("TP_PG_USER") or cfg.get("TP_PG_USER") or "postgres"
    p = subprocess.run(["psql", "-U", user, "-d", db, "-q", "-tA", "-F", "\t",
                        "-v", "ON_ERROR_STOP=1", "-f", "-"],
                       input=sql, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def table_ready(db: str | None = None) -> bool:
    rc, out = psql("SELECT to_regclass('public.simulation_assets') IS NOT NULL;", db=db)
    return rc == 0 and out.strip().lower() in ("t", "true")


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _json_literal(obj) -> str:
    """JSONB 字面量：base64 过关 + 服务端 decode，彻底绕开转义地雷。

    曾用 dollar-quoting（$j<hex>$ ... $j<hex>$），实测在真实数据上翻车：
    m20058 的清单里出现会让 psql -f - 的脚本解析与 JSON 文本打架的字节序列，
    落库报 `invalid input syntax for type json`（捕获失败、资产白抢收一次）。
    base64 字母表只有 [A-Za-z0-9+/=]，不含引号/反斜杠/$/分号/换行，任何
    内容都进不了 SQL 语法层——这是可证明安全的那一种写法。
    """
    text = json.dumps(obj, ensure_ascii=False)
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"convert_from(decode('{b64}', 'base64'), 'UTF8')::jsonb"


def insert_asset(event: dict, db: str | None = None) -> int | None:
    """登记一行；event_id 冲突（同内容已登记）返回 None。"""
    cols = ["event_id", "mission_id", "repo", "case_key", "params", "field_manifest",
            "curve_manifest", "geometry_manifest", "config_manifest", "report_url",
            "disk_path", "bytes_total", "file_count", "state", "capture_reason",
            "source_worktree"]
    vals = [
        _literal(event["event_id"]),
        _literal(event.get("mission_id")),
        _literal(event.get("repo")),
        _literal(event.get("case_key") or "unknown"),
        _json_literal(event.get("params") or {}),
        _json_literal(event.get("field_manifest") or []),
        _json_literal(event.get("curve_manifest") or []),
        _json_literal(event.get("geometry_manifest") or []),
        _json_literal(event.get("config_manifest") or []),
        _literal(event.get("report_url")),
        _literal(event.get("disk_path")),
        _literal(int(event.get("bytes_total") or 0)),
        _literal(int(event.get("file_count") or 0)),
        _literal(event.get("state") or "captured"),
        _literal(event.get("capture_reason")),
        _literal(event.get("source_worktree")),
    ]
    sql = ("INSERT INTO simulation_assets (" + ", ".join(cols) + ") VALUES ("
           + ", ".join(vals) + ") ON CONFLICT (event_id) DO NOTHING RETURNING id;")
    rc, out = psql(sql, db=db)
    if rc != 0:
        raise RuntimeError(f"登记失败 event={event['event_id']}: {out.strip()[:400]}")
    out = out.strip()
    return int(out) if out.isdigit() else None


def prior_capture_event(mission_id: int, db: str | None = None) -> str | None:
    rc, out = psql("SELECT event_id FROM simulation_assets WHERE mission_id = "
                   f"{int(mission_id)} AND state IN ('captured','partial') "
                   "ORDER BY captured_at DESC LIMIT 1;", db=db)
    if rc != 0:
        return None
    return out.strip() or None


# ─────────────────────────── mission 台账 ───────────────────────────
_MISSION_SQL = """
SELECT m.id,
       coalesce(m.repo, ''),
       m.status,
       coalesce(to_char(m.finished_at, 'YYYY-MM-DD"T"HH24:MI:SSOF'), ''),
       coalesce(to_char(m.created_at, 'YYYY-MM-DD"T"HH24:MI:SSOF'), ''),
       coalesce(m.task_key, ''),
       coalesce(m.report_id::text, ''),
       coalesce(replace(replace(left(m.goal, 600), E'\\n', ' '), E'\\t', ' '), ''),
       coalesce(m.model, ''),
       coalesce(m.budget::text, '{{}}')
FROM tensor_dev_missions m
WHERE m.status = 'succeeded' {extra}
ORDER BY m.id DESC
{limit}
"""


def fetch_missions(missions: list[int] | None = None, window: int = 400,
                   since_hours: int | None = None, db: str | None = None) -> list[dict]:
    extra, limit = "", f"LIMIT {int(window)}"
    if missions:
        ids = ",".join(str(int(m)) for m in missions)
        extra, limit = f"AND m.id IN ({ids})", ""
    elif since_hours:
        extra, limit = f"AND m.finished_at > NOW() - INTERVAL '{int(since_hours)} hours'", ""
    rc, out = psql(_MISSION_SQL.format(extra=extra, limit=limit), db=db)
    if rc != 0:
        raise RuntimeError(f"读 mission 台账失败: {out.strip()[:400]}")
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) < 10:
            continue
        rows.append({
            "id": int(f[0]), "repo": f[1], "status": f[2], "finished_at": f[3],
            "created_at": f[4], "task_key": f[5], "report_id": f[6], "goal": f[7],
            "model": f[8], "budget": f[9],
        })
    return rows


def report_url_for(mission: dict, cfg: dict) -> str | None:
    rid = (mission.get("report_id") or "").strip()
    if not rid.isdigit():
        return None
    base = (cfg.get("TENSOR_API_BASE") or "").rstrip("/")
    return f"{base}/tensor/reports/{rid}" if base else f"/tensor/reports/{rid}"


# ─────────────────────────── worktree 定位 ───────────────────────────
def worktree_candidates(mission_id: int, repo: str, work_root: pathlib.Path) -> list[pathlib.Path]:
    """worktree 可能在三处：agent 主工作根（现行）/ 各仓 work/（历史布局）/ 船平台别名仓。"""
    cands = [work_root / f"m{mission_id}"]
    if repo:
        cands.append(ECOSYSTEM / repo / "work" / f"m{mission_id}")
        alias = REPO_ALIASES.get(repo)
        if alias:
            cands.append(alias / "work" / f"m{mission_id}")
    seen, out = set(), []
    for c in cands:
        if str(c) not in seen:
            seen.add(str(c))
            out.append(c)
    return out


def resolve_worktree(mission_id: int, repo: str, work_root: pathlib.Path) -> pathlib.Path | None:
    for c in worktree_candidates(mission_id, repo, work_root):
        if c.is_dir():
            return c
    return None


def git_tracked(wt: pathlib.Path) -> set[str] | None:
    """worktree 的 tracked 文件集（相对路径）。非 git 树返回 None。"""
    try:
        p = subprocess.run(["git", "-C", str(wt), "ls-files", "-z"],
                           capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return {x.decode("utf-8", "replace") for x in p.stdout.split(b"\0") if x}


# ─────────────────────────── 产出发现 ───────────────────────────
def file_kind(rel: str) -> str | None:
    path = pathlib.PurePath(rel)
    suf = path.suffix.lower()
    name = path.name
    if suf in EXT_FIELD:
        return "field"
    if suf in EXT_GEOM:
        return "geometry"
    if suf in EXT_CURVE:
        return "curve"
    if suf in EXT_CONFIG and CONFIG_HINT.search(name):
        return "config"
    return None


def discover(wt: pathlib.Path, *, include_tracked: bool = False,
             max_files: int = DEFAULT_MAX_FILES, max_bytes: int = DEFAULT_MAX_BYTES,
             per_file_max: int = DEFAULT_PER_FILE_MAX,
             min_bytes: int = MIN_BYTES) -> dict:
    """清点一棵 worktree 的**产出**（默认排除 git tracked 的仓库自带文件）。

    返回 {"items":[{path,size,kind}], "truncated":bool, "skip":{...},
          "scanned":int, "bytes":int}
    """
    tracked = set() if include_tracked else (git_tracked(wt) or set())
    items = []
    skipped = {"tracked": 0, "small": 0, "oversize": 0, "other": 0,
               "capped_files": 0, "capped_bytes": 0}
    total = 0
    truncated = False
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(wt):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if fn.endswith(SKIP_SUFFIX):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, wt)
            kind = file_kind(rel)
            if kind is None:
                continue
            scanned += 1
            if rel in tracked:
                skipped["tracked"] += 1
                continue
            try:
                st = os.stat(full)
            except OSError:
                skipped["other"] += 1
                continue
            if st.st_size < min_bytes:
                skipped["small"] += 1
                continue
            if st.st_size > per_file_max:
                skipped["oversize"] += 1
                continue
            if len(items) >= max_files:
                skipped["capped_files"] += 1
                truncated = True
                continue
            if total + st.st_size > max_bytes:
                skipped["capped_bytes"] += 1
                truncated = True
                continue
            items.append({"path": rel, "size": st.st_size, "kind": kind})
            total += st.st_size
    order = {"field": 0, "geometry": 1, "curve": 2, "config": 3}
    items.sort(key=lambda it: (order.get(it["kind"], 9), -it["size"]))
    return {"items": items, "truncated": truncated, "skip": skipped,
            "scanned": scanned, "bytes": total}


def _safe_join(root: pathlib.Path, rel: str) -> pathlib.Path:
    dest = (root / rel).resolve()
    if not str(dest).startswith(str(root.resolve()) + os.sep):
        raise ValueError(f"路径越界: {rel}")
    return dest


def _copy_hash(src: pathlib.Path, dst: pathlib.Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fi, dst.open("wb") as fo:
        while True:
            buf = fi.read(chunk)
            if not buf:
                break
            h.update(buf)
            fo.write(buf)
    return h.hexdigest()


def _flatten_config(path: pathlib.Path, limit: int = 60) -> dict:
    """从入口配置抽标量参数（扁平化，限键数）——解析失败一律当没有。"""
    suf = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:2_000_000]
    except OSError:
        return {}
    data = None
    if suf == ".json":
        try:
            data = json.loads(text)
        except Exception:
            return {}
    elif suf in (".yaml", ".yml"):
        try:
            import yaml  # 本机可能没有；没有就退化为极简 key: value 扫描
            data = yaml.safe_load(text)
        except Exception:
            data = {}
            for line in text.splitlines():
                m = re.match(r"^\s*([A-Za-z_][\w.\-]*)\s*:\s*([^#\n]+?)\s*$", line)
                if m:
                    data[m.group(1)] = m.group(2)
    elif suf == ".toml":
        try:
            import tomllib
            data = tomllib.loads(text)
        except Exception:
            return {}
    elif suf in (".ini", ".cfg"):
        data = {}
        sec = ""
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                sec = s[1:-1]
            elif "=" in s and not s.startswith(("#", ";")):
                k, v = s.split("=", 1)
                data[f"{sec}.{k.strip()}" if sec else k.strip()] = v.strip()
    if not isinstance(data, dict):
        return {}
    flat: dict = {}

    def walk(node, prefix, depth):
        if len(flat) >= limit or depth > 3:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{prefix}.{k}" if prefix else str(k), depth + 1)
        elif isinstance(node, (int, float, str, bool)) and not isinstance(node, bytes):
            if isinstance(node, str) and len(node) > 200:
                return
            flat[prefix] = node

    walk(data, "", 0)
    return flat


# ─────────────────────────── 捕获 ───────────────────────────
def case_key_for(mission: dict, items: list[dict]) -> str:
    tk = (mission.get("task_key") or "").strip()
    if tk:
        return tk
    firsts = {pathlib.PurePath(it["path"]).parts[0] for it in items
              if len(pathlib.PurePath(it["path"]).parts) > 1}
    if len(firsts) == 1:
        return firsts.pop()
    return f"m{mission['id']}"


def _safe_json(text):
    try:
        return json.loads(text) if text else {}
    except Exception:
        return {"raw": str(text)[:200]}


def capture(wt: pathlib.Path, mission: dict, disc: dict, *, cfg: dict,
            dry_run: bool = False, db: str | None = None) -> dict:
    """把发现到的产出抢收进 /root/sim-assets/{event_id}/ 并登记。"""
    items = disc["items"]
    if not items:
        return {"status": "no_output", "mission_id": mission["id"], "event_id": None}
    need = disc["bytes"]
    staging = STAGING / f"m{mission['id']}-{os.getpid()}-{int(time.time() * 1000)}"
    manifests = {"field": [], "curve": [], "geometry": [], "config": []}
    run_cfg: dict = {}
    try:
        if not dry_run:
            SIM_ROOT.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(SIM_ROOT).free
            if free < need + DISK_HEADROOM:
                return {"status": "disk_low", "mission_id": mission["id"], "event_id": None,
                        "detail": f"free={free} need={need + DISK_HEADROOM}"}
            staging.mkdir(parents=True, exist_ok=True)
        for it in items:
            src = _safe_join(wt, it["path"])
            kind = it["kind"]
            if dry_run:
                manifests[kind].append({"path": it["path"], "size": it["size"], "sha256": None})
                continue
            dst = _safe_join(staging, it["path"])
            digest = _copy_hash(src, dst)
            manifests[kind].append({"path": it["path"], "size": it["size"], "sha256": digest})
            if kind == "config" and len(run_cfg) < 80:
                for k, v in _flatten_config(src).items():
                    run_cfg[f"{pathlib.PurePath(it['path']).name}:{k}"[:160]] = v
        allm = (manifests["field"] + manifests["curve"]
                + manifests["geometry"] + manifests["config"])
        digest_src = "\n".join(f"{m['path']}:{m['size']}:{m['sha256']}"
                               for m in sorted(allm, key=lambda m: m["path"]))
        content_digest = hashlib.sha256(digest_src.encode()).hexdigest()[:12]
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    event_id = f"m{mission['id']}-{content_digest}"
    event = {
        "event_id": event_id,
        "mission_id": mission["id"],
        "repo": mission.get("repo"),
        "case_key": case_key_for(mission, items),
        "params": {
            "mission": {"goal_head": (mission.get("goal") or "")[:400],
                        "model": mission.get("model") or None,
                        "status": mission.get("status"),
                        "finished_at": mission.get("finished_at") or None,
                        "created_at": mission.get("created_at") or None,
                        "budget": _safe_json(mission.get("budget"))},
            "run_config": run_cfg,
            "capture": {"scanned": disc["scanned"], "bytes": disc["bytes"],
                        "truncated": disc["truncated"], "skip": disc["skip"],
                        "root": str(SIM_ROOT)},
        },
        "field_manifest": manifests["field"],
        "curve_manifest": manifests["curve"],
        "geometry_manifest": manifests["geometry"],
        "config_manifest": manifests["config"],
        "report_url": report_url_for(mission, cfg),
        "disk_path": str(SIM_ROOT / event_id),
        "bytes_total": disc["bytes"],
        "file_count": len(allm),
        "state": "partial" if disc["truncated"] else "captured",
        "capture_reason": ("truncated: " + json.dumps(disc["skip"], ensure_ascii=False)
                           if disc["truncated"] else None),
        "source_worktree": str(wt),
    }
    if dry_run:
        shutil.rmtree(staging, ignore_errors=True)
        return {"status": "dry_run", "event": event, "mission_id": mission["id"],
                "bytes": disc["bytes"], "files": len(allm), "state": event["state"]}

    final = SIM_ROOT / event_id
    if final.exists():          # 同内容已抢收过（幂等）
        shutil.rmtree(staging, ignore_errors=True)
        row = insert_asset(event, db=db)   # ON CONFLICT DO NOTHING → None
        return {"status": "duplicate", "event_id": event_id, "mission_id": mission["id"],
                "inserted": row is not None}
    SIM_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(staging, final)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            shutil.move(str(staging), str(final))
        else:
            raise
    event["disk_path"] = str(final)
    row_id = insert_asset(event, db=db)
    return {"status": "captured", "event_id": event_id, "mission_id": mission["id"],
            "inserted": row_id is not None, "row_id": row_id, "bytes": disc["bytes"],
            "files": len(allm), "state": event["state"]}


# ─────────────────────────── 降级：worktree 已清 ───────────────────────────
def record_missed(mission: dict, *, cfg: dict, candidates: list[pathlib.Path],
                  prior_event: str | None, db: str | None = None,
                  dry_run: bool = False) -> dict:
    event = {
        "event_id": f"m{mission['id']}-missed",
        "mission_id": mission["id"],
        "repo": mission.get("repo"),
        "case_key": (mission.get("task_key") or f"m{mission['id']}"),
        "params": {"mission": {"finished_at": mission.get("finished_at") or None,
                               "goal_head": (mission.get("goal") or "")[:400]},
                   "already_captured_event_id": prior_event,
                   "checked_paths": [str(c) for c in candidates]},
        "report_url": report_url_for(mission, cfg),
        "disk_path": None,
        "bytes_total": 0,
        "file_count": 0,
        "state": "missed",
        "capture_reason": (
            f"worktree_absent: 已回收，存量快照 {prior_event} 保全" if prior_event
            else "worktree_absent: 源工作树已回收，本单产出无法再抢收（数据丢失）"),
        "source_worktree": str(candidates[0]) if candidates else None,
    }
    if dry_run:
        return {"status": "dry_run_missed", "event": event, "mission_id": mission["id"]}
    row_id = insert_asset(event, db=db)
    return {"status": "missed", "event_id": event["event_id"], "mission_id": mission["id"],
            "inserted": row_id is not None, "row_id": row_id, "event": event}


# ─────────────────────────── 状态文件 ───────────────────────────
def load_state() -> dict:
    try:
        st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        st.setdefault("seen", {})
        return st
    except Exception:
        return {"seen": {}, "cycles": 0, "totals": {}}


def save_state(state: dict) -> None:
    try:
        SIM_ROOT.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, STATE_PATH)
    except OSError as exc:
        log(f"WARN 状态文件写入失败: {exc}")


# ─────────────────────────── 一轮 ───────────────────────────
def run_cycle(*, window: int = 400, missions: list[int] | None = None,
              since_hours: int | None = None, retro: bool = False,
              retro_limit: int | None = None, extra_worktrees: list[str] | None = None,
              dry_run: bool = False, include_tracked: bool = False,
              max_files: int = DEFAULT_MAX_FILES, max_bytes: int = DEFAULT_MAX_BYTES,
              persist_state: bool = True, db: str | None = None,
              work_root: pathlib.Path | None = None) -> dict:
    cfg = load_agent_env()
    work_root = work_root or pathlib.Path(cfg.get("TP_WORK_ROOT") or "/root/tp-agent/work")
    state = load_state()
    seen: dict = state.setdefault("seen", {})

    if retro:
        rows = fetch_missions(window=retro_limit or 100000, db=db)
    else:
        rows = fetch_missions(missions=missions, window=window,
                              since_hours=since_hours, db=db)
    by_id = {r["id"]: r for r in rows}

    # 显式点名的 mission 若不在 succeeded 台账（历史遗留 worktree），补一个最小描述
    for mid in (missions or []):
        by_id.setdefault(int(mid), {"id": int(mid), "repo": "", "status": "unknown",
                                    "task_key": "", "goal": "", "report_id": "",
                                    "finished_at": "", "created_at": "", "model": "",
                                    "budget": "{}"})

    extra_targets: list[tuple[int, pathlib.Path]] = []
    for raw in (extra_worktrees or []):
        p = pathlib.Path(raw)
        m = re.search(r"m(\d+)", p.name)
        mid = int(m.group(1)) if m else 900000000 + (abs(hash(str(p))) % 10 ** 6)
        extra_targets.append((mid, p))

    explicit = bool(missions) or bool(extra_targets)
    stats = {"cycle_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "missions_considered": len(by_id) + len(extra_targets),
             "captured": 0, "duplicate": 0, "partial": 0, "no_output": 0,
             "missed": 0, "absent_unseen": 0, "errors": 0, "disk_low": 0,
             "bytes": 0, "events": [], "retro": {} if retro else None}
    if dry_run:
        stats["dry_run"] = True

    def handle(mid: int, mission: dict, wt: pathlib.Path | None,
               cands: list[pathlib.Path]) -> None:
        if wt is None:
            if str(mid) in seen or explicit:
                try:
                    res = record_missed(mission, cfg=cfg, candidates=cands,
                                        prior_event=prior_capture_event(mid, db=db),
                                        db=db, dry_run=dry_run)
                    stats["missed"] += 1
                    seen.pop(str(mid), None)
                    stats["events"].append({"status": res["status"], "mission_id": mid,
                                            "event_id": res.get("event_id"),
                                            "inserted": res.get("inserted")})
                except Exception as exc:
                    stats["errors"] += 1
                    log(f"ERROR m{mid} missed 登记失败: {exc}")
            else:
                stats["absent_unseen"] += 1
                stats["events"].append({"status": "absent_unseen", "mission_id": mid})
            return
        try:
            disc = discover(wt, include_tracked=include_tracked,
                            max_files=max_files, max_bytes=max_bytes)
            seen[str(mid)] = {"worktree": str(wt),
                              "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S")}
            res = capture(wt, mission, disc, cfg=cfg, dry_run=dry_run, db=db)
        except Exception as exc:
            stats["errors"] += 1
            log(f"ERROR m{mid} 捕获失败: {exc}")
            return
        st = res.get("status")
        if st in ("captured", "dry_run"):
            stats["captured"] += 1
            stats["bytes"] += int(res.get("bytes") or 0)
            if res.get("state") == "partial":
                stats["partial"] += 1
        elif st == "duplicate":
            stats["duplicate"] += 1
        elif st == "no_output":
            stats["no_output"] += 1
        elif st == "disk_low":
            stats["disk_low"] += 1
            log(f"WARN m{mid} 磁盘余量不足，跳过: {res.get('detail')}")
        stats["events"].append({k: v for k, v in res.items() if k != "event"})
        if st == "captured":
            log(f"CAPTURED m{mid} → {res['event_id']} files={res['files']} "
                f"bytes={res['bytes']} state={res['state']}")

    for mid, mission in by_id.items():
        cands = worktree_candidates(mid, mission.get("repo") or "", work_root)
        handle(mid, mission, resolve_worktree(mid, mission.get("repo") or "", work_root), cands)
    for mid, path in extra_targets:
        mission = {"id": mid, "repo": "external", "status": "external", "task_key": "",
                   "goal": f"extra worktree {path}", "report_id": "", "finished_at": "",
                   "created_at": "", "model": "", "budget": "{}"}
        handle(mid, mission, path if path.is_dir() else None, [path])

    # retro 口径：succeeded 台账里有多少单的 worktree 还在（=数据可抢收规模）
    if retro:
        present = sum(1 for mid, m in by_id.items()
                      if resolve_worktree(mid, m.get("repo") or "", work_root))
        stats["retro"] = {"missions_total": len(by_id), "worktree_present": present,
                          "worktree_absent": len(by_id) - present}

    if persist_state and not dry_run:
        state["cycles"] = int(state.get("cycles") or 0) + 1
        state["last_cycle"] = {k: v for k, v in stats.items() if k != "events"}
        tot = state.setdefault("totals", {})
        for k in ("captured", "duplicate", "partial", "no_output", "missed", "errors"):
            tot[k] = int(tot.get(k) or 0) + int(stats.get(k) or 0)
        tot["bytes"] = int(tot.get("bytes") or 0) + int(stats.get("bytes") or 0)
        save_state(state)
    return stats


def acquire_lock():
    SIM_ROOT.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="仿真成果捕获守护（FEAT-SIM-DATAHOUSE）")
    ap.add_argument("--once", action="store_true", help="跑一轮")
    ap.add_argument("--daemon", action="store_true", help="常驻轮转")
    ap.add_argument("--interval", type=int, default=900, help="常驻轮转间隔秒（默认 900）")
    ap.add_argument("--retro", action="store_true", help="对残存 work 树做一次全扫")
    ap.add_argument("--retro-limit", type=int, default=None, help="--retro 只看最近 N 单")
    ap.add_argument("--missions", type=int, nargs="*", default=None, help="点名 mission")
    ap.add_argument("--extra-worktree", action="append", default=None,
                    help="把任意目录当 worktree 扫（红队/自测用）")
    ap.add_argument("--window", type=int, default=400, help="周期性扫描最近 N 单（默认 400）")
    ap.add_argument("--since-hours", type=int, default=None)
    ap.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    ap.add_argument("--include-tracked", action="store_true",
                    help="连 git tracked 的文件一起收（默认只收产出）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true", help="把本轮统计打到 stdout（JSON）")
    ap.add_argument("--no-lock", action="store_true", help="不加单进程锁（仅调试）")
    args = ap.parse_args(argv)
    if args.json:
        os.environ["TP_SIM_ASSET_QUIET"] = "1"

    if not args.once and not args.daemon and not args.retro \
            and not args.missions and not args.extra_worktree:
        ap.print_help()
        return 2

    if not table_ready():
        log("FATAL simulation_assets 表不存在：先跑迁移 "
            "backend/migrations/126_simulation_assets.sql")
        return 3

    lock = None if args.no_lock else acquire_lock()
    if not args.no_lock and lock is None:
        log("另一轮捕获正在进行（锁占用），本轮跳过")
        return 0

    kw = dict(window=args.window, missions=args.missions, since_hours=args.since_hours,
              retro=args.retro, retro_limit=args.retro_limit,
              extra_worktrees=args.extra_worktree, dry_run=args.dry_run,
              include_tracked=args.include_tracked, max_files=args.max_files,
              max_bytes=args.max_bytes)

    if args.daemon:
        log(f"守护启动：每 {args.interval}s 一轮（window={args.window}）")
        while True:
            try:
                st = run_cycle(**kw)
                log("cycle: " + json.dumps({k: v for k, v in st.items() if k != "events"},
                                           ensure_ascii=False))
            except KeyboardInterrupt:
                log("守护收到中断，退出")
                return 0
            except Exception as exc:
                log(f"ERROR 本轮异常（下一轮继续）: {exc}")
            time.sleep(args.interval)

    st = run_cycle(**kw)
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=1))
    else:
        log("cycle: " + json.dumps({k: v for k, v in st.items() if k != "events"},
                                   ensure_ascii=False))
    return 0 if st.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())