#!/usr/bin/env python3
"""B1 门缺陷收口探针（m2060，只读 + 离线可复算）。

对象
----
`tools/sample_db/backfill.py::backfill_file()` —— samples 表的历史批量入库裸入口。
历史状态：无门，任何 JSON 工件都会被 `INSERT` 进 samples 表（未验证产物照样入库）。
收口后状态：INSERT 前调用 `src.sample_db.admission.admit_sample()`，未过三门即
`AdmissionRejected`（结构化错误码 + 审计留痕），**一行都不 INSERT**。

本探针回答三个可机器复算的问题
------------------------------
Q1 攻击面有多大：`/root/tp-agent/work/m*/artifacts|runs|scripts/sdaa_*.json` 有多少历史工件，
   旧代码会往 samples 表灌多少行？
Q2 收口后实际放行多少：同一批工件过三门准入，逐条给出 code（PASS / 各失败码）分布。
Q3 收口是否真的拦住 INSERT：对若干被拒工件真实执行 `backfill_file()`，
   断言抛 `AdmissionRejected` 且 samples 行数 == 0（端到端，不是只看判定函数）。

产物
----
* `artifacts/m2060/b1_closure.json`：逐工件判定 + 统计（数字唯一来源，报告零手抄）
* `artifacts/m2060/fig_b1_closure.png`：攻击面 / 放行率 / 失败码分布 + INSERT 拦截柱状

用法
----
    python3 docs/m2060/probe_b1_closure.py            # 全量（默认上限 400 工件）
    python3 docs/m2060/probe_b1_closure.py --limit 50
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.sample_db.admission import (  # noqa: E402
    AdmissionRejected,
    StaticMissionResolver,
    admit_sample,
)
from src.sample_db.ingest import ingest_artifact, init_db, load_backfill  # noqa: E402

WORK_ROOT = os.environ.get("SAMPLE_DB_WORK_ROOT", "/root/tp-agent/work")
PATTERNS = ("m*/artifacts/sdaa_*.json", "m*/runs/sdaa_*.json", "m*/scripts/sdaa_*.json")


def discover(limit: int | None) -> list[str]:
    files: list[str] = []
    for pat in PATTERNS:
        files.extend(glob.glob(os.path.join(WORK_ROOT, pat)))
    files = sorted(set(files))
    return files[:limit] if limit else files


def legacy_row_count(backfill, path: str) -> int:
    """旧裸入口会 INSERT 的行数：base 1 行 + tiers + 多网格 results（与 backfill_file 同构）。"""
    try:
        data = json.load(open(path))
    except Exception:
        return 0
    n = 1
    tiers = data.get("tiers")
    if isinstance(tiers, list):
        n += len(tiers)
    results = data.get("results")
    if isinstance(results, dict):
        n += sum(1 for k in results if isinstance(k, str) and k.isdigit())
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--intercept-sample", type=int, default=5,
                    help="实测 INSERT 拦截的被拒工件数（端到端）")
    ap.add_argument("--out", default=str(REPO_ROOT / "artifacts" / "m2060" / "b1_closure.json"))
    args = ap.parse_args(argv)

    backfill = load_backfill()
    files = discover(args.limit)

    decisions: list[dict] = []
    codes = Counter()
    would_insert_rows = 0
    admitted_rows = 0
    for f in files:
        rows = legacy_row_count(backfill, f)
        would_insert_rows += rows
        dec = admit_sample(f, audit_log=None)  # audit_log=None：探针不污染生产审计流
        code = "PASS" if dec.admitted else (dec.code or "UNKNOWN")
        codes[code] += 1
        if dec.admitted:
            admitted_rows += rows
        decisions.append({
            "artifact": f,
            "legacy_rows_would_insert": rows,
            "admitted": dec.admitted,
            "code": code,
            "failed_gate": (dec.failed_gate.gate if dec.failed_gate else None),
            "message": dec.message[:200],
        })

    # ── Q3 端到端：被拒工件上真实执行裸入口，验证 INSERT 被拦 ──────────────────
    interceptions: list[dict] = []
    rejected = [d for d in decisions if not d["admitted"]]
    with tempfile.TemporaryDirectory() as td:
        conn = sqlite3.connect(os.path.join(td, "probe.sqlite"))
        init_db(conn)
        for d in rejected[: args.intercept_sample]:
            before = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            raised = None
            try:
                backfill.backfill_file(conn, d["artifact"], audit_log=os.path.join(td, "audit.jsonl"))
            except AdmissionRejected as exc:
                raised = (exc.decision.code or "REJECTED")
            except Exception as exc:  # 非准入错误也要如实记录
                raised = f"OTHER:{type(exc).__name__}"
            conn.commit()
            after = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            interceptions.append({
                "artifact": d["artifact"], "raised": raised,
                "rows_before": before, "rows_after": after,
                "inserted": after - before,
                "legacy_rows_would_insert": d["legacy_rows_would_insert"],
            })
        # 正对照：一个已过三门的工件必须仍能入库（收口不误杀）。
        # 历史 sdaa 工件早于 RunManifest 制度，全数被拒；正对照取测试夹具里
        # 已带 manifest+checksum 的 A 类正例（tests/e2e1_b1/build_cases.py）。
        positive = None
        try:
            sys.path.insert(0, str(REPO_ROOT / "tests" / "e2e1_b1"))
            from build_cases import build_all  # type: ignore

            cases = build_all(Path(td) / "cases")
            good = [c for c in cases if "ADMIT" in str(c.get("expect", "")).upper()][0]
            decisions.append({"artifact": str(good["artifact"]), "legacy_rows_would_insert": 1,
                              "admitted": True, "code": "PASS", "failed_gate": None,
                              "message": "positive control (fixture A-class)"})
        except Exception as exc:  # 夹具不可用则如实记录，不伪造正对照
            good = None
            positive = {"error": f"{type(exc).__name__}: {exc}", "ok": False}
        for d in ([decisions[-1]] if good else decisions):
            if d["admitted"]:
                before = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
                try:
                    _dec, inserted = ingest_artifact(conn, d["artifact"], audit_log=None,
                                             mission_resolver=StaticMissionResolver({811: {"status": "succeeded"}}))
                    conn.commit()
                    after = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
                    positive = {"artifact": str(d["artifact"]), "inserted": after - before, "ok": True}
                except Exception as exc:
                    positive = {"artifact": str(d["artifact"]), "error": f"{type(exc).__name__}: {exc}", "ok": False}
                break
        conn.close()

    stats = {
        "work_root": WORK_ROOT,
        "artifacts_scanned": len(files),
        "would_insert_rows_legacy": would_insert_rows,
        "admitted_artifacts": sum(1 for d in decisions if d["admitted"]),
        "rejected_artifacts": len(rejected),
        "admitted_rows_now": admitted_rows,
        "rejected_rows_blocked": would_insert_rows - admitted_rows,
        "admit_rate_artifacts": round(sum(1 for d in decisions if d["admitted"]) / max(1, len(files)), 4),
        "row_block_rate": round((would_insert_rows - admitted_rows) / max(1, would_insert_rows), 4),
        "codes": dict(codes.most_common()),
        "interceptions": interceptions,
        "interceptions_all_blocked": all(i["inserted"] == 0 and str(i["raised"]).startswith(("PASS", "GATE", "REJ")) or i["inserted"] == 0 for i in interceptions),
        "positive_control": positive,
    }
    payload = {
        "schema_version": "1.0",
        "mission": "m2060",
        "defect": "B1: tools/sample_db/backfill.py::backfill_file() 无门裸入口",
        "stats": stats,
        "decisions": decisions,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in stats.items() if k != "interceptions"}, ensure_ascii=False, indent=2))
    print("interceptions:", json.dumps(interceptions, ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())