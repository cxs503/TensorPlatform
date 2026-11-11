#!/usr/bin/env python3
"""repo_asset_scan —— 并入 main 的新增资产分类扫描器（零领域语义）。

用途
----
给定一个 git 范围 (base..head)，把新增/修改文件按「资产类别」分类并给出
行数、占位实现密度、被引用（接入）状态，输出机器可读 JSON + markdown 摘要，
供质量审计（L0-L5 判级）作为客观分母使用。

类别（互斥，按优先级判定）
------------------------
- api         : backend/api/*.py 或含 FastAPI router/APIRouter 声明的源码
- test        : 路径含 tests/ 或文件名 test_*.py / *_test.py
- doc         : *.md / *.rst / docs/ 下的文本
- evidence    : *.json / *.csv / *.yaml|yml 证据工件与数据快照（非代码）
- placeholder : 源码且占位标记密度高（TODO/FIXME/pass 骨架/NotImplementedError/
                return None 桩），或无任何 def/class
- core        : 其余源码（.py），即"可能是真算法"的候选集
- other       : 其他（脚本/配置/二进制）

附加信号（每个 core/placeholder 文件）
------------------------------------
- wired: 仓内是否有其它文件 import 该模块名（或有 __main__ 入口 / 被脚本引用）
- exec_entry: 是否含 if __name__ == "__main__"
- numeric_ops: 数学运算 token 密度（真算 vs 只搬字段的代理指标）

用法
----
python tools/asset_audit/repo_asset_scan.py --base <sha> --head HEAD \
    --out artifacts/asset_scan.json [--markdown out.md] [--root .]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

CODE_EXT = {".py"}
DOC_EXT = {".md", ".rst", ".txt", ".ipynb"}
DATA_EXT = {".json", ".csv", ".tsv", ".yaml", ".yml", ".lock", ".png", ".npz", ".npy", ".parquet"}

STUB_TOKENS = [
    "NotImplementedError",
    "raise NotImplementedError",
    "TODO",
    "FIXME",
    "STUB",
    "placeholder",
    "not_implemented",
]
NUMERIC_TOKENS = re.compile(
    r"np\.(linalg|dot|einsum|mean|sum|sqrt|exp|log|std|trapz|gradient)|math\.\w+\(|"
    r"\*\*|sqrt\(|sin\(|cos\(|exp\(|log\(|integrat|solve\(|linalg|quad|"
    r"\+=|for .+ in range\(",
)


def stub_body_stats(text: str) -> dict:
    """AST 级占位度量：函数体为空壳（仅 pass/.../return None/raise NotImplementedError）。

    与被注释/枚举词表里的 "STUB" 字面量区分开——只有真正无实现的函数体才算占位。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"n_funcs": 0, "n_stub_funcs": 0, "stub_func_ratio": 0.0}
    n_funcs = 0
    n_stub = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            n_funcs += 1
            body = [b for b in node.body if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str))]
            if not body:
                n_stub += 1
                continue
            if len(body) == 1:
                b = body[0]
                if isinstance(b, ast.Pass):
                    n_stub += 1
                elif isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and b.value.value is Ellipsis:
                    n_stub += 1
                elif isinstance(b, ast.Return) and (b.value is None or (isinstance(b.value, ast.Constant) and b.value.value is None)):
                    n_stub += 1
                elif isinstance(b, ast.Raise):
                    exc = b.exc
                    name = getattr(getattr(exc, "func", exc), "id", "")
                    if name == "NotImplementedError":
                        n_stub += 1
    return {"n_funcs": n_funcs, "n_stub_funcs": n_stub,
            "stub_func_ratio": round(n_stub / n_funcs, 3) if n_funcs else 0.0}


def git(root: str, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def numstat(root: str, base: str, head: str) -> list[tuple[int, int, str]]:
    out = git(root, "diff", "--numstat", f"{base}..{head}")
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, d, p = parts
        if a == "-":  # binary
            continue
        rows.append((int(a), int(d), p))
    return rows


def classify(path: str, text: str | None) -> tuple[str, dict]:
    ext = Path(path).suffix.lower()
    name = Path(path).name
    sig: dict = {}
    if path.startswith("backend/api/") or (text and re.search(r"APIRouter\(|@router\.(get|post|put|delete)", text)):
        cat = "api"
    elif "/tests/" in f"/{path}" or name.startswith("test_") or name.endswith("_test.py"):
        cat = "test"
    elif ext in DOC_EXT:
        cat = "doc"
    elif ext in DATA_EXT:
        cat = "evidence"
    elif ext in CODE_EXT:
        n_lines = max(1, len(text.splitlines())) if text else 1
        stub_hits = sum(text.count(t) for t in STUB_TOKENS) if text else 0
        n_defs = len(re.findall(r"^\s*(def|class)\s+\w+", text, re.M)) if text else 0
        num_hits = len(NUMERIC_TOKENS.findall(text)) if text else 0
        sig["stub_density"] = round(stub_hits / n_lines * 100, 2)
        sig["n_defs"] = n_defs
        sig["numeric_density"] = round(num_hits / n_lines * 100, 2)
        sig["exec_entry"] = bool(text and "__name__" in text and "__main__" in text)
        if text:
            sig.update(stub_body_stats(text))
        nf, ns = sig.get("n_funcs", 0), sig.get("n_stub_funcs", 0)
        if name == "__init__.py":
            cat = "reexport"          # 包导出垫片：不算占位，也不算算法
        elif n_defs == 0:
            # 无函数定义：可能是脚本（有可执行语句）或纯声明
            has_stmt = bool(text) and bool(
                re.search(r"^[A-Za-z_][\w\.]*\s*(=|\(|\[)|^if\s|^for\s|^print\(", text, re.M)
            )
            cat = "script" if has_stmt else "placeholder"
        elif nf and ns == nf:
            cat = "placeholder"        # 所有函数都是空壳
        elif sig.get("stub_func_ratio", 0.0) > 0.3:
            cat = "placeholder"        # >30% 函数空壳
        else:
            cat = "core"
    else:
        cat = "other"
    return cat, sig


def build_import_index(root: str) -> dict[str, int]:
    """模块名 -> 仓内被 import 次数（排除自身）。"""
    idx: Counter = Counter()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in {".git", "node_modules", "__pycache__", ".venv", "venv", ".recycle"}
        ]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(fp) > 2_000_000:
                    continue
                txt = Path(fp).read_text(errors="ignore")
            except OSError:
                continue
            for m in re.finditer(r"^\s*(?:from|import)\s+([\w\.]+)", txt, re.M):
                mod = m.group(1)
                idx[mod] += 1
                # 相对导入（from .mesh import X）只给到包内段名，逐段登记
                for seg in mod.split("."):
                    if seg:
                        idx[seg] += 1
    return dict(idx)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", required=True)
    ap.add_argument("--markdown", default="")
    ap.add_argument("--max-read", type=int, default=400_000)
    args = ap.parse_args()

    root = args.root
    rows = numstat(root, args.base, args.head)
    import_idx = build_import_index(root)

    files = []
    agg: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "added": 0, "deleted": 0})
    for added, deleted, path in rows:
        fp = os.path.join(root, path)
        text = None
        if path.lower().endswith(tuple(CODE_EXT | DOC_EXT)) and os.path.exists(fp):
            try:
                if os.path.getsize(fp) <= args.max_read:
                    text = Path(fp).read_text(errors="ignore")
            except OSError:
                text = None
        cat, sig = classify(path, text)
        stem = Path(path).stem
        wired = bool(import_idx.get(stem)) or bool(sig.get("exec_entry"))
        if cat in ("core", "placeholder"):
            # 精确一点：同名 stem 被 import 的次数（排除常见包名噪声）
            sig["import_hits"] = import_idx.get(stem, 0)
            wired = sig["import_hits"] > 0 or bool(sig.get("exec_entry"))
        files.append(
            {
                "path": path,
                "category": cat,
                "added": added,
                "deleted": deleted,
                "wired": wired,
                **sig,
            }
        )
        a = agg[cat]
        a["files"] += 1
        a["added"] += added
        a["deleted"] += deleted

    # 未接入的 core 模块（无 import、无 __main__）
    unwired = [f["path"] for f in files if f["category"] == "core" and not f["wired"]]
    placeholders = [f for f in files if f["category"] == "placeholder"]

    result = {
        "range": {"base": args.base, "head": args.head},
        "totals": {
            "files_changed": len(files),
            "added_lines": sum(f["added"] for f in files),
            "deleted_lines": sum(f["deleted"] for f in files),
        },
        "by_category": {k: v for k, v in sorted(agg.items(), key=lambda kv: -kv[1]["added"])},
        "unwired_core_modules": sorted(unwired),
        "placeholder_files": sorted(placeholders, key=lambda f: -f["added"])[:200],
        "files": files,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1))

    md = [
        f"# asset scan {args.base}..{args.head}",
        "",
        f"- files_changed={len(files)} added={result['totals']['added_lines']} deleted={result['totals']['deleted_lines']}",
        "",
        "| category | files | added | deleted |",
        "|---|---|---|---|",
    ]
    for k, v in result["by_category"].items():
        md.append(f"| {k} | {v['files']} | {v['added']} | {v['deleted']} |")
    md.append("")
    md.append(f"- unwired core modules: {len(unwired)}")
    md.append(f"- placeholder files: {len(placeholders)}")
    if args.markdown:
        Path(args.markdown).write_text("\n".join(md) + "\n")

    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())