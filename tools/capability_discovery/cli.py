"""CLI：能力自动发现与注册的一条龙入口。

    # 1) 只扫描（静态），看能发现什么
    python -m tools.capability_discovery.cli scan --roots tests/

    # 2) 扫描 + 真实运行结局 + 登记 + 差异报告（dry-run）
    python -m tools.capability_discovery.cli register \
        --roots tests/ --outcomes artifacts/capability_discovery/outcomes.json \
        --ledger artifacts/capability_discovery/ledger.json

    # 3) 实写台账
    python -m tools.capability_discovery.cli register ... --write

    # 4) 回滚 / 恢复（必须给原因）
    python -m tools.capability_discovery.cli retract --ledger ... --capability X --reason "..."
    python -m tools.capability_discovery.cli restore --ledger ... --capability X --reason "..."

    # 5) 哈希链校验
    python -m tools.capability_discovery.cli verify --ledger ...

    # 6) 导出到 capability_upgrade 自动升级管线（C7）
    python -m tools.capability_discovery.cli export-upgrade --ledger ... --out-registry ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

if __package__ in (None, ""):  # 允许直接 python tools/capability_discovery/cli.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.capability_discovery import export as export_mod
from tools.capability_discovery import pipeline as pipeline_mod
from tools.capability_discovery.grading import load_policy
from tools.capability_discovery.markers import MarkerScanner
from tools.capability_discovery.model import CapabilityFeature, Grade
from tools.capability_discovery.plugin import load_outcome_records, load_outcomes
from tools.capability_discovery.store import Ledger, LedgerError, set_policy_digest

DEFAULT_LEDGER = "artifacts/capability_discovery/ledger.json"


def _write_json(path: Path | str, doc: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def _load_outcomes(path: Optional[str]) -> Dict[str, str]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"运行结局文件不存在: {p}")
    return load_outcomes(p)


def cmd_scan(args: argparse.Namespace) -> int:
    scanner = MarkerScanner(args.root)
    records = scanner.scan_paths([Path(r) for r in args.roots])
    payload = {
        "records": [r.to_dict() for r in records],
        "diagnostics": scanner.diagnostics.to_list(),
        "count": len(records),
        "capabilities": sorted({r.capability_id for r in records}),
        "formats": sorted({r.marker_format for r in records}),
    }
    if args.json:
        _write_json(args.json, payload)
        print(f"扫描记录 {len(records)} 条 → {args.json}")
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    ledger = Ledger.load_or_empty(args.ledger)
    outcomes = _load_outcomes(args.outcomes)
    outcome_items = load_outcome_records(args.outcomes) if args.outcomes else None
    result = pipeline_mod.run(
        roots=[Path(r) for r in args.roots],
        root=args.root,
        outcomes=outcomes,
        outcome_items=outcome_items,
        policy=policy,
        ledger=ledger,
        write_ledger=bool(args.write),
        actor=args.actor,
    )
    out_dir = Path(args.out_dir)
    if args.write:
        _write_json(out_dir / "discovery_report.json", result.report)
        (out_dir / "discovery_report.md").write_text(
            pipeline_mod.report_markdown(result.report), encoding="utf-8"
        )
        print(f"台账已写: {args.ledger}")
    s = result.report["summary"]
    print(
        "discovered={discovered} added={added} updated={updated} unchanged={unchanged} "
        "missing={missing} held_retracted={held_retracted} runtime_records_added={runtime_records_added} "
        "chain_ok={chain_ok}".format(**s)
    )
    print(f"等级分布: {s['levels']}")
    if not args.write:
        print("（dry-run：未落盘；加 --write 实写台账与报告）")
    return 0


def cmd_retract(args: argparse.Namespace) -> int:
    ledger = Ledger.load(args.ledger)
    try:
        entry = ledger.retract(args.capability, args.reason, actor=args.actor)
    except LedgerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    ledger.save()
    print(f"已撤回 {args.capability}（原因: {args.reason}）；等级记录保留: {entry['level']}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    ledger = Ledger.load(args.ledger)
    try:
        entry = ledger.restore(args.capability, args.reason, actor=args.actor)
    except LedgerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    ledger.save()
    print(f"已恢复 {args.capability}（原因: {args.reason}）；当前等级: {entry['level']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    ledger = Ledger.load(args.ledger)
    ok, msg = ledger.verify_chain()
    print(("OK: " if ok else "BROKEN: ") + msg)
    print(f"条目 {len(ledger.entries)} 条（active {len(ledger.active_ids())}）")
    return 0 if ok else 1


def cmd_export_upgrade(args: argparse.Namespace) -> int:
    ledger = Ledger.load(args.ledger)
    features: List[CapabilityFeature] = []
    grades: Dict[str, Grade] = {}
    for cid in ledger.active_ids():
        entry = ledger.entries[cid]
        ev = entry.get("evidence", {})
        features.append(
            CapabilityFeature(
                capability_id=cid,
                label=entry.get("label", ""),
                declared_level=ev.get("declared_level", ""),
                anchors=list(ev.get("anchors") or []),
                params=dict(ev.get("params") or {}),
                tags=list(ev.get("tags") or []),
                tests=list(ev.get("tests") or []),
                sources=list(ev.get("sources") or []),
                marker_formats=list(ev.get("marker_formats") or []),
                outcomes=dict(ev.get("outcomes") or {}),
                declared_levels_seen=list(ev.get("declared_levels_seen") or []),
            )
        )
        g = entry.get("grade", {})
        grades[cid] = Grade(
            capability_id=cid,
            level=entry["level"],
            outcome_state=g.get("outcome_state", "not_run"),
            reasons=list(g.get("reasons") or []),
            confidence=float(g.get("confidence", 0.0)),
            policy_digest=str(g.get("policy_digest", "")),
        )
    docs = export_mod.export(
        features,
        grades,
        registry_path=args.out_registry,
        evidence_path=args.out_evidence,
        note=args.note,
        target_level=args.target_level,
    )
    print(f"导出 {len(features)} 个能力 → {args.out_registry}")
    if args.out_evidence:
        print(f"证据 feed → {args.out_evidence}")
    if args.validate:
        checked = export_mod.validate_with_upgrade_pipeline(docs["registry"])
        print(f"升级管线校验通过: slots={checked['slots_applied']} criteria={checked['criteria_applied']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.capability_discovery.cli",
        description="求解器能力自动发现与注册（marker → 台账 → 差异报告）",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--root", default=".", help="仓根（nodeid / 路径基准）")
        sp.add_argument("--roots", nargs="+", default=["tests"], help="扫描路径（目录或文件）")

    sp_scan = sub.add_parser("scan", help="仅静态扫描 marker")
    common(sp_scan)
    sp_scan.add_argument("--json", default=None, help="输出 JSON 路径（缺省打印 stdout）")
    sp_scan.set_defaults(func=cmd_scan)

    sp_reg = sub.add_parser("register", help="扫描 + 定级 + 登记 + 差异报告")
    common(sp_reg)
    sp_reg.add_argument("--ledger", default=DEFAULT_LEDGER)
    sp_reg.add_argument("--outcomes", default=None, help="pytest 插件产出的运行结局 JSON")
    sp_reg.add_argument("--policy", default=None, help="定级策略 JSON（覆盖默认保守口径）")
    sp_reg.add_argument("--out-dir", default="artifacts/capability_discovery")
    sp_reg.add_argument("--write", action="store_true", help="实写台账与报告")
    sp_reg.add_argument("--actor", default="auto")
    sp_reg.set_defaults(func=cmd_register)

    sp_ret = sub.add_parser("retract", help="撤回一条自动登记（必须给原因）")
    sp_ret.add_argument("--ledger", default=DEFAULT_LEDGER)
    sp_ret.add_argument("--capability", required=True)
    sp_ret.add_argument("--reason", required=True)
    sp_ret.add_argument("--actor", default="human")
    sp_ret.set_defaults(func=cmd_retract)

    sp_res = sub.add_parser("restore", help="恢复一条被撤回的登记（必须给原因）")
    sp_res.add_argument("--ledger", default=DEFAULT_LEDGER)
    sp_res.add_argument("--capability", required=True)
    sp_res.add_argument("--reason", required=True)
    sp_res.add_argument("--actor", default="human")
    sp_res.set_defaults(func=cmd_restore)

    sp_ver = sub.add_parser("verify", help="校验台账哈希链")
    sp_ver.add_argument("--ledger", default=DEFAULT_LEDGER)
    sp_ver.set_defaults(func=cmd_verify)

    sp_exp = sub.add_parser("export-upgrade", help="导出到 capability_upgrade 自动升级管线")
    sp_exp.add_argument("--ledger", default=DEFAULT_LEDGER)
    sp_exp.add_argument("--out-registry", default="config/capability_discovery/exported_registry.json")
    sp_exp.add_argument("--out-evidence", default=None)
    sp_exp.add_argument("--target-level", default="REAL")
    sp_exp.add_argument("--note", default="")
    sp_exp.add_argument("--validate", action="store_true", help="用升级管线自身加载器校验")
    sp_exp.set_defaults(func=cmd_export_upgrade)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())