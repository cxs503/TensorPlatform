#!/usr/bin/env python3
"""Audit the SWE-bench style fleet capability baseline's *evidence grade*.

Why this exists
---------------
Three files under ``eval_set/results/`` describe "the same" fleet capability
baseline and they do not agree:

* ``baseline_report.json`` / ``.md`` (mission m538) -- per-model pass rates with
  Wilson 95% CIs, built by ``generate_baseline.py`` from a hand-typed
  ``HISTORICAL_MAPPING`` table ("Since we cannot run live evaluations in this
  environment ...").
* ``baseline_table.json`` -- per-model/per-difficulty table, every ``passed`` = 0.
* ``eval_results_20261030T194201Z_verified.json`` -- 100 verifications, every
  ``pass`` = false, every ``avg_score`` = 0.25 (the dry-run floor).

Any consumer that quotes "deepseek-v4-flash pass rate 0.455" is quoting one of
these numbers. Before such a number can be used as a capability claim it has to
be traced to its source and re-derived. This script does exactly that: it
re-derives each claimed number from the artifact the producer says it came from,
and grades the result.

Design rules
------------
* stdlib only, deterministic, read-only: no input file is ever rewritten, no
  network, no wall-clock in the output (timestamps of the sources are quoted,
  never generated).
* The production mapping table is recovered by AST-parsing
  ``generate_baseline.py`` (``ast.literal_eval`` of the ``HISTORICAL_MAPPING``
  assignment) instead of importing it -- importing would run module-level
  report writing and would make the audit depend on side effects.
* Every verdict is a pure function of the artifacts, so a contradiction that
  gets fixed flips the verdict on the next run instead of leaving a frozen
  claim behind.

Verdict vocabulary
------------------
``PASS``  claim reproduces from the artifact it claims to come from;
``WARN``  claim reproduces only partially / provenance marker missing;
``FAIL``  claim is contradicted by the artifact, or the artifact is a stub;
``INFO``  descriptive measurement (no pass/fail semantics).

Evidence levels (platform vocabulary)
-------------------------------------
``REAL``      recomputed here from primary artifacts;
``MOCK``      hard-coded / dry-run placeholder masquerading as a measurement;
``STUB``      declared but empty field;
``WITHHELD``  cannot be decided from the artifacts in the tree.

Usage
-----
    python3 eval_set/scripts/audit_baseline_integrity.py \
        --json eval_set/results/baseline_integrity_audit.json \
        --md  docs/audit/eval_baseline_integrity_ledger.md
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import re
from pathlib import Path

# ── paths (all relative to the repository root) ──────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
EVAL_JSON = ROOT / "eval_set" / "eval_set.json"
GEN_SRC = ROOT / "eval_set" / "scripts" / "generate_baseline.py"
RUN_SRC = ROOT / "eval_set" / "scripts" / "run_eval.py"
REPORT_JSON = ROOT / "eval_set" / "results" / "baseline_report.json"
TABLE_JSON = ROOT / "eval_set" / "results" / "baseline_table.json"
RESULTS_JSON = ROOT / "eval_set" / "results" / "eval_results_20261030T194201Z.json"
VERIFIED_JSON = ROOT / "eval_set" / "results" / "eval_results_20261030T194201Z_verified.json"

DIFFICULTIES = ("easy", "medium", "hard")
Z95 = 1.96
CI_TOL = 2e-3          # claimed CIs are rounded to 3 dp
RATE_TOL = 5e-4        # claimed rates are rounded to 3 dp
WALL_H_TOL = 0.06      # claimed wall hours are rounded to 1 dp


# ── helpers ──────────────────────────────────────────────────────────────────
def load_json(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def wilson_ci(p: float, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval, independent re-implementation of the producer's."""
    if n <= 0:
        return (0.0, 0.0)
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def extract_historical_mapping(src_path: Path) -> list[dict]:
    """Recover HISTORICAL_MAPPING = [(task_id, mission, model, status, wall_s), ...]."""
    tree = ast.parse(src_path.read_text(encoding="utf-8"), filename=str(src_path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "HISTORICAL_MAPPING":
                    rows = ast.literal_eval(node.value)
                    return [
                        {
                            "task_id": r[0],
                            "source_mission": r[1],
                            "model": r[2],
                            "status": r[3],
                            "wall_seconds": int(r[4]) if len(r) > 4 else 0,
                        }
                        for r in rows
                    ]
    raise LookupError("HISTORICAL_MAPPING not found in %s" % src_path)


def extract_all_models(src_path: Path) -> list[str]:
    tree = ast.parse(src_path.read_text(encoding="utf-8"), filename=str(src_path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "ALL_MODELS":
                    return list(ast.literal_eval(node.value))
    raise LookupError("ALL_MODELS not found in %s" % src_path)


def recompute_from_mapping(rows: list[dict], models: list[str],
                           task_diff: dict | None = None) -> dict:
    """Per-model / per-difficulty counts implied by the mapping alone.

    ``difficulty`` is *not* in the mapping, so per-difficulty recomputation is
    impossible from the mapping; we therefore key difficulty through the eval
    set (task -> difficulty) and report which buckets the mapping really fills.
    """
    if task_diff is None:
        task_diff = {t["id"]: t["difficulty"] for t in load_json(EVAL_JSON)["tasks"]}
    out = {m: {d: {"total": 0, "passed": 0, "wall_total": 0} for d in DIFFICULTIES}
           for m in models}
    for r in rows:
        bucket = out.setdefault(r["model"], {}).setdefault(
            task_diff.get(r["task_id"], "unknown"), {"total": 0, "passed": 0, "wall_total": 0})
        bucket["total"] += 1
        bucket["passed"] += 1 if r["status"] == "succeeded" else 0
        bucket["wall_total"] += r["wall_seconds"]
    for m in out.values():
        ov = {"total": 0, "passed": 0, "wall_total": 0}
        for d in list(m):
            ov["total"] += m[d]["total"]
            ov["passed"] += m[d]["passed"]
            ov["wall_total"] += m[d]["wall_total"]
        m["overall"] = ov
    return out


# ── checks ───────────────────────────────────────────────────────────────────
def _chk(cid, group, verdict, detail, evidence, **nums):
    rec = {"id": cid, "group": group, "verdict": verdict, "detail": detail,
           "evidence": evidence}
    if nums:
        rec["numbers"] = nums
    return rec


def check_provenance(report, table, results, verified, mapping, models, evalset) -> list[dict]:
    checks = []

    # A1 -- are the pass rates backed by an executed evaluation run?
    res_rows = results.get("results", [])
    dry = sum(1 for r in res_rows if r.get("status") == "dry_run")
    meta_dry = bool(results.get("meta", {}).get("dry_run"))
    checks.append(_chk(
        "A1_run_provenance", "provenance",
        "FAIL" if (meta_dry and dry == len(res_rows) and res_rows) else "PASS",
        "eval run artifact is a dry run: meta.dry_run=%s, %d/%d dispatched cells have "
        "status='dry_run' -> the per-model pass rates in baseline_report.json are NOT "
        "produced by executing the eval set" % (meta_dry, dry, len(res_rows)),
        "REAL",
        dry_run_rows=dry, dispatched_rows=len(res_rows), meta_dry_run=meta_dry))

    # A2 -- do the per-model denominators reproduce from the mapping table?
    rec = recompute_from_mapping(mapping, models)
    deltas = {}
    for m in models:
        claimed = report["baseline"][m]["total_tasks"]
        measured = rec[m]["overall"]["total"]
        deltas[m] = {"claimed_total": claimed, "mapping_rows": measured,
                     "imputed_rows": claimed - measured,
                     "claimed_passed": report["baseline"][m]["passed"],
                     "mapping_passed": rec[m]["overall"]["passed"]}
    imputed = sum(max(0, d["imputed_rows"]) for d in deltas.values())
    checks.append(_chk(
        "A2_mapping_closure", "provenance",
        "FAIL" if imputed > 0 else "PASS",
        "the report's denominators do not close on its own source: %d of %d per-model "
        "task slots are imputed (estimated_total=3 per empty difficulty bucket) rather "
        "than present in HISTORICAL_MAPPING" % (imputed, sum(d["claimed_total"] for d in deltas.values())),
        "REAL", per_model=deltas, imputed_rows=imputed))

    # A3 -- is the imputation marker preserved in the machine-readable artifact?
    blob = json.dumps(report, ensure_ascii=False)
    n_est = blob.count('"estimated"')
    checks.append(_chk(
        "A3_estimate_flag", "provenance",
        "FAIL" if n_est == 0 else "PASS",
        "generate_baseline.py sets baseline[model][diff]['estimated']=True for imputed "
        "buckets and the .md table renders it as '*', but baseline_report.json contains "
        "%d 'estimated' keys -> a JSON consumer cannot tell measured from imputed cells"
        % n_est,
        "REAL", estimated_keys_in_json=n_est))

    # A4 -- top-level CI block
    ci = report.get("confidence_intervals", "missing")
    checks.append(_chk(
        "A4_ci_block", "provenance",
        "STUB" if ci == {} else ("MISSING" if ci == "missing" else "PASS"),
        "baseline_report.json['confidence_intervals'] is %s while the .md/JSON present "
        "per-model ci_95 -> the declared CI container is a stub"
        % ("an empty object" if ci == {} else repr(ci)),
        "REAL"))

    # A5 -- repo coverage claim inside one artifact
    claimed_txt = report.get("summary", "")
    m5 = re.search(r"covering (\d+) repos", claimed_txt)
    listed = len(report.get("eval_set", {}).get("repos", []))
    actual = sorted({t["repo"] for t in evalset["tasks"]})
    ok = bool(m5) and int(m5.group(1)) == listed == len(actual)
    checks.append(_chk(
        "A5_repo_claim", "provenance", "PASS" if ok else "WARN",
        "summary text claims %s repos, report eval_set.repos lists %d, tasks actually "
        "cover %d (%s)"
        % (m5.group(1) if m5 else "?", listed, len(actual), ", ".join(actual)),
        "REAL", claimed=int(m5.group(1)) if m5 else None, listed=listed,
        actual=len(actual)))

    # A6 -- verified artifact contradicts the report
    vpass = verified.get("meta", {}).get("total_passed")
    vtotal = verified.get("meta", {}).get("total_tasks")
    rep_pass = sum(report["baseline"][m]["passed"] for m in models)
    rep_total = sum(report["baseline"][m]["total_tasks"] for m in models)
    checks.append(_chk(
        "A6_report_vs_verified", "cross_artifact",
        "FAIL" if (vpass == 0 and rep_pass > 0) else "PASS",
        "verified artifact reports %s/%s passed (pass_rate %s) while baseline_report "
        "reports %d/%d passed -> two 'same baseline' artifacts disagree on the headline"
        % (vpass, vtotal, verified.get("meta", {}).get("pass_rate"), rep_pass, rep_total),
        "REAL", verified_passed=vpass, verified_total=vtotal,
        report_passed=rep_pass, report_total=rep_total))

    # A7 -- verified scores are a constant floor
    scores = [s for m in verified.get("model_summary", {}).values() for s in m.get("scores", [])]
    uniq = sorted({round(float(s), 6) for s in scores})
    checks.append(_chk(
        "A7_verified_score_floor", "cross_artifact",
        "FAIL" if len(uniq) == 1 else "PASS",
        "all %d verification scores take the single value %s -> the weighted score is a "
        "constant, i.e. no criterion moved it (dry-run floor, not a measurement)"
        % (len(scores), uniq[0] if len(uniq) == 1 else uniq),
        "MOCK", n_scores=len(scores), distinct_values=len(uniq)))

    # A8 -- table vs report
    bad = []
    for m in models:
        t = table.get(m, {}).get("overall", {})
        r = report["baseline"][m]
        if t.get("passed") != r["passed"] or t.get("total") != r["total_tasks"]:
            bad.append({"model": m, "table": [t.get("passed"), t.get("total")],
                        "report": [r["passed"], r["total_tasks"]]})
    checks.append(_chk(
        "A8_table_vs_report", "cross_artifact",
        "FAIL" if bad else "PASS",
        "baseline_table.json and baseline_report.json disagree for %d/%d models on "
        "(passed, total)" % (len(bad), len(models)),
        "REAL", disagreeing=len(bad), rows=bad[:8]))
    return checks


def check_arithmetic(report, models, mapping, evalset) -> list[dict]:
    checks = []
    # B1 -- by_difficulty totals must sum to the model total
    bad = []
    for m in models:
        r = report["baseline"][m]
        s = sum(r["by_difficulty"][d]["total"] for d in DIFFICULTIES)
        if s != r["total_tasks"]:
            bad.append({"model": m, "sum_by_difficulty": s, "total_tasks": r["total_tasks"]})
    checks.append(_chk(
        "B1_difficulty_closure", "arithmetic", "PASS" if not bad else "FAIL",
        "per-model by_difficulty totals sum to total_tasks for %d/%d models"
        % (len(models) - len(bad), len(models)), "REAL", failures=bad))

    # B2 -- pass_rate == passed/total
    bad = []
    for m in models:
        for d in DIFFICULTIES + ("overall",):
            if d == "overall":
                p, n, rate = (report["baseline"][m]["passed"],
                              report["baseline"][m]["total_tasks"],
                              report["baseline"][m]["pass_rate"])
            else:
                b = report["baseline"][m]["by_difficulty"][d]
                p, n, rate = b["passed"], b["total"], b["pass_rate"]
            want = p / n if n else 0.0
            if abs(want - rate) > RATE_TOL:
                bad.append({"model": m, "bucket": d, "claimed": rate, "recomputed": round(want, 4)})
    checks.append(_chk(
        "B2_rate_arithmetic", "arithmetic", "PASS" if not bad else "FAIL",
        "claimed pass_rate equals passed/total (tol %g) in all %d buckets"
        % (RATE_TOL, len(models) * 4) if not bad else
        "%d buckets have a pass_rate that does not equal passed/total" % len(bad),
        "REAL", failures=bad[:8]))

    # B3 -- Wilson CI reproduces
    bad = []
    max_dev = 0.0
    for m in models:
        r = report["baseline"][m]
        lo, hi = wilson_ci(r["pass_rate"], r["total_tasks"])
        clo, chi = r["ci_95"]
        dev = max(abs(lo - clo), abs(hi - chi))
        max_dev = max(max_dev, dev)
        if dev > CI_TOL:
            bad.append({"model": m, "claimed": [clo, chi], "recomputed": [round(lo, 4), round(hi, 4)]})
    checks.append(_chk(
        "B3_wilson_recompute", "arithmetic", "PASS" if not bad else "FAIL",
        "per-model Wilson 95%% CI recomputed independently (z=%.2f); max deviation "
        "%0.5f, %d/%d models outside tolerance %g"
        % (Z95, max_dev, len(bad), len(models), CI_TOL),
        "REAL", max_abs_dev=round(max_dev, 6), failures=bad))

    # B4 -- wall hours
    bad = []
    for m in models:
        r = report["baseline"][m]
        want = r["wall_seconds_total"] / 3600.0
        if abs(want - r["wall_hours_total"]) > WALL_H_TOL:
            bad.append({"model": m, "claimed_h": r["wall_hours_total"], "recomputed_h": round(want, 3)})
    checks.append(_chk(
        "B4_wall_hours", "arithmetic", "PASS" if not bad else "FAIL",
        "wall_hours_total equals wall_seconds_total/3600 (tol %g) for %d/%d models"
        % (WALL_H_TOL, len(models) - len(bad), len(models)), "REAL", failures=bad))

    # B5 -- how much of the 5 x 20 cell matrix is actually observed
    cells = {(r["task_id"], r["model"]) for r in mapping}
    total_cells = len(evalset["tasks"]) * len(models)
    checks.append(_chk(
        "B5_cell_coverage", "arithmetic", "INFO",
        "HISTORICAL_MAPPING observes %d of %d (task, model) cells = %.1f%%; the rest of "
        "the matrix is imputed" % (len(cells), total_cells, 100.0 * len(cells) / total_cells),
        "REAL", observed_cells=len(cells), total_cells=total_cells))
    return checks


# ── acceptance-criterion verifiability classifier ────────────────────────────
# MEASURED  : the criterion states a numeric tolerance/comparator a script can assert.
# DECIDABLE : the criterion names an observable anchor (endpoint, HTTP status,
#             return value, test, file, state key) that a script can assert.
# SUBJECTIVE: narrative/quality prose ("documentation explains ...") -- only a human
#             judge can decide it, so it cannot gate a machine verdict.
MEASURED_RE = re.compile(
    r"(\d+(\.\d+)?\s*(%|s\b|ms\b|m/s|MB|GB|files?\b|lines?\b|orders?\b|"
    r"calls?\b|triangles?\b))|>=|<=|≥|≤|<|>|\bwithin\b|\bat least\b|"
    r"\bno more than\b|\bexactly\b|\bmax\b|\bmin\b", re.I)
DECIDABLE_RE = re.compile(
    r"(\bGET \b|\bPOST \b|\bPUT \b|\bPATCH \b|\bDELETE \b|/tensor/|/\w+\{|"
    r"\breturns?\b|pass/fail|\b40[13]\b|\b200\b|unit test|test_|\batomic\b|"
    r"\blogged\b|\brecords?\b|\bsucceeds?\b|\bfails?\b|without crash|"
    r"without .{0,20}failure|to completion|\bkeyed by\b|\bwhitelist\b|"
    r"\bbackward compatible\b|no duplicate|non-manifold|\bsymmetric\b|"
    r"\bcovers all\b|\btabulated\b|\bextracted\b|\bgenerated\b|\bemits?\b|"
    r"\bemitted\b|\bexists?\b|non-empty|\bconfigured\b|\bselectable\b|"
    r"\bavailable\b|\bpresent\b)", re.I)

# Hand labels for a deterministic 20-criterion sample (every 5th criterion of the
# flattened list), read by the auditor. M=measured, D=decidable, S=subjective.
CALIBRATION_SAMPLE = {
    0: "D", 5: "M", 10: "M", 15: "M", 20: "M", 25: "M", 30: "M", 35: "D",
    40: "M", 45: "M", 50: "D", 55: "M", 60: "D", 65: "M", 70: "D", 75: "M",
    80: "M", 85: "D", 90: "M", 95: "D",
}


def classify_criterion(text: str) -> str:
    if MEASURED_RE.search(text):
        return "measured"
    if DECIDABLE_RE.search(text):
        return "decidable"
    return "subjective"


def classify_criteria(tasks: list[dict]) -> dict:
    flat = [(t["id"], i, c) for t in tasks
            for i, c in enumerate(t["acceptance_criteria"])]
    tiers = {"measured": 0, "decidable": 0, "subjective": 0}
    subjective = []
    for tid, i, c in flat:
        tier = classify_criterion(c)
        tiers[tier] += 1
        if tier == "subjective":
            subjective.append({"task": tid, "index": i, "criterion": c[:110]})

    hit = 0
    for idx, label in CALIBRATION_SAMPLE.items():
        if idx < len(flat):
            auto = classify_criterion(flat[idx][2])
            if auto[0].upper() == label:
                hit += 1
    n_sample = sum(1 for i in CALIBRATION_SAMPLE if i < len(flat))
    n = len(flat)
    return {
        "n_criteria": n,
        "tiers": tiers,
        "subjective_fraction": round(tiers["subjective"] / n, 4) if n else 0.0,
        "subjective_examples": subjective[:12],
        "calibration": {
            "n_sample": n_sample,
            "agreement": round(hit / n_sample, 4) if n_sample else 0.0,
            "labels": {str(k): v for k, v in CALIBRATION_SAMPLE.items()},
            "note": "sample = every 5th acceptance criterion; labels assigned by the "
                    "auditor (single rater, no second annotator -> agreement is an "
                    "internal-consistency check, not inter-rater reliability)",
        },
    }


def check_evalset(evalset, run_src: Path) -> list[dict]:
    checks = []
    tasks = evalset["tasks"]

    # D1 -- difficulty distribution
    dist = {d: sum(1 for t in tasks if t["difficulty"] == d) for d in DIFFICULTIES}
    declared = evalset.get("meta", {}).get("difficulty_levels", {})
    checks.append(_chk(
        "D1_difficulty_dist", "evalset", "PASS" if sum(dist.values()) == len(tasks) else "FAIL",
        "recomputed difficulty distribution %s over %d tasks (meta declares the levels, "
        "not the counts)" % (dist, len(tasks)), "REAL", distribution=dist,
        declared_levels=len(declared)))

    # D2 -- succeeded/failed balance
    src = {s: sum(1 for t in tasks if t["source_status"] == s) for s in ("succeeded", "failed")}
    checks.append(_chk(
        "D2_source_balance", "evalset", "INFO",
        "source mission outcomes: %s (balance is a design choice, not a defect)" % src,
        "REAL", **src))

    # D3 -- id integrity
    ids = [t["id"] for t in tasks]
    dups = sorted({i for i in ids if ids.count(i) > 1})
    checks.append(_chk(
        "D3_id_integrity", "evalset", "PASS" if not dups and len(set(ids)) == len(ids) else "FAIL",
        "%d tasks, %d unique ids, duplicates=%s" % (len(ids), len(set(ids)), dups or "none"),
        "REAL", n_tasks=len(ids), n_unique=len(set(ids))))

    # D4 -- machine-verifiability of acceptance criteria (tiered lexicon + calibration)
    d4 = classify_criteria(tasks)
    checks.append(_chk(
        "D4_criteria_verifiability", "evalset",
        "PASS" if d4["subjective_fraction"] <= 0.10 else
        ("WARN" if d4["subjective_fraction"] <= 0.25 else "FAIL"),
        "%d acceptance criteria: %d measured (numeric tolerance), %d decidable "
        "(observable anchor: endpoint/status/return/test/file), %d subjective "
        "(%.1f%%, needs a human judge -> not machine-decidable; FAIL >25%%, WARN >10%%). "
        "Classifier calibrated on a %d-criterion hand-labelled sample, agreement %.0f%%"
        % (d4["n_criteria"], d4["tiers"]["measured"], d4["tiers"]["decidable"],
           d4["tiers"]["subjective"], 100 * d4["subjective_fraction"],
           d4["calibration"]["n_sample"], 100 * d4["calibration"]["agreement"]),
        "REAL", **{k: d4[k] for k in ("n_criteria", "tiers", "subjective_fraction")},
        calibration=d4["calibration"], examples=d4["subjective_examples"]))

    # D5 -- solution-hint leakage into the dispatch prompt
    run_src_txt = run_src.read_text(encoding="utf-8")
    leaks_hint = "solution_hints" in run_src_txt and "goal" in run_src_txt
    path_hints, num_hints = [], []
    for t in tasks:
        for h in t.get("solution_hints", []):
            if re.search(r"(tests?/|\.py\b|\.json\b|pytest|test_)", h):
                path_hints.append({"task": t["id"], "hint": h[:90]})
            if re.search(r"\d", h) and re.search(r"(\d+(\.\d+)?\s*(%|s\b|order))", h):
                num_hints.append({"task": t["id"], "hint": h[:90]})
    checks.append(_chk(
        "D5_hint_leakage", "evalset",
        "WARN" if (leaks_hint and (path_hints or num_hints)) else "PASS",
        "run_eval.py renders solution_hints into the dispatched goal, so the model sees "
        "the intended solution: %d hints name a concrete file/test path, %d carry the "
        "numeric acceptance magnitude" % (len(path_hints), len(num_hints)),
        "REAL", hints_with_paths=len(path_hints), hints_with_numbers=len(num_hints),
        examples=(path_hints + num_hints)[:6]))

    # D6 -- cross-repo artifact references resolvable inside the eval text
    addr = [{"task": t["id"], "field": f}
            for t in tasks for f in ("repo", "title", "description") if not str(t.get(f, "")).strip()]
    checks.append(_chk(
        "D6_field_completeness", "evalset", "PASS" if not addr else "FAIL",
        "all tasks carry non-empty repo/title/description (empty=%d)" % len(addr),
        "REAL", empties=addr))
    return checks


def check_reproducibility(report, results) -> list[dict]:
    checks = []
    # E1 -- does the report carry anything that lets a third party re-derive it?
    has_seed = any(k in json.dumps(report) for k in ('"seed"', '"mapping_digest"', '"sha256"'))
    checks.append(_chk(
        "E1_reproducibility_stamp", "reproducibility", "WARN" if not has_seed else "PASS",
        "baseline_report.json records no digest of HISTORICAL_MAPPING and no seed, so the "
        "reader cannot tell which mapping revision produced the numbers", "REAL"))
    # E2 -- regenerating would overwrite with a fresh wall clock
    g = report.get("generated_at", "")
    r = results.get("meta", {}).get("run_at", "")
    checks.append(_chk(
        "E2_timestamp_drift", "reproducibility", "INFO",
        "baseline generated_at=%s, eval run_at=%s -- regenerating rewrites these fields, "
        "so byte-identical reproduction is impossible by construction" % (g, r), "REAL"))
    return checks


# ── driver ───────────────────────────────────────────────────────────────────
def build_audit() -> dict:
    evalset = load_json(EVAL_JSON)
    report = load_json(REPORT_JSON)
    table = load_json(TABLE_JSON)
    results = load_json(RESULTS_JSON)
    verified = load_json(VERIFIED_JSON)
    mapping = extract_historical_mapping(GEN_SRC)
    models = extract_all_models(GEN_SRC)

    checks = []
    checks += check_provenance(report, table, results, verified, mapping, models, evalset)
    checks += check_arithmetic(report, models, mapping, evalset)
    checks += check_evalset(evalset, RUN_SRC)
    checks += check_reproducibility(report, results)

    # per-model claimed vs mapping-recomputed rates (figure/table source)
    rec = recompute_from_mapping(mapping, models)
    per_model = {}
    for m in models:
        claimed = report["baseline"][m]
        obs = rec[m]["overall"]
        obs_rate = (obs["passed"] / obs["total"]) if obs["total"] else 0.0
        lo, hi = wilson_ci(obs_rate, obs["total"])
        per_model[m] = {
            "claimed_total": claimed["total_tasks"],
            "claimed_passed": claimed["passed"],
            "claimed_rate": claimed["pass_rate"],
            "claimed_ci95": claimed["ci_95"],
            "mapping_total": obs["total"],
            "mapping_passed": obs["passed"],
            "mapping_rate": round(obs_rate, 4),
            "mapping_ci95": [round(lo, 4), round(hi, 4)],
            "verified_passed": verified.get("model_summary", {}).get(m, {}).get("passed"),
            "table_passed": table.get(m, {}).get("overall", {}).get("passed"),
        }

    counts = {}
    for c in checks:
        counts[c["verdict"]] = counts.get(c["verdict"], 0) + 1

    headline = {
        "artifact": "eval_set fleet capability baseline (mission m538)",
        "checks_total": len(checks),
        "verdicts": counts,
        "baseline_evidence_level": "MOCK",
        "baseline_evidence_reason": (
            "pass rates are derived from a hand-typed HISTORICAL_MAPPING inside "
            "generate_baseline.py ('cannot run live evaluations in this environment'); "
            "the only executed artifact is a dry run with every cell status='dry_run', "
            "every verification failed and every weighted score a constant 0.25"),
    }
    audit = {
        "audit_version": "1.0.0",
        "generator": "eval_set/scripts/audit_baseline_integrity.py",
        "inputs": {
            "eval_set": str(EVAL_JSON.relative_to(ROOT)),
            "report": str(REPORT_JSON.relative_to(ROOT)),
            "table": str(TABLE_JSON.relative_to(ROOT)),
            "results": str(RESULTS_JSON.relative_to(ROOT)),
            "verified": str(VERIFIED_JSON.relative_to(ROOT)),
            "mapping_source": str(GEN_SRC.relative_to(ROOT)),
        },
        "source_timestamps": {
            "baseline_generated_at": report.get("generated_at"),
            "eval_run_at": results.get("meta", {}).get("run_at"),
            "verified_at": verified.get("meta", {}).get("verified_at"),
        },
        "headline": headline,
        "per_model": per_model,
        "checks": checks,
    }
    return audit


def render_markdown(audit: dict) -> str:
    h = audit["headline"]
    lines = [
        "# 舰队能力基线（m538）证据等级审计 · 机检账本",
        "",
        "> 本页由 `eval_set/scripts/audit_baseline_integrity.py` 从 "
        "`eval_set/results/baseline_integrity_audit.json` 程序化渲染，数字零手抄。",
        "> 审计对象：`eval_set/results/baseline_report.json`（m538 能力基线）、"
        "`baseline_table.json`、`eval_results_20261030T194201Z{,_verified}.json`。",
        "",
        "## 判定摘要",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 检查项 | %d |" % h["checks_total"],
        "| PASS / WARN / FAIL / STUB / MISSING / INFO | %s / %s / %s / %s / %s / %s |" % (
            h["verdicts"].get("PASS", 0), h["verdicts"].get("WARN", 0),
            h["verdicts"].get("FAIL", 0), h["verdicts"].get("STUB", 0),
            h["verdicts"].get("MISSING", 0), h["verdicts"].get("INFO", 0)),
        "| 基线证据等级 | **%s** |" % h["baseline_evidence_level"],
        "",
        h["baseline_evidence_reason"],
        "",
        "## 逐模型：声称值 vs 由映射表复算值 vs 另两份工件",
        "",
        "| 模型 | 报告 passed/total | 报告 rate [95%CI] | 映射表 passed/total | 映射表 rate [95%CI] | table passed | verified passed |",
        "|---|---|---|---|---|---|---|"
    ]
    for m, d in audit["per_model"].items():
        lines.append("| %s | %d/%d | %.3f [%.3f, %.3f] | %d/%d | %.4f [%.4f, %.4f] | %s | %s |" % (
            m, d["claimed_passed"], d["claimed_total"], d["claimed_rate"],
            d["claimed_ci95"][0], d["claimed_ci95"][1],
            d["mapping_passed"], d["mapping_total"], d["mapping_rate"],
            d["mapping_ci95"][0], d["mapping_ci95"][1],
            d["table_passed"], d["verified_passed"]))
    lines += ["", "## 检查逐条", "", "| id | 组 | 判定 | 证据 | 说明 |", "|---|---|---|---|---|"]
    for c in audit["checks"]:
        detail = c["detail"].replace("|", "｜").replace("\n", " ")
        lines.append("| `%s` | %s | **%s** | %s | %s |" % (
            c["id"], c["group"], c["verdict"], c["evidence"], detail))
    lines += ["", "## 原始数值", "", "```json",
              json.dumps(audit["per_model"], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit fleet capability baseline evidence grade")
    ap.add_argument("--json", type=Path, default=None, help="write audit ledger JSON here")
    ap.add_argument("--md", type=Path, default=None, help="write rendered markdown here")
    args = ap.parse_args()

    audit = build_audit()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("wrote", args.json)
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(render_markdown(audit), encoding="utf-8")
        print("wrote", args.md)
    if not args.json and not args.md:
        print(json.dumps(audit["headline"], ensure_ascii=False, indent=2))
        for c in audit["checks"]:
            print("%-28s %-8s %s" % (c["id"], c["verdict"], c["detail"][:110]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())