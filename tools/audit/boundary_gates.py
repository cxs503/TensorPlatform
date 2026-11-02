#!/usr/bin/env python3
"""Domain boundary gates for the TensorPlatform core surface (G1/G2/G3).

TensorPlatform (TP) is a *domain-neutral* agent-runtime platform.  Ship-domain
payloads (resistance / propulsion / seakeeping / manoeuvring / ice loads ...)
are being migrated out of TP into the ``ship`` body.  "Migration done" is only
credible when it is *machine-checkable*, so this script turns the migration
contract into three gates:

G1  import boundary gate
    AST-parse the core surface and flag any import of a ship-domain module
    (``ship*``, ``polar``, ``holtrop``, ``resistance``, ``propeller``,
    ``seakeep*``, ``manoeuvr*``, ``maneuver``, ``ice_uq``).

G2  endpoint vocabulary gate
    Pull the live OpenAPI document from the local API (default :8010) and count
    endpoint paths whose URL carries ship-domain vocabulary
    (ship|holtrop|wigley|kvlcc|suboff|seakeep|manoeuv|maneuver|propeller|
    hydrostatic|ice-uq|ice-load|polar).  Frozen baseline is recorded; the gate
    is expected to ratchet down to 0 after migration.

G3  constant hoisting gate
    Text-scan the core surface for hard-coded hydrodynamics constants
    (WATER_DENSITY, KINEMATIC_VISCOSITY, KVLCC, SUBOFF, DTMB).  Physics data
    belongs to the domain body, not the platform.

Mode
----
Non-blocking baseline mode is the default: the script always exits 0, writes a
JSON baseline (violation list + counts per gate) and a markdown snapshot
report.  ``--enforce`` flips it into a blocking gate (exit 2 when any gate has
violations or cannot be evaluated), to be switched on once migration finished.

Usage
-----
    python tools/audit/boundary_gates.py            # baseline report, exit 0
    python tools/audit/boundary_gates.py --enforce  # blocking gate, exit 2
    python tools/audit/boundary_gates.py --openapi-file /tmp/oa.json  # offline

Exit codes: 0 = ok (baseline mode always), 2 = gate red in ``--enforce`` mode,
3 = internal error.
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import json
import re
import sys
import tokenize
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

SCHEMA = "boundary_gates/v1"

# ---------------------------------------------------------------------------
# Gate surface definitions
# ---------------------------------------------------------------------------

#: Individual core-surface files (paths relative to the repository root).
CORE_SURFACE_FILES: tuple[str, ...] = (
    "backend/main.py",
    "backend/api/domain/tensor.py",
    "backend/api/domain/iam.py",
    "backend/api/domain/platform.py",
    "backend/api/domain/observability.py",
    "backend/api/domain/data.py",
    "backend/api/domain/model.py",
    "backend/api/domain/deployment.py",
    "backend/api/domain/ops.py",
    "backend/api/domain/tools.py",
    "backend/api/domain/misc.py",
    "backend/services/benchmark_canon.py",
)

#: Directories whose ``*.py`` files are part of the core surface.
CORE_SURFACE_DIRS: tuple[str, ...] = ("tools/audit",)

#: Ship-domain vocabulary used by the G1 import gate.
SHIP_IMPORT_TOKENS: tuple[str, ...] = (
    "ship",
    "polar",
    "holtrop",
    "resistance",
    "propeller",
    "seakeep",
    "manoeuvr",
    "maneuver",
    "ice_uq",
)

#: Endpoint URL vocabulary used by the G2 vocabulary gate.
SHIP_ENDPOINT_PATTERN = (
    r"ship|holtrop|wigley|kvlcc|suboff|seakeep|manoeuv|maneuver|propeller"
    r"|hydrostatic|ice-uq|ice-load|polar"
)

#: Hydrodynamics constants that must not live in the platform core (G3).
HOIST_CONSTANTS: tuple[str, ...] = (
    "WATER_DENSITY",
    "KINEMATIC_VISCOSITY",
    "KVLCC",
    "SUBOFF",
    "DTMB",
)

#: Baseline recorded in the G1-G3 gate spec (audit report §7, 2026-11-01).
SPEC_STATED_G2_BASELINE = 7

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _token_matches(segment: str, token: str) -> bool:
    """Boundary-aware match of a ship-domain ``token`` inside a module name.

    Segments are split on non-alphanumerics *and* camelCase is not split (module
    names are snake_case in this repo).  A token matches when a word equals the
    token or starts with it, so ``manoeuvr`` catches ``manoeuvring`` while
    ``ship`` does **not** fire on ``sqlalchemy.orm.relationship``.
    """
    seg = segment.lower()
    tok = token.lower()
    words = [w for w in re.split(r"[^a-z0-9]+", seg) if w]
    words.append(seg)
    for word in words:
        if word == tok or word.startswith(tok):
            return True
    return False


def _ship_import_hit(module: str, names: Sequence[str]) -> Optional[tuple[str, str]]:
    """Return ``(matched_segment, matched_token)`` for a module+names spec."""
    candidates: List[str] = []
    if module:
        candidates.append(module)
        # ``from pkg import sub`` may resolve to ``pkg.sub``.
        for name in names:
            candidates.append(f"{module}.{name}")
    else:
        candidates.extend(names)
    for candidate in candidates:
        for segment in str(candidate).split("."):
            for token in SHIP_IMPORT_TOKENS:
                if _token_matches(segment, token):
                    return segment, token
    return None


def collect_surface(
    repo_root: Path, extra_files: Iterable[str] = ()
) -> tuple[List[Path], List[str], List[str]]:
    """Resolve the core surface to concrete ``*.py`` files under ``repo_root``."""
    files: List[Path] = []
    missing: List[str] = []
    seen: set[str] = set()

    candidates = list(CORE_SURFACE_FILES) + list(extra_files)
    for rel in candidates:
        path = repo_root / rel
        if path.is_file():
            if rel not in seen:
                seen.add(rel)
                files.append(path)
        else:
            missing.append(rel)

    for rel_dir in CORE_SURFACE_DIRS:
        directory = repo_root / rel_dir
        if not directory.is_dir():
            missing.append(rel_dir + "/")
            continue
        for path in sorted(directory.rglob("*.py")):
            rel = path.relative_to(repo_root).as_posix()
            if rel not in seen:
                seen.add(rel)
                files.append(path)
    return files, missing, sorted(seen)


def _self_path() -> Path:
    return Path(__file__).resolve()


# ---------------------------------------------------------------------------
# G1 — import boundary gate
# ---------------------------------------------------------------------------

def gate_g1_imports(
    repo_root: Path, extra_files: Iterable[str] = ()
) -> Dict[str, Any]:
    files, missing, surface_rel = collect_surface(repo_root, extra_files)
    violations: List[Dict[str, Any]] = []
    parse_errors: List[Dict[str, Any]] = []
    self_file = _self_path()
    scanned = 0

    for path in files:
        if path.resolve() == self_file:
            # A linter does not lint its own vocabulary table.
            continue
        scanned += 1
        rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:  # pragma: no cover - defensive
            parse_errors.append({"file": rel, "error": str(exc)})
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    hit = _ship_import_hit(alias.name, ())
                    if hit:
                        violations.append(
                            {
                                "file": rel,
                                "line": node.lineno,
                                "kind": "import",
                                "module": alias.name,
                                "matched_segment": hit[0],
                                "matched_token": hit[1],
                                "source": ast.unparse(node),
                            }
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [alias.name for alias in node.names]
                # ``from . import ship_x`` inside a package resolves via module='';
                # the alias names themselves are then the candidates.
                hit = _ship_import_hit(module, names)
                if hit:
                    violations.append(
                        {
                            "file": rel,
                            "line": node.lineno,
                            "kind": "from-import",
                            "module": module,
                            "names": names,
                            "matched_segment": hit[0],
                            "matched_token": hit[1],
                            "source": ast.unparse(node),
                        }
                    )

    violations.sort(key=lambda v: (v["file"], v["line"]))
    return {
        "id": "G1_import_boundary",
        "title": "core surface must not import ship-domain modules",
        "files_scanned": scanned,
        "surface": surface_rel,
        "missing_surface": missing,
        "tokens": list(SHIP_IMPORT_TOKENS),
        "count": len(violations),
        "status": "red" if violations else "green",
        "violations": violations,
        "parse_errors": parse_errors,
    }


# ---------------------------------------------------------------------------
# G2 — endpoint vocabulary gate
# ---------------------------------------------------------------------------

def load_openapi(
    api_base: str, openapi_file: Optional[str] = None, timeout: float = 8.0
) -> Dict[str, Any]:
    """Load an OpenAPI document from disk or over HTTP (never raises)."""
    if openapi_file:
        path = Path(openapi_file)
        try:
            return {
                "status": "ok",
                "source": str(path),
                "doc": json.loads(path.read_text(encoding="utf-8")),
                "error": None,
            }
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "status": "withheld",
                "source": str(path),
                "doc": None,
                "error": f"openapi file unreadable: {exc}",
            }

    url = api_base.rstrip("/") + "/openapi.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
        return {"status": "ok", "source": url, "doc": payload, "error": None}
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "withheld",
            "source": url,
            "doc": None,
            "error": f"openapi unavailable: {exc}",
        }


def gate_g2_endpoints(
    api_base: str, openapi_file: Optional[str] = None, timeout: float = 8.0
) -> Dict[str, Any]:
    loaded = load_openapi(api_base, openapi_file, timeout)
    pattern = re.compile(SHIP_ENDPOINT_PATTERN, re.IGNORECASE)
    endpoints: List[Dict[str, Any]] = []
    total_paths = 0

    if loaded["status"] == "ok":
        paths = loaded["doc"].get("paths") or {}
        total_paths = len(paths)
        for path in sorted(paths):
            if not pattern.search(path):
                continue
            operations = paths[path] or {}
            methods = [m.upper() for m in operations if m.lower() in HTTP_METHODS]
            endpoints.append(
                {
                    "path": path,
                    "methods": sorted(methods),
                    "matched_token": pattern.search(path).group(0).lower(),
                }
            )

    count = sum(max(1, len(e["methods"])) for e in endpoints)
    if loaded["status"] != "ok":
        return {
            "id": "G2_endpoint_vocabulary",
            "title": "live API must not expose ship-domain endpoint vocabulary",
            "source": loaded["source"],
            "status": "withheld",
            "count": None,
            "path_count": len(endpoints),
            "total_openapi_paths": total_paths,
            "spec_stated_baseline": SPEC_STATED_G2_BASELINE,
            "pattern": SHIP_ENDPOINT_PATTERN,
            "endpoints": endpoints,
            "violations": [],
            "error": loaded["error"],
        }

    return {
        "id": "G2_endpoint_vocabulary",
        "title": "live API must not expose ship-domain endpoint vocabulary",
        "source": loaded["source"],
        "status": "red" if endpoints else "green",
        "count": count,
        "path_count": len(endpoints),
        "total_openapi_paths": total_paths,
        "spec_stated_baseline": SPEC_STATED_G2_BASELINE,
        "pattern": SHIP_ENDPOINT_PATTERN,
        "endpoints": endpoints,
        "violations": [
            {"file": e["path"], "line": 0, "matched_token": e["matched_token"]}
            for e in endpoints
        ],
        "error": None,
    }


def _literal_spans(path: Path) -> tuple[List[tuple[int, int, int]], List[tuple[int, int, int]]]:
    """Return ``(comment_spans, string_spans)`` as ``(line, col_start, col_end)``.

    Used to triage G3 hits: a benchmark id inside a comment or docstring is a
    documentation duty, a bare constant inside executable code is the real
    domain leak.  Tokenisation failures degrade to "no spans" (everything is
    classified as code) instead of dropping the hit.
    """
    comments: List[tuple[int, int, int]] = []
    strings: List[tuple[int, int, int]] = []
    try:
        with path.open("rb") as handle:
            for tok in tokenize.tokenize(handle.readline):
                if tok.type == tokenize.COMMENT:
                    comments.append((tok.start[0], tok.start[1], tok.end[1]))
                elif tok.type == tokenize.STRING:
                    strings.append((tok.start[0], tok.start[1], tok.end[1]))
    except (tokenize.TokenError, SyntaxError, OSError):  # pragma: no cover
        pass
    return comments, strings


def _hit_context(line: int, col: int, comments, strings) -> str:
    for start_line, start_col, _end_col in comments:
        if start_line == line and col >= start_col:
            return "comment"
    for start_line, start_col, end_col in strings:
        if start_line == line and start_col <= col < max(end_col, start_col + 1):
            return "string"
    return "code"


# ---------------------------------------------------------------------------
# G3 — constant hoisting gate
# ---------------------------------------------------------------------------

def gate_g3_constants(
    repo_root: Path, extra_files: Iterable[str] = ()
) -> Dict[str, Any]:
    files, missing, surface_rel = collect_surface(repo_root, extra_files)
    # No trailing \b: bare ``grep`` semantics so ``KVLCC2``/``SUBOFF_...``
    # still count as hits of the ``KVLCC``/``SUBOFF`` families.
    pattern = re.compile(r"\b(" + "|".join(HOIST_CONSTANTS) + r")")
    violations: List[Dict[str, Any]] = []
    self_file = _self_path()
    scanned = 0

    for path in files:
        if path.resolve() == self_file:
            continue
        scanned += 1
        rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
        comments, strings = _literal_spans(path)
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            for match in pattern.finditer(line):
                violations.append(
                    {
                        "file": rel,
                        "line": lineno,
                        "constant": match.group(1),
                        "context": _hit_context(lineno, match.start(), comments, strings),
                        "text": line.strip()[:200],
                    }
                )

    violations.sort(key=lambda v: (v["file"], v["line"], v["constant"]))
    by_constant: Dict[str, int] = {}
    by_context: Dict[str, int] = {}
    for v in violations:
        by_constant[v["constant"]] = by_constant.get(v["constant"], 0) + 1
        by_context[v["context"]] = by_context.get(v["context"], 0) + 1
    return {
        "id": "G3_constant_hoisting",
        "title": "core surface must not hard-code hydrodynamics constants",
        "files_scanned": scanned,
        "surface": surface_rel,
        "missing_surface": missing,
        "constants": list(HOIST_CONSTANTS),
        "count": len(violations),
        "by_constant": by_constant,
        "by_context": by_context,
        "status": "red" if violations else "green",
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_gates(
    repo_root: Path,
    api_base: str = "http://127.0.0.1:8010",
    openapi_file: Optional[str] = None,
    only: Optional[Sequence[str]] = None,
    timeout: float = 8.0,
) -> Dict[str, Any]:
    wanted = {g.upper() for g in (only or ("G1", "G2", "G3"))}
    gates: Dict[str, Dict[str, Any]] = {}
    if "G1" in wanted:
        gates["G1_import_boundary"] = gate_g1_imports(repo_root)
    if "G2" in wanted:
        gates["G2_endpoint_vocabulary"] = gate_g2_endpoints(
            api_base, openapi_file, timeout
        )
    if "G3" in wanted:
        gates["G3_constant_hoisting"] = gate_g3_constants(repo_root)

    red = [g for g in gates.values() if g["status"] == "red"]
    withheld = [g for g in gates.values() if g["status"] == "withheld"]
    total = sum(int(g["count"] or 0) for g in gates.values())
    return {
        "schema": SCHEMA,
        "generated_at": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "repo_root": str(repo_root),
        "gate_spec": {
            "import_tokens": list(SHIP_IMPORT_TOKENS),
            "endpoint_pattern": SHIP_ENDPOINT_PATTERN,
            "hoist_constants": list(HOIST_CONSTANTS),
            "core_surface_files": list(CORE_SURFACE_FILES),
            "core_surface_dirs": list(CORE_SURFACE_DIRS),
            "self_excluded": "tools/audit/boundary_gates.py",
        },
        "gates": gates,
        "totals": {
            "violations": total,
            "gates": len(gates),
            "gates_red": len(red),
            "gates_green": sum(1 for g in gates.values() if g["status"] == "green"),
            "gates_withheld": len(withheld),
        },
    }


def _md_escape(text: str) -> str:
    return str(text).replace("|", "\\|")[:160]


def render_markdown(result: Dict[str, Any], mode: str) -> str:
    gates = result["gates"]
    totals = result["totals"]
    lines: List[str] = []
    lines.append("# TensorPlatform 领域边界门 G1-G3 — 基线快照")
    lines.append("")
    lines.append(f"- 生成时间 (UTC): `{result['generated_at']}`")
    lines.append(f"- 模式: **{mode}**({'阻断门' if mode == 'enforce' else '非阻断基线，仅报告'})")
    lines.append(f"- 仓库根: `{result['repo_root']}`")
    lines.append(f"- 汇总: 违规 {totals['violations']} 条 / 红门 {totals['gates_red']} 道"
                 f" / 绿门 {totals['gates_green']} 道 / 证据不足 {totals['gates_withheld']} 道")
    lines.append("")
    lines.append("## 门总览")
    lines.append("")
    lines.append("| 门 | 语义 | 计数 | 状态 |")
    lines.append("| --- | --- | --- | --- |")
    for key, gate in gates.items():
        count = "WITHHELD" if gate.get("count") is None else gate["count"]
        lines.append(
            f"| {key} | {_md_escape(gate['title'])} | {count} | {gate['status'].upper()} |"
        )
    lines.append("")

    g1 = gates.get("G1_import_boundary")
    if g1:
        lines.append("## G1 import 边界门")
        lines.append("")
        lines.append(f"扫描核心面文件 {g1['files_scanned']} 个；命中 token: "
                     f"`{', '.join(g1['tokens'])}`。")
        lines.append("")
        if g1["violations"]:
            lines.append("| 文件 | 行 | 语句 | 命中 |")
            lines.append("| --- | --- | --- | --- |")
            for v in g1["violations"]:
                lines.append(
                    f"| `{v['file']}` | {v['line']} | `{_md_escape(v['source'])}` "
                    f"| `{v['matched_token']}` |"
                )
        else:
            lines.append("绿灯：核心面无船舶域 import。")
        lines.append("")
        if g1["missing_surface"]:
            lines.append(f"缺失的核心面路径（记录但不计违规）: "
                         f"`{', '.join(g1['missing_surface'])}`")
            lines.append("")

    g2 = gates.get("G2_endpoint_vocabulary")
    if g2:
        lines.append("## G2 端点语汇门")
        lines.append("")
        lines.append(f"- OpenAPI 源: `{g2['source']}`")
        if g2["status"] == "withheld":
            lines.append(f"- 状态: WITHHELD（无法取到 openapi.json：`{g2['error']}`）")
        else:
            lines.append(f"- 语汇正则: `{g2['pattern']}`")
            lines.append(f"- OpenAPI 路径总数: {g2['total_openapi_paths']}")
            lines.append(f"- 命中路径数 / 端点数: {g2['path_count']} / {g2['count']}")
            lines.append(f"- 规格书声明基线: {g2['spec_stated_baseline']}"
                         f"（实测 {g2['count']}，差异见报告『影响点』）")
        lines.append("")
        if g2["endpoints"]:
            lines.append("| 路径 | 方法 | 命中语汇 |")
            lines.append("| --- | --- | --- |")
            for e in g2["endpoints"]:
                lines.append(
                    f"| `{e['path']}` | {', '.join(e['methods']) or '-'} | {e['matched_token']} |"
                )
            lines.append("")
            delta = g2["count"] - g2["spec_stated_baseline"]
            if delta:
                lines.append(
                    f"**基线与规格书差异**: 规格书（审计 §7）记 {g2['spec_stated_baseline']} 个活端点，"
                    f"本机实测 {g2['count']} 个，差 {delta:+d}。测量口径：OpenAPI `paths` 中 URL "
                    f"命中语汇正则、且方法为合法 HTTP 动词的 **operation** 数（同上表）。"
                    f"差异来源为规格书成文后 `calculations/seakeeping_*` 一族端点增补；"
                    f"棘轮以实测值为锚，规格书值仅作对照留档。"
                )
                lines.append("")

    g3 = gates.get("G3_constant_hoisting")
    if g3:
        lines.append("## G3 常数外置门")
        lines.append("")
        lines.append(f"扫描文件 {g3['files_scanned']} 个；命中 {g3['count']} 处；"
                     f"按常数分布: `{json.dumps(g3['by_constant'], ensure_ascii=False)}`；"
                     f"按语境分布: `{json.dumps(g3.get('by_context', {}), ensure_ascii=False)}`"
                     f"（code=可执行代码，string=字面量/文档串，comment=注释）")
        lines.append("")
        if g3["violations"]:
            lines.append("| 文件 | 行 | 常数 | 语境 | 片段 |")
            lines.append("| --- | --- | --- | --- | --- |")
            for v in g3["violations"][:60]:
                lines.append(
                    f"| `{v['file']}` | {v['line']} | `{v['constant']}` "
                    f"| {v.get('context', '?')} | `{_md_escape(v['text'])}` |"
                )
            if len(g3["violations"]) > 60:
                lines.append(f"| ... | | | 其余 {len(g3['violations']) - 60} 处见 JSON 基线 |")
            lines.append("")

    # --- programmatic triage / interpretation (no hand-copied numbers) -------
    lines.append("## 影响面分布（程序化统计）")
    lines.append("")
    if g1 and g1["violations"]:
        areas: Dict[str, int] = {}
        for v in g1["violations"]:
            area = "backend 核心面" if v["file"].startswith("backend/") else "tools/audit 审计面"
            areas[area] = areas.get(area, 0) + 1
        lines.append("G1 违规按面分布: " + "、".join(
            f"{k} {v} 条" for k, v in sorted(areas.items(), key=lambda kv: -kv[1])))
        lines.append("")
    if g3 and g3["violations"]:
        per_file: Dict[str, int] = {}
        for v in g3["violations"]:
            per_file[v["file"]] = per_file.get(v["file"], 0) + 1
        lines.append("| G3 文件 | 命中数 |")
        lines.append("| --- | --- |")
        for fname, cnt in sorted(per_file.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| `{fname}` | {cnt} |")
        lines.append("")
    if g2 and g2["status"] != "withheld":
        sorted_e = sorted(g2["endpoints"], key=lambda e: e["path"])
        by_prefix: Dict[str, int] = {}
        for e in sorted_e:
            prefix = "/".join(e["path"].split("/")[:4])
            by_prefix[prefix] = by_prefix.get(prefix, 0) + max(1, len(e["methods"]))
        lines.append("G2 端点按路由前缀分布: " + "、".join(
            f"`{k}` {v} 个" for k, v in sorted(by_prefix.items(), key=lambda kv: -kv[1])))
        lines.append("")

    lines.append("## 判定口径与翻绿方式")
    lines.append("")
    lines.append("- G1/G3 为静态扫描（AST / 文本），可离线复算，确定性。")
    lines.append("- G2 需要活服务；取不到 openapi.json 时记 WITHHELD（基线模式下不计违规，"
                 "`--enforce` 模式下判红——无法验证即不放行）。")
    lines.append("- 基线模式恒 exit 0，不阻断既有 PR 流；迁移完成后加 `--enforce` 翻为阻断门。")
    lines.append("- G3 的计数是裸 grep 语义（`KVLCC` 亦命中 `KVLCC2` 族），并额外做"
                 "「语境」三分（code/string/comment）：只有 `code` 才是真正的物理常数外泄，"
                 "`string`/`comment` 多为算例名与文档表述，属清理优先级较低的一类。")
    lines.append("- 退出码：基线模式恒 0；`--enforce` 下红门或不可评估（WITHHELD）返回 2；"
                 "内部错误返回 3。")
    lines.append("- 门脚本自身 `tools/audit/boundary_gates.py` 从扫描面剔除"
                 "（其词汇表必然含被检 token，等价于 linter 不 lint 自身词表）。")
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="TensorPlatform domain boundary gates G1-G3 (non-blocking baseline by default)."
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="repository root (default: two levels above this script)",
    )
    parser.add_argument(
        "--api-base",
        default="http://127.0.0.1:8010",
        help="API base URL whose /openapi.json is inspected (G2)",
    )
    parser.add_argument(
        "--openapi-file",
        default=None,
        help="read the OpenAPI document from a file instead of HTTP (offline/CI)",
    )
    parser.add_argument(
        "--json",
        default="artifacts/boundary_gates/baseline.json",
        help="JSON baseline output path (relative to repo root unless absolute)",
    )
    parser.add_argument(
        "--report",
        default="docs/audit/boundary_gates_baseline.md",
        help="markdown snapshot report path; empty string disables it",
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="blocking mode: exit 2 when any gate is red or cannot be evaluated",
    )
    parser.add_argument(
        "--max-violations",
        type=int,
        default=0,
        help="allowed violations per gate in --enforce mode (ratchet head-room)",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="comma separated subset of gates, e.g. G1,G3",
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    only = [g.strip() for g in args.only.split(",")] if args.only else None
    mode = "enforce" if args.enforce else "baseline"

    try:
        result = run_gates(
            repo_root,
            api_base=args.api_base,
            openapi_file=args.openapi_file,
            only=only,
            timeout=args.timeout,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[boundary_gates] internal error: {exc}", file=sys.stderr)
        return 3

    result["mode"] = mode
    result["enforce"] = bool(args.enforce)
    result["max_violations"] = args.max_violations

    json_path = Path(args.json)
    if not json_path.is_absolute():
        json_path = repo_root / json_path
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_written = None
    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = repo_root / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_markdown(result, mode), encoding="utf-8")
        report_written = report_path

    if not args.quiet:
        print(f"[boundary_gates] mode={mode} json={json_path}"
              + (f" report={report_written}" if report_written else ""))
        for key, gate in result["gates"].items():
            count = gate["count"]
            print(f"  {key}: status={gate['status']} count="
                  f"{'WITHHELD' if count is None else count}")
        print(f"  totals: {result['totals']}")

    if not args.enforce:
        return 0

    blocking: List[str] = []
    for key, gate in result["gates"].items():
        if gate["status"] == "withheld":
            blocking.append(f"{key}: withheld ({gate.get('error')})")
        elif int(gate["count"] or 0) > args.max_violations:
            blocking.append(
                f"{key}: {gate['count']} violations > max {args.max_violations}"
            )
    if blocking:
        print("[boundary_gates] ENFORCE FAIL:", file=sys.stderr)
        for item in blocking:
            print(f"  - {item}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())