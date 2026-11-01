#!/usr/bin/env python3
"""Benchmark reference integrity gate for the ship benchmark dataset.

背景
----
``backend/data/benchmark_reference.json`` 是平台「对标算例」的唯一真值来源：
``api/domain/ship_benchmark.py`` 用它给阻力计算结果打 pass/fail。真值本身若不
自洽，任何下游验收（含 honest-FAIL 判定）都会被污染。

本工具对每条算例做**独立的物理恒等式复核**（不需要跑求解器、不产生算力负载）：

C1 Froude 恒等       Fr == V / sqrt(g L)              |Δ|/Fr <= 2%
C2 阻力恒等          Rt == Ct * 0.5 rho V^2 S         S 由几何复算，落在 +/-25%
C3 摩擦下限          Ct / Cf(Re, ITTC-57) 属于 [0.9, 3.5]
C4 随 Fr 的单调性    同一船型内 Ct 不得随 Fr 显著下降（容差 5%）

S 的复算优先用算例自带的 ``wetted_surface_m2``；否则用 Holtrop & Mennen (1982)
湿表面积公式并显式记录假设（CM / CWP / 介质），假设写进产物，便于复核。

判定
----
REAL      C1-C3 全过，且 S 来自算例自带值
DERIVED   C1-C3 全过，但 S 由公式估计（几何口径未直接给出）
WITHHELD  任一硬性检查（C1/C2/C3）失败 -> 该条不得再作为验收真值

用法
----
    python3 tools/audit/benchmark_reference_integrity.py            # 审计 + 产物
    python3 tools/audit/benchmark_reference_integrity.py --apply    # 回填 integrity 块
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

G = 9.81
# 介质属性: (密度 kg/m^3, 运动粘性系数 m^2/s) @ 15 degC
MEDIA = {"fresh": (998.2, 1.139e-6), "sea": (1025.0, 1.19e-6)}
FULL_SCALE_LENGTH_M = 50.0  # 超过此长度视为实船（海水）

# 每船型的几何假设（显式记录，供复核；不隐藏自由度）
ASSUMPTIONS: Dict[str, Dict[str, Any]] = {
    "SUBOFF": {},  # 算例自带湿表面积
    "DTMB-5415": {"cm": 0.85, "medium": "fresh"},        # 无桨驱逐舰模型，CM 取常见值
    "KVLCC2": {"cm": 0.9986, "cwp": 0.8564, "medium": "sea"},  # KRISO 设计主尺度
}

S_TOL = 0.25          # 湿表面积允许偏差
FR_TOL = 0.02         # Froude 恒等允许偏差
CT_CF_BAND = (0.9, 3.5)
TREND_TOL = 0.05      # 单调性容差（相对）


# ---------------------------------------------------------------------------
# 物理公式
# ---------------------------------------------------------------------------

def froude_number(speed_ms: float, length_m: float) -> float:
    """Fr = V / sqrt(g L)。"""
    return speed_ms / math.sqrt(G * length_m)


def ittc57_cf(reynolds: float) -> float:
    """ITTC-1957 平板摩擦系数 Cf = 0.075 / (log10 Re - 2)^2。"""
    if reynolds <= 1e4:
        raise ValueError(f"Reynolds {reynolds:.3g} outside ITTC-57 validity (Re > 1e4)")
    return 0.075 / (math.log10(reynolds) - 2.0) ** 2


def holtrop_wetted_surface(
    length: float,
    beam: float,
    draft: float,
    cb: float,
    cm: float = 0.98,
    cwp: Optional[float] = None,
    abt: float = 0.0,
) -> Dict[str, float]:
    """Holtrop & Mennen (1982) 湿表面积估计, S = L(2T+B)sqrt(CM)*(...)+2.38 ABT/CB.

    CWP 缺省用 Schneekluth 近似 CWP = (1 + 2 CB)/3。
    """
    if cwp is None:
        cwp = (1.0 + 2.0 * cb) / 3.0
    inner = (
        0.453
        + 0.4425 * cb
        - 0.2862 * cm
        - 0.003467 * beam / draft
        + 0.3696 * cwp
    )
    s = length * (2.0 * draft + beam) * math.sqrt(cm) * inner + 2.38 * abt / cb
    return {"wetted_surface_m2": s, "cwp_assumed": cwp, "cm_assumed": cm}


# ---------------------------------------------------------------------------
# 单条算例审计
# ---------------------------------------------------------------------------

def audit_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """对单条基准算例做 C1-C3 物理恒等复核，返回诊断字典。"""
    case_id = case.get("case_id", "?")
    ship_type = case.get("ship_type", "?")
    geo = case.get("geometry", {})
    L = geo.get("length_m")
    B = geo.get("beam_m") or geo.get("diameter_m")
    T = geo.get("draft_m") or geo.get("diameter_m")
    cb = geo.get("cb") or case.get("cb") or 0.5
    V = case.get("speed_ms")
    ct = case.get("experimental_ct")
    rt = case.get("experimental_rt_n")
    fr_rep = case.get("froude_number")

    if not L or V is None or not ct:
        return {
            "case_id": case_id,
            "ship_type": ship_type,
            "verdict": "WITHHELD",
            "reason": "insufficient fields (length_m / speed_ms / experimental_ct)",
            "checks": {},
        }

    _asum = ASSUMPTIONS.get(ship_type, {})
    medium = case.get("medium") or _asum.get("medium") or (
        "sea" if L > FULL_SCALE_LENGTH_M else "fresh"
    )
    rho, nu = MEDIA[medium]

    # C1 Froude 恒等
    fr_geom = froude_number(V, L)
    fr_err = abs(fr_rep - fr_geom) / fr_geom if fr_rep else None
    c1_ok = fr_err is not None and fr_err <= FR_TOL

    # 参考湿表面积：算例自带优先
    s_decl = geo.get("wetted_surface_m2")
    if s_decl:
        s_ref, s_src = float(s_decl), "declared"
        assumptions: Dict[str, Any] = {}
    else:
        if not B or not T:
            assumptions = {}
            s_ref, s_src = None, "unavailable"
        else:
            hw = holtrop_wetted_surface(
                L, B, T, cb,
                cm=_asum.get("cm", 0.98),
                cwp=_asum.get("cwp"),
            )
            s_ref, s_src = hw["wetted_surface_m2"], "holtrop1982"
            assumptions = {
                "cm": hw["cm_assumed"],
                "cwp": hw["cwp_assumed"],
                "cwp_source": "explicit" if _asum.get("cwp") else "schneekluth",
            }

    # C2 阻力恒等: S_implied = Rt / (Ct * q)
    q = 0.5 * rho * V * V
    s_impl = rt / (ct * q) if rt else None
    s_ratio = (s_impl / s_ref) if (s_impl and s_ref) else None
    c2_ok = s_ratio is not None and abs(s_ratio - 1.0) <= S_TOL

    # C3 摩擦下限
    re = V * L / nu
    cf = ittc57_cf(re)
    ct_over_cf = ct / cf
    c3_ok = CT_CF_BAND[0] <= ct_over_cf <= CT_CF_BAND[1]

    hard_fail = [n for n, ok in (("C1_froude", c1_ok), ("C2_resistance", c2_ok), ("C3_friction", c3_ok)) if not ok]

    if hard_fail:
        verdict, reason = "WITHHELD", "failed " + "+".join(hard_fail)
    elif s_src == "declared":
        verdict, reason = "REAL", "all identity checks pass (wetted surface declared)"
    else:
        verdict, reason = "DERIVED", "identity checks pass (wetted surface estimated)"

    # 由 Ct 反推的阻力（唯一可信的非量纲量是 Ct；S 已知时可重算 Rt）
    rt_derived = ct * q * s_ref if s_ref else None

    return {
        "case_id": case_id,
        "ship_type": ship_type,
        "medium": medium,
        "rho": rho,
        "nu": nu,
        "verdict": verdict,
        "reason": reason,
        "checks": {
            "C1_froude": {
                "pass": c1_ok,
                "froude_reported": fr_rep,
                "froude_from_geometry": round(fr_geom, 5),
                "rel_err": None if fr_err is None else round(fr_err, 4),
                "tolerance": FR_TOL,
            },
            "C2_resistance": {
                "pass": c2_ok,
                "rt_reported_n": rt,
                "rt_derived_from_ct_n": None if rt_derived is None else round(rt_derived, 2),
                "s_wetted_ref_m2": None if s_ref is None else round(s_ref, 3),
                "s_wetted_source": s_src,
                "s_wetted_implied_m2": None if s_impl is None else round(s_impl, 3),
                "implied_over_ref": None if s_ratio is None else round(s_ratio, 4),
                "tolerance": S_TOL,
                "assumptions": assumptions,
            },
            "C3_friction": {
                "pass": c3_ok,
                "reynolds": round(re, 1),
                "cf_ittc57": round(cf, 6),
                "ct_over_cf": round(ct_over_cf, 3),
                "band": list(CT_CF_BAND),
            },
        },
    }


# ---------------------------------------------------------------------------
# 数据集级审计（含 C4 趋势检查）
# ---------------------------------------------------------------------------

def audit_dataset(data: Dict[str, Any]) -> Dict[str, Any]:
    cases: List[Dict[str, Any]] = data.get("cases", [])
    results = [audit_case(c) for c in cases]

    # C4: 同船型内 Ct 随 Fr 的单调性
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for case, res in zip(cases, results):
        by_type.setdefault(case.get("ship_type", "?"), []).append(
            {
                "case_id": case.get("case_id"),
                "fr": case.get("froude_number"),
                "ct": case.get("experimental_ct"),
                "res": res,
            }
        )
    trend_flags: List[Dict[str, Any]] = []
    for stype, rows in by_type.items():
        rows.sort(key=lambda r: r["fr"] or 0.0)
        for prev, cur in zip(rows, rows[1:]):
            if prev["ct"] and cur["ct"] and cur["ct"] < prev["ct"] * (1.0 - TREND_TOL):
                trend_flags.append(
                    {
                        "ship_type": stype,
                        "from": prev["case_id"],
                        "to": cur["case_id"],
                        "ct_drop_pct": round((prev["ct"] - cur["ct"]) / prev["ct"] * 100, 2),
                    }
                )

    counts = {"REAL": 0, "DERIVED": 0, "WITHHELD": 0}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    return {
        "dataset": data.get("description", ""),
        "n_cases": len(cases),
        "verdict_counts": counts,
        "trend_flags": trend_flags,
        "cases": results,
        "tolerances": {
            "froude_rel": FR_TOL,
            "wetted_surface_rel": S_TOL,
            "ct_over_cf_band": list(CT_CF_BAND),
            "ct_trend_rel": TREND_TOL,
        },
    }


def format_markdown(audit: Dict[str, Any]) -> str:
    lines = [
        "# 基准算例真值完整性审计（benchmark_reference.json）",
        "",
        f"- 算例数：{audit['n_cases']}",
        f"- 判定分布：{audit['verdict_counts']}",
        f"- 容差：{json.dumps(audit['tolerances'], ensure_ascii=False)}",
        "",
        "| case | 船型 | 判定 | Fr(报表/几何复算) | Rt(报表/由Ct推导) N | S(引用/反推) m² | 反推/引用 | Ct/Cf |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in audit["cases"]:
        ck = c.get("checks", {})
        c1 = ck.get("C1_froude", {})
        c2 = ck.get("C2_resistance", {})
        c3 = ck.get("C3_friction", {})
        lines.append(
            "| {id} | {st} | **{v}** | {fr_r} / {fr_g} | {rt_r} / {rt_d} | {s_r} / {s_i} | {ratio} | {cf} |".format(
                id=c["case_id"],
                st=c["ship_type"],
                v=c["verdict"],
                fr_r=c1.get("froude_reported"),
                fr_g=c1.get("froude_from_geometry"),
                rt_r=c2.get("rt_reported_n"),
                rt_d=c2.get("rt_derived_from_ct_n"),
                s_r=c2.get("s_wetted_ref_m2"),
                s_i=c2.get("s_wetted_implied_m2"),
                ratio=c2.get("implied_over_ref"),
                cf=c3.get("ct_over_cf"),
            )
        )
    if audit["trend_flags"]:
        lines += ["", "## C4 单调性告警", ""]
        for f in audit["trend_flags"]:
            lines.append(
                f"- {f['ship_type']}: {f['from']} -> {f['to']} Ct 下降 {f['ct_drop_pct']}%"
            )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 回填 integrity 块
# ---------------------------------------------------------------------------

def apply_metadata(data: Dict[str, Any], audit: Dict[str, Any]) -> Dict[str, Any]:
    """把审计结论回填到数据集（保留原始值作 legacy，不伪造实验数据）。"""
    results = {r["case_id"]: r for r in audit["cases"]}
    for case in data.get("cases", []):
        r = results.get(case.get("case_id"))
        if not r:
            continue
        c2 = r["checks"].get("C2_resistance", {})
        block = {
            "verdict": r["verdict"],
            "reason": r["reason"],
            "froude_from_geometry": r["checks"].get("C1_froude", {}).get("froude_from_geometry"),
            "wetted_surface_ref_m2": c2.get("s_wetted_ref_m2"),
            "wetted_surface_ref_source": c2.get("s_wetted_source"),
            "wetted_surface_implied_m2": c2.get("s_wetted_implied_m2"),
            "rt_derived_from_ct_n": c2.get("rt_derived_from_ct_n"),
            "cf_ittc57": r["checks"].get("C3_friction", {}).get("cf_ittc57"),
            "medium": r["medium"],
            "auditor": "tools/audit/benchmark_reference_integrity.py",
        }
        if r["verdict"] == "WITHHELD":
            block["usable_as_ground_truth"] = False
            if case.get("experimental_rt_n") is not None:
                case.setdefault("legacy_rt_n", case["experimental_rt_n"])
            if case.get("froude_number") is not None:
                case.setdefault("legacy_froude_number", case["froude_number"])
        else:
            block["usable_as_ground_truth"] = True
        case["integrity"] = block
    data["integrity_gate"] = {
        "tool": "tools/audit/benchmark_reference_integrity.py",
        "verdict_counts": audit["verdict_counts"],
        "tolerances": audit["tolerances"],
    }
    return data


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        default=str(Path(__file__).resolve().parents[2] / "backend/data/benchmark_reference.json"),
    )
    parser.add_argument("--out-dir", default="artifacts/benchmark_integrity")
    parser.add_argument("--apply", action="store_true", help="回填 integrity 块到数据集")
    args = parser.parse_args(argv)

    data_path = Path(args.data)
    data = json.loads(data_path.read_text())
    audit = audit_dataset(data)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    md = format_markdown(audit)
    (out_dir / "audit.md").write_text(md)
    print(md)

    if args.apply:
        apply_metadata(data, audit)
        data_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"[apply] integrity 块已回填: {data_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())