"""End-to-end runner: independence re-grade + metamorphic gate + injection bench.

Pipeline
--------
1. resolve the declaration locators (``build_declarations``) — unless already built;
2. build the synthetic handle population (3 targets + 20 controls);
3. run the metamorphic gate on the honest population, then per relation instance
   run its **negative control** (anti-vacuity);
4. inject the three forgery classes into the three targets and measure detection;
5. write everything back to SQLite (backup + timing + rollback drill);
6. read **all reported numbers back out of SQL** and assemble the report payload;
7. check the acceptance criteria and write ``acceptance.json``.

Nothing above is domain-specific: capability identifiers, probes and tolerances
are artifact data.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from . import build_declarations, handles as H, independence, injection, ledger, metamorphic
from .model import HandleSpec, InjectionType, RelationStatus

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR_REL = "artifacts/evidence_second_impl"
MANIFEST_REL = f"{OUT_DIR_REL}/bench_manifest.json"
DECLARATIONS_REL = independence.DEFAULT_DECLARATIONS_REL
LEDGER_REL = f"{OUT_DIR_REL}/evidence_second_impl.sqlite"
REPORT_REL = f"{OUT_DIR_REL}/run_report.json"
ACCEPTANCE_REL = f"{OUT_DIR_REL}/acceptance.json"

# acceptance thresholds (mission metadata, machine-checkable)
MIN_DETECTION_PER_TYPE = 2 / 3     # at least 2 of 3 targets per forgery class
MIN_DETECTION_OVERALL = 2 / 3
MAX_FALSE_ALARMS = 0
MAX_LEDGER_SECONDS = 5.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ensure_manifest(path: Path, proxies: Sequence[str]) -> Dict[str, Any]:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    manifest = H.build_manifest(proxy_ids=list(proxies))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    return manifest


def _ensure_declarations(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return build_declarations.build()
    return {"reused": True, "out": _rel(path, ROOT)}


def _run_negative_controls(specs: Sequence[HandleSpec]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for spec in specs:
        reference, second = H.build_oracles(spec)
        for rel in spec.relations:
            rows.append(metamorphic.negative_control(rel, spec, second, reference))
    return rows


def _domain_scan() -> Dict[str, Any]:
    """Domain-vocabulary scan of the mechanism package itself (criterion (f))."""
    try:
        from tools import evidence_domain_scan as scan  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - import guard
        return {"status": "SKIPPED", "detail": f"scanner unavailable: {exc}", "hits": []}
    result = scan.scan_paths(["tools/evidence_second_impl"])
    hits = result.get("hits") or []
    return {"status": "CLEAN" if not hits else "DIRTY", "hits": hits,
            "files_scanned": result.get("scanned_files"),
            "terms": result.get("terms")}


def _synthetic_id_check(specs: Sequence[HandleSpec]) -> Dict[str, Any]:
    bad = [s.handle_id for s in specs if not s.handle_id.startswith("synth.")]
    return {"all_synthetic": not bad, "non_synthetic": bad}


# ---------------------------------------------------------------------------
# main run
# ---------------------------------------------------------------------------

def run(root: Path | str = ROOT, out_dir: Path | str | None = None,
        write_ledger: bool = True) -> Dict[str, Any]:
    root = Path(root)
    out = Path(out_dir) if out_dir else root / OUT_DIR_REL
    out.mkdir(parents=True, exist_ok=True)

    t_start = time.perf_counter()

    # 1) declarations -> independence re-grade -----------------------------
    decl_info = _ensure_declarations(root / DECLARATIONS_REL)
    records = independence.load_declarations(root / DECLARATIONS_REL)
    regrade_rows = independence.regrade_all(records, root)
    indep_summary = independence.summarise(regrade_rows)

    # 2) handle population -------------------------------------------------
    proxies = [r.capability_id for r in records
               if r.graded_grade == "REAL"] or ["synth.proxy"]
    manifest = _ensure_manifest(out / "bench_manifest.json", proxies)
    specs = H.load_manifest(manifest)
    targets = [s for s in specs if s.handle_class == "target"]
    controls = [s for s in specs if s.handle_class == "control"]

    # 3) metamorphic gate + negative controls ------------------------------
    bench = injection.run_bench(specs)
    nc_rows = _run_negative_controls(specs)

    # 4) ledger write-back + rollback drill --------------------------------
    meta = {
        "schema_version": ledger.SCHEMA_VERSION,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": platform.python_version(),
        "n_handles": len(specs),
        "n_targets": len(targets),
        "n_controls": len(controls),
        "declaration_digest": decl_info.get("digest", ""),
    }
    handle_verdicts = [{"handle_id": r["handle_id"], "handle_class": r["handle_class"],
                        "results": [{"kind": x.kind.value, "status": x.status.value,
                                     "residual": x.residual, "tolerance": x.tolerance,
                                     "metric": x.metric}
                                    for x in injection.gate(
                                        spec,
                                        H.build_oracles(spec)[1],
                                        H.build_oracles(spec)[0]).results]}
                       for spec, r in zip(specs, bench["clean"])]

    db_path = out / "evidence_second_impl.sqlite"
    write_info: Dict[str, Any] = {}
    drill: Dict[str, Any] = {}
    if write_ledger:
        write_info = ledger.write_back(
            db_path, capabilities=regrade_rows, handle_results=handle_verdicts,
            negative_controls=nc_rows, injections=bench["injections"], meta=meta)
        drill = ledger.rollback_drill(db_path, write_info["backup"],
                                      write_info["pre_digest"])
        # restore the ledger once the drill is verified, so the artifact keeps data
        ledger.write_back(
            db_path, capabilities=regrade_rows, handle_results=handle_verdicts,
            negative_controls=nc_rows, injections=bench["injections"], meta=meta)

    sql = _sql_snapshot(db_path) if write_ledger else {}

    # 5) acceptance checks --------------------------------------------------
    acceptance = _acceptance(bench, nc_rows, sql, drill, write_info,
                             regrade_rows, specs)
    elapsed = time.perf_counter() - t_start

    report = {
        "schema_version": "evidence-second-impl-run/1.0",
        "generated_at": meta["built_at"],
        "elapsed_seconds": round(elapsed, 3),
        "declarations": decl_info,
        "independence": {k: v for k, v in indep_summary.items()},
        "bench": {"summary": bench["summary"],
                  "controls": bench["controls"],
                  "clean": bench["clean"],
                  "injections": bench["injections"]},
        "metamorphic": {
            "catalogue": metamorphic.catalogue(),
            "negative_controls": nc_rows,
        },
        "ledger": {"path": _rel(db_path, root),
                   "write": {k: v for k, v in write_info.items()},
                   "rollback_drill": drill,
                   "sql": sql},
        "domain_scan": _domain_scan(),
        "handle_ids": _synthetic_id_check(specs),
        "acceptance": acceptance,
    }
    (out / "run_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    (out / "acceptance.json").write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    return report


def _rel(path: Path, root: Path) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)


def _sql_snapshot(db_path: Path) -> Dict[str, Any]:
    return {
        "grade_counts": ledger.q_grade_counts(db_path),
        "downgrades": ledger.q_downgrades(db_path),
        "self_reference": ledger.q_self_reference(db_path),
        "self_reference_share": ledger.q_self_reference_share(db_path),
        "injection_rates": ledger.q_injection_rates(db_path),
        "injection_total": ledger.q_injection_total(db_path),
        "detecting_kinds": ledger.q_detecting_kinds(db_path),
        "detection_matrix": ledger.q_detection_matrix(db_path),
        "injection_residuals": ledger.q_injection_residuals(db_path),
        "clean_alarms": ledger.q_clean_alarms(db_path),
        "metamorphic_failures": ledger.q_metamorphic_failures(db_path),
        "relation_stats": ledger.q_relation_stats(db_path),
        "negative_controls": ledger.q_negative_controls(db_path),
        "negative_control_by_kind": ledger.q_negative_control_by_kind(db_path),
        "evidence_lines": ledger.q_evidence_lines(db_path),
    }


def _acceptance(bench: Mapping[str, Any], nc_rows: Sequence[Mapping[str, Any]],
                sql: Mapping[str, Any], drill: Mapping[str, Any],
                write_info: Mapping[str, Any], regrade_rows: Sequence[Mapping[str, Any]],
                specs: Sequence[HandleSpec]) -> Dict[str, Any]:
    summary = bench["summary"]
    per_type = summary["per_injection"]
    checks: Dict[str, Any] = {}

    # (a) injection detection
    per_type_ok = {k: v["rate"] >= MIN_DETECTION_PER_TYPE - 1e-12
                   for k, v in per_type.items()}
    checks["a_injection_detection"] = {
        "pass": bool(all(per_type_ok.values())
                     and summary["injection_rate"] >= MIN_DETECTION_OVERALL - 1e-12),
        "per_type": {k: {"detected": v["detected"], "handles": v["handles"],
                         "rate": v["rate"], "ok": per_type_ok[k]}
                     for k, v in per_type.items()},
        "overall_rate": summary["injection_rate"],
        "threshold": {"per_type": MIN_DETECTION_PER_TYPE, "overall": MIN_DETECTION_OVERALL},
    }

    # (b) false alarms on untouched population
    checks["b_false_alarms"] = {
        "pass": summary["false_alarm_count"] <= MAX_FALSE_ALARMS
                and summary["clean_alarm_count"] <= MAX_FALSE_ALARMS,
        "control_handles": summary["controls"],
        "false_alarms": summary["false_alarms"],
        "false_alarm_count": summary["false_alarm_count"],
        "clean_alarm_count": summary["clean_alarm_count"],
        "threshold": MAX_FALSE_ALARMS,
    }

    # (c) every relation instance has a failing negative control
    n_nc = len(nc_rows)
    n_disc = sum(1 for r in nc_rows if r["discriminating"])
    sql_nc = (sql.get("negative_controls") or [{}])[0] if sql else {}
    checks["c_negative_controls"] = {
        "pass": n_nc > 0 and n_disc == n_nc and bool(sql_nc.get("n_mutant_failed") == n_nc
                                                     if sql else True),
        "instances": n_nc,
        "discriminating": n_disc,
        "mutant_failed_sql": sql_nc.get("n_mutant_failed"),
        "by_kind": sql.get("negative_control_by_kind", []),
    }

    # (d) per-capability self-reference share is queryable, with evidence lines
    real_rows = sql.get("self_reference", []) if sql else []
    share_rows = sql.get("self_reference_share", [{}]) if sql else [{}]
    evidence = sql.get("evidence_lines", []) if sql else []
    retained = [r for r in regrade_rows if r["verdict"] == "RETAIN_FLAGGED"]
    checks["d_self_reference_share"] = {
        "pass": bool(real_rows) and all("line" in e and e.get("line")
                                        for e in evidence if e["role"] == "primary"),
        "real_after": len(real_rows),
        "share": share_rows[0] if share_rows else {},
        "retained_self_reference": [{"capability_id": r["capability_id"],
                                     "reason_code": r["reason_code"],
                                     "reason": r["reason"]} for r in retained],
        "evidence_lines": len(evidence),
    }

    # (e) ledger update reversible and fast
    checks["e_ledger_rollback"] = {
        "pass": bool(drill.get("verified")) and float(write_info.get("elapsed_seconds", 1e9))
                < MAX_LEDGER_SECONDS,
        "update_seconds": write_info.get("elapsed_seconds"),
        "rollback_verified": bool(drill.get("verified")),
        "rollback_seconds": drill.get("elapsed_seconds"),
        "threshold_seconds": MAX_LEDGER_SECONDS,
        "backup_sha256": (write_info.get("backup") or {}).get("sha256"),
    }

    # (f) zero domain semantics in the mechanism layer
    scan = _domain_scan()
    ids = _synthetic_id_check(specs)
    checks["f_domain_neutral"] = {
        "pass": scan["status"] == "CLEAN" and ids["all_synthetic"],
        "scan_status": scan["status"],
        "scan_hits": len(scan.get("hits") or []),
        "non_synthetic_handle_ids": ids["non_synthetic"],
    }

    all_pass = all(c["pass"] for c in checks.values())
    return {"all_pass": all_pass,
            "n_pass": sum(1 for c in checks.values() if c["pass"]),
            "n_checks": len(checks),
            "checks": checks}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="evidence second-implementation maturity run")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--out", default="")
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args(argv)
    report = run(args.root, args.out or None, write_ledger=not args.no_ledger)
    acc = report["acceptance"]
    print(json.dumps({"all_pass": acc["all_pass"], "n_pass": acc["n_pass"],
                      "n_checks": acc["n_checks"],
                      "injection_rate": acc["checks"]["a_injection_detection"]["overall_rate"],
                      "false_alarm_count": acc["checks"]["b_false_alarms"]["false_alarm_count"],
                      "real_before": report["independence"]["real_before"],
                      "real_after": report["independence"]["real_after"],
                      "ledger_seconds": acc["checks"]["e_ledger_rollback"]["update_seconds"]},
                     ensure_ascii=False, indent=1))
    return 0 if acc["all_pass"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())