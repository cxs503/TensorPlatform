"""验证面活性巡检：把静态导入解析与真实 pytest 收集合成 alive / dead / unmapped 三态。

单位（unit）= 一个测试模块（``tests/**/*.py``）。注册关系来自能力注册表
``config/capabilities.json`` 的 ``test_patterns``（正则），因此**零领域语义**：
换一份注册表就换一套对应关系，代码不动。

三态定义（互斥且穷尽）：

===========  ==========================================================
``dead``     该模块要么 pytest 收集失败（import/conftest/数据缺失），要么静态导入
             不可解析（模块或符号迁移）——即"看着在、其实从没跑过"
``unmapped`` 活性无碍，但没有任何已登记能力认领它（验证面与台账之间没有边）
``alive``    收集成功且静态导入全部可解析
===========  ==========================================================

能力层（capability）再聚合一次：任一被认领模块 dead ⇒ 该能力验证面 dead。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from . import imports as imports_mod
from .collector import CollectResult, run_pytest_collect_chunked

SCHEMA = "verification-vitality/1.0"
STATUSES = ("alive", "dead", "unmapped")


# ---------------------------------------------------------------------------
# 注册表 → 验证面映射
# ---------------------------------------------------------------------------

def load_registry(registry_path: Path) -> List[Dict[str, Any]]:
    """读取能力注册表（``config/capabilities.json``）；缺文件返回空表（降级不崩）。"""
    p = Path(registry_path)
    if not p.is_file():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    caps = raw.get("capabilities", raw) if isinstance(raw, dict) else raw
    out = []
    for cap in caps:
        cid = cap.get("capability_id") or cap.get("id")
        if not cid:
            continue
        out.append({"capability_id": cid,
                    "test_patterns": list(cap.get("test_patterns") or []),
                    "source": "registry"})
    return out


def discover_units(repo_root: Path, test_dirs: Sequence[str] = ("tests",)) -> List[str]:
    """枚举验证面单位（相对路径，POSIX 风格），跳过 ``__init__.py``/``conftest.py``。"""
    repo_root = Path(repo_root)
    units: List[str] = []
    for d in test_dirs:
        base = repo_root / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.py")):
            if f.name in ("__init__.py", "conftest.py"):
                continue
            units.append(f.relative_to(repo_root).as_posix())
    return units


def build_mapping(units: Sequence[str], registry: Sequence[Mapping[str, Any]],
                  explicit: Optional[Mapping[str, Sequence[str]]] = None) -> Dict[str, List[str]]:
    """``{unit: [capability_id, ...]}``；``explicit``（能力→glob/regex）优先于注册表模式。"""
    mapping: Dict[str, List[str]] = {u: [] for u in units}
    compiled: List[tuple[str, List[re.Pattern]]] = []
    for cap in registry:
        pats = [re.compile(p, re.IGNORECASE) for p in cap.get("test_patterns") or []]
        if pats:
            compiled.append((str(cap["capability_id"]), pats))
    explicit_compiled: List[tuple[str, List[re.Pattern]]] = []
    for cid, pats in (explicit or {}).items():
        explicit_compiled.append((cid, [re.compile(p, re.IGNORECASE) for p in pats]))

    for unit in units:
        owners: List[str] = []
        for cid, pats in explicit_compiled or compiled:
            if any(p.search(unit) for p in pats) and cid not in owners:
                owners.append(cid)
        if explicit_compiled:  # 显式映射存在时，注册表只补未认领的
            for cid, pats in compiled:
                if not owners and any(p.search(unit) for p in pats) and cid not in owners:
                    owners.append(cid)
        mapping[unit] = owners
    return mapping


def git_commit(repo_root: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_root),
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# 巡检主体
# ---------------------------------------------------------------------------

def patrol(repo_root: Path, *,
           test_dirs: Sequence[str] = ("tests",),
           registry_path: Optional[str | Path] = None,
           units: Optional[Sequence[str]] = None,
           explicit_mapping: Optional[Mapping[str, Sequence[str]]] = None,
           extra_roots: Sequence[str | Path] = (),
           run_collect: bool = True,
           chunk_size: int = 8,
           timeout: float = 100.0,
           collect_result: Optional[CollectResult] = None,
           python: str = sys.executable) -> Dict[str, Any]:
    """对 ``repo_root`` 做一次活性巡检并返回可落盘报告（纯函数，不写文件）。

    ``collect_result`` 允许注入（测试/事故重放复用同一次收集），否则真跑 pytest。
    """
    t0 = time.time()
    repo_root = Path(repo_root)
    if registry_path is None:
        registry_path = repo_root / "config" / "capabilities.json"
    registry = load_registry(Path(registry_path))
    unit_list = list(units) if units is not None else discover_units(repo_root, test_dirs)

    extra_roots_py = [Path(r) if Path(r).is_absolute() else repo_root / r for r in extra_roots]

    # 1) 真实收集（一次进程跑全部目标，成本与文件数近似线性）
    if collect_result is None:
        if run_collect and unit_list:
            # 目标是**测试根目录**（tests/ 这样的整体），而不是每个文件的父目录：
            # 单进程跑一次最省时间，也避免把几百个目录当参数重复导入 conftest。
            targets = [d for d in test_dirs if (repo_root / d).is_dir()]
            extra_parents = sorted({str(Path(u).parent) for u in unit_list
                                    if not any(str(Path(u)).startswith(str(d).rstrip("/") + "/")
                                               for d in targets)})
            targets = targets + extra_parents
            collect_result = run_pytest_collect_chunked(repo_root, targets, timeout=timeout,
                                                        chunk_size=chunk_size,
                                                        extra_pythonpath=extra_roots_py,
                                                        python=python)
        else:
            collect_result = CollectResult(exit_code=0)
    collect = collect_result

    # 2) 静态导入解析
    roots = imports_mod.local_roots(repo_root, extra_roots)
    static = imports_mod.scan_tree(repo_root, unit_list, roots=roots, rel_to=repo_root)

    aborted_roots = {str(Path(t).as_posix()).rstrip("/")
                     for abort in getattr(collect, "chunk_aborts", [])
                     for t in abort.get("targets", [])}

    # 3) 合成三态
    mapping = build_mapping(unit_list, registry, explicit_mapping)
    surfaces: List[Dict[str, Any]] = []
    for unit in unit_list:
        caps = mapping.get(unit, [])
        static_findings = static.get(unit, [])
        lethal_static = [f for f in static_findings if f.get("kind") in imports_mod.DEAD_KINDS
                         and not f.get("guarded") and not f.get("type_checking")]
        cerr = collect.error_for(unit)
        dead_reasons: List[str] = []
        if cerr is not None:
            dead_reasons.append(f"collect:{cerr.error_kind}")
        if any(unit == root or unit.startswith(root + "/") for root in aborted_roots):
            dead_reasons.append("collect:session_aborted")
        if lethal_static:
            dead_reasons.append("static:" + ",".join(sorted({str(f["kind"]) for f in lethal_static})))
        status = "dead" if dead_reasons else ("alive" if caps else "unmapped")
        surfaces.append({
            "unit": unit,
            "capability_ids": caps,
            "status": status,
            "registered": bool(caps),
            "collected": collect.collected.get(unit, 0),
            "evidence": {
                "collect": None if cerr is None else {
                    "error_kind": cerr.error_kind, "message": cerr.message},
                "static": static_findings,
            },
            "dead_reasons": dead_reasons,
        })

    # 4) 能力层聚合（注册表 ∪ 台账，字段名不同也能合并）
    capability_index: Dict[str, Dict[str, Any]] = {}
    for cap in registry:
        capability_index[str(cap["capability_id"])] = {"capability_id": cap["capability_id"],
                                                       "sources": ["registry"],
                                                       "surfaces": {"alive": 0, "dead": 0, "unmapped": 0}}
    for s in surfaces:
        for cid in s["capability_ids"]:
            entry = capability_index.setdefault(cid, {"capability_id": cid, "sources": [],
                                                      "surfaces": {"alive": 0, "dead": 0, "unmapped": 0}})
            bucket = s["status"] if s["status"] != "unmapped" else "alive"
            entry["surfaces"][bucket] = entry["surfaces"].get(bucket, 0) + 1
    capabilities: List[Dict[str, Any]] = []
    for cid, entry in sorted(capability_index.items()):
        sf = entry["surfaces"]
        if sf.get("dead", 0) > 0:
            status = "dead"
        elif sf.get("alive", 0) + sf.get("unmapped", 0) > 0:
            status = "alive"
        else:
            status = "unmapped"
        capabilities.append({"capability_id": cid, "status": status,
                             "sources": entry.get("sources", []),
                             "surfaces": sf,
                             "n_surfaces": sum(sf.values())})

    counts = {s: sum(1 for x in surfaces if x["status"] == s) for s in STATUSES}
    cap_counts = {s: sum(1 for c in capabilities if c["status"] == s) for s in STATUSES}
    report = {
        "schema": SCHEMA,
        "repo": {"root": str(repo_root), "git_commit": git_commit(repo_root),
                 "test_dirs": list(test_dirs)},
        "policy": {
            "unit": "test module",
            "states": list(STATUSES),
            "dead_rule": "collect error OR 非可选静态导入不可解析；unmapped=活性无碍但无能力认领",
            "registry": str(registry_path),
        },
        "collect": {
            "exit_code": collect.exit_code,
            "mode": getattr(collect, "mode", "single"),
            "chunk_aborts": getattr(collect, "chunk_aborts", []),
            "collected_total": collect.collected_total,
            "errors": [{"file": e.file, "error_kind": e.error_kind, "message": e.message}
                       for e in collect.errors],
            "timed_out": collect.timed_out,
        },
        "surfaces": surfaces,
        "capabilities": capabilities,
        "summary": {
            "n_units": len(surfaces),
            "alive": counts["alive"], "dead": counts["dead"], "unmapped": counts["unmapped"],
            "dead_ratio": round(counts["dead"] / len(surfaces), 4) if surfaces else 0.0,
            "n_capabilities": len(capabilities),
            "caps_alive": cap_counts["alive"], "caps_dead": cap_counts["dead"],
            "caps_unmapped": cap_counts["unmapped"],
            "elapsed_s": round(time.time() - t0, 2),
        },
    }
    return report


def patrol_summary(report: Mapping[str, Any]) -> Dict[str, Any]:
    """从报告里取验收判据要用的几个数（避免下游手抄）。"""
    s = dict(report.get("summary", {}))
    s["dead_units"] = [x["unit"] for x in report.get("surfaces", []) if x["status"] == "dead"]
    s["dead_capabilities"] = [c["capability_id"] for c in report.get("capabilities", [])
                              if c["status"] == "dead"]
    return s