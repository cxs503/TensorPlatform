#!/usr/bin/env python3
"""audit_findings_tracker.core —— 交差审计发现的结构化追踪内核（零领域语义）。

六条硬规矩（反假通过 / 反手抄 / 反标签错位）
--------------------------------------------
====== ================================================== ==============================
规矩   实现                                                    机器可读证据
====== ================================================== ==============================
R1     报告容器异构 → 按 schema 的 ``finding_list_keys`` 递归定位；   ``layout.mode`` /
       找不到结构 → ``FAIL``（不许把「没解析到」当「没发现」）        ``reasons[].code``
R2     只从结构化字段取类；缺结构化字段才退文本字段，且记           ``findings[].match_scope``
       ``match_scope="text"``（可审计，不冒充强证据）
R3     无法归类的条目降级 ``WITHHELD`` 并令总判 ``FAIL``             ``findings[].status``
R4     声明计数（报告自带的 counts）与实测计数不一致 →              ``origin="derived"`` 的
       派生一条 ``label_mismatch`` 发现（防手抄汇总数）              ``DECLARED_COUNT_MISMATCH``
R5     同一发现的声明类别与其它分类字段互相矛盾 →                    派生 ``LABEL_CONFLICT``
       派生一条 ``label_mismatch`` 发现（防标签错位）
R6     输出带报告/schema 的 sha256；无时钟无随机 → 同输入逐字节同    ``inputs.*.sha256``
====== ================================================== ==============================

注意：派生发现（derived）**不覆盖**原始发现的类别，只追加台账条目——honest 记录优先。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_ID = "audit-findings/1.0"
CANONICAL_CATEGORIES: Tuple[str, ...] = (
    "fake_pass",
    "hand_copy",
    "label_mismatch",
    "undefined_criteria",
    "other",
)
WITHHELD = "WITHHELD"

#: 原因码登记表（机器可读；新增只追加，不改旧义）
REASON_CODES: Dict[str, str] = {
    "PARSE_ERROR": "报告或 schema 不是合法 JSON / 不是期望的顶层结构",
    "SCHEMA_INVALID": "schema 缺类别闭词表或未覆盖全部五类发现",
    "SCHEMA_MISSING": "schema 文件不存在或不可读",
    "REPORT_MISSING": "报告文件不存在或不可读",
    "NO_FINDINGS_STRUCTURE": "报告中找不到可定位的发现容器（既非显式空列表，也无发现列表）",
    "UNRECOGNIZED_CATEGORY": "条目存在但无法归入五类发现，降级 WITHHELD",
    "UNSUPPORTED_ENTRY": "条目形态不受支持（非对象/非字符串），降级 WITHHELD",
    "MISSING_REQUIRED_CATEGORY": "schema 要求覆盖的类别在本报告中没有出现",
    "DECLARED_COUNT_MISMATCH": "报告自述计数与实测计数不一致（派生 label_mismatch）",
    "LABEL_CONFLICT": "同一发现的声明类别与其它分类字段互相矛盾（派生 label_mismatch）",
}

#: 触发 FAIL 的原因码（其余原因码只记录，不改变 PASS/FAIL）
CRITICAL_REASONS = frozenset({
    "PARSE_ERROR",
    "SCHEMA_INVALID",
    "SCHEMA_MISSING",
    "REPORT_MISSING",
    "NO_FINDINGS_STRUCTURE",
    "UNRECOGNIZED_CATEGORY",
    "UNSUPPORTED_ENTRY",
    "MISSING_REQUIRED_CATEGORY",
})

DEFAULT_SCHEMA_PATH = Path(__file__).with_name("audit_findings.v1.schema.json")

#: 保守默认（schema 缺字段时使用；与 schema 工件内容一致）
_DEFAULTS: Dict[str, Any] = {
    "finding_list_keys": ["findings", "audit_findings", "issues", "defects", "violations",
                          "problems", "items", "entries", "records", "results"],
    "classification_fields": ["category", "kind", "type", "code", "class", "label", "labels",
                              "tags", "tag", "status", "verdict"],
    "text_fields": ["title", "summary", "description", "detail", "details", "message", "msg",
                    "note", "notes", "text", "claim", "assertion", "rule", "reason"],
    "id_fields": ["id", "finding_id", "uid", "key"],
    "severity_fields": ["severity", "level", "priority", "risk"],
    "subject_fields": ["subject", "target", "node", "mission", "item", "path", "artifact",
                       "where", "location", "scope"],
    "count_container_keys": ["counts", "category_counts", "totals", "by_category",
                             "summary_counts"],
    "withheld_category": WITHHELD,
    "max_message_chars": 1000,
}

_ASCII_WORD = re.compile(r"[a-z]")


class TrackerError(Exception):
    """带机器可读原因码的失败（CLI 转成 FAIL 文档 + 退出码 1）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ------------------------------------------------------------------ 基础工具
def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path, missing_code: str) -> Tuple[Any, bytes]:
    try:
        payload = path.read_bytes()
    except OSError as exc:  # 文件不存在 / 权限
        raise TrackerError(missing_code, f"无法读取 {path}: {exc}") from exc
    try:
        return json.loads(payload.decode("utf-8")), payload
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrackerError("PARSE_ERROR", f"{path} 不是合法 UTF-8 JSON: {exc}") from exc


def _normalize(text: Any) -> str:
    """统一小写并压平分隔符：``FAKE-PASS`` / ``fake_pass`` → ``fake pass``。"""
    return re.sub(r"[\s_\-/\.]+", " ", str(text).lower()).strip()


def _alias_regex(alias_norm: str) -> re.Pattern:
    """ASCII 别名按词边界匹配（防 ``other`` 命中 ``another``）；CJK 直接子串。"""
    if _ASCII_WORD.search(alias_norm):
        return re.compile(r"(?<![a-z0-9])" + re.escape(alias_norm) + r"(?![a-z0-9])")
    return re.compile(re.escape(alias_norm))


class Taxonomy:
    """别名 → 类别的闭词表匹配器（类别顺序即优先级）。"""

    def __init__(self, aliases: Dict[str, Sequence[str]], order: Sequence[str] = CANONICAL_CATEGORIES):
        self.order = tuple(order)
        self._patterns: List[Tuple[str, re.Pattern]] = []
        for cat in self.order:
            seen = set()
            for alias in aliases.get(cat, ()):  # pragma: no branch - 防御空表
                norm = _normalize(alias)
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                self._patterns.append((cat, _alias_regex(norm)))

    def match(self, text: Any) -> List[str]:
        norm = _normalize(text)
        if not norm:
            return []
        hits: List[str] = []
        for cat, rx in self._patterns:
            if cat not in hits and rx.search(norm):
                hits.append(cat)
        hits.sort(key=self.order.index)
        return hits


# ------------------------------------------------------------------ schema
@dataclass
class Schema:
    raw: Dict[str, Any]
    sha256: str
    path: str
    categories: Tuple[str, ...]
    taxonomy: Taxonomy
    finding_list_keys: Tuple[str, ...]
    classification_fields: Tuple[str, ...]
    text_fields: Tuple[str, ...]
    id_fields: Tuple[str, ...]
    severity_fields: Tuple[str, ...]
    subject_fields: Tuple[str, ...]
    count_container_keys: Tuple[str, ...]
    required_categories: Tuple[str, ...]
    withheld_category: str
    max_message_chars: int


def load_schema(path: Any = None) -> Schema:
    """加载分类法工件；类别闭词表必须覆盖五类发现，否则 ``SCHEMA_INVALID``。"""
    p = Path(path) if path is not None else DEFAULT_SCHEMA_PATH
    doc, payload = _read_json(p, "SCHEMA_MISSING")
    if not isinstance(doc, dict):
        raise TrackerError("SCHEMA_INVALID", "schema 顶层必须是对象")
    cats = doc.get("categories")
    if not isinstance(cats, list) or not cats:
        raise TrackerError("SCHEMA_INVALID", "schema 缺少 categories 闭词表")
    cats = tuple(str(c) for c in cats)
    missing = [c for c in CANONICAL_CATEGORIES if c not in cats]
    if missing:
        raise TrackerError("SCHEMA_INVALID", f"schema 类别未覆盖五类发现，缺: {missing}")
    aliases = doc.get("category_aliases") or {}
    if not isinstance(aliases, dict):
        raise TrackerError("SCHEMA_INVALID", "schema.category_aliases 必须是对象")
    merged = {c: list(aliases.get(c, [])) for c in cats}
    # 别名兜底：类别名本身永远是别名
    for c in cats:
        if c not in merged[c]:
            merged[c].append(c)
    required = doc.get("required_categories") or []
    if not isinstance(required, list):
        raise TrackerError("SCHEMA_INVALID", "schema.required_categories 必须是列表")
    bad_required = [c for c in required if c not in cats]
    if bad_required:
        raise TrackerError("SCHEMA_INVALID", f"required_categories 含未知类别: {bad_required}")

    def _keys(name: str) -> Tuple[str, ...]:
        value = doc.get(name, _DEFAULTS.get(name))
        if not isinstance(value, list) or not value:
            raise TrackerError("SCHEMA_INVALID", f"schema.{name} 必须是非空列表")
        return tuple(str(v) for v in value)

    return Schema(
        raw=doc,
        sha256=_sha256_bytes(payload),
        path=str(p),
        categories=cats,
        taxonomy=Taxonomy(merged, cats),
        finding_list_keys=_keys("finding_list_keys"),
        classification_fields=_keys("classification_fields"),
        text_fields=_keys("text_fields"),
        id_fields=_keys("id_fields"),
        severity_fields=_keys("severity_fields"),
        subject_fields=_keys("subject_fields"),
        count_container_keys=_keys("count_container_keys"),
        required_categories=tuple(str(c) for c in required),
        withheld_category=str(doc.get("withheld_category") or WITHHELD),
        max_message_chars=int(doc.get("max_message_chars") or _DEFAULTS["max_message_chars"]),
    )


# ------------------------------------------------------------------ 结构定位
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _child_pointer(pointer: str, key: Any) -> str:
    if isinstance(key, str) and _IDENT.match(key):
        return f"{pointer}.{key}"
    return f"{pointer}[{json.dumps(key, ensure_ascii=False)}]"


def _collect_lists(node: Any, pointer: str, keys: Sequence[str],
                   out: List[Tuple[str, list]]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            p = _child_pointer(pointer, k)
            if k in keys and isinstance(v, list):
                out.append((p, v))
            _collect_lists(v, p, keys, out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _collect_lists(v, f"{pointer}[{i}]", keys, out)


def _all_lists(node: Any, pointer: str, out: List[Tuple[str, list]]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _all_lists(v, _child_pointer(pointer, k), out)
    elif isinstance(node, list):
        out.append((pointer, node))
        for i, v in enumerate(node):
            _all_lists(v, f"{pointer}[{i}]", out)


_FINDING_ID_TEXT = re.compile(r"^\s*(?:[A-Za-z]{1,8}[-_ ]?\d+|F\d+)", re.IGNORECASE)


def _looks_like_finding(node: Any, classification_fields: Sequence[str]) -> bool:
    if isinstance(node, str):
        return bool(_FINDING_ID_TEXT.match(node)) and len(node) <= 500
    if isinstance(node, dict):
        return any(f in node for f in classification_fields)
    return False


def _locate_finding_lists(doc: Any, schema: Schema) -> Tuple[List[Tuple[str, list]], str, List[dict]]:
    """返回 (发现列表, 布局模式, 备注)。显式容器优先；退化到启发式列表；全无 → 空。"""
    notes: List[dict] = []
    if isinstance(doc, list):
        return [("$", doc)], "root_list", notes
    hits: List[Tuple[str, list]] = []
    _collect_lists(doc, "$", schema.finding_list_keys, hits)
    if hits:
        non_empty = [(p, l) for p, l in hits if l]
        if non_empty:
            return non_empty, "explicit", notes
        return hits, "explicit_empty", notes

    # 启发式：找“看起来像发现列表”的列表（≥半数元素带分类字段或形如发现编号）
    candidates: List[Tuple[str, list]] = []
    all_lists: List[Tuple[str, list]] = []
    _all_lists(doc, "$", all_lists)
    for pointer, lst in all_lists:
        if not lst:
            continue
        look = sum(1 for item in lst if _looks_like_finding(item, schema.classification_fields))
        if look * 2 >= len(lst):
            candidates.append((pointer, lst))
    if candidates:
        if len(candidates) > 1:
            notes.append({"code": "HEURISTIC_MULTIPLE_LISTS",
                          "detail": f"多个候选列表，取首个: {[p for p, _ in candidates]}"})
        return [candidates[0]], "heuristic", notes

    # 显式空列表（报告声明“本次没有发现”）是合法形态
    explicit_empty: List[Tuple[str, list]] = []
    for pointer, lst in all_lists:
        if lst:
            continue
        tail = pointer.rsplit(".", 1)[-1]
        if tail in schema.finding_list_keys:
            explicit_empty.append((pointer, lst))
    if explicit_empty:
        return explicit_empty, "explicit_empty", notes
    return [], "none", notes


# ------------------------------------------------------------------ 归类
@dataclass
class Finding:
    finding_id: str
    origin: str
    status: str
    category: str
    source_pointer: str
    match_scope: str
    declared_id: Optional[str] = None
    declared_category: Optional[str] = None
    severity: Optional[str] = None
    subject: Optional[str] = None
    message: Optional[str] = None
    ambiguous: bool = False
    reason_code: Optional[str] = None
    related_pointer: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)


def _first_value(node: dict, fields: Sequence[str]) -> Optional[str]:
    for f in fields:
        if f in node and node[f] is not None:
            value = node[f]
            if isinstance(value, (str, int, float, bool)):
                return str(value)
            if isinstance(value, list) and value:
                return ",".join(str(v) for v in value if isinstance(v, (str, int, float)))
    return None


def _field_matches(node: dict, schema: Schema) -> Dict[str, List[str]]:
    per_field: Dict[str, List[str]] = {}
    for f in schema.classification_fields:
        if f not in node:
            continue
        value = node[f]
        values = value if isinstance(value, list) else [value]
        cats: List[str] = []
        for v in values:
            if isinstance(v, (str, int, float)) and not isinstance(v, bool):
                for c in schema.taxonomy.match(v):
                    if c not in cats:
                        cats.append(c)
        if cats:
            per_field[f] = cats
    return per_field


def _text_fallback(node: dict, schema: Schema) -> str:
    chunks: List[str] = []
    for f in schema.text_fields:
        v = node.get(f)
        if isinstance(v, str) and v.strip():
            chunks.append(v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            chunks.append(str(v))
    return "\n".join(chunks)


def _make_finding(node: Any, pointer: str, schema: Schema, index: int) -> Finding:
    fid = f"F-{index:04d}"
    if isinstance(node, str):
        cats = schema.taxonomy.match(node)
        msg = node[: schema.max_message_chars]
        if cats:
            return Finding(fid, "report", "RECOGNIZED", cats[0], pointer, "text",
                           declared_category=None, message=msg, ambiguous=len(cats) > 1,
                           detail={"matched": cats} if len(cats) > 1 else {})
        return Finding(fid, "report", "WITHHELD", schema.withheld_category, pointer, "-",
                       message=msg, reason_code="UNRECOGNIZED_CATEGORY")
    if not isinstance(node, dict):
        return Finding(fid, "report", "WITHHELD", schema.withheld_category, pointer, "-",
                       reason_code="UNSUPPORTED_ENTRY",
                       detail={"entry_type": type(node).__name__})

    per_field = _field_matches(node, schema)
    declared = _first_value(node, ("category",)) or _first_value(node, schema.classification_fields)
    severity = _first_value(node, schema.severity_fields)
    subject = _first_value(node, schema.subject_fields)
    message = _text_fallback(node, schema)[: schema.max_message_chars] or None
    declared_id = _first_value(node, schema.id_fields)

    if per_field:
        primary_field = next(f for f in schema.classification_fields if f in per_field)
        cats = per_field[primary_field]
        category = cats[0]
        conflicts = [
            {"field": f, "category": c}
            for f, field_cats in per_field.items() if f != primary_field
            for c in field_cats if c != category
        ]
        detail: Dict[str, Any] = {}
        if len(cats) > 1:
            detail["matched"] = cats
        if conflicts:
            detail["conflicts"] = conflicts
        return Finding(fid, "report", "RECOGNIZED", category, pointer,
                       f"structured:{primary_field}", declared_id=declared_id,
                       declared_category=declared, severity=severity, subject=subject,
                       message=message, ambiguous=len(cats) > 1, detail=detail)

    text = _text_fallback(node, schema)
    cats = schema.taxonomy.match(text) if text else []
    if cats:
        return Finding(fid, "report", "RECOGNIZED", cats[0], pointer, "text",
                       declared_id=declared_id, declared_category=declared, severity=severity,
                       subject=subject, message=message, ambiguous=len(cats) > 1,
                       detail={"matched": cats} if len(cats) > 1 else {})
    return Finding(fid, "report", "WITHHELD", schema.withheld_category, pointer, "-",
                   declared_id=declared_id, declared_category=declared, severity=severity,
                   subject=subject, message=message, reason_code="UNRECOGNIZED_CATEGORY",
                   detail={"keys": sorted(node.keys())[:20]})


# ------------------------------------------------------------------ 声明计数
def _collect_declared_counts(node: Any, schema: Schema,
                             out: List[Tuple[str, Dict[str, int]]]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in schema.count_container_keys and isinstance(v, dict):
                parsed = {c: v[c] for c in schema.categories
                          if isinstance(v.get(c), int) and not isinstance(v.get(c), bool)}
                if parsed:
                    out.append((k, parsed))
            _collect_declared_counts(v, schema, out)
    elif isinstance(node, list):
        for v in node:
            _collect_declared_counts(v, schema, out)


# ------------------------------------------------------------------ 主流程
def _empty_counts(schema: Schema) -> Dict[str, int]:
    counts = {c: 0 for c in schema.categories}
    counts[schema.withheld_category] = 0
    counts["total"] = 0
    return counts


def analyze_document(doc: Any, schema: Schema, report_path: str = "<memory>",
                     report_sha256: str = "0" * 64,
                     require_categories: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """纯函数：报告文档 → 结构化 findings 结果（CLI 与测试共用）。"""
    lists, layout_mode, layout_notes = _locate_finding_lists(doc, schema)
    findings: List[Finding] = []
    reasons: List[dict] = []
    reasons.extend(layout_notes)

    index = 0
    for pointer, lst in lists:
        for offset, item in enumerate(lst):
            index += 1
            findings.append(_make_finding(item, f"{pointer}[{offset}]", schema, index))

    if layout_mode == "none":
        reasons.append({"code": "NO_FINDINGS_STRUCTURE",
                        "detail": "报告里既无显式发现容器，也无启发式可识别的发现列表"})

    base_counts = _empty_counts(schema)
    for f in findings:
        base_counts["total"] += 1
        if f.status == "RECOGNIZED":
            base_counts[f.category] = base_counts.get(f.category, 0) + 1
        else:
            base_counts[schema.withheld_category] += 1

    # R4：声明计数 vs 实测计数（派生 label_mismatch；不改原始发现）
    declared_counts: List[Tuple[str, Dict[str, int]]] = []
    _collect_declared_counts(doc, schema, declared_counts)
    derived_seq = 0
    for container, declared in declared_counts:
        for cat, value in declared.items():
            actual = base_counts.get(cat, 0)
            if value != actual:
                derived_seq += 1
                findings.append(Finding(
                    finding_id=f"D-{derived_seq:04d}", origin="derived", status="RECOGNIZED",
                    category="label_mismatch", source_pointer=f"$.{container}" if _IDENT.match(container)
                    else f"$[{json.dumps(container, ensure_ascii=False)}]",
                    match_scope="derived:declared_counts", declared_category=cat,
                    subject=cat, reason_code="DECLARED_COUNT_MISMATCH",
                    message=f"声明 {container}.{cat}={value}，实测={actual}",
                    detail={"declared": value, "actual": actual},
                ))

    # R5：声明类别与其它分类字段冲突
    for f in findings:
        if f.origin != "report" or f.status != "RECOGNIZED":
            continue
        for conflict in f.detail.get("conflicts", ()):
            derived_seq += 1
            findings.append(Finding(
                finding_id=f"D-{derived_seq:04d}", origin="derived", status="RECOGNIZED",
                category="label_mismatch", source_pointer=f.source_pointer,
                match_scope="derived:label_conflict", declared_category=f.category,
                subject=f.subject, reason_code="LABEL_CONFLICT",
                related_pointer=f.finding_id,
                message=f"{f.finding_id} 声明={f.category} 但 {conflict['field']}={conflict['category']}",
                detail=dict(conflict),
            ))

    # 未识别条目是 FAIL 主因（R3）
    for f in findings:
        if f.status == "WITHHELD" and not any(
                r["code"] == f.reason_code and r.get("pointer") == f.source_pointer for r in reasons):
            reasons.append({"code": f.reason_code or "UNRECOGNIZED_CATEGORY",
                            "pointer": f.source_pointer,
                            "detail": (f.message or "")[:200]})

    # 类别覆盖要求（可选门）
    required = list(require_categories) if require_categories is not None else list(
        schema.required_categories)
    if required:
        seen_cats = {f.category for f in findings if f.status == "RECOGNIZED"}
        missing = [c for c in required if c not in seen_cats]
        if missing:
            reasons.append({"code": "MISSING_REQUIRED_CATEGORY", "detail": missing})

    final_counts = _empty_counts(schema)
    for f in findings:
        final_counts["total"] += 1
        if f.status == "RECOGNIZED":
            final_counts[f.category] = final_counts.get(f.category, 0) + 1
        else:
            final_counts[schema.withheld_category] += 1

    critical = [r for r in reasons if r["code"] in CRITICAL_REASONS]
    status = "FAIL" if critical else "PASS"

    categories_seen = [c for c in schema.categories if final_counts.get(c, 0) > 0]
    return {
        "tool": "tools.audit_findings_tracker",
        "schema": SCHEMA_ID,
        "status": status,
        "exit_code": 0 if status == "PASS" else 1,
        "inputs": {
            "report": {"path": report_path, "sha256": report_sha256},
            "schema": {"path": schema.path, "sha256": schema.sha256},
        },
        "layout": {"mode": layout_mode,
                   "finding_pointers": [p for p, _ in lists],
                   "notes": layout_notes},
        "counts": final_counts,
        "categories_seen": categories_seen,
        "findings": [asdict(f) for f in findings],
        "reasons": reasons,
        "summary": {
            "total": final_counts["total"],
            "recognized": sum(final_counts.get(c, 0) for c in schema.categories),
            "withheld": final_counts.get(schema.withheld_category, 0),
            "derived": sum(1 for f in findings if f.origin == "derived"),
            "required_categories": required,
        },
    }


def analyze_report(report_path: Any, schema_path: Any = None,
                   require_categories: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """报告文件 → 结构化 findings 结果；解析/schema 失败也返回 FAIL 文档（不抛）。"""
    schema: Optional[Schema] = None
    try:
        schema = load_schema(schema_path)
    except TrackerError as exc:
        return _failure_doc(exc.code, exc.message, str(report_path), None)
    try:
        doc, payload = _read_json(Path(report_path), "REPORT_MISSING")
    except TrackerError as exc:
        return _failure_doc(exc.code, exc.message, str(report_path), schema)
    return analyze_document(doc, schema, report_path=str(report_path),
                            report_sha256=_sha256_bytes(payload),
                            require_categories=require_categories)


def _failure_doc(code: str, message: str, report_path: str, schema: Optional[Schema]) -> Dict[str, Any]:
    reasons = [{"code": code, "detail": message}]
    counts = _empty_counts(schema) if schema else {"WITHHELD": 0, "total": 0}
    return {
        "tool": "tools.audit_findings_tracker",
        "schema": SCHEMA_ID,
        "status": "FAIL",
        "exit_code": 1,
        "inputs": {
            "report": {"path": report_path, "sha256": None},
            "schema": ({"path": schema.path, "sha256": schema.sha256} if schema
                       else {"path": None, "sha256": None}),
        },
        "layout": {"mode": "none", "finding_pointers": [], "notes": []},
        "counts": counts,
        "categories_seen": [],
        "findings": [],
        "reasons": reasons,
        "summary": {"total": 0, "recognized": 0, "withheld": 0, "derived": 0,
                    "required_categories": []},
    }


# ------------------------------------------------------------------ 渲染
def render_summary(result: Dict[str, Any]) -> str:
    """一行人类可读摘要（stdout JSON 之外，写 stderr 用）。"""
    counts = result.get("counts", {})
    parts = [f"status={result.get('status')}", f"total={counts.get('total', 0)}"]
    for cat, value in counts.items():
        if cat in ("total", WITHHELD):
            continue
        if value:
            parts.append(f"{cat}={value}")
    if counts.get(WITHHELD):
        parts.append(f"withheld={counts[WITHHELD]}")
    codes = [r["code"] for r in result.get("reasons", [])]
    if codes:
        parts.append("reasons=" + ",".join(codes))
    return " ".join(parts)


def dump_result(result: Dict[str, Any]) -> str:
    """确定性序列化（sort_keys + 固定分隔符；无时钟无随机）。"""
    return json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"