#!/usr/bin/env python3
"""机制重放一致性内核：声明式规则表 + 纯函数解释器（零领域语义）。

为什么需要这一层
----------------
平台门禁（能力登记/证据等级/机器门/报告门/合并门/自治提案/战役状态/预算门）此前
只有「事后结论」可查：某条 mission 被门放行或被门拒，判词淹没在业务代码分支里，
**没有任何离线可重放的机制纯函数入口**。于是机制退化（阈值被悄悄放宽、异常被吞、
归约开始依赖输入顺序、二次运行判词漂移、审计字段丢失、短路返回常量）无法在合并前
被发现——只能等线上出现「门变绿但事故发生了」。

本模块把每个机制压成一张**声明式规则表**（:class:`Rule` 元组），由单解释器
:func:`evaluate` 执行，产出可 diff 的结构化判词。规则表同时也是**变异注入的靶面**：
M1 改阈值、M2 改缺字段策略、M3 改集合归约策略、M5 改审计字段。

设计纪律
--------
* **零领域语义**：只认识平台机制字段（判据/证据/预算/审计），不含任何物理域词条。
* **纯函数**：无 I/O、无随机、无 wall clock；同一输入连续两次调用逐字节相同（幂等）。
* **顺序无关**：集合类规则只做计数/极值归约，判词里 reasons/checks 排序后输出；
  输入置换不改变判词（除非机制确有顺序语义——本套 8 个机制均无）。
* **缺字段可分辨**：输入缺字段抛 :class:`RuleInputError`，:func:`judge` 转成
  ``fail + MISSING_FIELD``，这样「吞掉异常」的变异体 M2 一定被判词 diff 抓到。
* **审计可校验**：判词带 ``audit.input_digest``（输入规范 JSON 的 sha256）与
  ``audit.rule_version``，M5（审计字段丢失）因此可机器检出。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

RULES_VERSION = "mech-replay-v1"

# 判词里出现的 diff 理由码，供报告与门禁引用
CODE_MISSING_FIELD = "MISSING_FIELD"
CODE_TYPE_ERROR = "TYPE_ERROR"


class RuleInputError(Exception):
    """输入缺失/不可判定——机制必须显式判 fail（不允许静默放行）。"""

    def __init__(self, path: str, detail: str = "missing"):
        super().__init__(f"{CODE_MISSING_FIELD}: {path} ({detail})")
        self.path = path
        self.detail = detail
        self.code = CODE_MISSING_FIELD


class RuleTypeError(Exception):
    """输入类型不合法（例如比率分母为 0）——与缺字段分开记码，便于审计归类。"""

    def __init__(self, path: str, detail: str = "type"):
        super().__init__(f"{CODE_TYPE_ERROR}: {path} ({detail})")
        self.path = path
        self.detail = detail
        self.code = CODE_TYPE_ERROR


@dataclass(frozen=True)
class Rule:
    """一条机制判据。

    kind 语义
    ---------
    ``enum_in``       字段值 ∈ :attr:`enum`
    ``min_value``     数值 ≥ threshold（direction=min）
    ``max_value``     数值 ≤ threshold（direction=max）
    ``min_count``     len(集合) ≥ threshold（direction=min）
    ``count_true``    映射/列表里 True 的个数 ≥ threshold（direction=min）
    ``max_ratio``     record[field] / record[field2] ≤ threshold（direction=max）
    ``presence``      bool(值) 为真
    ``all_false``     映射里所有值均为假
    ``eq_zero``       数值 == 0（direction=max，放宽方向无意义，仍按 max 记录）
    ``item_value_min`` 集合内「满足 item_when 的元素」的 item_key ≥ threshold
    ``item_value_max`` 集合内某 item_key 的最大值 ≤ threshold
    ``item_enum``     集合内某 item_key 的取值 ∈ :attr:`enum`
    """

    id: str
    kind: str
    field: str
    threshold: float | None = None
    direction: str = "none"  # min | max | none
    detail: str = ""
    when: tuple[str, Any] | None = None
    field2: str | None = None
    item_key: str | None = None
    item_when: tuple[str, Any] | None = None
    enum: tuple[str, ...] = ()


def canon(payload: Any) -> str:
    """规范 JSON：排序键 + 紧凑分隔符 + 9 位浮点截断，保证跨进程逐字节可比。"""
    return json.dumps(_normalize(payload), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def _normalize(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, Mapping):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def digest(payload: Any) -> str:
    return hashlib.sha256(canon(payload).encode("utf-8")).hexdigest()


def pair_signature(verdict: Mapping[str, Any]) -> str:
    """顺序置换对的比较口径：决策 + 理由码 + 审计面（剔除 input_digest）。

    置换会改变 ``audit.input_digest``（输入字节确实变了），所以它不能进比较口径；
    真正必须稳定的是「结论 + 理由 + 其余审计字段」。
    """
    audit = {k: v for k, v in (verdict.get("audit") or {}).items() if k != "input_digest"}
    return canon({"decision": verdict.get("decision"),
                  "codes": sorted({str(r.get("code")) for r in verdict.get("reasons") or []}),
                  "audit": audit,
                  "checks": verdict.get("checks") or []})


def get(record: Mapping[str, Any], path: str) -> Any:
    """按点分路径取值；缺失即抛 RuleInputError（绝不返回 None 静默放行）。"""
    cur: Any = record
    for part in path.split("."):
        if isinstance(cur, Mapping):
            if part not in cur:
                raise RuleInputError(path, f"absent segment '{part}'")
            cur = cur[part]
        elif isinstance(cur, Sequence) and not isinstance(cur, (str, bytes)):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                raise RuleInputError(path, f"bad index '{part}'") from None
        else:
            raise RuleInputError(path, "non-container")
    return cur


def _reduce(items: list[Any], reduce_policy: str) -> list[Any]:
    """集合归约策略。aggregate=顺序无关全量归约；first/last 仅供变异体注入。"""
    if reduce_policy == "aggregate" or len(items) <= 1:
        return list(items)
    if reduce_policy == "first":
        return [items[0]]
    if reduce_policy == "last":
        return [items[-1]]
    raise ValueError(f"unknown reduce_policy: {reduce_policy}")


def _as_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleTypeError(path, f"not numeric: {type(value).__name__}")
    return float(value)


def _as_mapping(value: Any, path: str) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        raise RuleTypeError(path, f"not a mapping: {type(value).__name__}")
    return dict(value)


def _item_value(item_map: Mapping[str, Any], key: str, path: str) -> Any:
    """集合元素内的必填键：缺失抛 RuleInputError（缺字段），存在但非数值才抛 TYPE_ERROR。"""
    if key not in item_map or item_map[key] is None:
        raise RuleInputError(f"{path}.{key}", "absent item key")
    return item_map[key]


def _as_items(value: Any, path: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RuleTypeError(path, f"not a sequence: {type(value).__name__}")
    return list(value)


def _apply(rule: Rule, record: Mapping[str, Any], reduce_policy: str) -> tuple[bool, Any]:
    """执行一条判据，返回 (ok, observed)。observed 进判词审计，便于人读 diff。"""
    kind = rule.kind
    if kind == "enum_in":
        value = get(record, rule.field)
        return (str(value) in rule.enum, str(value))
    if kind in ("min_value", "max_value"):
        value = _as_number(get(record, rule.field), rule.field)
        assert rule.threshold is not None
        ok = value >= rule.threshold if kind == "min_value" else value <= rule.threshold
        return ok, value
    if kind == "min_count":
        items = _as_items(get(record, rule.field), rule.field)
        items = _reduce(items, reduce_policy)
        assert rule.threshold is not None
        return len(items) >= rule.threshold, len(items)
    if kind == "count_true":
        mapping = _as_mapping(get(record, rule.field), rule.field)
        names = sorted(mapping)
        kept = _reduce(names, reduce_policy)
        trues = sum(1 for n in kept if bool(mapping[n]))
        assert rule.threshold is not None
        return trues >= rule.threshold, trues
    if kind == "max_ratio":
        if not rule.field2:
            raise RuleTypeError(rule.field, "max_ratio needs field2")
        num = _as_number(get(record, rule.field), rule.field)
        den = _as_number(get(record, rule.field2), rule.field2)
        if den == 0:
            raise RuleTypeError(rule.field2, "division by zero")
        ratio = num / den
        assert rule.threshold is not None
        return ratio <= rule.threshold, ratio
    if kind == "presence":
        value = get(record, rule.field)
        return bool(value), bool(value)
    if kind == "all_false":
        mapping = _as_mapping(get(record, rule.field), rule.field)
        names = sorted(mapping)
        kept = _reduce(names, reduce_policy)
        hits = [n for n in kept if bool(mapping[n])]
        return not hits, sorted(hits)
    if kind == "eq_zero":
        value = _as_number(get(record, rule.field), rule.field)
        return value == 0, value
    if kind == "item_value_min":
        items = _reduce(_as_items(get(record, rule.field), rule.field), reduce_policy)
        if not rule.item_key:
            raise RuleTypeError(rule.field, "item_value_min needs item_key")
        picked, observed = [], []
        for item in items:
            item_map = _as_mapping(item, rule.field)
            if rule.item_when and str(item_map.get(rule.item_when[0])) != str(rule.item_when[1]):
                continue
            raw = _item_value(item_map, rule.item_key, rule.field)
            picked.append(_as_number(raw, f"{rule.field}[].{rule.item_key}"))
            observed.append(_as_number(raw, f"{rule.field}[].{rule.item_key}"))
        assert rule.threshold is not None
        if not picked:  # 无适用元素：判据空真，但要留痕
            return True, {"applicable": 0}
        return min(picked) >= rule.threshold, {"applicable": len(picked), "min": min(observed)}
    if kind == "item_value_max":
        items = _reduce(_as_items(get(record, rule.field), rule.field), reduce_policy)
        if not rule.item_key:
            raise RuleTypeError(rule.field, "item_value_max needs item_key")
        values = [_as_number(_item_value(_as_mapping(i, rule.field), rule.item_key, rule.field),
                             f"{rule.field}[].{rule.item_key}") for i in items]
        assert rule.threshold is not None
        return max(values) <= rule.threshold, max(values)
    if kind == "item_enum":
        items = _reduce(_as_items(get(record, rule.field), rule.field), reduce_policy)
        if not rule.item_key:
            raise RuleTypeError(rule.field, "item_enum needs item_key")
        values = sorted({str(_item_value(_as_mapping(i, rule.field), rule.item_key, rule.field))
                         for i in items})
        bad = [v for v in values if v not in rule.enum]
        return (not bad), {"values": values, "bad": bad}
    if kind == "criteria_satisfied":
        items = _reduce(_as_items(get(record, rule.field), rule.field), reduce_policy)
        tol = _as_number(get(record, rule.field2 or "tol_rel"), rule.field2 or "tol_rel")
        failed, margins = [], []
        for item in items:
            item_map = _as_mapping(item, rule.field)
            cid = str(item_map.get("id", len(margins)))
            measured = _as_number(_item_value(item_map, "measured", rule.field),
                                  f"{rule.field}[].measured")
            threshold = _as_number(_item_value(item_map, "threshold", rule.field),
                                   f"{rule.field}[].threshold")
            direction = str(item_map.get("direction", "max"))
            if direction == "max":
                ok = measured <= threshold * (1.0 + tol)
                margin = (threshold * (1.0 + tol) - measured) / threshold if threshold else 0.0
            elif direction == "min":
                ok = measured >= threshold * (1.0 - tol)
                margin = (measured - threshold * (1.0 - tol)) / threshold if threshold else 0.0
            else:
                raise RuleTypeError(f"{rule.field}[].direction", direction)
            margins.append(round(margin, 9))
            if not ok:
                failed.append(cid)
        return not failed, {"failed": sorted(failed), "evaluated": len(items),
                            "min_margin": min(margins) if margins else None}
    raise ValueError(f"unknown rule kind: {kind}")


def evaluate(
    mechanism: str,
    record: Mapping[str, Any],
    rules: Iterable[Rule],
    *,
    missing_policy: str = "fail",
    reduce_policy: str = "aggregate",
    extras: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """解释执行规则表，返回结构化判词。

    ``missing_policy="fail"``（默认）是机制的正确行为；``"pass"`` 只被变异体 M2 使用，
    用来模拟「异常被吞掉」的退化。
    """
    rules = tuple(rules)
    checks: list[dict[str, Any]] = []
    reasons: list[dict[str, str]] = []
    skipped = 0
    for rule in rules:
        if rule.when is not None:
            cond_value = get(record, rule.when[0])
            if str(cond_value) != str(rule.when[1]):
                skipped += 1
                checks.append({"id": rule.id, "status": "skipped",
                               "observed": str(cond_value)})
                continue
        try:
            ok, observed = _apply(rule, record, reduce_policy)
        except (RuleInputError, RuleTypeError) as exc:
            if missing_policy == "pass":
                skipped += 1
                checks.append({"id": rule.id, "status": "swallowed",
                               "observed": exc.code})
                continue
            checks.append({"id": rule.id, "status": "error", "observed": exc.code})
            reasons.append({"code": exc.code, "rule": rule.id, "path": getattr(exc, "path", "")})
            # 一条输入错误即机械不可判定 → 直接判 fail（不再继续猜测其余判据）
            return _verdict(mechanism, record, checks, reasons, skipped, rules,
                            extras=extras)
        checks.append({"id": rule.id, "status": "pass" if ok else "fail",
                       "observed": observed, "threshold": rule.threshold})
        if not ok:
            reasons.append({"code": rule.id, "rule": rule.id, "path": rule.field})
    return _verdict(mechanism, record, checks, reasons, skipped, rules, extras=extras)


def _verdict(
    mechanism: str,
    record: Mapping[str, Any],
    checks: list[dict[str, Any]],
    reasons: list[dict[str, str]],
    skipped: int,
    rules: Sequence[Rule],
    extras: Mapping[str, Any] | None,
) -> dict[str, Any]:
    checks.sort(key=lambda c: c["id"])
    reasons.sort(key=lambda r: (r["code"], r["rule"]))
    audit: dict[str, Any] = {
        "mechanism": mechanism,
        "rule_version": RULES_VERSION,
        "rules_sha256": digest([r.id for r in rules]),
        "input_digest": digest(record),
        "checks_total": len(checks),
        "checks_failed": sum(1 for c in checks if c["status"] == "fail"),
        "checks_skipped": skipped,
    }
    if extras:
        audit.update(extras)
    return {
        "mechanism": mechanism,
        "decision": "fail" if reasons else "pass",
        "reasons": reasons,
        "checks": checks,
        "audit": audit,
    }