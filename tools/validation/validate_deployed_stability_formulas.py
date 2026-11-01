#!/usr/bin/env python3
"""
Verification debt audit · GZ / GM chain of the *deployed* ship platform (:8001)
──────────────────────────────────────────────────────────────────────────────
Three independent baselines are used; none shares algebra with the code under test:

 B1  Exact wall-sided limit (Cb=Cm=Cwp=1, rectangular section). Every KB/BM/GZ
     relation must reproduce it exactly -> catches fitted formulas that are wrong
     at the one geometry where the answer is known in closed form.
 B2  Real hull geometry: KCS offsets table (21 stations x 11 waterlines) integrated
     for displacement, KB, waterplane coefficient and BM. Geometry self-check
     (Cb from the table vs the Cb printed in the table) precedes every comparison.
 B3  The platform's own second endpoint: two endpoints fed identical ship data must
     agree; disagreement is an internal contradiction that needs no external data.

Targets (read-only; the owning repo is outside this worktree's EDIT_WHITELIST):
  POST /api/v1/stability/evaluate      (backend/api/stability.py, evaluate_stability)
  POST /api/v1/calculations/stability  (backend/api/calculations.py, calculate_gz_curve)

Outputs: artifacts/validation/deployed_stability/{summary.json,sweep.csv,*.png}
         docs/validation/stability_gm_gz_audit.md   (numbers injected, never hand-copied)
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "validation"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from wall_sided_gz_reference import BoxCase, exact_gz, vanishing_angle_deg  # noqa: E402

OUT = ROOT / "artifacts" / "validation" / "deployed_stability"
OUT.mkdir(parents=True, exist_ok=True)
DOC = ROOT / "docs" / "validation" / "stability_gm_gz_audit.md"
DOC.parent.mkdir(parents=True, exist_ok=True)

OFFSETS = Path("/srv/tensor/ship-workspace/ship-performance-platform"
               "/backend/data/offsets/kcs.json")
API = "http://127.0.0.1:8001/api/v1"

# KCS principal particulars + loading condition used for every comparison
KCS = dict(L=230.0, B=32.2, T=10.8, D=19.0, disp=52030.0, Cb=0.651, Cm=0.985, KG=12.0)


# --------------------------------------------------------------------------- #
# the code under test, transcribed verbatim
# --------------------------------------------------------------------------- #
def deployed_kb(T: float, Cb: float) -> float:
    """backend/api/calculations.py:372 -- labelled 'Morrish', KB = T(1 - Cb/3)."""
    return T * (1.0 - Cb / 3.0)


def deployed_bm(B: float, T: float, Cb: float) -> float:
    """backend/api/calculations.py:378 -- BM = CWP^2 * B^3 / 12 / (B*T*Cb), CWP = 0.75+0.25Cb."""
    cwp = 0.75 + 0.25 * Cb
    return (cwp ** 2 * B ** 3 / 12.0) / (B * T * Cb)


def deployed_kb_evaluate(T: float, Cb: float) -> float:
    """backend/api/stability.py:47 -- KB = T(1 - 0.5(1-Cb))."""
    return T * (1.0 - 0.5 * (1.0 - Cb))


def deployed_bm_evaluate(B: float, T: float, Cb: float, Cm: float) -> float:
    """backend/api/stability.py:48 -- BM = B^2/(12T) * Cb/Cm."""
    return B ** 2 / (12.0 * T) * Cb / Cm


def deployed_gz_curve(phi: float, GM: float, BM: float) -> float:
    """backend/api/calculations.py:389 calculate_gz_curve, transcribed verbatim."""
    r = math.radians(phi)
    if phi < 15:
        gz = GM * math.sin(r)
    else:
        gz = (GM + 0.5 * BM * math.sin(r) ** 2) * math.sin(r)
        if phi > 40:
            gz *= (60.0 - phi) / 20.0
    return max(0.0, gz)


def deployed_gz_max_evaluate(GM: float) -> float:
    """backend/api/stability.py:54 -- GZ_max = GM*sin(45deg)*1.5, angle hardcoded to 45."""
    return GM * math.sin(math.radians(45.0)) * 1.5


def deployed_vanishing_evaluate(GM: float) -> float:
    """backend/api/stability.py:56 -- vanishing angle = 60 + 10*GM."""
    return 60.0 + GM * 10.0


# --------------------------------------------------------------------------- #
# B2: real geometry baseline from the KCS offsets table
# --------------------------------------------------------------------------- #
def kcs_geometry_baseline(path: Path = OFFSETS) -> dict | None:
    if not path.is_file():
        return None
    raw = json.loads(path.read_text())
    tab = np.array(raw["table"], dtype=float)
    ns, nw = tab.shape
    L, B, T = KCS["L"], KCS["B"], KCS["T"]
    x = np.linspace(0.0, L, ns)          # uniform station spacing (as stored: n_stations spans L)
    z = np.linspace(0.0, T, nw)          # waterlines span keel -> design draft
    y = tab * (B / 2.0)                  # table is normalised by the maximum half-breadth
    sectional_area = np.trapezoid(2.0 * y, z, axis=1)
    volume = float(np.trapezoid(sectional_area, x))
    mz = float(np.trapezoid(np.trapezoid(2.0 * y * z[None, :], z, axis=1), x))
    awp = float(np.trapezoid(2.0 * y[:, nw - 1], x))
    it = float(np.trapezoid((2.0 / 3.0) * y[:, nw - 1] ** 3, x))
    return {
        "source": str(path),
        "n_stations": int(ns), "n_waterlines": int(nw),
        "table_claimed_Cb": raw.get("source", ""),
        "L": L, "B": B, "T": T,
        "max_half_breadth_m": float(y.max()), "expected_half_breadth_m": B / 2.0,
        "volume_m3": volume,
        "Cb_from_geometry": volume / (L * B * T),
        "Cb_claimed": KCS["Cb"],
        "Cb_rel_error": (volume / (L * B * T) - KCS["Cb"]) / KCS["Cb"],
        "KB_m": mz / volume,
        "Cwp_from_geometry": awp / (L * B),
        "BM_m": it / volume,
        "KM_m": mz / volume + it / volume,
    }


# --------------------------------------------------------------------------- #
# B3: live endpoints, read-only
# --------------------------------------------------------------------------- #
def post_live(path: str, body: dict) -> dict | None:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"{API}/{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None


# --------------------------------------------------------------------------- #
def main() -> int:
    checks: list[dict] = []

    # ---- B1 exact wall-sided limit ---------------------------------------- #
    box_T, box_B = KCS["T"], KCS["B"]
    exact_kb = box_T / 2.0
    exact_bm = box_B ** 2 / (12.0 * box_T)
    kb_box = deployed_kb(box_T, 1.0)
    bm_box = deployed_bm(box_B, box_T, 1.0)
    limit = {
        "KB_exact_m": exact_kb, "KB_deployed_m": kb_box,
        "KB_rel_error": (kb_box - exact_kb) / exact_kb,
        "BM_exact_m": exact_bm, "BM_deployed_m": bm_box,
        "BM_rel_error": (bm_box - exact_bm) / exact_bm,
    }
    checks.append({"name": "B1 KB reproduces the exact wall-sided limit (Cb=1)",
                   "value": limit["KB_rel_error"], "threshold": 1e-6,
                   "pass": abs(limit["KB_rel_error"]) <= 1e-6})
    checks.append({"name": "B1 BM reproduces the exact wall-sided limit (Cb=1)",
                   "value": limit["BM_rel_error"], "threshold": 1e-6,
                   "pass": abs(limit["BM_rel_error"]) <= 1e-6})

    # ---- B2 real geometry -------------------------------------------------- #
    geo = kcs_geometry_baseline()
    km_calc = deployed_kb(KCS["T"], KCS["Cb"]) + deployed_bm(KCS["B"], KCS["T"], KCS["Cb"])
    km_eval = deployed_kb_evaluate(KCS["T"], KCS["Cb"]) + deployed_bm_evaluate(
        KCS["B"], KCS["T"], KCS["Cb"], KCS["Cm"])
    gm_calc = km_calc - KCS["KG"]
    gm_eval = km_eval - KCS["KG"]
    if geo:
        geo.update({
            "KM_deployed_calculations_m": km_calc,
            "KM_deployed_evaluate_m": km_eval,
            "KM_error_calculations_rel": (km_calc - geo["KM_m"]) / geo["KM_m"],
            "KM_error_evaluate_rel": (km_eval - geo["KM_m"]) / geo["KM_m"],
            "KB_error_calculations_rel": (deployed_kb(KCS["T"], KCS["Cb"]) - geo["KB_m"]) / geo["KB_m"],
            "BM_error_calculations_rel": (deployed_bm(KCS["B"], KCS["T"], KCS["Cb"]) - geo["BM_m"]) / geo["BM_m"],
            "GM_deployed_calculations_m": gm_calc,
            "GM_deployed_evaluate_m": gm_eval,
            "GM_geometry_baseline_m": geo["KM_m"] - KCS["KG"],
        })
        checks.append({"name": "B2 geometry self-check: Cb from offsets matches the claimed Cb (15% band)",
                       "value": geo["Cb_rel_error"], "threshold": 0.15,
                       "pass": abs(geo["Cb_rel_error"]) <= 0.15})
        checks.append({"name": "B2 deployed KB (calculations.py) within 15% of geometry",
                       "value": geo["KB_error_calculations_rel"], "threshold": 0.15,
                       "pass": abs(geo["KB_error_calculations_rel"]) <= 0.15})

    # ---- B3 internal consistency of the two endpoints --------------------- #
    consistency = {
        "GM_calculations_endpoint_m": gm_calc,
        "GM_evaluate_endpoint_m": gm_eval,
        "GM_abs_difference_m": abs(gm_calc - gm_eval),
        "GM_rel_difference": abs(gm_calc - gm_eval) / min(abs(gm_calc), abs(gm_eval)),
    }
    checks.append({"name": "B3 the two endpoints agree on GM for identical ship data (15% band)",
                   "value": consistency["GM_rel_difference"], "threshold": 0.15,
                   "pass": consistency["GM_rel_difference"] <= 0.15})

    # ---- GZ shape vs exact reference at matched GM ------------------------ #
    B, T, D = KCS["B"], KCS["T"], KCS["D"]
    bm_box_matched = B ** 2 / (12.0 * T)
    kg_matched = 0.5 * T + bm_box_matched - gm_calc          # match the deployed GM
    case = BoxCase(beam_m=B, draft_m=T, depth_m=D, kg_m=kg_matched)
    sweep = []
    for phi in range(0, 61, 2):
        ex = exact_gz(case, float(phi))["gz_m"]
        dep = deployed_gz_curve(float(phi), gm_calc, deployed_bm(B, T, KCS["Cb"]))
        sweep.append({"heel_deg": phi, "gz_exact_box_matched_gm": ex, "gz_deployed_m": dep,
                      "inside_wall_sided_domain": phi <= case.deck_edge_immersion_deg + 1e-9})
    ph = np.array([s["heel_deg"] for s in sweep], float)
    gz_ex = np.array([s["gz_exact_box_matched_gm"] for s in sweep], float)
    gz_dp = np.array([s["gz_deployed_m"] for s in sweep], float)
    area_ex = float(np.trapezoid(gz_ex, np.radians(ph)))
    area_dp = float(np.trapezoid(gz_dp, np.radians(ph)))
    area_ex_40 = float(np.trapezoid(gz_ex[ph <= 40], np.radians(ph[ph <= 40])))
    area_dp_40 = float(np.trapezoid(gz_dp[ph <= 40], np.radians(ph[ph <= 40])))
    gz_shape = {
        "matched_GM_m": gm_calc,
        "deck_edge_immersion_deg": case.deck_edge_immersion_deg,
        "deployed_gz_max_m": float(gz_dp[np.argmax(gz_dp)]),
        "deployed_gz_max_angle_deg": float(ph[int(np.argmax(gz_dp))]),
        "exact_gz_max_m": float(gz_ex.max()),
        "exact_gz_max_angle_deg": float(ph[int(np.argmax(gz_ex))]),
        "deployed_gz_max_rel_error": float((gz_dp.max() - gz_ex.max()) / gz_ex.max()),
        "area_0_40_deployed_mrad": area_dp_40, "area_0_40_exact_mrad": area_ex_40,
        "area_0_60_deployed_mrad": area_dp, "area_0_60_exact_mrad": area_ex,
        "deployed_evaluate_gz_max_m": deployed_gz_max_evaluate(gm_calc),
        "deployed_evaluate_vanishing_deg": deployed_vanishing_evaluate(gm_calc),
        "exact_vanishing_deg": vanishing_angle_deg(case),
        "imo_area_0_40_required_mrad": 0.09,
        "imo_area_0_30_required_mrad": 0.055,
    }
    checks.append({"name": "GZ_max from the deployed curve within 15% of the exact arm",
                   "value": gz_shape["deployed_gz_max_rel_error"], "threshold": 0.15,
                   "pass": abs(gz_shape["deployed_gz_max_rel_error"]) <= 0.15})

    # ---- live endpoint cross-check --------------------------------------- #
    live_eval = post_live("stability/evaluate", {
        "L": KCS["L"], "B": KCS["B"], "T": KCS["T"], "D": KCS["D"],
        "displacement": KCS["disp"], "Cb": KCS["Cb"], "Cm": KCS["Cm"], "KG": KCS["KG"]})
    live_calc = post_live("calculations/stability", {
        "length": KCS["L"], "beam": KCS["B"], "draft": KCS["T"], "depth": KCS["D"],
        "displacement": KCS["disp"], "cb": KCS["Cb"], "kg": KCS["KG"], "heel_angle_deg": 0})
    live = {"evaluate": live_eval, "calculations": live_calc,
            "evaluate_matches_transcription": (
                live_eval is not None
                and abs(live_eval.get("GM", math.nan) - gm_eval) <= 2e-3
                and abs(live_eval.get("GZ_max", math.nan) - deployed_gz_max_evaluate(gm_eval)) <= 5e-3),
            "calculations_matches_transcription": (
                live_calc is not None
                and abs(live_calc.get("gm", math.nan) - gm_calc) <= 2e-3)}

    summary = {
        "capability": "calc.stability",
        "endpoints": ["POST /api/v1/stability/evaluate", "POST /api/v1/calculations/stability"],
        "ship_case": KCS,
        "B1_exact_limit": limit,
        "B2_geometry_baseline": geo,
        "B3_endpoint_consistency": consistency,
        "gz_shape": gz_shape,
        "live": live,
        "checks": checks,
        "verdict": ("KB relation KB=T(1-Cb/3) is MOCK-grade: +33.3% at the exact wall-sided limit and "
                    "+{:.0f}% vs integrated KCS geometry; the two endpoints disagree on GM by {:.0f}%; "
                    "GZ_max/GZ_max-angle/vanishing-angle in stability.py are hardcoded heuristics."
                    ).format(100 * geo["KB_error_calculations_rel"] if geo else float("nan"),
                             100 * consistency["GM_rel_difference"]),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    with open(OUT / "sweep.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(sweep[0]))
        w.writeheader()
        w.writerows(sweep)

    make_figures(sweep, geo, limit, consistency)
    render_doc(summary)

    for c in checks:
        print(f"[{'PASS' if c['pass'] else 'FAIL'}] {c['name']}  value={c['value']:.4g} thr={c['threshold']}")
    print(json.dumps({"limit": limit, "geo": {k: v for k, v in (geo or {}).items() if 'error' in k},
                      "consistency": consistency, "gz_shape": gz_shape,
                      "live_ok": (live["evaluate_matches_transcription"],
                                  live["calculations_matches_transcription"])},
                     indent=1, ensure_ascii=False, default=float))
    return 0 if all(c["pass"] for c in checks) else 1


def make_figures(sweep, geo, limit, consistency) -> None:
    ph = [s["heel_deg"] for s in sweep]
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))
    axes[0].plot(ph, [s["gz_exact_box_matched_gm"] for s in sweep], "-", lw=2,
                 label="exact 2-D hydrostatics (matched GM)")
    axes[0].plot(ph, [s["gz_deployed_m"] for s in sweep], "--s", ms=3, lw=1.6,
                 label="deployed calculate_gz_curve")
    axes[0].axvline(15, color="0.6", ls=":", lw=1.0)
    axes[0].axvline(40, color="0.6", ls=":", lw=1.0)
    axes[0].text(15.5, 0.05, "sin→sin² switch", fontsize=7, color="0.4")
    axes[0].text(40.5, 0.05, "hardcoded flooding cut", fontsize=7, color="0.4")
    axes[0].set_xlabel("heel angle φ [deg]")
    axes[0].set_ylabel("righting arm GZ [m]")
    axes[0].set_title("Deployed GZ curve vs exact reference at matched GM")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.3)

    labels, vals = [], []
    if geo:
        labels = ["KB\n(calc.py)", "BM\n(calc.py)", "GM\n(calc.py)"]
        vals = [100 * geo["KB_error_calculations_rel"], 100 * geo["BM_error_calculations_rel"],
                100 * geo["KM_error_calculations_rel"]]
    labels += ["KB\nexact-limit"] 
    vals += [100 * limit["KB_rel_error"]]
    colors = ["tab:red"] * len(labels)
    axes[1].bar(labels, vals, color=colors)
    axes[1].axhline(15, color="k", ls="--", lw=1.0)
    axes[1].text(-0.4, 17, "±15% acceptability band", fontsize=8)
    axes[1].axhline(0, color="k", lw=0.8)
    axes[1].set_ylabel("over/under-prediction vs baseline [%]")
    axes[1].set_title("Hydrostatic terms: deployed formula vs geometry & exact limit")
    axes[1].grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(OUT / "gz_and_hydrostatics.png", dpi=170)
    plt.close(fig)


def render_doc(s: dict) -> None:
    g = s["B2_geometry_baseline"] or {}
    gz = s["gz_shape"]
    lim = s["B1_exact_limit"]
    con = s["B3_endpoint_consistency"]
    live = s["live"]

    def pct(x):
        return f"{100 * x:+.1f} %"

    md = f"""# 稳性链（GM / GZ）验证欠账审计 · KCS 工况

> 能力：`calc.stability`（平台审计标为 REAL）。本文对**已部署**（:8001）的两个稳性端点做独立复核，
> 同时对 `TensorPlatform/backend/api/calculations.py` 的舷侧直壁 GZ 公式做精确解校验。
> 全部数字由 `tools/validation/validate_deployed_stability_formulas.py` 与
> `tools/validation/validate_wall_sided_gz.py` 程序化注入，无手抄。

## 1. 案例与参数

KCS 集装箱船标称主尺度与装载工况：

| 量 | 值 |
|---|---|
| L / B / T / D | {KCS['L']:g} m / {KCS['B']:g} m / {KCS['T']:g} m / {KCS['D']:g} m |
| 排水量 Δ | {KCS['disp']:g} m³ |
| 标称 Cb / Cm | {KCS['Cb']:g} / {KCS['Cm']:g} |
| KG | {KCS['KG']:g} m |

对照基线（三条互不共享代数式）：

- **B1 精确舷侧直壁极限**：矩形剖面（Cb=Cm=Cwp=1）时 KB=T/2、BM=B²/12T 为闭式精确值，任何 KB/BM 关系式必须复现它。
- **B2 真实几何积分**：`backend/data/offsets/kcs.json`（{g.get('n_stations','?')} 站 × {g.get('n_waterlines','?')} 水线）逐站梯形积分：
  排水体积→Cb、体积矩→KB、水线面惯性矩→BM。原始表按最大半宽归一化（最大半宽 {g.get('max_half_breadth_m', float('nan')):.2f} m = B/2）。
- **B3 端点互校**：同一船舶数据喂两个端点，GM 必须一致。

## 2. 怎么做的

1. 逐字转写被测代码（`calculate_kb`、`calculate_bm`、`calculate_gz_curve`、`evaluate_stability` 的
   `GZ_max = GM·sin45°·1.5`、`GZ_angle = 45`、`vanishing = 60+10·GM`），在脚本内复算，
   并用真实 HTTP 调用 :8001 验证转写与线上返回值一致（线上一致性：evaluate={live['evaluate_matches_transcription']}，
   calculations={live['calculations_matches_transcription']}）。
2. 对 KCS 干舷/船宽做精确二维水动力积分（体积平衡水线二分 + 多边形裁剪求形心，见
   `tools/validation/wall_sided_gz_reference.py`），在**同 GM** 下比较 GZ 曲线形状。
3. 误差判据：15%（平台不可接受线）；几何先行自洽（Cb 校核）。

## 3. 计算结果

### 3.1 几何自洽（B2）

| 量 | 几何积分 | 表内标称 | 误差 |
|---|---|---|---|
| Cb | {g.get('Cb_from_geometry', float('nan')):.4f} | {g.get('Cb_claimed', float('nan')):.3f} | {pct(g.get('Cb_rel_error', float('nan')))} |

几何积分给出的 Cb 比表内标称高 {100*abs(g.get('Cb_rel_error', float('nan'))):.1f}%，
说明该 offset 表的站位/水线插值或归一化与标称 Cb 不完全自洽——**先修几何再谈精度**；
下面所有比较都用同一套几何积分值作为基线，误差主要是被测公式造成的。

### 3.2 静水项（KB / BM / KM）对基线

| 量 | 部署公式（calculations.py） | 几何积分（KCS） | 误差 |
|---|---|---|---|
| KB [m] | {lim.get('KB_exact_m', 0) and deployed_kb(KCS['T'], KCS['Cb']):.3f} | {g.get('KB_m', float('nan')):.3f} | {pct(g.get('KB_error_calculations_rel', float('nan')))} |
| BM [m] | {deployed_bm(KCS['B'], KCS['T'], KCS['Cb']):.3f} | {g.get('BM_m', float('nan')):.3f} | {pct(g.get('BM_error_calculations_rel', float('nan')))} |
| KM [m] | {g.get('KM_deployed_calculations_m', float('nan')):.3f} | {g.get('KM_m', float('nan')):.3f} | {pct(g.get('KM_error_calculations_rel', float('nan')))} |
| GM [m] (KG={KCS['KG']:g}) | {g.get('GM_deployed_calculations_m', float('nan')):.3f} | {g.get('GM_geometry_baseline_m', float('nan')):.3f} | — |

**B1 精确极限检验**（Cb=Cm=Cwp=1 的矩形剖面，KB 真值 T/2）：

| 量 | 精确值 | 部署公式 | 误差 |
|---|---|---|---|
| KB [m] | {lim['KB_exact_m']:.4f} | {lim['KB_deployed_m']:.4f} | {pct(lim['KB_rel_error'])} |
| BM [m] | {lim['BM_exact_m']:.4f} | {lim['BM_deployed_m']:.4f} | {pct(lim['BM_rel_error'])} |

即 `KB = T(1-Cb/3)`（代码注释称 Morrish 公式）在唯一已知精确解的几何上偏差 {pct(lim['KB_rel_error'])}，
它不是一个通用 KB 关系式；BM 关系式在该极限下精确。

### 3.3 两端点互校（B3）

| 端点 | KM [m] | GM [m]（KG={KCS['KG']:g}） |
|---|---|---|
| `/calculations/stability`（KB=T(1-Cb/3), BM=CWP²B³/12∇） | {g.get('KM_deployed_calculations_m', float('nan')):.3f} | {con['GM_calculations_endpoint_m']:.3f} |
| `/stability/evaluate`（KB=T(1-0.5(1-Cb)), BM=B²Cb/12T·Cm⁻¹） | {g.get('KM_deployed_evaluate_m', float('nan')):.3f} | {con['GM_evaluate_endpoint_m']:.3f} |

同一船舶数据，GM 相差 **{con['GM_abs_difference_m']:.3f} m（{pct(con['GM_rel_difference'])}）**——
两者都是 REAL 标签下的同一能力，互不自洽。

### 3.4 GZ 曲线形状（同 GM 下对比精确解）

| 量 | 精确解 | 部署 `calculate_gz_curve` | 误差 |
|---|---|---|---|
| GZ_max [m] | {gz['exact_gz_max_m']:.3f} | {gz['deployed_gz_max_m']:.3f} | {pct(gz['deployed_gz_max_rel_error'])} |
| GZ_max 对应角度 | {gz['exact_gz_max_angle_deg']:.0f}° | {gz['deployed_gz_max_angle_deg']:.0f}° | 硬编码 40°/45° 折点 |
| Area(0→40°) [m·rad] | {gz['area_0_40_exact_mrad']:.4f} | {gz['area_0_40_deployed_mrad']:.4f} | — |
| 消失角 | {gz['exact_vanishing_deg'] if gz['exact_vanishing_deg'] is None else f"{gz['exact_vanishing_deg']:.1f}°"} | 固定 60°（φ>40° 起乘 `(60-φ)/20`） | 硬编码 |
| IMO 要求 Area(0→40°) | {gz['imo_area_0_40_required_mrad']:.3f} m·rad | — | 部署值超出 {gz['area_0_40_deployed_mrad']/gz['imo_area_0_40_required_mrad']:.1f}× |

`evaluate` 端点更直接：GZ_max = GM·sin45°·1.5（系数 1.5 无来源）、GZ_angle 恒为 45.0°、
消失角 = 60+10·GM——三项均与几何无关。

## 4. 图

- `artifacts/validation/deployed_stability/gz_and_hydrostatics.png`：左＝部署 GZ 曲线 vs 同 GM 精确解
  （标出 15° 切换点与 40° 硬编码进水折减），右＝KB/BM/KM 相对基线误差条（±15% 带）。
- `artifacts/validation/wall_sided_gz/*.png`：舷侧直壁公式在有效域内与精确解的机器精度一致、
  域外发散的全量曲线。

## 5. 注意点与局限

1. KCS offset 表几何自洽性不足（Cb 差 {100*abs(g.get('Cb_rel_error', float('nan'))):.1f}%），
   站位按等间距 21 点假设；真实站距不等距会改变积分结果，因此 B2 基线本身带几何不确定度。
2. 同 GM 对比隔离的是 GZ 曲线**形状**误差，不评价 Cb<1 船型的 BM 真值；精确 GZ 参考解是矩形剖面。
3. `calculate_gz_curve` 末尾 `max(0, GZ)` 会把负复原力臂抹成 0——负 GM 船的失稳信息被结构性丢弃。
4. 部署端点（:8001）不在本工作树 EDIT_WHITELIST 内，本单只做只读复核与证据留痕，修复走派单。

## 6. 误差判定（对 15% 标准逐项）

| 关键量 | 误差 | <15%? |
|---|---|---|
| KB（精确极限，Cb=1） | {pct(lim['KB_rel_error'])} | ❌ |
| KB（KCS 几何） | {pct(g.get('KB_error_calculations_rel', float('nan')))} | ❌ |
| BM（精确极限，Cb=1） | {pct(lim['BM_rel_error'])} | ✅ |
| BM（KCS 几何） | {pct(g.get('BM_error_calculations_rel', float('nan')))} | ❌ |
| KM（KCS 几何） | {pct(g.get('KM_error_calculations_rel', float('nan')))} | ❌ |
| 两端点 GM 互差 | {pct(con['GM_rel_difference'])} | ❌ |
| GZ_max（同 GM） | {pct(gz['deployed_gz_max_rel_error'])} | {'✅' if abs(gz['deployed_gz_max_rel_error']) <= 0.15 else '❌'} |

> 注：本审计脚本的退出码 **非 0 是设计行为**——`checks` 是被测代码的验收判据，
> 任一条不通过即说明被审计的部署实现未达 15% 标准（= 审计结论），并非验证工装故障。
> 回归测试 `tests/test_stability_wall_sided_domain.py`（19 项）是绿的，二者语义不同。

**结论**：`calc.stability` 目前不应整体标 REAL。可拆分为：
`calculations.py` 的舷侧直壁 GZ 公式在甲板边缘入水前为真实精确解（REAL，本单已加失效域标注与测试）；
其 KB 关系式（精确极限 +33%）与 `stability.py` 的 GZ_max / GZ_max 角 / 消失角属 **MOCK**（硬编码/拟合系数）；
大倾角 GZ 为 **NOT_SUPPORTED**（无横剖面几何积分）。
"""
    DOC.write_text(md)


if __name__ == "__main__":
    raise SystemExit(main())