"""战役节点 metadata 契约验证器（必填字段校验 + 缺失恒等降级不崩）。

契约（字段名与语义一律来自配置 ``node_schema``）
------------------------------------------------
必填：``ext_id`` / ``repo`` / ``goal`` / ``acceptance_criteria``。
“填了”的定义（机器可裁，不做语义猜测）：

* ``ext_id``：非空字符串（数字亦可，转 str 后非空）
* ``repo``：非空字符串
* ``goal``：非空字符串（去空白后长度 ≥1）
* ``acceptance_criteria``：非空 str / 非空 list / 非空 dict（任一形态皆合法，
  因为历史节点混用「一段话」「判据列表」「{metric: target} 映射」三种写法）

降级（identity-degrade）语义
----------------------------
* 缺字段 **不抛异常、不丢节点**：用配置里的 ``degrade_defaults`` 补齐，
  记录 ``missing`` / ``degraded`` / ``warnings``，节点仍可进入下游（覆盖率账本、去重）。
* 无 ``metadata``（None / 非 dict / JSON 坏串）→ 整节点降级，``missing`` 记全部必填项。
* 这是「fail-soft 读侧」：写侧（播种）仍应 fail-closed，两侧语义不同、不可混用。

漏报率的机验
------------
:func:`validate_batch` 同时跑两条独立实现：
``contract``（本模块，走别名映射与类型规则）与 ``oracle``（最朴素的
``key exists and truthy`` 直查，见 :func:`oracle_missing`）。
**漏报** = oracle 认为缺、contract 认为齐 → 记入 ``missed``。两者判定同一份必填清单，
所以漏报率应恒为 0；一旦非 0 说明别名/类型规则把缺失的字段“洗白”了，是回归信号。

``known_key_typos``（如 ``is_marity``→``is_maturity``）只产生 warning，不影响必填判定。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

_TYPO_HINT_LIMIT = 5


@dataclass(frozen=True)
class NodeValidation:
    node_id: str
    ok: bool
    missing: tuple[str, ...] = ()
    degraded: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    filled: dict[str, Any] = field(default_factory=dict)
    action: str = "accept"

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "ok": self.ok,
            "action": self.action,
            "missing": list(self.missing),
            "degraded": list(self.degraded),
            "warnings": list(self.warnings),
        }


def _as_metadata(value: Any) -> tuple[dict[str, Any], tuple[str, ...]]:
    """把 metadata 归一成 dict；不可解析返回空 dict + warning（不抛异常）。"""
    warnings: list[str] = []
    if value is None:
        warnings.append("metadata_missing")
        return {}, tuple(warnings)
    if isinstance(value, Mapping):
        return dict(value), ()
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            warnings.append("metadata_unparsable")
            return {}, tuple(warnings)
        if isinstance(parsed, Mapping):
            return dict(parsed), ()
        warnings.append("metadata_not_object")
        return {}, tuple(warnings)
    warnings.append("metadata_not_object")
    return {}, tuple(warnings)


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def resolve_field(meta: Mapping[str, Any], field_name: str, aliases: Sequence[str]) -> Any:
    """按「原名优先、别名次之」取值（保序，命中第一个 present 的）。"""
    for key in (field_name, *[a for a in aliases if a != field_name]):
        if key in meta and _present(meta[key]):
            return meta[key]
    return None


def oracle_missing(meta: Mapping[str, Any], required: Sequence[str]) -> set[str]:
    """独立实现（最朴素直查）：必填键必须存在且 truthy。"""
    return {f for f in required if not _present(meta.get(f))}


def validate_node(node: Any, cfg: Mapping[str, Any]) -> NodeValidation:
    """校验单个战役节点（``node`` 可以是 dict / 带 metadata 的对象）。"""
    spec = dict(cfg.get("node_schema", {}) or {})
    required = [str(f) for f in spec.get("required_fields", [])]
    aliases = {str(k): [str(a) for a in v] for k, v in (spec.get("field_aliases", {}) or {}).items()}
    defaults = dict(spec.get("degrade_defaults", {}) or {})
    typos = dict(spec.get("known_key_typos", {}) or {})

    payload = dict(node) if isinstance(node, Mapping) else {}
    node_id = str(
        payload.get("id")
        or payload.get("node_id")
        or payload.get("ext_id")
        or "__unknown_node__"
    )
    meta, warnings = _as_metadata(payload.get("metadata"))
    # 允许 metadata 平铺在节点本身（历史节点有这种形态）
    for req in required:
        if req not in meta and req in payload:
            meta[req] = payload[req]

    missing: list[str] = []
    degraded: list[str] = []
    filled: dict[str, Any] = {}
    for req in required:
        value = resolve_field(meta, req, aliases.get(req, []))
        if value is None:
            missing.append(req)
            degraded.append(req)
            filled[req] = defaults.get(req)
        else:
            filled[req] = value

    warn: list[str] = []
    for wrong, right in typos.items():
        if wrong in meta:
            warn.append(f"key_typo:{wrong}->{right}")
    if payload.get("id") is None and filled.get("ext_id") is None:
        warn.append("no_identity")  # ext_id 与节点 id 双缺 → 无法回链（仍降级不崩）

    return NodeValidation(
        node_id=node_id,
        ok=not missing,
        missing=tuple(missing),
        degraded=tuple(degraded),
        warnings=tuple(list(warnings) + warn),
        filled=filled,
        action="accept" if not missing else "degrade",
    )


@dataclass
class BatchReport:
    total: int
    ok_count: int
    degraded_count: int
    missing_field_counts: dict[str, int]
    missed_by_oracle: list[str]
    field_fill_rate: dict[str, float]
    warning_counts: dict[str, int]
    quality: dict[str, int]
    records: list[NodeValidation]

    @property
    def missed_count(self) -> int:
        return len(self.missed_by_oracle)

    @property
    def miss_rate(self) -> float:
        return self.missed_count / self.total if self.total else 0.0

    def as_dict(self, *, include_records: bool = True) -> dict[str, Any]:
        return {
            "total": self.total,
            "ok_count": self.ok_count,
            "degraded_count": self.degraded_count,
            "degraded_ratio": round(self.degraded_count / self.total, 6) if self.total else 0.0,
            "missing_field_counts": dict(sorted(self.missing_field_counts.items())),
            "field_fill_rate": {k: round(v, 6) for k, v in sorted(self.field_fill_rate.items())},
            "missed_by_oracle": list(self.missed_by_oracle),
            "missed_count": self.missed_count,
            "miss_rate": round(self.miss_rate, 6),
            "warning_counts": dict(sorted(self.warning_counts.items())),
            "quality": dict(sorted(self.quality.items())),
            "records": [r.as_dict() for r in self.records] if include_records else [],
        }


def _quality_flags(filled: Mapping[str, Any], cfg: Mapping[str, Any]) -> list[str]:
    """非阻断质量标记（不进 missing，只做提醒）：仓白名单/判据可裁性。"""
    spec = dict(cfg.get("node_schema", {}) or {})
    num = re.compile(str(spec.get("numeric_pattern", r"\d")))
    comp = re.compile(str(spec.get("comparator_pattern", r"≥|≤|>|<|=|%>")))
    whitelist = {str(r) for r in (cfg.get("repo_whitelist", []) or [])}
    flags: list[str] = []
    repo = filled.get("repo")
    if isinstance(repo, str) and repo not in whitelist:
        flags.append("repo_not_in_whitelist")
    crit = filled.get("acceptance_criteria")
    text = crit if isinstance(crit, str) else json.dumps(crit, ensure_ascii=False) if crit else ""
    if crit and not (num.search(text) and comp.search(text)):
        flags.append("criteria_without_numeric_threshold")
    return flags


def validate_batch(nodes: Iterable[Any], cfg: Mapping[str, Any]) -> BatchReport:
    """批量校验 + 与独立 oracle 对账（漏报率机验）。"""
    spec = dict(cfg.get("node_schema", {}) or {})
    required = [str(f) for f in spec.get("required_fields", [])]
    records: list[NodeValidation] = []
    missing_counts: dict[str, int] = {f: 0 for f in required}
    warning_counts: dict[str, int] = {}
    quality: dict[str, int] = {}
    missed: list[str] = []
    filled_counts: dict[str, int] = {f: 0 for f in required}
    total = 0

    for node in nodes:
        total += 1
        rec = validate_node(node, cfg)
        records.append(rec)
        for f in rec.missing:
            missing_counts[f] = missing_counts.get(f, 0) + 1
        for f, v in rec.filled.items():
            if _present(v) and f not in rec.missing:
                filled_counts[f] = filled_counts.get(f, 0) + 1
        for w in rec.warnings:
            warning_counts[w] = warning_counts.get(w, 0) + 1
        for q in _quality_flags(rec.filled, cfg):
            quality[q] = quality.get(q, 0) + 1
        # 独立 oracle 对账
        payload = dict(node) if isinstance(node, Mapping) else {}
        meta, _ = _as_metadata(payload.get("metadata"))
        for req in required:
            if req not in meta and req in payload:
                meta[req] = payload[req]
        oracle = oracle_missing(meta, required)
        missed_here = oracle - set(rec.missing)
        if missed_here:
            missed.append(f"{rec.node_id}:{','.join(sorted(missed_here))}")

    fill_rate = {
        f: (filled_counts.get(f, 0) / total if total else 0.0) for f in required
    }
    return BatchReport(
        total=total,
        ok_count=sum(1 for r in records if r.ok),
        degraded_count=sum(1 for r in records if not r.ok),
        missing_field_counts=missing_counts,
        missed_by_oracle=missed,
        field_fill_rate=fill_rate,
        warning_counts=warning_counts,
        quality=quality,
        records=records,
    )