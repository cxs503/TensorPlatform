"""定级层：运行结局 + 声明 + 锚点 → 证据等级（策略即数据）。

为什么定级要单独一层
--------------------
自动登记最大的风险不是漏报，而是**高估**：测试跑过了就自动记 REAL，等于把
「有人写了 marker」当成「算法真被验证过」。因此本层默认取最保守口径：

* 只有 ``passed`` 才可能达到**声明的**等级；未声明等级时上限 ``WITHHELD``
  （``undeclared_passed_level``）；
* 声明 ``REAL`` 还要求至少 ``min_anchors`` 个验证锚（指向解析解/文献/基线的
  字符串标识）——没有锚点的通过只是"代码能跑"，不是"算得对"；
* ``failed`` / ``error`` 判 ``MOCK``（声明被自家测试证伪，与平台
  ``capability_ledger_auto.grader`` 的既有口径一致）；
* ``skipped`` / ``xfailed`` / ``xpassed`` / ``not_run`` 一律 ``WITHHELD``；
* 测试挂了 skip/skipif/xfail 修饰符的，等级被压到 ``WITHHELD`` 封顶；
* 源文件缺 sha256（来源不可追溯）的，等级压到 ``WITHHELD``。

上述规则全部写在 :data:`DEFAULT_POLICY` 这份**数据**里，可用 ``--policy``
换一份 JSON 覆盖，不需要改代码分支（策略与机理分离）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .extract import aggregate_outcome
from .model import CapabilityFeature, Grade, LEVEL_RANK, digest, level_rank, norm_level

POLICY_SCHEMA = "capability_discovery.policy.v1"

DEFAULT_POLICY: Dict[str, Any] = {
    "schema": POLICY_SCHEMA,
    "title": "capability auto-discovery grading policy (conservative default)",
    "outcome_levels": {
        "passed": "declared",
        "failed": "MOCK",
        "error": "MOCK",
        "xpassed": "WITHHELD",
        "xfailed": "WITHHELD",
        "skipped": "WITHHELD",
        "not_run": "WITHHELD",
    },
    "undeclared_passed_level": "WITHHELD",
    "require_anchors_for": ["REAL"],
    "min_anchors": 1,
    "modifier_caps": {"skip": "WITHHELD", "skipif": "WITHHELD", "xfail": "WITHHELD"},
    "require_source_sha256": True,
}

_POLICY_KEYS = set(DEFAULT_POLICY)
# 只作文档用途的字段：允许出现，但不参与定级、也不计入策略指纹
_DOC_KEYS = {"title", "note", "notes", "comment"}


def load_policy(path: Optional[Path | str] = None) -> Dict[str, Any]:
    """加载策略：缺省用内置保守口径；文件可覆盖任意字段（深合并一层）。"""
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    if path is None:
        return policy
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"策略文件不存在: {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("策略文件必须是 JSON 对象")
    unknown = set(doc) - _POLICY_KEYS - _DOC_KEYS
    if unknown:
        raise ValueError(
            f"策略含未知字段: {sorted(unknown)}；允许 {sorted(_POLICY_KEYS | _DOC_KEYS)}"
        )
    for key, value in doc.items():
        if isinstance(value, dict) and isinstance(policy.get(key), dict):
            policy[key].update(value)
        else:
            policy[key] = value
    return policy


def policy_digest(policy: Dict[str, Any]) -> str:
    """策略指纹：只覆盖影响定级的字段（文档字段不参与，改注释不换指纹）。"""
    return digest({k: v for k, v in policy.items() if k not in _DOC_KEYS})


def _confidence(feature: CapabilityFeature, level: str) -> float:
    """确定性置信度（启发式，不是概率）：通过率 × 锚点因子 × 等级因子。"""
    total = max(feature.total_tests, 1)
    passed = feature.outcomes.get("passed", 0)
    ratio = passed / total
    anchor_factor = 1.0 if feature.anchors else 0.6
    level_factor = 0.5 + 0.5 * (level_rank(level) / 4.0)
    return round(min(1.0, ratio * anchor_factor * level_factor), 4)


def grade_feature(feature: CapabilityFeature, policy: Optional[Dict[str, Any]] = None) -> Grade:
    """对单个能力特征定级，返回等级 + 逐条理由（可审计）。"""
    pol = policy or DEFAULT_POLICY
    reasons: List[str] = []
    state = aggregate_outcome(feature.outcomes)
    reasons.append(f"outcome_state={state} counts={feature.outcomes}")

    mapping = pol.get("outcome_levels") or {}
    base = str(mapping.get(state, "WITHHELD")).upper()
    if base == "DECLARED":
        if feature.declared_level:
            base = norm_level(feature.declared_level)
            reasons.append(f"declared_level={base} accepted (test passed)")
        else:
            base = norm_level(pol.get("undeclared_passed_level", "WITHHELD"))
            reasons.append("no declared level -> undeclared_passed_level cap")

    level = base
    # 修饰符封顶
    mod_caps = pol.get("modifier_caps") or {}
    test_mods = {m for t in feature.tests for m in t.get("modifiers", [])}
    for mod in sorted(test_mods):
        cap = mod_caps.get(mod)
        if cap and level_rank(level) > level_rank(cap):
            reasons.append(f"modifier '{mod}' caps level {level} -> {cap}")
            level = norm_level(cap)

    # 锚点要求
    need_anchor = {str(x).upper() for x in pol.get("require_anchors_for") or []}
    min_anchors = int(pol.get("min_anchors", 1))
    if level in need_anchor and len(feature.anchors) < min_anchors:
        reasons.append(
            f"{level} requires >= {min_anchors} anchor(s), found {len(feature.anchors)} -> WITHHELD"
        )
        level = "WITHHELD"

    # 来源可追溯性
    if pol.get("require_source_sha256", True):
        missing = [s["path"] for s in feature.sources if not s.get("sha256")]
        if missing and level_rank(level) > LEVEL_RANK["WITHHELD"]:
            reasons.append(f"missing source sha256 for {missing} -> WITHHELD")
            level = "WITHHELD"

    # 声明等级冲突（同一能力被声明成不同等级 = 需人审，压到 WITHHELD 封顶）
    if len(feature.declared_levels_seen) > 1:
        reasons.append(
            f"conflicting declared levels {feature.declared_levels_seen} -> cap WITHHELD"
        )
        if level_rank(level) > LEVEL_RANK["WITHHELD"]:
            level = "WITHHELD"

    if not reasons or level == base:
        reasons.append(f"policy applied: base={base}, final={level}")

    return Grade(
        capability_id=feature.capability_id,
        level=norm_level(level),
        outcome_state=state,
        reasons=reasons,
        confidence=_confidence(feature, level),
        policy_digest=policy_digest(pol),
    )


def grade_features(
    features: List[CapabilityFeature], policy: Optional[Dict[str, Any]] = None
) -> List[Grade]:
    pol = policy or DEFAULT_POLICY
    return [grade_feature(f, pol) for f in features]