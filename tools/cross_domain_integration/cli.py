"""CLI: 一次跑完 汇总 → 契约 → 基线 → 判据 → 图 → 报告。

用法::

    python -m tools.cross_domain_integration.cli \
        --manifest artifacts/<campaign>/manifest.json \
        --out artifacts/<campaign>_report.json \
        --figures-dir artifacts/figures \
        --doc docs/<REPORT>.md \
        [--work-root <root overriding manifest scan.work_root>] \
        [--generated-utc 2026-11-10T14:30:00Z] [--strict]

退出码: 0 = 门跑完(判据可 FAIL，报告如实记录)；2 = manifest/工件读失败；
3 = --strict 且判据未全满足。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .criteria import finalize
from .figures import fig_baseline, fig_completeness, fig_contract
from .gate import run_gate, seal
from .ingest import IngestError
from .manifest import ManifestError, load_manifest
from .provenance import write_json


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--figures-dir", default=None)
    ap.add_argument("--doc", default=None)
    ap.add_argument("--work-root", default=None)
    ap.add_argument("--generated-utc", default=None)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
    except (ManifestError, OSError, ValueError) as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 2
    work_root = args.work_root or manifest["scan"]["work_root"]
    generated = args.generated_utc
    if not generated:
        from datetime import datetime, timezone
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    doc_path = args.doc or manifest["doc"].get("path")
    try:
        body = run_gate(manifest, work_root, generated,
                        report_path=args.out, doc_path=doc_path)
    except (IngestError, OSError) as exc:
        print(f"gate error: {exc}", file=sys.stderr)
        return 2

    figures = []
    if args.figures_dir:
        fdir = Path(args.figures_dir)
        campaign = manifest["campaign"]["key"]
        figures.append(fig_completeness(body["nodes"], fdir / f"cdi_{campaign}_completeness.png"))
        figures.append(fig_contract(body["contract"], fdir / f"cdi_{campaign}_contract.png"))
        baseline_label = (manifest["campaign"]["baseline"] or {}).get("key", "baseline")
        figures.append(fig_baseline(body["baseline"]["quantities"], baseline_label,
                                    fdir / f"cdi_{campaign}_baseline.png"))
        body["figures"] = [{"name": f["name"], "path": f["path"],
                            "sha256": f["sha256"]} for f in figures]

    finalize(body, args.out, figures)
    sealed = seal(body, figures)
    sha = write_json(args.out, sealed)
    print(f"report: {args.out} (content sha256={sha})")
    print(f"verdict: {body['criteria']['verdict']} "
          f"passed={body['criteria']['passed']}")
    for key, crit in body["criteria"]["criteria"].items():
        state = ("N/A" if crit.get("applicable") is False
                 else ("PASS" if crit["pass"] else "FAIL"))
        print(f"  {key}: {state} — {crit['detail']}")
    if args.strict and not body["criteria"]["all_pass"]:
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())