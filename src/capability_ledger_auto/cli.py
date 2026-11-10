"""CLI 入口（command-line interface）。

用法
----
    python -m capability_ledger_auto.cli --registry config/capabilities.json --root .
    python -m capability_ledger_auto.cli --registry config/capabilities.json --root . --write
    python -m capability_ledger_auto.cli --audit --exec-tests          # 出 ledger_audit.json 产物
    python -m capability_ledger_auto.cli --ac --exec-tests             # 战役判据 AC1–AC8 机器验收
    python -m capability_ledger_auto.cli --snapshot tests/fixtures/capability_ledger/capabilities_snapshot.json --root .
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure src is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from capability_ledger_auto.registry import CapabilityRegistry
from capability_ledger_auto.scanner import EvidenceScanner
from capability_ledger_auto.evidence_chain import build_evidence_chain
from capability_ledger_auto.grader import GradeDeterminer


def main():
    parser = argparse.ArgumentParser(description="能力台账自动升账引擎")
    parser.add_argument("--registry", type=str, help="能力定义注册表 JSON 路径")
    parser.add_argument("--snapshot", type=str, help="能力快照 JSON 路径（兼容旧格式）")
    parser.add_argument("--root", type=str, default=".", help="工作树根目录")
    parser.add_argument("--write", action="store_true", help="写入产物文件")
    parser.add_argument("--audit", action="store_true",
                        help="走完整审计流水线并落盘 ledger_audit.json（含 sha256/git_commit）")
    parser.add_argument("--ac", action="store_true", help="运行战役判据 AC1–AC8 并落盘 ac_results.json")
    parser.add_argument("--exec-tests", action="store_true",
                        help="真实执行匹配到的测试文件（REAL 判级要求执行证据）")
    parser.add_argument("--corpus", action="append", default=None,
                        help="离线报告语料路径（可多次指定）")
    parser.add_argument("--adjudication-ledger", action="append", default=None,
                        help="结构化裁决台账路径（可多次指定）")
    parser.add_argument("--test-cache", default=None, help="测试执行结果缓存路径")
    parser.add_argument("--output", type=str, default="artifacts/capability_ledger_auto", help="输出目录")
    args = parser.parse_args()

    root = Path(args.root).resolve()

    if args.audit or args.ac:
        from . import ac_verify
        from .ledger_audit import DEFAULT_ADJUDICATION_LEDGER, DEFAULT_CORPUS, run_audit

        corpus = args.corpus or [root / DEFAULT_CORPUS]
        adj = args.adjudication_ledger or [root / DEFAULT_ADJUDICATION_LEDGER]
        if args.ac:
            report = ac_verify.verify_all(
                root, out_dir=args.output, exec_tests=args.exec_tests,
                test_cache_path=args.test_cache,
            )
            for r in report["ac"]:
                print(f"[{r['status']:7s}] {r['id']} :: {r['detail']}")
            print(f"AC 通过 {report['summary']['passed']}/{report['summary']['total']}")
            raise SystemExit(0 if report["summary"]["failed"] == 0 else 1)
        result = run_audit(
            root, registry_path=args.registry or "config/capabilities.json",
            out_dir=args.output, corpus_paths=corpus, adjudication_ledgers=adj,
            exec_tests=args.exec_tests, test_cache_path=args.test_cache, write=True,
        )
        payload = result["payload"]
        print(f"tally={payload['summary']['grade_tally']} digest={payload['content_digest']}")
        print(f"产物: {result['paths']}")
        raise SystemExit(0)

    # 加载注册表
    if args.registry:
        registry = CapabilityRegistry.from_json(args.registry)
    elif args.snapshot:
        registry = CapabilityRegistry.from_snapshot(args.snapshot)
    else:
        # 默认尝试两个路径
        reg_path = root / "config" / "capabilities.json"
        snap_path = root / "tests" / "fixtures" / "capability_ledger" / "capabilities_snapshot.json"
        if reg_path.exists():
            registry = CapabilityRegistry.from_json(reg_path)
        elif snap_path.exists():
            registry = CapabilityRegistry.from_snapshot(snap_path)
        else:
            print("ERROR: 未找到注册表文件，请使用 --registry 或 --snapshot 指定", file=sys.stderr)
            sys.exit(2)

    print(f"加载注册表: {registry.source_path}")
    print(f"能力数量: {registry.count()}")

    # 扫描
    scanner = EvidenceScanner(root, registry)
    scan_results = scanner.scan_all()
    print(f"扫描完成: {sum(1 for r in scan_results.values() if r.has_implementation)} 项有代码实现")

    # 构建证据链
    chains = {}
    for cap in registry.list_all():
        scan = scan_results[cap.capability_id]
        chain = build_evidence_chain(cap, scan, root)
        chains[cap.capability_id] = chain

    # 等级判定
    determiner = GradeDeterminer()
    grade_results = determiner.determine_all(chains)
    summary = determiner.summary(grade_results)

    # 输出
    output = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "registry_source": registry.source_path,
        "summary": summary,
        "capabilities": {
            cid: {
                "grade": r.grade,
                "confidence": r.confidence,
                "reasoning": r.reasoning,
                "evidence_summary": r.evidence_summary,
                "evidence_chain": chains[cid].to_dict(),
            }
            for cid, r in grade_results.items()
        },
    }

    # 打印摘要
    print("\n=== 能力等级分布 ===")
    for grade, count in summary["grade_tally"].items():
        if count > 0:
            print(f"  {grade}: {count}")
    print(f"平均置信度: {summary['average_confidence']:.2%}")

    # 写入文件
    if args.write:
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "auto_ledger.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"\n产物已写入: {out_path}")

        # 生成 Markdown 表格
        md_lines = ["# 能力台账自动升账报告\n", f"生成时间: {output['generated_at']}\n"]
        md_lines.append(f"\n## 等级分布\n")
        for grade, count in summary["grade_tally"].items():
            if count > 0:
                md_lines.append(f"- **{grade}**: {count}")
        md_lines.append(f"\n## 能力明细\n")
        md_lines.append("| 能力ID | 等级 | 置信度 | 代码 | 测试 | 报告 | 五元 |")
        md_lines.append("|--------|------|--------|------|------|------|------|")
        for cid, r in grade_results.items():
            es = r.evidence_summary
            md_lines.append(
                f"| {cid} | {r.grade} | {r.confidence:.2f} | "
                f"{es.get('code_count', 0)} | {es.get('test_count', 0)} | "
                f"{es.get('report_count', 0)} | {es.get('five_tuple_present', 0)}/5 |"
            )
        md_path = out_dir / "auto_ledger_table.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))
        print(f"表格已写入: {md_path}")

    return output


if __name__ == "__main__":
    main()
