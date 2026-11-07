"""Cross-ISA reproducibility matrix: run H1/H2/H5 over machines x flags x reps.

Three modes
-----------
``ref``     (control plane) run the 3 kernels at the two reference variants
            (``base`` = ``-O2``, ``canon`` = strict IEEE, no FMA/no vectoriser),
            3 repetitions each, and write the *values* bundle that other
            machines compare against.
``node``    (any machine) run the full flags matrix with 3 repetitions, compare
            every run against the reference bundle (bit digest vs quantized
            digest vs ULP), and write a compact summary JSON.
``assemble`` (control plane) merge the machine summaries into
            ``artifacts/cross_isa_repro.json`` and derive the gate ``k``.

Everything is stdlib-only so that the identical script runs on the loong64
cluster nodes (no numpy there).

Protocol in one line: ``quant_digest(ref) == quant_digest(cur)`` **and**
``max_ulp(ref, cur) <= k``; the naive gate (``bit_digest`` equality) is
measured alongside so its failure rate is evidence, not opinion.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import emit_digest as ed
from . import spec as _spec
from . import ulp

BASE_FLAGS = "-O2"
CANON_FLAGS = "-O0 -ffp-contract=off -fno-tree-vectorize"

#: flags matrix.  Roles are ISA-independent; the two ``march`` slots are filled
#: from :func:`emit_digest.march_flags` so the same matrix runs on x86/aarch64.
FLAG_MATRIX: List[Dict[str, str]] = [
    {"variant": "O0", "flags": "-O0"},
    {"variant": "O1", "flags": "-O1"},
    {"variant": "O2", "flags": "-O2"},
    {"variant": "O3", "flags": "-O3"},
    {"variant": "O2_nofma", "flags": "-O2 -ffp-contract=off"},
    {"variant": "O3_nofma", "flags": "-O3 -ffp-contract=off"},
    {"variant": "O3_fmafast", "flags": "-O3 -ffp-contract=fast"},
    {"variant": "O3_novec", "flags": "-O3 -fno-tree-vectorize"},
    {"variant": "O3_novec_nofma", "flags": "-O3 -fno-tree-vectorize -ffp-contract=off"},
    {"variant": "MARCH_OLD", "flags": "-O3"},   # march flag appended at runtime
    {"variant": "MARCH_NEW", "flags": "-O3"},
    {"variant": "O3_fastmath", "flags": "-O3 -ffast-math"},
    {"variant": "O2_libm_alt", "flags": "-O2 -DREPRO_LIBM_ALT=1"},
]


def flag_matrix() -> List[Dict[str, str]]:
    """Resolve the ISA-specific ``march`` slots for the host."""
    mar = ed.march_flags()
    out = []
    for entry in FLAG_MATRIX:
        e = dict(entry)
        if entry["variant"] == "MARCH_OLD":
            if len(mar) < 1:
                continue
            e["flags"] = "%s %s" % (entry["flags"], mar[0])
        elif entry["variant"] == "MARCH_NEW":
            if len(mar) < 2:
                continue
            e["flags"] = "%s %s" % (entry["flags"], mar[1])
        out.append(e)
    return out


def machine_label() -> str:
    return "%s-%s" % (platform.node(), platform.machine())


def machine_meta(label: Optional[str] = None) -> Dict[str, str]:
    return {
        "machine": label or machine_label(),
        "arch": platform.machine(),
        "hostname": platform.node(),
        "cc": ed.cc_version(),
        "libc": ed.libc_version(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "cpu": _cpu_model(),
    }


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            low = line.lower()
            if low.startswith("model name") or low.startswith("cpu model"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "unknown"


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #
def run_kernel_record(bench: str, flags: str, variant: str, workdir: Path,
                      keep_values: bool = False) -> Dict:
    """Run one (bench, flags) once; returns the raw hex record (optionally saved)."""
    vout = (Path(workdir) / "values" / ("%s_%s.json" % (bench, variant))) if keep_values else None
    return ed.run_kernel(bench, flags, value_out=vout, variant=variant,
                         bindir=Path(workdir) / "bin")


def repeat_and_check(bench: str, flags: str, variant: str, workdir: Path,
                     reps: int = 3) -> Dict:
    """Run ``reps`` times on *this* machine: determinism floor + drift profile."""
    from . import ulp as U

    recs = [run_kernel_record(bench, flags, variant, workdir) for _ in range(reps)]
    norm = [U.load_record(r) for r in recs]
    bit_eq = all(U.digest_record(n)["record_digest"]["bit"] ==
                 U.digest_record(norm[0])["record_digest"]["bit"] for n in norm[1:])
    quant_eq = all(U.digest_record(n)["record_digest"]["quant_observables"] ==
                   U.digest_record(norm[0])["record_digest"]["quant_observables"]
                   for n in norm[1:])
    max_ulp = 0
    for n in norm[1:]:
        rep = U.compare_records(norm[0], n)
        max_ulp = max(max_ulp, rep["max_ulp"])
    return {
        "reps": reps,
        "bit_digest_equal_rate": 1.0 if bit_eq else 0.0,
        "quant_digest_equal_rate": 1.0 if quant_eq else 0.0,
        "max_ulp_across_reps": max_ulp,
        "record_bit_digest": U.digest_record(norm[0])["record_digest"]["bit"],
        "record_quant_digest": U.digest_record(norm[0])["record_digest"]["quant"],
        "_record": recs[0],
    }


def compare_to_ref(bench: str, cand_raw: Dict, ref_raw: Dict) -> Dict:
    """Full comparison summary of one candidate vs one reference record."""
    from . import ulp as U

    cand = U.load_record(cand_raw)
    ref = U.load_record(ref_raw)
    rep = U.compare_records(ref, cand)
    sweep = {}
    for d in (12, 11, 10, 9, 8):
        r_d = U.compare_records(ref, cand, digits=d)
        sweep[str(d)] = {"obs_quant_equal": r_d["obs_quant_equal"],
                         "n_obs_mismatch": len(r_d["obs_names_quant_mismatch"]),
                         "max_ulp": r_d["max_ulp"]}
    per_field = {k: {"class": v["class"], "max_ulp": v["max_ulp"],
                     "quant_equal": v["quant_equal"], "bit_equal": v["bit_equal"],
                     "max_rel_diff": v["max_rel_diff"],
                     "max_abs_diff": v["max_abs_diff"],
                     "diag_ok": v.get("diag_ok"),
                     "argmax_index": v["argmax_index"]}
                 for k, v in list(rep["fields"].items()) + list(rep["scalars"].items())}
    return {
        "bit_equal": rep["bit_digest_equal"],
        "quant_equal": rep["obs_quant_equal"],
        "quant_equal_all": rep["quant_digest_equal"],
        "diag_ok": rep["diag_ok"],
        "max_ulp": rep["max_ulp"],
        "max_rel_diff": max([v["max_rel_diff"] for v in per_field.values()] or [0.0]),
        "fields_bit_equal_rate": rep["fields_bit_equal_rate"],
        "fields_quant_equal_rate": rep["fields_quant_equal_rate"],
        "first_divergence_step": rep["first_divergence_step"],
        "digits": rep["digits"],
        "digit_sweep": sweep,
        "per_field": per_field,
    }


def negative_control(bench: str, ref_raw: Dict, rel: float = 1e-9) -> Dict:
    """Inject ``rel`` relative perturbation; the protocol must reject it."""
    from . import ulp as U

    ref = U.load_record(ref_raw)
    out = []
    for which, name in (("scalar", None), ("field", None)):
        pert = U.inject_relative_perturbation(ref, rel=rel, which=which, name=name)
        try:
            U.assert_reproducible(ref, pert, ulp_gate=1)
            rejected, reason = False, "NOT rejected (protocol hole)"
        except U.ReproViolation as exc:
            rejected, reason = True, str(exc)[:220]
        rep = U.compare_records(ref, pert)
        out.append({"which": which, "rel": rel, "rejected": rejected,
                    "max_ulp": rep["max_ulp"], "quant_equal": rep["obs_quant_equal"],
                    "diag_ok": rep["diag_ok"], "reason": reason})
    return {"bench": bench, "rel": rel, "trials": out,
            "all_rejected": all(t["rejected"] for t in out)}


# --------------------------------------------------------------------------- #
# mode: ref  (control plane builds the reference bundle)
# --------------------------------------------------------------------------- #
def mode_ref(outdir: Path, reps: int = 3, label: Optional[str] = None) -> Dict:
    outdir = Path(outdir)
    (outdir / "values").mkdir(parents=True, exist_ok=True)
    bundle: Dict[str, Dict] = {"machine": machine_meta(label), "flags": {"base": BASE_FLAGS,
                                                                   "canon": CANON_FLAGS},
                               "benches": {}}
    t0 = time.time()
    for bench in ed.BENCH_ORDER:
        entry = {"bench": bench, "variants": {}}
        for variant, flags in (("base", BASE_FLAGS), ("canon", CANON_FLAGS)):
            info = repeat_and_check(bench, flags, variant, outdir, reps=reps)
            rec = info.pop("_record")
            (outdir / "values" / ("%s_%s.json" % (bench, variant))).write_text(
                json.dumps(rec, indent=1))
            entry["variants"][variant] = info
        bundle["benches"][bench] = entry
        print("[ref] %s done (%.1fs)" % (bench, time.time() - t0), file=sys.stderr)
    bundle["wall_s"] = round(time.time() - t0, 3)
    (outdir / "ref_bundle.json").write_text(json.dumps(bundle, indent=2))
    # the values of the two reference variants travel with the bundle
    return bundle


# --------------------------------------------------------------------------- #
# mode: node  (full flags matrix on this machine, compared to the bundle)
# --------------------------------------------------------------------------- #
def mode_node(refdir: Path, outdir: Path, reps: int = 3,
              label: Optional[str] = None) -> Dict:
    refdir = Path(refdir)
    outdir = Path(outdir)
    (outdir / "values").mkdir(parents=True, exist_ok=True)
    refs: Dict[str, Dict[str, Dict]] = {}
    for bench in ed.BENCH_ORDER:
        refs[bench] = {}
        for variant in ("base", "canon"):
            p = refdir / "values" / ("%s_%s.json" % (bench, variant))
            refs[bench][variant] = json.loads(p.read_text())

    summary: Dict = {
        "machine": machine_meta(label),
        "flags": {"base": BASE_FLAGS, "canon": CANON_FLAGS},
        "reps": reps,
        "kernels": [],
        "negctl": [],
        "errors": [],
    }
    t0 = time.time()
    for bench in ed.BENCH_ORDER:
        for entry in flag_matrix():
            variant, flags = entry["variant"], entry["flags"]
            try:
                t1 = time.time()
                info = repeat_and_check(bench, flags, variant, outdir, reps=reps)
                rec = info.pop("_record")
                row = {"bench": bench, "variant": variant, "flags": flags,
                       "wall_s": round(time.time() - t1, 3), **info}
                for refname in ("base", "canon"):
                    row["vs_" + refname] = compare_to_ref(bench, rec, refs[bench][refname])
                summary["kernels"].append(row)
                if variant == "O2":  # keep this machine's own base values
                    (outdir / "values" / ("%s_base.json" % bench)).write_text(
                        json.dumps(rec, indent=1))
                print("[node] %s/%s ok (%.1fs)" % (bench, variant, time.time() - t1),
                      file=sys.stderr)
            except Exception as exc:  # a variant that refuses to build/run is data too
                summary["errors"].append({"bench": bench, "variant": variant,
                                          "flags": flags,
                                          "error": "%s: %s" % (type(exc).__name__, exc)})
                print("[node] %s/%s FAILED: %s" % (bench, variant, exc), file=sys.stderr)
                print(traceback.format_exc()[-600:], file=sys.stderr)
        nc = negative_control(bench, refs[bench]["base"])
        summary["negctl"].append(nc)
    summary["wall_s"] = round(time.time() - t0, 3)
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


# --------------------------------------------------------------------------- #
# mode: assemble
# --------------------------------------------------------------------------- #
def _load(path: Path) -> Dict:
    return json.loads(Path(path).read_text())


def assemble(ref_bundle_path: Path, summaries: Sequence[Path], out_path: Path,
             gate_margin: float = 4.0, gate_min: int = 64) -> Dict:
    """Merge machine summaries, derive k, and write the artefact JSON."""
    bundle = _load(ref_bundle_path)
    mach = [_load(p) for p in summaries]

    # ---- sweep A: determinism floor (same machine, 3 reps) ----------------
    floor = []
    for m in mach:
        for row in m["kernels"]:
            floor.append({"machine": m["machine"]["machine"], "bench": row["bench"],
                          "variant": row["variant"],
                          "bit_equal_rate": row["bit_digest_equal_rate"],
                          "quant_equal_rate": row["quant_digest_equal_rate"],
                          "max_ulp": row["max_ulp_across_reps"]})
    floor_bit = [f["bit_equal_rate"] for f in floor]
    floor_max_ulp = max([f["max_ulp"] for f in floor] or [0])

    # ---- sweep B: cross-machine + cross-flags ------------------------------
    accepted = [r for r in row_records(mach) if not r["_fastmath"]]
    rejected_by_scope = [r for r in row_records(mach) if r["_fastmath"]]
    cross_isa = [r for r in accepted if r["variant"] == "O2"]
    measured_k = max([r["vs_base_max_ulp"] for r in accepted] or [0])
    k = _gate(gate_margin * measured_k, gate_min)
    k_by_bench = {}
    for bench in ed.BENCH_ORDER:
        mk = max([r["vs_base_max_ulp"] for r in accepted if r["bench"] == bench] or [0])
        k_by_bench[bench] = _gate(gate_margin * mk, gate_min)

    benches_out: Dict[str, Dict] = {}
    first_machine = (mach[0]["machine"]["machine"] if mach else "")
    for bench in ed.BENCH_ORDER:
        rows = [r for r in row_records(mach) if r["bench"] == bench]
        benches_out[bench] = {
            "table": [
                {
                    "machine": r["machine"], "arch": r["arch"],
                    "variant": r["variant"], "flags": r["flags"],
                    "same_machine_bit_equal_rate": r["same_bit"],
                    "same_machine_quant_equal_rate": r["same_quant"],
                    "same_machine_max_ulp": r["same_max_ulp"],
                    "vs_base_bit_equal": r["vs_base_bit"],
                    "vs_base_quant_equal": r["vs_base_quant"],
                    "vs_base_max_ulp": r["vs_base_max_ulp"],
                    "vs_base_max_rel_diff": r["vs_base_rel"],
                    "vs_base_first_divergence_step": r["vs_base_first_div"],
                    "digit_sweep": r["vs_base_sweep"],
                    "vs_canon_max_ulp": r["vs_canon_max_ulp"],
                }
                for r in rows
            ],
            "flags_sensitivity_max_ulp": {r["variant"]: r["vs_base_max_ulp"] for r in rows
                                          if r["machine"] == first_machine},
            "fields": rows[0]["vs_base_per_field"] if rows else {},
        }

    # ---- attribution ------------------------------------------------------
    attribution = {}
    for bench in ed.BENCH_ORDER:
        rows = [r for r in row_records(mach) if r["bench"] == bench]
        get = lambda v: next((r["vs_base_max_ulp"] for r in rows if r["variant"] == v), None)
        # drift *relative to the canonical (no-FMA, no-vector) reference* so that
        # switching FMA off must move the result *towards* the reference
        canon_of = lambda v: next((r["vs_canon_max_ulp"] for r in rows if r["variant"] == v), None)
        fma_on = canon_of("O3_fmafast")
        fma_off = canon_of("O3_nofma")
        vec_on = canon_of("O3")
        vec_off = canon_of("O3_novec")
        libm_alt = canon_of("O2_libm_alt")
        base_canon = canon_of("O2")
        attribution[bench] = {
            "drift_vs_canon_ulp": {"O3_fmafast": fma_on, "O3_nofma": fma_off,
                                   "O3": vec_on, "O3_novec": vec_off,
                                   "O2": base_canon, "O2_libm_alt": libm_alt},
            "fma_ratio_with_over_without": _ratio(fma_on, fma_off),
            "vec_ratio_with_over_without": _ratio(vec_on, vec_off),
            "libm_alt_over_base": _ratio(libm_alt, base_canon),
            "first_divergence_steps": {
                r["variant"]: r["vs_base_first_div"] for r in rows},
        }

    # ---- naive gate vs protocol gate --------------------------------------
    naive_fail = [r for r in accepted if not r["vs_base_bit"]]
    protocol_fail = [r for r in accepted
                     if not r["vs_base_quant"] or r["vs_base_max_ulp"] > k
                     or not r["vs_base_diag_ok"]]
    counterexample = {
        "naive_bit_gate": {
            "scope": "cross-variant / cross-machine runs inside the accepted envelope",
            "n": len(accepted),
            "n_failed": len(naive_fail),
            "failure_rate": round(len(naive_fail) / len(accepted), 4) if accepted else None,
            "examples": [{"machine": r["machine"], "bench": r["bench"], "variant": r["variant"],
                          "flags": r["flags"], "max_ulp": r["vs_base_max_ulp"]}
                         for r in naive_fail[:6]],
        },
        "protocol_gate": {
            "digits": ulp.DEFAULT_DIGITS, "k": k, "n": len(accepted),
            "n_failed": len(protocol_fail),
            "failure_rate": round(len(protocol_fail) / len(accepted), 4) if accepted else None,
        },
        "fastmath_out_of_envelope": [
            {"machine": r["machine"], "bench": r["bench"], "max_ulp": r["vs_base_max_ulp"],
             "quant_equal": r["vs_base_quant"],
             "first_divergence_step": r["vs_base_first_div"]}
            for r in rejected_by_scope],
    }

    negctl = {"trials": [t for m in mach for nc in m.get("negctl", []) for t in nc["trials"]],
              "n": 0, "n_rejected": 0}
    negctl["n"] = len(negctl["trials"])
    negctl["n_rejected"] = sum(1 for t in negctl["trials"] if t["rejected"])
    negctl["all_rejected"] = negctl["n"] == negctl["n_rejected"] and negctl["n"] > 0

    out = {
        "task": "IN22-N08 cross-ISA floating point reproducibility metering",
        "protocol": {
            "quantization": {
                "digits": ulp.DEFAULT_DIGITS,
                "digits_by_bench": {(("h1_channel60", "h2_powerlaw", "h5_calibration")[i]): _spec.digits_for(
                    ("h1_channel60", "h2_powerlaw", "h5_calibration")[i]) for i in range(3)},
                "method": "format(x, '.{digits}g') canonical decimal string",
                "rule": "grid must sit >= 100x above the artefact's measured noise floor",
            },
            "gauge_classes": {
                "observable": "canonical digest equality AND max_ulp <= k",
                "diagnostic": "absolute tolerance (cancelling quantities have no ULP scale)",
            },
            "ulp_gate_k": k,
            "ulp_gate_k_by_bench": k_by_bench,
            "k_derivation": {"measured_max_ulp_in_envelope": measured_k,
                             "margin": gate_margin, "floor": gate_min,
                             "rounding": "next power of two",
                             "quant_digits_by_bench": {b: _spec.digits_for(
                                 ("h1_channel60", "h2_powerlaw", "h5_calibration")[i])
                                 for i, b in enumerate(ed.BENCH_ORDER)}},
            "acceptance": "quant_digest_eq AND max_ulp <= k",
            "envelope_excludes": ["-ffast-math"],
        },
        "machines": [m["machine"] for m in mach],
        "reference": {"machine": bundle["machine"], "flags": bundle["flags"]},
        "determinism_floor": {
            "reps": max((m["reps"] for m in mach), default=0),
            "n_runs": len(floor),
            "bit_equal_rate_min": min(floor_bit) if floor_bit else None,
            "quant_equal_rate_min": min(f["quant_equal_rate"] for f in floor) if floor else None,
            "max_ulp": floor_max_ulp,
            "rows": floor,
        },
        "cross_machine_base_table": [
            {"machine": r["machine"], "arch": r["arch"], "bench": r["bench"],
             "flags": r["flags"], "bit_equal": r["vs_base_bit"],
             "quant_equal": r["vs_base_quant"], "max_ulp": r["vs_base_max_ulp"],
             "max_rel_diff": r["vs_base_rel"]}
            for r in cross_isa],
        "benches": benches_out,
        "attribution": attribution,
        "gate_evidence": counterexample,
        "negative_control": negctl,
        "errors": [e for m in mach for e in m.get("errors", [])],
        "limitations": [
            "All measured machines are loongarch64 (外协方 n315/n330 + 99 control plane): "
            "no x86_64 host, qemu-user, docker or cross-toolchain exists in this ecosystem, "
            "so the literal x86 leg of the acceptance table is NOT EXECUTED (see docs).",
            "The libm axis is a controlled proxy (long-double entry points), not a second libc build.",
            "Kernels use fixed iteration/step counts only; tolerance-driven loops are excluded "
            "by design because their iteration-count divergence is a different failure class.",
        ],
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(out, indent=2, sort_keys=False))
    return out


def _ratio(on, off):
    """Drift(on)/Drift(off) relative to the canonical reference.

    ``"inf"`` when switching the mechanism off removes the drift entirely,
    ``"not_significant"`` when both sides are zero, ``None`` when unmeasured.
    """
    if on is None or off is None:
        return None
    if off == 0:
        return "inf" if on > 0 else "not_significant"
    return on / off


def _gate(value: float, floor: int) -> int:
    """Conservative gate: next power of two above ``value`` (at least ``floor``)."""
    v = max(1, int(math.ceil(value)))
    p = 1
    while p < v:
        p <<= 1
    return max(floor, p)


def row_records(mach: Sequence[Dict]) -> List[Dict]:
    """Flatten machine summaries into comparable rows."""
    rows = []
    for m in mach:
        meta = m["machine"]
        for k in m["kernels"]:
            vb = k.get("vs_base") or {}
            vc = k.get("vs_canon") or {}
            rows.append({
                "machine": meta["machine"], "arch": meta["arch"], "cc": meta["cc"],
                "libc": meta["libc"], "bench": k["bench"], "variant": k["variant"],
                "flags": k["flags"],
                "same_bit": k["bit_digest_equal_rate"],
                "same_quant": k["quant_digest_equal_rate"],
                "same_max_ulp": k["max_ulp_across_reps"],
                "vs_base_bit": vb.get("bit_equal"), "vs_base_quant": vb.get("quant_equal"),
                "vs_base_quant_all": vb.get("quant_equal_all"),
                "vs_base_diag_ok": vb.get("diag_ok"),
                "vs_base_max_ulp": vb.get("max_ulp"),
                "vs_base_rel": vb.get("max_rel_diff"),
                "vs_base_first_div": vb.get("first_divergence_step"),
                "vs_base_sweep": vb.get("digit_sweep"),
                "vs_base_per_field": vb.get("per_field"),
                "vs_canon_max_ulp": vc.get("max_ulp"),
                "_fastmath": "ffast-math" in k["flags"],
            })
    return rows


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    p = sub.add_parser("ref")
    p.add_argument("--outdir", default="artifacts/repro_digest/ref")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--label", default=None, help="machine label, e.g. ctl99")

    p = sub.add_parser("node")
    p.add_argument("--refdir", default="artifacts/repro_digest/ref")
    p.add_argument("--outdir", default="artifacts/repro_digest/node")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--label", default=None, help="machine label, e.g. n315")

    p = sub.add_parser("assemble")
    p.add_argument("--ref-bundle", default="artifacts/repro_digest/ref/ref_bundle.json")
    p.add_argument("--summaries", nargs="+", required=True)
    p.add_argument("--out", default="artifacts/cross_isa_repro.json")
    p.add_argument("--gate-margin", type=float, default=4.0)
    p.add_argument("--gate-min", type=int, default=64)

    args = ap.parse_args(argv)
    if args.mode == "ref":
        mode_ref(Path(args.outdir), reps=args.reps, label=args.label)
    elif args.mode == "node":
        mode_node(Path(args.refdir), Path(args.outdir), reps=args.reps, label=args.label)
    else:
        out = assemble(Path(args.ref_bundle), [Path(s) for s in args.summaries],
                       Path(args.out), gate_margin=args.gate_margin, gate_min=args.gate_min)
        print(json.dumps({"k": out["protocol"]["ulp_gate_k"],
                          "naive_fail_rate": out["gate_evidence"]["naive_bit_gate"]["failure_rate"],
                          "floor_bit": out["determinism_floor"]["bit_equal_rate_min"],
                          "negctl_ok": out["negative_control"]["all_rejected"]}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())