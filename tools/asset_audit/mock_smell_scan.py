#!/usr/bin/env python3
"""mock_smell_scan —— 新增核心代码的"MOCK 风险画像"（零领域语义、只做结构统计）。

不做判级，只给证据密度，供审计抽样定位：
- rand:   random/np.random 使用次数（随机数冒充计算结果的头号信号）
- fake:   fake/dummy/mock/demo/placeholder/stub 字面量（自承）
- canned: 返回字面量常量 dict/list 的 return 语句数（查表冒充计算）
- clock:  time.time()/datetime.now() 参与数值（时间戳冒充度量）
- sleep:  显式 sleep（把"计算耗时"演出来）
- print_report: 是否有自证式 __main__ demo
- asserts_in_module: 模块内 assert（含自检强度代理）

用法：python tools/asset_audit/mock_smell_scan.py --scan artifacts/asset_scan.json --out out.json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

SELF_ADMIT = re.compile(r"\b(fake|dummy|mock|demo|placeholder|stub|hardcode[d]?|pretend|fabricat)\w*", re.I)


def analyze(path: str) -> dict | None:
    try:
        text = Path(path).read_text(errors="ignore")
    except OSError:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"path": path, "syntax_error": True}
    canned = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, (ast.Dict, ast.List, ast.Constant)):
            v = node.value
            if isinstance(v, ast.Constant) and isinstance(v.value, (int, float, str)) and v.value not in (None, True, False):
                canned += 1
            elif not isinstance(v, ast.Constant):
                canned += 1
    n_lines = max(1, len(text.splitlines()))
    return {
        "path": path,
        "lines": n_lines,
        "rand": len(re.findall(r"\brandom\.|np\.random|default_rng", text)),
        "self_admit": len(SELF_ADMIT.findall(text)),
        "canned_returns": canned,
        "clock": len(re.findall(r"time\.time\(|datetime\.now\(|time\.monotonic\(", text)),
        "sleep": len(re.findall(r"\btime\.sleep\(", text)),
        "has_main_demo": "__main__" in text,
        "asserts": len(re.findall(r"^\s*assert ", text, re.M)),
        "rand_per_100l": round(100 * len(re.findall(r"\brandom\.|np\.random|default_rng", text)) / n_lines, 3),
        "canned_per_100l": round(100 * canned / n_lines, 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-lines", type=int, default=80)
    args = ap.parse_args()
    scan = json.loads(Path(args.scan).read_text())
    cands = [
        f["path"]
        for f in scan["files"]
        if f["category"] == "core" and f["path"].endswith(".py") and f["added"] >= args.min_lines
    ]
    rows = [r for r in (analyze(p) for p in cands) if r and not r.get("syntax_error")]
    rows.sort(key=lambda r: (r["rand_per_100l"] + r["canned_per_100l"]), reverse=True)
    agg = {
        "n_files": len(rows),
        "lines": sum(r["lines"] for r in rows),
        "with_random": sum(1 for r in rows if r["rand"] > 0),
        "with_self_admit": sum(1 for r in rows if r["self_admit"] > 0),
        "with_clock": sum(1 for r in rows if r["clock"] > 0),
        "with_sleep": sum(1 for r in rows if r["sleep"] > 0),
        "with_main_demo": sum(1 for r in rows if r["has_main_demo"]),
        "with_asserts": sum(1 for r in rows if r["asserts"] > 0),
        "canned_returns_total": sum(r["canned_returns"] for r in rows),
    }
    out = {"aggregate": agg, "top_smell": rows[:40], "rows": rows}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(agg, indent=1))
    print("--- top smell ---")
    for r in rows[:10]:
        print(f"  rand={r['rand_per_100l']:6.2f} canned={r['canned_per_100l']:5.2f} admit={r['self_admit']:3d} {r['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())