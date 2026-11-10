"""端到端演示：跑测试 → 采结局 → 发现 → 定级 → 登记 → 差异报告 → 导出升级管线。

为什么要有这个脚本
------------------
报告里的每个数字都必须由程序写入（零手抄）。本脚本把整条闭环跑一遍，把
台账 / 差异报告 / 运行结局 / 导出文档全部落到 ``artifacts/capability_discovery/``，
报告生成器再从这些 JSON 里读数——数字因此可复算、可审计。

用法::

    PYTHONPATH=. python -m tools.capability_discovery.demo            # 默认产物目录
    PYTHONPATH=. python -m tools.capability_discovery.demo --out-dir X
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from . import export as export_mod
from . import pipeline as pipeline_mod
from .grading import load_policy
from .plugin import OUTCOME_ENV, load_outcome_records, load_outcomes
from .store import Ledger

DEFAULT_OUT = "artifacts/capability_discovery"
DEFAULT_SUITE = "tools/capability_discovery/fixtures/synthetic_suite"
PLUGIN = "tools.capability_discovery.plugin"


def _run_tests(root: Path, suite: str, outcomes_path: Path, extra: List[str] | None = None) -> Dict[str, Any]:
    """在仓内真实跑一次带 marker 的测试套件，采集运行结局。"""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        PLUGIN,
        # 夹具套件自带的 conftest 会把自身排除在仓级收集之外（防止被当成平台
        # 测试结果），因此就地运行时必须 --noconftest 才能真正执行它们。
        "--noconftest",
        f"--capability-outcomes={outcomes_path}",
        *(extra or []),
        suite,
    ]
    proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
    summary = (proc.stdout.strip().splitlines() or [""])[-1]
    return {
        "cmd": " ".join(cmd),
        "returncode": proc.returncode,
        "summary_line": summary,
        "outcomes_path": str(outcomes_path),
    }


def demo(root: Path | str = ".", out_dir: Path | str = DEFAULT_OUT, suite: str = DEFAULT_SUITE) -> Dict[str, Any]:
    root = Path(root).resolve()
    out = Path(out_dir)
    if not out.is_absolute():
        out = root / out
    out.mkdir(parents=True, exist_ok=True)
    outcomes_path = out / "outcomes.json"

    run_info = _run_tests(root, suite, outcomes_path)
    outcomes = load_outcomes(outcomes_path)
    items = load_outcome_records(outcomes_path)

    ledger_path = out / "ledger.json"
    # 演示从干净台账起步（重复运行同一命令应得到同一份台账，见下方 idempotency）
    if ledger_path.exists():
        ledger_path.unlink()
    ledger = Ledger.load_or_empty(ledger_path)
    policy = load_policy(None)
    result = pipeline_mod.run(
        roots=[suite],
        root=root,
        outcomes=outcomes,
        outcome_items=items,
        policy=policy,
        ledger=ledger,
        write_ledger=False,
    )
    ledger.save()
    (out / "discovery_report.json").write_text(
        json.dumps(result.report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "discovery_report.md").write_text(pipeline_mod.report_markdown(result.report), encoding="utf-8")

    # 幂等性证据：同一输入再跑一遍，台账必须字节不变、链不增长
    first_digest = export_mod.digest(ledger.dumps())
    second = pipeline_mod.run(
        roots=[suite],
        root=root,
        outcomes=outcomes,
        outcome_items=items,
        policy=policy,
        ledger=Ledger.load(ledger_path),
        write_ledger=False,
    )
    second_ledger = second.ledger
    second_ledger.save()
    second_digest = export_mod.digest(Ledger.load(ledger_path).dumps())
    idempotency = {
        "first_run_added": result.report["summary"]["added"],
        "second_run_added": second.report["summary"]["added"],
        "second_run_updated": second.report["summary"]["updated"],
        "second_run_unchanged": second.report["summary"]["unchanged"],
        "ledger_sha256_first": first_digest,
        "ledger_sha256_second": second_digest,
        "ledger_bytes_identical": first_digest == second_digest,
        "chain_records_after_first": len(ledger.chain),
        "chain_records_after_second": len(second_ledger.chain),
    }

    docs = export_mod.export(
        result.features,
        result.grades,
        registry_path=out / "exported_upgrade_registry.json",
        evidence_path=out / "upgrade_evidence_feed.json",
        note="auto-discovered from tools/capability_discovery/fixtures/synthetic_suite",
    )
    checked = export_mod.validate_with_upgrade_pipeline(docs["registry"])

    fixture_index = {
        "schema": "capability_discovery.fixture_index.v1",
        "note": "synthetic fixture suite (zero domain vocabulary): each file and the marker formats it exercises",
        "files": sorted(p.name for p in (root / suite).glob("*.py")),
        "capabilities": {
            cid: {
                "level": result.grades[cid].level,
                "outcome_state": result.grades[cid].outcome_state,
                "marker_formats": sorted(
                    {t for f in result.features if f.capability_id == cid for t in f.marker_formats}
                ),
                "tests": sorted(t["nodeid"].split("::", 1)[1] for f in result.features if f.capability_id == cid for t in f.tests),
            }
            for cid in sorted(result.grades)
        },
        "test_run": run_info,
        "upgrade_linkage": checked,
        "idempotency": idempotency,
        "ledger_head_hash": second_ledger.head_hash(),
        "chain_ok": ledger.verify_chain()[0],
    }
    (out / "fixture_index.json").write_text(
        json.dumps(fixture_index, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {"run": run_info, "report": result.report, "linkage": checked, "out_dir": str(out)}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="capability discovery end-to-end demo")
    parser.add_argument("--root", default=".")
    parser.add_argument("--out-dir", default=DEFAULT_OUT)
    parser.add_argument("--suite", default=DEFAULT_SUITE)
    args = parser.parse_args(argv)
    info = demo(args.root, args.out_dir, args.suite)
    print(json.dumps({
        "test_run": info["run"]["summary_line"],
        "test_returncode": info["run"]["returncode"],
        "summary": info["report"]["summary"],
        "upgrade_linkage": {k: info["linkage"][k] for k in ("ok", "slots_applied", "criteria_applied")},
        "out_dir": info["out_dir"],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())