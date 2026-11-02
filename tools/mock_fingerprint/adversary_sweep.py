#!/usr/bin/env python3
"""Adversarial evasion sweep for the MOCK-fingerprint detectors.

Why this exists
---------------
``tools/mock_fingerprint`` is the platform's structural trust gate: it decides
whether an endpoint that *self-declares* REAL is in fact synthesising numbers.
The gate was validated in ``docs/verification_debt/propeller_endpoint/mock_gate.md``
by feeding it one synthetic defective response (``KQ = KT/10``) and one healthy
response (the Wageningen B-series regression).  That is a two-point validation:
it proves the detectors *can* fire and *can* stay silent, but says nothing about
**how much a mocker has to perturb its output to walk through the gate**.

That question is the one that matters operationally.  A MOCK written today is
rarely a clean analytic law; it is a table lookup, a scaled surrogate or a law
plus a rounding artefact.  This module measures the *evasion threshold*: the
smallest relative perturbation ``eps`` of a synthetic relation that makes every
structural detector go silent, i.e. lets a MOCK be adjudicated REAL.

Method
------
* ``healthy()`` produces the reference-free calibration series from the
  in-repo Wageningen B-series regressions (``tools/ref_anchor_lib.py``), i.e. a
  response that a REAL endpoint would be consistent with.
* ``fake_*(eps)`` produces synthesised responses of four families:
    F1 ``exact``      KQ = KT/10, a pure synthetic ratio (the historical MOCK);
    F2 ``ratio_jitter`` KQ = KT * 1.1 * (1 + eps*w) — same defect, noisy w;
    F3 ``power_law``  KT = c*J^k, blind to P/D, Ae/Ao and Z (parameter-blind);
    F4 ``clamp``      eta0 capped at 0.85 over the upper J range.
* Each family is pushed through the *existing, unmodified* detectors
  (``ratio_lock``, ``parameter_blindness``, ``physical_bound``) at
  eps in {0, 1e-12, 1e-9, 1e-6, 1e-4, 1e-2} and the verdict
  ``MOCK`` (any structural detector fires) / ``REAL`` (none fires) is recorded.

Outputs (artifacts/mock_fingerprint/):
    detector_evasion_sweep.json   machine-readable power table
    detector_evasion_sweep.png    evasion-threshold figure

Usage
-----
    python3 -m tools.mock_fingerprint.adversary_sweep
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import ref_anchor_lib as ref  # noqa: E402
from tools.mock_fingerprint import (  # noqa: E402
    ratio_lock,
    parameter_blindness,
    physical_bound,
    clamp_plateau,
)

OUT_DIR = ROOT / "artifacts" / "mock_fingerprint"
EPS_GRID = [0.0, 1e-12, 1e-9, 1e-6, 1e-4, 1e-2]
J_GRID = [round(i / 20.0, 3) for i in range(0, 26)]        # J = 0 .. 1.25
DESIGNS = [(1.0, 0.70, 4), (1.4, 0.75, 5), (0.8, 0.70, 4)]


def _wobble(i: int) -> float:
    """Deterministic +-1 sequence (no RNG: the artefact must be reproducible)."""
    return 1.0 if (i * 7919) % 13 < 7 else -1.0


# ───────────────────────────── response families ─────────────────────────────
def healthy() -> List[Dict]:
    out = []
    for k, (p_d, ae, z) in enumerate(DESIGNS):
        for i, J in enumerate(J_GRID):
            kt = ref.b_series_kt(J, p_d, ae, z)
            kq = ref.b_series_kq(J, p_d, ae, z)
            out.append({"design_key": f"D{k}", "P_D": p_d, "Ae_Ao": ae, "Z": z,
                        "J": J, "KT": kt, "KQ": kq,
                        "eta0": (J * kt / (2 * math.pi * kq)) if kq and kq > 0 else 0.0})
    return out


def fake_exact_law(eps: float = 0.0) -> List[Dict]:
    """F1/F2: synthetic ratio KQ = KT/10 (with optional relative jitter eps)."""
    out = []
    for k, (p_d, ae, z) in enumerate(DESIGNS):
        for i, J in enumerate(J_GRID):
            kt = 0.36 * max(0.0, 1.0 - J / 1.25) ** 0.5
            kq = (kt / 10.0) * (1.0 + eps * _wobble(i + 31 * k))
            out.append({"design_key": f"D{k}", "P_D": p_d, "Ae_Ao": ae, "Z": z,
                        "J": J, "KT": kt, "KQ": kq,
                        "eta0": (J * kt / (2 * math.pi * kq)) if kq > 0 else 0.0})
    return out


def fake_power_law(eps: float = 0.0) -> List[Dict]:
    """F3: KT = c J^k, identical for every design (blind to P/D, Ae/Ao, Z)."""
    out = []
    for k, (p_d, ae, z) in enumerate(DESIGNS):
        for i, J in enumerate(J_GRID):
            kt = 0.40 * max(1e-9, (1.05 - J)) ** 1.1
            kq = 0.045 * max(1e-9, (1.05 - J)) ** 1.4
            jit = 1.0 + eps * _wobble(i + 17 * k)
            out.append({"design_key": f"D{k}", "P_D": p_d, "Ae_Ao": ae, "Z": z,
                        "J": J, "KT": kt * jit, "KQ": kq * (1.0 + eps * _wobble(i)) ,
                        "eta0": (J * kt / (2 * math.pi * kq)) if kq > 0 else 0.0})
    return out


def fake_clamped(eps: float = 0.0) -> List[Dict]:
    """F4: efficiency clamped at 0.85 above J = 0.6 (programme cap, not physics)."""
    out = []
    for k, (p_d, ae, z) in enumerate(DESIGNS):
        for i, J in enumerate(J_GRID):
            kt = ref.b_series_kt(J, p_d, ae, z)
            kq = ref.b_series_kq(J, p_d, ae, z)
            eta = (J * kt / (2 * math.pi * kq)) if kq and kq > 0 else 0.0
            if J > 0.6:
                eta = 0.85 * (1.0 + eps * _wobble(i + 5 * k))
            out.append({"design_key": f"D{k}", "P_D": p_d, "Ae_Ao": ae, "Z": z,
                        "J": J, "KT": kt, "KQ": kq, "eta0": eta})
    return out


# ───────────────────────────── detector battery ──────────────────────────────
def run_detectors(records: Sequence[Dict]) -> Dict:
    """Push one response set through the unmodified structural detectors."""
    drag = [d for d in records if d["J"] > 0.0]                       # avoid 0/0
    at_j = [r for r in records if abs(r["J"] - 0.6) < 1e-9]
    dets = [
        ratio_lock(drag, "KQ", "KT"),
        ratio_lock(drag, "eta0", "J"),
        parameter_blindness(at_j, "P_D", "KT"),
        parameter_blindness(at_j, "P_D", "eta0"),
        parameter_blindness([{"param": r["Z"], "v": r["KT"]} for r in at_j], "param", "v"),
        physical_bound(records, "eta0", 1.0),
        clamp_plateau(records, "eta0", 0.6),
        clamp_plateau(records, "eta0", 0.8),
    ]
    fired = [d for d in dets if d.get("triggered")]
    return {
        "verdict": "MOCK" if fired else "REAL",
        "fired": [d["name"] + "/" + (d.get("expr") or d.get("metric") or d.get("param") or "")
                  for d in fired],
        "min_spread": min((d.get("rel_spread") for d in dets
                           if d.get("rel_spread") is not None), default=None),
        "detectors": dets,
    }


def sweep() -> Dict:
    rows = []
    families = {
        "healthy_Bseries": lambda eps: healthy(),
        "F1_exact_ratio": fake_exact_law,
        "F3_power_law_blind": fake_power_law,
        "F4_clamped_eta": fake_clamped,
    }
    for name, fn in families.items():
        for eps in (EPS_GRID if name in ("F1_exact_ratio", "F3_power_law_blind",
                                        "F4_clamped_eta") else [0.0]):
            res = run_detectors(fn(eps))
            rows.append({"family": name, "eps": eps, "verdict": res["verdict"],
                         "fired": res["fired"], "min_rel_spread": res["min_spread"]})
    # evasion thresholds: smallest eps at which the family is adjudicated REAL
    thresholds = {}
    for name in families:
        fam = [r for r in rows if r["family"] == name]
        evaded = [r["eps"] for r in fam if r["verdict"] == "REAL"]
        thresholds[name] = min(evaded) if evaded else None
    # parameter sensitivity reference: how much KT really changes with Z (4 -> 5)
    kt4 = [ref.b_series_kt(J, 1.0, 0.70, 4) for J in J_GRID if J > 0]
    kt5 = [ref.b_series_kt(J, 1.0, 0.70, 5) for J in J_GRID if J > 0]
    z_effect = max(abs(a - b) / max(abs(a), 1e-12) for a, b in zip(kt4, kt5))
    return {"eps_grid": EPS_GRID, "j_grid": J_GRID, "designs": DESIGNS,
            "rows": rows, "evasion_threshold_eps": thresholds,
            "rel_tol_in_force": 1e-12,
            "z_sensitivity_max_rel": z_effect,
            "verdict": {
                "structural_gate_evadable_at_eps": thresholds.get("F1_exact_ratio"),
                "healthy_false_positive": any(
                    r["verdict"] == "MOCK" for r in rows if r["family"] == "healthy_Bseries"),
                "note": ("rel_tol=1e-12 means any injection with >=1e-9 relative "
                         "perturbation is adjudicated REAL"),
            }}


def make_figure(res: Dict, path: Path) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fams = ["F1_exact_ratio", "F3_power_law_blind", "F4_clamped_eta"]
    eps = res["eps_grid"]
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.6))

    ax = axes[0]
    for i, fam in enumerate(fams):
        caught = [1.0 if next(r for r in res["rows"]
                              if r["family"] == fam and r["eps"] == e)["verdict"] == "MOCK"
                  else 0.0 for e in eps]
        ax.step(range(len(eps)), caught, where="mid", marker="o", label=fam)
    ax.set_xticks(range(len(eps)))
    ax.set_xticklabels([("0" if e == 0 else f"{e:g}") for e in eps])
    ax.set_xlabel(r"injected relative perturbation $\varepsilon$")
    ax.set_ylabel("detector fires (1 = MOCK caught)")
    ax.set_title("Structural detectors vs perturbation of the same defect")
    ax.set_ylim(-0.15, 1.15); ax.grid(alpha=0.3); ax.legend(fontsize=8)

    ax = axes[1]
    h = run_detectors(healthy())
    labels = [d["name"] + "\n" + (d.get("expr") or d.get("metric") or d.get("param") or "")
              for d in h["detectors"]]
    spreads = [d.get("rel_spread") for d in h["detectors"]]
    vals = [s if s is not None else 1e-18 for s in spreads]
    ax.barh(range(len(vals)), [math.log10(v) if v and v > 0 else -18 for v in vals],
            color="#2e86c1")
    ax.axvline(math.log10(res["rel_tol_in_force"]), color="crimson", ls="--",
               label=f"rel_tol in force = {res['rel_tol_in_force']:g}")
    ax.set_yticks(range(len(vals))); ax.set_yticklabels(labels, fontsize=6)
    ax.set_xlabel(r"$\log_{10}$ relative spread of a HEALTHY response")
    ax.set_title("Head-room: healthy series sit far above the trigger tolerance")
    ax.grid(alpha=0.3); ax.legend(fontsize=8)

    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return str(path)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    res = sweep()
    fig = make_figure(res, OUT_DIR / "detector_evasion_sweep.png")
    res["figure"] = str(Path(fig).relative_to(ROOT))
    (OUT_DIR / "detector_evasion_sweep.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"evasion_threshold_eps": res["evasion_threshold_eps"],
                      "z_sensitivity_max_rel": res["z_sensitivity_max_rel"],
                      "verdict": res["verdict"]}, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())