#!/usr/bin/env python3
"""成果登记质量门 —— E 级强制（SPEC v1.2 §7.1 规则 8/9 的机器落点）。

定位：平台只提供「等级结构 + 记录 + 审查流程」的通用机制（SPEC §6.3
「领域专业质量门由领域系统维护；平台只负责提供通用接入、记录和执行机制」）。
因此本模块**零领域语义**：不含任何领域词表/领域判据，等级的领域含义由领域
验证器注入（§6.2）；测试夹具一律使用合成域名（synthetic-alpha 等）。

硬判据（任一 FAIL = 整体 FAIL，阻断登记；门可以失败）：
  G1 任何等级成果（S1–S5 / S-N）登记至少要求 E≥1，且携带可检查制品。
     §7.1-8「任何等级的成果登记至少要求 E1。纯声明（E0）不足以登记 S1–S5」。
  G2 申报 S5 须 E≥3（v1.2 收紧条款，§7.1-9「S5 须 E≥3 方可作为已确认
     创造性成果」）。S5/E0–E1 的「候选」通道因此关闭，等待补齐结果验证后
     以新记录重登记（不覆盖旧判决，见 QUALITY_BAR R5）。

附加判据（同源于 SPEC，随门常驻，同样可失败）：
  G3 等级必须附理由：无理由等级 = 纯声明层，不可登记（§7.1-1/-4）。
  G4 申报 S4「已确认」须 E≥2 且附对照基线（§9「S4 须 E≥2 且附对照基线」）；
     不满足时可（且只能）登记为候选。
  G5 申报 S5 须声明新颖性检索范围（§7.1 S5 最低评价依据「说明发现来源与
     新颖性检索范围」）。
  G6 否定性成果 S-N 登记须 E≥2（§7.1「S-N 登记须满足 E≥2」）。

统计口径（§7.1-9）：非法 S×E 组合不计入成果统计——`countable` 仅在
整体 PASS 且非候选时为真。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --- 等级空间（SPEC §6.2 / §7.1 的标签集合，不含领域含义） -------------------
E_LEVELS: Tuple[str, ...] = ("E0", "E1", "E2", "E3", "E4")
E_RANK: Dict[str, int] = {lv: i for i, lv in enumerate(E_LEVELS)}
POSITIVE_GRADES: Tuple[str, ...] = ("S1", "S2", "S3", "S4", "S5")
NEGATIVE_GRADE = "S-N"
GRADES: Tuple[str, ...] = POSITIVE_GRADES + (NEGATIVE_GRADE,)

#: 等级理由的最小长度（低于此长度视为「无理由」）——纯形式判据，无领域语义。
MIN_REASON_CHARS = 12
#: 新颖性检索范围声明的最小长度。
MIN_NOVELTY_CHARS = 8

PASS = "PASS"
FAIL = "FAIL"

#: 分类标签：REJECTED（不可登记）/ CANDIDATE（候选，不计成果统计）/ CONFIRMED
REJECTED = "REJECTED"
CANDIDATE = "CANDIDATE"
CONFIRMED = "CONFIRMED"


class DeclarationError(ValueError):
    """非法的成果声明（等级标签/证据标签不在 SPEC 标签集合内）。"""


@dataclass(frozen=True)
class OutcomeDeclaration:
    """一次成果登记声明。字段与 SPEC §7.1 评价记录字段一一对应。"""

    outcome_id: str
    mission_id: str
    grade: str
    evidence_level: str
    artifacts: Tuple[str, ...] = ()
    reason: str = ""
    baseline: Optional[str] = None
    novelty_search_scope: Optional[str] = None
    declared_confirmed: bool = True
    #: 领域标签只做记录用途（合成域名），不参与任何判据分支 → 零领域语义。
    domain: str = "synthetic-alpha"

    def __post_init__(self) -> None:
        if self.grade not in GRADES:
            raise DeclarationError(f"unknown grade {self.grade!r}; allowed={GRADES}")
        if self.evidence_level not in E_LEVELS:
            raise DeclarationError(
                f"unknown evidence level {self.evidence_level!r}; allowed={E_LEVELS}")
        if not str(self.outcome_id).strip() or not str(self.mission_id).strip():
            raise DeclarationError("outcome_id and mission_id are required")
        object.__setattr__(self, "artifacts", tuple(self.artifacts or ()))

    @property
    def e_rank(self) -> int:
        return E_RANK[self.evidence_level]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome_id": self.outcome_id,
            "mission_id": self.mission_id,
            "grade": self.grade,
            "evidence_level": self.evidence_level,
            "artifacts": list(self.artifacts),
            "reason": self.reason,
            "baseline": self.baseline,
            "novelty_search_scope": self.novelty_search_scope,
            "declared_confirmed": self.declared_confirmed,
            "domain": self.domain,
        }


# ---------------------------------------------------------------------------
# 逐条判据
# ---------------------------------------------------------------------------

def _rule(code: str, title: str, spec: str, ok: bool, reason: str) -> Dict[str, Any]:
    return {"rule": code, "title": title, "spec": spec,
            "verdict": PASS if ok else FAIL, "reason": reason}


def _has_artifacts(decl: OutcomeDeclaration) -> bool:
    return any(str(a).strip() for a in decl.artifacts)


def g1_evidence_present(decl: OutcomeDeclaration) -> Dict[str, Any]:
    """G1：任何等级登记 E≥1 且携带可检查制品（无证据即拒）。"""
    spec = "§7.1-8 + §6.2 E1(可检查)"
    if decl.e_rank < E_RANK["E1"]:
        return _rule("G1", "登记须 E≥1", spec, False,
                     f"{decl.grade} 申报证据等级 {decl.evidence_level}："
                     "E0=纯声明，不足以登记任何等级成果")
    if not _has_artifacts(decl):
        return _rule("G1", "登记须 E≥1", spec, False,
                     f"{decl.grade}/{decl.evidence_level} 未携带任何可检查制品"
                     "（制品/日志/来源），E1 不成立")
    return _rule("G1", "登记须 E≥1", spec, True,
                 f"{decl.evidence_level} 且制品 {len(decl.artifacts)} 件：可检查")


def g2_s5_requires_e3(decl: OutcomeDeclaration) -> Dict[str, Any]:
    """G2：申报 S5 须 E≥3（低报高即拒）。"""
    spec = "§7.1-9（v1.2 收紧）"
    if decl.grade != "S5":
        return _rule("G2", "S5 须 E≥3", spec, True, "不申报 S5，判据不适用")
    if decl.e_rank < E_RANK["E3"]:
        return _rule("G2", "S5 须 E≥3", spec, False,
                     f"申报 S5 但证据等级 {decl.evidence_level}"
                     f"（ranks: E3=结果验证），不满足 S5 须 E≥3；"
                     "低报高不得计入成果统计")
    return _rule("G2", "S5 须 E≥3", spec, True,
                 f"S5/{decl.evidence_level} 满足 E≥3")


def g3_reason_present(decl: OutcomeDeclaration) -> Dict[str, Any]:
    """G3：等级必须附理由（无理由等级即拒）。"""
    spec = "§7.1-1/-4"
    text = (decl.reason or "").strip()
    if len(text) < MIN_REASON_CHARS:
        return _rule("G3", "等级须附理由", spec, False,
                     f"等级 {decl.grade} 的理由为空或过短"
                     f"（{len(text)}<{MIN_REASON_CHARS} 字符）：无理由等级不可登记")
    return _rule("G3", "等级须附理由", spec, True, f"理由 {len(text)} 字符")


def g4_s4_requirements(decl: OutcomeDeclaration) -> Dict[str, Any]:
    """G4：S4 申报「已确认」须 E≥2 且附对照基线；否则只能登记为候选。"""
    spec = "§7.1-9"
    if decl.grade != "S4":
        return _rule("G4", "S4 已确认须 E≥2+基线", spec, True, "不申报 S4，判据不适用")
    if not decl.declared_confirmed:
        return _rule("G4", "S4 已确认须 E≥2+基线", spec, True,
                     "按候选登记（declared_confirmed=False），允许")
    missing: List[str] = []
    if decl.e_rank < E_RANK["E2"]:
        missing.append(f"E≥2（实为 {decl.evidence_level}）")
    if not (decl.baseline or "").strip():
        missing.append("对照基线")
    if missing:
        return _rule("G4", "S4 已确认须 E≥2+基线", spec, False,
                     "S4 申报已确认但缺 " + "、".join(missing) +
                     "；只能登记为候选（创新候选/待验证）")
    return _rule("G4", "S4 已确认须 E≥2+基线", spec, True,
                 "E≥2 且附对照基线")


def g5_s5_novelty_scope(decl: OutcomeDeclaration) -> Dict[str, Any]:
    """G5：申报 S5 须声明新颖性检索范围。"""
    spec = "§7.1 S5 最低评价依据"
    if decl.grade != "S5":
        return _rule("G5", "S5 须新颖性检索范围", spec, True, "不申报 S5，判据不适用")
    scope = (decl.novelty_search_scope or "").strip()
    if len(scope) < MIN_NOVELTY_CHARS:
        return _rule("G5", "S5 须新颖性检索范围", spec, False,
                     "S5 未声明新颖性检索范围（来源/时段/检索式）："
                     "无法证明「新在哪里」")
    return _rule("G5", "S5 须新颖性检索范围", spec, True,
                 f"检索范围声明 {len(scope)} 字符")


def g6_negative_grade_threshold(decl: OutcomeDeclaration) -> Dict[str, Any]:
    """G6：否定性成果 S-N 登记须 E≥2。"""
    spec = "§7.1 否定性成果门槛"
    if decl.grade != NEGATIVE_GRADE:
        return _rule("G6", "S-N 须 E≥2", spec, True, "不申报 S-N，判据不适用")
    if decl.e_rank < E_RANK["E2"]:
        return _rule("G6", "S-N 须 E≥2", spec, False,
                     f"S-N 申报证据等级 {decl.evidence_level} < E2："
                     "否定结论必须可复现")
    return _rule("G6", "S-N 须 E≥2", spec, True, f"S-N/{decl.evidence_level} 满足 E≥2")


RULES: Tuple[Tuple[str, Any], ...] = (
    ("G1", g1_evidence_present),
    ("G2", g2_s5_requires_e3),
    ("G3", g3_reason_present),
    ("G4", g4_s4_requirements),
    ("G5", g5_s5_novelty_scope),
    ("G6", g6_negative_grade_threshold),
)


# ---------------------------------------------------------------------------
# 门判定
# ---------------------------------------------------------------------------

def evaluate(decl: OutcomeDeclaration) -> Dict[str, Any]:
    """对单条成果声明跑全部门判据，返回可 JSON 化的判决。"""
    results = [fn(decl) for _, fn in RULES]
    failed = [r for r in results if r["verdict"] == FAIL]
    verdict = FAIL if failed else PASS

    if verdict == FAIL:
        classification = REJECTED
    elif not decl.declared_confirmed:
        classification = CANDIDATE
    elif decl.grade == "S4" and not (
            decl.e_rank >= E_RANK["E2"] and (decl.baseline or "").strip()):
        # 防御式兜底：G4 已保证此处不会发生，但分类不与判据脱钩
        classification = CANDIDATE
    else:
        classification = CONFIRMED

    return {
        "outcome_id": decl.outcome_id,
        "mission_id": decl.mission_id,
        "grade": decl.grade,
        "evidence_level": decl.evidence_level,
        "declared_confirmed": decl.declared_confirmed,
        "verdict": verdict,
        "classification": classification,
        "countable": verdict == PASS and classification == CONFIRMED,
        "failed_rules": [r["rule"] for r in failed],
        "rules": results,
        "declaration": decl.to_dict(),
    }


def evaluate_all(decls: Iterable[OutcomeDeclaration]) -> List[Dict[str, Any]]:
    return [evaluate(d) for d in decls]


def summarize(verdicts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """统计口径：候选/被拒不计入成果分布（§7.1-9）。"""
    out = {
        "total": len(verdicts),
        "pass": sum(v["verdict"] == PASS for v in verdicts),
        "fail": sum(v["verdict"] == FAIL for v in verdicts),
        "confirmed": sum(v["classification"] == CONFIRMED for v in verdicts),
        "candidate": sum(v["classification"] == CANDIDATE for v in verdicts),
        "rejected": sum(v["classification"] == REJECTED for v in verdicts),
        "countable": sum(bool(v["countable"]) for v in verdicts),
        "by_grade": {},
        "failed_rule_counts": {},
    }
    for v in verdicts:
        g = out["by_grade"].setdefault(
            v["grade"], {"confirmed": 0, "candidate": 0, "rejected": 0})
        g[{"CONFIRMED": "confirmed", "CANDIDATE": "candidate",
           "REJECTED": "rejected"}[v["classification"]]] += 1
        for r in v["failed_rules"]:
            out["failed_rule_counts"][r] = out["failed_rule_counts"].get(r, 0) + 1
    return out


def gate_rules_doc() -> List[Dict[str, str]]:
    """判据清册（写进审计工件，机器可读）。"""
    return [
        {"rule": "G1", "title": "任何等级登记须 E≥1 且有可检查制品",
         "spec": "§7.1-8 + §6.2", "effect": "FAIL 阻断登记"},
        {"rule": "G2", "title": "申报 S5 须 E≥3",
         "spec": "§7.1-9（v1.2 收紧）", "effect": "FAIL 阻断登记"},
        {"rule": "G3", "title": "等级须附理由（≥%d 字符）" % MIN_REASON_CHARS,
         "spec": "§7.1-1/-4", "effect": "FAIL 阻断登记"},
        {"rule": "G4", "title": "S4 申报已确认须 E≥2+对照基线，否则只能候选",
         "spec": "§7.1-9", "effect": "FAIL 阻断登记"},
        {"rule": "G5", "title": "申报 S5 须声明新颖性检索范围",
         "spec": "§7.1 S5 最低评价依据", "effect": "FAIL 阻断登记"},
        {"rule": "G6", "title": "否定性成果 S-N 登记须 E≥2",
         "spec": "§7.1 否定性成果门槛", "effect": "FAIL 阻断登记"},
    ]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI：读一份声明 JSON（或 stdin），输出判决 JSON；FAIL 退出码 1。"""
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="outcome registration evidence gate")
    ap.add_argument("declaration", nargs="?", help="declaration JSON file (- for stdin)")
    ap.add_argument("--rules", action="store_true", help="dump rule catalogue")
    args = ap.parse_args(argv)

    if args.rules:
        print(json.dumps(gate_rules_doc(), ensure_ascii=False, indent=2))
        return 0
    if not args.declaration:
        ap.error("declaration file required (or --rules)")
    raw = sys.stdin.read() if args.declaration == "-" else open(
        args.declaration, encoding="utf-8").read()
    payload = json.loads(raw)
    verdict = evaluate(OutcomeDeclaration(**payload))
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0 if verdict["verdict"] == PASS else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())