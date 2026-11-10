#!/usr/bin/env python3
"""8 个核心平台机制的声明式规则表（零领域语义）+ 纯函数判词入口。

覆盖清单（对表 ECOSYSTEM-MAP「平台只做共性机制」铁律，8 个机制均为**机制级**，
不含任何船舶物理词条）：

============  ==========================================================
机制            本轮守的是什么门
============  ==========================================================
capability_registration  能力登记：证据等级枚举合法 + REAL 档必须带齐工件/测试/文档
evidence_grade           证据等级：REAL 声明须有足够多独立验证证据与参考锚
machine_gate             机器门：判据条数/容差/重复运行稳定 + 逐条判据达标
report_gate              报告门：六要素摘要/图或豁免/误差结论/honest-FAIL 声明
merge_gate               合并门：CI 绿 + 评审数 + 冲突数 + 守卫风险 + 改动面
autonomy_proposal        自治提案：三重检索 + 新颖锚 + 机器可算判据 + 不越红线
campaign_status          战役状态：节点状态枚举 + 连续失败上限 + verified 证据密度
budget_gate              预算门：token/并发/wall 三个比率闸
============  ==========================================================

每条规则的 ``direction`` 标注阈值放宽方向，供变异体 M1（阈值放宽 20%）机械注入：
``min`` → 放宽 = 阈值 ÷ 1.2；``max`` → 放宽 = 阈值 × 1.2。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from tools.mech_replay import spec
from tools.mech_replay.spec import Rule, RuleTypeError

GRADES = ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED")
NODE_STATUSES = ("pending", "in_flight", "done", "verified", "blocked", "failed")


def _audit_campaign(record: Mapping[str, Any]) -> dict[str, Any]:
    """战役状态派生（顺序无关的优先级归约），仅作审计留痕，不参与判词。"""
    try:
        nodes = record["nodes"]
        statuses = [str(n.get("status")) for n in nodes]
    except (KeyError, TypeError):
        return {}
    for status in ("verified", "done", "in_flight", "blocked", "failed", "pending"):
        if status in statuses:
            return {"derived_status": status, "node_count": len(statuses)}
    return {"derived_status": "unknown", "node_count": len(statuses)}


def _audit_budget(record: Mapping[str, Any]) -> dict[str, Any]:
    try:
        tokens = round(float(record["spent_tokens"]) / float(record["budget_tokens"]), 6)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return {}
    return {"token_utilization": tokens}


def _audit_machine(record: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return {"criteria_count": len(record["criteria"]), "tol_rel": record["tol_rel"]}
    except (KeyError, TypeError):
        return {}


def _audit_report(record: Mapping[str, Any]) -> dict[str, Any]:
    try:
        sections = record["sections"]
        return {"sections_true": sum(1 for v in sections.values() if v),
                "figure_count": len(record.get("figures") or [])}
    except (KeyError, TypeError, AttributeError):
        return {}


RULES: dict[str, tuple[Rule, ...]] = {
    "capability_registration": (
        Rule("CAP_GRADE_ENUM", "enum_in", "evidence_grade", enum=GRADES,
             detail="证据等级必须落在 REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED 枚举内"),
        Rule("CAP_ARTIFACTS", "min_count", "artifacts", threshold=3, direction="min",
             when=("evidence_grade", "REAL"), detail="REAL 档须 ≥3 个可点验工件"),
        Rule("CAP_TESTS", "min_value", "tests", threshold=12, direction="min",
             when=("evidence_grade", "REAL"), detail="REAL 档须 ≥12 个 pytest 用例"),
        Rule("CAP_DOCS", "presence", "docs", when=("evidence_grade", "REAL"),
             detail="REAL 档须有文档工件"),
        Rule("CAP_NS_NO_ARTIFACT", "eq_zero", "artifact_count", direction="max",
             when=("evidence_grade", "NOT_SUPPORTED"),
             detail="NOT_SUPPORTED 不得挂工件（否则是误分级）"),
    ),
    "evidence_grade": (
        Rule("EG_GRADE_ENUM", "enum_in", "declared_grade", enum=GRADES,
             detail="声明等级必须合法"),
        Rule("EG_VERIFIED", "count_true", "evidence_verified", threshold=6, direction="min",
             when=("declared_grade", "REAL"), detail="REAL 须 ≥6 条已验证证据"),
        Rule("EG_KINDS", "min_count", "evidence_kinds", threshold=3, direction="min",
             when=("declared_grade", "REAL"), detail="REAL 须 ≥3 类独立证据"),
        Rule("EG_ANCHOR", "presence", "reference_anchor", when=("declared_grade", "REAL"),
             detail="REAL 须有参考锚（文献/解析解/实验）"),
        Rule("EG_INDEP", "presence", "independent_reimpl", when=("declared_grade", "REAL"),
             detail="REAL 须有独立第二实现或等价交叉验证"),
        Rule("EG_NS_EMPTY", "eq_zero", "artifact_count", direction="max",
             when=("declared_grade", "NOT_SUPPORTED"),
             detail="NOT_SUPPORTED 不得挂工件"),
    ),
    "machine_gate": (
        Rule("MG_MIN_CRITERIA", "min_count", "criteria", threshold=6, direction="min",
             detail="机器门须 ≥6 条机器可算判据"),
        Rule("MG_TOL", "max_value", "tol_rel", threshold=0.05, direction="max",
             detail="判据容差相对值 ≤5%"),
        Rule("MG_RUNS", "min_value", "runs", threshold=6, direction="min",
             detail="判据须 ≥6 次独立运行复现"),
        Rule("MG_CRITERIA", "criteria_satisfied", "criteria", field2="tol_rel",
             detail="逐条判据在容差内达标"),
    ),
    "report_gate": (
        Rule("RG_TITLE", "presence", "title", detail="标题必填"),
        Rule("RG_SUMMARY", "min_value", "summary_length", threshold=40, direction="min",
             detail="摘要 ≥40 字符"),
        Rule("RG_SECTIONS", "count_true", "sections", threshold=6, direction="min",
             detail="六要素齐全（背景/方法/结果/图/注意点/误差判定）"),
        Rule("RG_FIGURES", "min_count", "figures", threshold=1, direction="min",
             when=("figure_waiver_reason", ""), detail="至少 1 张图，或写明豁免理由"),
        Rule("RG_ERROR_ANSWER", "presence", "error_answer",
             detail="必须显式回答关键量误差是否达标"),
        Rule("RG_HONEST_FAIL", "presence", "honest_fail", when=("status", "FAIL"),
             detail="FAIL 结论必须带 honest_fail 声明"),
    ),
    "merge_gate": (
        Rule("MR_CI", "presence", "ci_green", detail="CI 必须绿"),
        Rule("MR_APPROVALS", "min_value", "approvals", threshold=6, direction="min",
             detail="独立评审 ≥6"),
        Rule("MR_CONFLICTS", "eq_zero", "conflicts", direction="max",
             detail="合并冲突数必须为 0"),
        Rule("MR_GUARD", "max_value", "guard_risk", threshold=0.05, direction="max",
             detail="守卫扫描风险 ≤5%"),
        Rule("MR_CHANGED", "max_value", "changed_files", threshold=400, direction="max",
             detail="单次合并改动面 ≤400 文件"),
        Rule("MR_BASE", "presence", "base_branch_ok", detail="基线分支正确"),
    ),
    "autonomy_proposal": (
        Rule("AP_TITLE", "presence", "proposal_title", detail="提案须有标题"),
        Rule("AP_PRIOR_ART", "min_value", "prior_art_checks", threshold=6, direction="min",
             detail="三重检索（节点/提案池/仓内 grep）≥6 条前置检查"),
        Rule("AP_ANCHORS", "min_count", "novelty_anchors", threshold=1, direction="min",
             detail="须给出 ≥1 个新颖性锚点"),
        Rule("AP_CRITERIA", "min_count", "criteria", threshold=2, direction="min",
             detail="须有 ≥2 条机器可算判据"),
        Rule("AP_REDLINE", "all_false", "redline_flags",
             detail="战略级红线不得被触碰"),
    ),
    "campaign_status": (
        Rule("CS_NODES", "min_count", "nodes", threshold=1, direction="min",
             detail="战役至少 1 个节点"),
        Rule("CS_STATUS_ENUM", "item_enum", "nodes", item_key="status", enum=NODE_STATUSES,
             detail="节点状态必须落在枚举内"),
        Rule("CS_MAX_FAILS", "item_value_max", "nodes", item_key="consecutive_fails",
             threshold=4, direction="max", detail="单节点连续失败 ≤4"),
        Rule("CS_VERIFIED_EVIDENCE", "item_value_min", "nodes", item_key="evidence_count",
             threshold=6, direction="min", item_when=("status", "verified"),
             detail="verified 节点须 ≥6 条证据支撑"),
    ),
    "budget_gate": (
        Rule("BG_TOKEN_RATIO", "max_ratio", "spent_tokens", field2="budget_tokens",
             threshold=0.9, direction="max", detail="token 消耗率 ≤90%"),
        Rule("BG_CONCURRENCY", "max_ratio", "concurrent_jobs", field2="max_jobs",
             threshold=1.0, direction="max", detail="并发不超配"),
        Rule("BG_WALL_RATIO", "max_ratio", "wall_used", field2="wall_limit",
             threshold=1.05, direction="max", detail="wall clock 不超时 5%"),
        Rule("BG_BUDGET_MIN", "min_value", "budget_tokens", threshold=1000, direction="min",
             detail="预算额度须 ≥1000 token（防零预算空转）"),
        Rule("BG_SPENT_NONNEG", "min_value", "spent_tokens", threshold=0, direction="min",
             detail="消耗量非负"),
    ),
}

TITLES: dict[str, str] = {
    "capability_registration": "能力登记门",
    "evidence_grade": "证据等级门",
    "machine_gate": "机器门",
    "report_gate": "报告门",
    "merge_gate": "合并门",
    "autonomy_proposal": "自治提案门",
    "campaign_status": "战役状态机制",
    "budget_gate": "预算门",
}

EXTRAS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "capability_registration": lambda r: {},
    "evidence_grade": lambda r: {},
    "machine_gate": _audit_machine,
    "report_gate": _audit_report,
    "merge_gate": lambda r: {},
    "autonomy_proposal": lambda r: {},
    "campaign_status": _audit_campaign,
    "budget_gate": _audit_budget,
}

MECHANISMS: tuple[str, ...] = tuple(RULES)


def judge(
    mechanism: str,
    record: Mapping[str, Any],
    *,
    rules: tuple[Rule, ...] | None = None,
    missing_policy: str = "fail",
    reduce_policy: str = "aggregate",
) -> dict[str, Any]:
    """机制纯函数入口：输入即记录（不连库），输出结构化判词。"""
    if mechanism not in RULES:
        raise KeyError(f"unknown mechanism: {mechanism}")
    return spec.evaluate(
        mechanism,
        record,
        RULES[mechanism] if rules is None else rules,
        missing_policy=missing_policy,
        reduce_policy=reduce_policy,
        extras=EXTRAS[mechanism](record),
    )


def numeric_rules(mechanism: str) -> tuple[Rule, ...]:
    """带可放宽数值阈值的规则（M1 变异的面）。"""
    return tuple(r for r in RULES[mechanism] if r.threshold is not None and r.direction in ("min", "max"))


def metadata() -> dict[str, Any]:
    out = {}
    for name in MECHANISMS:
        out[name] = {
            "title": TITLES[name],
            "rule_count": len(RULES[name]),
            "numeric_rules": [r.id for r in numeric_rules(name)],
            "rule_ids": [r.id for r in RULES[name]],
            "rule_version": spec.RULES_VERSION,
        }
    return out


__all__ = ["GRADES", "NODE_STATUSES", "RULES", "MECHANISMS", "TITLES", "judge",
           "metadata", "numeric_rules", "Rule", "RuleTypeError"]