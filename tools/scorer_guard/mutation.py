"""Source-level mutation harness: negative controls for the scorer (IN25-T5).

A red-team report is only credible if the *tests themselves* can fail.  This
module injects small semantic mutations into the guard source (a gate disabled,
a tolerance widened, a sign dropped, a fingerprint pinned) and re-runs the whole
case suite in an isolated copy of the package:

* a **semantic mutant must be killed** -- at least one case output changes;
* a **control mutant** (comment / whitespace / alpha-rename) **must survive** --
  proving the harness kills semantics, not cosmetics.

The result is a mutation score plus a machine-readable list of *negative
examples* (which case caught which mutant, with pristine vs mutant numbers).
"""
from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

PKG_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Mutant:
    mutant_id: str
    path: str
    description: str
    old: str
    new: str
    semantic: bool
    expected_count: int = 1


MUTANTS: Sequence[Mutant] = (
    # ---- scorer arithmetic -------------------------------------------------
    Mutant("M01-band-widened", "score.py", "score band widened 2x (leniency)",
           "    clipped = min(1.0, max(0.0, 1.0 - err_ratio))",
           "    clipped = min(1.0, max(0.0, 1.0 - 0.5 * err_ratio))", True),
    Mutant("M02-sign-dropped", "score.py", "absolute value dropped from relative error",
           "    rel_err = abs(float(value) - a) / scale",
           "    rel_err = (float(value) - a) / scale", True),
    Mutant("M03-scale-inflated", "score.py", "score scale inflated 10x (max score 1000)",
           '"score": SCORE_SCALE * clipped', '"score": SCORE_SCALE * clipped * 10.0', True),
    Mutant("M04-mean-only", "score.py", "aggregate degenerates to mean only (cherry-pick exploit)",
           "    total = MEAN_WEIGHT * mean_s + (1.0 - MEAN_WEIGHT) * worst_s",
           "    total = 1.0 * mean_s + 0.0 * worst_s", True),
    # ---- gates -------------------------------------------------------------
    Mutant("M05-degenerate-gate-off", "score.py", "degenerate constant-output gate disabled",
           "        if abs(spread) / vmax <= CONST_SPREAD_TOL and anchor_sep >= CONST_ANCHOR_SEP:",
           "        if False:", True),
    Mutant("M06-leak-gate-off", "score.py", "anchor-leak gate disabled",
           "    if leak_hits >= LEAK_MIN_HITS:", "    if False:", True),
    Mutant("M07-unit-gate-off", "score.py", "unit-decade gate disabled",
           "    if unit_hits:", "    if False:", True),
    Mutant("M08-envelope-gate-off", "score.py", "envelope gate disabled",
           "                if not (lo <= float(regime[key]) <= hi):", "                if False:", True),
    Mutant("M09-regime-gate-off", "score.py", "regime-mismatch gate disabled",
           "                if ref != 0.0 and abs(float(regime[key]) - ref) / abs(ref) > REGIME_TOL:",
           "                if False:", True),
    Mutant("M10-lock-gate-off", "score.py", "version-lock gate disabled (unlocked scoring allowed)",
           "    if expected_fingerprint is not None and fp != expected_fingerprint:", "    if False:", True),
    Mutant("M11-finite-gate-off", "score.py", "non-finite gate disabled (NaN may score)",
           "        if not _isfinite(value):", "        if False:", True),
    Mutant("M12-missing-gate-off", "score.py", "anchor-coverage gate disabled",
           "    if require_all and missing:", "    if False:", True),
    Mutant("M13-physics-gate-off", "score.py", "physics invariant gate disabled",
           "    if phys_violations:", "    if False:", True),
    Mutant("M14-quarantine-gate-off", "score.py", "quarantined-anchor gate disabled",
           '        if anchor.provenance_status != "VERIFIED" or anchor.value is None:',
           "        if False:", True),
    Mutant("M15-replay-gate-off", "score.py", "replay gate disabled",
           "    if seen_payloads is not None and ph in set(seen_payloads):", "    if False:", True),
    # ---- provenance / lock -------------------------------------------------
    Mutant("M16-fingerprint-pinned", "score.py", "fingerprint ignores the anchor ledger head",
           '    spec["anchor_head"] = anchor_head', '    spec["anchor_head"] = "CONST"', True),
    Mutant("M17-chain-orderless", "anchors.py", "hash chain drops the previous link (order insensitive)",
           "    return sha256_text(prev + canonical_json(dict(rec)))",
           "    return sha256_text(canonical_json(dict(rec)))", True),
    Mutant("M18-ittc-exponent", "physics.py", "ITTC-1957 reference formula altered (lr-2 -> lr-1)",
           "    return 0.075 / (lr - 2.0) ** 2", "    return 0.075 / (lr - 1.0) ** 2", True),
    # ---- controls: must survive -------------------------------------------
    Mutant("C01-comment", "score.py", "comment reworded (control)",
           "# --- gate REPLAY", "# --- gate REPLAY (dedup)", False),
    Mutant("C02-whitespace", "score.py", "extra spaces inside a statement (control)",
           "    scale = abs(a) if a != 0.0 else 1.0", "    scale  =  abs(a) if a != 0.0 else 1.0", False),
    Mutant("C03-rename-local", "score.py", "local variable renamed (control)",
           "err_ratio", "error_ratio", False, 2),
    Mutant("C04-constant-comment", "score.py", "trailing comment on a constant (control)",
           "TINY = 1e-300", "TINY = 1e-300  # smallest positive sentinel", False),
)


def _numeric_close(a: Any, b: Any, tol: float = 1e-9) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a == b:
            return True
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    return False


def outputs_differ(a: Any, b: Any) -> bool:
    """Structural comparison with relative tolerance for numbers."""
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        if set(a) != set(b):
            return True
        return any(outputs_differ(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return True
        return any(outputs_differ(x, y) for x, y in zip(a, b))
    if _numeric_close(a, b):
        return False
    return a != b


def case_outputs(module: Any) -> Dict[str, Any]:
    """Run the suite inside an imported package copy and key it by case id."""
    redteam = importlib.import_module(module.__name__ + ".redteam")
    ctx = redteam.make_ctx()
    res = redteam.run_cases(ctx)
    out: Dict[str, Any] = {}
    for rec in res["cases"]:
        out[rec["case_id"]] = {"output": rec["output"], "passed": rec["passed"]}
    out["__summary__"] = {
        "n_cases": res["n_cases"],
        "n_failed": res["n_failed"],
        "n_attacks": res["n_attacks"],
        "n_attacks_intercepted": res["n_attacks_intercepted"],
    }
    return out


def baseline_outputs() -> Dict[str, Any]:
    """Case outputs of the *pristine* package (the reference the mutants are diffed against)."""
    import tools.scorer_guard as pristine

    importlib.import_module("tools.scorer_guard.redteam")
    return case_outputs(pristine)


def _load_mutant_package(mutant: Mutant, idx: int) -> (Any, Path):
    tmp = Path(tempfile.mkdtemp(prefix="scorer_guard_mut_"))
    name = f"scorer_mut_{idx}"
    dst = tmp / name
    shutil.copytree(PKG_DIR, dst, ignore=shutil.ignore_patterns("__pycache__"))
    target = dst / mutant.path
    src = target.read_text(encoding="utf-8")
    got = src.count(mutant.old)
    if got != mutant.expected_count:
        raise AssertionError(
            f"mutant {mutant.mutant_id}: expected {mutant.expected_count} occurrence(s) of "
            f"{mutant.old!r} in {mutant.path}, found {got}"
        )
    target.write_text(src.replace(mutant.old, mutant.new), encoding="utf-8")
    sys.path.insert(0, str(tmp))
    mod = importlib.import_module(name)
    sys.path.remove(str(tmp))
    return mod, tmp


def run_mutation(baseline: Optional[Mapping[str, Any]] = None,
                 mutants: Sequence[Mutant] = MUTANTS) -> Dict[str, Any]:
    """Execute every mutant and classify KILLED / SURVIVED / ERROR."""
    base = dict(baseline) if baseline is not None else baseline_outputs()
    records: List[Dict[str, Any]] = []
    for idx, mutant in enumerate(mutants):
        rec: Dict[str, Any] = {
            "mutant_id": mutant.mutant_id,
            "path": mutant.path,
            "description": mutant.description,
            "semantic": mutant.semantic,
            "old": mutant.old,
            "new": mutant.new,
        }
        mod = None
        tmp = None
        try:
            mod, tmp = _load_mutant_package(mutant, idx)
            out = case_outputs(mod)
            diffs = [cid for cid in base if cid != "__summary__"
                     and outputs_differ(base[cid], out.get(cid))]
            rec["differing_cases"] = diffs
            rec["status"] = "KILLED" if diffs else "SURVIVED"
            if diffs:
                cid = diffs[0]
                rec["negative_example"] = {
                    "case_id": cid,
                    "pristine": base[cid],
                    "mutant": out.get(cid),
                }
        except Exception as exc:  # pragma: no cover - harness self-check path
            rec["status"] = "ERROR"
            rec["error"] = repr(exc)
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)
                for key in [k for k in sys.modules if k.startswith("scorer_mut_")]:
                    sys.modules.pop(key, None)
        records.append(rec)

    semantic = [r for r in records if r["semantic"]]
    controls = [r for r in records if not r["semantic"]]
    killed = [r for r in semantic if r["status"] == "KILLED"]
    survived_controls = [r for r in controls if r["status"] == "SURVIVED"]
    return {
        "n_mutants": len(records),
        "n_semantic": len(semantic),
        "n_semantic_killed": len(killed),
        "n_controls": len(controls),
        "n_controls_survived": len(survived_controls),
        "mutation_score": (len(killed) / len(semantic)) if semantic else 0.0,
        "controls_clean": len(survived_controls) == len(controls),
        "ok": len(killed) == len(semantic) and len(survived_controls) == len(controls),
        "mutants": records,
    }