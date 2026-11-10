#!/usr/bin/env python3
"""变异器：给每个机制注入 6 类退化，度量 golden replay 能否 100% 检出。

6 类退化（对应「门变绿但事故发生了」的六种真实病理）
------------------------------------------------------
======================  =====================================================
M1_THRESHOLD_RELAX      阈值放宽（min ÷ 因子 / max × 因子）——「先把门放软」
M2_EXCEPTION_SWALLOW    输入异常被吞掉（缺字段 → 放行）——「坏数据也能过门」
M3_ORDER_SENSITIVE      集合归约只取首/末元素——「换个顺序结论就变」
M4_IDEMPOTENCY_BREAK    二次运行判词不同（有状态）——「重放不可复现」
M5_AUDIT_FIELD_LOSS     审计字段丢失——「过了门但查不到为什么」
M6_CONST_SHORTCUT       短路返回常量——「门只是个摆设」
======================  =====================================================

诚实性纪律
----------
* 变异体是**行为级**的：每个变异体必须先在某条 fixture 上产生与 golden **不同的判词**，
  才算被检出（等价变异体=未检出，如实记入 ``undetected``，不粉饰）。
* 检测手段只用 replay 的三条正常渠道（判词 diff / 双次调用 / 置换对），不为变异体定制后门。
* 每个变异体独立新建实例，避免状态跨样本泄漏导致检测率虚高。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from tools.mech_replay import mechanisms, reference, spec
from tools.mech_replay.spec import Rule

COLLECTION_KINDS = {"min_count", "count_true", "all_false", "criteria_satisfied",
                    "item_value_min", "item_value_max", "item_enum"}

MUTATION_CLASSES: dict[str, dict[str, Any]] = {
    "M1_THRESHOLD_RELAX": {
        "title": "阈值放宽",
        "description": "把机制判据的数值阈值按 min÷因子 / max×因子 放宽（默认 20%）",
        "target": "规则表阈值",
    },
    "M2_EXCEPTION_SWALLOW": {
        "title": "异常吞掉",
        "description": "输入缺字段/类型错时不再判 fail，而是跳过或直接放行",
        "target": "缺字段策略",
    },
    "M3_ORDER_SENSITIVE": {
        "title": "输入顺序置换敏感",
        "description": "集合归约只取首元素（或末元素），判词随输入顺序漂移",
        "target": "集合归约策略",
    },
    "M4_IDEMPOTENCY_BREAK": {
        "title": "幂等失效",
        "description": "机制带隐式状态，同一输入第二次运行判词不同",
        "target": "调用状态",
    },
    "M5_AUDIT_FIELD_LOSS": {
        "title": "审计字段丢失",
        "description": "判词仍对，但审计字段（rule_version/整块 audit）被摘掉",
        "target": "判词审计面",
    },
    "M6_CONST_SHORTCUT": {
        "title": "短路返回常量",
        "description": "机制不再计算，直接返回 pass/fail 常量判词",
        "target": "控制流",
    },
}

FACTORS = (1.2, 1.5)

# 每类退化「必须被哪条语义渠道」检出（比"判词有任何不同"更强的判据）：
#   decision_flip —— 决策或理由码变化（语义退化）
#   idempotency   —— 同输入二次调用判词不同（状态泄漏）
#   audit_diff    —— 决策不变但审计面丢失（正是 M5 的病理）
CHANNEL_REQUIREMENTS: dict[str, str] = {
    "M1_THRESHOLD_RELAX": "decision_flip",
    "M2_EXCEPTION_SWALLOW": "decision_flip",
    "M3_ORDER_SENSITIVE": "decision_flip",
    "M4_IDEMPOTENCY_BREAK": "idempotency",
    "M5_AUDIT_FIELD_LOSS": "audit_diff",
    "M6_CONST_SHORTCUT": "decision_flip",
}


# --------------------------------------------------------------------------- #
# 变异注入
# --------------------------------------------------------------------------- #
def relax_rules(rules: Sequence[Rule], factor: float) -> tuple[Rule, ...]:
    """M1：按 direction 放宽阈值（min 减小、max 增大），保留其余字段。"""
    out = []
    for rule in rules:
        if rule.threshold is None or rule.direction not in ("min", "max"):
            out.append(rule)
            continue
        new_threshold = (rule.threshold / factor if rule.direction == "min"
                         else rule.threshold * factor)
        out.append(Rule(**{**rule.__dict__, "threshold": new_threshold}))
    return tuple(out)


@dataclass
class Mutant:
    sample_id: str
    mechanism: str
    klass: str
    variant: str
    judge: Callable[[Mapping[str, Any]], dict[str, Any]]
    note: str = ""

    def run(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return self.judge(record)


def _const_verdict(mechanism: str, decision: str) -> dict[str, Any]:
    return {
        "mechanism": mechanism,
        "decision": decision,
        "reasons": [],
        "checks": [],
        "audit": {"mechanism": mechanism, "rule_version": spec.RULES_VERSION,
                  "checks_total": 0, "checks_failed": 0, "checks_skipped": 0,
                  "shortcut": True},
    }


def _stateful(mutant_judge: Callable[[Mapping[str, Any]], dict[str, Any]],
              mode: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """M4：第二次调用起判词改变（toggle 决策 / audit 加计数）。"""
    state = {"calls": 0}

    def wrapper(record: Mapping[str, Any]) -> dict[str, Any]:
        state["calls"] += 1
        verdict = copy.deepcopy(mutant_judge(record))
        if state["calls"] > 1:
            if mode == "toggle_decision":
                verdict["decision"] = "pass" if verdict["decision"] == "fail" else "fail"
            elif mode == "audit_nonce":
                verdict.setdefault("audit", {})["call_index"] = state["calls"]
            else:  # pragma: no cover - 防御
                raise ValueError(mode)
        return verdict

    return wrapper


def build_mutants(mechanism_names: Sequence[str] | None = None) -> list[Mutant]:
    names = list(mechanism_names or mechanisms.MECHANISMS)
    mutants: list[Mutant] = []
    for name in names:
        rules = mechanisms.RULES[name]
        has_collection = any(r.kind in COLLECTION_KINDS for r in rules)
        has_numeric = any(r.threshold is not None and r.direction in ("min", "max")
                          for r in rules)

        # ---- M1 阈值放宽（两个因子） ----
        if has_numeric:
            for factor in FACTORS:
                relaxed = relax_rules(rules, factor)
                mutants.append(Mutant(
                    sample_id=f"M1_THRESHOLD_RELAX:{name}:factor-{factor}",
                    mechanism=name, klass="M1_THRESHOLD_RELAX",
                    variant=f"factor-{factor}",
                    judge=(lambda m, rs: (lambda rec: mechanisms.judge(
                        m, rec, rules=rs)))(name, relaxed),
                    note="数值阈值放宽 %.1fx" % factor,
                ))

        # ---- M2 异常吞掉（跳过 / 直接放行） ----
        mutants.append(Mutant(
            sample_id=f"M2_EXCEPTION_SWALLOW:{name}:skip-on-error",
            mechanism=name, klass="M2_EXCEPTION_SWALLOW", variant="skip-on-error",
            judge=(lambda m: (lambda rec: mechanisms.judge(
                m, rec, missing_policy="pass")))(name),
            note="缺字段/类型错时跳过该判据，按剩余判据定论",
        ))

        def _blanket(rec: Mapping[str, Any], m: str = name) -> dict[str, Any]:
            verdict = mechanisms.judge(m, rec)
            error_codes = {spec.CODE_MISSING_FIELD, spec.CODE_TYPE_ERROR}
            if any(r["code"] in error_codes for r in verdict["reasons"]):
                return _const_verdict(m, "pass")
            return verdict

        mutants.append(Mutant(
            sample_id=f"M2_EXCEPTION_SWALLOW:{name}:blanket-pass",
            mechanism=name, klass="M2_EXCEPTION_SWALLOW", variant="blanket-pass",
            judge=_blanket, note="输入异常一律改判 pass（彻底吞掉）",
        ))

        # ---- M3 顺序敏感（first / last 归约） ----
        if has_collection:
            for policy in ("first", "last"):
                mutants.append(Mutant(
                    sample_id=f"M3_ORDER_SENSITIVE:{name}:reduce-{policy}",
                    mechanism=name, klass="M3_ORDER_SENSITIVE", variant=f"reduce-{policy}",
                    judge=(lambda m, p: (lambda rec: mechanisms.judge(
                        m, rec, reduce_policy=p)))(name, policy),
                    note=f"集合归约只保留 {policy} 个元素",
                ))

        # ---- M4 幂等失效 ----
        for mode in ("toggle_decision", "audit_nonce"):
            mutants.append(Mutant(
                sample_id=f"M4_IDEMPOTENCY_BREAK:{name}:{mode}",
                mechanism=name, klass="M4_IDEMPOTENCY_BREAK", variant=mode,
                judge=_stateful((lambda m: (lambda rec: mechanisms.judge(m, rec)))(name), mode),
                note="第二次调用起判词漂移",
            ))

        # ---- M5 审计字段丢失 ----
        def _drop_audit(rec: Mapping[str, Any], m: str = name) -> dict[str, Any]:
            verdict = mechanisms.judge(m, rec)
            verdict.pop("audit", None)
            return verdict

        def _drop_rule_version(rec: Mapping[str, Any], m: str = name) -> dict[str, Any]:
            verdict = mechanisms.judge(m, rec)
            if isinstance(verdict.get("audit"), dict):
                verdict["audit"].pop("rule_version", None)
            return verdict

        mutants.append(Mutant(
            sample_id=f"M5_AUDIT_FIELD_LOSS:{name}:drop-audit-block",
            mechanism=name, klass="M5_AUDIT_FIELD_LOSS", variant="drop-audit-block",
            judge=_drop_audit, note="整块 audit 被摘掉"))
        mutants.append(Mutant(
            sample_id=f"M5_AUDIT_FIELD_LOSS:{name}:drop-rule-version",
            mechanism=name, klass="M5_AUDIT_FIELD_LOSS", variant="drop-rule-version",
            judge=_drop_rule_version, note="audit.rule_version 被摘掉"))

        # ---- M6 短路常量 ----
        for decision in ("pass", "fail"):
            mutants.append(Mutant(
                sample_id=f"M6_CONST_SHORTCUT:{name}:const-{decision}",
                mechanism=name, klass="M6_CONST_SHORTCUT", variant=f"const-{decision}",
                judge=(lambda m, d: (lambda rec: _const_verdict(m, d)))(name, decision),
                note=f"常量返回 {decision}"))

    mutants.sort(key=lambda m: m.sample_id)
    return mutants


# --------------------------------------------------------------------------- #
# 检出判定
# --------------------------------------------------------------------------- #
def detect(mutant: Mutant, fixtures: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """单变异体检出：判词 diff / 幂等两次调用 / 置换对一致性，三条渠道取并。"""
    evidence: list[str] = []
    golden_mismatch: list[str] = []
    decision_flip: list[str] = []
    idem_mismatch: list[str] = []
    pair_signatures: dict[str, list[str]] = {}

    for fixture in fixtures:
        record = fixture["record"]
        golden = fixture["expected_verdict"]
        first = mutant.run(record)
        if spec.canon(first) != spec.canon(golden):
            golden_mismatch.append(fixture["fixture_id"])
        if (first.get("decision") != golden.get("decision")
                or reference.spec_codes(first) != reference.spec_codes(golden)):
            decision_flip.append(fixture["fixture_id"])
        second = mutant.run(record)
        if spec.canon(second) != spec.canon(first):
            idem_mismatch.append(fixture["fixture_id"])
        probe_class = str(fixture.get("probe_class") or "")
        if probe_class.startswith("order-pair"):
            key = f"{fixture['mechanism']}:order-pair"
            pair_signatures.setdefault(key, []).append(spec.pair_signature(second))

    pair_violations = sorted(k for k, v in pair_signatures.items() if len(set(v)) > 1)
    if decision_flip:
        evidence.append(f"decision_flip:{decision_flip[0]}")
    if golden_mismatch:
        evidence.append(f"verdict_diff:{golden_mismatch[0]}")
    if idem_mismatch:
        evidence.append(f"idempotency:{idem_mismatch[0]}")
    if pair_violations:
        evidence.append(f"permutation:{pair_violations[0]}")
    channels = {
        "decision_flip": bool(decision_flip),
        "idempotency": bool(idem_mismatch),
        "permutation": bool(pair_violations),
        "audit_diff": bool(golden_mismatch) and not decision_flip,
    }
    return {
        "sample_id": mutant.sample_id,
        "mechanism": mutant.mechanism,
        "class": mutant.klass,
        "variant": mutant.variant,
        "detected": bool(evidence),
        "decision_flip": bool(decision_flip),
        "channels": channels,
        "evidence": evidence,
        "verdict_mismatches": len(golden_mismatch),
        "decision_flip_mismatches": len(decision_flip),
        "idempotency_mismatches": len(idem_mismatch),
        "permutation_violations": len(pair_violations),
        "first_mismatch": golden_mismatch[0] if golden_mismatch else None,
    }


def run_suite(fixtures: Sequence[Mapping[str, Any]],
              mechanism_names: Sequence[str] | None = None) -> dict[str, Any]:
    by_mechanism: dict[str, list[Mapping[str, Any]]] = {}
    for fixture in fixtures:
        by_mechanism.setdefault(fixture["mechanism"], []).append(fixture)

    samples: list[dict[str, Any]] = []
    for mutant in build_mutants(mechanism_names):
        scoped = by_mechanism.get(mutant.mechanism, [])
        result = detect(mutant, scoped)
        result["fixtures_in_scope"] = len(scoped)
        samples.append(result)

    classes: dict[str, dict[str, Any]] = {}
    for class_id, meta in MUTATION_CLASSES.items():
        scoped = [s for s in samples if s["class"] == class_id]
        detected = [s for s in scoped if s["detected"]]
        classes[class_id] = {
            "title": meta["title"],
            "description": meta["description"],
            "samples": len(scoped),
            "detected": len(detected),
            "detection_rate": round(len(detected) / len(scoped), 6) if scoped else 0.0,
            "undetected": [s["sample_id"] for s in scoped if not s["detected"]],
            "decision_flip_detected": sum(1 for s in scoped if s["decision_flip"]),
            "channel_detected": {ch: sum(1 for s in scoped if s["channels"][ch])
                                 for ch in ("decision_flip", "idempotency",
                                            "permutation", "audit_diff")},
            "required_channel": CHANNEL_REQUIREMENTS[class_id],
            "required_channel_ok": all(s["channels"][CHANNEL_REQUIREMENTS[class_id]]
                                       for s in scoped),
            "mechanisms_covered": sorted({s["mechanism"] for s in scoped}),
        }

    total = len(samples)
    detected_total = sum(1 for s in samples if s["detected"])
    flip_total = sum(1 for s in samples if s["decision_flip"])
    return {
        "mutation_samples": total,
        "mutation_detected": detected_total,
        "detection_rate": round(detected_total / total, 6) if total else 0.0,
        "classes": classes,
        "min_samples_per_class": min((c["samples"] for c in classes.values()), default=0),
        "decision_flip_detected": flip_total,
        "decision_flip_rate": round(flip_total / total, 6) if total else 0.0,
        "all_detected": detected_total == total and total > 0,
        "samples": samples,
    }


def matrix(samples: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, int]]]:
    """机制 × 退化类 的检出矩阵（供图/表）。"""
    out: dict[str, dict[str, dict[str, int]]] = {}
    for sample in samples:
        bucket = out.setdefault(sample["mechanism"], {}).setdefault(
            sample["class"], {"samples": 0, "detected": 0})
        bucket["samples"] += 1
        bucket["detected"] += 1 if sample["detected"] else 0
    return {m: dict(sorted(v.items())) for m, v in sorted(out.items())}


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    from tools.mech_replay import replay as replay_mod

    parser = argparse.ArgumentParser(description="mech_replay 变异器：6 类退化检出率")
    parser.add_argument("--fixtures", default=str(replay_mod.DEFAULT_FIXTURES))
    parser.add_argument("--mechanism", default=None)
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)
    fixtures = replay_mod.load_fixtures(args.fixtures, args.mechanism)
    report = run_suite(fixtures, [args.mechanism] if args.mechanism else None)
    payload = {k: v for k, v in report.items() if k != "samples"}
    payload["matrix"] = matrix(report["samples"])
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    print(json.dumps({"mutation_samples": report["mutation_samples"],
                      "mutation_detected": report["mutation_detected"],
                      "detection_rate": report["detection_rate"],
                      "min_samples_per_class": report["min_samples_per_class"],
                      "decision_flip_rate": report["decision_flip_rate"],
                      "classes": {k: {"samples": v["samples"], "detected": v["detected"]}
                                  for k, v in report["classes"].items()}},
                     ensure_ascii=False, indent=2))
    return 0 if report["all_detected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())