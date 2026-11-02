#!/usr/bin/env python3
"""mission 1803 工件生成器：CII A–E 等级（G5）影子参考复核。

产出（数字全部程序化生成，报告零手抄）：
  artifacts/cii_rating_m1803/verification.json
  artifacts/cii_rating_m1803/cii_rating_bands.png
  artifacts/cii_rating_m1803/cii_grade_map.png

影子参考实现从测试文件导入（单一来源），避免口径分叉。
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for p in (str(BACKEND), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from services import green_navigation as gn  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "cii_ref_test", ROOT / "tests" / "test_green_cii_rating_reference_m1803.py"
)
_t = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_t)

shadow_cii_rating = _t.shadow_cii_rating
G5_DD = _t.G5_DD
GRADE_ORDER = _t.GRADE_ORDER
OUT = ROOT / "artifacts" / "cii_rating_m1803"


def rating_bands(required: float) -> dict[str, float]:
    return {
        "superior_A_bound": G5_DD[0] * required,
        "lower_B_bound": G5_DD[1] * required,
        "upper_C_bound": G5_DD[2] * required,
        "inferior_D_bound": G5_DD[3] * required,
    }


def ship_rows(ship_type: str, dwt: float, years=(2023, 2024, 2025, 2026)):
    out = []
    for y in years:
        req = gn.required_cii(ship_type, dwt, y)
        out.append({
            "year": y,
            "reference_value_2019": req["reference_value_2019"],
            "z_factor": req["z_factor"],
            "required_value": req["required_value"],
            "bands": rating_bands(req["required_value"]),
            "evidence_grade": req["evidence_grade"],
        })
    return out


def demo_vessels():
    """三条实船量级算例：达到值由 功率立方律→燃油→CO2→航程 程序化生成。

    attained(v) = P_ref·(v/v_ref)^3 · SFOC · C_F / (DWT · v)
    注：航时在分子分母中约去，故 CII 只依赖 (DWT, v, SFOC, C_F)，可独立手核。
    """
    cases = [
        {"name": "Handymax bulker 55k", "ship_type": "bulk_carrier",
         "dwt": 55_000.0, "p_ref_kw": 8_500.0, "v_ref_kn": 13.0, "v_kn": 12.0,
         "sfoc": 170.0, "fuel": "hfo"},
        {"name": "Aframax tanker 110k", "ship_type": "tanker",
         "dwt": 110_000.0, "p_ref_kw": 13_500.0, "v_ref_kn": 14.0, "v_kn": 12.5,
         "sfoc": 172.0, "fuel": "hfo"},
        {"name": "Post-Panamax box 100k", "ship_type": "container_ship",
         "dwt": 100_000.0, "p_ref_kw": 42_000.0, "v_ref_kn": 21.0, "v_kn": 17.0,
         "sfoc": 168.0, "fuel": "hfo"},
    ]
    rows = []
    for c in cases:
        cf = gn.carbon_factor(c["fuel"])
        p_kw = c["p_ref_kw"] * (c["v_kn"] / c["v_ref_kn"]) ** 3
        hours = 5_000.0
        distance_nm = c["v_kn"] * hours
        co2_g = gn.co2_from_power(p_kw, c["sfoc"], cf, hours=hours)
        attained = gn.attained_cii(co2_g, c["dwt"], distance_nm)
        per_year = []
        for y in (2023, 2024, 2025, 2026):
            req = gn.required_cii(c["ship_type"], c["dwt"], y)
            per_year.append({
                "year": y, "required_value": req["required_value"],
                "ratio": attained / req["required_value"],
                "grade": shadow_cii_rating(attained, req["required_value"]),
            })
        rows.append({
            "name": c["name"], "ship_type": c["ship_type"], "dwt": c["dwt"],
            "speed_kn": c["v_kn"], "p_me_kw": p_kw, "sfoc": c["sfoc"],
            "hours": hours, "distance_nm": distance_nm,
            "co2_t": co2_g / 1.0e6, "attained_cii": attained,
            "bands_2026": rating_bands(
                gn.required_cii(c["ship_type"], c["dwt"], 2026)["required_value"]),
            "grades": per_year,
        })
    return rows


def speed_sweep():
    """Handymax 55k 散货船：慢速航行对 CII 等级的影响（2024 年 Z=7%）。"""
    dwt, p_ref, v_ref, sfoc = 55_000.0, 8_500.0, 13.0, 170.0
    cf = gn.carbon_factor("hfo")
    req = gn.required_cii("bulk_carrier", dwt, 2024)["required_value"]
    out = []
    for v in [9.0 + 0.25 * i for i in range(25)]:          # 9.0 … 15.0 kn
        p_kw = p_ref * (v / v_ref) ** 3
        attained = gn.attained_cii(gn.co2_from_power(p_kw, sfoc, cf), dwt, v)
        out.append({"speed_kn": v, "p_me_kw": p_kw, "attained_cii": attained,
                    "ratio": attained / req, "grade": shadow_cii_rating(attained, req)})
    return {"dwt": dwt, "required_value_2024": req, "sweep": out}


def invariants():
    """五类不变式在稠密采样上的实测结果（供报告表格）。"""
    required = 6.0
    samples = [required * (0.30 + 0.001 * i) for i in range(1200)]
    idx = [GRADE_ORDER.index(shadow_cii_rating(a, required)) for a in samples]
    mono = all(b >= a for a, b in zip(idx, idx[1:]))
    max_jump = max(b - a for a, b in zip(idx, idx[1:]))
    scale_ok = all(
        shadow_cii_rating(6.0 * f, required * f) == shadow_cii_rating(6.0, required)
        for f in (1e-6, 1e-3, 0.5, 2.0, 1e3, 1e6)
    )
    boundary_ok = True
    for r in (0.25, 1.0, 4.0, 17.5, 100.0):
        for dd in G5_DD:
            if shadow_cii_rating(dd * r, r) != GRADE_ORDER[G5_DD.index(dd)]:
                boundary_ok = False
    return {
        "n_samples": len(samples),
        "monotone_nonincreasing_grade_index": mono,
        "max_grade_jump_per_0.1pct_step": max_jump,
        "scale_invariance": scale_ok,
        "boundary_closed_on_worse_side": boundary_ok,
        "grades_reachable": sorted({shadow_cii_rating(a, required) for a in samples}),
    }


def arithmetic_residuals():
    """L3 应用算术 required = ref·(1−Z) 的机器残差（应逐位为 0）。"""
    worst = 0.0
    rows = []
    for ship_type, dwt in (("bulk_carrier", 80_000.0), ("tanker", 110_000.0),
                           ("container_ship", 100_000.0), ("gas_carrier", 60_000.0)):
        a, c = gn.CII_REFERENCE_LINES[ship_type]
        for y, z in sorted(gn.CII_Z_FACTOR.items()):
            res = gn.required_cii(ship_type, dwt, y)
            ref = a * dwt ** (-c)
            req = ref * (1.0 - z)
            rel_ref = abs(res["reference_value_2019"] - ref) / ref
            rel_req = abs(res["required_value"] - req) / req
            worst = max(worst, rel_ref, rel_req)
            rows.append({
                "ship_type": ship_type, "dwt": dwt, "year": y,
                "rel_err_reference": rel_ref, "rel_err_required": rel_req,
            })
    return {"rows": rows, "worst_rel_err": worst}


def plot(vessels, out_dir: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    # ---- Figure 1: rating bands vs DWT (bulk carrier, 2026) + demo vessel points ----
    fig, ax = plt.subplots(figsize=(9.4, 5.4), dpi=150)
    dwt = np.logspace(math.log10(5_000), math.log10(320_000), 240)
    a, c = gn.CII_REFERENCE_LINES["bulk_carrier"]
    required = a * dwt ** (-c) * (1.0 - gn.CII_Z_FACTOR[2026])
    b1, b2, b3, b4 = (G5_DD[0] * required, G5_DD[1] * required,
                      G5_DD[2] * required, G5_DD[3] * required)
    ax.fill_between(dwt, 1e-3, b1, color="#2e7d32", alpha=0.16)
    ax.fill_between(dwt, b1, b2, color="#7cb342", alpha=0.18)
    ax.fill_between(dwt, b2, b3, color="#fdd835", alpha=0.30)
    ax.fill_between(dwt, b3, b4, color="#fb8c00", alpha=0.22)
    ax.fill_between(dwt, b4, 1e3, color="#c62828", alpha=0.13)
    for arr, lbl, ls in ((b1, "A", ":"), (b2, "B", "--"), (b3, "C", "-."), (b4, "D", "-")):
        ax.plot(dwt, arr, ls, lw=1.0, color="k", alpha=0.7)
    for ytxt, arr in (("A", b1), ("B", b2), ("C", b3), ("D", b4), ("E", b4 * 1.28)):
        ax.annotate(ytxt, (3.0e5, arr[-1]), fontsize=9, ha="left", va="center")
    for v in vessels:
        if v["ship_type"] != "bulk_carrier":
            continue
        g = [r for r in v["grades"] if r["year"] == 2026][0]
        ax.plot(v["dwt"], v["attained_cii"], "o", ms=9, mfc="w", mec="k", mew=1.7, zorder=5)
        ax.annotate(f"{v['name']} @ {v['speed_kn']:.1f} kn\n"
                    f"CII={v['attained_cii']:.2f}, ratio={g['ratio']:.2f} -> {g['grade']}",
                    (v["dwt"], v["attained_cii"]), textcoords="offset points",
                    xytext=(14, 12), fontsize=8.5)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_ylim(1e-3, 100)
    ax.set_xlabel("Deadweight DWT [t] (log)")
    ax.set_ylabel("CII [gCO$_2$/(t$\\cdot$nm)] (log)")
    ax.set_title("CII rating bands, bulk carrier 2026 (Z=11%), boundaries dd=0.86/0.94/1.06/1.18\n"
                 "open markers = demo vessel attained values graded by shadow reference")
    ax.grid(True, which="both", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_dir / "cii_rating_bands.png")
    plt.close(fig)

    # ---- Figure 2: speed sweep -> grade transitions (physical, decision relevant) ----
    sw = speed_sweep()
    fig, ax = plt.subplots(figsize=(8.8, 4.6), dpi=150)
    req = sw["required_value_2024"]
    v = np.array([r["speed_kn"] for r in sw["sweep"]])
    at = np.array([r["attained_cii"] for r in sw["sweep"]])
    ax.fill_between(v, 0, G5_DD[0] * req, color="#2e7d32", alpha=0.16)
    ax.fill_between(v, G5_DD[0] * req, G5_DD[1] * req, color="#7cb342", alpha=0.18)
    ax.fill_between(v, G5_DD[1] * req, G5_DD[2] * req, color="#fdd835", alpha=0.30)
    ax.fill_between(v, G5_DD[2] * req, G5_DD[3] * req, color="#fb8c00", alpha=0.22)
    ax.fill_between(v, G5_DD[3] * req, at.max() * 1.1, color="#c62828", alpha=0.13)
    for dd, lbl in zip(G5_DD, ("A|B", "B|C", "C|D", "D|E")):
        ax.axhline(dd * req, color="k", ls=":", lw=1.0, alpha=0.75)
        ax.annotate(f"{lbl}  {dd}x req", (v[0] + 0.05, dd * req + 0.06),
                    fontsize=8, va="bottom")
    ax.plot(v, at, "-o", ms=4, color="#1565c0", lw=2.0, label="attained CII $\\propto v^2$")
    for r in sw["sweep"]:
        if abs(r["speed_kn"] - round(r["speed_kn"])) < 1e-9 and int(r["speed_kn"]) in (10, 11, 12, 13, 14, 15):
            ax.annotate(f"{r['grade']}", (r["speed_kn"], r["attained_cii"]),
                        textcoords="offset points", xytext=(6, -12), fontsize=9,
                        color="#1565c0", fontweight="bold")
    ax.set_xlabel("ship speed [kn]  (power $\\propto v^3$, Handymax bulker 55k DWT, 2024)")
    ax.set_ylabel("CII [gCO$_2$/(t$\\cdot$nm)]")
    ax.set_title("Slow steaming moves the grade: D at 13 kn -> A at 10 kn (G5 bands, shadow reference)")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left", fontsize=8.5)
    fig.tight_layout()
    fig.savefig(out_dir / "cii_grade_map.png")
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    vessels = demo_vessels()
    payload = {
        "mission_id": 1803,
        "repo": "TensorPlatform",
        "domain": "green_navigation / L4 CII rating (G5)",
        "g5_dd_vector": list(G5_DD),
        "z_schedule": dict(sorted(gn.CII_Z_FACTOR.items())),
        "cii_reference_lines": {k: list(v) for k, v in gn.CII_REFERENCE_LINES.items()},
        "rating_status": gn.cii_rating_status(),
        "ship_required_bands": {
            "bulk_carrier_80000": ship_rows("bulk_carrier", 80_000.0),
            "tanker_110000": ship_rows("tanker", 110_000.0),
            "container_100000": ship_rows("container_ship", 100_000.0),
            "gas_carrier_60000": ship_rows("gas_carrier", 60_000.0),
        },
        "demo_vessels": vessels,
        "speed_sweep_handymax_55k_2024": speed_sweep(),
        "invariants": invariants(),
        "l3_arithmetic": arithmetic_residuals(),
    }
    (OUT / "verification.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    plot(vessels, OUT)
    print("worst L3 arithmetic rel err:", payload["l3_arithmetic"]["worst_rel_err"])
    print("invariants:", json.dumps(payload["invariants"], ensure_ascii=False))
    for v in vessels:
        print(v["name"], "CII=%.3f" % v["attained_cii"],
              [(g["year"], g["grade"], round(g["ratio"], 3)) for g in v["grades"]])
    print("artifacts ->", OUT)


if __name__ == "__main__":
    main()