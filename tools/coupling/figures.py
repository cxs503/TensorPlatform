#!/usr/bin/env python3
"""Figures for node IN383-PLAT01 (coupling exchange + clock semantics).

Three figures, every number taken from the same code paths the acceptance
driver uses (no hand-copied values):

``fig1_operator_maps.png``    coarse source field vs the three operators on a fine mesh
``fig2_conservation_drift.png``  measured drift per operator, correction on/off, against thresholds
``fig3_twin_and_convergence.png``  twin residual per cycle + remap accuracy vs source resolution

Each PNG is written with a SHA-256 recorded in ``figures.json`` so the report can
bind the picture to the run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .mesh import BoxMesh, Field, QuantityKind  # noqa: E402
from .remap import RemapOperator, RemapSpec, remap  # noqa: E402
from .twin import TwinSpec, run_twin  # noqa: E402

DENSITY = QuantityKind.DENSITY


def _blob(n: int, centre=0.42, width=0.16, amp=1.0, lo=0.0, hi=1.0) -> tuple[BoxMesh, Field]:
    mesh = BoxMesh.uniform([lo, lo], [hi, hi], [n, n], name=f"n{n}")
    c = mesh.centers
    d2 = ((c - centre) ** 2).sum(axis=1)
    return mesh, Field(amp * np.exp(-d2 / (2 * width * width)), mesh, DENSITY, name="blob")


def _grid(field: Field, n: int) -> np.ndarray:
    return field.values.reshape(n, n)


def fig1(outdir: Path) -> Path:
    src_n, dst_n = 8, 16
    src, field = _blob(src_n, width=0.18)
    fine = BoxMesh.uniform([0, 0], [1, 1], [dst_n, dst_n], name="fine")
    ops = [
        (RemapOperator.OVERLAP_L2, True, "overlap-L2 (exact projection)"),
        (RemapOperator.RBF_CONSERVATIVE, True, "RBF + global conservation scaling"),
        (RemapOperator.NEAREST_SCALED, False, "nearest, correction OFF (negative control)"),
    ]
    results = [(label, remap(field, src, fine, "density",
                             spec=RemapSpec(operator=op,
                                            apply_conservation_correction=corr)), corr)
               for op, corr, label in ops]

    fig, axes = plt.subplots(2, 2, figsize=(9.6, 8.0), constrained_layout=True)
    panels = [("source (coarse %dx%d)" % (src_n, src_n), field, src_n)]
    tags = []
    for label, result, corr in results:
        panels.append((label, result.field, dst_n))
        tags.append(f"{label}: total={result.field.total:.6f} "
                    f"drift={100 * result.report.rel_residual:.3e} %")
    vmax = max(np.abs(p.values).max() for _, p, _ in panels)
    for ax, (title, fld, n) in zip(axes.ravel(), panels):
        im = ax.imshow(_grid(fld, n), origin="lower", extent=[0, 1, 0, 1],
                       cmap="inferno", vmin=0, vmax=vmax)
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("x")
        ax.set_ylabel("y")
        fig.colorbar(im, ax=ax, shrink=0.85, label="density")
    fig.suptitle(
        "Same coarse field remapped onto a 16x16 mesh: overlap and RBF keep the total,\n"
        "the uncorrected nearest baseline does not (numbers below)", fontsize=11)
    fig.text(0.5, -0.02, "   |   ".join(tags), ha="center", fontsize=8, wrap=True)
    path = outdir / "fig1_operator_maps.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def fig2(outdir: Path) -> Path:
    layouts = {}
    for layout in ("nested", "shifted"):
        src_n, dst_n = 8, 12
        src, field = _blob(src_n, width=0.18)
        if layout == "nested":
            dst = src.refined(2, name="fine")
        else:
            dst = BoxMesh.uniform([0, 0], [1, 1], [dst_n + 1, dst_n + 1], name="shifted")
        rows = []
        for op in RemapOperator:
            for corr in (True, False):
                res = remap(field, src, dst, "density",
                            spec=RemapSpec(operator=op, apply_conservation_correction=corr))
                rows.append((op.value, corr, max(res.report.rel_residual, 1e-18)))
        layouts[layout] = rows

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2), constrained_layout=True, sharey=True)
    labels = [f"{op}\n{'correction ON' if corr else 'correction OFF'}"
              for op, corr in [(o, c) for o in RemapOperator for c in (True, False)]]
    x = np.arange(len(labels))
    for ax, (layout, rows) in zip(axes, layouts.items()):
        vals = [r[2] for r in rows]
        colors = ["#1b6ca8" if r[1] else "#d1495b" for r in rows]
        ax.bar(x, vals, color=colors, width=0.65)
        ax.set_yscale("log")
        ax.axhline(1e-12, color="k", ls="--", lw=1, label="C1 threshold 1e-12")
        ax.axhline(1e-2, color="#8d6e63", ls=":", lw=1.2, label="1 % drift (C5 floor)")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        ax.set_title(f"{layout} destination mesh")
        ax.set_ylabel("relative total drift")
        ax.grid(axis="y", alpha=0.25)
        ax.legend(fontsize=8, loc="upper left")
    fig.suptitle("Conservation correction is doing real work: OFF (red) drifts, ON (blue) does not",
                 fontsize=11)
    path = outdir / "fig2_conservation_drift.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def fig3(outdir: Path) -> Path:
    curves = []
    for op, corr, label in [
        (RemapOperator.OVERLAP_L2, True, "overlap-L2 (correction on)"),
        (RemapOperator.RBF_CONSERVATIVE, True, "RBF (correction on)"),
        (RemapOperator.NEAREST_SCALED, True, "nearest (correction on)"),
        (RemapOperator.NEAREST_SCALED, False, "nearest (correction OFF, negative control)"),
    ]:
        twin = run_twin(TwinSpec(cycles=40, operator=op, apply_conservation_correction=corr))
        curves.append((label, np.maximum(twin["residual_series"], 1e-18),
                       twin["max_residual"]))

    resolutions = [4, 6, 8, 12, 16, 24]
    analytic = lambda c: np.exp(-(((c - 0.42) ** 2).sum(axis=1)) / (2 * 0.16 ** 2))  # noqa: E731
    target = BoxMesh.uniform([0, 0], [1, 1], [64, 64], name="target")
    exact = analytic(target.centers)
    errs = {RemapOperator.OVERLAP_L2: [], RemapOperator.RBF_CONSERVATIVE: []}
    for n in resolutions:
        m, fld = _blob(n, width=0.16)
        for op, bucket in errs.items():
            out = remap(fld, m, target, "density",
                        spec=RemapSpec(operator=op, apply_conservation_correction=True)).field
            bucket.append(float(np.mean(np.abs(out.values - exact))))

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2), constrained_layout=True)
    ax = axes[0]
    for label, series, _ in curves:
        ax.semilogy(np.arange(1, len(series) + 1), series, lw=1.8, label=label)
    ax.axhline(1e-10, color="k", ls="--", lw=1, label="C4 threshold 1e-10")
    ax.set_xlabel("coupling cycle (master step)")
    ax.set_ylabel("|M(t) - M(0)| / M(0)")
    ax.set_title("Twin loop: two solvers, one exchange per master step")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)

    ax = axes[1]
    h = np.array([1.0 / n for n in resolutions])
    for op, vals in errs.items():
        vals = np.asarray(vals)
        order = np.polyfit(np.log(h), np.log(vals), 1)[0]
        ax.loglog(h, vals, "o-", label=f"{op.value} (order ≈ {order:.2f})")
    ax.loglog(h, errs[RemapOperator.OVERLAP_L2][0] * (h / h[0]), "k:", lw=1, label="slope 1")
    ax.set_xlabel("source cell size h")
    ax.set_ylabel("mean |F_remap - F_exact|")
    ax.set_title("Remap accuracy vs source resolution (patch-test consistency)")
    ax.grid(alpha=0.25, which="both")
    ax.legend(fontsize=8)
    path = outdir / "fig3_twin_and_convergence.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", default="artifacts/IN383-PLAT01")
    args = ap.parse_args(argv)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    figures = {}
    for fn in (fig1, fig2, fig3):
        path = fn(outdir)
        figures[path.name] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        print(f"wrote {path} ({figures[path.name]['bytes']} bytes, sha256 {figures[path.name]['sha256'][:16]}…)")
    manifest = outdir / "figures.json"
    manifest.write_text(json.dumps(figures, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())