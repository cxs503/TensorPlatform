#!/usr/bin/env python3
"""Quantify the platform resistance model against the audited reference dataset.

The platform model is the simplified Holtrop/ITTC-1957 combination implemented in
``backend/api/calculations.py::calc_resistance``:

    S  = pi * B * L * (0.5 + 0.25 * Cb)
    Cf = 0.075 / (log10(Re) - 2)^2                       (ITTC-1957)
    Cr = 0.002 * (1 + 2*Cb) * Fn^2.5        (Fn > 0.05, else 0)
    Ct = Cf + Cr

Because friction and residual terms share the same reference surface, the total
coefficient is surface-independent, so ``Ct`` can be compared with
``experimental_ct`` directly.  The function ``platform_model`` below is a faithful
transcription of that code path; ``backend/tests/feat/`` pins it to the source with
a drift guard so the replica cannot silently diverge.

Outputs (``artifacts/m1512/``):
    resistance_vs_reference.json   per-case table + summary
    fig_ct_error.png               signed error per case vs the 15% acceptance band
    fig_ct_froude.png              Ct(Fn) reference vs platform vs friction floor
    fig_form_factor.png            implied form factor (1+k) required to match
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.audit_benchmark_reference import (  # noqa: E402
    DEFAULT_DATASET, audit_case,
)

OUT = REPO_ROOT / "artifacts" / "m1512"
ACCEPTANCE_PCT = 15.0
# Physically typical band for hull form factors (1+k1) of conventional hulls;
# used only to judge whether a model residual is structurally missing.
FORM_FACTOR_BAND = (1.05, 1.40)


def platform_model(length: float, beam: float, draft: float, cb: float,
                   speed: float, rho: float, nu: float,
                   with_form_factor: float = 1.0,
                   with_correlation_allowance: float = 0.0) -> dict:
    """Faithful replica of backend/api/calculations.py::calc_resistance."""
    S = math.pi * beam * length * (0.5 + 0.25 * cb)
    Re = speed * length / nu
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
    Fn = speed / math.sqrt(9.81 * length)
    Cr = 0.002 * (1 + 2.0 * cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    # Optional structurally-missing terms (defaults reproduce the shipped model).
    Ct = Cf * with_form_factor + Cr + with_correlation_allowance
    q = 0.5 * rho * speed ** 2
    return {"wetted_surface_m2": S, "reynolds": Re, "cf": Cf, "cr": Cr,
            "fn": Fn, "ct": Ct, "rt_n": Ct * q * S}


def run() -> dict:
    data = json.loads(Path(DEFAULT_DATASET).read_text(encoding="utf-8"))
    rows = []
    for case in data["cases"]:
        aud = audit_case(case)
        g = case["geometry"]
        m = platform_model(
            length=g["length_m"],
            beam=g.get("beam_m", g.get("diameter_m")),
            draft=g.get("draft_m", g.get("diameter_m")),
            cb=g.get("cb", 0.5),
            speed=case["speed_ms"],
            rho=case["fluid"]["rho"],
            nu=case["fluid"]["nu"])
        ct_ref = case["experimental_ct"]
        err = (m["ct"] - ct_ref) / ct_ref * 100.0
        # Form factor the model would need to reproduce the reference exactly.
        implied_1k = (ct_ref - m["cr"]) / m["cf"] if m["cf"] else float("nan")
        rows.append({
            "case_id": case["case_id"],
            "ship_type": case["ship_type"],
            "scale": case["scale"],
            "speed_ms": case["speed_ms"],
            "froude_number": m["fn"],
            "reynolds": m["reynolds"],
            "cf_ittc57": m["cf"],
            "cr_platform": m["cr"],
            "ct_platform": m["ct"],
            "ct_reference": ct_ref,
            "rt_platform_n": m["rt_n"],
            "rt_reference_derived_n": case["experimental_rt_derived_n"],
            "runtime_published_rt_quarantined_n": case["experimental_rt_n"],
            "rt_status": case["rt_status"],
            "froude_status": case["froude_status"],
            "audit_verdict": aud["verdict"],
            # A case can be used to judge the *coefficient* model only if its
            # kinematics are self-consistent (G4) and its published Ct sits above
            # the friction floor (G2).  Rt-column coherence (G3) is irrelevant here.
            "eligible_for_ct_comparison": bool(
                aud["froude_ok"] and aud.get("ct_above_friction_floor")),
            "platform_wetted_surface_m2": m["wetted_surface_m2"],
            "audit_wetted_surface_m2": aud["wetted_surface_m2"],
            "wetted_surface_source": aud["wetted_surface_source"],
            "error_pct": err,
            "abs_error_pct": abs(err),
            "within_acceptance": abs(err) <= ACCEPTANCE_PCT,
            "required_form_factor_1k": implied_1k,
        })

    eligible = [r for r in rows if r["eligible_for_ct_comparison"]]
    by_type: dict[str, list] = {}
    for r in eligible:
        by_type.setdefault(r["ship_type"], []).append(r)
    summary = {
        t: {
            "n": len(rs),
            "mean_error_pct": sum(r["error_pct"] for r in rs) / len(rs),
            "mean_abs_error_pct": sum(r["abs_error_pct"] for r in rs) / len(rs),
            "max_abs_error_pct": max(r["abs_error_pct"] for r in rs),
            "within_15pct": sum(r["within_acceptance"] for r in rs),
            "within_3pct": sum(r["abs_error_pct"] <= 3.0 for r in rs),
            "required_1k_min": min(r["required_form_factor_1k"] for r in rs),
            "required_1k_max": max(r["required_form_factor_1k"] for r in rs),
        } for t, rs in by_type.items()
    }
    overall = {
        "n_eligible": len(eligible),
        "n_excluded": len(rows) - len(eligible),
        "excluded": [r["case_id"] for r in rows if not r["eligible_for_ct_comparison"]],
        "mean_abs_error_pct": sum(r["abs_error_pct"] for r in eligible) / max(len(eligible), 1),
        "max_abs_error_pct": max((r["abs_error_pct"] for r in eligible), default=0.0),
        "within_15pct": sum(r["within_acceptance"] for r in eligible),
        "within_3pct": sum(r["abs_error_pct"] <= 3.0 for r in eligible),
        "acceptance_pct": ACCEPTANCE_PCT,
        "form_factor_band": list(FORM_FACTOR_BAND),
        "cases_requiring_1k_above_band": sum(
            r["required_form_factor_1k"] > FORM_FACTOR_BAND[1] for r in eligible),
    }
    return {"cases": rows, "summary_by_type": summary, "summary_overall": overall}


def report(payload: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "resistance_vs_reference.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    rows = payload["cases"]

    print(f"{'case':13s} {'Fn':>6s} {'Cf':>8s} {'Cr':>8s} {'Ct_api':>8s} "
          f"{'Ct_ref':>8s} {'err%':>7s} {'need 1+k':>8s}")
    for r in rows:
        print(f"{r['case_id']:13s} {r['froude_number']:6.3f} {r['cf_ittc57']:8.5f} "
              f"{r['cr_platform']:8.5f} {r['ct_platform']:8.5f} {r['ct_reference']:8.5f} "
              f"{r['error_pct']:7.1f} {r['required_form_factor_1k']:8.2f}")
    o = payload["summary_overall"]
    print(f"\neligible={o['n_eligible']} excluded={o['n_excluded']} {o['excluded']}")
    print(f"overall: mean|err|={o['mean_abs_error_pct']:.1f}% "
          f"max={o['max_abs_error_pct']:.1f}% "
          f"within{o['acceptance_pct']:.0f}%={o['within_15pct']}/{o['n_eligible']} "
          f"within3%={o['within_3pct']}/{o['n_eligible']}")
    for t, s in payload["summary_by_type"].items():
        print(f"  {t:10s} mean|err|={s['mean_abs_error_pct']:5.1f}% "
              f"need 1+k in [{s['required_1k_min']:.2f}, {s['required_1k_max']:.2f}]")

    # ---- fig 1: signed error vs acceptance band --------------------------
    fig, ax = plt.subplots(figsize=(9, 4.2))
    labels = [r["case_id"] for r in rows]
    errs = [r["error_pct"] for r in rows]
    colors = ["#7f8c8d" if not r["eligible_for_ct_comparison"]
              else "#c0392b" if abs(r["error_pct"]) > ACCEPTANCE_PCT else "#27ae60"
              for r in rows]
    ax.bar(range(len(rows)), errs, color=colors)
    ax.axhline(0, color="k", lw=0.8)
    ax.axhline(ACCEPTANCE_PCT, color="#e67e22", ls="--", lw=1,
               label=f"±{ACCEPTANCE_PCT:.0f}% acceptance")
    ax.axhline(-ACCEPTANCE_PCT, color="#e67e22", ls="--", lw=1)
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Ct error (platform - reference) [%]")
    ax.set_title(f"Platform resistance model vs audited reference Ct "
                 f"({o['within_15pct']}/{o['n_eligible']} eligible cases within "
                 f"±{ACCEPTANCE_PCT:.0f}%; grey = excluded by audit)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "fig_ct_error.png", dpi=150)
    plt.close(fig)

    # ---- fig 2: Ct(Fn) curves --------------------------------------------
    by_type: dict[str, list] = {}
    for r in rows:
        by_type.setdefault(r["ship_type"], []).append(r)
    fig, axes = plt.subplots(1, len(by_type), figsize=(4.0 * len(by_type), 3.8))
    for ax, (t, rs) in zip(axes, by_type.items()):
        rs = sorted(rs, key=lambda r: r["froude_number"])
        fn = [r["froude_number"] for r in rs]
        ax.plot(fn, [r["ct_reference"] for r in rs], "o-", label="reference Ct")
        ax.plot(fn, [r["ct_platform"] for r in rs], "s--", label="platform Ct")
        ax.plot(fn, [r["cf_ittc57"] for r in rs], ":", color="gray",
                label="ITTC-57 Cf (floor)")
        ax.set_xlabel("Froude number")
        ax.set_title(t)
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("resistance coefficient [-]")
    axes[0].legend(fontsize=7)
    fig.suptitle("Reference vs platform total-resistance coefficient "
                 "(friction floor = admissibility bound)", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / "fig_ct_froude.png", dpi=150)
    plt.close(fig)

    # ---- fig 3: implied form factor --------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.scatter([r["froude_number"] for r in rows],
               [r["required_form_factor_1k"] for r in rows],
               c=["#2980b9" if r["ship_type"] == "SUBOFF" else "#8e44ad"
                  for r in rows], s=45)
    ax.axhspan(*FORM_FACTOR_BAND, color="#95a5a6", alpha=0.25,
               label="typical hull (1+k) band")
    for r in rows:
        ax.annotate(r["case_id"], (r["froude_number"], r["required_form_factor_1k"]),
                    fontsize=6, xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel("Froude number")
    ax.set_ylabel("implied form factor (1+k) = (Ct_ref - Cr)/Cf")
    ax.set_title("Form factor the shipped model would need to match the reference")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig_form_factor.png", dpi=150)
    plt.close(fig)
    print(f"artifacts -> {OUT}")


def main() -> int:
    payload = run()
    report(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())