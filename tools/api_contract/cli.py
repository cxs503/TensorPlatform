"""CLI for the OpenAPI contract-testing harness.

Usage (all configuration is data, never code)::

    python -m tools.api_contract.cli run   --config runs/<id>/contract_config.json
    python -m tools.api_contract.cli diff  --base old.json --head new.json [--fail-on-breaking]
    python -m tools.api_contract.cli mutations --spec live.json

Exit codes: 0 = criteria pass or degraded (WITHHELD), 1 = a criterion failed
(only when ``--strict`` is given for ``run``, and always for a breaking ``diff``
with ``--fail-on-breaking``).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from typing import Any, Dict, List, Optional, Sequence

from . import breaking, checks, generate, report, runner, spec as spec_mod, targets


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_json(path: Optional[str], default: Any) -> Any:
    if not path:
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_list(path: Optional[str], key: str) -> List[Any]:
    """Load a list, accepting either a bare list or ``{"<key>": [...]}``."""
    payload = _load_json(path, [])
    if isinstance(payload, dict):
        return list(payload.get(key) or [])
    return list(payload or [])


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _build_target(cfg: Dict[str, Any]) -> Any:
    kind = cfg.get("kind", "http")
    if kind == "http":
        return targets.HttpTarget(
            base_url=cfg["base_url"],
            timeout=float(cfg.get("timeout", 20.0)),
            retries=int(cfg.get("retries", 2)),
            health_path=cfg.get("health_path", ""),
            recovery_wait=float(cfg.get("recovery_wait", 60.0)),
            headers=cfg.get("headers") or {},
        )
    if kind == "asgi":
        for entry in cfg.get("sys_path") or []:
            if entry not in sys.path:
                sys.path.insert(0, entry)
        app, module = targets.load_asgi_app(cfg["app"])
        _log("[target] asgi app %s loaded from %s" % (cfg["app"], module))
        return targets.ASGITarget(app, timeout=float(cfg.get("timeout", 120.0)))
    raise ValueError("unknown target kind: %r" % kind)


def _spec_from_target(cfg: Dict[str, Any], spec_cfg: Dict[str, Any]) -> spec_mod.SpecLoad:
    """Load the spec, optionally straight from an in-process ASGI app."""
    source = spec_cfg.get("source") or ""
    if source == "app" or spec_cfg.get("from_app"):
        loader = _build_target({**cfg, "kind": "asgi", **(spec_cfg.get("app_config") or {})})
        try:
            app = getattr(loader, "_client", None)
            raw_app = cfg.get("app")
            for entry in cfg.get("sys_path") or []:
                if entry not in sys.path:
                    sys.path.insert(0, entry)
            app_obj, module = targets.load_asgi_app(spec_cfg.get("app") or raw_app)
            document = app_obj.openapi()
            path = spec_cfg.get("dump") or ""
            if path:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(document, fh)
                return spec_mod.load_spec(path)
            return spec_mod.SpecLoad(spec_mod.STATUS_OK, spec=document, source="asgi:%s" % module)
        finally:
            close = getattr(loader, "close", None)
            if callable(close):
                close()
    return spec_mod.load_spec(source)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load_json(args.config, {})
    out_dir = cfg.get("out") or args.out or "runs/api_contract"
    os.makedirs(out_dir, exist_ok=True)
    spec_cfg = cfg.get("spec") or {}
    target_cfg = cfg.get("target") or {}

    load = _spec_from_target(target_cfg, spec_cfg)
    _log("[spec] %s" % json.dumps(load.as_dict(), ensure_ascii=False))
    spec_doc = load.spec

    # ---- degradation path: no usable spec -> WITHHELD, exit 0 ------------
    if not load.ok:
        acceptance = report.evaluate({
            "coverage": {"state": "WITHHELD", "operations_in_spec": 0},
            "schema": {"state": "WITHHELD"},
            "matrix": {"state": "WITHHELD"},
            "breaking": {"injected": 0},
            "precision": {"state": "WITHHELD"},
            "negatives": {"all_degraded": True,
                          "controls": {"spec": {"state": load.status, "reason": load.reason}}},
        })
        acceptance["spec"] = load.as_dict()
        acceptance["summary"]["all_pass"] = False
        acceptance["degraded"] = True
        report.write_json(os.path.join(out_dir, "acceptance.json"), acceptance)
        _log("[degraded] spec state=%s reason=%s -> WITHHELD, exiting 0" % (load.status, load.reason))
        return 0

    overrides = _load_json(cfg.get("overrides"), {})
    matrix_cfg = _load_list(cfg.get("matrix"), "endpoints")
    references = _load_list(cfg.get("references"), "references")
    sensitivity = _load_list(cfg.get("sensitivity"), "probes")
    checkpoints = cfg.get("checkpoints") or {}

    target = _build_target(target_cfg)
    started = time.time()
    try:
        # ---- (a)(b) case sweep -------------------------------------------
        cases = generate.generate_all(spec_doc, overrides, include_invalid=True)
        cap = int((cfg.get("caps") or {}).get("max_cases") or 0)
        if cap:
            cases = cases[:cap]
        _log("[cases] generated %d cases over %d operations"
             % (len(cases), len({case.operation for case in cases})))

        def progress(index: int, total: int, row: Dict[str, Any]) -> None:
            if index % 25 == 0 or index == total:
                status = row["response"].get("status") or row["response"].get("error")
                _log("[cases] %d/%d %s -> %s" % (index, total, row["case_id"][:70], status))

        rows = runner.run_cases(target, spec_doc, cases,
                                checkpoint_path=checkpoints.get("cases"),
                                resume=bool(cfg.get("resume")),
                                sleep_s=float((cfg.get("caps") or {}).get("sleep_s") or 0.0),
                                progress=progress)
        _log("[cases] finished %d rows in %.1fs" % (len(rows), time.time() - started))

        # ---- (c) status matrix ------------------------------------------
        matrix = None
        if matrix_cfg:
            matrix = runner.run_matrix(target, spec_doc, matrix_cfg,
                                       (cfg.get("matrix_scenarios") or generate.MATRIX_SCENARIOS))
            _log("[matrix] %d/%d conforming" % (matrix["conforming"], matrix["executed"]))

        # ---- (e) numeric invariants + determinism ------------------------
        references_result = runner.run_references(target, spec_doc, references) if references else None
        if references_result:
            _log("[references] %d/%d checks passed" % (references_result["passed"],
                                                       references_result["checks"]))
        det_cfg = cfg.get("determinism") or {}
        determinism = None
        if det_cfg.get("cases"):
            sample = [case for case in cases if case.kind == "valid"][: int(det_cfg["cases"])]
            determinism = runner.run_determinism(target, sample,
                                                 repeats=int(det_cfg.get("repeats", 2)))
            _log("[determinism] %d/%d deterministic" % (determinism["deterministic"],
                                                        determinism["checked"]))

        # ---- sensitivity / MOCK probe ------------------------------------
        sensitivity_result = runner.run_sensitivity(target, spec_doc, sensitivity) if sensitivity else None
        if sensitivity_result:
            _log("[sensitivity] mock_suspect=%d" % sensitivity_result["mock_suspect"])
    finally:
        close = getattr(target, "close", None)
        if callable(close):
            close()

    # ---- (d) incompatible-change detection -------------------------------
    mutation_result = breaking.verify_mutations(spec_doc)
    drift = None
    baseline_path = spec_cfg.get("baseline")
    if baseline_path and os.path.exists(baseline_path):
        baseline = _load_json(baseline_path, {})
        drift_changes = breaking.diff_specs(baseline, spec_doc)
        drift = {"baseline": baseline_path, "changes": drift_changes,
                 "summary": breaking.summarise(drift_changes)}
        _log("[drift] %d changes (%d breaking) vs baseline"
             % (drift["summary"]["total"], drift["summary"]["breaking"]))

    # ---- (f) negative controls ------------------------------------------
    neg_cfg = cfg.get("negative_controls") or {}
    negatives = runner.negative_controls(
        spec_source_missing=neg_cfg.get("spec_missing", os.path.join(out_dir, "__absent__.json")),
        spec_source_malformed=neg_cfg.get("spec_malformed", ""),
        unreachable_base_url=neg_cfg.get("unreachable_base_url", ""),
        health_path=neg_cfg.get("health_path", ""),
    )
    _log("[negative] all_degraded=%s" % negatives["all_degraded"])

    # ---- assemble acceptance -------------------------------------------
    coverage = report.endpoint_coverage(spec_doc, rows)
    schema = report.schema_criteria(rows)
    precision = report.precision_criterion(references_result, determinism, rows)
    matrix_eval = report.matrix_criterion(matrix, len(matrix_cfg) * len(generate.MATRIX_SCENARIOS))
    acceptance = report.evaluate({
        "coverage": coverage,
        "schema": schema,
        "matrix": matrix_eval,
        "breaking": mutation_result,
        "precision": precision,
        "negatives": negatives,
    })

    findings = {
        "invalid_not_rejected": schema.get("invalid_not_rejected") or [],
        "error_envelope_drift": schema.get("error_envelope_violations") or [],
        "spec_underspecified": schema.get("spec_underspecified") or [],
        "numeric_diagnostics": precision.get("findings") or [],
        "mock_suspects": [row["path"] for row in (sensitivity_result or {}).get("results", [])
                          if row["verdict"] == "MOCK_SUSPECT"],
    }
    acceptance["findings"] = findings
    acceptance["harness_capability"] = {
        "operation_coverage": coverage,
        "mutation_detection": {"injected": mutation_result["injected"],
                               "detected": mutation_result["detected"]},
        "negative_controls": negatives,
        "sensitivity_probes": (sensitivity_result or {}).get("probes", 0),
    }
    payload = {
        "spec": load.as_dict(),
        "findings": findings,
        "spec_fingerprint": spec_mod.spec_fingerprint(spec_doc),
        "target": {k: v for k, v in target_cfg.items() if k != "headers"},
        "runtime_seconds": round(time.time() - started, 2),
        "cases": rows,
        "matrix": matrix,
        "references": references_result,
        "determinism": determinism,
        "sensitivity": sensitivity_result,
        "mutations": mutation_result,
        "drift": drift,
        "negative_controls": negatives,
        "acceptance": acceptance,
    }
    report.write_json(os.path.join(out_dir, "contract_report.json"), payload)
    report.write_json(os.path.join(out_dir, "acceptance.json"), acceptance)
    report.write_markdown(os.path.join(out_dir, "REPORT.md"), acceptance, {
        "spec_source": load.source, "spec_sha256": load.sha256,
        "target": "%s %s" % (target_cfg.get("kind", "http"), target_cfg.get("base_url", "")),
        "output_dir": out_dir,
    })
    summary = acceptance["summary"]
    _log("[acceptance] pass=%d fail=%d withheld=%d all_pass=%s -> %s"
         % (summary["pass"], summary["fail"], summary["withheld"], summary["all_pass"], out_dir))
    if args.strict and not summary["all_pass"]:
        return 1
    return 0


# ---------------------------------------------------------------------------
# diff / mutations
# ---------------------------------------------------------------------------


def cmd_diff(args: argparse.Namespace) -> int:
    base = _load_json(args.base, {})
    head = _load_json(args.head, {})
    changes = breaking.diff_specs(base, head)
    summary = breaking.summarise(changes)
    payload = {"base": args.base, "head": args.head, "summary": summary, "changes": changes}
    if args.out:
        report.write_json(args.out, payload)
    print(json.dumps({"summary": summary, "changes": changes}, ensure_ascii=False, indent=2))
    if args.fail_on_breaking and summary["breaking"]:
        return 1
    return 0


def cmd_mutations(args: argparse.Namespace) -> int:
    doc = _load_json(args.spec, {})
    result = breaking.verify_mutations(doc)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["detected"] == result["injected"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="api_contract", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the full contract suite from a config file")
    run_p.add_argument("--config", required=True)
    run_p.add_argument("--out", default=None)
    run_p.add_argument("--strict", action="store_true",
                       help="exit 1 when a criterion fails (default: record only)")
    run_p.set_defaults(func=cmd_run)

    diff_p = sub.add_parser("diff", help="compare two OpenAPI documents")
    diff_p.add_argument("--base", required=True)
    diff_p.add_argument("--head", required=True)
    diff_p.add_argument("--out", default=None)
    diff_p.add_argument("--fail-on-breaking", action="store_true")
    diff_p.set_defaults(func=cmd_diff)

    mut_p = sub.add_parser("mutations", help="verify the detector on injected mutations")
    mut_p.add_argument("--spec", required=True)
    mut_p.set_defaults(func=cmd_mutations)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception:  # noqa: BLE001 - never crash the caller: log and fail loudly
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())