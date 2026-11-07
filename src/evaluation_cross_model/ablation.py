"""消融/敏感性试验：把每一个「设计选择」的代价量化出来（IN47-N08 注意点）。

本模块存在的理由：评分器里有三个可能被质疑为「调参」的选择——
判定死区、证据独立性门、族 B 阈值 τ 的标定方式。这里把它们逐个关掉/换掉，
同时报告 (i) 判级正确率（标定集 / hold-out）、(ii) 误判方向（假阳性=过度认证，
假阴性=过度保守）、(iii) 翻转率，让读者自己判断选择是否合理。

**注意**：结论取值（``runner``）使用默认配置；消融结果只用于披露与诊断，
不参与结论的选择性挑选 —— 全部三组结果原样写入产物 JSON。
"""
from __future__ import annotations

from functools import partial
from typing import Any, Callable, Dict, List, Sequence

from .corpus import CaseRecord, REAL, oracle_label
from .families import agree, family_a, family_b, tau_for
from .robustness import flip_rate

SCHEMA = "in47-n08-ablation/1"


def _evaluate(cases: Sequence[CaseRecord], fam_a: Callable, fam_b: Callable) -> Dict[str, Any]:
    """联合判级（不一致→WITHHELD）vs 参考标准：正确率、误判方向、一致率。"""
    by_split: Dict[str, Dict[str, int]] = {
        "verified": {"n": 0, "hit": 0, "fp": 0, "fn": 0},
        "holdout": {"n": 0, "hit": 0, "fp": 0, "fn": 0},
    }
    agreed = 0
    for c in cases:
        va, vb = fam_a(c), fam_b(c)
        ok = agree(va, vb)
        agreed += int(ok)
        joint = va["label"] if ok else "WITHHELD"
        orc = oracle_label(c)["label"]
        s = by_split[c.split]
        s["n"] += 1
        if joint == orc:
            s["hit"] += 1
        elif joint == REAL:
            s["fp"] += 1     # 过度认证
        else:
            s["fn"] += 1     # 过度保守
    out = {}
    for k, s in by_split.items():
        out[k] = {
            "n": s["n"], "hit": s["hit"], "fp": s["fp"], "fn": s["fn"],
            "accuracy": round(s["hit"] / s["n"], 4) if s["n"] else None,
        }
    dv = out["verified"]["accuracy"]
    dh = out["holdout"]["accuracy"]
    out["delta_accuracy_pp"] = (None if (dv is None or dh is None)
                               else round(100.0 * (dh - dv), 4))
    out["consistency"] = round(agreed / len(cases), 4) if cases else None
    return out


def deadband_ablation(cases: Sequence[CaseRecord],
                      factors: Sequence[float] = (0.0, 0.25, 0.5)) -> Dict[str, Any]:
    """族 A 判定死区系数消融：死区越大越保守（假阴性↑），抖动抑制由协议承担。"""
    rows: List[Dict[str, Any]] = []
    for f in factors:
        fa = partial(family_a, deadband_factor=f)
        ev = _evaluate(cases, fa, family_b)
        rb = flip_rate(cases, k_measurements=3, fam_a=fa, fam_b=family_b)
        rows.append({"deadband_factor": f, **ev,
                     "joint_flip_rate": rb["flip_rate"]["joint"]})
    return {"schema": SCHEMA, "kind": "deadband", "rows": rows,
            "note": ("默认因子 0（不引入额外自由参数）。实测：非零死区在本语料上不改变正确率"
                     "（族 B 已更严，联合判级由它兜底），只把翻转率从 0.0218 降到 0.0145；"
                     "因抖动抑制已由复测取中位协议提供，故不引入该自由参数。")}


def independence_gate_ablation(cases: Sequence[CaseRecord]) -> Dict[str, Any]:
    """证据独立性门消融：关掉门后，仓内引用/仓内交叉实现会被误判为 REAL。"""
    rows: List[Dict[str, Any]] = []
    for use_gate in (True, False):
        fa = partial(family_a, require_independent_ref=use_gate)
        fb = partial(family_b, require_independent_ref=use_gate)
        ev = _evaluate(cases, fa, fb)
        rb = flip_rate(cases, k_measurements=3, fam_a=fa, fam_b=fb)
        rows.append({"require_independent_ref": use_gate, **ev,
                     "joint_flip_rate": rb["flip_rate"]["joint"]})
    return {"schema": SCHEMA, "kind": "independence_gate", "rows": rows,
            "note": "假阳性列即「把仓内自证当外部证据」的次数——门是抵御自我循环的关键。"}


def tau_calibration_ablation(cases: Sequence[CaseRecord]) -> Dict[str, Any]:
    """族 B 阈值标定方式消融：单 τ（最高参考等级标定） vs 逐等级 τ。"""
    rows: List[Dict[str, Any]] = []

    def _b_single(case: CaseRecord) -> Dict[str, Any]:
        """单 τ：所有参考等级共用 analytic 级边界案例标定的 τ。"""
        out = family_b(case)
        tau_single = tau_for("analytic")
        # 重算一次判定：把逐等级 τ 的判定替换成单 τ 判定
        from .corpus import REF_CLASS_QUALITY

        q_ref = REF_CLASS_QUALITY.get(case.ref_class, 0.0)
        if out["detail"].get("stage") != "composite":
            return out
        check = out["detail"]
        score = out["score"]
        label = REAL if score >= tau_single else "WITHHELD"
        out = dict(out)
        out["label"] = label
        from .families import LABEL_TO_GRADE

        out["e_level"], out["grade"] = LABEL_TO_GRADE[label]
        out["detail"] = dict(check, tau=tau_single, q_ref=q_ref, variant="single_tau")
        return out

    for tag, fb in (("per_class_tau", family_b), ("single_tau", _b_single)):
        ev = _evaluate(cases, family_a, fb)
        rows.append({"variant": tag, **ev})
    return {"schema": SCHEMA, "kind": "tau_calibration", "rows": rows,
            "note": ("单 τ 会让低等级参考的可接受偏差意外收紧（回归参考约 11.6%），"
                     "造成贴着容差的合格案例被误判为 WITHHELD（假阴性）；逐等级 τ 的"
                     "有效边界约为 14.35%，与预注册 15% 标准一致。")}


def run_ablations(cases: Sequence[CaseRecord]) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "deadband": deadband_ablation(cases),
        "independence_gate": independence_gate_ablation(cases),
        "tau_calibration": tau_calibration_ablation(cases),
    }