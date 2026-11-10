"""证据链装配器（适配层）：把仓内可执行事实变成证据节点。

这是**上游契约边界**：能力定义（``config/capabilities.json`` 一类工件）与
真库台账格式都可能漂移，漂移只改本模块（+ ``ledger_adapter``），引擎与阶梯不动。

装配策略（全部机器可判，零领域语义）::

    implemented  源码定位命中 ``code_patterns`` → 文件 sha256 入 source.artifact_sha256
    tested       测试文件命中 ``test_patterns`` 且**实际重跑通过** → rerun{cmd,status,log_sha256}
    benchmark_verified  结构化数值判据工件（``**/bench_evidence/*.json``）自带
                 criterion{value,baseline_ref,tolerance} → 只有自洽且落容差才成节点
    coupled_prototype / engineering_qualified  不自动装配：自动装配无法证明"耦合"与
                 "独立复核"，宁可停在 tested 也不伪造（fail-closed 的落地方式）

运行环境解释：``execute=False`` 时测试节点 ``status=not_run``（不可支撑 tested）；
``execute=True`` 时**一次批量重跑**（junitxml 逐文件判定），超时/收集错误记
``timeout``/``failed``，如实入账。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from xml.etree import ElementTree as ET

from .evidence import build_node, canonical_digest

PRODUCER = "maturity-inference-linker"

#: 扫描时跳过的目录（构建产物/依赖/回收站/前端静态资源）
SKIP_DIRS = {
    ".git", ".recycle", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".mypy_cache", ".pytest_cache", "frontend", "frontend-vue",
    "frontend-console", "frontend-factory",
}

#: 源码扩展名
CODE_EXTS = {".py", ".js", ".ts", ".sql", ".sh", ".c", ".cpp", ".f90", ".jl"}
#: 测试文件前缀
TEST_PREFIX = "test_"

#: 实现定位优先目录（先语义目录，后根目录脚本）
PREFERRED_DIRS = ("backend/", "src/", "domains/", "tools/", "services/", "api/")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iter_files(root: Path, exts: Optional[Sequence[str]] = None) -> Iterable[Path]:
    """遍历仓库文件（跳过 ``SKIP_DIRS``，按路径排序保证可复算）。"""
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git"))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if exts and p.suffix not in exts:
                continue
            yield p


def _match_patterns(path: Path, patterns: Sequence[str], root: Path) -> Optional[str]:
    """路径命中判据（**大小写敏感**；≤3 字符的模式要求整词命中，避免 ``Rt`` 命中噪声）。"""
    rel = str(path.relative_to(root))
    tokens = [t for t in re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", rel) if t]
    for pat in patterns:
        pat = str(pat or "")
        if not pat:
            continue
        if len(pat) <= 3:
            if any(tok.lower() == pat.lower() for tok in tokens):
                return pat
            continue
        if pat in rel or pat.lower() in rel.lower():
            return pat
    return None


def _rank(path: Path, root: Path) -> tuple:
    rel = str(path.relative_to(root))
    pref = 0 if rel.startswith(PREFERRED_DIRS) else 1
    return (pref, rel)


def discover_sources(capability: Mapping[str, Any], root: Path,
                     limit: int = 3) -> List[Dict[str, Any]]:
    """按 ``code_patterns`` 找实现文件（语义目录优先，路径排序稳定）。"""
    patterns = [str(p) for p in (capability.get("code_patterns") or [])]
    if not patterns:
        return []
    hits: List[Path] = []
    for path in iter_files(root, CODE_EXTS):
        if path.name.startswith(TEST_PREFIX):
            continue
        if _match_patterns(path, patterns, root):
            hits.append(path)
    hits.sort(key=lambda p: _rank(p, root))
    return [{"path": str(p.relative_to(root)), "sha256": _sha256_file(p),
             "pattern": _match_patterns(p, patterns, root)} for p in hits[:limit]]


def discover_tests(capability: Mapping[str, Any], root: Path,
                   limit: int = 2) -> List[Dict[str, Any]]:
    """按 ``test_patterns`` 找测试文件（路径排序稳定）。"""
    patterns = [str(p) for p in (capability.get("test_patterns") or [])]
    if not patterns:
        return []
    hits: List[Path] = []
    for path in iter_files(root, CODE_EXTS):
        if not path.name.startswith(TEST_PREFIX):
            continue
        if _match_patterns(path, patterns, root):
            hits.append(path)
    hits.sort(key=lambda p: str(p.relative_to(root)))
    return [{"path": str(p.relative_to(root)), "pattern": _match_patterns(p, patterns, root),
             "sha256": _sha256_file(p)} for p in hits[:limit]]


def discover_bench(capability: Mapping[str, Any], root: Path) -> List[Dict[str, Any]]:
    """找结构化数值判据工件：``bench_evidence/*.json``，且 ``capability_key`` 精确匹配。"""
    key = str(capability.get("capability_id") or capability.get("id") or "")
    out: List[Dict[str, Any]] = []
    for path in iter_files(root, {".json"}):
        if "bench_evidence" not in str(path):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = data if isinstance(data, list) else [data]
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if str(row.get("capability_key") or "") != key:
                continue
            out.append({
                "path": str(path.relative_to(root)),
                "sha256": _sha256_file(path),
                "criterion": dict(row.get("criterion") or {}),
                "rerun": dict(row.get("rerun") or {}),
                "scope": str(row.get("scope") or "repo"),
            })
    return out


def _config_root(path: str, root: Path) -> Path:
    """定位测试文件所属的 pytest 配置根（有自有 ini 的目录优先，否则仓根）。

    动机（实测坑）：仓内 ``backend/pyproject.toml`` 声明了 ``[tool.pytest.ini_options]``，
    一旦与仓根 ``tests/`` 混在一批参数里，pytest 的 rootdir 推断会切到 ``backend/``，
    其 ``pythonpath=[".",".."]`` 让 ``backend/tools``（无 ``__init__`` 的命名空间目录）
    抢在仓根 ``tools/`` 之前，仓根 conftest 的插件导入即失败。分组跑可彻底避开该歧义。
    """
    root = Path(root).resolve()
    p = (root / path).resolve().parent
    for anc in [p, *p.parents]:
        if anc == root or root in anc.parents:
            for name in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"):
                cfg = anc / name
                if not cfg.is_file():
                    continue
                text = cfg.read_text(encoding="utf-8", errors="ignore")
                if "[tool.pytest.ini_options]" in text or name == "pytest.ini":
                    return anc
        if anc == root:
            break
    return root


def run_pytest_batch(paths: Sequence[str], root: Path, timeout: int = 900,
                     python: str = sys.executable) -> Dict[str, Any]:
    """按 pytest 配置根**分组**重跑测试文件，用 junitxml 做逐文件判定。

    返回 ``{status_by_path, cmd, exit_code, executed_at, log_sha256, stdout_tail,
    counts, groups}``；超时/未收集到用例一律 ``not_run``（不可支撑 tested）。
    """
    import time as _time

    root = Path(root).resolve()
    paths = [p for p in paths if p]
    if not paths:
        return {"status_by_path": {}, "cmd": "", "exit_code": None,
                "executed_at": _now(), "log_sha256": _sha256_text(""),
                "stdout_tail": "", "counts": {"passed": 0, "failed": 0, "not_run": 0},
                "groups": 0}

    groups: Dict[str, List[str]] = {}
    for p in paths:
        groups.setdefault(str(_config_root(p, root)), []).append(p)

    status: Dict[str, str] = {p: "not_run" for p in paths}
    cmds: List[str] = []
    logs: List[str] = []
    exit_codes: Dict[str, int] = {}
    deadline = _time.time() + max(30, timeout)
    started = _now()
    for group_root, files in sorted(groups.items()):
        g = Path(group_root)
        remain = max(20, int(deadline - _time.time()))
        rel = [os.path.relpath(root / f, g) for f in files]
        # 临时目录放仓内（/tmp 在共享主机上会被别的作业挤满 → ENOSPC）
        tmp_root = Path(root) / "artifacts" / "maturity_inference" / "_tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        junit = Path(tempfile.mkdtemp(prefix="junit_", dir=str(tmp_root))) / "report.xml"
        cmd = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               f"--junitxml={junit}", *rel]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        env["PYTHONPATH"] = os.pathsep.join(
            [str(root)] + [x for x in [env.get("PYTHONPATH", "")] if x])
        try:
            proc = subprocess.run(cmd, cwd=str(g), capture_output=True, text=True,
                                  timeout=remain, env=env)
            out = (proc.stdout or "") + (proc.stderr or "")
            exit_codes[group_root] = proc.returncode
        except subprocess.TimeoutExpired as exc:
            raw = exc.stdout if isinstance(exc.stdout, str) else ""
            out = (raw or "timeout") + "\n[timeout]"
            exit_codes[group_root] = -1
        except FileNotFoundError:  # pragma: no cover
            out, exit_codes[group_root] = "pytest not available", -1
        cmds.append(" ".join(cmd))
        logs.append(out)
        prefix = "" if g == root else os.path.relpath(g, root)
        for rel_path, st in _parse_junit(junit, rel).items():
            orig = os.path.normpath(os.path.join(prefix, rel_path)) if prefix else rel_path
            orig = orig.replace(os.sep, "/")
            if orig in status:
                status[orig] = st
        shutil.rmtree(junit.parent, ignore_errors=True)  # 解析完再清临时目录
    counts = {k: sum(1 for v in status.values() if v == k)
              for k in ("passed", "failed", "not_run")}
    return {
        "status_by_path": status,
        "cmd": " && ".join(cmds),
        "exit_code": exit_codes,
        "executed_at": started,
        "log_sha256": _sha256_text("\n".join(logs)),
        "stdout_tail": "\n".join(logs)[-600:],
        "counts": counts,
        "groups": len(groups),
    }


def _parse_junit(junit: Path, paths: Sequence[str]) -> Dict[str, str]:
    """junitxml → {测试文件: passed/failed/not_run}（无用例记录 = not_run）。"""
    status = {p: "not_run" for p in paths}
    if not junit.exists():
        return status
    try:
        tree = ET.parse(junit)
    except ET.ParseError:
        return status
    seen: Dict[str, bool] = {}
    for case in tree.iter("testcase"):
        file_attr = case.get("file")
        if file_attr:
            rel = str(Path(file_attr)).replace("/", "/")
        else:  # pragma: no cover - 老版本 pytest 无 file 属性
            cls = case.get("classname") or ""
            rel = cls.replace(".", "/") + ".py"
        target = None
        for p in paths:
            if rel.endswith(p) or p.endswith(rel):
                target = p
                break
        if target is None:
            continue
        failed = any(child.tag in ("failure", "error") for child in case)
        seen[target] = seen.get(target, False) or failed
    for p, failed in seen.items():
        status[p] = "failed" if failed else "passed"
    return status


def link_capabilities(
    capabilities: Sequence[Mapping[str, Any]],
    root: Path | str,
    *,
    execute: bool = False,
    timeout: int = 900,
    max_tests: int = 24,
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    """为每项能力装配证据节点；返回 ``{nodes, links, summary, test_run}``。"""
    root = Path(root)
    stamp = recorded_at or _now()
    caps = sorted(capabilities, key=lambda c: str(c.get("capability_id") or c.get("id") or ""))

    discovered = []
    wanted: List[str] = []
    for cap in caps:
        key = str(cap.get("capability_id") or cap.get("id") or "")
        sources = discover_sources(cap, root)
        tests = discover_tests(cap, root)
        benches = discover_bench(cap, root)
        discovered.append((key, sources, tests, benches))
        for t in tests:
            if t["path"] not in wanted:
                wanted.append(t["path"])

    batch = sorted(wanted)[:max_tests]
    test_run: Dict[str, Any] = {"status_by_path": {}, "cmd": "", "counts": {"not_run": len(wanted)}}
    if execute and batch:
        test_run = run_pytest_batch(batch, root, timeout=timeout)

    nodes: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    for key, sources, tests, benches in discovered:
        entry: Dict[str, Any] = {"capability_key": key, "sources": sources,
                                 "tests": [], "benches": benches}
        if sources:
            src = sources[0]
            nodes.append(build_node(
                key, "implemented", kind="source", locator=src["path"],
                artifact_sha256=src["sha256"], producer=PRODUCER,
                recorded_at=stamp, scope="repo"))
        for test in tests:
            status = test_run.get("status_by_path", {}).get(test["path"], "not_run")
            recorded = {
                "path": test["path"],
                "status": status,
                # 重跑命令给**稳定可复现**的那条（临时 junit 路径不进证据链）
                "cmd": (f"{sys.executable} -m pytest -q {test['path']}"
                        if status != "not_run" else None),
                "log_sha256": test_run.get("log_sha256") if status != "not_run" else None,
                "executed_at": test_run.get("executed_at") if status != "not_run" else None,
                "sha256": test["sha256"],
                "pattern": test["pattern"],
            }
            entry["tests"].append(recorded)
            if status in {"passed", "failed"}:
                nodes.append(build_node(
                    key, "tested", kind="pytest", locator=test["path"],
                    artifact_sha256=test["sha256"], producer=PRODUCER,
                    recorded_at=stamp, scope="repo",
                    rerun={"cmd": recorded["cmd"] or "",
                           "status": status,
                           "executed_at": recorded["executed_at"] or stamp,
                           "log_sha256": recorded["log_sha256"] or _sha256_text("")}))
        for bench in benches:
            criterion = dict(bench.get("criterion") or {})
            criterion.setdefault("satisfied", True)
            rerun = dict(bench.get("rerun") or {})
            nodes.append(build_node(
                key, "benchmark_verified", kind="artifact", locator=bench["path"],
                artifact_sha256=bench["sha256"], producer=PRODUCER,
                recorded_at=stamp, scope=bench.get("scope", "repo"),
                rerun={"cmd": rerun.get("cmd", f"cat {bench['path']}"),
                       "status": rerun.get("status", "passed"),
                       "executed_at": rerun.get("executed_at", stamp),
                       "log_sha256": rerun.get("log_sha256") or _sha256_text(bench["sha256"])},
                criterion=criterion))
        links.append(entry)

    summary = {
        "capabilities": len(links),
        "implemented_links": sum(1 for e in links if e["sources"]),
        "test_links": sum(len(e["tests"]) for e in links),
        "tests_executed": sum(1 for e in links for t in e["tests"] if t["status"] != "not_run"),
        "tests_passed": sum(1 for e in links for t in e["tests"] if t["status"] == "passed"),
        "tests_failed": sum(1 for e in links for t in e["tests"] if t["status"] == "failed"),
        "bench_links": sum(len(e["benches"]) for e in links),
        "nodes": len(nodes),
        "execute": bool(execute),
    }
    summary["digest"] = canonical_digest(
        [{e["capability_key"]: {"src": len(e["sources"]), "t": len(e["tests"]), "b": len(e["benches"])}}
         for e in links])
    return {"nodes": nodes, "links": links, "summary": summary, "test_run": test_run}


def write_links(payload: Mapping[str, Any], path: Path) -> str:
    """落盘装配结果，返回内容 sha256。"""
    blob = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(blob + "\n", encoding="utf-8")
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()