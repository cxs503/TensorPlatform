#!/usr/bin/env python3
"""PR 边界门扫描器：把「PR 加行里的域词」判成合规/越界（SPEC §3 落机器）。

为什么需要它
------------
平台是 domain-neutral 的运行底座；领域语义应该长在领域仓里，平台仓里
``backend/`` ``tools/`` ``tests/`` 等代码路径出现领域词条，通常意味着"领域功能
正在回流平台"。历史 PR 里这类回流是靠人看 review 抓的，漏一个就固化一次。
本工具把它变成机器判决：**逐 PR 增量行扫描 + JSON 判决（文件+行号）**，
可以被 CI 动作或 pre-merge 钩子直接消费。

零领域语义约定（铁律）
----------------------
* **代码里不出现任何域词条**：词表、弱词表、豁免复合名、路径域全部来自
  ``data/boundary/pr_boundary_lexicon.json``（可由 ``--lexicon`` 换）；
* 词表文件本身位于 ``data/``，不属于扫描域，因此词表可以自由扩充而不会自伤；
* 单元测试用**合成词表 + 合成 diff**（虚构词条），真实词表只用于"对历史 PR
  复判"的校准用例，校准结论落 ``artifacts/``。

匹配语义（词边界）
------------------
1. ``word``      —— 词边界匹配：前后不得紧跟字母/数字（连字符与下划线视为边界，
   因此 ``<term>_part`` / ``<term>_other`` 这类标识符会被命中，而 ``<term>x`` 不会）。
2. ``substring`` —— 无词边界的语言（中文等）按子串匹配。
3. ``dash_prefix`` —— 词条以 ``-`` 结尾表示"连字符复合名前缀"（形如 ``xxx-*``）。
   命中后取出**整个复合名**，若该复合名在 ``exempt_compounds`` 里则豁免
   （专有名词=对外部实体的引用，不是领域语义）；豁免按整名精确匹配，
   因此 ``<stem>-<real-domain-noun>`` 这类真领域复合名仍判红。

路径语义
--------
* ``blocked_prefixes``     代码扫描域：命中即越界。
* ``observation_prefixes`` 观察数据白名单：**仅对** ``observation_extensions``
  （.md/.json/.csv/...）生效；代码扩展名落在白名单目录内仍按代码扫描
  —— 即"白名单路径伪造域代码"必须判红。
* 仓根散件（路径无 ``/``）：代码扩展名→代码域，观察数据扩展名→观察数据。
* 其余路径 out_of_scope（不判、计入统计；已知覆盖局限，见 docs/boundary/PR_BOUNDARY_GATE.md §6）。

判决
----
JSON（``schema: tp.boundary.pr_gate_scan/v1``）：``verdict`` ∈ {PASS, FAIL}、
``violations[]``（file/line/column/term_id/matched/kind/snippet）、``advisories[]``
（弱域词，不阻断，供 review 参考）、``exempted[]``（被豁免的专有名词命中，
留痕可审计）、``files[]``（每文件分类）、``lexicon``（路径+sha256，审计链）。

用法
----
    # 1) 复判一个已并入的 PR（两提交之间的净变更）
    python3 tools/boundary/pr_gate_scan.py git --base <base> --head <head>

    # 2) 由统一 diff 判（CI 里管道喂进来；-U0 或任意上下文均可）
    git diff -U0 origin/main...HEAD | python3 tools/boundary/pr_gate_scan.py diff -

    # 3) 本地按文件判（pre-merge 钩子/开发自检）
    python3 tools/boundary/pr_gate_scan.py paths tools/foo/bar.py tests/test_x.py

退出码：0=PASS，2=FAIL（越界），3=内部错误（词表缺失/参数错）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = "tp.boundary.pr_gate_scan/v1"
LEXICON_SCHEMA = "tp.boundary.pr_gate_lexicon/v1"
DEFAULT_LEXICON_REL = "data/boundary/pr_boundary_lexicon.json"
SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]

_ALNUM = "0-9A-Za-z"
_COMPOUND_CHARS = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-")
_SNIPPET_MAX = 160


# --------------------------------------------------------------------------- #
# 词表
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Term:
    """一个词条：id + 匹配种类 + 可取值的若干写法 + 是否阻断 + 备注。"""

    term_id: str
    kind: str  # word | substring | dash_prefix
    values: Tuple[str, ...]
    blocking: bool
    gloss: str = ""

    def patterns(self) -> List[Tuple[str, "re.Pattern[str]"]]:
        out: List[Tuple[str, "re.Pattern[str]"]] = []
        for value in self.values:
            if self.kind == "word":
                rx = rf"(?<![{_ALNUM}]){re.escape(value)}(?![{_ALNUM}])"
            elif self.kind == "dash_prefix":
                rx = rf"(?<![{_ALNUM}]){re.escape(value)}(?=[{_ALNUM}_])"
            elif self.kind == "substring":
                rx = re.escape(value)
            else:  # pragma: no cover - 词表校验会先拦下
                raise ValueError(f"unknown term kind: {self.kind}")
            out.append((value, re.compile(rx, re.IGNORECASE)))
        return out


@dataclass
class Lexicon:
    path: str
    sha256: str
    version: str
    terms: List[Term]
    advisory_terms: List[Term]
    exempt_compounds: Tuple[str, ...]
    blocked_prefixes: Tuple[str, ...]
    observation_prefixes: Tuple[str, ...]
    observation_extensions: Tuple[str, ...]
    self_exempt_paths: Tuple[str, ...]

    # -- 构造 ---------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "Lexicon":
        raw_bytes = path.read_bytes()
        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover - 防御
            raise ValueError(f"lexicon is not valid JSON: {exc}") from exc
        if data.get("schema") != LEXICON_SCHEMA:
            raise ValueError(
                f"lexicon schema mismatch: expected {LEXICON_SCHEMA!r}, got {data.get('schema')!r}"
            )
        scopes = data.get("scopes") or {}

        def _terms(key: str, blocking: bool) -> List[Term]:
            out: List[Term] = []
            for item in data.get(key) or []:
                values = tuple(str(v) for v in (item.get("values") or [item.get("value")]))
                if not values or values == ("None",):
                    raise ValueError(f"term {item.get('id')!r} has no values")
                kind = str(item.get("kind", "word"))
                out.append(
                    Term(
                        term_id=str(item.get("id") or values[0]),
                        kind=kind,
                        values=values,
                        blocking=blocking,
                        gloss=str(item.get("gloss", "")),
                    )
                )
            return out

        return cls(
            path=str(path),
            sha256=hashlib.sha256(raw_bytes).hexdigest(),
            version=str(data.get("version", "unknown")),
            terms=_terms("terms", True),
            advisory_terms=_terms("advisory_terms", False),
            exempt_compounds=tuple(str(x).lower() for x in (data.get("exempt_compounds") or [])),
            blocked_prefixes=tuple(scopes.get("blocked_prefixes") or []),
            observation_prefixes=tuple(scopes.get("observation_prefixes") or []),
            observation_extensions=tuple(
                e.lower() for e in (scopes.get("observation_extensions") or [])
            ),
            self_exempt_paths=tuple(scopes.get("self_exempt_paths") or []),
        )

    # -- 路径分类 -----------------------------------------------------------
    def classify(self, path: str) -> str:
        """返回 observation / blocked / blocked_whitelist_code / self_exempt / out_of_scope。"""

        p = normalize_path(path)
        if any(p.startswith(pre) for pre in self.self_exempt_paths):
            return "self_exempt"
        ext = Path(p).suffix.lower()
        if any(p.startswith(pre) for pre in self.observation_prefixes):
            if ext in self.observation_extensions:
                return "observation"
            return "blocked_whitelist_code"
        if any(p.startswith(pre) for pre in self.blocked_prefixes):
            return "blocked"
        if "/" not in p:
            # 仓根散件：代码（含无扩展名的 Makefile/Dockerfile 之类）也算代码域，
            # 仓根文档（README/CHANGELOG 等 .md）按观察数据处理。
            return "blocked" if ext not in self.observation_extensions else "observation"
        return "out_of_scope"

    def describe(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "version": self.version,
            "term_count": len(self.terms),
            "advisory_term_count": len(self.advisory_terms),
            "exempt_compound_count": len(self.exempt_compounds),
            "blocked_prefixes": list(self.blocked_prefixes),
            "observation_prefixes": list(self.observation_prefixes),
            "observation_extensions": list(self.observation_extensions),
        }


def normalize_path(path: str) -> str:
    p = path.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


# --------------------------------------------------------------------------- #
# 单行扫描
# --------------------------------------------------------------------------- #
@dataclass
class Hit:
    term_id: str
    kind: str
    matched: str
    start: int
    end: int
    blocking: bool
    compound: str = ""
    exempt_reason: str = ""

    @property
    def length(self) -> int:
        return self.end - self.start


def _compound_at(line: str, start: int, end: int) -> str:
    """取出命中所在的整复合名（连字符/下划线连接的标识符）。"""
    left = start
    while left > 0 and line[left - 1] in _COMPOUND_CHARS:
        left -= 1
    right = end
    while right < len(line) and line[right] in _COMPOUND_CHARS:
        right += 1
    return line[left:right]


def scan_line(line: str, lexicon: Lexicon) -> List[Hit]:
    """扫一行，返回去重后的命中（长命中优先，阻断优先）。"""

    candidates: List[Hit] = []
    for term in list(lexicon.terms) + list(lexicon.advisory_terms):
        for _value, rx in term.patterns():
            for m in rx.finditer(line):
                start, end = m.start(), m.end()
                if start == end:  # pragma: no cover - 防御空匹配
                    continue
                compound = _compound_at(line, start, end)
                hit = Hit(
                    term_id=term.term_id,
                    kind=term.kind,
                    matched=m.group(0),
                    start=start,
                    end=end,
                    blocking=term.blocking,
                    compound=compound,
                )
                if compound and compound.lower() in lexicon.exempt_compounds:
                    hit.exempt_reason = "exempt_compound"
                candidates.append(hit)

    # 重叠去重：阻断优先，其次长命中优先
    candidates.sort(key=lambda h: (not h.blocking, -h.length, h.start))
    kept: List[Hit] = []
    for hit in candidates:
        if any(not (hit.end <= k.start or hit.start >= k.end) for k in kept):
            continue
        kept.append(hit)
    return sorted(kept, key=lambda h: h.start)


def _snippet(line: str) -> str:
    s = line.strip()
    return s if len(s) <= _SNIPPET_MAX else s[: _SNIPPET_MAX - 1] + "…"


# --------------------------------------------------------------------------- #
# diff 解析
# --------------------------------------------------------------------------- #
_HUNK_RX = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_added_lines(diff_text: str) -> List[Tuple[str, int, str]]:
    """从统一 diff 里取 (路径, 新文件行号, 行内容)，只取新增行。

    只取 ``+`` 行：边界门判的是"PR 加进来的东西"，删掉的域词是治理成果，
    不该判红（校准用例 #610 的 docs 行与 #608 的 tests 行都是新增行，不受影响）。
    """

    added: List[Tuple[str, int, str]] = []
    current: Optional[str] = None
    new_lineno = 0
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            current, new_lineno = None, 0
            continue
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            if target.startswith("b/"):
                target = target[2:]
            current = None if target == "/dev/null" else normalize_path(target)
            continue
        if raw.startswith("--- "):
            continue
        if raw.startswith("@@"):
            m = _HUNK_RX.match(raw)
            if m:
                new_lineno = int(m.group(1))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            if current is not None:
                added.append((current, new_lineno, raw[1:]))
            new_lineno += 1
            continue
        if raw.startswith("-") and not raw.startswith("---"):
            continue
        if raw.startswith(" "):
            new_lineno += 1
            continue
        # "\\ No newline at end of file" / "Binary files ... differ" 等忽略
    return added


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #
@dataclass
class ScanResult:
    mode: str
    lexicon: Lexicon
    violations: List[Dict[str, Any]] = field(default_factory=list)
    advisories: List[Dict[str, Any]] = field(default_factory=list)
    exempted: List[Dict[str, Any]] = field(default_factory=list)
    files: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    target: Dict[str, Any] = field(default_factory=dict)
    lines_seen: int = 0

    @property
    def verdict(self) -> str:
        return "FAIL" if self.violations else "PASS"

    @property
    def exit_code(self) -> int:
        return 2 if self.violations else 0

    def _file_entry(self, path: str) -> Dict[str, Any]:
        return self.files.setdefault(
            path, {"path": path, "classification": self.lexicon.classify(path), "hits": 0}
        )

    def add(self, path: str, lineno: int, line: str) -> None:
        self.lines_seen += 1
        entry = self._file_entry(path)
        for hit in scan_line(line, self.lexicon):
            record = {
                "file": path,
                "line": lineno,
                "column": hit.start + 1,
                "term_id": hit.term_id,
                "kind": hit.kind,
                "matched": hit.matched,
                "snippet": _snippet(line),
            }
            if hit.exempt_reason:
                record["exempt_reason"] = hit.exempt_reason
                record["compound"] = hit.compound
                self.exempted.append(record)
                entry["hits"] += 1
                continue
            if not hit.blocking:
                self.advisories.append(record)
                continue
            self.violations.append(record)
            entry["hits"] += 1

    def to_json(self) -> Dict[str, Any]:
        scanned = [f for f in self.files.values() if f["classification"] in
                   ("blocked", "blocked_whitelist_code")]
        return {
            "schema": SCHEMA,
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "mode": self.mode,
            "target": self.target,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "lexicon": self.lexicon.describe(),
            "totals": {
                "lines_scanned": self.lines_seen,
                "files_touched": len(self.files),
                "files_scanned": len(scanned),
                "files_whitelisted": sum(
                    1 for f in self.files.values() if f["classification"] == "observation"
                ),
                "files_out_of_scope": sum(
                    1 for f in self.files.values() if f["classification"] == "out_of_scope"
                ),
                "files_self_exempt": sum(
                    1 for f in self.files.values() if f["classification"] == "self_exempt"
                ),
                "violations": len(self.violations),
                "advisories": len(self.advisories),
                "exempted": len(self.exempted),
            },
            "violations": self.violations,
            "advisories": self.advisories,
            "exempted": self.exempted,
            "files": sorted(self.files.values(), key=lambda f: f["path"]),
        }


def scan_diff(
    diff_text: str,
    lexicon: Lexicon,
    *,
    mode: str = "diff",
    target: Optional[Dict[str, Any]] = None,
) -> ScanResult:
    result = ScanResult(mode=mode, lexicon=lexicon, target=target or {})
    for path, lineno, content in parse_added_lines(diff_text):
        classification = lexicon.classify(path)
        result._file_entry(path)
        if classification in ("observation", "out_of_scope", "self_exempt"):
            continue
        result.add(path, lineno, content)
    return result


def scan_files(
    paths: Sequence[Path],
    lexicon: Lexicon,
    *,
    root: Optional[Path] = None,
    target: Optional[Dict[str, Any]] = None,
) -> ScanResult:
    """按整文件扫描（本地自检 / pre-merge 钩子用）；路径按 root 归一化为仓内相对路径。"""

    result = ScanResult(mode="paths", lexicon=lexicon, target=target or {})
    root = root or SCRIPT_REPO_ROOT
    for raw in paths:
        p = Path(raw)
        try:
            rel = normalize_path(str(p.resolve().relative_to(root.resolve())))
        except ValueError:
            rel = normalize_path(str(p))
        classification = lexicon.classify(rel)
        result._file_entry(rel)
        if classification in ("observation", "out_of_scope", "self_exempt"):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:  # pragma: no cover - 防御
            result.files[rel]["error"] = str(exc)
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            result.add(rel, i, line)
    return result


# --------------------------------------------------------------------------- #
# git / CLI
# --------------------------------------------------------------------------- #
def git_diff(base: str, head: str, repo: Path, *, unified: int = 0) -> str:
    proc = subprocess.run(
        [
            "git", "-C", str(repo), "-c", "core.quotepath=false",
            "diff", f"-U{unified}", "--no-color", "--find-renames", base, head, "--",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def resolve_lexicon(cli_path: Optional[str], repo_root: Path) -> Lexicon:
    if cli_path:
        path = Path(cli_path)
        if not path.is_absolute():
            candidate = repo_root / path
            path = candidate if candidate.exists() else path
    else:
        path = repo_root / DEFAULT_LEXICON_REL
        if not path.exists():
            path = SCRIPT_REPO_ROOT / DEFAULT_LEXICON_REL
    if not path.exists():
        raise FileNotFoundError(f"lexicon not found: {path}")
    return Lexicon.load(path)


def _render_text(report: Dict[str, Any]) -> str:
    lines = [
        f"PR boundary gate: {report['verdict']}  (mode={report['mode']})",
        f"  lexicon {report['lexicon']['path']} v{report['lexicon']['version']} "
        f"sha256={report['lexicon']['sha256'][:12]}…  terms={report['lexicon']['term_count']}",
        "  totals: " + ", ".join(f"{k}={v}" for k, v in report["totals"].items()),
    ]
    for v in report["violations"]:
        lines.append(
            f"  VIOLATION {v['file']}:{v['line']}:{v['column']} "
            f"term={v['term_id']} matched={v['matched']!r} :: {v['snippet']}"
        )
    for a in report["advisories"]:
        lines.append(f"  advisory  {a['file']}:{a['line']} term={a['term_id']}")
    for e in report["exempted"]:
        lines.append(f"  exempted  {e['file']}:{e['line']} compound={e['compound']!r}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="PR 边界门：扫描 PR 加行里的域词，输出 JSON 判决（文件+行号）。",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--lexicon", help=f"词表路径（默认 {DEFAULT_LEXICON_REL}）")
        sp.add_argument("--json", dest="json_out", help="判决 JSON 落盘路径")
        sp.add_argument("--format", choices=("json", "text"), default="json",
                        help="stdout 输出格式（默认 json）")
        sp.add_argument("--repo-root", default=None, help="仓根（默认按脚本位置推断）")

    d = sub.add_parser("diff", help="由统一 diff 判定（缺省从 stdin 读，'-' 同义）")
    d.add_argument("diff_file", nargs="?", default="-", help="diff 文件路径，'-'=stdin")
    common(d)

    g = sub.add_parser("git", help="由两个提交之间的净变更判定（复判已并 PR）")
    g.add_argument("--base", required=True, help="base 提交（PR 的第一父提交）")
    g.add_argument("--head", required=True, help="head 提交（PR 合并提交/分支头）")
    g.add_argument("--repo", default=".", help="git 仓路径")
    common(g)

    s = sub.add_parser("paths", help="按整文件判定（pre-merge 钩子/本地自检）")
    s.add_argument("paths", nargs="+", help="待判定文件路径")
    common(s)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else SCRIPT_REPO_ROOT
    try:
        lexicon = resolve_lexicon(args.lexicon, repo_root)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    try:
        if args.cmd == "diff":
            if args.diff_file in ("-", None):
                diff_text = sys.stdin.read()
                target = {"source": "stdin"}
            else:
                diff_text = Path(args.diff_file).read_text(encoding="utf-8", errors="replace")
                target = {"source": str(args.diff_file)}
            result = scan_diff(diff_text, lexicon, mode="diff", target=target)
        elif args.cmd == "git":
            repo = Path(args.repo).resolve()
            diff_text = git_diff(args.base, args.head, repo)
            mode = "git-worktree" if subprocess.run(
                ["git", "-C", str(repo), "diff", "--quiet", args.head, "--"],
                capture_output=True).returncode else "git"
            result = scan_diff(
                diff_text, lexicon, mode="git",
                target={"base": args.base, "head": args.head, "repo": str(repo),
                        "worktree_dirty": mode == "git-worktree"},
            )
        else:
            result = scan_files([Path(x) for x in args.paths], lexicon, root=repo_root,
                                target={"paths": list(args.paths)})
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    report = result.to_json()
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    print(_render_text(report) if args.format == "text" else payload)
    return report["exit_code"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())