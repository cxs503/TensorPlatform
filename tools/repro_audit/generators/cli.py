"""repro_audit.generators.cli —— 生成器命令行接口。

用法::

    # 盘点生成器覆盖度
    python3 -m tools.repro_audit.generators.cli --inventory --work-root /root/tp-agent/work --n 36
    
    # 运行单个生成器
    python3 -m tools.repro_audit.generators.cli --run tools/repro_audit/generators/gen_benchmark_validation.py
    
    # 入库门检查
    python3 -m tools.repro_audit.generators.cli --gate validation_report.json
    
    # 列出所有生成器
    python3 -m tools.repro_audit.generators.cli --list
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import inventory, gate, maturity, run_generator


def cmd_list(args):
    """列出所有生成器。"""
    gen_dir = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "repro_audit", "generators")
    if not os.path.isdir(gen_dir):
        gen_dir = os.path.dirname(__file__)
    
    generators = []
    for fname in sorted(os.listdir(gen_dir)):
        if fname.startswith("gen_") and fname.endswith(".py"):
            fpath = os.path.join(gen_dir, fname)
            # 提取 OUTPUT_PATH
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    source = f.read()
                import re
                m = re.search(r'OUTPUT_PATH\s*=\s*["\']([^"\']+)["\']', source)
                output_path = m.group(1) if m else "unknown"
            except Exception:
                output_path = "unknown"
            
            generators.append({
                "file": fname,
                "output_path": output_path
            })
    
    print(json.dumps({"generators": generators, "count": len(generators)}, indent=2))


def cmd_inventory(args):
    """盘点生成器覆盖度。"""
    inv = inventory.scan_inventory(args.work_root, n_sample=args.n, seed=args.seed)
    print(json.dumps(inv, indent=2))
    
    if args.gaps:
        gaps = inventory.report_gaps(inv)
        print("\n--- Gaps ---")
        print(json.dumps(gaps, indent=2))


def cmd_run(args):
    """运行单个生成器。"""
    result = run_generator(args.generator, output_path=args.output)
    print(json.dumps(result, indent=2))


def cmd_gate(args):
    """入库门检查。"""
    g = gate.IngestionGate(args.repo)
    
    if args.artifact:
        result = g.check_artifact(args.artifact, rel_tol=args.tol)
    else:
        result = g.check_report(args.report, rel_tol=args.tol)
    
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["passed"] else 1)


def cmd_channels(args):
    """数值通道：审计 / 入库门 / 基线（maturity 模块的统一入口）。"""
    if args.gate:
        result = maturity.gate(args.repo, args.ledger, args.min_coverage,
                               args.min_grade, args.total_required)
    elif args.baseline:
        base = maturity.legacy_baseline(args.repo)
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(base, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        result = {"baseline": args.out, "grades": base["grades"],
                  "coverage": base["coverage"]}
    else:
        ledger = maturity.audit(args.repo)
        maturity.write_ledger(ledger, args.out)
        manifest_out = os.path.join(os.path.dirname(args.out), "channels.json")
        maturity.write_manifest(manifest_out)
        result = {"ledger": args.out, "manifest": manifest_out,
                  "coverage": ledger["coverage"], "grades": ledger["grades"]}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.gate and not result.get("passed"):
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Generator CLI")
    subparsers = parser.add_subparsers(dest="command")
    
    # list
    p_list = subparsers.add_parser("list", help="List all generators")
    p_list.set_defaults(func=cmd_list)
    
    # inventory
    p_inv = subparsers.add_parser("inventory", help="Scan generator coverage")
    p_inv.add_argument("--work-root", default="/root/tp-agent/work", help="Work root directory")
    p_inv.add_argument("--n", type=int, default=36, help="Number of missions to sample")
    p_inv.add_argument("--seed", default="repro-audit", help="Random seed")
    p_inv.add_argument("--gaps", action="store_true", help="Report gaps")
    p_inv.set_defaults(func=cmd_inventory)
    
    # run
    p_run = subparsers.add_parser("run", help="Run a generator")
    p_run.add_argument("generator", help="Generator module path")
    p_run.add_argument("--output", "-o", help="Output file path")
    p_run.set_defaults(func=cmd_run)
    
    # gate
    p_gate = subparsers.add_parser("gate", help="Ingestion gate check")
    p_gate.add_argument("report", nargs="?", help="Report file to check")
    p_gate.add_argument("--artifact", help="Single artifact to check")
    p_gate.add_argument("--repo", default=".", help="Repository root")
    p_gate.add_argument("--tol", type=float, default=1e-6, help="Relative tolerance")
    p_gate.set_defaults(func=cmd_gate)

    # channels：数值通道成熟度（审计 / 门 / 基线）
    p_ch = subparsers.add_parser(
        "channels", help="Numeric-channel maturity: audit / gate / baseline")
    p_ch.add_argument("--gate", action="store_true", help="fail-closed 入库门")
    p_ch.add_argument("--baseline", action="store_true",
                      help="给历史 gen_*.py 注册表评级（基线）")
    p_ch.add_argument("--repo", default=".", help="Repository root")
    p_ch.add_argument("--out", default=maturity.DEFAULT_OUT, help="台账/基线输出")
    p_ch.add_argument("--ledger", default=maturity.DEFAULT_LEDGER,
                      help="入库门读取的台账")
    p_ch.add_argument("--min-coverage", type=int, default=24)
    p_ch.add_argument("--min-grade", default="L3", choices=sorted(maturity.GRADE_ORDER))
    p_ch.add_argument("--total-required", type=int, default=None)
    p_ch.set_defaults(func=cmd_channels)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    args.func(args)


if __name__ == "__main__":
    main()
