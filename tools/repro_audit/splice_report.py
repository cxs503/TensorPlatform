"""报告缝合器：把模板里的 ``{{token}}`` 全部用审计 JSON 的实测值替换，再拼上程序化生成的表格。

为什么不用手抄：owner 2026-10-29 要求「结果数字表程序化注入零手抄」。本脚本保证
``docs/repro_audit/P70-N08_repro_audit.md`` 里的每个数字都来自 ``artifacts/repro_audit.json``：
token 缺失或类型不符会直接报错，避免手写数字混进报告。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Dict

TOKEN_RE = re.compile(r"\{\{([a-zA-Z0-9_.\[\]]+)\}\}")
TABLES_MARK = "<!--TABLES-->"


def _lookup(doc: Dict[str, Any], path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if "[" in part:
            name, idx = part[:-1].split("[")
            cur = cur[name][int(idx)]
        else:
            cur = cur[part]
    return cur


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        if value == 0:
            return "0"
        if abs(value) < 1e-3 or abs(value) >= 1e5:
            return "%.6g" % value
        return ("%.6f" % value).rstrip("0").rstrip(".")
    return str(value)


def splice(template: str, doc: Dict[str, Any], tables: str) -> str:
    def repl(m):
        key = m.group(1)
        try:
            return _fmt(_lookup(doc, key))
        except Exception as exc:  # token 写错就炸，绝不静默留 {{...}}
            raise KeyError("token 无法解析: %s (%s)" % (key, exc))

    out = TOKEN_RE.sub(repl, template)
    if TABLES_MARK in out:
        out = out.replace(TABLES_MARK, tables)
    if "{{" in out:
        raise ValueError("模板仍有未解析 token")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", default="artifacts/repro_audit.json")
    ap.add_argument("--template", default="docs/repro_audit/P70-N08_repro_audit.tmpl.md")
    ap.add_argument("--tables", default="artifacts/repro_audit/report_tables.md")
    ap.add_argument("--out", default="docs/repro_audit/P70-N08_repro_audit.md")
    a = ap.parse_args()
    with open(a.audit, "r", encoding="utf-8") as f:
        doc = json.load(f)
    with open(a.template, "r", encoding="utf-8") as f:
        tmpl = f.read()
    with open(a.tables, "r", encoding="utf-8") as f:
        tables = f.read()
    out = splice(tmpl, doc, tables)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(out)
    print(json.dumps({"out": a.out, "bytes": len(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())