"""CLI：``python3 -m tools.capability_audit_gate.cli <command>``。

命令
----
``audit``   跑审计（可选基线回归判定 / 验收判据 / Markdown+JSON 报告）
``freeze``  把审计结果冻结成基线（回归防护的参照点）
``validate``只跑一致率校验（要求 spec 带 ``expected_level``，即标注语料）
``drill``   回归防护演练：逐个把 REAL 能力注入降级，要求 gate 100% 检出（C3）

退出码
------
0 通过 ｜ 1 回归违规（CI 红）｜ 3 验收判据未达 ｜ 4 spec/基线/ack 配置错误
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - 允许直接执行
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.capability_audit_gate import (drill as drill_mod, engine, gate as gate_mod,
                                         report as report_mod, spec as spec_mod)

EXIT_OK, EXIT_GATE, EXIT_ACCEPT, EXIT_CONFIG = 0, 1, 3, 4


def _build(args):
    if args.registry:
        return spec_mod.spec_from_registry(args.registry)
    return spec_mod.load_spec(args.spec)


def cmd_audit(args) -> int:
    spec = _build(args)
    contrast = engine.load_contrast(args.contrast, args.contrast_key) if args.contrast else None
    rep = engine.run_audit(spec, root=args.root, cache_path=args.cache, contrast=contrast)
    gate_result = None
    if args.baseline:
        gate_result = gate_mod.compare(rep, gate_mod.load_baseline(args.baseline),
                                       gate_mod.load_ack(args.ack))
    checks = None
    if not args.no_acceptance:
        checks = engine.acceptance(rep, min_labeled=args.min_labeled,
                                   min_agreement=args.min_agreement,
                                   max_runtime=args.max_runtime)
    paths = report_mod.write_reports(rep, args.out, gate=gate_result, checks=checks)
    print(f"audited={rep['summary']['audited']}/{rep['summary']['capabilities']} "
          f"coverage={rep['summary']['coverage']:.3f} "
          f"runtime={rep['summary']['runtime_sec']}s "
          f"cache_hit={rep['summary']['cache']['hit_rate']:.2f} "
          f"levels={rep['summary']['level_tally']} "
          f"agreement={rep['validation'].get('agreement')} "
          f"gate={'FAIL' if gate_result and gate_result['fail'] else 'PASS' if gate_result else 'n/a'} "
          f"acceptance={'PASS' if checks and checks['all_pass'] else 'FAIL' if checks else 'n/a'}")
    print(f"report: {paths['report_json']} ; {paths['report_md']}")
    if gate_result and gate_result["fail"] and args.fail_on_gate:
        for row in gate_result["violations"]:
            print(f"  VIOLATION {row['id']}: {row['baseline']} -> {row['current']} ({row['change']})",
                  file=sys.stderr)
        return EXIT_GATE
    if checks and not checks["all_pass"] and args.enforce_acceptance:
        for k, c in checks.items():
            if k != "all_pass" and not c["pass"]:
                print(f"  ACCEPTANCE {k}: value={c['value']} threshold={c['threshold']}",
                      file=sys.stderr)
        return EXIT_ACCEPT
    return EXIT_OK


def cmd_freeze(args) -> int:
    spec = _build(args)
    rep = engine.run_audit(spec, root=args.root, cache_path=args.cache)
    base = gate_mod.baseline_from_report(rep)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(base, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"frozen {len(base['levels'])} capabilities -> {out} "
          f"(ruleset={base['ruleset_version']})")
    return EXIT_OK


def cmd_drill(args) -> int:
    """回归防护演练：逐个把 REAL 能力做成 MOCK，要求 gate 100% 检出（C3 的机器证据）。"""
    spec = _build(args)
    result = drill_mod.run_drill(spec, root=args.root, target_level=args.target_level,
                                 out_dir=args.out)
    print(f"drill targets={result['targets']} detected={result['detected']} "
          f"rate={result['detection_rate']} worktree_unchanged={result['worktree_unchanged']} "
          f"runtime={result['runtime_sec']}s pass={result['pass']}")
    if result.get("note"):
        print(f"  note: {result['note']}", file=sys.stderr)
    if not result["pass"]:
        for cid in result["missed"]:
            print(f"  MISSED {cid}: injected downgrade not detected", file=sys.stderr)
        return EXIT_CONFIG if not result["targets"] else EXIT_GATE
    return EXIT_OK


def cmd_validate(args) -> int:
    spec = _build(args)
    rep = engine.run_audit(spec, root=args.root, cache_path=args.cache)
    v = rep["validation"]
    if v.get("agreement") is None:
        print("spec has no expected_level labels: cannot validate agreement", file=sys.stderr)
        return EXIT_CONFIG
    print(json.dumps({k: v[k] for k in ("labeled", "agreement", "majority_baseline",
                                        "agreement_gain", "disagreements")},
                     ensure_ascii=False, indent=2))
    return EXIT_OK if v["agreement"] >= args.min_agreement else EXIT_ACCEPT


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="能力台账自动审计 + 回归防护")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("audit", "freeze", "validate", "drill"):
        p = sub.add_parser(name)
        src = p.add_mutually_exclusive_group(required=True)
        src.add_argument("--spec", help="审计 spec JSON")
        src.add_argument("--registry", help="能力定义注册表（config/capabilities.json）")
        p.add_argument("--root", default=".", help="仓库根（默认 cwd）")
        p.add_argument("--out", default=("artifacts/capability_audit_gate/baseline.json"
                                         if name == "freeze"
                                         else "artifacts/capability_audit_gate"))
        p.add_argument("--cache", default=None, help="增量扫描缓存路径（缺省不落盘）")
        p.add_argument("--min-agreement", type=float, default=0.9)
        if name == "drill":
            p.add_argument("--target-level", default="REAL",
                           help="被注入降级的目标等级（默认 REAL）")
        if name == "audit":
            p.add_argument("--baseline", default=None)
            p.add_argument("--ack", default=None)
            p.add_argument("--fail-on-gate", action="store_true")
            p.add_argument("--enforce-acceptance", action="store_true")
            p.add_argument("--no-acceptance", action="store_true")
            p.add_argument("--contrast", default=None, help="外部真值台账（逐能力等级）")
            p.add_argument("--contrast-key", default="reconciled_level")
            p.add_argument("--min-labeled", type=int, default=10)
            p.add_argument("--max-runtime", type=float, default=60.0)
    args = ap.parse_args(argv)
    try:
        return {"audit": cmd_audit, "freeze": cmd_freeze, "validate": cmd_validate,
                "drill": cmd_drill}[args.cmd](args)
    except (spec_mod.SpecError, gate_mod.BaselineError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())