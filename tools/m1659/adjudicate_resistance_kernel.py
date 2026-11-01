"""m1659 adjudication: legacy axisymmetric resistance kernel vs Holtrop-Mennen.

Reads the canon benchmark dataset (``backend/data/benchmark_reference.json``,
v3.0, the single adjudicator per m1614) and, for every case whose recorded
``experimental_ct`` is reviewable (``rt_status``/``reference_status`` != WITHHELD),
computes the total-resistance coefficient with both kernels and the relative
error against the recorded value.

Outputs (all numbers machine-written, nothing hand-typed):
  artifacts/m1659/adjudication.json
  artifacts/m1659/fig1_wetted_surface.png
  artifacts/m1659/fig2_ct_error.png
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.holtrop_mennen import holtrop_resistance  # noqa: E402
from services.ship_resistance import ct_from_rt  # noqa: E402

OUT = ROOT / "artifacts" / "m1659"
OUT.mkdir(parents=True, exist_ok=True)
G = 9.80665


def legacy_ct(L, B, T, Cb, V, rho, nu):
    """Shipped default kernel of /calculations/resistance (verbatim formulas)."""
    S = math.pi * B * L * (0.5 + 0.25 * Cb)
    Re = V * L / nu
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
    Fn = V / math.sqrt(G * L)
    Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rt = 0.5 * rho * S * V ** 2 * (Cf + Cr)
    return S, Cf, Fn, Rt, ct_from_rt(Rt, rho, S, V)


data = json.loads((ROOT / "backend/data/benchmark_reference.json").read_text())
rows = []
for c in data["cases"]:
    g = c["geometry"]
    L = g["length_m"]
    B = g.get("beam_m") or g.get("diameter_m")      # SUBOFF rows carry a diameter
    T = g.get("draft_m") or (B / 2.0)   # axisymmetric rows: draft ~ radius
    Cb = g.get("cb")        # filled below once rho is known
    V = c.get("speed_ms")
    rho = c.get("reference_density_kg_m3") or 1025.0
    nu = c.get("reference_kinematic_viscosity_m2_s") or 1.188e-6
    if not Cb:
        disp = g.get("displacement_kg") or 0.0
        Cb = disp / (L * B * T * rho) if disp and L * B * T else 0.6   # canon rule R4
        cb_derived = True
    else:
        cb_derived = False
    ct_exp = c.get("experimental_ct")
    reviewable = (ct_exp is not None and V and c.get("reference_status") != "WITHHELD"
                  and c.get("rt_status") != "WITHHELD")
    S_leg, Cf, Fn, Rt_leg, ct_leg = legacy_ct(L, B, T, Cb, V, rho, nu)
    try:
        h = holtrop_resistance(L, B, T, Cb, V, rho=rho, nu=nu)
        S_h, ct_h, Rt_h, notes = h.wetted_surface_m2, h.ct, h.rt_n, list(h.derived)
    except Exception as exc:  # domain violations are reported, never hidden
        S_h, ct_h, Rt_h, notes = float("nan"), float("nan"), float("nan"), [f"error: {exc}"]
    row = dict(
        case_id=c["case_id"], ship_type=c["ship_type"], scale=c.get("scale"),
        L=L, B=B, T=T, Cb=round(Cb, 5), cb_derived=cb_derived, V=V, rho=rho, nu=nu, froude=round(Fn, 5),
        S_legacy=round(S_leg, 2), S_holtrop=round(S_h, 2) if S_h == S_h else None,
        S_ratio=round(S_leg / S_h, 4) if S_h == S_h else None,
        ct_recorded=ct_exp,
        ct_legacy=round(ct_leg, 6), ct_holtrop=round(ct_h, 6) if ct_h == ct_h else None,
        reviewable=bool(reviewable),
        derived=notes,
    )
    if reviewable:
        row["err_legacy_pct"] = round(100 * (ct_leg - ct_exp) / ct_exp, 2)
        row["err_holtrop_pct"] = (round(100 * (ct_h - ct_exp) / ct_exp, 2)
                                  if ct_h == ct_h else None)
        for k in ("err_legacy_pct", "err_holtrop_pct"):
            if row[k] is not None:
                row[k.replace("_pct", "_ok15")] = abs(row[k]) <= 15.0
    rows.append(row)

reviewable = [r for r in rows if r["reviewable"]]

# --- reference-value anchors (published, cited in-repo) ----------------------
anchors = {
    "KVLCC2_public_S_m2": 27194.0,
    "source": "backend/tests/test_benchmark_reference_integrity.py "
              "(KVLCC2 main particulars, published wetted surface)",
}


def band_ok(s, ref, tol=0.10):
    return abs(s - ref) / ref <= tol


kvl = dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098, speed_ms=7.97,
           rho=1025.0, nu=1.188e-6)
S_leg_k = math.pi * 58.0 * 320.0 * (0.5 + 0.25 * 0.8098)
S_h_k = holtrop_resistance(**kvl).wetted_surface_m2
anchor_block = {
    "KVLCC2": dict(
        S_legacy=round(S_leg_k, 2), S_holtrop=round(S_h_k, 2),
        legacy_bias_pct=round(100 * (S_leg_k - 27194.0) / 27194.0, 2),
        holtrop_bias_pct=round(100 * (S_h_k - 27194.0) / 27194.0, 2),
        legacy_ok_10pct=band_ok(S_leg_k, 27194.0), holtrop_ok_10pct=band_ok(S_h_k, 27194.0),
    )
}

summary = dict(
    n_cases=len(rows),
    n_reviewable=len(reviewable),
    n_withheld=len(rows) - len(reviewable),
    reviewable_mean_abs_err_legacy_pct=(
        round(sum(abs(r["err_legacy_pct"]) for r in reviewable) / len(reviewable), 2)
        if reviewable else None),
    reviewable_mean_abs_err_holtrop_pct=(
        round(sum(abs(r["err_holtrop_pct"]) for r in reviewable
                  if r.get("err_holtrop_pct") is not None) /
              max(1, sum(1 for r in reviewable if r.get("err_holtrop_pct") is not None)), 2)
        if reviewable else None),
    reviewable_pass_legacy=sum(1 for r in reviewable if r.get("err_legacy_ok15")),
    reviewable_pass_holtrop=sum(1 for r in reviewable if r.get("err_holtrop_ok15")),
    S_ratio_range=[min(r["S_ratio"] for r in rows if r["S_ratio"]),
                   max(r["S_ratio"] for r in rows if r["S_ratio"])],
)

payload = dict(mission="m1659", anchors=anchors, anchor_check=anchor_block,
               summary=summary, cases=rows)
(OUT / "adjudication.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False))
print(json.dumps(summary, indent=1))
print(json.dumps(anchor_block, indent=1))
for r in reviewable:
    print(r["case_id"], r["ship_type"], "Ct rec", r["ct_recorded"],
          "legacy", r["ct_legacy"], f"({r['err_legacy_pct']:+.1f}%)",
          "holtrop", r["ct_holtrop"],
          (f"({r['err_holtrop_pct']:+.1f}%)" if r.get("err_holtrop_pct") is not None else ""))

# --- figures ----------------------------------------------------------------
fig, ax = plt.subplots(figsize=(7.2, 4.2))
labels = ["KVLCC2\n(canon main particulars)"]
xs = [0, 1, 2]
vals = [27194.0, S_leg_k, S_h_k]
cols = ["#444444", "#d1495b", "#2e86ab"]
bars = ax.bar(xs, vals, color=cols, width=0.6)
ax.set_xticks(xs, ["published\nreference", "legacy\n$\\pi BL(0.5+0.25C_B)$",
                   "Holtrop-Mennen\n1982"])
ax.set_ylabel("wetted surface $S$ [m$^2$]")
ax.set_title("m1659 — KVLCC2 wetted surface: the m1414 defect and its fix")
for b, v in zip(bars, vals):
    ax.text(b.get_x() + b.get_width() / 2, v * 1.01, f"{v:,.0f}", ha="center", fontsize=9)
ax.set_ylim(min(vals) * 0.85, max(vals) * 1.08)
ax.grid(axis="y", alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "fig1_wetted_surface.png", dpi=160)

fig, ax = plt.subplots(figsize=(7.6, 4.2))
ids = [r["case_id"] for r in reviewable]
el = [r["err_legacy_pct"] for r in reviewable]
eh = [r["err_holtrop_pct"] if r.get("err_holtrop_pct") is not None else 0.0 for r in reviewable]
xp = range(len(ids))
ax.bar([x - 0.2 for x in xp], el, width=0.4, label="legacy axisymmetric kernel", color="#d1495b")
ax.bar([x + 0.2 for x in xp], eh, width=0.4, label="Holtrop-Mennen 1982", color="#2e86ab")
ax.axhspan(-15, 15, color="#9ad0a6", alpha=0.35, label="+/-15 % acceptance band")
ax.axhline(0, color="k", lw=0.8)
ax.set_xticks(list(xp), ids, rotation=30, ha="right", fontsize=8)
ax.set_ylabel("$C_T$ error vs recorded value [%]")
ax.set_title("m1659 — reviewable canon cases: kernel error")
ax.legend(fontsize=8)
ax.grid(axis="y", alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "fig2_ct_error.png", dpi=160)
print("figures ->", OUT)