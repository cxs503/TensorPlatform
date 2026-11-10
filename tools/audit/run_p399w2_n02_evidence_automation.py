"""p399w2-n02 产物生成器：带标注算例的判级器效度 + 平台台账证据指纹。

用法::

    PYTHONPATH=src python tools/audit/run_p399w2_n02_evidence_automation.py --stage eval
    PYTHONPATH=src python tools/audit/run_p399w2_n02_evidence_automation.py --stage audit

产物（均落 ``artifacts/capability_ledger_auto/``，附 sha256 旁车）::

    eval_validity_p399w2.json          修复后：五级召回/精确率 + 混淆矩阵
    eval_validity_p399w2_baseline.json 对照基线：code_roots=("src",)（修复前扫描根）
    ledger_audit_p399w2.json           平台 21 项能力：等级 + 置信度 + 证据指纹根
    summary_p399w2.json                报告用的数字摘要（程序化注入，零手抄）

零领域语义：算例用 fx.* 合成命名；平台侧只读注册表下发的模式。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from capability_ledger_auto import eval_validity as ev  # noqa: E402
from capability_ledger_auto.fingerprint import record_digest  # noqa: E402
from capability_ledger_auto.ledger_audit import run_audit  # noqa: E402
from capability_ledger_auto.scanner import sha256_file  # noqa: E402

OUT_DIR = ROOT / "artifacts" / "capability_ledger_auto"
REPLICAS = 3


def _write_json(path: Path, payload: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    digest = sha256_file(path)
    path.with_suffix(path.suffix + ".sha256").write_text(f"{digest}  {path.name}\n",
                                                         encoding="utf-8")
    return digest


def stage_eval(tmp_root: Path) -> dict:
    """同一套带标注算例上跑「修复前基线」与「修复后」，给出前后对比数字。"""
    repo, registry, cases = ev.build_fixture_repo(tmp_root / "fixture", replicas=REPLICAS)

    t0 = time.time()
    baseline = ev.evaluate(repo, registry, cases, exec_tests=True, code_roots=("src",),
                           exec_timeout_s=120, exec_budget_s=900)
    baseline_s = round(time.time() - t0, 2)

    t0 = time.time()
    fixed = ev.evaluate(repo, registry, cases, exec_tests=True,
                        exec_timeout_s=120, exec_budget_s=900)
    fixed_s = round(time.time() - t0, 2)

    d_base = _write_json(OUT_DIR / "eval_validity_p399w2_baseline.json", baseline)
    d_fixed = _write_json(OUT_DIR / "eval_validity_p399w2.json", fixed)

    per_level = {
        level: {
            "support": fixed["per_level"][level]["support"],
            "recall_before": baseline["per_level"][level]["recall"],
            "recall_after": fixed["per_level"][level]["recall"],
            "precision_before": baseline["per_level"][level]["precision"],
            "precision_after": fixed["per_level"][level]["precision"],
        }
        for level in ev.LEVELS
    }
    return {
        "stage": "eval",
        "case_count": fixed["case_count"],
        "variants": sorted(fixed["by_variant"]),
        "accuracy_before": baseline["accuracy"],
        "accuracy_after": fixed["accuracy"],
        "macro_recall_before": baseline["macro_recall"],
        "macro_recall_after": fixed["macro_recall"],
        "by_variant": fixed["by_variant"],
        "per_level": per_level,
        "confusion_matrix_after": fixed["confusion_matrix"],
        "confusion_matrix_before": baseline["confusion_matrix"],
        "mismatches_before": baseline["mismatches"],
        "mismatches_after": fixed["mismatches"],
        "wall_seconds": {"baseline": baseline_s, "fixed": fixed_s},
        "artifact": str((OUT_DIR / "eval_validity_p399w2.json").relative_to(ROOT)),
        "artifact_sha256": d_fixed,
        "baseline_artifact": str((OUT_DIR / "eval_validity_p399w2_baseline.json").relative_to(ROOT)),
        "baseline_artifact_sha256": d_base,
    }


def stage_audit(code_roots=None, suffix: str = "") -> dict:
    """平台 21 项能力的真实审计（含证据指纹根与证据驱动置信度）。

    ``code_roots=("src",)`` 复现修复前的扫描根，用于前后对照基线。
    """
    t0 = time.time()
    result = run_audit(
        ROOT, registry_path="config/capabilities.json",
        out_dir=OUT_DIR / ("run_p399w2" + suffix), exec_tests=True, write=True,
        code_roots=code_roots,
    )
    elapsed = round(time.time() - t0, 2)
    return _pack_audit(result["payload"], elapsed=elapsed,
                       name=("ledger_audit_p399w2%s.json" % suffix))


def _pack_audit(payload: dict, elapsed: float, name: str = "ledger_audit_p399w2.json") -> dict:
    rows = []
    for cid in sorted(payload["capabilities"]):
        cap = payload["capabilities"][cid]
        rows.append({
            "capability_id": cid,
            "registry_sha256": payload["registry"]["sha256"],
            "grade": cap["grade"],
            "confidence": cap["confidence"],
            "evidence_root": cap["evidence_fingerprint"]["root"],
            "leaf_count": cap["evidence_fingerprint"]["leaf_count"],
            "record_digest": cap["record_digest"],
            "chain_quality": cap["chain_quality"],
            "five_tuple_present": cap["five_tuple"]["present_count"],
            "test_evidence_source": cap["evidence_summary"]["test_evidence_source"],
            "code_roots": cap["evidence_summary"].get("code_roots", []),
            "weak_code_count": cap["evidence_summary"].get("weak_code_count", 0),
            "substantive_code_count": cap["evidence_summary"]["substantive_code_count"],
            "adjudicated_level": cap["evidence_summary"]["adjudicated_level"],
        })
    # 复算自检：每行 record_digest 必须能被独立复算
    verified = all(row["record_digest"] == record_digest(row) for row in rows)

    audit_payload = {
        "schema": "ledger-audit-p399w2/v1",
        "git_commit": payload.get("git_commit"),
        "registry_sha256": payload["registry"]["sha256"],
        "evidence_state_digest": payload["evidence_state_digest"],
        "grade_tally": payload["summary"]["grade_tally"],
        "policy": payload["policy"],
        "rows": rows,
        "record_digest_verified": verified,
    }
    digest = _write_json(OUT_DIR / name, audit_payload)
    return {
        "stage": "audit",
        "git_commit": payload.get("git_commit"),
        "registry_sha256": payload["registry"]["sha256"],
        "policy": payload["policy"],
        "capabilities": len(rows),
        "grade_tally": payload["summary"]["grade_tally"],
        "evidence_state_digest": payload["evidence_state_digest"],
        "record_digest_verified": verified,
        "wall_seconds": elapsed,
        "artifact": str((OUT_DIR / name).relative_to(ROOT)),
        "artifact_sha256": digest,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["eval", "audit", "audit_baseline", "pack", "all"],
                        default="all")
    parser.add_argument("--tmp", default=None, help="临时目录（默认 tempfile.mkdtemp）")
    args = parser.parse_args()

    tmp_root = Path(args.tmp) if args.tmp else Path(tempfile.mkdtemp(prefix="p399w2_"))
    summary: dict = {"generated_by": "tools/audit/run_p399w2_n02_evidence_automation.py"}

    if args.stage in ("eval", "all"):
        summary["eval"] = stage_eval(tmp_root)
        print(json.dumps({k: v for k, v in summary["eval"].items()
                          if k not in ("rows", "confusion_matrix_after",
                                       "confusion_matrix_before")},
                         ensure_ascii=False, indent=1))
    if args.stage in ("audit", "all"):
        summary["audit"] = stage_audit()
        summary["audit_baseline"] = stage_audit(code_roots=("src",), suffix="_baseline")
        print(json.dumps({k: v for k, v in summary["audit"].items() if k != "rows"},
                         ensure_ascii=False, indent=1))

    if args.stage == "audit_baseline":
        summary["audit_baseline"] = stage_audit(code_roots=("src",), suffix="_baseline")
        print(json.dumps({k: v for k, v in summary["audit_baseline"].items() if k != "rows"},
                         ensure_ascii=False, indent=1))
        return 0

    if args.stage == "pack":
        # 复用已跑完的审计产物重新打包（不重扫仓库）：修复后 + 对照基线
        for run_dir, name, key in (
            ("run_p399w2", "ledger_audit_p399w2.json", "audit"),
            ("run_p399w2_baseline", "ledger_audit_p399w2_baseline.json", "audit_baseline"),
        ):
            path = OUT_DIR / run_dir / "ledger_audit.json"
            if not path.exists():
                print("skip (missing):", path)
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            packed = _pack_audit(raw, elapsed=0.0, name=name)
            if key == "audit":
                summary["audit"] = packed
            else:
                summary["audit_baseline"] = packed
        for key in ("audit", "audit_baseline"):
            if key in summary:
                print(key, json.dumps({k: v for k, v in summary[key].items() if k != "rows"},
                                      ensure_ascii=False))
        return 0

    if False:
        _write_json(OUT_DIR / "ledger_audit_p399w2.json", {
            "schema": "ledger-audit-p399w2/v1",
            "git_commit": summary["audit"]["git_commit"],
            "registry_sha256": summary["audit"]["registry_sha256"],
            "evidence_state_digest": summary["audit"]["evidence_state_digest"],
            "grade_tally": summary["audit"]["grade_tally"],
            "policy": summary["audit"]["policy"],
            "rows": summary["audit"]["rows"],
            "record_digest_verified": summary["audit"]["record_digest_verified"],
        })
        print(json.dumps({k: v for k, v in summary["audit"].items() if k != "rows"},
                         ensure_ascii=False, indent=1))
        return 0

    if args.stage == "all":
        _write_json(OUT_DIR / "summary_p399w2.json", summary)
        print("summary ->", (OUT_DIR / "summary_p399w2.json").relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())