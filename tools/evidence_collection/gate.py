"""验收判据（C1–C7）与回归门。

机器可算，逐条落数字；报告里的每个数都来自这里的返回值，不手抄。

================  ============================================================
判据               口径
================  ============================================================
C1 覆盖率          ``covered / total ≥ min_covered``（默认 15/21）；covered 定义=
                   **至少一项已执行证据**（在线探针执行 / 参考误差复算 / 绑定测试执行 /
                   面清单核验），"只声明没执行"不算
C2 等级正确性      判定器对带标注语料（合成信号 fixture + 真实能力的独立审计标签）的
                   一致率 ≥ ``min_agreement``，语料规模 ≥ ``min_labeled``
C3 证据工件        每个 covered 能力：acceptance.json 存在、自摘要一致、判据表 ≥1 行
C4 回归门          冻结基线逐能力比对：证据不丢；等级下降必须是"有正面反证"的下降
                   （INVARIANT_VIOLATED / REFERENCE_OVER_ACCEPT / SURFACE_NOT_IMPLEMENTED），
                   否则记 violation；``legacy_real`` 列出的能力必须仍为 REAL
C5 零领域语义      扫描器/测试代码对域词表零命中（词表以数据形式从 config 读取）
C6 成熟化          标准 CI 工作流里存在采集/验收步骤（可注入路径）
C7 pytest          ``tests/evidence_collection`` 全绿且用例数 ≥ ``min_tests``
================  ============================================================
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from tools.capability_audit_gate.taxonomy import classify_change, normalize

from .artifacts import verify_acceptance

REFUTATION_REASONS = {
    "INVARIANT_VIOLATED",
    "REFERENCE_OVER_ACCEPT",
    "REFERENCE_CRITERION_FAILED",
    "SURFACE_NOT_IMPLEMENTED",
}


# ── 语料展开（合成信号 fixture → 采集信号结构；零领域语义） ─────────────────
def expand_case(case: dict) -> dict:
    """把紧凑的标注用例展开成 :func:`grade.grade` 需要的信号结构。"""
    surface_state = case.get("surface", "none")
    surface = None
    if surface_state != "none":
        surface = {
            "declared": 1,
            "present": [] if surface_state == "absent" else [{"pattern": "/x", "matches": ["/x"]}],
            "absent": [{"pattern": "/x", "matches": []}] if surface_state == "absent" else [],
            "status": {"pass": "PASS", "absent": "FAIL"}.get(surface_state, "NOT_EXECUTED"),
        }
    probe_spec = case.get("probe")
    probes = []
    if probe_spec is not None:
        probes.append({
            "id": "p",
            "method": "POST",
            "path": "/x",
            "executed": bool(probe_spec.get("executed", True)),
            "digest": "d" * 8,
            "samples": [],
            "criteria": [
                {"id": cid, "kind": "identity", "class": ccls, "verdict": cverdict,
                 "measured": None, "threshold": None, "detail": "", "path": None}
                for cid, ccls, cverdict in probe_spec.get("criteria", [])
            ],
        })
    ref = case.get("reference")
    reference = None
    if ref is not None:
        reference = {
            "path": ref.get("path", "artifacts/x.json"),
            "status": ref.get("status", "PASS"),
            "worst_pct": ref.get("worst_pct"),
            "tolerance_pct": ref.get("tolerance_pct", 3.0),
            "n_values": ref.get("n_values", 1),
            "sha256": "e" * 16,
        }
    tests = case.get("tests")
    tests_sig = None
    if tests is not None:
        tests_sig = {
            "modules": ["tests/x.py"],
            "modules_digest": "f" * 16,
            "status": tests.get("status", "PASSED"),
            "totals": {"tests": tests.get("total", 3), "passed": tests.get("passed", 3),
                       "failures": tests.get("failures", 0), "errors": tests.get("errors", 0),
                       "skipped": tests.get("skipped", 0)},
            "cases": [],
            "exit_code": 0,
        }
    return {"capability": case.get("id", "case"), "surface": surface, "probes": probes,
            "reference": reference, "tests": tests_sig}


def agreement(cases: list[dict], thresholds: dict) -> dict:
    """对标注语料跑判定器，返回一致率与逐条明细。"""
    from .grade import grade

    detail = []
    hits = 0
    for case in cases:
        expected = normalize(case["expected_level"])
        got = grade(expand_case(case), thresholds)["level"]
        ok = got == expected
        hits += int(ok)
        detail.append({"id": case.get("id"), "expected": expected, "got": got, "match": ok,
                       "basis": case.get("basis", "")})
    n = len(cases)
    return {"labeled": n, "matches": hits, "agreement": (hits / n) if n else None,
            "detail": detail}


# ── C5 零领域语义扫描 ────────────────────────────────────────────────────────
def _denylist_hits(files: list[Path], denylist: dict) -> list[dict]:
    ascii_words = [w.lower() for w in denylist.get("ascii_words", [])]
    cjk_words = list(denylist.get("cjk_words", []))
    hits = []
    for path in files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        low = text.lower()
        for word in ascii_words:
            for m in re.finditer(rf"(?<![a-z0-9_]){re.escape(word)}(?![a-z0-9_])", low):
                line = low.count("\n", 0, m.start()) + 1
                hits.append({"file": str(path), "word": word, "line": line})
        for word in cjk_words:
            idx = text.find(word)
            while idx != -1:
                hits.append({"file": str(path), "word": word,
                             "line": text.count("\n", 0, idx) + 1})
                idx = text.find(word, idx + len(word))
    return hits


# ── C7 测试套件 ──────────────────────────────────────────────────────────────
def _run_suite(path: str, timeout_s: float = 900.0) -> dict:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"status": "TIMEOUT", "passed": 0, "failed": 0, "output": ""}
    out = proc.stdout + proc.stderr
    passed = failed = 0
    m = re.search(r"(\d+) passed", out)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+) failed", out)
    if m:
        failed = int(m.group(1))
    return {"status": "PASS" if proc.returncode == 0 and failed == 0 else "FAIL",
            "passed": passed, "failed": failed, "exit_code": proc.returncode,
            "output": "\n".join(out.strip().splitlines()[-5:])}


# ── 主入口 ──────────────────────────────────────────────────────────────────
def evaluate(summary: dict, records: dict, spec: dict, baseline: dict | None,
             calibration: dict, root: str | Path = ".",
             acks: dict | None = None, claims: dict | None = None,
             suite_runner: Callable[[str], dict] | None = None,
             min_covered: int = 15, min_agreement: float = 0.9, min_labeled: int = 10,
             min_tests: int = 10) -> dict:
    root = Path(root).resolve()
    ac = spec.get("acceptance") or {}
    min_covered = int(ac.get("min_covered", min_covered))
    min_agreement = float(ac.get("min_agreement", min_agreement))
    min_labeled = int(ac.get("min_labeled", min_labeled))
    min_tests = int(ac.get("min_tests", min_tests))
    checks: dict[str, dict] = {}

    # C1 覆盖率
    cov = summary["coverage"]
    checks["C1_coverage"] = {
        "value": cov["covered"], "total": cov["total"], "threshold": min_covered,
        "pass": cov["covered"] >= min_covered,
        "detail": {"covered_ids": cov["covered_ids"][:40], "uncovered_ids": cov["uncovered_ids"][:40]},
    }

    # C2 等级正确性（合成 + 真实标签）
    # 合成用例：直接重放判定器（只验规则本身，信号为人工构造的语义标签）
    synth = calibration.get("cases") or []
    graded_synth = agreement(synth, spec["thresholds"])
    # 真实标签：与本次**实际采集结论**比对（不重放，避免拿构造信号冒充真实证据）
    real_detail = []
    for label in calibration.get("real_labels") or []:
        cap = label["capability"]
        if cap not in records:
            continue
        got = records[cap]["level"]
        real_detail.append({"capability": cap, "expected": normalize(label["expected_level"]),
                            "got": got, "match": got == normalize(label["expected_level"]),
                            "as_of": label.get("as_of"), "basis": label.get("basis", "")})
    real_hits = sum(1 for d in real_detail if d["match"])
    labeled = graded_synth["labeled"] + len(real_detail)
    matches = graded_synth["matches"] + real_hits
    combined = (matches / labeled) if labeled else None
    merged = {"labeled": labeled, "matches": matches, "agreement": combined,
              "synthetic": {"labeled": graded_synth["labeled"], "matches": graded_synth["matches"],
                            "agreement": graded_synth["agreement"], "detail": graded_synth["detail"]},
              "real_labels": real_detail,
              "real_agreement": (real_hits / len(real_detail)) if real_detail else None}
    checks["C2_level_accuracy"] = {
        "value": combined, "labeled": labeled, "matches": matches,
        "threshold": min_agreement, "min_labeled": min_labeled,
        "pass": bool(labeled >= min_labeled and (combined or 0) >= min_agreement),
        "detail": merged,
    }

    # C3 证据工件
    missing = []
    bad = []
    for cap, rec in records.items():
        if rec.get("executed_evidence_items", 0) <= 0:
            continue
        ap = root / rec["acceptance_path"] if not Path(rec["acceptance_path"]).is_absolute() else Path(rec["acceptance_path"])
        tp = root / rec["criteria_table_path"] if not Path(rec["criteria_table_path"]).is_absolute() else Path(rec["criteria_table_path"])
        if not ap.exists() or not tp.exists():
            missing.append(cap)
            continue
        ver = verify_acceptance(ap)
        if not ver["sha256_match"] or rec.get("criteria_rows", 0) < 1:
            bad.append({"capability": cap, "sha256_match": ver["sha256_match"],
                        "criteria_rows": rec.get("criteria_rows")})
    checks["C3_acceptance_artifacts"] = {
        "value": len(records) - len(missing) - len(bad), "threshold": min_covered,
        "pass": not missing and not bad,
        "detail": {"missing": missing, "bad": bad},
    }

    # C4 回归门
    acks = acks or {}
    violations, changes, acked = [], [], []
    base_levels = (baseline or {}).get("levels") or {}
    for cap, rec in records.items():
        old = base_levels.get(cap)
        if old is None:
            changes.append({"capability": cap, "old": None, "new": rec["level"], "change": "NEW"})
            continue
        klass = classify_change(old, rec["level"])
        refuted = bool(REFUTATION_REASONS & set(rec.get("reason_codes") or []))
        ack = acks.get(cap)
        entry = {"capability": cap, "old": old, "new": rec["level"], "change": klass,
                 "refutation_backed": refuted, "acked": bool(ack)}
        changes.append(entry)
        if klass in {"DOWNGRADE", "REFUTATION"}:
            if refuted:
                continue
            if ack and ack.get("basis"):
                acked.append({**entry, "basis": ack["basis"]})
                continue
            violations.append(entry)
    lost_evidence = [cap for cap in base_levels if cap not in records]
    legacy_real = (baseline or {}).get("legacy_real") or []
    legacy_evidence = (baseline or {}).get("legacy_evidence") or []
    legacy_detail = [{"capability": cap, "level": records.get(cap, {}).get("level"),
                      "is_real": records.get(cap, {}).get("level") == "REAL",
                      "acked": cap in acks} for cap in legacy_real]
    legacy_ok = all(d["is_real"] or d["acked"] for d in legacy_detail)
    legacy_ev_detail = [{"capability": cap,
                         "registered": cap in records,
                         "executed_evidence_items": records.get(cap, {}).get("executed_evidence_items", 0),
                         "level": records.get(cap, {}).get("level")} for cap in legacy_evidence]
    legacy_ev_ok = all(d["registered"] and d["executed_evidence_items"] > 0 for d in legacy_ev_detail)
    # 台账自述 vs 本次采集：只作对照（不影响门），因为"自述"不是证据
    claim_contrast = []
    for cap, claim in (claims or {}).items():
        got = records.get(cap, {}).get("level")
        claim_contrast.append({"capability": cap, "claimed": normalize(claim), "collected": got,
                               "status": "CONFIRMED" if got == normalize(claim) else "UNCONFIRMED"})
    checks["C4_regression_gate"] = {
        "value": len(violations), "threshold": 0,
        "pass": not violations and not lost_evidence and legacy_ok and legacy_ev_ok,
        "detail": {"violations": violations, "lost_evidence": lost_evidence,
                   "legacy_real": legacy_detail, "legacy_evidence": legacy_ev_detail,
                   "changes": changes, "acked_downgrades": acked,
                   "refutation_backed_downgrades": [c for c in changes
                                                    if c.get("change") in {"DOWNGRADE", "REFUTATION"}
                                                    and c.get("refutation_backed")],
                   "claim_contrast": claim_contrast,
                   "claim_contrast_summary": {
                       "claims": len(claim_contrast),
                       "confirmed": sum(1 for c in claim_contrast if c["status"] == "CONFIRMED"),
                       "unconfirmed": sum(1 for c in claim_contrast if c["status"] == "UNCONFIRMED"),
                   }},
    }

    # C5 零领域语义
    denylist_path = root / (spec.get("acceptance") or {}).get(
        "denylist", "config/domain_word_denylist.json")
    scan_globs = (spec.get("acceptance") or {}).get(
        "zero_domain_scan", ["tools/evidence_collection/*.py", "tests/evidence_collection/*.py"])
    files: list[Path] = []
    for pattern in scan_globs:
        files.extend(sorted(root.glob(pattern)))
    denylist = json.loads(denylist_path.read_text(encoding="utf-8")) if denylist_path.exists() else {}
    hits = _denylist_hits(files, denylist)
    checks["C5_zero_domain_semantics"] = {
        "value": len(hits), "threshold": 0, "pass": len(hits) == 0,
        "detail": {"files_scanned": [str(f.relative_to(root)) for f in files],
                   "hits": hits[:20]},
    }

    # C6 成熟化（标准 CI 集成）
    ci_rel = (spec.get("acceptance") or {}).get("ci_workflow", ".github/workflows/capability-audit.yml")
    ci_path = root / ci_rel
    ci_text = ci_path.read_text(encoding="utf-8") if ci_path.exists() else ""
    ci_markers = (spec.get("acceptance") or {}).get(
        "ci_markers", ["tools.evidence_collection", "evidence_collection"])
    ci_hits = [m for m in ci_markers if m in ci_text]
    checks["C6_ci_integration"] = {
        "value": len(ci_hits), "threshold": len(ci_markers), "pass": len(ci_hits) == len(ci_markers),
        "detail": {"workflow": ci_rel, "markers_found": ci_hits},
    }

    # C7 pytest
    runner = suite_runner or (lambda p: _run_suite(p))
    suite_path = (spec.get("acceptance") or {}).get("test_dir", "tests/evidence_collection")
    suite = runner(suite_path)
    checks["C7_pytest"] = {
        "value": suite.get("passed", 0), "failed": suite.get("failed", 0),
        "threshold": min_tests,
        "pass": suite.get("status") == "PASS" and suite.get("passed", 0) >= min_tests,
        "detail": {"path": suite_path, "status": suite.get("status"),
                   "output_tail": suite.get("output", "")},
    }

    return {"checks": checks, "all_pass": all(c["pass"] for c in checks.values()),
            "generated_at": summary.get("generated_at"), "ruleset": summary.get("ruleset")}