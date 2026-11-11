"""repro_audit.generators.contract_cli —— 生成器契约审计 / 入库门 / 基线 / 负控 CLI。

    # 1) 审计：干净检出 → 重放 → 幂等 → 篡改取证 → 台账
    python3 -m tools.repro_audit.generators.contract_cli --audit \\
        --specs artifacts/repro_audit/generator_specs.json \\
        --out artifacts/repro_audit/generator_coverage.json

    # 2) 入库门（fail-closed）：覆盖 < 24 或 等级 < G2 或 台账陈旧 → exit 1
    python3 -m tools.repro_audit.generators.contract_cli --gate \\
        --ledger artifacts/repro_audit/generator_coverage.json \\
        --specs artifacts/repro_audit/generator_specs.json \\
        --min-coverage 24 --min-grade G2

    # 3) 历史 gen_*.py 同口径基线（"改造前"红基线）
    python3 -m tools.repro_audit.generators.contract_cli --legacy-baseline \\
        --out artifacts/repro_audit/generator_baseline_legacy.json

    # 4) 负控三刀（门禁效力实证）
    python3 -m tools.repro_audit.generators.contract_cli --negcontrols \\
        --out artifacts/repro_audit/negcontrols.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

from . import contract, negcontrols

__all__ = ["main"]


def _dump(obj, path: str | None) -> None:
    body = json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
    else:
        sys.stdout.write(body)


def cmd_audit(args) -> int:
    ledger = contract.build_ledger(
        args.repo, args.specs, ref=args.ref, idempotency=not args.no_idempotency,
        contract_evidence=not args.no_contract_evidence)
    _dump(ledger, args.out)
    print(f"coverage {ledger['coverage']['mature']}/{ledger['coverage']['total']} "
          f"(independent {ledger['coverage']['independent']}) grades={ledger['grades']}",
          file=sys.stderr)
    return 0


def cmd_gate(args) -> int:
    with open(args.ledger, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)
    freshness = contract.ledger_freshness(ledger, args.repo, args.specs or None)
    verdict = contract.gate(ledger, min_coverage=args.min_coverage,
                            min_grade=args.min_grade, freshness=freshness)
    _dump(verdict, args.out)
    if not verdict["passed"]:
        print("GATE REJECTED: " + "; ".join(verdict["reasons"]), file=sys.stderr)
        return 1
    print("GATE PASSED", file=sys.stderr)
    return 0


def cmd_legacy_baseline(args) -> int:
    specs = contract.legacy_specs(args.repo)
    tmp = os.path.join(tempfile.mkdtemp(prefix="legacy_specs_"), "specs.json")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(specs, fh)
    ledger = contract.build_ledger(args.repo, tmp, ref=args.ref,
                                   idempotency=False, contract_evidence=False)
    ledger["baseline_of"] = "tools/repro_audit/generators/gen_*.py"
    ledger["baseline_note"] = ("同一把尺子量改造前：历史 gen_*.py 是实现「简化模型」的复算桩，"
                               "重放值对不上工件即落 G1；此文件是 before 基线，不入库门。")
    _dump(ledger, args.out)
    print(f"legacy baseline coverage {ledger['coverage']['mature']}/"
          f"{ledger['coverage']['total']} grades={ledger['grades']}", file=sys.stderr)
    return 0


def cmd_negcontrols(args) -> int:
    report = negcontrols.run_controls()
    _dump(report, args.out)
    return 0 if report["all_ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="生成器契约审计 / 入库门 / 基线 / 负控")
    ap.add_argument("--repo", default=".", help="仓库根（默认当前目录）")
    ap.add_argument("--ref", default="HEAD", help="干净检出用的 git ref（默认 HEAD）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    # 子命令名：audit / gate / legacy-baseline / negcontrols（接受 --audit 形态）

    a = sub.add_parser("--audit".lstrip("-"), help="跑完整审计并写台账")
    a.add_argument("--specs", default="artifacts/repro_audit/generator_specs.json")
    a.add_argument("--out", default=None)
    a.add_argument("--no-idempotency", action="store_true")
    a.add_argument("--no-contract-evidence", action="store_true")
    a.set_defaults(func=cmd_audit)

    g = sub.add_parser("--gate".lstrip("-"), help="fail-closed 入库门")
    g.add_argument("--ledger", default="artifacts/repro_audit/generator_coverage.json")
    g.add_argument("--specs", default=None)
    g.add_argument("--min-coverage", type=int, default=24)
    g.add_argument("--min-grade", default="G2")
    g.add_argument("--out", default=None)
    g.set_defaults(func=cmd_gate)

    b = sub.add_parser("--legacy-baseline".lstrip("-"), help="历史 gen_*.py 同口径基线")
    b.add_argument("--out", default=None)
    b.set_defaults(func=cmd_legacy_baseline)

    n = sub.add_parser("--negcontrols".lstrip("-"), help="负控三刀")
    n.add_argument("--out", default=None)
    n.set_defaults(func=cmd_negcontrols)
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 兼容 ``--audit --specs ...`` 形态：子命令名允许带前导 --
    if argv and argv[0].startswith("--"):
        argv[0] = argv[0].lstrip("-")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())