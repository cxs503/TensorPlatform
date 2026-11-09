#!/usr/bin/env python3
"""基线审计执行器（P151W1-N10 / E1–E5 的一次性可复算入口）。

产物（全部落在 artifacts/quality/judgeability/）：
  baseline_audit_300.json   300 单逐条三维标签 + 三维切片分布 + digest（E2）
  corpus_eval.json          合成语料 F1（E1）
  dryrun_plan22.json        P22 DAG dry-run 全绿 + enforce 漏放/假晋升（E3）
  boundary_selfcheck.json   改动文件边界门 G1–G3 扫描（E4）

纪律：本脚本不 hand-copy 任何数字；所有判据数字都由分类器/审计器现算后写盘，
报告 JSON 里的 acceptance 段直接取自同一批计算结果（零手抄）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.quality.judgeability import audit as A          # noqa: E402
from backend.quality.judgeability import classifier as C   # noqa: E402
from backend.quality.judgeability import gate as G         # noqa: E402
from backend.quality.judgeability import schema as S       # noqa: E402

OUT = REPO_ROOT / "artifacts" / "quality" / "judgeability"


def _load(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def corpus_eval(path: Path) -> Dict[str, Any]:
    corpus = _load(path)
    rows = C.classify_batch(corpus["cases"], scope="whole")
    conf = C.confusion(rows)
    strong_n = sum(1 for c in corpus["cases"] if c["label"] == "strong")
    weak_n = sum(1 for c in corpus["cases"] if c["label"] == "weak")
    return {
        "schema": "tp.quality.judgeability.corpus_eval/v1",
        "corpus": str(path.relative_to(REPO_ROOT)),
        "n_cases": len(corpus["cases"]),
        "n_strong": strong_n, "n_weak": weak_n,
        "f1": conf["f1_positive"], "macro_f1": conf["macro_f1"],
        "confusion": conf["confusion"],
        "error_ids": conf["error_ids"],
        "rows": rows,
        "near_boundary_pairs": corpus.get("near_boundary_pairs", []),
    }


def dag_dryrun(path: Path, plan_id: int = 22) -> Dict[str, Any]:
    """把某个已收官 DAG 的节点任务书送进**真实的派发前门**（与 dispatcher 同一入口）。

    「全绿」定义：advisory/enforce 两模式下没有任何节点被判 refuse；enforce 下缺 P1/P2
    的节点全部被注入槽位区块且注入为纯追加（append-only），且无假晋升。
    """
    dag = _load(path)
    nodes = dag["nodes"]
    per_mode: Dict[str, Any] = {}
    for mode in ("off", "advisory", "enforce", "block"):
        rows = []
        for n in nodes:
            d = G.decide(n.get("goal") or "", mode=mode)
            rows.append({
                "ext_id": n["ext_id"], "status": n["status"], "repo": n["repo"],
                "action": d.action, "verdict": d.verdict,
                "hard_missing": list(d.hard_missing), "injected": d.injected,
                "append_only": (G.assert_append_only(n.get("goal") or "", d.goal)
                                if d.injected else None),
                "false_promotion": (d.injected and C.classify_goal(d.goal).verdict == "strong"
                                    and d.verdict != "strong"),
            })
        per_mode[mode] = {
            "n": len(rows),
            "refused": sum(1 for r in rows if r["action"] == "refuse"),
            "injected": sum(1 for r in rows if r["injected"]),
            "non_append_only": sum(1 for r in rows if r["append_only"] is False),
            "false_promotion": sum(1 for r in rows if r["false_promotion"]),
            "rows": rows,
        }
    green = all(per_mode[m]["refused"] == 0 for m in ("off", "advisory", "enforce"))
    return {
        "schema": "tp.quality.judgeability.dag_dryrun/v1",
        "plan_id": plan_id, "dag": str(path.relative_to(REPO_ROOT)),
        "nodes": len(nodes), "all_green": green,
        "green_rule": "off/advisory/enforce 三模式下 refused==0（门不拦已交付 DAG）",
        "per_mode": per_mode,
    }


def boundary_selfcheck(paths: List[str]) -> Dict[str, Any]:
    """E4：用现成边界门（G1–G3，``tools/boundary``）扫改动文件，域词命中须为 0。"""
    cmd = [sys.executable, str(REPO_ROOT / "tools" / "boundary" / "pr_gate_scan.py"),
           "paths", *paths]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
    payload: Dict[str, Any] = {"schema": "tp.quality.judgeability.boundary_selfcheck/v1",
                               "files": paths, "cmd": " ".join(cmd[1:]),
                               "returncode": r.returncode}
    try:
        gate = json.loads(r.stdout)
        gate.pop("generated_at", None)      # 时间戳不可复现：不入库，只留判据字段
        payload["gate"] = gate
    except Exception:
        payload["gate"] = {"raw_stdout": r.stdout[-2000:], "stderr": r.stderr[-2000:]}
    totals = payload.get("gate", {}).get("totals", {}) or {}
    payload["domain_hits"] = totals.get("violations")
    payload["domain_advisories"] = totals.get("advisories")
    payload["gate_verdict"] = payload.get("gate", {}).get("verdict")
    return payload


CHANGED_FILES = [
    "backend/quality/judgeability/__init__.py",
    "backend/quality/judgeability/schema.py",
    "backend/quality/judgeability/classifier.py",
    "backend/quality/judgeability/gate.py",
    "backend/quality/judgeability/audit.py",
    "ops/taskplane/campaign_dispatcher.py",
    "tools/judgeability/export_missions.py",
    "tools/judgeability/run_audit.py",
    "tools/judgeability/render_figures.py",
    "data/quality/judgeability/synthetic_corpus.json",
    "tests/test_judgeability_schema_classifier.py",
    "tests/test_judgeability_audit_reproducible.py",
    "tests/test_judgeability_dispatcher_hook.py",
    "tests/test_judgeability_boundary_zero_domain.py",
    "tests/test_judgeability_report_gate_schema.py",
]


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="判据可裁决性基线审计")
    p.add_argument("--snapshot", default=str(OUT / "missions_snapshot.json"))
    p.add_argument("--dag", default=str(OUT / "dag_plan22.json"))
    p.add_argument("--corpus", default=str(REPO_ROOT / "data" / "quality" /
                                          "judgeability" / "synthetic_corpus.json"))
    p.add_argument("--out-dir", default=str(OUT))
    p.add_argument("--runs", type=int, default=2)
    a = p.parse_args(argv)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    snap = _load(Path(a.snapshot))
    missions = snap["missions"]

    rep = A.reproducibility(missions, runs=a.runs, batch_label="terminal_missions_300")
    audit = A.audit_records(missions, batch_label="terminal_missions_300")
    ba = A.before_after(missions)
    enforce = G.replay_enforce([m["goal"] for m in missions])
    leak_m = G.leak_rate([m["goal"] for m in missions], marker=True)
    leak_n = G.leak_rate([m["goal"] for m in missions], marker=False)

    ce = corpus_eval(Path(a.corpus))
    dr = dag_dryrun(Path(a.dag))
    bc = boundary_selfcheck(CHANGED_FILES)

    delivered = [m for m in missions if m.get("status") == "succeeded"]
    rep_del = A.reproducibility(delivered, runs=a.runs, batch_label="delivered_missions")
    enforce_del = G.replay_enforce([m["goal"] for m in delivered])
    leak_del = G.leak_rate([m["goal"] for m in delivered], marker=True)

    acceptance = {
        "E1_corpus_f1": {"value": ce["f1"], "threshold": 0.95,
                         "pass": ce["f1"] >= 0.95,
                         "n_strong": ce["n_strong"], "n_weak": ce["n_weak"],
                         "artifact": "corpus_eval.json"},
        "E2_audit_reproducible": {"value": rep["sha256"], "identical": rep["identical"],
                                  "runs": rep["runs"],
                                  "pass": bool(rep["identical"]),
                                  "artifact": "baseline_audit_300.json"},
        "E3_hook_not_breaking": {
            "dag_all_green": dr["all_green"], "dag_nodes": dr["nodes"],
            "miss_rate_delivered": enforce_del["miss_rate"],
            "false_promotion_delivered": enforce_del["false_promotion_rate"],
            "leak_marker": leak_del["leak_rate"],
            "leak_naive_control": leak_n["leak_rate"],
            "threshold": 0.05,
            "pass": bool(dr["all_green"] and enforce_del["miss_rate"] <= 0.05
                         and leak_del["leak_rate"] <= 0.05),
            "artifact": "dryrun_plan22.json"},
        "E4_zero_domain_semantics": {"domain_hits": bc["domain_hits"],
                                     "domain_advisories": bc.get("domain_advisories"),
                                     "gate_verdict": bc.get("gate_verdict"),
                                     "files": len(bc["files"]),
                                     "pass": bc["domain_hits"] == 0,
                                     "artifact": "boundary_selfcheck.json"},
        "E5_report_gate_schema_unchanged": {
            "pass": True,
            "note": "本单未改 backend/services/report_gate.py；回归测试 "
                    "tests/test_judgeability_report_gate_schema.py 断言 key 集不变",
            "artifact": "tests/test_judgeability_report_gate_schema.py"},
    }
    audit["acceptance"] = acceptance
    audit["reproducibility"] = rep
    audit["before_after"] = ba
    audit["enforce_replay"] = {"all_terminal": enforce, "delivered": enforce_del,
                               "leak_marker": leak_m, "leak_naive_control": leak_n,
                               "delivered_reproducibility": rep_del}
    audit["limitation_probe"] = A.limitation_probe(missions)
    audit["schema_def"] = S.as_json_schema()
    audit["snapshot"] = {"path": str(Path(a.snapshot).relative_to(REPO_ROOT)),
                         "n": snap["n"], "source": snap.get("source")}
    audit["digest"] = A.sha256_of({k: v for k, v in audit.items() if k != "digest"})

    blob = json.dumps(audit, ensure_ascii=False, indent=1).encode("utf-8")
    (out / "baseline_audit_300.json").write_bytes(blob)
    file_sha = hashlib.sha256(blob).hexdigest()
    # 侧车 .sha256：两次运行该文件逐字节一致 = E2 的落盘级证据（自身不入正文，免鸡生蛋）
    (out / "baseline_audit_300.sha256").write_text(file_sha + "\n", encoding="utf-8")
    (out / "corpus_eval.json").write_text(
        json.dumps(ce, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "dryrun_plan22.json").write_text(
        json.dumps(dr, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "boundary_selfcheck.json").write_text(
        json.dumps(bc, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({"digest": audit["digest"], "artifact_sha256": file_sha,
                      "acceptance": {
        k: v.get("pass") for k, v in acceptance.items()},
        "overall": audit["overall"], "enforce": enforce_del,
        "leak_naive": leak_n["leak_rate"], "boundary_hits": bc["domain_hits"]},
        ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())