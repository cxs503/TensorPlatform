#!/usr/bin/env python3
"""P02-2 规划产物机器验收：8 项结构检查，输出 verify_report.json（零手抄）。

用法: python3 docs/skills_training/P02-2/verify.py
判据: 全部 PASS 方为合格；任一 FAIL 即规划不合格。
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOC = HERE.parent / "P02-2.md"
CONTRACT = HERE / "contract"

results = []


def check(cid, name, ok, detail):
    results.append({"id": cid, "check": name, "pass": bool(ok), "detail": detail})
    return ok


def main():
    doc = DOC.read_text(encoding="utf-8")

    # C1: openapi.yaml 可解析
    import yaml
    try:
        spec = yaml.safe_load((CONTRACT / "openapi.yaml").read_text(encoding="utf-8"))
        ok = check("C1", "openapi.yaml 可解析", True, f"openapi={spec.get('openapi')}")
    except Exception as e:  # noqa: BLE001
        spec = None
        ok = check("C1", "openapi.yaml 可解析", False, f"parse error: {e}")

    # C2: 3 个操作均有 2xx 响应 schema
    if ok and spec:
        ops = []
        for p, item in spec.get("paths", {}).items():
            for method in ("get", "post", "put", "delete"):
                if method in item:
                    ops.append((method.upper(), p, item[method]))
        bad = []
        for m, p, op in ops:
            resp = op.get("responses", {})
            has_2xx = any(
                k.startswith("2") and "content" in v for k, v in resp.items()
            )
            if not has_2xx:
                bad.append(f"{m} {p}")
        check(
            "C2",
            "3 个操作均有 2xx 响应 schema",
            len(ops) == 3 and not bad,
            f"ops={len(ops)}/3, missing_2xx={bad or '无'}",
        )
        # 收集 OpenAPI 引用的非 2xx 错误码（供 C8）
        err_codes = set()
        for m, p, op in ops:
            for k in op.get("responses", {}):
                if not k.startswith("2"):
                    err_codes.add(int(k))
    else:
        check("C2", "3 个操作均有 2xx 响应 schema", False, "依赖 C1 失败")
        err_codes = set()

    # C3: storage.sql 在 sqlite3 内存库执行成功
    try:
        conn = sqlite3.connect(":memory:")
        conn.executescript((CONTRACT / "storage.sql").read_text(encoding="utf-8"))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        check("C3", "storage.sql 可执行", "certification_exports" in tables,
              f"tables={tables}")
    except Exception as e:  # noqa: BLE001
        check("C3", "storage.sql 可执行", False, f"exec error: {e}")

    # C4: 4 个 WP × 7 字段齐全
    wp_sections = re.split(r"\n### ", doc)
    wp_blocks = {}
    for blk in wp_sections:
        m = re.match(r"(WP\d)\s", blk)
        if m:
            wp_blocks[m.group(1)] = blk
    fields = ["目标", "输入", "输出", "owner", "验收", "依赖", "风险"]
    missing = []
    for wp in ("WP1", "WP2", "WP3", "WP4"):
        blk = wp_blocks.get(wp, "")
        for f in fields:
            if not re.search(rf"^\|\s*{f}\s*\|", blk, flags=re.M):
                missing.append(f"{wp}.{f}")
    check("C4", "4 个 WP × 7 字段齐全",
          len(wp_blocks) == 4 and not missing,
          f"wp_found={sorted(wp_blocks)}, missing={missing or '无'}")

    # C5: 依赖图无环（从 mermaid 块解析边）
    mermaid = re.search(r"```mermaid\n(.*?)```", doc, flags=re.S)
    edges = set()
    if mermaid:
        for line in mermaid.group(1).splitlines():
            mm = re.findall(r"(WP\w+)\s*(?:\[[^\]]*\])?\s*-->\s*(WP\w+)", line)
            edges.update(mm)
    nodes = sorted({n for e in edges for n in e})
    indeg = {n: 0 for n in nodes}
    adj = {n: [] for n in nodes}
    for u, v in edges:
        adj[u].append(v)
        indeg[v] += 1
    from collections import deque
    q = deque(n for n in nodes if indeg[n] == 0)
    seen = 0
    while q:
        n = q.popleft()
        seen += 1
        for w in adj[n]:
            indeg[w] -= 1
            if indeg[w] == 0:
                q.append(w)
    check("C5", "依赖图无环", seen == len(nodes),
          f"nodes={len(nodes)}, topo_visited={seen}, edges={len(edges)}")

    # C6: 关键路径复算（从 §5.2 CPM 表解析工期，最长路径法）
    durs = {}
    for m in re.finditer(r"^\|\s*(WP\w+)\s*\|\s*([\d.]+)\s*\|", doc, flags=re.M):
        durs[m.group(1)] = float(m.group(2))
    ef = {}

    def longest(n):
        if n in ef:
            return ef[n]
        preds = [u for u, v in edges if v == n]
        ef[n] = durs.get(n, 0.0) + (max((longest(u) for u in preds), default=0.0))
        return ef[n]

    for n in nodes:
        longest(n)
    cp_total = max(ef.values()) if ef else 0.0
    claim = re.search(r"总工期\s*\*\*([\d.]+)\s*人日", doc)
    claimed = float(claim.group(1)) if claim else None
    check("C6", "关键路径复算一致",
          claimed is not None and abs(cp_total - claimed) < 1e-9,
          f"computed={cp_total:.1f}d, doc_claim={claimed}, "
          f"critical={' -> '.join(critical_path(edges, ef))}")

    # C7: 交叉引用闭合（图边 ↔ WP 依赖列表，WP4a/WP4b 归并到 WP4）
    def parent(n):
        return "WP4" if n.startswith("WP4") else n

    dep_cells = {}
    for wp, blk in wp_blocks.items():
        m = re.search(r"^\|\s*依赖\s*\|\s*(.*?)\s*\|", blk, flags=re.M)
        cell = m.group(1) if m else ""
        dep_cells[wp] = set(re.findall(r"WP\d", cell))
    problems = []
    for u, v in edges:
        if parent(u) == parent(v):
            continue  # 包内顺序，豁免
        if parent(u) not in dep_cells.get(parent(v), set()):
            problems.append(f"edge {u}->{v} 未在 {parent(v)} 依赖列表")
    for wp, deps in dep_cells.items():
        for d in deps:
            if not any(parent(u) == d and parent(v) == wp for u, v in edges):
                problems.append(f"{wp} 依赖 {d} 无对应图边")
    check("C7", "交叉引用闭合", not problems, f"problems={problems or '无'}")

    # C8: 错误码表覆盖 OpenAPI 引用的全部非 2xx 码
    ec = (CONTRACT / "error_codes.md").read_text(encoding="utf-8")
    ec_codes = set(int(x) for x in re.findall(r"^\|\s*(\d{3})\s*\|", ec, flags=re.M))
    uncovered = sorted(err_codes - ec_codes)
    check("C8", "错误码表覆盖", not uncovered,
          f"openapi_codes={sorted(err_codes)}, uncovered={uncovered or '无'}")

    report = {
        "task": "P02-2 certification export endpoint planning",
        "checks": results,
        "passed": sum(1 for r in results if r["pass"]),
        "total": len(results),
        "verdict": "PASS" if all(r["pass"] for r in results) else "FAIL",
    }
    out = HERE / "verify_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


def critical_path(edges, ef):
    """回溯最长路径（从 EF 最大的节点沿最大前驱回溯）。"""
    end = max(ef, key=ef.get)
    path = [end]
    cur = end
    while True:
        preds = [u for u, v in edges if v == cur]
        if not preds:
            break
        cur = max(preds, key=lambda u: ef[u])
        path.append(cur)
    return list(reversed(path))


if __name__ == "__main__":
    sys.exit(main())
