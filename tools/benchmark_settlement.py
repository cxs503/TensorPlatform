#!/usr/bin/env python3
"""对标决算复算（data 席 m2054）：把 KPI#9「对标算例数」/KPI#10「阻力精度」
从**手写字面量**降级为**机器可复算**。

背景（审计事实，2026-11-02）：
  * 正典参考侧已成熟：``backend/data/benchmark_reference.json`` v3.0（12 例
    SUBOFF/DTMB-5415/KVLCC2，含 R1~R6 解析恒等式与逐例 ``reference_status``）。
    其 integrity_gate.policy 明写：「仅 reference_status=DERIVED 的行可参与
    15% 精度判决；WITHHELD 行如实标注、不出判决」。
  * 计算侧唯一存量决算工件是 ``artifacts/benchmark_validation.json``（M398），
    含 calc_ct / ref_ct / error_pct / pass 四个字段——**判决与误差都是手写值**，
    且从未与 v3.0 正典对账：本工具实测 M398 在 8 例
    ``reference_status=WITHHELD`` 的行上照出 pass 判决，直接违反正典 policy。
  * 因此 KPI#9 的字面量「1 完成」与 KPI#10 的「未实测」都无法由数据复核
    （见 docs/kpi/literal_ledger.json 的 note：升级路径=benchmark registry）。

本工具做的事（最小、可验证、零手抄）：
  1. **误差复算**：rel_err = (calc-ref)/ref，一律从原始数字重算，
     与工件里手写的 ``error_pct`` 对账（drift>0.05pp → 标 TYPED_DRIFT，
     即手写字段不可信的证据）；
  2. **正典一致性自检（R1）**：Rt = Ct·½ρV²S 逐例复算并与正典
     ``experimental_rt_n`` 比对（正典自身也是被验证对象，不是神谕）；
  3. **policy 判决**：reference_status=WITHHELD 的行不得出精度判决；
     源工件若已出 pass/fail → 计 ``policy_violation``；
  4. **三档判据**：3%（论文级）/ 5%（指南 KPI#10 静水阻力）/ 15%（平台
     不可接受线），边界按闭区间；
  5. **KPI 折算**：qualified_cases（可判决 且 |err|≤15%）→ KPI#9；
     within5_rate（可判决行里 |err|≤5% 的比例）→ KPI#10；分母为 0 → None
     （未实测，不许塞 0 说谎）；
  6. 产出 ``artifacts/benchmark_settlement/{audit.json,audit.md}``，
     供 tools/guide_kpi_ledger.py 以 kind='registry' 直接读取。

用法：
  python3 tools/benchmark_settlement.py                # 复算 + 打印摘要
  python3 tools/benchmark_settlement.py --write        # 落盘工件
  python3 tools/benchmark_settlement.py --json-out X   # 指定输出路径
"""
from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Dict, Iterable, List, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
CANON_PATH = ROOT / "backend" / "data" / "benchmark_reference.json"
SETTLEMENT_PATH = ROOT / "artifacts" / "benchmark_validation.json"
OUT_DIR = ROOT / "artifacts" / "benchmark_settlement"
AUDIT_JSON = OUT_DIR / "audit.json"

# 判据线（%）：3% 论文级；5% 指南 KPI#10 静水阻力；15% 平台不可接受线
PAPER_TOL = 3.0
GUIDE_TOL = 5.0
HARD_TOL = 15.0
# 手写误差字段与复算值允许的绝对偏差（百分点）
TYPED_ERR_TOL_PP = 0.05
# 正典 R1 恒等式允许的相对残差（%）
R1_REL_TOL = 0.5
# 源工件引用的参考值与正典的允许相对偏差（%）
STALE_REF_TOL = 0.5

VERDICT_ORDER = ("PASS_3PCT", "PASS_5PCT", "FAIL_5PCT", "FAIL_15PCT", "FAIL_OVER_15")


# ───────────────────────────── 纯函数层（可单测） ─────────────────────────────

def rel_err_pct(calc: float, ref: float) -> float:
    """相对误差（%）。ref=0 时抛错——不静默返回 0 或 inf。"""
    ref = float(ref)
    if ref == 0.0:
        raise ZeroDivisionError("参考值为 0，相对误差无定义")
    return (float(calc) - ref) / ref * 100.0


def verdict_of(err_pct: float) -> str:
    """三档判据，闭区间。err 取绝对值。"""
    a = abs(float(err_pct))
    if a <= PAPER_TOL:
        return "PASS_3PCT"
    if a <= GUIDE_TOL:
        return "PASS_5PCT"
    if a <= HARD_TOL:
        return "FAIL_5PCT"
    return "FAIL_OVER_15"


def r1_identity(case: dict, rel_tol: float = R1_REL_TOL) -> Optional[dict]:
    """正典 R1：Rt = Ct·½ρV²S。返回 {rt_recomputed, rt_canon, rel_err_pct, ok} 或 None（缺输入）。"""
    ct = case.get("experimental_ct")
    rt = case.get("experimental_rt_n")
    rho = case.get("reference_density_kg_m3")
    v = case.get("speed_ms")
    geom = case.get("geometry") or {}
    s = geom.get("wetted_surface_m2")
    if None in (ct, rt, rho, v, s) or rt is None:
        return None
    rt_calc = float(ct) * 0.5 * float(rho) * float(v) ** 2 * float(s)
    e = rel_err_pct(rt_calc, float(rt))
    return dict(rt_recomputed=rt_calc, rt_canon=float(rt), rel_err_pct=e,
                ok=abs(e) <= rel_tol)


def load_canon(path: pathlib.Path = CANON_PATH) -> dict:
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return {"meta": {k: data.get(k) for k in ("version", "description")},
            "policy": (data.get("integrity_gate") or {}).get("policy", ""),
            "cases": {c["case_id"]: c for c in data.get("cases", [])}}


def load_settlement(path: pathlib.Path = SETTLEMENT_PATH) -> dict:
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return {"meta": {k: data.get(k) for k in ("mission", "description", "total_cases")},
            "results": data.get("results", [])}


def is_admissible(case: dict) -> bool:
    """正典 policy：只有 reference_status=DERIVED 的行可参与精度判决。"""
    return str(case.get("reference_status", "")).upper() == "DERIVED"


def settle_row(result: dict, case: Optional[dict]) -> dict:
    """单例决算：误差一律复算，手写值仅作对账。"""
    cid = result.get("case_id")
    row: Dict[str, Any] = dict(case_id=cid, ship_type=result.get("ship_type"),
                               speed_ms=result.get("speed_ms"),
                               method=result.get("method"))
    if case is None:
        row.update(status="NO_CANON_REFERENCE", admissible=False, verdict="WITHHELD",
                   reason="正典无此例，无法判决")
        return row

    ref_canon = case.get("experimental_ct")
    ref_used = result.get("ref_ct")
    calc = result.get("calc_ct")
    row.update(reference_status=case.get("reference_status"),
               rt_status=case.get("rt_status"),
               ref_ct_canon=ref_canon, ref_ct_used=ref_used, calc_ct=calc,
               source_verdict=result.get("pass"))

    if ref_canon is None or calc is None:
        row.update(status="NO_REFERENCE_VALUE", admissible=False, verdict="WITHHELD",
                   reason="参考值/计算值缺失")
        return row

    # 1) 源工件引用的参考值是否仍与正典一致（正典 v3.0 曾把 SUBOFF-01 Rt 95.0→92.52 N）
    if ref_used is not None:
        stale = rel_err_pct(float(ref_used), float(ref_canon))
        row["ref_stale_pct"] = stale
        row["ref_stale"] = abs(stale) > STALE_REF_TOL
    else:
        row["ref_stale_pct"] = None
        row["ref_stale"] = None

    # 2) 误差复算（正典口径）+ 与手写字段对账
    err = rel_err_pct(float(calc), float(ref_canon))
    row["err_pct"] = err
    row["abs_err_pct"] = abs(err)
    typed = result.get("error_pct")
    row["err_pct_typed"] = typed
    # 手写字段丢了符号（把有向误差写成绝对值）→ 误差方向信息丢失
    row["sign_lost"] = (typed is not None and float(typed) > 0 > err)
    row["typed_drift_pp"] = None if typed is None else err - float(typed)
    row["typed_field_trusted"] = (None if typed is None
                                  else abs(err - float(typed)) <= TYPED_ERR_TOL_PP)

    # 3) policy：WITHHELD 行不出判决
    admissible = is_admissible(case)
    row["admissible"] = admissible
    if not admissible:
        row["verdict"] = "WITHHELD"
        row["reason"] = "正典 reference_status=WITHHELD：禁止出精度判决（policy）"
        row["policy_violation"] = result.get("pass") is not None
    else:
        row["verdict"] = verdict_of(err)
        row["reason"] = "正典 DERIVED：可判决"
        row["policy_violation"] = False
    row["status"] = "SETTLED" if admissible else "INADMISSIBLE"
    return row


def settle(canon: dict, settlement: dict) -> List[dict]:
    rows = [settle_row(r, canon["cases"].get(r.get("case_id")))
            for r in settlement["results"]]
    # 正典里存在但计算侧从未决算的例（覆盖缺口也是欠账）
    settled_ids = {r["case_id"] for r in rows}
    for cid, case in canon["cases"].items():
        if cid not in settled_ids:
            rows.append(dict(case_id=cid, ship_type=case.get("ship_type"),
                             status="NOT_SETTLED", admissible=is_admissible(case),
                             verdict="NOT_SETTLED", reason="正典有例、计算侧无决算",
                             reference_status=case.get("reference_status")))
    return rows


def _rel(p) -> str:
    p = pathlib.Path(p)
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def _rate(num: int, den: int) -> Optional[float]:
    return None if den == 0 else num / den


def _verdict_mismatch(row: dict) -> bool:
    """源工件 pass/fail 与复算判决是否冲突（仅对可判决行）。"""
    if row.get("admissible") is not True or row.get("source_verdict") is None:
        return False
    recomputed_pass = row.get("verdict") in ("PASS_3PCT", "PASS_5PCT")
    return bool(row["source_verdict"]) != recomputed_pass


def summarize(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    adm = [r for r in rows if r.get("admissible")]
    inadm = [r for r in rows if r.get("admissible") is False]
    settled_adm = [r for r in adm if r.get("status") == "SETTLED"]
    within3 = [r for r in settled_adm if r["verdict"] in ("PASS_3PCT",)]
    within5 = [r for r in settled_adm if r["verdict"] in ("PASS_3PCT", "PASS_5PCT")]
    within15 = [r for r in settled_adm if r["verdict"] in ("PASS_3PCT", "PASS_5PCT", "FAIL_5PCT")]
    r1 = [r for r in rows if r.get("r1_check")]
    r1_bad = [r for r in r1 if not r["r1_check"]["ok"]]
    return dict(
        n_rows=len(rows),
        admissible=len(adm),
        inadmissible=len(inadm),
        settled_admissible=len(settled_adm),
        not_settled=len([r for r in rows if r.get("status") == "NOT_SETTLED"]),
        # KPI#9（严格口径）：可判决 且 |err| ≤ 15% 的算例数
        qualified_cases=len(within15),
        within3=len(within3),
        within5=len(within5),
        within5_rate=_rate(len(within5), len(settled_adm)),
        within3_rate=_rate(len(within3), len(settled_adm)),
        qualified_rate=_rate(len(within15), len(settled_adm)),
        policy_violations=len([r for r in rows if r.get("policy_violation")]),
        sign_lost=len([r for r in rows if r.get("sign_lost")]),
        source_pass_verdicts=len([r for r in rows if r.get("source_verdict") is True]),
        source_verdict_mismatch=len([r for r in rows if _verdict_mismatch(r)]),
        stale_refs=len([r for r in rows if r.get("ref_stale")]),
        typed_field_untrusted=len([r for r in rows if r.get("typed_field_trusted") is False]),
        typed_field_checked=len([r for r in rows if r.get("typed_field_trusted") is not None]),
        max_abs_err_pct=(max((r["abs_err_pct"] for r in settled_adm), default=None)),
        r1_checked=len(r1),
        r1_failed=len(r1_bad),
        rt_level_settled=len([r for r in rows if r.get("calc_rt_n") is not None]),
    )


def kpi_metrics(payload: dict) -> dict:
    """折算成 KPI 可复算口径（guide_kpi_ledger kind='registry' 直接消费）。"""
    s = payload["summary"]
    return dict(
        benchmark_cases=float(s["qualified_cases"]),
        resistance_accuracy=s["within5_rate"],  # None = 无可判决样本（未实测）
        settled_admissible=float(s["settled_admissible"]),
        policy_violations=float(s["policy_violations"]),
        sign_lost=float(s["sign_lost"]),
        _note=("benchmark_cases=可判决且|err|≤15% 的算例数；"
               "resistance_accuracy=可判决行中|err|≤5% 的比例；"
               "WITHHELD 行按正典 policy 不计入分母"),
    )


def audit(canon: dict, settlement: dict, *, canon_path=CANON_PATH,
          settlement_path=SETTLEMENT_PATH) -> dict:
    rows = settle(canon, settlement)
    # R1 恒等式自检：正典自身也是被验证对象
    for cid, case in canon["cases"].items():
        chk = r1_identity(case)
        if chk is None:
            continue
        for r in rows:
            if r["case_id"] == cid:
                r["r1_check"] = chk
    payload = dict(
        tool="tools/benchmark_settlement.py",
        canon=dict(path=_rel(canon_path), meta=canon["meta"], policy=canon["policy"]),
        settlement=dict(path=_rel(settlement_path), meta=settlement["meta"]),
        thresholds=dict(paper=PAPER_TOL, guide=GUIDE_TOL, hard=HARD_TOL,
                        typed_err_tol_pp=TYPED_ERR_TOL_PP, r1_rel_tol=R1_REL_TOL,
                        stale_ref_tol=STALE_REF_TOL),
        summary=summarize(rows),
        kpi={},
        rows=sorted(rows, key=lambda r: r["case_id"]),
    )
    payload["kpi"] = kpi_metrics(payload)
    return payload


# ───────────────────────────── 渲染层 ─────────────────────────────

def render_md(payload: dict) -> str:
    s = payload["summary"]
    lines = [
        "# 对标决算复算（benchmark settlement audit）",
        "",
        f"- 计算侧工件：`{payload['settlement']['path']}`（{payload['settlement']['meta'].get('mission')}）",
        f"- 正典参考：`{payload['canon']['path']}` v{payload['canon']['meta'].get('version')}",
        f"- 正典 policy：{payload['canon']['policy']}",
        "",
        "## 汇总（全部机器复算）",
        "",
        "| 指标 | 值 |", "| --- | --- |",
        f"| 决算行数 | {s['n_rows']} |",
        f"| 可判决（reference_status=DERIVED） | {s['admissible']} |",
        f"| 不可判决（WITHHELD） | {s['inadmissible']} |",
        f"| 可判决且已决算 | {s['settled_admissible']} |",
        f"| 达标 ≤3% | {s['within3']} |",
        f"| 达标 ≤5%（指南 KPI#10） | {s['within5']} |",
        f"| 合格 ≤15%（KPI#9 严格口径） | {s['qualified_cases']} |",
        f"| within5_rate | {s['within5_rate']} |",
        f"| policy 越权判决 | {s['policy_violations']} |",
        f"| 引用值已过期（vs 正典） | {s['stale_refs']} |",
        f"| 手写误差字段不可信 | {s['typed_field_untrusted']}/{s['typed_field_checked']} |",
        f"| 手写误差丢符号（方向丢失） | {s['sign_lost']} |",
        f"| 源工件 pass 判决数 | {s['source_pass_verdicts']} |",
        f"| 源判决与复算冲突（可判决行） | {s['source_verdict_mismatch']} |",
        f"| 最大绝对误差 % | {s['max_abs_err_pct']} |",
        f"| 正典 R1 自检失败 | {s['r1_failed']}/{s['r1_checked']} |",
        "",
        "## 逐例决算",
        "",
        "| case | ref_status | calc_ct | ref_ct(正典) | err% (复算) | 手写 err% | verdict | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in payload["rows"]:
        lines.append("| {cid} | {rs} | {calc} | {ref} | {err} | {typed} | {vd} | {note} |".format(
            cid=r["case_id"], rs=r.get("reference_status", "-"),
            calc=_fmt(r.get("calc_ct")), ref=_fmt(r.get("ref_ct_canon")),
            err=_fmt(r.get("err_pct")), typed=_fmt(r.get("err_pct_typed")),
            vd=r.get("verdict", "-"),
            note=("policy 越权判决" if r.get("policy_violation") else r.get("reason", ""))))
    return "\n".join(lines) + "\n"


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "pass" if v else "fail"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def write_artifacts(payload: dict, out_dir: pathlib.Path = OUT_DIR) -> dict:
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "audit.md").write_text(render_md(payload), encoding="utf-8")
    return dict(json=str(out_dir / "audit.json"), md=str(out_dir / "audit.md"))


def load_registry(path: pathlib.Path = AUDIT_JSON) -> Optional[dict]:
    """给 guide_kpi_ledger 用：读已落盘决算工件（不存在 → None，KPI 降级为未实测）。"""
    p = pathlib.Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="对标决算复算")
    ap.add_argument("--canon", default=str(CANON_PATH))
    ap.add_argument("--settlement", default=str(SETTLEMENT_PATH))
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--write", action="store_true", help="落工件到 artifacts/benchmark_settlement/")
    a = ap.parse_args(argv)
    payload = audit(load_canon(pathlib.Path(a.canon)),
                    load_settlement(pathlib.Path(a.settlement)),
                    canon_path=pathlib.Path(a.canon), settlement_path=pathlib.Path(a.settlement))
    print(render_md(payload))
    if a.write:
        print("written:", write_artifacts(payload))
    if a.json_out:
        p = pathlib.Path(a.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print("json:", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())