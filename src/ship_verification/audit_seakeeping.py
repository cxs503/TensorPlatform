"""``耐波/seakeeping`` 端点的独立审计（m1809，承接 m1780「耐波伪表」缺陷线索）。

背景
----
``api/calculations/seakeeping`` 自述 ``method = "切片理论 + 经验公式"``，但没有任何
独立复核记录，且它**不在**平台 21 项能力台账里（既无 REAL 也无 MOCK 定级）——
即"有端点、无证据"。本审计把它变成可机器裁决的对象。

判据（每条都能被第三方用本仓代码重算）
--------------------------------------
============  ================================================================
id            判据
============  ================================================================
G-CB          几何先行：输入 Cb 必须与 Δ/(ρ·L·B·T) 一致（±5%），否则该案例作废
H-LONGWAVE    ω→0（λ≫L）时 RAO_heave → 1（**精确恒等**，船随波面平移）；误差>15% ⇒ FAIL
H-FREQ        线性耐波解必含 ζ̈、ζ̇ ⇒ RAO_heave 在周期扫描上必须有共振峰；
              要求 max/min ≥ 1.5；恒等 ⇒ FAIL（结构性问题，与取值无关）
H-HEADING     RAO 必须随航向变化（遭遇频率改变）；三航向逐位相同 ⇒ FAIL
H-ROLL-SYM    ψ=180°/0° 时横摇激励恒为 0（左右对称，FK 横摇矩逐项抵消，**精确**）；
              roll RAO > 0.05 rad/rad ⇒ FAIL
H-LINEAR      RAO 必须与波高无关（线性理论）；此项应 PASS，用于证明判据非"全否"
H-A33         剖面附加质量（本仓面元核，解析锚 ≤0.4%）与端点 ``added_mass_heave``
              对账；误差 >15% ⇒ FAIL
============  ================================================================

证据等级由 FAIL 条数决定：≥2 条硬 FAIL ⇒ MOCK；1 条 ⇒ WITHHELD；0 条 ⇒ REAL。

用法::

    python -m ship_verification.audit_seakeeping --out docs/m1809/outputs --figures

网络客户端以 ``fetch`` 注入，便于离线单测（见 tests/audit/test_ship_perf_seakeeping.py）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from ship_perf import seakeeping as sk
from ship_perf.constants import G, RHO_SEA

DEFAULT_BASE = "http://127.0.0.1:8001/api/v1"
ENDPOINT = "/calculations/seakeeping"

#: 平台精度标准：>15% 不可接受
TOL_HARD = 0.15
TOL_GOAL = 0.03


# ---------------------------------------------------------------- 案例


@dataclass
class Case:
    name: str
    length: float
    beam: float
    draft: float
    cb: float
    displacement_t: float
    speed_kn: float = 5.0
    note: str = ""

    @property
    def volume(self) -> float:
        return self.cb * self.length * self.beam * self.draft

    @property
    def cb_from_displacement(self) -> float:
        return self.displacement_t * 1000.0 / (RHO_SEA * self.length * self.beam * self.draft)

    def hull(self) -> sk.Hull:
        return sk.Hull(self.length, self.beam, self.draft, self.cb)

    def payload(self, wave_height: float, wave_period: float, heading: float) -> Dict[str, float]:
        return {
            "length": self.length, "beam": self.beam, "draft": self.draft, "cb": self.cb,
            "speed": self.speed_kn, "heading": heading,
            "displacement": self.displacement_t,
            "wave_height": wave_height, "wave_period": wave_period,
        }


CASES: List[Case] = [
    Case("M100", 100.0, 15.0, 5.0, 0.60, 4616.0, 12.0, "中型多用途船（Cb·LBT 自洽）"),
    Case("KVLCC2-like", 320.0, 58.0, 20.8, 0.8098, 320000.0, 15.0, "SIMMAN 2008 油轮量级"),
    Case("S30", 30.0, 8.0, 2.5, 0.55, 338.0, 9.0, "小型船（λ/L 覆盖宽）"),
]

PERIODS: Sequence[float] = (4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 20.0, 24.0, 30.0)
HEADINGS: Sequence[float] = (180.0, 135.0, 90.0)
HEIGHTS: Sequence[float] = (1.0, 2.0, 4.0)


# ---------------------------------------------------------------- 网络


def http_fetch(base: str, timeout: float = 20.0) -> Callable[[Dict[str, float]], Dict]:
    def _fetch(payload: Dict[str, float]) -> Dict:
        req = urllib.request.Request(
            base + ENDPOINT, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    return _fetch


# ---------------------------------------------------------------- 判据


def _err(v: float, ref: float) -> float:
    return abs(v - ref) / abs(ref) if ref else float("inf")


@dataclass
class Criterion:
    cid: str
    description: str
    measured: object
    threshold: str
    verdict: str            # PASS / FAIL / WITHHELD
    detail: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "id": self.cid, "description": self.description, "measured": self.measured,
            "threshold": self.threshold, "verdict": self.verdict, "detail": self.detail,
        }


def wave_length(period: float) -> float:
    """深水波长 λ = gT²/2π（m）。"""
    return G * period * period / (2.0 * math.pi)


def audit_case(case: Case, fetch: Callable[[Dict[str, float]], Dict]) -> Dict[str, object]:
    """单案例全判据。返回 JSON 友好字典。"""
    hull = case.hull()
    mass = case.displacement_t * 1000.0
    cb_consistency = _err(case.cb, case.cb_from_displacement)

    sweep: List[Dict[str, object]] = []
    for heading in HEADINGS:
        for period in PERIODS:
            resp = fetch(case.payload(2.0, period, heading))
            sweep.append({
                "heading": heading, "period": period,
                "omega": 2.0 * math.pi / period, "wave_length": wave_length(period),
                "lambda_over_L": wave_length(period) / case.length,
                **{k: resp.get(k) for k in (
                    "heave_rao_max", "roll_rao_max", "pitch_rao_max",
                    "heave_amplitude", "roll_amplitude", "pitch_amplitude",
                    "added_mass_heave", "added_mass_roll", "added_mass_pitch",
                    "vertical_acc", "lateral_acc", "roll_natural_period", "method")},
            })

    heights: List[Dict[str, object]] = []
    for h in HEIGHTS:
        resp = fetch(case.payload(h, 8.0, 180.0))
        heights.append({"wave_height": h,
                        "heave_rao_max": resp.get("heave_rao_max"),
                        "heave_amplitude": resp.get("heave_amplitude"),
                        "roll_rao_max": resp.get("roll_rao_max")})

    crit: List[Criterion] = []

    # G-CB：几何先行
    crit.append(Criterion(
        "G-CB", "输入 Cb 与 Δ/(ρLBT) 一致（几何先行，±5%）",
        round(cb_consistency * 100, 3), "≤5%",
        "PASS" if cb_consistency <= 0.05 else "FAIL",
        f"Cb_in={case.cb:.4f} vs Δ/(ρLBT)={case.cb_from_displacement:.4f}"))

    # H-LONGWAVE：ω→0 精确极限 RAO=1
    head = [r for r in sweep if r["heading"] == 180.0]
    longest = max(head, key=lambda r: r["period"])
    api_long = float(longest["heave_rao_max"])
    # 参考值：同一周期下本仓切片核的 RAO（其 ω→0 极限精确为 1，见
    # tests/audit/test_m1809_seakeeping.py::test_heave_rao_tends_to_unity_for_long_waves）
    mass0 = case.displacement_t * 1000.0
    a33_0 = sk.strip_heave_added_mass(hull, RHO_SEA, 15, 12)["added_mass_heave"]
    ref_long = sk.heave_rao(hull, [longest["period"]], mass0, damping_ratio=0.15,
                            a33=a33_0)[0]["rao_heave"]
    long_err = _err(api_long, ref_long)
    crit.append(Criterion(
        "H-LONGWAVE", f"λ/L≥4 时 RAO_heave→1（长波极限）；端点须与参考核一致",
        {"period_s": longest["period"], "lambda_over_L": round(float(longest["lambda_over_L"]), 2),
         "api_rao": api_long, "kernel_rao": ref_long, "exact_limit": 1.0,
         "error": round(long_err, 4)},
        "≤15%（平台不可接受线），目标 ≤3%",
        "PASS" if long_err <= TOL_HARD else "FAIL",
        f"端点 {api_long:.3f} vs 参考核 {ref_long:.3f}（精确极限 1.000）→ 误差 {long_err*100:.1f}%"))

    # H-FREQ：周期敏感性（线性解必有共振峰）
    raos = [float(r["heave_rao_max"]) for r in head]
    ratio = (max(raos) / min(raos)) if min(raos) > 0 else float("inf")
    crit.append(Criterion(
        "H-FREQ", "RAO_heave 在周期扫描上必须有共振结构（max/min ≥ 1.5）",
        {"max": max(raos), "min": min(raos), "ratio": round(ratio, 4),
         "periods": list(PERIODS)},
        "ratio ≥ 1.5",
        "PASS" if ratio >= 1.5 else "FAIL",
        "线性耐波解含 ζ̈、ζ̇ ⇒ RAO(ω) 不可能恒定；恒定说明返回值与周期无关（伪表）"))

    # H-HEADING：航向敏感性
    heave_by_heading = {
        h: [float(r["heave_rao_max"]) for r in sweep if r["heading"] == h] for h in HEADINGS
    }
    spread = max(max(v) for v in heave_by_heading.values()) - min(
        min(v) for v in heave_by_heading.values())
    crit.append(Criterion(
        "H-HEADING", "RAO 随航向变化（遭遇频率改变）",
        {str(k): v for k, v in heave_by_heading.items()},
        "三航向极差 > 1e-6",
        "PASS" if spread > 1e-6 else "FAIL",
        f"三航向 heave RAO 极差 = {spread:.3e}"))

    # H-ROLL-SYM：顶浪/随浪横摇激励恒 0
    roll_head = [float(r["roll_rao_max"]) for r in head]
    fk_roll_180 = max(sk.fk_roll_moment_free_surface(hull, 1.0, t, 180.0)
                      for t in (6.0, 8.0, 10.0, 12.0))
    fk_roll_90 = max(sk.fk_roll_moment_free_surface(hull, 1.0, t, 90.0)
                     for t in (6.0, 8.0, 10.0, 12.0))
    max_roll_head = max(roll_head) if roll_head else float("nan")
    crit.append(Criterion(
        "H-ROLL-SYM", "ψ=180° 横摇激励恒 0（对称性，精确）",
        {"api_roll_rao_max_head_seas": max_roll_head,
         "kernel_fk_roll_moment_180": fk_roll_180,
         "kernel_fk_roll_moment_90": fk_roll_90},
        "roll RAO ≤ 0.05 rad/rad（|M4_180| < 1e-6·|M4_90|）",
        "PASS" if max_roll_head <= 0.05 else "FAIL",
        f"端点顶浪横摇 RAO={max_roll_head:.3f}；本仓 FK 横摇矩 180°={fk_roll_180:.3e} vs "
        f"90°={fk_roll_90:.3e} N·m"))

    # H-LINEAR：波高无关（应 PASS）
    rao_by_h = [float(h["heave_rao_max"]) for h in heights]
    hspread = max(rao_by_h) - min(rao_by_h)
    crit.append(Criterion(
        "H-LINEAR", "RAO 与波高无关（线性理论）",
        {"wave_heights": list(HEIGHTS), "rao": rao_by_h, "spread": round(hspread, 6)},
        "极差 ≤ 1e-6",
        "PASS" if hspread <= 1e-6 else "FAIL",
        "此项应 PASS：用于证明判据体系不是'全否'"))

    # H-A33：剖面附加质量对账
    a33 = sk.strip_heave_added_mass(hull, RHO_SEA, 15, 12)["added_mass_heave"]
    api_a33 = float(head[0]["added_mass_heave"])
    a33_err = _err(api_a33, a33)
    crit.append(Criterion(
        "H-A33", "端点 added_mass_heave 对账本仓面元核（解析锚 ≤0.4%）",
        {"kernel_kg": a33, "api_kg": api_a33, "error": round(a33_err, 4),
         "kernel_ratio_to_displacement": round(a33 / mass, 4)},
        "≤15%",
        "PASS" if a33_err <= TOL_HARD else "FAIL",
        f"{api_a33:.4e} vs 面元 {a33:.4e} kg → 误差 {a33_err*100:.1f}%"))

    hard_fail = [c.cid for c in crit if c.verdict == "FAIL"]
    grade = "MOCK" if len(hard_fail) >= 2 else ("WITHHELD" if hard_fail else "REAL")

    tn = sk.heave_natural_period(hull, mass, RHO_SEA, a33=a33)
    return {
        "case": {"name": case.name, "L": case.length, "B": case.beam, "T": case.draft,
                 "Cb": case.cb, "displacement_t": case.displacement_t, "note": case.note},
        "sweep": sweep,
        "wave_height_sweep": heights,
        "criteria": [c.as_dict() for c in crit],
        "kernel": {"a33_kg": a33, "natural_period_s": tn["period_n"],
                   "natural_omega": tn["omega_n"], "stiffness_N_per_m": tn["stiffness"],
                   "rao_band": _rao_band(hull, mass, a33)},
        "grade": grade,
        "hard_fails": hard_fail,
    }


def _rao_band(hull: sk.Hull, mass: float, a33: float) -> List[Dict[str, float]]:
    """阻尼比带下的升沉 RAO 曲线（阻尼为参数化 ⇒ 峰值幅值 WITHHELD）。"""
    rows: Dict[float, Dict[str, float]] = {}
    for zeta in (0.05, 0.15, 0.30):
        for r in sk.heave_rao(hull, PERIODS, mass, damping_ratio=zeta, a33=a33):
            row = rows.setdefault(r["period"], {"period": r["period"]})
            row[f"rao_zeta_{zeta:g}"] = r["rao_heave"]
    return [rows[k] for k in sorted(rows)]


# ---------------------------------------------------------------- 报告


def run(base: str = DEFAULT_BASE, cases: Sequence[Case] = tuple(CASES),
        fetch: Optional[Callable] = None) -> Dict[str, object]:
    fetch = fetch or http_fetch(base)
    results = []
    for case in cases:
        results.append(audit_case(case, fetch))
    all_fails = [cid for r in results for cid in r["hard_fails"]]
    overall = "MOCK" if len(all_fails) >= 2 else ("WITHHELD" if all_fails else "REAL")
    return {
        "mission": "m1809",
        "endpoint": ENDPOINT,
        "base": base,
        "periods": list(PERIODS),
        "headings": list(HEADINGS),
        "cases": results,
        "overall_grade": overall,
        "overall_hard_fails": sorted(set(all_fails)),
        "kernel_evidence": sk.EVIDENCE,
        "thresholds": {"hard": TOL_HARD, "goal": TOL_GOAL},
    }


def to_markdown(rep: Dict[str, object]) -> str:
    lines = [
        "# 耐波（seakeeping）端点独立审计 — m1809",
        "",
        f"端点：`{rep['endpoint']}`  ·  总证据等级：**{rep['overall_grade']}**",
        "",
        f"硬 FAIL 判据：{', '.join(rep['overall_hard_fails']) or '无'}",
        "",
        "## 逐案例判据",
        "",
        "| 案例 | 判据 | 判定 | 实测 |",
        "|------|------|------|------|",
    ]
    for case in rep["cases"]:
        for c in case["criteria"]:
            meas = json.dumps(c["measured"], ensure_ascii=False)
            if len(meas) > 160:
                meas = meas[:157] + "..."
            lines.append(f"| {case['case']['name']} | {c['id']} | {c['verdict']} | {meas} |")
    lines += ["", "## 参考核（本仓面元法）", ""]
    for case in rep["cases"]:
        k = case["kernel"]
        lines.append(
            f"- **{case['case']['name']}**：A33 = {k['a33_kg']:.4e} kg，"
            f"升沉固有周期 T_n = {k['natural_period_s']:.2f} s，"
            f"C = {k['stiffness_N_per_m']:.4e} N/m")
    lines += ["", "## 判据说明（可第三方复算）", ""]
    for cid, ev in rep["kernel_evidence"].items():
        lines.append(f"- `{cid}` → {ev}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="audit seakeeping endpoint (m1809)")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--out", default="docs/m1809/outputs")
    ap.add_argument("--figures", action="store_true")
    args = ap.parse_args(argv)

    rep = run(args.base)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "seakeeping_audit.json"), "w") as fh:
        json.dump(rep, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "seakeeping_audit.md"), "w") as fh:
        fh.write(to_markdown(rep))
    print(f"overall grade: {rep['overall_grade']}  fails={rep['overall_hard_fails']}")
    for case in rep["cases"]:
        print(" ", case["case"]["name"], case["grade"],
              [(c["id"], c["verdict"]) for c in case["criteria"]])
    if args.figures:
        from ship_verification.seakeeping_figures import make_all
        make_all(rep, args.out)
        print("figures →", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())