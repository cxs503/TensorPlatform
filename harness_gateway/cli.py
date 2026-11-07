"""命令行入口：python -m harness_gateway <cmd> ...

子命令：
    mirror      源 harness → 门控仓镜像（脱敏 + manifest）
    verify      镜像 vs 源漂移检测
    gate        单独跑一道门（compile / canary）
    run-patch   补丁 → 门链（含红队负例）
    diagnose    执行协调官诊断单
    deploy      部署计划（默认 dry-run）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harness_gateway import mirror as mirror_mod
from harness_gateway.diagnostics import DiagnosticExecutor
from harness_gateway.gates import canary_gate, compile_gate
from harness_gateway.pipeline import HarnessDeployPipeline, PatchSpec
from harness_gateway.spec import DEFAULT_UNITS

DEFAULT_SRC = "/root/tp-agent"
DEFAULT_MIRROR = "harness_gateway/mirror"


def _pipeline(args) -> HarnessDeployPipeline:
    return HarnessDeployPipeline(
        repo_root=args.repo_root,
        mirror_root=args.mirror,
        src_root=args.src if args.src != "-" else None,
        artifacts_dir=args.artifacts,
        shim_exit=args.shim_exit,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness_gateway", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--src", default=DEFAULT_SRC, help="live harness root (use '-' to disable)")
        p.add_argument("--mirror", default=DEFAULT_MIRROR)
        p.add_argument("--repo-root", default=".")
        p.add_argument("--artifacts", default="artifacts/harness_gateway")
        p.add_argument("--shim-exit", type=int, default=1, help="psql shim exit code (1 = DB unreachable)")
        return p

    p = common(sub.add_parser("mirror", help="源 → 镜像（脱敏）"))
    p.add_argument("--no-write-manifest", action="store_true")

    common(sub.add_parser("verify", help="镜像漂移检测"))

    pg = sub.add_parser("gate", help="单跑一道门")
    common(pg)
    pg.add_argument("gate", choices=["compile", "canary"])

    rp = common(sub.add_parser("run-patch", help="补丁走门链"))
    rp.add_argument("--patch", required=True, help="patch json file")
    rp.add_argument("--run-id", default=None)
    rp.add_argument("--negative-control", default=None, help="label for red-team negative control")
    rp.add_argument("--print-verdict", action="store_true")

    dg = common(sub.add_parser("diagnose", help="执行协调官诊断单"))
    dg.add_argument("--order", required=True)

    dp = common(sub.add_parser("deploy", help="部署计划（默认 dry-run）"))
    dp.add_argument("--target", default=DEFAULT_SRC)
    dp.add_argument("--apply", action="store_true", help="真的写生产 harness（默认只出计划）")

    args = ap.parse_args(argv)

    if args.cmd == "mirror":
        rep = mirror_mod.mirror_harness(args.src, args.mirror, DEFAULT_UNITS,
                                        write_manifest=not args.no_write_manifest)
        print(json.dumps({"units": len(rep.entries), "redaction_total": rep.redaction_total,
                          "manifest": str(Path(args.mirror) / "MIRROR_MANIFEST.json")}, ensure_ascii=False))
        return 0

    if args.cmd == "verify":
        drifts = mirror_mod.verify_mirror(args.src, args.mirror)
        print(json.dumps({"drifts": [d.to_dict() for d in drifts], "count": len(drifts)}, ensure_ascii=False))
        return 0 if not drifts else 3

    if args.cmd == "gate":
        paths = [Path(args.mirror) / u.mirror_relpath for u in DEFAULT_UNITS]
        if args.gate == "compile":
            res = compile_gate.run_compile_gate(paths)
        else:
            res = canary_gate.run_canary_gate(
                args.mirror, DEFAULT_UNITS,
                sandbox_root=Path(args.artifacts) / "cli_canary", shim_exit=args.shim_exit,
            )
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0 if res.ok else 1

    if args.cmd == "run-patch":
        patch = PatchSpec.from_file(args.patch)
        report = _pipeline(args).run(patch, run_id=args.run_id, negative_control=args.negative_control)
        if args.print_verdict:
            print(json.dumps({"verdict": report["verdict"], "caught_by": report["caught_by"],
                              "run_id": report["run_id"], "report": report["artifacts"]["report"],
                              "stages": [(s["gate"], s["status"]) for s in report["stages"]]},
                             ensure_ascii=False, indent=2))
        ok = report["verdict"] in ("PASS", "FAIL_ROLLED_BACK")
        return 0 if ok else 1

    if args.cmd == "diagnose":
        ex = DiagnosticExecutor(_pipeline(args), run_dir=Path(args.artifacts) / "diagnostics")
        rep = ex.execute(args.order)
        print(json.dumps({"order_id": rep["order_id"], "verdict": rep["verdict"], "counts": rep["counts"],
                          "items": [(i["id"], i["op"], i["status"]) for i in rep["items"]]},
                         ensure_ascii=False, indent=2))
        return 0 if rep["verdict"] != "EXECUTED_FAIL" else 1

    if args.cmd == "deploy":
        pipe = _pipeline(args)
        if args.apply:
            out = pipe.apply_deploy(args.target, dry_run=False)
        else:
            out = pipe.deploy_plan(args.target)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())