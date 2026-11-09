"""现网判据抽样器：把平台里**已经存在**的数值验收阈值抽成注册表条目（零领域语义）。

机制边界
--------
* 抽什么由数据工件 ``data/criteria_governance/live_sampling_plan.json`` 声明（文件 + 符号名）；
* 代码只做三件通用事：AST 取常量值/行号、取证窗口内找来源证据、按名字约定定比较算子；
* 找不到出处证据的常量**不许冒充**有来源：一律标 ``source_type=heuristic``（不可裁），
  证据片段与行号写进条目，供人工复核。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "data" / "criteria_governance" / "live_sampling_plan.json"

#: 取证窗口：赋值行自身 + 紧邻其上的连续注释/文档行（最多 N 行）
EVIDENCE_WINDOW = 6

#: 出处取证词表：**配置在数据工件里**（``provenance_patterns``），代码零领域语义
_PATTERN_KEYS = ("doi", "standard", "empirical")


def compile_patterns(plan: Optional[Dict] = None) -> Dict[str, "re.Pattern[str]"]:
    """从抽样计划读取证词表并编译（缺项即报错，不回退到代码内硬编码）。"""
    plan = plan if plan is not None else load_plan()
    specs = plan.get("provenance_patterns") or {}
    missing = [key for key in _PATTERN_KEYS if not specs.get(key)]
    if missing:
        raise KeyError(f"provenance_patterns missing in plan: {missing}")
    return {key: re.compile(specs[key], re.IGNORECASE if key == "empirical" else 0)
            for key in _PATTERN_KEYS}


class UnparseableSource(RuntimeError):
    """被抽样的源文件当前无法解析（例如遗留冲突标记）——如实跳过并记账，不静默忽略。"""


def load_plan(path: Path = PLAN_PATH) -> Dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _numeric_value(node: ast.AST) -> Optional[float]:
    sign = 1.0
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        sign, node = -1.0, node.operand
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return sign * float(node.value)
    return None


def extract_constants(path: Path, symbols: Sequence[str]) -> Dict[str, Dict[str, object]]:
    """在文件里取指定符号的数值常量（任意嵌套层级），返回 {symbol: {value, line}}。"""
    source = Path(path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise UnparseableSource(f"{path}: {exc.msg} @ line {exc.lineno}") from exc
    wanted = set(symbols)
    found: Dict[str, Dict[str, object]] = {}
    for node in ast.walk(tree):
        targets: Iterable[ast.AST]
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        value = _numeric_value(node.value) if node.value is not None else None
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in wanted:
                found.setdefault(target.id, {"value": value, "line": node.lineno})
    return found


def evidence_window(source: str, line: int) -> List[Tuple[int, str]]:
    """取证窗口：本行 + 其上方至多 ``EVIDENCE_WINDOW`` 行内的注释/文档行（遇代码行即止）。

    空行不截断（阈值注释与常量之间常隔空行），但**代码行**截断——只采信"紧邻声明"的出处，
    不去捞整个文件的头部文档，避免把不相关的引用算到某条阈值头上。
    """
    lines = source.splitlines()
    idx = line - 1
    window: List[Tuple[int, str]] = []
    if 0 <= idx < len(lines):
        window.append((line, lines[idx]))
    cursor = idx - 1
    scanned = 0
    while cursor >= 0 and scanned < EVIDENCE_WINDOW:
        text = lines[cursor].strip()
        if not text:
            cursor -= 1
            scanned += 1
            continue
        if text.startswith('#') or text.startswith('"""') \
                or text.startswith("'''") or text.startswith("*") \
                or text.endswith('"""'):
            window.append((cursor + 1, lines[cursor]))
            cursor -= 1
            scanned += 1
            continue
        break
    return window

def classify_source(window: Sequence[Tuple[int, str]], symbol: str,
                    patterns: Optional[Dict[str, "re.Pattern[str]"]] = None) -> Dict[str, object]:
    """按取证窗口判 source_type（证据不足 → heuristic，绝不臆造出处）。"""
    patterns = patterns or compile_patterns()
    blob = "\n".join(text for _, text in window)
    doi = patterns["doi"].search(blob)
    if doi:
        return {"source_type": "literature", "kind": "doi", "identifier": doi.group(0).rstrip("."),
                "evidence": f"DOI literal in evidence window (line {_line_of(window, doi.group(0))})"}
    standard = patterns["standard"].search(blob)
    if standard:
        return {"source_type": "standard", "kind": "standard_clause",
                "identifier": f"{standard.group(0).strip()} @ {symbol}",
                "evidence": f"standard citation in evidence window (line {_line_of(window, standard.group(0))})"}
    empirical = patterns["empirical"].search(blob)
    if empirical:
        return {"source_type": "empirical", "kind": "file_sha256",
                "identifier": f"{symbol} @ empirical-calibration context",
                "evidence": f"empirical/calibration wording in evidence window (line {_line_of(window, empirical.group(0))})"}
    return {"source_type": "heuristic", "kind": "file_sha256",
            "identifier": f"{symbol} @ no provenance in evidence window",
            "evidence": "no DOI/standard/empirical evidence within window → heuristic (non-adjudicable)"}


def _line_of(window: Sequence[Tuple[int, str]], needle: str) -> int:
    for lineno, text in window:
        if needle in text:
            return lineno
    return window[0][0] if window else 0


def comparator_from_name(symbol: str, value: float) -> Tuple[str, str]:
    """按符号命名约定定比较算子（下限型 ``>=``，其余上限型 ``<=``）。"""
    upper = symbol.upper()
    if upper.startswith("MIN_") or "_MIN" in upper or upper.endswith("_MIN"):
        return ">=", "name_prefix_min_floor"
    if "MAX" in upper or upper.startswith("LIMIT"):
        return "<=", "name_prefix_max_ceiling"
    return "<=", "default_tolerance_ceiling"


def unit_from_name(symbol: str) -> str:
    upper = symbol.upper()
    if upper.endswith("_PCT") or "_PCT_" in upper:
        return "pct"
    return "1"


def harvest(plan: Optional[Dict] = None, *, root: Path = ROOT,
            review_dates: Optional[Dict[str, str]] = None) -> List[Dict]:
    """按抽样计划抽取现网阈值，返回 schema v2 条目列表（含取证审计链）。"""
    plan = plan or load_plan()
    patterns = compile_patterns(plan)
    review_dates = review_dates or {}
    entries: List[Dict] = []
    entries_skipped: List[Dict] = []
    index = 0
    for source_spec in plan.get("sources") or []:
        rel = str(source_spec["path"])
        path = root / rel
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            constants = extract_constants(path, source_spec.get("symbols") or [])
        except UnparseableSource as exc:
            entries_skipped.append({"path": rel, "reason": str(exc)})
            continue
        for symbol in sorted(constants, key=lambda s: constants[s]["line"]):
            index += 1
            value = float(constants[symbol]["value"])
            line = int(constants[symbol]["line"])
            window = evidence_window(source, line)
            verdict = classify_source(window, symbol, patterns)
            override = (source_spec.get("comparators") or {}).get(symbol)
            if override:
                comparator, comparator_rule = str(override), "plan_override"
            else:
                comparator, comparator_rule = comparator_from_name(symbol, value)
            entries.append({
                "criterion_id": f"LIVE-{index:03d}-{symbol}",
                "title": f"live threshold {symbol}",
                "statement": f"{symbol} (declared in {rel}:{line}) must satisfy "
                             f"observed {comparator} {value}",
                "quantity": symbol,
                "comparator": comparator,
                "threshold": value,
                "unit": unit_from_name(symbol),
                "source_type": verdict["source_type"],
                "source_ref": {
                    "kind": verdict["kind"],
                    "identifier": verdict["identifier"],
                    "file_sha256": digest if verdict["kind"] == "file_sha256" else digest,
                    "locator": f"{rel}:{line}",
                },
                "depends_on": [],
                "derivation": {"method": "source_binding", "reference": "",
                               "parents": [], "expression": None},
                "probe": {"name": "source_constant", "params": {"path": rel, "symbol": symbol}},
                "review_date": review_dates.get(rel),
                "_evidence": {"snippet": verdict["evidence"],
                              "window_lines": [ln for ln, _ in window],
                              "comparator_rule": comparator_rule,
                              "artifact": rel},
            })
    return entries


def plan_coverage(plan: Optional[Dict] = None, *, root: Path = ROOT) -> Dict[str, object]:
    """抽样计划覆盖率：声明了多少、抽到多少、缺哪些（防"计划写了一堆但没抽"）。"""
    plan = plan or load_plan()
    declared = 0
    harvested = 0
    missing: List[str] = []
    unparseable: List[Dict] = []
    for source_spec in plan.get("sources") or []:
        rel = str(source_spec["path"])
        symbols = list(source_spec.get("symbols") or [])
        declared += len(symbols)
        path = root / rel
        if not path.is_file():
            missing.extend(f"{rel}::{s}" for s in symbols)
            continue
        try:
            constants = extract_constants(path, symbols)
        except UnparseableSource as exc:
            unparseable.append({"path": rel, "reason": str(exc)})
            missing.extend(f"{rel}::{s}" for s in symbols)
            continue
        harvested += len(constants)
        missing.extend(f"{rel}::{s}" for s in symbols if s not in constants)
    return {"declared": declared, "harvested": harvested, "missing": missing,
            "unparseable": unparseable,
            "coverage": (harvested / declared) if declared else 0.0}


__all__ = ["PLAN_PATH", "load_plan", "compile_patterns", "extract_constants", "evidence_window",
           "classify_source", "comparator_from_name", "harvest", "plan_coverage",
           "UnparseableSource"]