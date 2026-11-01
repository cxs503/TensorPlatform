"""Verification-debt audit: platform transverse-stability model vs independent exact references.

Audits POST /api/v1/stability/gz-curve and /api/v1/stability-imo/gz-curve against
(1) the closed-form wall-sided cross curve and (2) voxel volume-integration cross
curves.  Writes artifacts/gz_audit/gz_audit.json + four figures.
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exact_gz as eg

API = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ART = os.path.join(ROOT, "artifacts", "gz_audit")
FIGS = os.path.join(ROOT, "docs", "verification", "figs")

ANGLES = list(range(0, 91, 5))


def post(path, body):
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


# --- the deployed model, transcribed for attribution only (not used as truth) ---
def model_kb(T, Cb):
    return T * (0.5 + 0.05 * Cb)


def model_bm(B, T, Cb, Cm):
    return (0.085 * Cb - 0.002) * B ** 2 / T


def model_kn(phi, BM, KB):
    p = math.radians(phi)
    return (BM * math.sin(p) * math.cos(p) + KB * math.sin(p)) * (1 + 0.001 * phi)


def trapezoid(angles, vals, lo, hi):
    a = np.asarray(angles, float); v = np.asarray(vals, float)
    m = (a >= lo - 1e-9) & (a <= hi + 1e-9)
    return float(np.trapezoid(v[m], np.radians(a[m])))


def run_case(name, geo, rho_note=""):
    body = dict(geo)
    out = post("/stability-imo/gz-curve", body)
    kn_p = np.array(out["kn_values"], float)
    gz_p = np.array(out["gz_values"], float)
    ang = np.array(out["angles"], float)
    KG = geo["KG"]

    # ---- reference hull definitions ----
    if name.startswith("box"):
        L, B, T, D = geo["L"], geo["B"], geo["T"], geo["D"]
        hs = eg.box_hydrostatics(B, T)
        kn_closed = np.array([eg.wall_sided_kn(a, hs["KB"], hs["BM"]) for a in ANGLES])
        hull = eg.VoxelHull(eg.box_solid(L, B), L, B, D, nx=40, ny=80, nz=80)
        V0 = hull.volume_below(T)
        kn_num = np.array([hull.kn(a, V0) for a in ANGLES])
        deck = eg.box_deck_edge_angle(B, T, D)
        ref_kind = ("closed-form wall-sided up to deck edge (%.1f deg), "
                    "voxel volume integration beyond" % deck)
        ref_kn = np.where(np.array(ANGLES) <= deck, kn_closed, kn_num)
    else:
        L, B, T, D = geo["L"], geo["B"], geo["T"], geo["D"]
        hp = eg.parabolic_hydrostatics(L, B, T)
        kn_closed = np.array([eg.wall_sided_kn(a, hp["KB"], hp["BM"]) for a in ANGLES])
        hull = eg.VoxelHull(eg.parabolic_wall_sided_solid(L, B), L, B, D, nx=80, ny=80, nz=40)
        V0 = hull.volume_below(T)
        kn_num = np.array([hull.kn(a, V0) for a in ANGLES])
        deck = eg.box_deck_edge_angle(B, T, D)
        ref_kind = ("closed-form wall-sided (parabolic waterplanes, KB=%.3f, BM=%.3f) "
                    "up to deck edge %.1f deg, voxel beyond" % (hp["KB"], hp["BM"], deck))
        ref_kn = np.where(np.array(ANGLES) <= deck, kn_closed, kn_num)

    gz_ref = ref_kn - KG * np.sin(np.radians(ang))
    cmpmask = np.array(ANGLES) >= 5
    scale = float(np.max(np.abs(gz_ref[cmpmask])))
    err = gz_p - gz_ref
    rel = np.divide(np.abs(err), np.abs(gz_ref) + 1e-12)
    nerr = np.abs(err) / max(scale, 1e-9)

    mask = cmpmask
    return {
        "case": name, "geometry": geo, "rho_note": rho_note,
        "reference": ref_kind, "deck_edge_angle_deg": deck,
        "V0_m3": V0,
        "KM_exact_small_angle": float(ref_kn[1] / math.sin(math.radians(5))),
        "KM_model": model_kb(T, geo["Cb"]) + model_bm(B, T, geo["Cb"], geo["Cm"]),
        "GM_exact": float(ref_kn[1] / math.sin(math.radians(5)) - KG),
        "model_KB": model_kb(T, geo["Cb"]), "exact_KB_first_principles":
            (T / 2.0 if name.startswith("box") else None),
        "model_BM": model_bm(B, T, geo["Cb"], geo["Cm"]),
        "exact_BM_box": (B * B / (12 * T) if name.startswith("box") else None),
        "angles": ANGLES, "kn_platform": kn_p.tolist(), "kn_reference": ref_kn.tolist(),
        "kn_voxel_crosscheck": kn_num.tolist(),
        "gz_platform": gz_p.tolist(), "gz_reference": gz_ref.tolist(),
        "gz_err_abs": err.tolist(),
        "gz_err_norm_of_GZmax": nerr.tolist(),
        "gz_rel_err": rel.tolist(),
        "max_abs_err_norm": float(np.max(nerr[mask])),
        "max_rel_err": float(np.max(rel[mask])),
        "angles_over_15pct_norm": [float(a) for a, e in zip(ang[mask], nerr[mask]) if e > 0.15],
        "GZmax_platform": float(np.max(gz_p)), "GZmax_angle_platform": float(ang[int(np.argmax(gz_p))]),
        "GZmax_reference": float(np.max(gz_ref)),
        "GZmax_angle_reference": float(ang[int(np.argmax(gz_ref))]),
        "area_0_30_platform": out["area_0_30"], "area_0_30_reference": trapezoid(ang, gz_ref, 0, 30),
        "area_0_40_platform": out["area_0_40"], "area_0_40_reference": trapezoid(ang, gz_ref, 0, 40),
        "area_30_40_platform": out["area_30_40"], "area_30_40_reference": trapezoid(ang, gz_ref, 30, 40),
        "kn_model_check_max_dev": float(np.max(np.abs(kn_p - np.array(
            [model_kn(a, model_bm(B, T, geo["Cb"], geo["Cm"]), model_kb(T, geo["Cb"]))
             for a in ANGLES])))),
    }


def main():
    os.makedirs(ART, exist_ok=True); os.makedirs(FIGS, exist_ok=True)
    cases = [
        ("box_barge_Cb1.0", dict(L=100.0, B=20.0, T=10.0, D=20.0, displacement=20500.0,
                                 Cb=1.0, Cm=1.0, KG=7.0), "wall-sided barge, ground truth exact"),
        ("parabolic_Cb0.667", dict(L=100.0, B=20.0, T=8.0, D=16.0, displacement=10936.0,
                                   Cb=0.6667, Cm=1.0, KG=5.5), "parabolic waterplanes, Cb=2/3 exact"),
    ]
    results = [run_case(n, g, note) for n, g, note in cases]

    # ---- input-invariance probe: does `displacement` influence the output? ----
    base = dict(cases[0][1]); alt = dict(base); alt["displacement"] = base["displacement"] * 1.5
    a = post("/stability-imo/gz-curve", base); b = post("/stability-imo/gz-curve", alt)
    invariance = {"displacement_in": [base["displacement"], alt["displacement"]],
                  "gz_identical": a["gz_values"] == b["gz_values"],
                  "note": "declared displacement is ignored by the model" if a["gz_values"] == b["gz_values"] else "displacement used"}

    # ---- deck-edge continuity probe ----
    L, B, T, D, KG = 100.0, 20.0, 10.0, 20.0, 7.0
    deck = eg.box_deck_edge_angle(B, T, D)
    probe = {}
    for d in (-1.0, -0.2, 0.2, 1.0):
        ang = deck + d
        out = post("/stability-imo/gz-curve", dict(L=L, B=B, T=T, D=D, displacement=20500.0,
                                                  Cb=1.0, Cm=1.0, KG=KG))
        lhs = float(np.interp(ang, out["angles"], out["gz_values"]))
        rhs = eg.wall_sided_gz(ang, T / 2, B * B / (12 * T), KG) if ang <= 90 else float("nan")
        probe[f"{ang:.2f}"] = {"platform_interp": lhs, "exact": rhs}

    payload = {"api": API, "generated_by": "tools/verification/gz_audit/run_audit.py",
               "cases": results, "displacement_invariance": invariance,
               "deck_edge_probe_box": probe,
               "selftest_voxel_vs_closed_form": eg.selftest_box()}
    with open(os.path.join(ART, "gz_audit.json"), "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    # ---------------- figures ----------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    c1, c2 = results[0], results[1]
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.plot(c1["angles"], c1["kn_platform"], "o-", label="platform KN (API)")
    ax.plot(c1["angles"], c1["kn_reference"], "k-", lw=2, label="exact wall-sided KN")
    ax.plot(c1["angles"], c1["kn_voxel_crosscheck"], "r--", label="voxel cross-check")
    ax.axvline(c1["deck_edge_angle_deg"], color="grey", ls=":", label="deck-edge immersion")
    ax.set_xlabel("heel angle [deg]"); ax.set_ylabel("KN [m]")
    ax.set_title("Box barge (L=100,B=20,T=10,KG=7): KN cross curve"); ax.legend(); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(os.path.join(FIGS, "fig1_kn_box_barge.png"), dpi=130); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.plot(c1["angles"], c1["gz_platform"], "o-", label="platform GZ (API)")
    ax.plot(c1["angles"], c1["gz_reference"], "k-", lw=2, label="exact GZ (wall-sided)")
    ax.axvline(c1["deck_edge_angle_deg"], color="grey", ls=":")
    ax.set_xlabel("heel angle [deg]"); ax.set_ylabel("GZ [m]")
    ax.set_title("Box barge GZ: platform vs exact"); ax.legend(); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(os.path.join(FIGS, "fig2_gz_box_barge.png"), dpi=130); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.plot(c2["angles"], c2["gz_platform"], "o-", label="platform GZ (API)")
    ax.plot(c2["angles"], c2["gz_reference"], "k-", lw=2, label="voxel-exact GZ (Wigley)")
    ax.set_xlabel("heel angle [deg]"); ax.set_ylabel("GZ [m]")
    ax.set_title("Parabolic-waterplane hull (Cb=2/3, KG=5.5): platform vs exact GZ")
    ax.legend(); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(os.path.join(FIGS, "fig3_gz_parabolic.png"), dpi=130); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    for c, lab in ((c1, "box barge"), (c2, "parabolic hull")):
        ax.plot(c["angles"], 100 * np.array(c["gz_err_norm_of_GZmax"]), "o-", label=lab)
    ax.axhline(15, color="red", ls="--", label="15% acceptance limit")
    ax.axhline(3, color="orange", ls=":", label="3% target")
    ax.set_xlabel("heel angle [deg]"); ax.set_ylabel("|GZ error| / GZ_max  [%]")
    ax.set_title("Righting-arm error of the platform model"); ax.legend(); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(os.path.join(FIGS, "fig4_gz_error.png"), dpi=130); plt.close(fig)

    for c in results:
        print(f"[{c['case']}] ref={c['reference']}")
        print(f"   max|err|/GZmax={100*c['max_abs_err_norm']:.1f}%   angles>15%: {c['angles_over_15pct_norm']}")
        print(f"   GZmax  model={c['GZmax_platform']:.3f}@{c['GZmax_angle_platform']:.0f}deg  exact={c['GZmax_reference']:.3f}@{c['GZmax_angle_reference']:.0f}deg")
        print(f"   area_0_30 model={c['area_0_30_platform']:.4f} exact={c['area_0_30_reference']:.4f} | "
              f"area_0_40 model={c['area_0_40_platform']:.4f} exact={c['area_0_40_reference']:.4f} | "
              f"area_30_40 model={c['area_30_40_platform']:.4f} exact={c['area_30_40_reference']:.4f}")
        print(f"   KM model={c['KM_model']:.3f} exact={c['KM_exact_small_angle']:.3f} (err {100*(c['KM_model']-c['KM_exact_small_angle'])/c['KM_exact_small_angle']:.1f}%) | GM exact={c['GM_exact']:.3f}")
        print(f"   KB model={c['model_KB']:.3f} exact={c['exact_KB_first_principles']} | BM model={c['model_BM']:.3f} exact={c['exact_BM_box']}")
        print(f"   KN replication dev={c['kn_model_check_max_dev']:.2e}")
    print("displacement invariance:", invariance)
    print("deck-edge probe:", json.dumps(probe, ensure_ascii=False))
    print("artifact:", os.path.join(ART, "gz_audit.json"))


if __name__ == "__main__":
    main()
