"""判决与混淆矩阵：声明等级（claimed） vs 证伪结果（falsified）。

判决规则（机器可执行，全部只看刀具证据，不看任何自述字段）：

  | 条件 | 证伪等级 |
  |---|---|
  | 端点全部 404 / 无入口 | NOT_SUPPORTED |
  | 数值能力但全部响应为 0/空 | STUB |
  | 任一刀具 KILL | MOCK |
  | 数值能力、刀 B 解析锚 PASS 且无 KILL（A/C 不否决） | REAL |
  | 其余（可调用但无解析锚 / 舍入受限 / 未定位入口） | WITHHELD |

`claimed REAL → falsified MOCK` 记**假阳性（over-claim）**，假阳性率 =
over-claim 能力数 / 声明 REAL 的能力数。
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Dict, List

from . import knives

GRADES = ["REAL", "WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED"]
CLAIM_GRADES = ["REAL", "WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED"]


def falsified_grade(trace: Dict[str, Any]) -> Dict[str, Any]:
    kind = trace["kind"]
    status = trace["entrypoint_status"]
    agg = trace.get("aggregate") or {}
    statuses = trace.get("http_statuses") or []
    if kind == "numeric":
        if status != "ok":
            if all(s == "404" for s in statuses) if statuses else False:
                return {"grade": "NOT_SUPPORTED", "reason": "端点 404"}
            return {"grade": "WITHHELD", "reason": f"端点不可用（{status}）"}
        if trace.get("stub_like"):
            return {"grade": "STUB", "reason": "所有响应字段为空或恒为 0"}
        if agg.get("state") == "KILLED":
            return {"grade": "MOCK",
                    "reason": f"被刀具推翻：{'+'.join(agg.get('kills') or [])}"}
        if agg.get("state") == "SURVIVED" and _has_anchor(trace):
            return {"grade": "REAL",
                    "reason": f"无 KILL 且解析/恒等式锚通过（PASS 刀 {','.join(agg.get('passes') or [])}）"}
        if agg.get("state") == "SURVIVED":
            return {"grade": "WITHHELD",
                    "reason": "三刀存活，但刀 B 只有跨模型一致性锚（无解析解/恒等式绝对锚），不足以宣布 REAL"}
        return {"grade": "WITHHELD", "reason": "可调用但缺解析锚/判决不确定"}
    # 非数值能力
    if status == "absent":
        return {"grade": "NOT_SUPPORTED", "reason": "候选端点全部 404（openapi 无此路径）"}
    if status == "ok":
        return {"grade": "WITHHELD",
                "reason": "查询/列表类端点：无「数值输入→数值输出」映射，三刀不适用（不得据此宣布 REAL）"}
    if status == "not_attempted":
        return {"grade": "WITHHELD", "reason": "台账无在线入口（运维/开发动作），三刀不适用"}
    return {"grade": "WITHHELD", "reason": f"端点异常（{status}）"}


ANCHOR_KINDS = ("analytic", "identity")


def _has_anchor(trace: Dict[str, Any]) -> bool:
    """是否存在通过了的解析解/恒等式锚（跨模型一致性 consistency 不算绝对锚）。"""
    for kr in trace.get("knife_results") or []:
        if kr["knife"] != "B":
            continue
        for chk in kr.get("checks") or []:
            if chk.get("verdict") == "PASS" and chk.get("kind") in ANCHOR_KINDS:
                return True
    return False


def grade_traces(ledger: Dict[str, Any], probe: Dict[str, Any]) -> Dict[str, Any]:
    by_id = {c["id"]: c for c in ledger["capabilities"]}
    rows = []
    for trace in probe["capabilities"]:
        cid = trace["capability"]
        entry = by_id.get(cid, {})
        g = falsified_grade(trace)
        claimed = entry.get("evidence_grade")
        rows.append({
            "id": cid,
            "name": entry.get("name"),
            "domain": entry.get("domain"),
            "claimed_grade": claimed,
            "reconciled_level": (entry.get("evidence_ref") or {}).get("reconciled_level"),
            "falsified_grade": g["grade"],
            "reason": g["reason"],
            "entrypoint": (entry.get("entrypoint") or {}).get("path"),
            "kind": trace["kind"],
            "knife_verdicts": {kr.get("driver") or kr.get("series") or kr["knife"]: kr["verdict"]
                              for kr in trace.get("knife_results") or []},
            "knife_kills": (trace.get("aggregate") or {}).get("kills") or [],
            "has_analytic_anchor": _has_anchor(trace),
            "applies_knives": entry.get("callable_spec", {}).get("applicable_knives") or [],
        })
    rows.sort(key=lambda r: r["id"])
    return {"rows": rows}


def confusion_matrix(verdicts: Dict[str, Any]) -> Dict[str, Any]:
    rows = verdicts["rows"]
    mat = {c: {f: 0 for f in GRADES} for c in CLAIM_GRADES}
    for r in rows:
        c = r["claimed_grade"] if r["claimed_grade"] in mat else "WITHHELD"
        f = r["falsified_grade"]
        mat[c][f] = mat[c].get(f, 0) + 1
    claimed_real = [r for r in rows if r["claimed_grade"] == "REAL"]
    over = [r["id"] for r in claimed_real
            if r["falsified_grade"] in ("MOCK", "STUB", "NOT_SUPPORTED", "WITHHELD")]
    killed = [r["id"] for r in claimed_real if r["falsified_grade"] in ("MOCK", "STUB")]
    confirmed = [r["id"] for r in claimed_real if r["falsified_grade"] == "REAL"]
    conservative = [r["id"] for r in rows
                    if r["claimed_grade"] in ("MOCK", "STUB", "NOT_SUPPORTED")
                    and r["falsified_grade"] == "REAL"]
    return {
        "grades_rows": CLAIM_GRADES,
        "grades_cols": GRADES,
        "matrix": mat,
        "n_capabilities": len(rows),
        "claimed_real": len(claimed_real),
        "overclaim": over,
        "overclaim_killed": killed,
        "confirmed_real": confirmed,
        "overclaim_rate": (len(over) / len(claimed_real)) if claimed_real else None,
        "refuted_rate": (len(killed) / len(claimed_real)) if claimed_real else None,
        "kill_rate_on_claim_real": (len(killed) / len(claimed_real)) if claimed_real else None,
        "conservative_underclaim": conservative,
        "unfalsifiable": [r["id"] for r in rows if r["falsified_grade"] == "WITHHELD"],
        "not_supported": [r["id"] for r in rows if r["falsified_grade"] == "NOT_SUPPORTED"],
        "note": ("overclaim_rate（未确证率）= 声明 REAL 但证伪结果不是 REAL 的能力数 / 声明 REAL 的能力数；"
                 "refuted_rate（主动推翻率）= 其中被刀具判成 MOCK/STUB/NOT_SUPPORTED 的子集 / 声明 REAL 的能力数。"
                 "两率分开报，避免把「不可判定（WITHHELD）」与「被推翻（MOCK）」混为一谈"),
    }


def knife_summary(probe: Dict[str, Any]) -> Dict[str, Any]:
    out = {"A": {"pass": 0, "kill": 0, "inconclusive": 0, "killed_capabilities": []},
           "A2": {"pass": 0, "kill": 0, "inconclusive": 0, "killed_capabilities": []},
           "B": {"pass": 0, "kill": 0, "inconclusive": 0, "killed_capabilities": []},
           "C": {"pass": 0, "kill": 0, "inconclusive": 0, "killed_capabilities": []}}
    for tr in probe["capabilities"]:
        for kr in tr.get("knife_results") or []:
            k = kr["knife"]
            v = kr["verdict"]
            key = {"PASS": "pass", "KILL": "kill"}.get(v, "inconclusive")
            out[k][key] += 1
            if v == "KILL" and tr["capability"] not in out[k]["killed_capabilities"]:
                out[k]["killed_capabilities"].append(tr["capability"])
    return out


def build_report(ledger: Dict[str, Any], probe: Dict[str, Any], verdicts: Dict[str, Any],
                 matrix: Dict[str, Any], mutants: Dict[str, Any] | None,
                 tool_ids: Dict[str, Any]) -> Dict[str, Any]:
    ledger_ids = {c["id"] for c in ledger["capabilities"]}
    t_ids = set(tool_ids.get("ids") or [])
    sym = sorted(ledger_ids ^ t_ids)
    ks = knife_summary(probe)
    n_numeric = sum(1 for t in probe["capabilities"] if t["kind"] == "numeric")
    n_probed_ok = sum(1 for t in probe["capabilities"] if t["entrypoint_status"] == "ok")
    criteria = {
        "C1_ledger_single_truth": {
            "n_tool_capabilities": len(t_ids),
            "n_ledger_capabilities": len(ledger_ids),
            "symmetric_difference": sym,
            "id_set_diff_empty": len(sym) == 0,
            "required_fields_present": all(
                all(k in c for k in ("id", "name", "entrypoint", "evidence_grade",
                                     "evidence_ref", "callable_spec"))
                for c in ledger["capabilities"]),
            "source": tool_ids.get("source"),
        },
        "C2_three_knives_executable": {
            "invariance_tol": knives.INVARIANCE_TOL,
            "analytic_tol": knives.ANALYTIC_TOL,
            "knife_A_groups": ks["A"]["pass"] + ks["A"]["kill"] + ks["A"]["inconclusive"],
            "knife_A2_repeatability": ks["A2"]["pass"] + ks["A2"]["kill"] + ks["A2"]["inconclusive"],
            "knife_B_checks": ks["B"]["pass"] + ks["B"]["kill"] + ks["B"]["inconclusive"],
            "knife_C_series": ks["C"]["pass"] + ks["C"]["kill"] + ks["C"]["inconclusive"],
            "knife_summary": ks,
            "all_three_exercised": all(
                (ks[k]["pass"] + ks[k]["kill"] + ks[k]["inconclusive"]) > 0
                for k in ("A", "A2", "B", "C")),
        },
        "C3_confusion_matrix": {
            "n_capabilities": matrix["n_capabilities"],
            "claimed_real": matrix["claimed_real"],
            "overclaim_rate": matrix["overclaim_rate"],
            "refuted_rate": matrix.get("refuted_rate"),
            "kill_rate_on_claim_real": matrix["kill_rate_on_claim_real"],
            "confirmed_real": matrix["confirmed_real"],
            "matrix": matrix["matrix"],
        },
        "C4_probe_coverage": {
            "n_numeric_probed": n_numeric,
            "n_entrypoints_reachable": n_probed_ok,
            "n_capabilities": len(ledger_ids),
            "unfalsifiable": matrix["unfalsifiable"],
            "not_supported": matrix["not_supported"],
        },
        "C5_instrument_selftest": mutants or {"status": "NOT_RUN"},
    }
    return {
        "mission": "W18-N09",
        "title": "TensorPlatform 能力台账证伪成熟化：REAL/MOCK/STUB 机器可执行证伪",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "criteria": criteria,
        "verdicts": verdicts,
        "matrix": matrix,
    }


def write_json(path, obj) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)