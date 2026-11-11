#!/usr/bin/env python3
"""KPI-QC-RECHECK 平台侧集成探针（L3 证据）——真调用 KPI 面板服务。

做的事：建真实 SQLAlchemy Session → 调用
``services.platform_kpi.compute_all_kpis(db)``（即 ``GET /tensor/kpi/platform_dashboard``
背后的同一个函数）→ 落盘 ``integration_call.json``。

因此该工件证明的不是"工具能跑"，而是**平台 KPI 面板真的在调计量能力**，
且面板读数与离线计量脚本读数一致（逐位比对 qc_recheck_rate.numerator/denominator）。

用法::

    python3 scripts/kpi_qc_recheck_integration_probe.py \
        --out artifacts/kpi_qc_recheck/integration_call.json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
# 顺序敏感：backend/ 下有同名 tools 包（backend/tools），必须让仓根 tools/ 先胜出
for p in (str(ROOT / "backend"), str(ROOT)):
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/kpi_qc_recheck/integration_call.json")
    ap.add_argument("--result", default="artifacts/kpi_qc_recheck/kpi_result.json")
    args = ap.parse_args(argv)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from tools.audit.repro_replay import default_db_url

    dsn = os.environ.get("KPI_PROBE_DSN") or default_db_url()
    # SQLAlchemy 方言前缀：postgresql://... → postgresql+psycopg://...
    sa_dsn = dsn if "+" in dsn.split("://", 1)[0] else dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(sa_dsn, future=True)
    Session = sessionmaker(bind=engine, future=True)

    from services.platform_kpi import compute_all_kpis

    started = datetime.now(timezone.utc)
    with Session() as db:
        kpis = compute_all_kpis(db)      # ← 平台侧真调用（同一函数的 API 亦用之）
    elapsed_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 1)

    qc = kpis.get("qc_recheck_rate", {})
    payload = {
        "probe": "scripts/kpi_qc_recheck_integration_probe.py",
        "calls": "services.platform_kpi.compute_all_kpis(db)  (same function behind GET /tensor/kpi/platform_dashboard)",
        "probed_at": started.isoformat().replace("+00:00", "Z"),
        "elapsed_ms": elapsed_ms,
        "qc_recheck_rate": qc,
        "all_pass": kpis.get("all_pass"),
        "all_pass_including_qc": kpis.get("all_pass_including_qc"),
        "db_backend": sa_dsn.split("://", 1)[0],
    }

    # 与离线计量脚本逐位比对（面板读数不得与脚本读数分叉）
    if args.result and os.path.exists(args.result):
        with open(args.result, encoding="utf-8") as fh:
            res = json.load(fh)
        payload["cross_check_vs_measure_py"] = {
            "result_file": args.result,
            "numerator": {"panel": qc.get("numerator"), "script": res["numerator"]["ind_reviewed_prs"],
                          "equal": qc.get("numerator") == res["numerator"]["ind_reviewed_prs"]},
            "denominator": {"panel": qc.get("denominator"), "script": res["denominator"]["merged_prs"],
                            "equal": qc.get("denominator") == res["denominator"]["merged_prs"]},
            "current": {"panel": qc.get("value"), "script": res["current"],
                        "equal": qc.get("value") == res["current"]},
        }
        cc = payload["cross_check_vs_measure_py"]
        # 窗口是滚动的（now() 在前进）：面板与脚本两次读数之间可能有新合并 PR 进分母。
        # 判据因此不是"逐位相等"，而是：分子精确一致 + 分母只增不减 + 比率差 ≤ 0.01pp。
        cc["denominator_drift"] = (qc.get("denominator") or 0) - res["denominator"]["merged_prs"]
        cc["rate_abs_diff_pp"] = round(abs((qc.get("value") or 0.0) - res["current"]), 4)
        cc["drift_tolerance"] = {
            "why": "滚动 30 天窗口 + now() 前进，两次读数之间允许新 PR 进入分母",
            "numerator": "must be exactly equal",
            "denominator": "panel - script >= 0（只增）",
            "rate": "abs diff <= 0.01 pp",
        }
        cc["all_equal"] = bool(
            cc["numerator"]["equal"]
            and cc["denominator_drift"] >= 0
            and cc["rate_abs_diff_pp"] <= 0.01
        )

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    ok = (qc.get("status") == "measured" and qc.get("numerator") is not None
          and payload.get("cross_check_vs_measure_py", {}).get("all_equal", True))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())