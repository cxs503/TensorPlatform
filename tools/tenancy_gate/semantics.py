#!/usr/bin/env python3
"""零领域语义自检：用**预注册** deny-list 扫描本包新代码。

为什么平台要做到"零领域语义"
----------------------------
平台是共性机制仓：一旦这里出现具体学科/业务词条（哪个量是"关键量"、哪些算例叫
什么名字），机制就被某个域的假设绑死，别的域复用时会连带继承错误的判据。
所以域内容只能经 ``config`` / API / 工件注入，代码里零命中。

匹配规则（预注册，改前需评审）
------------------------------
* ASCII 词条：按**词边界**匹配（``\\bterm\\b``，忽略大小写）——否则普通英文单词
  里的短子串会被当成命中，把门变成噪音；
* 非 ASCII 词条（中日韩等）：按子串匹配（这些语言没有词边界）；
* deny-list 文件自身与被显式登记为 ``allow`` 的文件不参与扫描（否则自我命中）。

输出可复算：命中按 ``(path, line, term)`` 排序，``digest`` 由内容派生。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .model import digest

__all__ = ["load_denylist", "scan_text", "scan_tree"]

DEFAULT_DENYLIST = Path(__file__).with_name("domain_denylist.json")


def load_denylist(path: str | Path | None = None) -> dict:
    data = json.loads(Path(path or DEFAULT_DENYLIST).read_text(encoding="utf-8"))
    if not data.get("terms"):
        raise ValueError("denylist_empty")
    return data


def _matcher(term: str):
    if all(ord(ch) < 128 for ch in term):
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])", re.IGNORECASE)
        return lambda text: bool(pattern.search(text))
    return lambda text: term in text


def scan_text(text: str, terms) -> list[dict]:
    hits = []
    matchers = [(t, _matcher(t)) for t in terms]
    for lineno, line in enumerate(text.splitlines(), start=1):
        for term, match in matchers:
            if match(line):
                hits.append({"line": lineno, "term": term, "text": line.strip()[:160]})
    return hits


def scan_tree(root: str | Path, denylist: dict | None = None, suffixes=(".py",)) -> dict:
    root = Path(root)
    data = denylist or load_denylist()
    terms = list(data["terms"])
    allow = {str(p) for p in data.get("allow", [])}
    results = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        rel = str(path.relative_to(root.parent))
        if rel in allow or path.name in allow or str(path) in allow:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for hit in scan_text(text, terms):
            results.append({"path": rel, **hit})
    results.sort(key=lambda h: (h["path"], h["line"], h["term"]))
    return {
        "schema": "tenancy_gate/semantics_scan/v1",
        "root": str(root),
        "terms": len(terms),
        "files_scanned": sum(
            1 for p in root.rglob("*") if p.is_file() and p.suffix in suffixes
            and str(p.relative_to(root.parent)) not in allow
        ),
        "hits": results,
        "hit_count": len(results),
        "ok": not results,
        "denylist_digest": digest(sorted(terms)),
        "digest": digest(results),
    }