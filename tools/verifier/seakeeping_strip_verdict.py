#!/usr/bin/env python3
"""Seakeeping "strip theory" capability verdict: MOCK fingerprint + exact-limit adjudication.

Why this module exists
----------------------
Report 1431 froze endpoint verdicts for the manoeuvring and BEM/RAO subsystems.  The
seakeeping family was left WITHHELD because the in-repo reference kernel
(``tools/verifier/seakeeping_reference.py``) only carries exact limits + SDOF sanity
bounds; it is explicitly *not* a strip-theory solver.  This module closes the part of
that debt that can be closed **without** a third-party digitised dataset:

  1. MOCK fingerprinting -- physical inputs (wave frequency, speed, heading, main
     particulars) are perturbed and the responses are digested.  A capability that is
     byte-identical across physically meaningful input changes is not computing
     anything; it is a table lookup.
  2. Exact-limit adjudication -- linear seakeeping must honour two exact limits
     (already proved in ``seakeeping_reference.py``):
         lambda/L -> 0  : |RAO_3| -> 0   (short waves are filtered out by the hull)
         lambda/L -> inf: |RAO_3| -> 1   (the ship follows the wave surface)
     together with non-negativity, single-peak structure and post-peak decay.
  3. Axis integrity -- a returned RAO series without its frequency axis cannot be
     judged against any limit, so its absence is itself a finding.

What this module deliberately does NOT do (honest scope statement)
------------------------------------------------------------------
It does not attempt to judge detail accuracy (< 3 %) of a *real* RAO curve: that needs
a strip-theory/BEM reference or digitised model-test data, neither of which exists in
this repository.  Anything beyond the two exact limits is reported WITHHELD.

Usage
-----
    python3 tools/verifier/seakeeping_strip_verdict.py                 # live probe
    python3 tools/verifier/seakeeping_strip_verdict.py --offline       # from fixture
    python3 tools/verifier/seakeeping_strip_verdict.py --fig           # + figure

Artifacts: artifacts/seakeeping_strip_verdicts.json (+ fixture, + figure).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request

import numpy as np

BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ART = os.path.join(ROOT, "artifacts")
FIXTURE = os.path.join(ART, "seakeeping_strip_responses.json")
VERDICTS = os.path.join(ART, "seakeeping_strip_verdicts.json")

# KVLCC2-class parent hull (geometry that the platform's own canon dataset carries).
HULL = dict(L=320.0, B=58.0, T=20.8, D=30.0, displacement=312622.0, Cb=0.8098,
            Cm=0.998, Cwp=0.87, KG=18.0, GM=1.5, speed=7.7, wave_frequency=0.5,
            wave_amplitude=1.0, heading=180.0)

ENDPOINTS = ("seakeeping-advanced/strip-theory", "seakeeping/rao")

# Perturbations: each entry must change the physics, hence the answer.
PERTURBATIONS = (
    ("wave_frequency=0.3", {"wave_frequency": 0.3}),
    ("wave_frequency=0.5", {"wave_frequency": 0.5}),
    ("wave_frequency=0.8", {"wave_frequency": 0.8}),
    ("wave_frequency=1.2", {"wave_frequency": 1.2}),
    ("speed=14.0", {"speed": 14.0}),
    ("heading=120", {"heading": 120.0}),
    ("L=180 (half size)", {"L": 180.0, "B": 32.0, "T": 12.0, "D": 18.0,
                           "displacement": 55000.0, "Cb": 0.82, "GM": 0.9,
                           "speed": 6.0}),
)


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------
def _post(path: str, body: dict, timeout: float = 120.0) -> tuple[int, dict]:
    url = f"{BASE.rstrip('/')}/{path}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read())
        except Exception:
            payload = {"detail": "unparsable error body"}
        return e.code, payload


def _digest(payload: dict) -> str:
    """Digest of the physical content only (keys sorted, floats rounded to 1e-9)."""
    def norm(o):
        if isinstance(o, float):
            return round(o, 9)
        if isinstance(o, list):
            return [norm(v) for v in o]
        if isinstance(o, dict):
            return {k: norm(v) for k, v in sorted(o.items())}
        return o
    return hashlib.sha256(json.dumps(norm(payload), sort_keys=True).encode()).hexdigest()[:16]




def series_digests(cases: list[dict]) -> dict[str, dict]:
    """Per-field digest over a case subset: which physical series are input-invariant?"""
    out: dict[str, dict] = {}
    ok = [c for c in cases if c["status"] == 200]
    if not ok:
        return out
    names = [k for k, v in ok[0]["payload"].items()
             if isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v)]
    for name in names:
        if not all(isinstance(c["payload"].get(name), list) and
                   len(c["payload"][name]) == len(ok[0]["payload"][name]) for c in ok):
            continue
        ds = sorted({_digest(c["payload"][name]) for c in ok})
        out[name] = {"n_distinct": len(ds), "digests": ds,
                     "invariant": len(ds) == 1, "n_points": len(ok[0]["payload"][name])}
    return out


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------
def capture() -> dict:
    rec = {"base": BASE, "hull": HULL, "endpoints": {}}
    for ep in ENDPOINTS:
        cases = []
        for label, delta in PERTURBATIONS:
            body = dict(HULL)
            body.update(delta)
            status, payload = _post(ep, body)
            cases.append({"label": label, "request": body, "status": status,
                          "payload": payload, "_digest": _digest(payload) if status == 200 else None})
        rec["endpoints"][ep] = cases
    return rec


# --------------------------------------------------------------------------
# adjudication
# --------------------------------------------------------------------------
def _series(payload: dict, name: str) -> list[float] | None:
    for key in (name, name + "_rao", name.replace("heave", "heave_motion")):
        v = payload.get(key)
        if isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v):
            return [float(x) for x in v]
    return None


def _grid(payload: dict, n: int) -> list[float] | None:
    """Frequency axis carried by the payload, else the platform's documented grid."""
    for key in ("frequencies", "omega", "wave_frequencies", "frequency"):
        v = payload.get(key)
        if isinstance(v, list) and len(v) == n:
            return [float(x) for x in v]
    if n == 30:                      # /seakeeping/rao documented sweep 0.2..3.1 rad/s
        return [round(0.2 + 0.1 * i, 3) for i in range(30)]
    return None


def adjudicate(rec: dict) -> dict:
    out = {"base": rec.get("base"), "hull": rec.get("hull"), "endpoints": {}}
    for ep, cases in rec["endpoints"].items():
        ok = [c for c in cases if c["status"] == 200]
        digests = sorted({c["_digest"] for c in ok if c["_digest"]})
        findings: list[dict] = []
        if len(ok) < len(cases):
            findings.append({"code": "HTTP_ERROR", "detail":
                             f"{len(cases) - len(ok)}/{len(cases)} perturbations non-200"})
        # ---- 1. MOCK fingerprint -------------------------------------------
        freq_cases = [c for c in cases if c["label"].startswith("wave_frequency=")]
        sdig = series_digests(freq_cases)
        invariance = 1.0 if ok and len(digests) == 1 else 0.0
        if ok and len(digests) == 1:
            findings.append({"code": "MOCK_HARDCODED", "detail":
                             f"identical response digest {digests[0]} for all "
                             f"{len(ok)} physically distinct inputs"})
        else:
            findings.append({"code": "INPUT_SENSITIVE", "detail":
                             f"{len(digests)} distinct whole-payload digests over "
                             f"{len(ok)} inputs (scalar fields respond)"})
        locked = {k: v for k, v in sdig.items() if v["invariant"]}
        rao_locked = {k: v for k, v in locked.items()
                      if any(t in k for t in ("rao", "heave", "pitch", "roll"))}
        if rao_locked:
            findings.append({"code": "MOCK_HARDCODED", "detail":
                             "RAO series byte-identical across wave_frequency "
                             f"{[c['label'].split('=')[1] for c in freq_cases]}: "
                             + ", ".join(f"{k} ({v['n_points']} pts, digest {v['digests'][0]})"
                                         for k, v in rao_locked.items())})

        # ---- 2. exact limits on the heave RAO ------------------------------
        c0 = ok[0] if ok else None
        heave = _series(c0["payload"], "heave") if c0 else None
        limits = {}
        if heave:
            arr = np.asarray(heave, dtype=float)
            grid = _grid(c0["payload"], len(arr))
            peaks = int(np.sum((arr[1:-1] > arr[:-2]) & (arr[1:-1] >= arr[2:])))
            head, tail = float(arr[0]), float(arr[-1])
            limits = {
                "n_points": int(arr.size),
                "axis_present": grid is not None,
                "f_first": None if grid is None else float(grid[0]),
                "f_last": None if grid is None else float(grid[-1]),
                "peak_rao": float(arr.max()),
                "peak_index": int(arr.argmax()),
                "head_rao": head,
                "tail_rao": tail,
                "n_local_peaks": peaks,
                "min_rao": float(arr.min()),
                "std_rao": float(arr.std()),
            }
            if arr.min() < 0.0:
                findings.append({"code": "LIMIT_VIOLATION",
                                 "detail": f"negative RAO ({arr.min():.4f}) has no physical meaning"})
            if grid is None:
                findings.append({"code": "AXIS_MISSING", "detail":
                                 f"{arr.size}-point RAO returned without a frequency axis: "
                                 "lambda/L limits not adjudicable, series uninterpretable"})
            else:
                # exact limits, evaluated with the deeper-water group of points
                # (lowest frequency = longest wave, highest = shortest wave)
                long_err = abs(head - 1.0)
                short_ok = tail <= 0.25
                limits.update({"long_wave_err": long_err, "short_wave_ok": bool(short_ok)})
                if long_err > 0.15:
                    findings.append({"code": "LIMIT_VIOLATION", "detail":
                                     f"lowest-frequency RAO_3={head:.4f} vs exact limit 1.0 "
                                     f"(error {100 * long_err:.1f} % > 15 %)"})
                if not short_ok:
                    findings.append({"code": "LIMIT_VIOLATION", "detail":
                                     f"highest-frequency RAO_3={tail:.4f} vs exact limit 0 "
                                     f"(short-wave filtering absent)"})
            if peaks > 1 and limits["std_rao"] > 1e-6:
                findings.append({"code": "STRUCTURE", "detail":
                                 f"{peaks} local peaks: single-DOF heave resonance expected"})
            if arr.size >= 5 and limits["std_rao"] < 1e-9:
                findings.append({"code": "MOCK_CONSTANT", "detail":
                                 f"RAO constant at {head:.4f} across the whole sweep"})
        pitch = _series(c0["payload"], "pitch") if c0 else None
        if pitch is not None:
            pa = np.asarray(pitch, dtype=float)
            limits["pitch_constant"] = bool(pa.size > 1 and float(pa.std()) < 1e-12)
            if limits["pitch_constant"]:
                findings.append({"code": "MOCK_CONSTANT", "detail":
                                 f"pitch RAO constant at {float(pa[0]):.4f} over {pa.size} points"})

        if heave is not None and pitch is not None and len(heave) == len(pitch) \
                and np.allclose(heave, pitch, rtol=0.0, atol=1e-12):
            findings.append({"code": "STRUCTURE", "detail":
                             "pitch RAO series is numerically identical to heave RAO: "
                             "the two degrees of freedom share one lookup table"})

        # ---- 3. evidence grade --------------------------------------------
        codes = {f["code"] for f in findings}
        if "MOCK_HARDCODED" in codes or "MOCK_CONSTANT" in codes:
            grade = "MOCK"
        elif codes & {"LIMIT_VIOLATION", "STRUCTURE", "AXIS_MISSING"}:
            grade = "WITHHELD"          # computes something, but unphysical output
        elif "INPUT_SENSITIVE" in codes:
            grade = "REAL_UNVERIFIED"
        else:
            grade = "WITHHELD"
        out["endpoints"][ep] = {
            "n_cases": len(cases),
            "n_200": len(ok),
            "digests": digests,
            "input_invariance": invariance,
            "series_invariance": sdig,
            "heave_limits": limits,
            "findings": findings,
            "evidence_grade": grade,
        }
    return out


def figure(rec: dict, verdict: dict, path: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    ax = axes[0]
    cases = [c for c in rec["endpoints"]["seakeeping-advanced/strip-theory"] if c["status"] == 200]
    for c in cases:
        if c["label"].startswith("wave_frequency="):
            hv = _series(c["payload"], "heave")
            ax.plot(range(len(hv)), hv, marker="o", ms=3,
                    label=f"input {c['label'].split('=')[1]} rad/s")
    ax.set_title("A. /seakeeping-advanced/strip-theory\nthree input frequencies, one curve")
    ax.set_xlabel("returned point index")
    ax.set_ylabel("heave RAO [-]")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)

    ax = axes[1]
    c0 = [c for c in rec["endpoints"]["seakeeping/rao"] if c["status"] == 200][0]
    hv = np.asarray(_series(c0["payload"], "heave"), dtype=float)
    grid = _grid(c0["payload"], hv.size)
    ax.plot(grid, hv, marker="o", ms=3, label="platform /seakeeping/rao")
    ax.axhline(1.0, color="k", ls="--", lw=1, label="exact limit: $\\lambda/L\\to\\infty$")
    ax.axhline(0.0, color="k", ls=":", lw=1, label="exact limit: $\\lambda/L\\to 0$")
    ax.set_xlabel("wave frequency $\\omega$ [rad/s]")
    ax.set_ylabel("heave RAO [-]")
    ax.set_title("B. exact-limit adjudication (no damping, no frequency filtering)")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def tables(verdict: dict) -> str:
    """Machine-generated markdown tables; every number is read from the verdict JSON."""
    lines = ["<!-- generated by tools/verifier/seakeeping_strip_verdict.py: do not hand-edit -->",
             "",
             "| endpoint | evidence grade | distinct whole-payload digests / inputs | RAO series locked to input |",
             "|---|---|---|---|"]
    for ep, v in verdict["endpoints"].items():
        locked = [k for k, d in v.get("series_invariance", {}).items()
                  if d["invariant"] and any(t in k for t in ("rao", "heave", "pitch", "roll"))]
        lines.append(f"| `/{ep}` | **{v['evidence_grade']}** | {len(v['digests'])}/{v['n_cases']} "
                     f"| {', '.join(locked) if locked else '--'} |")
    lines += ["",
              "| endpoint | n points | axis present | peak RAO | head RAO (lowest $\\omega$) "
              "| exact limit | rel. error | tail RAO (highest $\\omega$) | exact limit |",
              "|---|---|---|---|---|---|---|---|"]
    for ep, v in verdict["endpoints"].items():
        lm = v["heave_limits"]
        if not lm:
            continue
        head, n = lm["head_rao"], lm["n_points"]
        err = abs(head - 1.0)
        lines.append(f"| `/{ep}` | {n} | {lm['axis_present']} | {lm['peak_rao']:.3f} "
                     f"| {head:.3f} | 1.000 | {100 * err:.1f} % | {lm['tail_rao']:.3f} | 0.000 |")
    lines += ["", "### findings", ""]
    for ep, v in verdict["endpoints"].items():
        lines.append(f"- `/{ep}` = **{v['evidence_grade']}**")
        for f in v["findings"]:
            lines.append(f"    - `{f['code']}`: {f['detail']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="reuse the frozen fixture")
    ap.add_argument("--fig", action="store_true")
    args = ap.parse_args()

    os.makedirs(ART, exist_ok=True)
    if args.offline:
        with open(FIXTURE) as fh:
            rec = json.load(fh)
    else:
        rec = capture()
        with open(FIXTURE, "w") as fh:
            json.dump(rec, fh, indent=1)

    verdict = adjudicate(rec)
    with open(VERDICTS, "w") as fh:
        json.dump(verdict, fh, indent=1)

    for ep, v in verdict["endpoints"].items():
        print(f"\n{ep}: {v['evidence_grade']}  "
              f"({v['n_200']}/{v['n_cases']} ok, {len(v['digests'])} distinct digests)")
        lm = v["heave_limits"]
        if lm:
            print(f"  heave: peak={lm['peak_rao']:.3f} head={lm['head_rao']:.3f} "
                  f"tail={lm['tail_rao']:.3f} peaks={lm['n_local_peaks']} axis={lm['axis_present']}")
        for f in v["findings"]:
            print(f"  [{f['code']}] {f['detail']}")

    if args.fig:
        fig_path = os.path.join(ROOT, "docs", "benchmarks", "figs",
                                "seakeeping_strip_verdict.png")
        os.makedirs(os.path.dirname(fig_path), exist_ok=True)
        print("\nfigure:", figure(rec, verdict, fig_path))
    tpath = os.path.join(ROOT, "docs", "benchmarks", "seakeeping_strip_verdict_tables.md")
    os.makedirs(os.path.dirname(tpath), exist_ok=True)
    with open(tpath, "w") as fh:
        fh.write(tables(verdict))
    print("\nwrote", VERDICTS, "and", tpath)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())