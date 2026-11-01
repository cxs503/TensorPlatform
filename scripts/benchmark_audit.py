"""
Benchmark reference-data audit + resistance-engine analytic ledger (m1406).

Two independent jobs, both data-layer:

1. Reference base audit — ``backend/data/benchmark_reference.json`` is the only
   experimental reference behind ``/calculations/benchmark_validate``.  Every row
   is checked against the identities it is quoted under
   (C_T = R_T/(0.5 rho S V^2), Fn = V/sqrt(gL), Delta = rho L B T Cb) and graded
   REAL / WITHHELD by ``services.benchmark_integrity``.

2. Engine analytic ledger — the live resistance endpoint is recomputed from closed
   forms (ITTC-1957 friction line, Reynolds/Froude definitions, R_T = R_f + R_r),
   so the numbers in the report are machine-derived rather than transcribed.

Outputs
-------
runs/m1406/benchmark_audit.json      full ledger (dataset audit + engine checks)
docs/benchmarks/figs/benchmark_audit.png

Usage
-----
    python3 scripts/benchmark_audit.py [--api http://127.0.0.1:8010/api/v1]
"""
from __future__ import annotations

import argparse
import json
import os
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from services.benchmark_integrity import audit_dataset  # noqa: E402

DATASET = ROOT / "backend" / "data" / "benchmark_reference.json"
OUT_DIR = ROOT / "runs" / "m1406"
FIG_DIR = ROOT / "docs" / "benchmarks" / "figs"

G = 9.81


def _token() -> str:
    """Bearer token for the platform API (env, then the shift token file)."""
    tok = os.getenv("TENSOR_TOKEN", "").strip()
    if tok:
        return tok
    for path in (Path("/tmp/tok.txt"), Path.home() / ".tensor_token"):
        try:
            return path.read_text().strip()
        except OSError:
            continue
    return ""


def post_json(url: str, body: dict, timeout: float = 15.0) -> dict:
    headers = {"Content-Type": "application/json"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _rel(value: float, reference: float) -> float:
    return abs(value - reference) / abs(reference) if reference else float("inf")


def engine_ledger(api_base: str, case: dict) -> dict:
    """Recompute the resistance engine's outputs from the definitions."""
    geom = case["geometry"]
    payload = {
        "ship_type": case["ship_type"],
        "speed_ms": case["speed_ms"],
        "length": geom["length_m"],
        "beam": geom.get("beam_m") or geom.get("diameter_m"),
        "draft": geom.get("draft_m") or geom.get("diameter_m"),
        "cb": geom.get("cb") or 0.542,
    }
    live = post_json(f"{api_base}/calculations/resistance", payload)

    V = payload["speed_ms"]
    L = payload["length"]
    B = payload["beam"]
    T = payload["draft"]
    Cb = payload["cb"]
    rho = 998.0
    nu = 1.004e-6

    checks = {}

    # Re = V L / nu
    re_ref = V * L / nu
    checks["reynolds"] = {
        "api": live["reynolds_number"], "closed_form": round(re_ref, 2),
        "rel_error": _rel(live["reynolds_number"], re_ref),
    }

    # Fn = V / sqrt(g L)
    fn_ref = V / math.sqrt(G * L)
    checks["froude"] = {
        "api": live["froude_number"], "closed_form": round(fn_ref, 6),
        "rel_error": _rel(live["froude_number"], fn_ref),
    }

    # The deployed build may predate the transparency fields (wetted_surface_m2,
    # cf): derive them from the reported resistances instead of assuming them.
    S_formula = math.pi * B * L * (0.5 + 0.25 * Cb)
    S = live.get("wetted_surface_m2") or S_formula
    api_cf = live.get("cf")
    if api_cf is None:
        api_cf = live["frictional_resistance_n"] / (0.5 * rho * S * V ** 2)

    # ITTC-1957 friction line evaluated at the engine's own Reynolds number
    cf_ref = 0.075 / (math.log10(live["reynolds_number"]) - 2.0) ** 2
    checks["ittc1957_cf"] = {
        "api": round(api_cf, 8), "closed_form": round(cf_ref, 8),
        "rel_error": _rel(api_cf, cf_ref),
        "source": "reported cf" if "cf" in live else "derived from R_f/(0.5 rho S V^2)",
    }

    # R_f = 0.5 rho S Cf V^2 with the engine's own S
    checks["wetted_surface_formula"] = {
        "api": round(S, 6),
        "closed_form": round(S_formula, 6),
        "rel_error": _rel(S, S_formula),
    }
    rf_ref = 0.5 * rho * S * api_cf * V ** 2
    checks["frictional_resistance"] = {
        "api": live["frictional_resistance_n"], "closed_form": round(rf_ref, 4),
        "rel_error": _rel(live["frictional_resistance_n"], rf_ref),
    }

    # R_T = R_f + R_r (additive identity)
    checks["total_is_sum"] = {
        "api": live["total_resistance_n"],
        "closed_form": round(live["frictional_resistance_n"] + live["residual_resistance_n"], 4),
        "abs_error": abs(live["total_resistance_n"]
                         - (live["frictional_resistance_n"] + live["residual_resistance_n"])),
    }

    # residual law Cr = 0.002 (1 + 2 Cb) Fn^2.5 (documented in the endpoint)
    api_cr = live.get("cr")
    if api_cr is None:
        api_cr = live["residual_resistance_n"] / (0.5 * rho * S * V ** 2)
    cr_ref = 0.002 * (1 + 2 * Cb) * fn_ref ** 2.5 if fn_ref > 0.05 else 0.0
    checks["residual_law"] = {
        "api": round(api_cr, 8), "closed_form": round(cr_ref, 8),
        "rel_error": _rel(api_cr, cr_ref),
        "source": "reported cr" if "cr" in live else "derived from R_r/(0.5 rho S V^2)",
    }

    return {"case_id": case["case_id"], "request": payload, "checks": checks}


def legacy_payload_probe(api_base: str, case: dict) -> dict:
    """Show the old ``speed`` payload key being silently ignored (default 1.0 m/s)."""
    geom = case["geometry"]
    body = {
        "ship_type": case["ship_type"],
        "speed": case["speed_ms"],          # pre-fix key
        "length": geom["length_m"],
        "beam": geom.get("beam_m") or geom.get("diameter_m"),
        "draft": geom.get("draft_m") or geom.get("diameter_m"),
        "cb": geom.get("cb") or 0.542,
    }
    live = post_json(f"{api_base}/calculations/resistance", body)
    return {
        "requested_speed_ms": case["speed_ms"],
        "engine_speed_ms": live["speed_ms"],
        "total_resistance_n": live["total_resistance_n"],
        "silently_wrong": abs(live["speed_ms"] - case["speed_ms"]) > 1e-9,
    }


def make_figure(audit: dict, engine: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cases = audit["cases"]
    ids = [c["case_id"] for c in cases]
    ratios = [c.get("implied_over_envelope") or float("nan") for c in cases]

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.6))

    ax = axes[0]
    ax.bar(range(len(ids)), ratios, color=["#c0392b" if (r == r and r < 0.4) else "#27ae60"
                                           for r in ratios])
    ax.axhspan(0.4, 2.0, color="#95a5a6", alpha=0.25, label="plausible band [0.4, 2.0]")
    ax.set_xticks(range(len(ids)))
    ax.set_xticklabels(ids, rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("implied wetted area / block envelope")
    ax.set_title("(a) C_T-R_T implied area vs geometry")
    ax.legend(fontsize=7)

    ax = axes[1]
    err = [abs(c.get("froude_rel_error") or 0.0) * 100 for c in cases]
    ax.bar(range(len(ids)), err, color=["#c0392b" if e > 5 else "#27ae60" for e in err])
    ax.axhline(5.0, color="k", ls="--", lw=0.8, label="5% tolerance")
    ax.set_xticks(range(len(ids)))
    ax.set_xticklabels(ids, rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("|declared - V/sqrt(gL)| / Fn  [%]")
    ax.set_title("(b) Froude column vs definition")
    ax.legend(fontsize=7)

    ax = axes[2]
    names = list(engine["checks"].keys())
    rels = []
    for n in names:
        c = engine["checks"][n]
        rels.append(max(c.get("rel_error", 0.0) * 100, 1e-10))
    ax.barh(range(len(names)), rels, color="#2980b9")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=7)
    ax.set_xscale("log")
    ax.set_xlabel("relative error vs closed form [%], log")
    ax.set_title(f"(c) engine ledger, {engine['case_id']}")

    fig.suptitle("m1406 benchmark reference integrity + resistance engine ledger", fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8010/api/v1")
    args = ap.parse_args()

    dataset = json.loads(DATASET.read_text())
    audit = audit_dataset(dataset)

    engine = None
    legacy = None
    probe_case = next(c for c in dataset["cases"] if c["case_id"] == "SUBOFF-01")
    try:
        engine = engine_ledger(args.api, probe_case)
        legacy = legacy_payload_probe(args.api, probe_case)
    except (urllib.error.URLError, OSError) as exc:  # engine offline is not fatal
        engine = {"error": f"engine unreachable: {exc}"}
        legacy = {"error": str(exc)}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger = {"dataset_audit": audit, "engine_ledger": engine, "legacy_payload_probe": legacy}
    (OUT_DIR / "benchmark_audit.json").write_text(json.dumps(ledger, indent=2, ensure_ascii=False))

    if engine and "checks" in engine:
        make_figure(audit, engine, FIG_DIR / "benchmark_audit.png")
        print("figure ->", FIG_DIR / "benchmark_audit.png")

    print(json.dumps(audit["summary"], ensure_ascii=False, indent=1))
    if engine and "checks" in engine:
        for name, c in engine["checks"].items():
            key = "rel_error" if "rel_error" in c else "abs_error"
            print(f"  {name:26s} {key}={c[key]:.3e}")
    if legacy and legacy.get("silently_wrong"):
        print(f"  legacy 'speed' key -> engine ran at {legacy['engine_speed_ms']} m/s "
              f"(requested {legacy['requested_speed_ms']})")
    print("ledger ->", OUT_DIR / "benchmark_audit.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())