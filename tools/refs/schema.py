"""参考值资产模式（refs.schema v1）。

设计要点
--------
1. **溯源分级**：每条参考值必须声明 ``source.type``:
   - ``analytic``     闭式/解析解，可由 ``source.formula`` + ``source.params`` 复算
   - ``standard``     船级社/ITTC/ISO 等规范条款（要求 document + clause）
   - ``literature``   文献表格（要求 doi/isbn/report_no + page/table + sha256_of_scan）
   - ``dataset``      站内既有数据集（要求 path + field + sha256_of_record）
2. **容差**：``tolerance`` = {rel, abs}，判定 ``|calc-ref| <= abs + rel*|ref|``。
3. **覆盖因子**：``coverage_k`` 记录该容差对应的不确定度覆盖倍数（k=1 标准，
   k=2 约 95%）；``analytic`` 类条目容差取 1e-12，k=0（精确值，无不确定度）。
4. **锚定**：``sha256`` 为 ``canonical_payload(entry)`` 的摘要；任何字段被手改而未
   重建语料都会在 ``verify_anchor`` 处被机器抓住（防"数字手抄/事后调数"）。

本模块不依赖第三方库，纯标准库实现，便于在任何 CI 里跑。
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = "1.0"

SOURCE_TYPES = (
    "analytic",     # 可复算的闭式解
    "standard",     # 规范条款
    "literature",   # 文献表格
    "dataset",      # 站内数据集字段
)

#: 各来源类型必须携带的证据字段（缺一即判 INCOMPLETE）
_REQUIRED_SOURCE_FIELDS = {
    "analytic": ("formula", "params"),
    "standard": ("document", "clause"),
    "literature": ("reference", "locator", "sha256_of_scan"),
    "dataset": ("path", "field", "sha256_of_record"),
}

GRADES = ("REAL", "WITHHELD", "STUB")


class CorpusError(ValueError):
    """语料/Schema 校验失败。"""


# ---------------------------------------------------------------------------
# 规范化 + 锚定
# ---------------------------------------------------------------------------
def canonical_payload(entry: Dict[str, Any]) -> Dict[str, Any]:
    """返回参与摘要计算的规范化载荷（剔除 sha256 自身与展示用备注）。"""
    payload = {k: v for k, v in entry.items() if k not in ("sha256", "notes")}
    return payload


def canonical_json(obj: Any) -> str:
    """确定性 JSON 序列化：键排序、无多余空白、浮点用 repr。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(entry: Dict[str, Any]) -> str:
    """条目 sha256 锚。"""
    return hashlib.sha256(canonical_json(canonical_payload(entry)).encode("utf-8")).hexdigest()


def file_digest(path: str) -> str:
    """文件 sha256（用于文献扫描件/数据集锚定）。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def validate_entry(entry: Dict[str, Any]) -> List[str]:
    """校验单条目，返回问题列表（空列表=通过）。"""
    problems: List[str] = []
    for key in ("id", "quantity", "value", "unit", "tolerance", "coverage_k", "source", "grade"):
        if key not in entry:
            problems.append(f"missing field '{key}'")
    if problems:
        return problems

    if not isinstance(entry["id"], str) or not entry["id"]:
        problems.append("id must be a non-empty string")
    if entry["grade"] not in GRADES:
        problems.append(f"grade must be one of {GRADES}, got {entry['grade']!r}")
    if not isinstance(entry["value"], (int, float)) or isinstance(entry["value"], bool):
        problems.append("value must be a number")
    elif math.isnan(float(entry["value"])) or math.isinf(float(entry["value"])):
        problems.append("value must be finite")

    tol = entry["tolerance"]
    if not isinstance(tol, dict) or "rel" not in tol or "abs" not in tol:
        problems.append("tolerance must be {'rel':float,'abs':float}")
    else:
        for k in ("rel", "abs"):
            if not isinstance(tol[k], (int, float)) or tol[k] < 0:
                problems.append(f"tolerance.{k} must be a non-negative number")

    src = entry["source"]
    if not isinstance(src, dict):
        problems.append("source must be an object")
    else:
        stype = src.get("type")
        if stype not in SOURCE_TYPES:
            problems.append(f"source.type must be one of {SOURCE_TYPES}, got {stype!r}")
        else:
            for field in _REQUIRED_SOURCE_FIELDS[stype]:
                if field not in src or src[field] in (None, "", {}):
                    problems.append(f"source.type={stype} requires source.{field}")

    k = entry["coverage_k"]
    if not isinstance(k, (int, float)) or k < 0:
        problems.append("coverage_k must be a non-negative number")
    return problems


def validate_corpus(corpus: Dict[str, Any]) -> List[str]:
    """校验整份语料：模式版本、条目校验、id 唯一、锚点一致。"""
    problems: List[str] = []
    if corpus.get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"schema_version must be {SCHEMA_VERSION!r}, got {corpus.get('schema_version')!r}"
        )
    entries = corpus.get("entries")
    if not isinstance(entries, list) or not entries:
        return problems + ["entries must be a non-empty list"]

    seen = set()
    for entry in entries:
        eid = entry.get("id", "<no-id>")
        if eid in seen:
            problems.append(f"duplicate id {eid!r}")
        seen.add(eid)
        for p in validate_entry(entry):
            problems.append(f"[{eid}] {p}")
        if entry.get("sha256") != digest(entry):
            problems.append(f"[{eid}] sha256 anchor mismatch (entry modified after build?)")
    return problems


# ---------------------------------------------------------------------------
# 容差判定
# ---------------------------------------------------------------------------
def tolerance_band(entry: Dict[str, Any]) -> float:
    """返回该条目的允许偏差（绝对量纲）。"""
    rel = float(entry["tolerance"]["rel"])
    abs_ = float(entry["tolerance"]["abs"])
    return abs_ + rel * abs(float(entry["value"]))


def relative_error(calc: float, ref: float) -> float:
    """相对误差 |calc-ref|/|ref|；ref=0 时退化为绝对差。"""
    if ref == 0:
        return abs(calc - ref)
    return abs(calc - ref) / abs(ref)


def judge(entry: Dict[str, Any], calc: float) -> Dict[str, Any]:
    """把"计算值"与"参考值"比对，返回判定包。"""
    ref = float(entry["value"])
    err_abs = abs(calc - ref)
    err_rel = relative_error(calc, ref)
    band = tolerance_band(entry)
    return {
        "id": entry["id"],
        "quantity": entry["quantity"],
        "unit": entry["unit"],
        "calc": calc,
        "ref": ref,
        "err_abs": err_abs,
        "err_rel": err_rel,
        "band": band,
        "passed": err_abs <= band,
        "grade": entry["grade"],
        "coverage_k": entry["coverage_k"],
    }


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------
def load_corpus(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        corpus = json.load(fh)
    problems = validate_corpus(corpus)
    if problems:
        raise CorpusError("; ".join(problems))
    return corpus


def index_by_id(corpus: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {e["id"]: e for e in corpus["entries"]}


def filter_entries(corpus: Dict[str, Any], quantity: Optional[str] = None) -> Iterable[Dict[str, Any]]:
    for e in corpus["entries"]:
        if quantity is None or e["quantity"] == quantity:
            yield e