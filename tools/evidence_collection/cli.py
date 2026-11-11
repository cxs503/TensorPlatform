"""命令行入口：collect / acceptance / freeze / verify。

    python -m tools.evidence_collection.cli collect    --spec config/evidence_collection/spec.json
    python -m tools.evidence_collection.cli acceptance --spec ... --baseline ...
    python -m tools.evidence_collection.cli freeze     --summary artifacts/.../summary.json
    python -m tools.evidence_collection.cli verify     --summary artifacts/.../summary.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import artifacts, collect as collector, gate, ledger
from .grade import grade
from .spec import load_spec

ROOT = Path(__file__).resolve().parents[2]


def _dump(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def cmd_collect(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    out_dir = Path(args.out or spec["sources"]["out_dir"])
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    base_url = None if args.no_runtime else (args.base_url or spec["sources"]["runtime"].get("base_url"))
    run = collector.collect(spec, ROOT, base_url=base_url, run_tests=not args.no_tests,
                            timeout_s=args.timeout, test_timeout_s=args.test_timeout)
    summary = run["summary"]
    records = {}
    for cap_id, signals in run["signals"].items():
        verdict = signals["verdict"]
        inputs = {
            "spec_sha256": run["spec_sha256"],
            "ledger_path": spec["sources"]["ledger"],
            "ledger_sha256": (summary["ledger"] or {}).get("sha256_before"),
            "openapi_sha256": run["runtime"].get("openapi_sha256"),
            "runtime_base_url": base_url,
            "ruleset": verdict["ruleset"],
        }
        written = artifacts.write_acceptance(out_dir, cap_id, signals, inputs)
        records[cap_id] = {
            "level": verdict["level"],
            "reason_codes": verdict["reason_codes"],
            "executed_evidence_items": verdict["executed_evidence_items"],
            "counts": verdict["counts"],
            "signals_digest": summary["capabilities"][cap_id]["signals_digest"],
            **{k: written[k] for k in ("acceptance_path", "criteria_table_path",
                                       "acceptance_sha256", "criteria_rows", "table_sha256")},
        }
        records[cap_id]["acceptance_path"] = str(Path(written["acceptance_path"]).relative_to(ROOT)
                                                if Path(written["acceptance_path"]).is_absolute()
                                                else written["acceptance_path"])
        records[cap_id]["criteria_table_path"] = str(
            Path(written["criteria_table_path"]).relative_to(ROOT)
            if Path(written["criteria_table_path"]).is_absolute() else written["criteria_table_path"])

    ledger_path = ROOT / spec["sources"]["ledger"]
    previous = None
    if ledger_path.exists():
        previous = json.loads(ledger_path.read_text(encoding="utf-8")).get(ledger.BLOCK_KEY)
    block = ledger.build_block(summary, records, previous)
    upd = ledger.update_ledger(ledger_path, block)
    view = ledger.write_view(out_dir, block)
    _dump(out_dir / "summary.json", summary)
    _dump(out_dir / "acceptance_checks_input.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "coverage": summary["coverage"], "level_distribution": summary["level_distribution"],
        "ledger": upd, "ledger_view": str(view),
    })
    print(f"collected={summary['coverage']['covered']}/{summary['coverage']['total']} "
          f"levels={summary['level_distribution']} runtime={summary['runtime']['available']} "
          f"out={out_dir}")
    return 0


def _load(path: str | Path, root: Path = ROOT) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    return json.loads(p.read_text(encoding="utf-8"))


def cmd_acceptance(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    summary = _load(args.summary)
    view = _load(args.ledger_view or (Path(args.summary).parent / "ledger_view.json"))
    baseline = _load(args.baseline) if args.baseline else None
    calibration = _load(args.calibration or spec["sources"].get(
        "calibration", "config/evidence_collection/calibration.json"))
    records = view["records"]
    # 把采集信号回灌，供 C2 的合成/真实标签判定与 verify 使用
    for cap, rec in records.items():
        ap = Path(rec["acceptance_path"])
        ap = ap if ap.is_absolute() else ROOT / ap
        if ap.exists():
            rec["_signals"] = json.loads(ap.read_text(encoding="utf-8"))["signals"]
            rec["_signals"]["verdict"] = grade(rec["_signals"], spec["thresholds"])
    acks_doc = _load(args.acks) if args.acks and Path(args.acks).exists() else {}
    acks = acks_doc.get("acks", acks_doc) if isinstance(acks_doc, dict) else {}
    claims = {}
    ledger_path = ROOT / spec["sources"]["ledger"]
    if ledger_path.exists():
        ledger_doc = json.loads(ledger_path.read_text(encoding="utf-8"))
        for cap in ledger_doc.get("capabilities") or []:
            if cap.get("claimed_level"):
                claims[cap["id"]] = cap["claimed_level"]
    result = gate.evaluate(summary, records, spec, baseline, calibration, root=ROOT,
                           acks=acks, claims=claims,
                           suite_runner=None if not args.no_suite else
                           (lambda p: {"status": "SKIPPED", "passed": 0, "failed": 0,
                                       "output": "--no-suite"}))
    _dump(Path(args.out or (Path(args.summary).parent / "acceptance_checks.json")), result)
    for name, check in result["checks"].items():
        print(f"{name:26s} pass={str(check['pass']):5s} value={check.get('value')} "
              f"threshold={check.get('threshold')}")
    print(f"ALL_PASS={result['all_pass']}")
    return 0 if result["all_pass"] or args.no_fail else 1


def cmd_freeze(args: argparse.Namespace) -> int:
    summary = _load(args.summary)
    view = _load(args.ledger_view or (Path(args.summary).parent / "ledger_view.json"))
    baseline = {
        "schema": "evidence_collection.baseline.v1",
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ruleset": summary["ruleset"],
        "spec_sha256": summary["spec_sha256"],
        "levels": {cap: rec["level"] for cap, rec in view["records"].items()},
        "legacy_real": list(args.legacy_real or []),
        "legacy_evidence": list(args.legacy_evidence or []),
        "note": ("冻结基线：回归门只拦『无正面反证的降级』。legacy_real 是独立人审已确认为 REAL "
                 "的能力，回归门要求它们保持 REAL。"),
    }
    path = _dump(Path(args.out), baseline)
    print(f"frozen {len(baseline['levels'])} levels → {path}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    summary = _load(args.summary)
    out_dir = Path(args.summary).parent
    bad, ok = [], 0
    for cap in summary["capabilities"]:
        ap = out_dir / artifacts.slug(cap) / "acceptance.json"
        if not ap.exists():
            bad.append({"capability": cap, "reason": "acceptance.json missing"})
            continue
        ver = artifacts.verify_acceptance(ap)
        if not ver["sha256_match"]:
            bad.append({"capability": cap, "reason": "acceptance sha256 mismatch"})
            continue
        payload = json.loads(ap.read_text(encoding="utf-8"))
        replay = grade(payload["signals"], spec["thresholds"])
        if replay["level"] != payload["level"]:
            bad.append({"capability": cap, "reason": "level not reproducible from stored signals",
                        "stored": payload["level"], "replay": replay["level"]})
            continue
        ok += 1
    result = {"schema": "evidence_collection.verify.v1", "verified": ok,
              "total": len(summary["capabilities"]), "failures": bad,
              "pass": not bad}
    _dump(out_dir / "verify.json", result)
    print(f"verified={ok}/{result['total']} pass={result['pass']}")
    for item in bad[:10]:
        print("  FAIL", item)
    return 0 if result["pass"] or args.no_fail else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="evidence_collection", description="能力台账证据自动化采集管线")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="执行采集（面清单/探针/参考复算/测试）并回写台账")
    c.add_argument("--spec", default="config/evidence_collection/spec.json")
    c.add_argument("--out", default=None)
    c.add_argument("--base-url", default=None)
    c.add_argument("--no-runtime", action="store_true")
    c.add_argument("--no-tests", action="store_true")
    c.add_argument("--timeout", type=float, default=20.0)
    c.add_argument("--test-timeout", type=float, default=600.0)
    c.set_defaults(func=cmd_collect)

    a = sub.add_parser("acceptance", help="跑 C1–C7 验收判据 + 回归门")
    a.add_argument("--spec", default="config/evidence_collection/spec.json")
    a.add_argument("--summary", default="artifacts/evidence_collection/summary.json")
    a.add_argument("--ledger-view", default=None)
    a.add_argument("--baseline", default="config/evidence_collection/baseline.json")
    a.add_argument("--calibration", default=None)
    a.add_argument("--out", default=None)
    a.add_argument("--acks", default="config/evidence_collection/acks.json")
    a.add_argument("--no-suite", action="store_true")
    a.add_argument("--no-fail", action="store_true")
    a.set_defaults(func=cmd_acceptance)

    f = sub.add_parser("freeze", help="把本次采集结论冻结成回归基线")
    f.add_argument("--summary", default="artifacts/evidence_collection/summary.json")
    f.add_argument("--ledger-view", default=None)
    f.add_argument("--out", default="config/evidence_collection/baseline.json")
    f.add_argument("--legacy-real", nargs="*", default=None)
    f.add_argument("--legacy-evidence", nargs="*", default=None)
    f.set_defaults(func=cmd_freeze)

    v = sub.add_parser("verify", help="复核已提交工件：自摘要一致 + 等级可由信号重放")
    v.add_argument("--spec", default="config/evidence_collection/spec.json")
    v.add_argument("--summary", default="artifacts/evidence_collection/summary.json")
    v.add_argument("--no-fail", action="store_true")
    v.set_defaults(func=cmd_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())