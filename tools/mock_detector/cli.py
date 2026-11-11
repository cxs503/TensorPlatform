"""CLI: ``python3 -m tools.mock_detector.cli {scan,repeat,ledger,report,fixtures}``.

Every subcommand writes its artefact under ``--out`` (default
``artifacts/mock_detector``) so the whole chain is re-runnable and the report can
be regenerated from machine data alone.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import autospec, ledger_link, report as report_mod
from .canonical import sha256_text
from .fixtures import run_selfcheck
from .httpc import JsonClient
from .model import LEVELS, Target
from .probes import ProbeConfig, run_probes
from .scanner import scan_targets

DEFAULT_OUT = "artifacts/mock_detector"


def _write(path: Path, data: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _read(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _probe_config(args) -> ProbeConfig:
    cfg = ProbeConfig()
    if getattr(args, "max_axes", None):
        cfg.max_axes = args.max_axes
    if getattr(args, "budget", None):
        cfg.request_budget_per_target = args.budget
    if getattr(args, "deadline", None):
        cfg.target_deadline_s = args.deadline
    return cfg


# --------------------------------------------------------------------------
def _ledger_priority_order(targets: List[Target]) -> List[Target]:
    """Stable re-ordering: ledger-referenced endpoints first, then the rest."""
    ledger = ledger_link.load_ledger(None)
    caps = [c.get("id") for c in (ledger.get("payload") or {}).get("capabilities", [])]
    mapping = ledger_link.mapping_from_adapters()
    wanted_paths = set()
    for cap_id in caps:
        endpoint = mapping.get(cap_id)
        if endpoint:
            wanted_paths.add(endpoint if endpoint.startswith("/") else "/" + endpoint)
    def score(target: Target) -> tuple:
        hit_path = target.path in wanted_paths
        hit_token = any(ledger_link.match_score(c, target.path) >= 0.5 for c in caps if c)
        return (0 if hit_path else (1 if hit_token else 2), target.path)
    return sorted(targets, key=score)


def cmd_scan(args) -> int:
    out = Path(args.out)
    client = JsonClient(args.base_url, default_timeout=args.timeout,
                        api_prefix=args.api_prefix,
                        transport_retries=args.transport_retries)
    if args.from_audit_specs:
        targets = autospec.targets_from_audit_specs(timeout_s=args.timeout)
        spec_source = "tools.capability_audit.adapters::SPECS"
        if not targets:
            print("ERROR: no payloads found in the domain audit registry", file=sys.stderr)
            return 2
    else:
        if args.openapi and Path(args.openapi).is_file():
            spec = autospec.load_spec_file(args.openapi)
            spec_source = args.openapi
        else:
            resp = client.request("GET", "/openapi.json", None, timeout=60)
            if not resp.ok:
                print(f"ERROR: cannot read OpenAPI from {args.base_url}/openapi.json "
                      f"({resp.status} {resp.error})", file=sys.stderr)
                return 2
            spec = resp.payload
            spec_source = f"{args.base_url}/openapi.json"
        # when prioritising, discover a larger candidate pool first: truncating
        # alphabetically *before* prioritising would keep the ledger endpoints out
        pool = args.max_targets * 8 if args.prioritise_ledger else args.max_targets
        targets = autospec.build_targets(spec, include=args.include, exclude=args.exclude,
                                        max_targets=pool, timeout_s=args.timeout)
    if not targets:
        print("ERROR: no targets discovered", file=sys.stderr)
        return 2
    if args.prioritise_ledger and not args.targets:
        # Probe the endpoints the platform's own ledger makes claims about *first*,
        # so the ledger reconciliation is not starved by alphabetical truncation.
        targets = _ledger_priority_order(targets)[:args.max_targets]
    cfg = _probe_config(args)
    scan = scan_targets(client, targets, cfg=cfg, concurrency=args.concurrency,
                        rejected_retries=args.retries, repo_root=args.repo_root,
                        config_sha256=sha256_text(json.dumps(cfg.to_dict(), sort_keys=True)))
    scan.summary["openapi_source"] = spec_source
    scan_path = _write(out / "scan.json", scan.to_dict())
    _write(out / "targets.json", [t.to_dict() for t in targets])
    counts = scan.summary["level_counts"]
    print(f"scanned {len(targets)} targets — " +
          " ".join(f"{k}={counts.get(k, 0)}" for k in LEVELS))
    print(f"scan → {scan_path}")
    return 0


def cmd_fixtures(args) -> int:
    out = Path(args.out)
    res = run_selfcheck(str(out / "fixture_selftest.json"), port=args.port)
    if not args.quiet:
        for r in res["results"]:
            mark = "OK " if r["correct"] else "MISMATCH"
            print(f"{mark:9s} {r['path']:34s} expected={r['expected']:14s} "
                  f"observed={r['observed']:14s} rule={r['fired_rule']}")
        print(f"{res['n_correct']}/{res['n_fixtures']} correct  "
              f"FN={res['false_negatives']} FP={len(res['false_positive_list'])}")
    return 0 if res["n_correct"] == res["n_fixtures"] else 1


def cmd_repeat(args) -> int:
    """Re-scan a sub-sample and compare levels — the reproducibility criterion."""
    scan = _read(args.scan)
    targets_path = Path(args.scan).parent / "targets.json"
    if not targets_path.is_file():
        print(f"ERROR: {targets_path} missing (scan wrote it; older scans did not)",
              file=sys.stderr)
        return 2
    targets: List[Target] = autospec.targets_from_config(str(targets_path))
    by_cap = {t.cap_id: t for t in targets}
    decided = [p for p in scan["probes"] if p["level"] in ("REAL", "MOCK", "STUB")]
    decided.sort(key=lambda p: (p["level"], p["path"]))
    sample = decided[:args.n]
    if args.paths:
        wanted = set(args.paths.split(","))
        sample = [p for p in scan["probes"] if p["path"] in wanted][:args.n]
    client = JsonClient(scan["base_url"], default_timeout=args.timeout,
                        api_prefix=args.api_prefix, transport_retries=1)
    # replay with the *same* probe configuration as the first pass, otherwise the
    # agreement test would compare two different experiments
    saved = scan.get("summary", {}).get("probe_config") or {}
    cfg = ProbeConfig(
        determinism_tol=saved.get("determinism_tol", 1e-9),
        idempotency_tol=saved.get("idempotency_tol", 1e-9),
        invariance_tol=saved.get("invariance_tol", 1e-9),
        sweep_ladder=tuple(saved.get("sweep_ladder") or (0.8, 0.9, 1.0, 1.1, 1.2)),
        max_axes=saved.get("max_axes", 6),
        min_axis_points=saved.get("min_axis_points", 3),
        request_budget_per_target=saved.get("request_budget_per_target", 40),
        move_tol=saved.get("move_tol", 1e-6),
        target_deadline_s=saved.get("target_deadline_s", 30.0),
    )
    compared, agree, disagreements = 0, 0, []
    rows = []
    for probe in sample:
        target = by_cap.get(probe["cap_id"])
        if target is None:
            continue
        r1 = run_probes(client, target, cfg)
        r2 = run_probes(client, target, cfg)
        from .classify import classify
        l1, l2 = classify(r1).level, classify(r2).level
        compared += 1
        same = l1 == probe["level"] and l2 == probe["level"]
        agree += 1 if same else 0
        if not same:
            disagreements.append({"path": probe["path"], "first_pass": probe["level"],
                                  "repeat_a": l1, "repeat_b": l2})
        rows.append({"path": probe["path"], "first_pass": probe["level"],
                     "repeat_a": l1, "repeat_b": l2, "agree": same})
    out = {
        "schema": "mock-detector-repeat/1.0",
        "base_url": scan["base_url"],
        "n_compared": compared,
        "n_agree": agree,
        "agreement": round(agree / compared, 6) if compared else None,
        "disagreements": disagreements,
        "rows": rows,
    }
    path = _write(Path(args.out) / "repeat.json", out)
    print(f"repeat: {agree}/{compared} agree → {path}")
    return 0 if compared and agree == compared else 1


def cmd_ledger(args) -> int:
    scan = _read(args.scan)
    from .model import ProbeReport
    probes = [ProbeReport(**p) for p in scan["probes"]]
    ledger = ledger_link.load_ledger(args.repo_root)
    mapping: Dict[str, str] = {}
    if args.mapping:
        raw = _read(args.mapping)
        mapping.update(raw.get("mapping", raw) if isinstance(raw, dict) else {})
    if not args.no_adapter_mapping:
        for cap_id, endpoint in ledger_link.mapping_from_adapters().items():
            mapping.setdefault(cap_id, endpoint)
    overlay = ledger_link.build_overlay(probes, ledger, mapping)
    out = Path(args.out)
    path = _write(out / "ledger_overlay.json", overlay)
    print(f"ledger: {overlay['flag_counts']} → {path}")
    if overlay["mocked_capability_ids"]:
        print(f"auto-flagged MOCK/STUB overclaims: {overlay['mocked_capability_ids']}")
    if args.apply_to:
        res = ledger_link.apply_overlay(overlay, ledger, args.apply_to)
        print(f"derived ledger written → {res['out_path']} ({res['n_mock_flags']} flags)")
    return 0


def cmd_report(args) -> int:
    out = Path(args.out)
    scan = _read(args.scan)
    overlay_path = Path(args.overlay) if args.overlay else out / "ledger_overlay.json"
    overlay = _read(str(overlay_path)) if Path(overlay_path).is_file() else None
    repeat_path = out / "repeat.json"
    repeat_result = _read(str(repeat_path)) if repeat_path.is_file() else None
    fixture_path = out / "fixture_selftest.json"
    fixture_result = _read(str(fixture_path)) if fixture_path.is_file() else None
    root = Path(args.repo_root)
    leak = report_mod.domain_leak_scan(root)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    figs = report_mod.make_figures(scan, overlay, out / "figures")
    _write(out / "figures_manifest.json",
           [{k: f[k] for k in ("name", "path", "caption")} for f in figs])
    leak_path = _write(out / "domain_leak_scan.json", leak)
    artifacts = {
        "scan_json": str(Path(args.scan)),
        "targets_json": str(Path(args.scan).parent / "targets.json"),
        "overlay_json": str(overlay_path),
        "repeat_json": str(repeat_path),
        "fixture_json": str(fixture_path),
        "leak_json": leak_path,
        "report_md": str(out / "report.md"),
        "criteria_json": str(out / "criteria.json"),
        "figures_dir": str(out / "figures"),
        "figures_manifest": str(out / "figures_manifest.json"),
    }
    criteria = report_mod.build_criteria(scan, overlay, fixture_result, repeat_result,
                                         leak, artifacts)
    _write(out / "criteria.json", criteria)
    injected_path = out / "injected" / "scan.json"
    injected = _read(str(injected_path)) if injected_path.is_file() else None
    if injected:
        criteria["supplementary_injected_scan"] = {
            "path": str(injected_path),
            "n_targets": injected.get("summary", {}).get("n_targets"),
            "level_counts": injected.get("summary", {}).get("level_counts"),
            "note": "payloads injected from the domain audit registry; not part of the "
                    "pre-registered criteria",
        }
        _write(out / "criteria.json", criteria)
    md = report_mod.render_markdown(scan, overlay, criteria, figs, injected)
    (out / "report.md").write_text(md, encoding="utf-8")
    if args.docs:
        docs_path = Path(args.docs)
        docs_path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "<!-- generated by python3 -m tools.mock_detector.cli report: do not hand-edit -->\n"
            f"<!-- source artefacts: {out}/scan.json, criteria.json, ledger_overlay.json -->\n\n")
        docs_path.write_text(header + md, encoding="utf-8")
        print(f"docs  → {docs_path}")
    print(f"report → {out / 'report.md'}  criteria {criteria['n_pass']}/{criteria['n_criteria']}"
          f" all_pass={criteria['all_pass']}")
    for c in criteria["criteria"]:
        print(f"  {c['id']} {'PASS' if c['pass'] else 'FAIL'} {c['threshold']} :: "
              f"{json.dumps(c['value'], ensure_ascii=False)[:160]}")
    return 0 if criteria["all_pass"] else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mock_detector", description=__doc__)
    ap.add_argument("--repo-root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="probe OpenAPI-discovered endpoints")
    s.add_argument("--base-url", default="http://127.0.0.1:8002")
    s.add_argument("--out", default=DEFAULT_OUT)
    s.add_argument("--api-prefix", default="/api/v1")
    s.add_argument("--openapi", default=None, help="local OpenAPI json (default: fetch)")
    s.add_argument("--targets", default=None, help="explicit target config json")
    s.add_argument("--from-audit-specs", action="store_true",
                   help="take payloads from the domain capability-audit registry")
    s.add_argument("--include", default=None, help="regex on path to include")
    s.add_argument("--exclude", default=None, help="regex on path to exclude")
    s.add_argument("--max-targets", type=int, default=120)
    s.add_argument("--prioritise-ledger", action="store_true",
                   help="probe endpoints referenced by the capability ledger first")
    s.add_argument("--concurrency", type=int, default=8)
    s.add_argument("--timeout", type=float, default=20.0)
    s.add_argument("--retries", type=int, default=2)
    s.add_argument("--transport-retries", type=int, default=1,
                   help="retries on timeout/5xx (noise reduction, not verdict shopping)")
    s.add_argument("--deadline", type=float, default=30.0, help="wall-clock cap per target (s)")
    s.add_argument("--max-axes", type=int, default=6)
    s.add_argument("--budget", type=int, default=40)
    s.set_defaults(func=cmd_scan)

    f = sub.add_parser("fixtures", help="run the synthetic known-answer self-check")
    f.add_argument("--out", default=DEFAULT_OUT)
    f.add_argument("--port", type=int, default=0)
    f.add_argument("--quiet", action="store_true")
    f.set_defaults(func=cmd_fixtures)

    r = sub.add_parser("repeat", help="re-scan a sub-sample (reproducibility)")
    r.add_argument("--scan", default=f"{DEFAULT_OUT}/scan.json")
    r.add_argument("--out", default=DEFAULT_OUT)
    r.add_argument("--n", type=int, default=8)
    r.add_argument("--paths", default=None)
    r.add_argument("--api-prefix", default="/api/v1")
    r.add_argument("--timeout", type=float, default=20.0)
    r.set_defaults(func=cmd_repeat)

    l = sub.add_parser("ledger", help="reconcile with the capability ledger")
    l.add_argument("--scan", default=f"{DEFAULT_OUT}/scan.json")
    l.add_argument("--out", default=DEFAULT_OUT)
    l.add_argument("--mapping", default=None, help="cap_id → endpoint json")
    l.add_argument("--apply-to", default=None, help="write derived ledger copy here")
    l.add_argument("--no-adapter-mapping", action="store_true")
    l.set_defaults(func=cmd_ledger)

    p = sub.add_parser("report", help="figures + criteria.json + markdown")
    p.add_argument("--scan", default=f"{DEFAULT_OUT}/scan.json")
    p.add_argument("--overlay", default=None)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--docs", default=None, help="also write the markdown here (docs/)")
    p.set_defaults(func=cmd_report)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":               # pragma: no cover
    raise SystemExit(main())