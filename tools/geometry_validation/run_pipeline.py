#!/usr/bin/env python3
"""Campaign runner for node p372w2-n04 (geometry / mesh validation gate maturity).

Produces every artifact the report cites, all machine-generated (no hand-typed
numbers)::

    artifacts/p372w2_n04/
        cases/                     generated surfaces, meshes, logs, batch file
        figures/fig1_pipeline_flow.png
        figures/fig2_interception_stats.png
        figures/fig3_cb_convergence.png
        figures/fig4_mesh_quality.png
        validation_report.json     the gate's own output (schema 1.0)
        numbers.json               every number quoted in the report
        audit/validation_audit.jsonl   hash-chained audit trail

Usage::

    python3 tools/geometry_validation/run_pipeline.py [--out artifacts/p372w2_n04]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.services.geometry_validation import (  # noqa: E402
    AuditLog, load_profile, validate_batch, verify_audit_chain)
from backend.services.geometry_validation import geometry_extract as gx  # noqa: E402
from backend.services.geometry_validation import mesh_quality as mq  # noqa: E402
from tools.geometry_validation import fixtures_gen as fg  # noqa: E402

PROFILE = os.path.join(ROOT, "config/geometry_validation/profiles/hull_mesh_v1.json")
NODE_ID = "p372w2-n04-geometry-validation-maturity"

WIGLEY = {"L": 10.0, "B": 1.0, "T": 0.625}
BOX = {"L": 10.0, "B": 4.0, "T": 2.0}


# ---------------------------------------------------------------------------
# 1. case generation
# ---------------------------------------------------------------------------
def build_cases(case_dir: str) -> Dict[str, str]:
    os.makedirs(case_dir, exist_ok=True)
    paths: Dict[str, str] = {}

    box_tri = fg.box_triangles(**BOX)
    paths["box_stl"] = fg.write_stl_binary(os.path.join(case_dir, "box_closed.stl"), box_tri)

    for name, (nu, nv) in {"coarse": (10, 4), "medium": (20, 8),
                           "fine": (40, 16), "finer": (80, 32)}.items():
        tri = fg.wigley_triangles(WIGLEY["L"], WIGLEY["B"], WIGLEY["T"], nu, nv)
        paths[f"wigley_{name}_obj"] = fg.write_obj(
            os.path.join(case_dir, f"hull_{name}.obj"), tri)

    mixed = fg.wigley_triangles(WIGLEY["L"], WIGLEY["B"], WIGLEY["T"], 16, 6,
                               mixed_winding=True, rng_seed=7)
    paths["mixed_winding"] = fg.write_stl_binary(
        os.path.join(case_dir, "hull_mixed_winding.stl"), mixed)

    open_shell = fg.wigley_triangles(WIGLEY["L"], WIGLEY["B"], WIGLEY["T"], 12, 5,
                                     omit_bottom=True)
    paths["open_shell"] = fg.write_obj(os.path.join(case_dir, "hull_open_shell.obj"), open_shell)

    # duplicated (double-shell) export: two nested boxes -> Cb ~ 2 > 1, physically impossible
    double = np.vstack([fg.box_triangles(**BOX),
                        fg.box_triangles(BOX["L"] * 0.98, BOX["B"] * 0.98, BOX["T"] * 0.98)])
    paths["double_shell"] = fg.write_stl_binary(
        os.path.join(case_dir, "box_double_shell.stl"), double)

    paths["truncated"] = fg.write_truncated_stl(os.path.join(case_dir, "surface_truncated.stl"))
    with open(os.path.join(case_dir, "surface_unsupported.igs"), "w", encoding="utf-8") as fh:
        fh.write("IGES fixture: B-rep only, no volume information\n"
                 + "S      1\n" + "G      1\n" + "D      1\n" + "P      1\n" + "T      1\n")
    paths["iges"] = os.path.join(case_dir, "surface_unsupported.igs")

    # --- volume meshes ---------------------------------------------------
    pts, cells = fg.hex_mesh(4, 3, 2)
    paths["mesh_regular"] = fg.write_vtk_ascii(
        os.path.join(case_dir, "mesh_regular.vtk"), pts, cells)
    pts, cells = fg.hex_mesh(4, 3, 2, shear=6.0)
    paths["mesh_skewed"] = fg.write_vtk_ascii(
        os.path.join(case_dir, "mesh_skewed.vtk"), pts, cells)
    pts, cells = fg.hex_mesh(3, 3, 3, dz=0.005)
    paths["mesh_graded"] = fg.write_vtk_ascii(
        os.path.join(case_dir, "mesh_graded.vtk"), pts, cells)
    pts, cells = fg.hex_mesh(2, 2, 2, invert_cell=3)
    paths["mesh_inverted"] = fg.write_vtk_ascii(
        os.path.join(case_dir, "mesh_inverted.vtk"), pts, cells)
    paths["polymesh"] = fg.write_openfoam_polymesh(
        os.path.join(case_dir, "openfoam_case"), 3, 2, 1)

    # --- solver logs -----------------------------------------------------
    paths["yplus_pass"] = fg.write_yplus_log(
        os.path.join(case_dir, "yPlus_pass.log"),
        {"hull": (42.0, 168.0, 95.0), "appendages": (36.0, 210.0, 120.0)})
    paths["yplus_fail"] = fg.write_yplus_log(
        os.path.join(case_dir, "yPlus_fail.log"), {"hull": (0.4, 7.5, 2.1)})
    paths["checkmesh"] = fg.write_checkmesh_log(
        os.path.join(case_dir, "checkMesh.log"), n_cells=6, n_faces=29, n_points=24,
        max_skewness=0.02, max_aspect_ratio=1.0)
    return paths


def build_batch(p: Dict[str, str]) -> List[Dict[str, Any]]:
    """Curated batch: PASS cases, each engineered interception, and error recovery."""
    cb_analytic = 4.0 / 9.0
    v_analytic = cb_analytic * WIGLEY["L"] * WIGLEY["B"] * WIGLEY["T"]
    flow_pass = {"u_inf": 5.0, "nu": 1.0e-6, "reynolds": 5.0e6, "wall_distance": 3.0e-4}
    items: List[Dict[str, Any]] = [
        {   # 1. closed box: analytic V=80, Cb=1, Aw=40 — the zero-error reference
            "id": "01-box-reference",
            "geometry": {"path": p["box_stl"], "draft": BOX["T"],
                         "declared": {"length": BOX["L"], "beam": BOX["B"],
                                      "draft": BOX["T"],
                                      "displacement_volume": BOX["L"] * BOX["B"] * BOX["T"],
                                      "block_coefficient": 1.0,
                                      "waterplane_coefficient": 1.0}},
            "metadata": {"case": "analytic box", "expected": "PASS"},
        },
        {   # 2. fine Wigley hull, all declared values exact -> PASS
            "id": "02-hull-fine-declared-exact",
            "geometry": {"path": p["wigley_finer_obj"], "draft": WIGLEY["T"],
                         "declared": {"length": WIGLEY["L"], "beam": WIGLEY["B"],
                                      "draft": WIGLEY["T"],
                                      "displacement_volume": v_analytic,
                                      "block_coefficient": cb_analytic}},
            "mesh": {"path": p["mesh_regular"]},
            "flow": flow_pass,
            "metadata": {"case": "fine hull + regular hex mesh", "expected": "PASS"},
        },
        {   # 3. declared Cb 5% high -> blocked by the 1% declared tolerance
            "id": "03-declared-cb-mismatch",
            "geometry": {"path": p["wigley_finer_obj"], "draft": WIGLEY["T"],
                         "declared": {"block_coefficient": cb_analytic * 1.05}},
            "metadata": {"case": "declared Cb +5%", "expected": "BLOCK"},
        },
        {   # 4. mixed face winding: repaired, still PASS (warns)
            "id": "04-mixed-winding-repair",
            "geometry": {"path": p["mixed_winding"], "draft": WIGLEY["T"]},
            "metadata": {"case": "40% faces flipped", "expected": "PASS (W_GEOM_ORIENTATION_REPAIRED)"},
        },
        {   # 5. open shell (no bottom) -> not watertight
            "id": "05-open-shell",
            "geometry": {"path": p["open_shell"], "draft": WIGLEY["T"]},
            "metadata": {"case": "surface missing its bottom", "expected": "BLOCK"},
        },
        {   # 6. truncated STL -> parse failure, batch continues
            "id": "06-truncated-file",
            "geometry": {"path": p["truncated"], "draft": 1.0},
            "metadata": {"case": "truncated binary STL", "expected": "BLOCK"},
        },
        {   # 7. IGES without a tessellator -> capability withheld, explicit code
            "id": "07-iges-without-tessellator",
            "geometry": {"path": p["iges"], "draft": 1.0},
            "metadata": {"case": "IGES B-rep, no tessellator", "expected": "BLOCK"},
        },
        {   # 8. hull outside every empirical envelope -> warnings, not block
            "id": "08-envelope-extrapolation",
            "geometry": {"path": p["box_stl"], "draft": BOX["T"],
                         "declared": {"length": BOX["L"], "beam": BOX["B"],
                                      "draft": BOX["T"]}},
            "metadata": {"case": "Cb=1 box vs merchant-hull envelopes",
                         "expected": "PASS + W_DOMAIN_OUT_OF_ENVELOPE"},
        },
        {   # 9. OpenFOAM polyMesh + solver logs, y+ inside band -> PASS
            "id": "09-openfoam-mesh-yplus-pass",
            "mesh": {"path": p["polymesh"],
                     "log": {"yplus": p["yplus_pass"], "checkmesh": p["checkmesh"]}},
            "metadata": {"case": "polyMesh 6 cells + yPlus log", "expected": "PASS"},
        },
        {   # 10. same mesh, wall-resolved y+ from the log -> blocked
            "id": "10-yplus-out-of-band",
            "mesh": {"path": p["polymesh"], "log": {"yplus": p["yplus_fail"]}},
            "metadata": {"case": "y+ 0.4..7.5 vs band 30..300", "expected": "BLOCK"},
        },
        {   # 11. sheared mesh: skewness above 0.85
            "id": "11-mesh-skewness",
            "mesh": {"path": p["mesh_skewed"]},
            "metadata": {"case": "sheared hex layer", "expected": "BLOCK"},
        },
        {   # 12. anisotropic mesh: aspect ratio 200 > 100
            "id": "12-mesh-aspect-ratio",
            "mesh": {"path": p["mesh_graded"]},
            "metadata": {"case": "dz/dx = 1/200", "expected": "BLOCK"},
        },
        {   # 13. inverted element: negative volume
            "id": "13-mesh-inverted-cell",
            "mesh": {"path": p["mesh_inverted"]},
            "metadata": {"case": "node permutation inverts one hex", "expected": "BLOCK"},
        },
        {   # 15. duplicated body export -> Cb ~ 2, caught by the *blocking* envelope
            "id": "15-double-shell-export",
            "geometry": {"path": p["double_shell"], "draft": BOX["T"]},
            "metadata": {"case": "two nested closed shells in one STL",
                         "expected": "BLOCK (E_DOMAIN_OUT_OF_ENVELOPE)"},
        },
        {   # 14. y+ from the first cell height + flat-plate correlation
            "id": "14-yplus-from-first-cell",
            "mesh": {"path": p["polymesh"]},
            "flow": {"u_inf": 5.0, "nu": 1.0e-6, "reynolds": 5.0e6,
                     "wall_distance": 5.0e-4},
            "metadata": {"case": "y+ from first-layer height", "expected": "PASS"},
        },
    ]
    return items


# ---------------------------------------------------------------------------
# 2. convergence study (numbers for the analytic comparison)
# ---------------------------------------------------------------------------
def convergence_study(case_dir: str) -> Dict[str, Any]:
    rows = []
    analytic_cb = 4.0 / 9.0
    analytic_v = analytic_cb * WIGLEY["L"] * WIGLEY["B"] * WIGLEY["T"]
    for nu, nv in [(10, 4), (20, 8), (40, 16), (80, 32), (160, 64)]:
        tri = fg.wigley_triangles(WIGLEY["L"], WIGLEY["B"], WIGLEY["T"], nu, nv)
        metrics = gx.geometry_metrics({"triangles": tri}, draft=WIGLEY["T"])
        rows.append({
            "n_u": nu, "n_v": nv, "n_triangles": metrics["n_triangles"],
            "volume": metrics["volume_submerged"],
            "block_coefficient": metrics["block_coefficient"],
            "waterplane_area_at_design_draft": metrics["waterplane_area"],
            "watertight": metrics["topology"]["watertight"],
            "rel_error_Cb_pct": 100.0 * (metrics["block_coefficient"] - analytic_cb) / analytic_cb,
        })
    # box reference: exact at four waterlines
    box_rows = []
    tri = fg.box_triangles(**BOX)
    for draft in (0.5, 1.0, 1.5, 2.0):
        m = gx.geometry_metrics({"triangles": tri}, draft=draft)
        box_rows.append({
            "draft": draft,
            "volume": m["volume_submerged"],
            "volume_analytic": BOX["L"] * BOX["B"] * draft,
            "rel_error_pct": 100.0 * (m["volume_submerged"] - BOX["L"] * BOX["B"] * draft)
                             / (BOX["L"] * BOX["B"] * draft),
            "waterplane_area": m["waterplane_area"],
            "waterplane_area_analytic": BOX["L"] * BOX["B"],
        })
    return {"wigley": {"L": WIGLEY["L"], "B": WIGLEY["B"], "T": WIGLEY["T"],
                       "analytic_cb": analytic_cb, "analytic_volume": analytic_v,
                       "rows": rows},
            "box": {"L": BOX["L"], "B": BOX["B"], "T": BOX["T"], "rows": box_rows}}


def mesh_quality_table(case_dir: str, profile) -> List[Dict[str, Any]]:
    rows = []

    def add(name: str, mesh_payload: Dict[str, Any], flow=None) -> None:
        res = mq.evaluate_mesh(mesh_payload, profile.mesh, flow=flow)
        rows.append({
            "case": name,
            "n_cells": res["n_cells"],
            "total_volume": res["total_volume"],
            "min_volume": res["volume"]["min"],
            "max_skewness": res["skewness"]["max"],
            "max_aspect_ratio": res["aspect_ratio"]["max"],
            "y_plus": res["y_plus"].get("y_plus"),
            "violations": [v["code"] for v in res["violations"]],
        })

    flow = {"u_inf": 5.0, "nu": 1.0e-6, "reynolds": 5.0e6, "wall_distance": 3.0e-4}
    for name, kw in [("regular", {}), ("sheared_6", {"shear": 6.0}),
                     ("graded_dz_0.005", {"dz": 0.005})]:
        pts, cells = fg.hex_mesh(4, 3, 2, **kw) if name != "graded_dz_0.005" else \
            fg.hex_mesh(3, 3, 3, **kw)
        add(name, {"points": pts, "cells": cells, "format": "vtk", "path": "-",
                   "skipped_cells": 0}, flow=flow)
    pts, cells = fg.hex_mesh(2, 2, 2, invert_cell=3)
    add("inverted_cell", {"points": pts, "cells": cells, "format": "vtk", "path": "-",
                          "skipped_cells": 0})
    add("openfoam_3x2x1",
        mq.read_openfoam_polymesh(os.path.join(case_dir, "openfoam_case")))
    return rows


# ---------------------------------------------------------------------------
# 3. figures
# ---------------------------------------------------------------------------
def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def fig_pipeline_flow(path: str) -> str:
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(11, 6.2))
    ax.set_axis_off()
    boxes = [
        (0.02, 0.78, "batch items\n(JSON schema 1.0)", "#dbe9f6"),
        (0.22, 0.78, "surface reader\nSTL / OBJ / IGES(tess.)", "#dbe9f6"),
        (0.44, 0.78, "topology gate\nweld + winding repair\nwatertight?", "#f9e0c7"),
        (0.66, 0.78, "integral geometry\nV(z), Aw = dV/dz, Cb, Cwp, LCB", "#d8f0d8"),
        (0.66, 0.50, "declared vs derived\n|rel| <= 1%", "#f9e0c7"),
        (0.44, 0.50, "applicability envelopes\n(profile data, cited)", "#f9e0c7"),
        (0.22, 0.50, "volume-mesh reader\nVTK / polyMesh / logs", "#dbe9f6"),
        (0.02, 0.50, "mesh quality\nskewness < 0.85\naspect ratio < 100\ny+ in [30, 300]", "#f9e0c7"),
        (0.02, 0.16, "verdict engine\nPASS / BLOCK / ERROR", "#e6d5f2"),
        (0.30, 0.16, "reason-code registry\n(E_* / W_* machine codes)", "#e6d5f2"),
        (0.58, 0.16, "JSON report\nschema 1.0 + statistics", "#e6d5f2"),
        (0.80, 0.16, "audit log\nsha256 hash chain", "#e6d5f2"),
    ]
    for x, y, label, color in boxes:
        ax.add_patch(plt.Rectangle((x, y), 0.18, 0.15, facecolor=color,
                                   edgecolor="#333", linewidth=1.2, zorder=2))
        ax.text(x + 0.09, y + 0.075, label, ha="center", va="center", fontsize=8.2,
                zorder=3)
    arrows = [((0.20, 0.855), (0.22, 0.855)), ((0.42, 0.855), (0.44, 0.855)),
              ((0.62, 0.855), (0.66, 0.855)), ((0.75, 0.78), (0.75, 0.65)),
              ((0.66, 0.575), (0.62, 0.575)), ((0.44, 0.575), (0.40, 0.575)),
              ((0.22, 0.575), (0.20, 0.575)), ((0.11, 0.50), (0.11, 0.31)),
              ((0.30, 0.235), (0.30, 0.37)), ((0.48, 0.235), (0.58, 0.235)),
              ((0.76, 0.235), (0.80, 0.235))]
    for (x0, y0), (x1, y1) in arrows:
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="->", color="#333", lw=1.4))
    ax.text(0.75, 0.60, "derived geometry metrics feed the envelope interceptor",
            fontsize=7.5, ha="center", color="#444")
    ax.text(0.11, 0.37, "both gates\nOR-ed", fontsize=7.5, ha="center", color="#444")
    ax.set_title("Pre-solve geometry / mesh validation gate — mature pipeline "
                 "(p372w2-n04)", fontsize=11.5)
    ax.set_xlim(0, 1)
    ax.set_ylim(0.08, 1.0)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def fig_interception_stats(report: Dict[str, Any], path: str) -> str:
    plt = _mpl()
    counts = report["summary"]["reason_code_counts"]
    verdicts = report["summary"]["verdict_counts"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 5.0),
                                   gridspec_kw={"width_ratios": [1.55, 1.0]})
    codes = list(counts.keys())
    vals = [counts[c] for c in codes]
    colors = ["#c0392b" if c.startswith("E_") else "#e09f3e" for c in codes]
    ax1.barh(range(len(codes)), vals, color=colors)
    ax1.set_yticks(range(len(codes)))
    ax1.set_yticklabels(codes, fontsize=8)
    ax1.invert_yaxis()
    ax1.set_xlabel("batch items flagged")
    ax1.set_title("Interception statistics by reason code\n"
                  f"({report['run']['n_items']} items, run {report['run']['run_id']})",
                  fontsize=10.5)
    for i, v in enumerate(vals):
        ax1.text(v + 0.05, i, str(v), va="center", fontsize=8)
    ax1.set_xlim(0, max(vals) + 1 if vals else 1)

    labels = ["PASS", "BLOCK", "ERROR"]
    values = [verdicts.get(k, 0) for k in labels]
    bars = ax2.bar(labels, values, color=["#2e8b57", "#c0392b", "#7f8c8d"])
    for bar, v in zip(bars, values):
        ax2.text(bar.get_x() + bar.get_width() / 2, v + 0.05, str(v),
                 ha="center", fontsize=10)
    ax2.set_ylabel("items")
    ax2.set_title("Verdict distribution\nblock_rate = "
                  f"{report['run']['block_rate']:.3f}", fontsize=10.5)
    ax2.set_ylim(0, max(values) + 1.5 if values else 1)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def fig_convergence(conv: Dict[str, Any], path: str) -> str:
    plt = _mpl()
    rows = conv["wigley"]["rows"]
    nu = np.array([r["n_u"] for r in rows], dtype=float)
    cb = np.array([r["block_coefficient"] for r in rows], dtype=float)
    err = np.array([abs(r["rel_error_Cb_pct"]) for r in rows], dtype=float)
    analytic = conv["wigley"]["analytic_cb"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.6))
    ax1.plot(nu, cb, "o-", label="triangulated mesh (divergence theorem)")
    ax1.axhline(analytic, color="#c0392b", ls="--", label=f"analytic Cb = 4/9 = {analytic:.6f}")
    ax1.set_xscale("log")
    ax1.set_xlabel("streamwise panels n_u (n_v = n_u/2.5)")
    ax1.set_ylabel("block coefficient Cb")
    ax1.set_title("Wigley hull: Cb from mesh volume vs analytic", fontsize=10.5)
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)

    ax2.loglog(nu, err, "s-", color="#1f6f8b", label="|relative error| [%]")
    ref = err[0] * (nu[0] / nu) ** 2
    ax2.loglog(nu, ref, "k:", label="O(h^2) reference (slope -2)")
    ax2.set_xlabel("streamwise panels n_u")
    ax2.set_ylabel("relative error of Cb [%]")
    ax2.set_title("Convergence: second-order in panel size", fontsize=10.5)
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def fig_mesh_quality(rows: List[Dict[str, Any]], profile, path: str) -> str:
    plt = _mpl()
    names = [r["case"] for r in rows]
    skew = [r["max_skewness"] for r in rows]
    ar = [r["max_aspect_ratio"] for r in rows]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 4.8))
    idx = np.arange(len(names))
    ax1.bar(idx, skew, color=["#c0392b" if v > profile.mesh["max_skewness"] else "#2e8b57"
                              for v in skew])
    ax1.axhline(profile.mesh["max_skewness"], color="k", ls="--",
                label=f"limit {profile.mesh['max_skewness']}")
    ax1.set_xticks(idx)
    ax1.set_xticklabels(names, rotation=25, ha="right", fontsize=8)
    ax1.set_ylabel("max equiangle skewness")
    ax1.set_title("Mesh skewness per fixture", fontsize=10.5)
    ax1.legend(fontsize=8)

    ax2.bar(idx, ar, color=["#c0392b" if v > profile.mesh["max_aspect_ratio"] else "#2e8b57"
                            for v in ar])
    ax2.axhline(profile.mesh["max_aspect_ratio"], color="k", ls="--",
                label=f"limit {profile.mesh['max_aspect_ratio']:g}")
    ax2.set_yscale("log")
    ax2.set_xticks(idx)
    ax2.set_xticklabels(names, rotation=25, ha="right", fontsize=8)
    ax2.set_ylabel("max aspect ratio (log)")
    ax2.set_title("Mesh aspect ratio per fixture", fontsize=10.5)
    ax2.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# 4. driver
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "artifacts", "p372w2_n04"))
    args = ap.parse_args(argv)
    out = args.out
    case_dir = os.path.join(out, "cases")
    fig_dir = os.path.join(out, "figures")
    os.makedirs(case_dir, exist_ok=True)
    os.makedirs(fig_dir, exist_ok=True)
    os.makedirs(os.path.join(out, "audit"), exist_ok=True)

    profile = load_profile(PROFILE)
    paths = build_cases(case_dir)
    items = build_batch(paths)
    with open(os.path.join(case_dir, "batch_items.json"), "w", encoding="utf-8") as fh:
        json.dump({"items": items}, fh, indent=2, ensure_ascii=False)

    audit_path = os.path.join(out, "audit", "validation_audit.jsonl")
    if os.path.isfile(audit_path):
        os.remove(audit_path)
    audit = AuditLog(audit_path, run_id="p372w2-n04")
    report = validate_batch(items, profile, audit=audit, run_metadata={"node": NODE_ID})
    with open(os.path.join(out, "validation_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True, ensure_ascii=False)

    conv = convergence_study(case_dir)
    mesh_rows = mesh_quality_table(case_dir, profile)

    figures = {
        "fig1_pipeline_flow": fig_pipeline_flow(os.path.join(fig_dir, "fig1_pipeline_flow.png")),
        "fig2_interception_stats": fig_interception_stats(
            report, os.path.join(fig_dir, "fig2_interception_stats.png")),
        "fig3_cb_convergence": fig_convergence(
            conv, os.path.join(fig_dir, "fig3_cb_convergence.png")),
        "fig4_mesh_quality": fig_mesh_quality(
            mesh_rows, profile, os.path.join(fig_dir, "fig4_mesh_quality.png")),
    }

    numbers: Dict[str, Any] = {
        "node": NODE_ID,
        "profile": profile.summary(),
        "profile_path": os.path.relpath(PROFILE, ROOT),
        "gate_summary": report["summary"],
        "gate_run": report["run"],
        "audit_chain": report.get("audit", {}).get("chain"),
        "audit_chain_recheck": verify_audit_chain(audit_path),
        "item_table": [
            {"id": it["id"], "verdict": it["verdict"],
             "reason_codes": it["reason_codes"],
             "expected": it["metadata"].get("expected"),
             "case": it["metadata"].get("case"),
             "cb": ((it.get("geometry") or {}).get("metrics") or {}).get("block_coefficient"),
             "max_skewness": ((it.get("mesh") or {}).get("skewness") or {}).get("max"),
             "max_aspect_ratio": ((it.get("mesh") or {}).get("aspect_ratio") or {}).get("max")}
            for it in report["items"]
        ],
        "convergence": conv,
        "mesh_quality_table": mesh_rows,
        "figures": {k: os.path.relpath(v, ROOT) for k, v in figures.items()},
    }
    with open(os.path.join(out, "numbers.json"), "w", encoding="utf-8") as fh:
        json.dump(numbers, fh, indent=2, ensure_ascii=False)

    # console summary (report text is generated from numbers.json, not retyped)
    print(f"profile          : {profile.profile_id}  sha256={profile.sha256[:16]}")
    print(f"gate             : {report['run']['n_items']} items -> "
          f"PASS {report['run']['n_pass']} / BLOCK {report['run']['n_block']} / "
          f"ERROR {report['run']['n_error']}  block_rate={report['run']['block_rate']:.3f}")
    print("reason codes     : " + ", ".join(f"{k}={v}"
                                            for k, v in report["summary"]["reason_code_counts"].items()))
    print(f"audit chain      : ok={report['audit']['chain']['ok']} "
          f"entries={report['audit']['chain']['n_entries']} "
          f"head={report['audit']['chain']['head_hash'][:16]}")
    for row in conv["wigley"]["rows"]:
        print(f"  wigley n_u={row['n_u']:>4} n_v={row['n_v']:>3} tris={row['n_triangles']:>6} "
              f"Cb={row['block_coefficient']:.6f} err={row['rel_error_Cb_pct']:+.4f}%")
    for row in conv["box"]["rows"]:
        print(f"  box draft={row['draft']:.2f} V={row['volume']:.6f} "
              f"(analytic {row['volume_analytic']:.6f}) err={row['rel_error_pct']:+.2e}% "
              f"Aw={row['waterplane_area']:.4f}")
    for row in mesh_rows:
        print(f"  mesh {row['case']:<18} cells={row['n_cells']:>3} V={row['total_volume']:.4f} "
              f"skew={row['max_skewness']:.4f} AR={row['max_aspect_ratio']:.2f} "
              f"viol={row['violations']}")
    print(f"artifacts        : {os.path.relpath(out, ROOT)}")
    return 0 if report["run"]["n_error"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())