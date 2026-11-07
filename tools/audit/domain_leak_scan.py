#!/usr/bin/env python3
"""域词渗漏扫描器（domain-leak scanner）。

背景 / 问题
-----------
判例 #608：平台层（backend/、tools/、tests/）里曾出现「把某个具体领域的词条/
判据/算例硬编码进代码」的渗漏——例如台账机制测试直接断言某领域条目的证据等级、
把某个域的运行 ID 写死在断言里。这类测试一旦该领域工件被清理，测试立刻变红，
且它验证的是领域结论而不是平台机制；领域结论应由承接该领域的验证单负责。

本工具把「域词表」当**数据**（config/domain_word_denylist.json），对给定路径做
有界扫描，输出 JSON 摘要（命中文件/行号/词条），供 CI 与测试复用。

用法
----
    python3 tools/audit/domain_leak_scan.py                     # 扫 denylist 里的 scan_paths
    python3 tools/audit/domain_leak_scan.py --paths tests/x.py  # 扫指定路径
    python3 tools/audit/domain_leak_scan.py --json out.json     # 落盘 JSON 摘要

退出码：0 = 零命中；1 = 有命中；2 = 输入缺失。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DENYLIST = ROOT / "config" / "domain_word_denylist.json"
SNIPPET_MAXLEN = 160


def load_denylist(path: Path | str = DENYLIST) -> dict:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    for key in ("ascii_words", "cjk_words"):
        if not isinstance(doc.get(key), list) or not doc[key]:
            raise ValueError(f"denylist 缺少 {key} 词条列表: {path}")
    return doc


def compile_patterns(denylist: dict) -> list[tuple[str, re.Pattern]]:
    """ASCII 词条用「非标识符字符夹逼」匹配；CJK 词条用子串匹配。"""
    patterns: list[tuple[str, re.Pattern]] = []
    for word in denylist["ascii_words"]:
        patterns.append(
            (word, re.compile(r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])", re.I))
        )
    for word in denylist["cjk_words"]:
        patterns.append((word, re.compile(re.escape(word))))
    return patterns


def scan_text(text: str, patterns: list[tuple[str, re.Pattern]]) -> list[dict]:
    hits: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for word, rx in patterns:
            for m in rx.finditer(line):
                start = max(0, m.start() - 40)
                hits.append(
                    {
                        "word": word,
                        "line": lineno,
                        "col": m.start() + 1,
                        "match": m.group(0),
                        "snippet": line[start:start + SNIPPET_MAXLEN].strip(),
                    }
                )
    return hits


def _resolve(paths, root: Path) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.is_absolute():
            p = root / p
        out.append(p)
    return out


def iter_files(paths, denylist: dict, root: Path = ROOT):
    suffixes = {s.lower() for s in denylist.get("text_suffixes", [".py", ".json", ".md"])}
    exempt = {str(e) for e in denylist.get("exempt", [])}
    for target in _resolve(paths, root):
        if target.is_file():
            candidates = [target]
        elif target.is_dir():
            candidates = sorted(p for p in target.rglob("*") if p.is_file())
        else:
            continue
        for p in candidates:
            if p.suffix.lower() not in suffixes:
                continue
            try:
                rel = str(p.resolve().relative_to(root.resolve()))
            except ValueError:
                rel = str(p)
            if rel in exempt or p.name in exempt:
                continue
            yield p, rel


def scan(paths=None, denylist_path: Path | str = DENYLIST, root: Path = ROOT) -> dict:
    denylist = load_denylist(denylist_path)
    patterns = compile_patterns(denylist)
    targets = list(paths) if paths else list(denylist["scan_paths"])
    files_scanned, skipped, hits = [], [], []
    for p, rel in iter_files(targets, denylist, root=root):
        if not p.exists():
            skipped.append(rel)
            continue
        files_scanned.append(rel)
        for hit in scan_text(p.read_text(encoding="utf-8", errors="replace"), patterns):
            hits.append({"path": rel, **hit})
    return {
        "schema_version": "1.0",
        "denylist": str(denylist_path).replace(str(root) + "/", ""),
        "targets": [str(t) for t in targets],
        "files_scanned": files_scanned,
        "files_missing": skipped,
        "word_count": len(denylist["ascii_words"]) + len(denylist["cjk_words"]),
        "hits": hits,
        "hit_count": len(hits),
        "ok": not hits,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="域词渗漏扫描（判例 #608）")
    ap.add_argument("--paths", nargs="*", default=None, help="待扫路径（缺省用 denylist.scan_paths）")
    ap.add_argument("--denylist", default=str(DENYLIST))
    ap.add_argument("--json", dest="json_out", default=None, help="JSON 摘要落盘路径")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    try:
        result = scan(args.paths, args.denylist)
    except (OSError, ValueError) as exc:  # 输入缺失
        print(f"无法加载 denylist: {exc}", file=sys.stderr)
        return 2

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        print(
            f"扫描 {len(result['files_scanned'])} 个文件 / {result['word_count']} 个域词条 → "
            f"命中 {result['hit_count']}"
        )
        for hit in result["hits"]:
            print(f"  {hit['path']}:{hit['line']}  [{hit['word']}]  {hit['snippet']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())