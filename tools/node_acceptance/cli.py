"""Command line interface for the node-acceptance engine.

    python -m tools.node_acceptance scan  /path/to/work/m12345
    python -m tools.node_acceptance parse "agreement >= 4/5" "耗时 <= 30s"
    python -m tools.node_acceptance judge /path/to/work/m12345 --criteria-json criteria.json
    python -m tools.node_acceptance report /path/to/work/m12345 --out-dir artifacts/node_acceptance
    python -m tools.node_acceptance cross-validate /path/to/work/m12345 /path/to/work/m12346
    python -m tools.node_acceptance self-audit --work-root /root/tp-agent/work

Every command prints JSON on stdout so results can be piped into a ledger; the
report command additionally writes ``<node>_acceptance.json`` and
``<node>_acceptance.md``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (
    build_acceptance_report,
    cross_validate,
    discover_all,
    evaluate_node,
    load_lexicon,
    parse_criteria,
    render_markdown,
    scan_node_dir,
    summarise,
)
from .aggregator import aggregate


def _load_criteria(path: str | None):
    if path is None:
        return None
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(doc, dict):
        doc = doc.get("criteria") or doc.get("acceptance_criteria") or [doc]
    return doc


def cmd_scan(args) -> int:
    ev = scan_node_dir(args.node, node_id=args.node_id)
    print(json.dumps(ev.as_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_parse(args) -> int:
    lex = load_lexicon()
    crits = parse_criteria(args.text, source="cli", lexicon=lex)
    print(json.dumps([c.as_dict() for c in crits], ensure_ascii=False, indent=2))
    return 0


def cmd_judge(args) -> int:
    lex = load_lexicon()
    evidence = scan_node_dir(args.node, node_id=args.node_id, lex=lex)
    items = _load_criteria(args.criteria_json)
    crits = discover_all(evidence, lex) if items is None else parse_criteria(items, source="cli", lexicon=lex)
    verdict = aggregate(evidence, crits, lex)
    check = cross_validate(verdict, evidence)
    print(json.dumps({"verdict": verdict.as_dict(), "cross_validation": check.as_dict()},
                     ensure_ascii=False, indent=2))
    return 0 if verdict.verdict.value != "FAIL" else 1


def cmd_report(args) -> int:
    verdict, report, check = evaluate_node(args.node, _load_criteria(args.criteria_json), node_id=args.node_id)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    node = verdict.node_id
    (out / f"{node}_acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
    (out / f"{node}_acceptance.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"verdict": verdict.verdict.value, "counts": verdict.counts,
                      "agreement": check.agreement,
                      "json": str(out / f"{node}_acceptance.json"),
                      "markdown": str(out / f"{node}_acceptance.md")}, ensure_ascii=False, indent=2))
    return 0


def cmd_cross_validate(args) -> int:
    checks = []
    for node in args.nodes:
        verdict, _ = None, None
        v, _, check = evaluate_node(node)
        checks.append(check)
    print(json.dumps(summarise(checks), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m tools.node_acceptance", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="scan a node artifact directory")
    p.add_argument("node")
    p.add_argument("--node-id", default=None)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("parse", help="parse criterion text")
    p.add_argument("text", nargs="+")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("judge", help="machine verdict for one node")
    p.add_argument("node")
    p.add_argument("--criteria-json", default=None, help="explicit criteria file (else discovered)")
    p.add_argument("--node-id", default=None)
    p.set_defaults(func=cmd_judge)

    p = sub.add_parser("report", help="write the six-element acceptance report")
    p.add_argument("node")
    p.add_argument("--criteria-json", default=None)
    p.add_argument("--node-id", default=None)
    p.add_argument("--out-dir", default="artifacts/node_acceptance")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("cross-validate", help="compare machine verdicts against human reports")
    p.add_argument("nodes", nargs="+")
    p.set_defaults(func=cmd_cross_validate)

    p = sub.add_parser("self-audit", help="run the predeclared self-audit protocol")
    p.add_argument("--work-root", default="/root/tp-agent/work")
    p.add_argument("--out", default="artifacts/node_acceptance")
    p.add_argument("--nodes", nargs="*", default=None)
    p.set_defaults(func=lambda a: _self_audit(a))
    return ap


def _self_audit(args) -> int:
    from .self_audit import run

    audit = run(args.work_root, Path(args.out), nodes=args.nodes)
    print(json.dumps({"gates": audit["gates"], "cross_validation": audit["cross_validation"]},
                     ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())