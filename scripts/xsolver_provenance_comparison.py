#!/usr/bin/env python3
"""Measure the same conservation probe against the declared repo and against a shadow
copy of the package, and plot the two drift curves.

Why this exists: the cross-solver frame's C2 originally certified a mass residual of
9.3e-07 at 100 steps -- measured, not quoted, but measured on a *different* checkout
than the commit recorded next to it, because an editable install of ``tensorlbm``
pointing at another worktree wins the import over ``sys.path``.  This script keeps the
comparison reproducible: both arms are measured here, in one process, with the module
file and its sha256 recorded, so the claim "pin the import or the number is worthless"
can be re-checked instead of believed.

Outputs
    artifacts/xsolver/probe/provenance_comparison.json
    artifacts/xsolver/figures/fig4_provenance_mass_defect.png
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

from services.xsolver import probe as P  # noqa: E402

ART = REPO / "artifacts" / "xsolver"
HORIZONS = [10, 100, 1000, 5000]
TOL = 1e-6


def measure_with(module, *, tau: float, u0: float, nx: int, ny: int,
                 dtype, horizons) -> dict:
    import torch

    x = torch.arange(nx, dtype=dtype).view(1, nx)
    y = torch.arange(ny, dtype=dtype).view(ny, 1)
    rho0 = torch.ones((ny, nx), dtype=dtype)
    ux0 = torch.full((ny, nx), u0, dtype=dtype) + 0.01 * torch.sin(2 * math.pi * y / ny)
    uy0 = 0.01 * torch.sin(2 * math.pi * x / nx) + torch.zeros((ny, nx), dtype=dtype)

    f = module.equilibrium(rho0, ux0, uy0)
    rho, ux, uy = module.macroscopic(f)
    mass_ref = float(rho.sum())
    px_ref = float((rho * ux).sum())

    rows, step = [], 0
    for horizon in sorted(horizons):
        while step < horizon:
            f = module.collide_bgk(f, tau)
            f = module.stream(f)
            step += 1
        rho, ux, uy = module.macroscopic(f)
        rows.append({"steps": horizon,
                     "mass": abs(float(rho.sum()) - mass_ref) / mass_ref,
                     "momentum": abs(float((rho * ux).sum()) - px_ref) / abs(px_ref)})
    rate = rows[0]["mass"] / rows[0]["steps"] if rows[0]["steps"] else None
    return {"horizons": rows,
            "drift_rate_per_step": rate,
            "crossing_steps": (TOL / rate) if rate else None}


def main() -> int:
    spec = P.SPEC["probe"]
    backend = P.SPEC["backends"][spec["backend"]]
    dtype_name = spec.get("dtype", "float64")

    import torch

    dtype = torch.float64 if dtype_name == "float64" else torch.float32
    kwargs = {"tau": float(spec["tau"]), "u0": float(spec["velocity"]),
              "nx": int(spec["grid"]["nx"]), "ny": int(spec["grid"]["ny"]),
              "dtype": dtype, "horizons": HORIZONS}

    # arm 1: pinned to the declared repo (the frame's production path)
    pinned_module, provenance = P._load_backend(backend)
    pinned = measure_with(pinned_module, **kwargs)
    pinned["origin"] = provenance

    # arm 2: whatever a plain import resolves to (the shadow copy)
    for name in [n for n in list(sys.modules) if n == backend["module"]
                 or n.startswith(backend["module"] + ".")]:
        del sys.modules[name]
    shadow_module = importlib.import_module(backend["module"])
    shadow_file = Path(shadow_module.__file__).resolve()
    shadow = measure_with(shadow_module, **kwargs)
    shadow["origin"] = {
        "module_file": str(shadow_file),
        "module_sha256": hashlib.sha256(shadow_file.read_bytes()).hexdigest(),
        "inside_declared_repo": bool(provenance["repo_root"]
                                     and Path(provenance["repo_root"]).resolve()
                                     in shadow_file.parents),
        "version": getattr(shadow_module, "__version__", None),
    }

    report = {
        "schema": "xsolver/provenance_comparison/v1",
        "purpose": ("same probe, same process, two import origins: a residual is only "
                    "attributable to the commit recorded beside it if the module came "
                    "from that checkout"),
        "tolerances": {"mass": TOL},
        "pinned": {"label": "declared repo (pinned import)", **pinned},
        "shadow": {"label": "plain import (editable install shadow)", **shadow},
    }
    out = ART / "probe" / "provenance_comparison.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True),
                   encoding="utf-8")

    _figure(report, ART / "figures" / "fig4_provenance_mass_defect.png")
    print(json.dumps({
        "pinned": {k: pinned[k] for k in ("drift_rate_per_step", "crossing_steps")},
        "shadow": {k: shadow[k] for k in ("drift_rate_per_step", "crossing_steps")},
        "pinned_origin": pinned["origin"]["module_file"],
        "shadow_origin": shadow["origin"]["module_file"],
        "artifact": str(out),
    }, indent=1))
    return 0


def _figure(report: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    styles = {"pinned": ("#1f77b4", "o-"), "shadow": ("#d62728", "s--")}
    for key in ("pinned", "shadow"):
        arm = report[key]
        xs = [r["steps"] for r in arm["horizons"]]
        ys = [r["mass"] for r in arm["horizons"]]
        colour, style = styles[key]
        ax.loglog(xs, ys, style, color=colour, label=arm["label"], lw=1.8, ms=5)
        if arm["crossing_steps"] and arm["crossing_steps"] < 1e7:
            ax.axvline(arm["crossing_steps"], color=colour, lw=0.9, ls=":")
            ax.annotate(f"1e-6 at {arm['crossing_steps']:.0f} steps",
                        xy=(arm["crossing_steps"], TOL), xytext=(6, 6),
                        textcoords="offset points", color=colour, fontsize=8)
    ax.axhline(TOL, color="k", lw=1.0, ls="-.", label="C2 mass tolerance 1e-6")
    ax.set_xlabel("BGK steps (32x32 periodic box, float64, $\\tau$=0.8)")
    ax.set_ylabel("relative mass defect $|\\sum\\rho_N-\\sum\\rho_0|/\\sum\\rho_0$")
    ax.set_title("Same probe, two import origins: provenance decides the number")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())