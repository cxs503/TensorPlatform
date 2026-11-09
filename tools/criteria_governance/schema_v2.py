"""判据 schema v2：来源 / 依赖量 / 重推导链 / 复核日期 的结构化登记（零领域语义）。

v1（``registry.py``）要求每条判据"有出处、有可执行仪器"；v2 在此之上把**阈值本身的
身份**也结构化：

* ``source_ref``  —— 出处的机器可校验标识（DOI / 标准条款 / 文件 sha256+locator），
  是"阈值可以被改"的唯一合法理由；
* ``depends_on``  —— 阈值依赖量与指数（``threshold = C·h^p`` 的 ``[{param, exponent}]``），
  让"阈值不是常数"成为可重推导的断言；
* ``derivation.expression`` —— 可选显式表达式（``C*h**p``），供重推导闭环求值；
* ``review_date`` —— 复核日期（ISO）。

本模块只实现**机制**：字段规范化、校验、可裁判定、语义/装饰投影、表达式求值。
判据条目一律来自数据工件；代码里没有任何领域词条、阈值或算例。
"""

from __future__ import annotations

import ast
import math
import re
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

SOURCE_TYPES_V2: Tuple[str, ...] = ("analytic", "literature", "standard", "empirical", "heuristic")

#: 允许进入"可裁集合"的来源类型：有独立出处且不是拟合实测/专家判断
ADJUDICABLE_SOURCE_TYPES: Tuple[str, ...] = ("analytic", "literature", "standard")

COMPARATORS: Tuple[str, ...] = ("<=", ">=", "<", ">", "==", "!=")

#: 阈值表达式里允许的"无需声明指数"的常数符号：解析误差常数 C 与安全裕度 SF
CONSTANT_SYMBOLS: Tuple[str, ...] = ("C", "SF")

#: 出处类型：DOI / 标准条款 / 文件内容哈希 / 解析恒等式 / 探针观测
SOURCE_REF_KINDS: Tuple[str, ...] = (
    "doi", "standard_clause", "file_sha256", "analytic_identity", "probe_observation",
)

#: 纯装饰字段：改动它们**不得**触发漂移红灯（只影响可读性）
COSMETIC_KEYS: Tuple[str, ...] = ("note", "comment", "comments", "reviewed_by", "format", "formatting")

#: 描述性字段：改动只作信息记录，不参与"阈值漂移"裁决
DESCRIPTIVE_KEYS: Tuple[str, ...] = ("title", "statement", "description", "doc")

#: 语义字段：阈值身份与来源绑定；漂移裁决只看这些
SEMANTIC_KEYS: Tuple[str, ...] = (
    "criterion_id", "quantity", "comparator", "threshold", "unit",
    "source_type", "source_ref", "depends_on", "derivation", "review_date",
)

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: 表达式允许的 AST 节点（白名单，避免任意 eval）
_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant,
                  ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)


class ThresholdLawError(ValueError):
    """阈值表达式不合法（非白名单语法/未知符号）。"""


# --------------------------------------------------------------------------- #
# 阈值表达式（threshold(h) 显式形式）求值
# --------------------------------------------------------------------------- #
def compile_threshold_law(expression: str) -> ast.Expression:
    """把 ``C*h**p`` 这类表达式解析成 AST 并做白名单校验。"""
    if not isinstance(expression, str) or not expression.strip():
        raise ThresholdLawError("empty expression")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:  # pragma: no cover - 语法错直接上报
        raise ThresholdLawError(f"syntax_error:{exc}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ThresholdLawError(f"disallowed_node:{type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ThresholdLawError("non_numeric_constant")
    return tree


def free_symbols(expression: str) -> List[str]:
    tree = compile_threshold_law(expression)
    return sorted({node.id for node in ast.walk(tree) if isinstance(node, ast.Name)})


def evaluate_threshold_law(expression: str, values: Dict[str, float]) -> float:
    """在给定 ``{param: value}`` 下求阈值表达式的值。"""
    tree = compile_threshold_law(expression)
    missing = [name for name in free_symbols(expression) if name not in values]
    if missing:
        raise ThresholdLawError("unbound_symbols:" + ",".join(missing))
    return float(_eval_node(tree.body, values))


def _eval_node(node: ast.AST, values: Dict[str, float]) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return float(values[node.id])
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, values)
        return -operand if isinstance(node.op, ast.USub) else operand
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, values)
        right = _eval_node(node.right, values)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0.0:
                raise ThresholdLawError("division_by_zero")
            return left / right
        if isinstance(node.op, ast.Pow):
            return left ** right
    raise ThresholdLawError(f"unsupported_node:{type(node).__name__}")


# --------------------------------------------------------------------------- #
# 规范化（v1 条目 / 原始字典 → v2 条目）
# --------------------------------------------------------------------------- #
def _sha_of_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_entry(raw: Dict, *, file_sha256: Optional[str] = None,
                    locator: Optional[str] = None,
                    review_date: Optional[str] = None) -> Dict:
    """把条目规范化成 v2 形状（缺什么就留空，由 :func:`validate_entry` 判错，不静默补值）。"""
    threshold = raw.get("threshold")
    if isinstance(threshold, dict):
        value = threshold.get("value")
        comparator = threshold.get("op") or threshold.get("comparator")
        unit = threshold.get("unit")
    else:
        value, comparator, unit = threshold, raw.get("comparator"), raw.get("unit")

    derivation = dict(raw.get("derivation") or {})
    probe = dict(raw.get("probe") or {})

    source_ref = raw.get("source_ref")
    if not isinstance(source_ref, dict):
        source_ref = None
        derivation_reference = str(derivation.get("reference") or "").strip()
        if derivation_reference:
            kind = ("analytic_identity" if derivation.get("method") == "analytic_identity"
                    else "probe_observation")
            source_ref = {"kind": kind, "identifier": derivation_reference,
                          "file_sha256": file_sha256, "locator": locator}
        elif file_sha256:
            source_ref = {"kind": "file_sha256", "identifier": f"{locator or '<artifact>'}#sha256",
                          "file_sha256": file_sha256, "locator": locator}

    depends_on = raw.get("depends_on")
    if depends_on is None:
        depends_on = []
        exponent = derivation.get("declared_exponent")
        if exponent is not None:
            depends_on = [{"param": "h", "exponent": float(exponent)}]

    entry = {
        "criterion_id": raw.get("criterion_id") or raw.get("id"),
        "title": raw.get("title"),
        "statement": raw.get("statement"),
        "quantity": raw.get("quantity") or raw.get("metric"),
        "comparator": comparator,
        "threshold": value,
        "unit": unit,
        "source_type": raw.get("source_type"),
        "source_ref": source_ref,
        "depends_on": list(depends_on or []),
        "derivation": derivation,
        "probe": probe,
        "review_date": raw.get("review_date") or review_date,
        "note": raw.get("note"),
    }
    if raw.get("fit_to_measurement"):
        entry["fit_to_measurement"] = True
    return entry


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def validate_entry(entry: Dict, *, known_ids: Optional[Iterable[str]] = None) -> List[str]:
    """返回不合规原因列表；空列表 = 入册。所有原因都是机器可读的标签。"""
    reasons: List[str] = []

    cid = entry.get("criterion_id")
    if not isinstance(cid, str) or not cid.strip():
        reasons.append("criterion_id_missing")
    elif known_ids is not None and cid in set(known_ids):
        reasons.append(f"duplicate_criterion_id:{cid}")

    quantity = entry.get("quantity")
    if not isinstance(quantity, str) or not quantity.strip():
        reasons.append("quantity_missing")

    if entry.get("comparator") not in COMPARATORS:
        reasons.append(f"comparator_invalid:{entry.get('comparator')}")
    if not _is_number(entry.get("threshold")):
        reasons.append("threshold_not_numeric")

    source_type = entry.get("source_type")
    if not source_type:
        reasons.append("source_type_missing")
    elif source_type not in SOURCE_TYPES_V2:
        reasons.append(f"source_type_unknown:{source_type}")

    reasons.extend(validate_source_ref(entry.get("source_ref"), source_type))

    depends_on = entry.get("depends_on")
    if not isinstance(depends_on, list):
        reasons.append("depends_on_not_list")
    else:
        for item in depends_on:
            if not isinstance(item, dict):
                reasons.append("depends_on_item_not_object")
                continue
            if not isinstance(item.get("param"), str) or not item["param"].strip():
                reasons.append("depends_on_param_missing")
            if not _is_number(item.get("exponent")):
                reasons.append("depends_on_exponent_not_numeric")

    expression = (entry.get("derivation") or {}).get("expression")
    if expression:
        try:
            symbols = set(free_symbols(expression))
        except ThresholdLawError as exc:
            reasons.append(f"expression_invalid:{exc}")
            symbols = set()
        declared = {item.get("param") for item in depends_on or [] if isinstance(item, dict)}
        declared |= set(CONSTANT_SYMBOLS)
        if symbols - declared:
            reasons.append("expression_symbols_undeclared:" + ",".join(sorted(symbols - declared)))

    review_date = entry.get("review_date")
    if not review_date:
        reasons.append("review_date_missing")
    elif not (_ISO_DATE_RE.match(str(review_date)) and _parse_date(str(review_date))):
        reasons.append(f"review_date_invalid:{review_date}")

    if source_type == "analytic":
        method = (entry.get("derivation") or {}).get("method")
        if method not in ("analytic_identity", "refinement_scan", "standard_reference"):
            reasons.append(f"analytic_method_not_analytic:{method}")
        if not str((entry.get("derivation") or {}).get("reference") or "").strip():
            reasons.append("analytic_reference_missing")

    return reasons


def validate_source_ref(source_ref: Optional[Dict], source_type: Optional[str]) -> List[str]:
    """出处结构校验：kind/identifier 必填；DOI 与 sha256 各自格式校验。"""
    if not isinstance(source_ref, dict):
        return ["source_ref_missing"]
    reasons: List[str] = []
    kind = source_ref.get("kind")
    identifier = source_ref.get("identifier")
    if kind not in SOURCE_REF_KINDS:
        reasons.append(f"source_ref_kind_invalid:{kind}")
    if not isinstance(identifier, str) or not identifier.strip():
        reasons.append("source_ref_identifier_missing")
        identifier = ""
    if kind == "doi" and not _DOI_RE.match(str(identifier)):
        reasons.append(f"source_ref_doi_malformed:{identifier}")
    if kind == "standard_clause" and len(str(identifier).strip()) < 3:
        reasons.append("source_ref_clause_too_short")
    if kind == "file_sha256":
        digest = source_ref.get("file_sha256")
        if not isinstance(digest, str) or not _SHA256_RE.match(digest):
            reasons.append("source_ref_sha256_malformed")
    if source_type == "analytic" and kind == "file_sha256":
        reasons.append("analytic_source_ref_not_analytic")
    return reasons


def _parse_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# 可裁判定 / 投影
# --------------------------------------------------------------------------- #
def adjudicability(entry: Dict) -> Dict[str, object]:
    """可裁集合判定：heuristic/empirical 一律不可裁（C1/C7）。

    返回 ``{"adjudicable": bool, "reason": str}``；``reason`` 是可审计的口径标签。
    """
    source_type = entry.get("source_type")
    if entry.get("fit_to_measurement"):
        return {"adjudicable": False, "reason": "threshold_fitted_to_measurement"}
    if source_type == "heuristic":
        return {"adjudicable": False, "reason": "heuristic_expert_judgement"}
    if source_type == "empirical":
        return {"adjudicable": False, "reason": "empirical_fit_to_observation"}
    if source_type not in ADJUDICABLE_SOURCE_TYPES:
        return {"adjudicable": False, "reason": f"source_type_not_adjudicable:{source_type}"}
    if not _is_number(entry.get("threshold")):
        return {"adjudicable": False, "reason": "threshold_not_numeric"}
    if validate_source_ref(entry.get("source_ref"), source_type):
        return {"adjudicable": False, "reason": "source_ref_incomplete"}
    probe = entry.get("probe") or {}
    if not probe.get("name"):
        return {"adjudicable": False, "reason": "no_executable_probe"}
    return {"adjudicable": True, "reason": "sourced_with_probe"}


def semantic_projection(entry: Dict) -> Dict:
    """语义投影：参与漂移裁决的字段（阈值身份 + 来源绑定 + 依赖链）。"""
    source_ref = entry.get("source_ref") or {}
    derivation = entry.get("derivation") or {}
    return {
        "criterion_id": entry.get("criterion_id"),
        "quantity": entry.get("quantity"),
        "comparator": entry.get("comparator"),
        "threshold": float(entry["threshold"]) if _is_number(entry.get("threshold")) else entry.get("threshold"),
        "unit": entry.get("unit"),
        "source_type": entry.get("source_type"),
        "source_ref": {
            "kind": source_ref.get("kind"),
            "identifier": source_ref.get("identifier"),
            "file_sha256": source_ref.get("file_sha256"),
        },
        "depends_on": sorted(
            ((item or {}).get("param"), (item or {}).get("exponent"))
            for item in (entry.get("depends_on") or [])),
        "derivation": {
            "method": derivation.get("method"),
            "expression": derivation.get("expression"),
            "declared_exponent": derivation.get("declared_exponent"),
            "reference": derivation.get("reference"),
        },
    }


def cosmetic_projection(entry: Dict) -> Dict:
    """装饰投影：只有这些字段变了才允许"改注释不报警"。"""
    return {key: entry.get(key) for key in COSMETIC_KEYS if key in entry}


def cosine_of_text(text: Optional[str]) -> Optional[str]:
    return _sha_of_text(text)


__all__ = [
    "SOURCE_TYPES_V2", "ADJUDICABLE_SOURCE_TYPES", "COMPARATORS", "CONSTANT_SYMBOLS", "SOURCE_REF_KINDS",
    "COSMETIC_KEYS", "DESCRIPTIVE_KEYS", "SEMANTIC_KEYS", "ThresholdLawError",
    "compile_threshold_law", "free_symbols", "evaluate_threshold_law",
    "normalize_entry", "validate_entry", "validate_source_ref",
    "adjudicability", "semantic_projection", "cosmetic_projection",
]