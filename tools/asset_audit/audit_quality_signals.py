#!/usr/bin/env python3
"""audit_quality_signals —— 测试强度与重复实现两类客观信号（零领域语义）。

S1 测试强度：窗口内新增测试文件里，断言语句按强度分类
    strong : 含比较/容差/精确值/异常断言（==, !=, <, >, approx, raises, <=）
    weak   : 只断言存在性/真值（is not None, isinstance, assert x, len(...) == 0 形式）
输出每个 test 文件的 strong/weak 计数与 weak_ratio；weak_ratio 高的文件标为
"只跑不判"（execution-only）候选。

S2 重复实现：窗口内新增 core 模块 vs 窗口前已存在模块，按函数名集合 Jaccard 比对，
    Jaccard >= 0.6 且模块名不同 → 重复实现候选（附双方文件与共有函数数）。

用法：python tools/asset_audit/audit_quality_signals.py --scan artifact.json --out out.json [--repo-root .]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from itertools import islice
from pathlib import Path

WEAK_PAT = re.compile(
    r"^\s*assert\s+("
    r"[^=!<>]*\bis\s+not\s+None"
    r"|[^=!<>]*\bisinstance\("
    r"|[\w\.\[\]\(\)]+\s*$"
    r"|len\([^)]*\)\s*(==\s*0|>=\s*0)"
    r")",
)
STRONG_PAT = re.compile(r"(==|!=|<=|>=|<|>|approx|pytest\.raises|assert_almost_equal|assert_allclose|raises\()")


def func_names(path: str) -> set[str]:
    try:
        tree = ast.parse(Path(path).read_text(errors="ignore"))
    except (OSError, SyntaxError):
        return set()
    return {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
    }


def test_strength(path: str) -> dict | None:
    try:
        text = Path(path).read_text(errors="ignore")
    except OSError:
        return None
    asserts = [ln for ln in text.splitlines() if ln.strip().startswith("assert ")]
    strong = sum(1 for ln in asserts if STRONG_PAT.search(ln.split("assert ", 1)[1]))
    weak = len(asserts) - strong
    n_tests = len(re.findall(r"^def test_", text, re.M))
    return {
        "path": path,
        "n_tests": n_tests,
        "n_asserts": len(asserts),
        "strong": strong,
        "weak": weak,
        "weak_ratio": round(weak / len(asserts), 3) if asserts else None,
        "asserts_per_test": round(len(asserts) / n_tests, 2) if n_tests else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--max-old", type=int, default=6000)
    args = ap.parse_args()
    scan = json.loads(Path(args.scan).read_text())
    new_core = [f["path"] for f in scan["files"] if f["category"] == "core" and f["path"].endswith(".py")]
    new_tests = [f["path"] for f in scan["files"] if f["category"] == "test" and f["path"].endswith(".py")]
    changed = {f["path"] for f in scan["files"]}

    tests = [r for r in (test_strength(p) for p in new_tests) if r and r["n_tests"]]
    tot_asserts = sum(t["n_asserts"] for t in tests)
    tot_weak = sum(t["weak"] for t in tests)
    tests.sort(key=lambda t: (-(t["weak_ratio"] or 0), -t["n_asserts"]))
    execution_only = [t for t in tests if t["n_asserts"] == 0 and t["n_tests"] > 0]

    # 重复实现：新 core 与"窗口内未出现的既有模块"比函数名集合
    candidates = []
    new_sets = {}
    for p in new_core:
        s = func_names(p)
        if len(s) >= 4:
            new_sets[p] = s
    old_py = []
    for dirpath, dirnames, filenames in islice(
        ((d, dn, fn) for d, dn, fn in _walk(args.repo_root)), 10_000_000
    ):
        for fn in filenames:
            if fn.endswith(".py"):
                rel = str(Path(dirpath, fn).relative_to(args.repo_root))
                if rel not in changed:
                    old_py.append(rel)
    old_py = old_py[: args.max_old]
    for op in old_py:
        os_ = func_names(op)
        if len(os_) < 4:
            continue
        for np_, ns in new_sets.items():
            inter = ns & os_
            if len(inter) < 4:
                continue
            j = len(inter) / len(ns | os_)
            if j >= 0.6:
                candidates.append({"new": np_, "old": op, "jaccard": round(j, 3), "shared": len(inter)})
    candidates.sort(key=lambda c: -c["jaccard"])

    out = {
        "tests": {
            "n_test_files": len(tests),
            "n_asserts": tot_asserts,
            "weak_asserts": tot_weak,
            "weak_ratio": round(tot_weak / tot_asserts, 3) if tot_asserts else None,
            "execution_only_files": [t["path"] for t in execution_only][:50],
            "weakest_files": tests[:15],
        },
        "duplicates": candidates[:60],
        "duplicate_count_ge_0.6": len(candidates),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in out["tests"].items() if k != "weakest_files"}, indent=1))
    print("duplicate candidates:", len(candidates))
    for c in candidates[:6]:
        print("  ", c)
    return 0


def _walk(root: str):
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", "__pycache__", ".venv", "venv", ".recycle", "artifacts"}]
        yield dirpath, dirnames, filenames


if __name__ == "__main__":
    raise SystemExit(main())