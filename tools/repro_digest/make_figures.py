"""Figures for the cross-ISA reproducibility report.

    python3 -m tools.repro_digest.make_figures

Produces (under ``artifacts/repro_digest/figs/``):

``fig1_ulp_histogram.png``   distribution of per-element ULP drift for the
                             observables of all divergent (bench, flags) pairs
``fig2_flags_heatmap.png``   flags-sensitivity heat-map, log10 max ULP vs the
                             ``-O2`` reference, 3 benches x 13 build variants
``fig3_divergence_trace.png`` first-divergence localisation: |du| growth of the
                             H1 channel probe versus physical time
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from ..repro_digest import emit_digest as ed  # noqa: E402
from ..repro_digest import spec as _spec  # noqa: E402
from ..repro_digest import ulp  # noqa: E402

ART = Path("artifacts/cross_isa_repro.json")
REF_VALUES = Path("artifacts/repro_digest/ref/values")
OUT = Path("artifacts/repro_digest/figs")

LABELS = {"h1": "H1 channel 60 s", "h2": "H2 power-law slit", "h5": "H5 calibration chain"}
#: (label, flags) pairs that produce a non-bit-identical run
DIVERGENT = [
    ("h1", "FMA contract", "-O3 -ffp-contract=fast"),
    ("h2", "FMA contract", "-O3 -ffp-contract=fast"),
    ("h2", "alt libm path", "-O2 -DREPRO_LIBM_ALT=1"),
    ("h5", "FMA contract", "-O3 -ffp-contract=fast"),
]


def _obs(rec, bucket, name):
    return _spec.class_of(rec["bench"], name) == _spec.OBSERVABLE


def fig1():
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.0), sharey=True)
    per_bench = {b: [] for b in ("h1", "h2", "h5")}
    for bench, mode, flags in DIVERGENT:
        ref = ulp.load_record(json.loads((REF_VALUES / ("%s_base.json" % bench)).read_text()))
        cand = ulp.load_record(ed.run_kernel(bench, flags, variant="fig"))
        for name, vec in ref["fields"].items():
            if not _obs(ref, "fields", name):
                continue
            for a, b in zip(vec, cand["fields"][name]):
                per_bench[bench].append(ulp.ulp_distance(a, b))
        for name, val in ref["scalars"].items():
            if _obs(ref, "scalars", name):
                per_bench[bench].append(
                    ulp.ulp_distance(val, cand["scalars"][name]))
    for ax, bench in zip(axes, ("h1", "h2", "h5")):
        d = np.array(per_bench[bench], dtype=float)
        nz = d[d > 0]
        ax.hist(np.log10(nz), bins=np.arange(0, np.log10(max(2, nz.max())) + 0.5, 0.25),
                color="#2b6cb0", edgecolor="black", linewidth=0.4)
        ax.set_title("%s\nn=%d values, %d non-zero ULP" % (LABELS[bench], len(d), len(nz)),
                     fontsize=10)
        ax.set_xlabel("log$_{10}$ ULP drift vs $-O2$")
        ax.grid(alpha=0.25)
        if len(nz):
            ax.axvline(np.log10(nz.mean()), color="crimson", ls="--", lw=1.2,
                       label="mean %.2f dex" % np.log10(nz.mean()))
            ax.legend(fontsize=8)
    axes[0].set_ylabel("count of values")
    fig.suptitle("Fig.1  Per-value ULP drift of gated observables (FMA-contract + alt-libm builds)",
                 fontsize=11)
    fig.tight_layout()
    p = OUT / "fig1_ulp_histogram.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return p, {b: (len(v), sum(1 for x in v if x > 0)) for b, v in per_bench.items()}


def fig2():
    data = json.loads(ART.read_text())
    variants = list(next(iter(data["benches"].values()))["flags_sensitivity_max_ulp"])
    mat = np.zeros((3, len(variants)))
    for i, bench in enumerate(("h1", "h2", "h5")):
        row = data["benches"][bench]["table"]
        got = {}
        for r in row:
            if r["machine"] == data["machines"][0]["machine"]:
                got[r["variant"]] = r["vs_base_max_ulp"]
        for j, v in enumerate(variants):
            mat[i, j] = got.get(v, 0)
    fig, ax = plt.subplots(figsize=(13.0, 3.4))
    disp = np.log10(1.0 + mat)
    im = ax.imshow(disp, cmap="magma_r", aspect="auto")
    ax.set_xticks(range(len(variants)))
    ax.set_xticklabels(variants, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(3))
    ax.set_yticklabels([LABELS[b] for b in ("h1", "h2", "h5")], fontsize=9)
    for i in range(3):
        for j in range(len(variants)):
            txt = "%.0f" % mat[i, j] if mat[i, j] < 1e4 else "%.1e" % mat[i, j]
            ax.text(j, i, txt, ha="center", va="center", fontsize=7,
                    color="white" if disp[i, j] > disp.max() * 0.6 else "black")
    k = data["protocol"]["ulp_gate_k"]
    ax.set_title("Fig.2  Flags sensitivity: max ULP vs $-O2$ reference "
                 "(gate k=%d ULP; values >k are protocol failures)" % k, fontsize=11)
    cb = fig.colorbar(im, ax=ax, pad=0.01)
    cb.set_label("log$_{10}$(1+max ULP)")
    fig.tight_layout()
    p = OUT / "fig2_flags_heatmap.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return p, {"variants": variants, "max_ulp": mat.tolist()}


def fig3():
    ref = ulp.load_record(json.loads((REF_VALUES / "h1_base.json").read_text()))
    cand = ulp.load_record(ed.run_kernel("h1", "-O3 -ffp-contract=fast", variant="fig"))
    steps = [pt["step"] for pt in ref["traj"]]
    base = np.array([pt["probe"][1] for pt in ref["traj"]])
    fma = np.array([pt["probe"][1] for pt in cand["traj"]])
    # drift envelope over all three velocity probes (u(0.25H), u(0.5H), u(0.75H))
    venv = [max(abs(cp["probe"][i] - rp["probe"][i]) for i in (0, 1, 2))
            for rp, cp in zip(ref["traj"], cand["traj"])]
    dt = 0.01
    diff = np.array(venv)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 3.8))
    ax1.plot(np.array(steps) * dt, base, label="$-O2$ reference", lw=1.5)
    ax1.plot(np.array(steps) * dt, fma, "--", label="$-O3$ -ffp-contract=fast", lw=1.2)
    ax1.set_xlabel("physical time $t$ [s]")
    ax1.set_ylabel("$u(z=0.5H)$ [m/s]")
    ax1.set_title("H1 channel probe trajectory $u(0.5H)$", fontsize=10)
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.25)
    nz = np.nonzero(diff)[0]
    ax2.semilogy(np.array(steps) * dt, np.where(diff > 0, diff, np.nan), "o-", ms=3,
                 color="crimson")
    if len(nz):
        ax2.axvline(steps[nz[0]] * dt, color="black", ls=":", lw=1.2)
        ax2.annotate("first bifurcating checkpoint\nt = %.2f s (step %d)"
                     % (steps[nz[0]] * dt, steps[nz[0]]),
                     xy=(steps[nz[0]] * dt, diff[nz[0]]), xytext=(0.3, 0.55),
                     textcoords="axes fraction", fontsize=8,
                     arrowprops=dict(arrowstyle="->", lw=0.8))
    ax2.set_xlabel("physical time $t$ [s]")
    ax2.set_ylabel("max$_i$ $|\\Delta u_i|$ [m/s]  (3 probes)")
    ax2.set_title("First divergence localisation (1 s resolution)", fontsize=10)
    ax2.grid(alpha=0.25, which="both")
    fig.suptitle("Fig.3  Where the FMA-contracting build leaves the reference", fontsize=11)
    fig.tight_layout()
    p = OUT / "fig3_divergence_trace.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    return p, {"first_divergent_step": int(steps[nz[0]]) if len(nz) else None,
               "first_divergent_time_s": float(steps[nz[0]] * dt) if len(nz) else None,
               "n_divergent_checkpoints": int(len(nz)),
               "max_abs_diff": float(np.nanmax(diff)) if len(nz) else 0.0,
               "final_abs_diff": float(diff[-1]), "final_rel_diff": float(diff[-1] / base[-1])}


def _web_copy(path: Path, max_width: int = 1150, colors: int = 96) -> Path:
    """Downscaled palette copy for report embedding (payload discipline)."""
    try:
        from PIL import Image
    except Exception:  # pragma: no cover - Pillow optional
        return path
    web = path.parent / "web" / path.name
    web.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(path).convert("RGB")
    w, h = im.size
    if w > max_width:
        im = im.resize((max_width, int(h * max_width / w)), Image.LANCZOS)
    im = im.quantize(colors=colors, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG)
    im.save(web, optimize=True)
    return web


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {}
    for fn in (fig1, fig2, fig3):
        p, info = fn()
        info["web_copy_kb"] = round(_web_copy(p).stat().st_size / 1024, 1)
        meta[p.name] = info
        print("wrote", p)
    (OUT / "figures_meta.json").write_text(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())