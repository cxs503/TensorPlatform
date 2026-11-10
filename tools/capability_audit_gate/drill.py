"""回归防护演练：把「REAL 被降级为 MOCK 时 CI 必红」变成可机器复算的演练证据（C3）。

为什么需要单独演练（而不是只留单元测试）
----------------------------------------
`gate.compare` 的单元测试只能证明"给定两份等级表，函数会算出 REFUTATION"；它证明不了
**整条管线**在真实 spec 上对**每一个** REAL 能力都能检出降级——路径解析、扫描、判定、
基线比对任一环节漏气，单元测试都会绿。本模块在**临时工作树副本**上逐个注入降级：

    对每个当前判定为 REAL 的能力：
      1. 把 target 的实现文件改写成三族独立造假信号（未播种随机 / 硬编码标记 / 常量直返）
      2. 用**未改动树**冻结的基线做 gate 比对
      3. 要求该能力被判为 REFUTATION（或至少 DOWNGRADE）→ 计入检出

    detection_rate = 检出数 / 注入数（要求 1.0，任一漏检 ⇒ 退出码 1）

纪律
----
* **零领域语义**：目标能力、实现路径全部来自 spec（数据），注入源码是通用 Python 片段；
* **不脏工作树**：注入只发生在 ``tempfile`` 副本里，演练前后对 spec 引用的原文件做
  sha256 自校验（``worktree_unchanged``），发现被写坏直接判 FAIL；
* **证据留痕**：每个目标能力的注入文件、注入后等级、理由码、检出判定都写进 drill.json。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path

from . import engine, gate, taxonomy

#: 通用造假实现：三族独立 MOCK 信号（marker / unseeded random / constant return）。
#: 不含任何领域词条——它演示的是"代码形态"，不是"物理算法"。
TAMPER_SOURCE = '''"""drill-injected fake implementation (regression control, not shipped)."""
# mock: hardcoded placeholder
# mock: fake dummy response
import random


def compute(x):
    return 1.0


def derive(x):
    return random.random()
'''

DRILL_SCHEMA = "capability_audit_gate.drill.v1"

#: 全树复制上限（超过则只复制 spec 引用的文件，见 ``_materialize``）
MAX_FILES = 5000
MAX_BYTES = 64 * 1024 * 1024


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _spec_paths(spec: dict) -> list[str]:
    """spec 直接引用的相对路径（绑定证据 + 基准工件）。"""
    out: list[str] = []
    for cap in spec["capabilities"]:
        bindings = cap.get("bindings") or {}
        out.extend(bindings.get("code") or [])
        out.extend(bindings.get("tests") or [])
        bench = cap.get("benchmark") or {}
        if isinstance(bench, dict) and bench.get("path"):
            out.append(bench["path"])
    # 去重保序
    return list(dict.fromkeys(p for p in out if p))


def _tree_size(root: Path) -> tuple[int, int]:
    files = size = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in {".git", "__pycache__", "node_modules", ".pytest_cache"}
               for part in p.parts):
            continue
        files += 1
        size += p.stat().st_size
        if files > MAX_FILES or size > MAX_BYTES:
            break
    return files, size


def _materialize(spec: dict, root: Path, dest: Path) -> dict:
    """把审计所需的树复制到 ``dest``；小树整树复制，大树只复制 spec 引用文件。

    返回 ``{"mode": "full"|"spec_paths", "files": n}``。整树复制的意义是让**模式匹配**
    解析（未显式绑定的能力）在副本里与原地一致，避免"复制导致证据丢失"的假降级。
    """
    files, size = _tree_size(root)
    if files <= MAX_FILES and size <= MAX_BYTES:
        shutil.copytree(root, dest, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__",
                                                      "node_modules", ".pytest_cache"))
        return {"mode": "full", "files": files}
    copied = 0
    for rel in _spec_paths(spec):
        src = root / rel
        if not src.is_file():
            continue
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    return {"mode": "spec_paths", "files": copied}


def _tamper(root: Path, rel_paths: list[str]) -> list[str]:
    """把目标实现文件逐个改写成造假实现，返回实际改写的路径。"""
    touched = []
    for rel in rel_paths:
        p = root / rel
        if not p.is_file():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(TAMPER_SOURCE, encoding="utf-8")
        touched.append(rel)
    return touched


def _levels(report: dict) -> dict:
    return {v["capability"]: v["level"] for v in report["capabilities"]}


def run_drill(spec: dict, root: str | Path = ".", target_level: str = "REAL",
              out_dir: str | Path | None = None) -> dict:
    """对每个 ``target_level`` 能力注入降级并验证 gate 检出，返回演练结果。"""
    t0 = time.perf_counter()
    root = Path(root).resolve()
    baseline_report = engine.run_audit(spec, root=root)
    baseline = gate.baseline_from_report(baseline_report)
    targets = [v["capability"] for v in baseline_report["capabilities"]
               if v["level"] == taxonomy.normalize(target_level)]

    # 工作树自校验：演练前后 spec 引用文件必须逐字节一致
    watched = {rel: _sha256(root / rel) for rel in _spec_paths(spec)}
    impl_bindings = {cap["id"]: list((cap.get("bindings") or {}).get("code") or [])
                     for cap in spec["capabilities"]}

    rows: list[dict] = []
    collateral: list[str] = []
    for cid in targets:
        bound = impl_bindings.get(cid) or []
        with tempfile.TemporaryDirectory(prefix="cag_drill_") as td:
            tree = Path(td) / "root"
            materialized = _materialize(spec, root, tree)
            touched = _tamper(tree, bound)
            if not touched:
                rows.append({"id": cid, "detected": False, "change": "NO_TAMPER_TARGET",
                             "note": "no bound implementation file to tamper",
                             "injected_files": []})
                continue
            report = engine.run_audit(spec, root=tree)
            after = _levels(report)
            cmp = gate.compare(report, baseline)
            detected_rows = {v["id"]: v for v in cmp["violations"] + cmp["acked"]}
            row = detected_rows.get(cid)
            change = row["change"] if row else taxonomy.classify_change(
                baseline["levels"].get(cid, "REAL"), after.get(cid, "ABSENT"))
            detected = bool(row) and change in {"REFUTATION", "DOWNGRADE",
                                                "CAPABILITY_DROPPED"}
            # 非目标能力的等级漂移（整树复制时应为空；subset 模式仅作透明记录）
            moved = sorted(k for k, v in after.items()
                           if k != cid and baseline["levels"].get(k) not in (None, v))
            collateral.extend(f"{cid}->{k}" for k in moved)
            rows.append({
                "id": cid,
                "baseline_level": baseline["levels"].get(cid),
                "injected_level": after.get(cid),
                "change": change,
                "detected": detected,
                "injected_files": touched,
                "reasons": next((v["reasons"] for v in report["capabilities"]
                                 if v["capability"] == cid), []),
                "materialize": materialized,
                "collateral_level_moves": moved,
            })

    detected = sum(1 for r in rows if r["detected"])
    rate = (detected / len(rows)) if rows else None
    unchanged = all(_sha256(root / rel) == sha for rel, sha in watched.items())
    result = {
        "schema_version": DRILL_SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ruleset_version": baseline_report["ruleset_version"],
        "target_level": taxonomy.normalize(target_level),
        "targets": len(rows),
        "detected": detected,
        "missed": [r["id"] for r in rows if not r["detected"]],
        "detection_rate": round(rate, 4) if rate is not None else None,
        "worktree_unchanged": unchanged,
        "collateral_level_moves": sorted(set(collateral)),
        "rows": rows,
        "runtime_sec": round(time.perf_counter() - t0, 3),
        "pass": bool(rows) and detected == len(rows) and unchanged,
    }
    if not rows:
        result["note"] = (f"spec has no capability at level {result['target_level']}: "
                          "nothing to drill (insufficient evidence, not a pass)")
    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "drill.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return result