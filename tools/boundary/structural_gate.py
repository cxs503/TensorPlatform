#!/usr/bin/env python3
"""边界门 v1.1 = 基础门禁（IN17-B1 / m5016）+ 结构性判据（P20-N08 / E4）。

基础门禁 ``tools/boundary/pr_gate_scan.py`` 只做「词表 × 词边界」匹配，明文命中率
好但对抗形态（同义改写/符号化/跨语言/遮蔽/长程引用）会漏。本模块叠加三条
**结构性**判据，判据的领域内容全部外置在 ``data/boundary/structural_rules.json``：

* ``R1_unmask``    遮蔽还原：NFKC / 去格式字符 / 百分号解码 / 同形字折叠 / 数字替换
                   + 跨相邻行断词拼接（拼接命中必须跨过拼接边界）
* ``R2_notation``  记号形态：方形系数、L/B、Re_τ、主尺度三元组、主尺度 dict 字面量
* ``R3_reference`` 引用解析：路径 + 节号锚 → 读被引用工件 → 若其携带阻断词条则判引用越界

设计纪律
--------
1. 结构性判据**只对基础门禁未命中的行**生效（已命中的行不重复计数），因此
   不会因为遮蔽还原把明文误报面放大。
2. 本文件不含任何域词条：词条来自 lexicon，记号形状/折叠表来自 rules 数据。
3. 判决 JSON 与基础门禁同 schema 字段（``verdict``/``violations``/``totals``），
   新增 ``structural`` 段留痕，便于 CI 直接替换命令行。

用法
----
    git diff -U0 "$BASE" "$HEAD" | python3 tools/boundary/structural_gate.py diff -
    python3 tools/boundary/structural_gate.py diff - --rules data/boundary/structural_rules.json --json out.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote

if __package__ in (None, ""):  # 允许直接跑
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.boundary import pr_gate_scan as gate  # type: ignore
else:  # pragma: no cover
    from . import pr_gate_scan as gate  # type: ignore

SCHEMA = "tp.boundary.pr_gate_scan_structural/v1"
RULES_SCHEMA = "tp.boundary.structural_rules/v1"
DEFAULT_RULES_REL = "data/boundary/structural_rules.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
_RE_SCANNED = ("blocked", "blocked_whitelist_code")


# --------------------------------------------------------------------------- #
# 规则数据
# --------------------------------------------------------------------------- #
@dataclass
class StructuralRules:
    path: str
    rule_ids: Tuple[str, ...]
    homoglyph: Dict[str, str]
    leet: Dict[str, str]
    strip_categories: Tuple[str, ...]
    percent_decode: bool
    nfkc: bool
    join_enabled: bool
    join_max_chars: int
    notation: List[Tuple[str, "re.Pattern[str]"]] = field(default_factory=list)
    cooc_window: int = 0
    cooc_min_total: int = 2
    cooc_min_distinctive: int = 1
    cooc_keys: List[Tuple[str, bool, "re.Pattern[str]"]] = field(default_factory=list)
    anchor_rx: Optional["re.Pattern[str]"] = None
    path_rx: Optional["re.Pattern[str]"] = None
    max_target_bytes: int = 200000

    @classmethod
    def load(cls, path: Path) -> "StructuralRules":
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != RULES_SCHEMA:
            raise ValueError(f"rules schema mismatch: {data.get('schema')!r} != {RULES_SCHEMA!r}")
        by_id = {r["id"]: r for r in data.get("rules", [])}
        unmask = by_id["R1_unmask"]["fold"]
        join = by_id["R1_unmask"].get("join_adjacent_lines") or {}
        notation = [
            (p["id"], re.compile(p["regex"]))
            for r in data.get("rules", []) if r.get("kind") == "notation" and r.get("enabled", True)
            for p in (r.get("patterns") or [])
        ]
        cooc = next((r for r in data.get("rules", []) if r.get("kind") == "key_cooccurrence"
                     and r.get("enabled", True)), None)
        cooc_keys: List[Tuple[str, bool, "re.Pattern[str]"]] = []
        if cooc:
            shape = cooc.get("value_shape") or "[=:]"
            for key in cooc.get("distinctive_keys") or []:
                cooc_keys.append((key, True, re.compile(
                    rf"(?<![0-9A-Za-z]){re.escape(key)}(?![0-9A-Za-z]){shape}", re.IGNORECASE)))
            for key in cooc.get("generic_keys") or []:
                cooc_keys.append((key, False, re.compile(
                    rf"(?<![0-9A-Za-z]){re.escape(key)}(?![0-9A-Za-z]){shape}", re.IGNORECASE)))
        ref = by_id["R3_reference"]
        return cls(
            path=str(path),
            rule_ids=tuple(r["id"] for r in data.get("rules", []) if r.get("enabled", True)),
            homoglyph=dict(unmask.get("homoglyph") or {}),
            leet=dict(unmask.get("leet") or {}),
            strip_categories=tuple(unmask.get("strip_categories") or ()),
            percent_decode=bool(unmask.get("percent_decode")),
            nfkc=bool(unmask.get("nfkc")),
            join_enabled=bool(join.get("enabled")),
            join_max_chars=int(join.get("max_joined_chars", 400)),
            notation=notation,
            cooc_window=int((cooc or {}).get("window_lines", 0)),
            cooc_min_total=int((cooc or {}).get("min_total_keys", 2)),
            cooc_min_distinctive=int((cooc or {}).get("min_distinctive_keys", 1)),
            cooc_keys=cooc_keys,
            anchor_rx=re.compile(ref["anchor_regex"]),
            path_rx=re.compile(ref["path_regex"]),
            max_target_bytes=int(ref.get("max_target_bytes", 200000)),
        )

    def describe(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "enabled_rules": list(self.rule_ids),
            "notation_patterns": [pid for pid, _ in self.notation],
            "key_cooccurrence": {
                "window_lines": self.cooc_window,
                "min_total_keys": self.cooc_min_total,
                "min_distinctive_keys": self.cooc_min_distinctive,
                "keys": [[k, d] for k, d, _ in self.cooc_keys],
            },
        }


# --------------------------------------------------------------------------- #
# 结构算子
# --------------------------------------------------------------------------- #
def strip_format(text: str, categories: Sequence[str]) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) not in set(categories))


def unmask(text: str, rules: StructuralRules) -> str:
    """遮蔽还原（顺序固定：兼容归一 → 去格式 → 百分号解码 → 同形字 → 数字）。"""

    out = text
    if rules.nfkc:
        out = unicodedata.normalize("NFKC", out)
    if rules.strip_categories:
        out = strip_format(out, rules.strip_categories)
    if rules.percent_decode:
        out = unquote(out)
    if rules.homoglyph:
        out = "".join(rules.homoglyph.get(ch, ch) for ch in out)
    if rules.leet:
        out = "".join(rules.leet.get(ch, ch) for ch in out)
    return out


def _blocking_hits(text: str, lexicon: gate.Lexicon) -> List[Any]:
    return [h for h in gate.scan_line(text, lexicon) if h.blocking and not h.exempt_reason]


def _is_alnum(ch: str) -> bool:
    return ch.isalnum()


def _resolve_ref(raw: str, repo_root: Path) -> Optional[Path]:
    raw = raw.strip().strip("\"'`()[]<>,;:")
    if raw.startswith("./"):
        raw = raw[2:]
    if raw.startswith("/") or ".." in Path(raw).parts:
        return None
    path = (repo_root / raw).resolve()
    try:
        path.relative_to(repo_root.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def scan_diff_structural(
    diff_text: str,
    lexicon: gate.Lexicon,
    rules: StructuralRules,
    *,
    repo_root: Path = REPO_ROOT,
    mode: str = "diff",
    target: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """基础门禁 + 结构性判据，返回扩展判决 JSON。"""

    base = gate.scan_diff(diff_text, lexicon, mode=mode, target=target)
    report = base.to_json()
    caught = {(v["file"], v["line"]) for v in base.violations}
    flagged = set(caught)
    structural: List[Dict[str, Any]] = []
    considered = skipped = 0

    added = [
        (path, lineno, content)
        for path, lineno, content in gate.parse_added_lines(diff_text)
        if lexicon.classify(path) in _RE_SCANNED
    ]
    by_file: Dict[str, List[Tuple[int, str]]] = {}
    for path, lineno, content in added:
        by_file.setdefault(path, []).append((lineno, content))

    for path, lineno, content in added:
        considered += 1
        if (path, lineno) in caught:
            skipped += 1
            continue

        # R1-a 单行遮蔽还原
        folded = unmask(content, rules)
        if folded != content:
            hits = _blocking_hits(folded, lexicon)
            if hits:
                structural.append({
                    "file": path, "line": lineno, "rule": "R1_unmask",
                    "term_id": hits[0].term_id, "matched": hits[0].matched,
                    "snippet": gate._snippet(content), "evidence": gate._snippet(folded),
                })
                flagged.add((path, lineno))
                continue

        # R2 记号形态
        notation_hit = None
        for pid, rx in rules.notation:
            m = rx.search(content)
            if m:
                notation_hit = {"pattern_id": pid, "matched": m.group(0)}
                break
        if notation_hit:
            structural.append({
                "file": path, "line": lineno, "rule": "R2_notation",
                "term_id": notation_hit["pattern_id"], "matched": notation_hit["matched"],
                "snippet": gate._snippet(content),
            })
            flagged.add((path, lineno))
            continue

        # R3 引用解析
        if rules.anchor_rx and rules.path_rx and rules.anchor_rx.search(content):
            ref_hit = None
            for m in rules.path_rx.finditer(content):
                target_path = _resolve_ref(m.group(0), Path(repo_root))
                if target_path is None:
                    continue
                if any(normalize_rel(target_path, Path(repo_root)).startswith(p)
                       for p in lexicon.self_exempt_paths):
                    continue
                try:
                    raw = target_path.read_text(encoding="utf-8", errors="replace")
                except OSError:  # pragma: no cover - 防御
                    continue
                if len(raw) > rules.max_target_bytes:
                    raw = raw[: rules.max_target_bytes]
                for raw_line in raw.splitlines():
                    hits = _blocking_hits(raw_line, lexicon)
                    if hits:
                        ref_hit = {
                            "target": normalize_rel(target_path, Path(repo_root)),
                            "term_id": hits[0].term_id, "matched": hits[0].matched,
                        }
                        break
                if ref_hit:
                    break
            if ref_hit:
                structural.append({
                    "file": path, "line": lineno, "rule": "R3_reference",
                    "term_id": ref_hit["term_id"], "matched": ref_hit["term_id"],
                    "snippet": gate._snippet(content),
                    "target": ref_hit["target"], "target_matched": ref_hit["matched"],
                })
                flagged.add((path, lineno))
                continue

    # R2b 窗口内键共现（表格/代码块里的逐行域量）
    cooc_hits = 0
    if rules.cooc_window > 0 and rules.cooc_keys:
        for path, items in by_file.items():
            i = 0
            while i < len(items):
                window = items[i:i + rules.cooc_window]
                found: Dict[str, Tuple[int, bool]] = {}
                for ln, text in window:
                    for key, distinctive, rx in rules.cooc_keys:
                        if key in found:
                            continue
                        if rx.search(text):
                            found[key] = (ln, distinctive)
                distinctive_n = sum(1 for _, (_, d) in found.items() if d)
                if (len(found) >= rules.cooc_min_total
                        and distinctive_n >= rules.cooc_min_distinctive):
                    anchor = min(found.items(), key=lambda kv: kv[1][0])
                    ln = anchor[1][0]
                    if (path, ln) not in flagged:
                        structural.append({
                            "file": path, "line": ln, "rule": "R2b_key_cooccurrence",
                            "term_id": "geometry_key_closure",
                            "matched": "+".join(sorted(found)),
                            "snippet": gate._snippet(next(t for l2, t in window if l2 == ln)),
                            "keys": sorted(found),
                        })
                        flagged.add((path, ln))
                        cooc_hits += 1
                    i += len(window)
                else:
                    i += 1

    # R1-b 跨相邻行断词
    joined_hits = 0
    if rules.join_enabled:
        for path, items in by_file.items():
            for (ln_a, a), (ln_b, b) in zip(items, items[1:]):
                if (path, ln_a) in caught or (path, ln_b) in caught:
                    continue
                sa, sb = a.rstrip(), b.lstrip()
                if not sa or not sb or not _is_alnum(sa[-1]) or not _is_alnum(sb[0]):
                    continue
                if len(sa) + len(sb) > rules.join_max_chars:
                    continue
                joined = sa + sb
                for hit in _blocking_hits(joined, lexicon):
                    if hit.start < len(sa) < hit.end:  # 命中必须跨过拼接边界
                        structural.append({
                            "file": path, "line": ln_a, "rule": "R1_unmask",
                            "term_id": hit.term_id, "matched": hit.matched,
                            "snippet": gate._snippet(f"{sa}{sb}"),
                            "evidence": "cross-line join",
                        })
                        flagged.add((path, ln_a))
                        joined_hits += 1
                        break

    violations = list(report["violations"]) + structural
    report.update({
        "schema": SCHEMA,
        "verdict": "FAIL" if violations else "PASS",
        "exit_code": 2 if violations else 0,
        "structural": {
            "rules": rules.describe(),
            "lines_considered": considered,
            "lines_skipped_already_caught": skipped,
            "violations": structural,
            "cross_line_joins": joined_hits,
            "key_cooccurrence_blocks": cooc_hits,
        },
        "violations": violations,
        "totals": dict(report["totals"], violations=len(violations),
                       base_violations=report["totals"]["violations"],
                       structural_violations=len(structural)),
    })
    return report


def normalize_rel(path: Path, root: Path) -> str:
    try:
        return gate.normalize_path(str(path.resolve().relative_to(root.resolve())))
    except ValueError:  # pragma: no cover - 防御
        return str(path)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="边界门 v1.1（基础 + 结构性判据）")
    ap.add_argument("mode", choices=["diff"])
    ap.add_argument("source", help="- 表示从 stdin 读统一 diff")
    ap.add_argument("--lexicon", default=str(REPO_ROOT / gate.DEFAULT_LEXICON_REL))
    ap.add_argument("--rules", default=str(REPO_ROOT / DEFAULT_RULES_REL))
    ap.add_argument("--json", default=None)
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    args = ap.parse_args(argv)

    try:
        lexicon = gate.Lexicon.load(Path(args.lexicon))
        rules = StructuralRules.load(Path(args.rules))
    except (OSError, ValueError) as exc:  # pragma: no cover - 防御
        print(f"internal error: {exc}", file=sys.stderr)
        return 3

    diff_text = sys.stdin.read() if args.source == "-" else Path(args.source).read_text(encoding="utf-8")
    report = scan_diff_structural(diff_text, lexicon, rules, repo_root=Path(args.repo_root))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        t = report["totals"]
        print(f"{report['verdict']}: base={t['base_violations']} structural={t['structural_violations']} "
              f"lines={t['lines_scanned']} files_scanned={t['files_scanned']}")
        for v in report["violations"][:20]:
            print(f"  {v['file']}:{v['line']} [{v.get('rule', 'base')}/{v['term_id']}] {v['snippet']}")
    return report["exit_code"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())