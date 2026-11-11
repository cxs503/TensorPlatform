#!/usr/bin/env python3
"""秘密卫生命令行：三出口判定 + 注入标定。

用法::

    python3 tools/secret_hygiene/cli.py calibrate                # 出标定产物（C5~C8）
    python3 tools/secret_hygiene/cli.py scan PATH [PATH ...]     # 扫文件（只打印脱敏结果）
    python3 tools/secret_hygiene/cli.py gate --outlet artifact --path out.json --content-file x
    python3 tools/secret_hygiene/cli.py check                    # 读已有标定产物跑判据

退出码：0 = 放行/判据全过；1 = 拦截/判据不达标；2 = 输入缺失。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.secret_hygiene.calibrate import calibrate, write_calibration
from tools.secret_hygiene.engine import HygieneError, load_config, scan_paths
from tools.secret_hygiene.integration import ReportGuard, scan_staged
from tools.secret_hygiene.outlets import (SecretBlocked, artifact_outlet, precommit_outlet,
                                          report_outlet)

DEFAULT_OUT = "artifacts/secret_hygiene"


def _criteria(payload: dict) -> dict:
    return {
        "C5_recall_ge_95": payload["detection"]["C5_recall_ge_95"],
        "C6_fpr_le_5": payload["false_positives"]["C6_fpr_le_5"],
        "C7_three_outlets_effective": payload["outlets"]["C7_all_three_effective"],
        "C8_redaction_idempotent": payload["redaction"]["C8_idempotent"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="秘密卫生门（三出口 + 标定）")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--rules", default="config/secret_hygiene/rules.json")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cal = sub.add_parser("calibrate", help="运行注入标定并落盘")
    p_cal.add_argument("--out", default=DEFAULT_OUT)
    p_cal.add_argument("--seed", type=int, default=20261111)

    p_scan = sub.add_parser("scan", help="扫描文件/目录（输出脱敏命中）")
    p_scan.add_argument("paths", nargs="+")

    p_gate = sub.add_parser("gate", help="三出口判定")
    p_gate.add_argument("--outlet", choices=("precommit", "report", "artifact"), required=True)
    p_gate.add_argument("--path", default=None, help="artifact/precommit 的路径")
    p_gate.add_argument("--content-file", default=None)
    p_gate.add_argument("--payload-file", default=None, help="report 出口的 JSON 载荷")
    p_gate.add_argument("--mode", choices=("block", "redact"), default="block")
    p_gate.add_argument("--audit", default=None, help="工件出口的审计 JSONL 路径")

    p_staged = sub.add_parser("staged", help="提交出口：判定 git 索引快照（pre-commit 钩子用）")
    p_staged.add_argument("--mode", choices=("block", "redact"), default="block")

    p_check = sub.add_parser("check", help="读已有标定产物跑判据")
    p_check.add_argument("--out", default=DEFAULT_OUT)

    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.cmd == "check":
        target = root / args.out / "calibration.json"
        if not target.exists():
            print(f"ERROR: 缺产物 {target}", file=sys.stderr)
            return 2
        payload = json.loads(target.read_text(encoding="utf-8"))
        crit = _criteria(payload)
        for key, ok in crit.items():
            print(f"[{'PASS' if ok else 'FAIL'}] {key}")
        print(f"检出 {payload['detection']['detected']}/{payload['corpus']['secrets']}"
              f"（recall={payload['detection']['recall']:.3f}）；"
              f"误报 {payload['false_positives']['flagged']}/{payload['corpus']['benign']}"
              f"（fpr={payload['false_positives']['fpr']:.3f}）")
        return 0 if all(crit.values()) else 1

    try:
        cfg = load_config(args.rules, root=root)
    except HygieneError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "staged":
        try:
            verdict = scan_staged(root, cfg, mode=args.mode)
        except Exception as exc:  # pragma: no cover - 非 git 环境
            print(f"ERROR: 读取 git 索引失败: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2))
        return 0 if verdict.allowed else 1

    if args.cmd == "calibrate":
        payload = calibrate(root, cfg=cfg, rules_path=args.rules, seed=args.seed)
        written = write_calibration(payload, root / args.out)
        crit = _criteria(payload)
        for key, ok in crit.items():
            print(f"[{'PASS' if ok else 'FAIL'}] {key}")
        print(f"产物 {written['path']} sha256={written['sha256']}")
        return 0 if all(crit.values()) else 1

    if args.cmd == "scan":
        hits = scan_paths([Path(p) for p in args.paths], cfg)
        for hit in hits:
            print(json.dumps(hit.to_dict(), ensure_ascii=False))
        print(f"命中 {len(hits)} 处", file=sys.stderr)
        return 0 if not hits else 1

    # gate
    if args.outlet == "report":
        if not args.payload_file:
            print("ERROR: report 出口需要 --payload-file", file=sys.stderr)
            return 2
        payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
        verdict = report_outlet(payload, cfg, mode=args.mode)
    else:
        if not args.path or not args.content_file:
            print("ERROR: 需要 --path 与 --content-file", file=sys.stderr)
            return 2
        content = Path(args.content_file).read_text(encoding="utf-8")
        try:
            if args.outlet == "precommit":
                verdict = precommit_outlet([(args.path, content)], cfg, mode=args.mode)
            else:
                verdict = artifact_outlet(Path(args.path), content, cfg, mode=args.mode,
                                          audit_path=args.audit, root=root
                                          if args.mode == "block" else None)
        except SecretBlocked as exc:
            print(json.dumps(exc.verdict.to_dict(), ensure_ascii=False, indent=2))
            return 1
    print(json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2))
    return 0 if verdict.allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())