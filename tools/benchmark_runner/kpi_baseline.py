"""外部基线捕获：把 KPI ETL 口径的 ``unattended_cases`` 值连同 SQL 溯源一起冻结成工件。

为什么要单独冻结一份基线
------------------------
战役要求"unattended_cases 从 65 推到 ≥75"。这个 65 来自平台 KPI ETL 的
**DB 代理口径**（游走线成功案例去重数，SQL 见下）。若报告里只写"现在是 94"，
没人能复核"从哪来"；若运行期临时查库，历史会被后来者改写（别人再跑单，基线就漂了）。
所以：**捕获一次、冻结成带 SQL 与名字清单的工件、再哈希**，后续对账只认这份工件。

口径**不改**上游 ETL
--------------------
本工具不修改 ``tools/guide_kpi_ledger.py`` 的定义，也不写库；它只把当前值按 ETL
同一条 SQL 读出来存证。引擎侧增量由 :mod:`tools.benchmark_runner.unattended`
用 case_id 求差并入（重叠显式扣除）。

fail-closed
-----------
查库失败 / psql 不可用 / 结果为 0 行 → ``status`` 分别为
``unavailable`` / ``unavailable`` / ``empty``，**不写 value**（"查不到" ≠ "65 还在"）。

CLI::

    python3 -m tools.benchmark_runner.kpi_baseline --capture \
        --out artifacts/benchmark_runner/kpi_baseline.json
    python3 -m tools.benchmark_runner.kpi_baseline --show \
        --out artifacts/benchmark_runner/kpi_baseline.json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from . import artifact as art

# 与 tools/guide_kpi_ledger.py 中 KPI_SPECS['unattended_cases'] 的 SQL 完全一致
KPI_SQL = """SELECT count(DISTINCT substring(goal from '案例 ([a-zA-Z0-9_]+)（')) AS cases
                FROM tensor_dev_missions
                WHERE goal LIKE '%案例 %（Tensor%' AND status='succeeded'"""

# 名字清单（用于和引擎 case_id 求差，避免重复计数）
CASES_SQL = """SELECT DISTINCT substring(goal from '案例 ([a-zA-Z0-9_]+)（') AS case_name
                 FROM tensor_dev_missions
                 WHERE goal LIKE '%案例 %（Tensor%' AND status='succeeded'
                 ORDER BY case_name"""


def build_record(value: Optional[int], case_names: Sequence[str], db: Dict[str, Any],
                 captured_at: Optional[str] = None, sql: str = KPI_SQL,
                 cases_sql: str = CASES_SQL) -> Dict[str, Any]:
    """组装基线工件（纯函数：无 IO，可单测）。"""
    names = sorted({str(n) for n in case_names})
    if value is None:
        status = "unavailable"
    elif not names:
        status = "empty"
    else:
        status = "ok"
    record: Dict[str, Any] = {
        "metric": "unattended_cases",
        "definition": ("KPI ETL DB proxy: distinct case names parsed out of succeeded mission goals "
                       "(see tools/guide_kpi_ledger.py KPI_SPECS['unattended_cases'])"),
        "sql": " ".join(sql.split()),
        "cases_sql": " ".join(cases_sql.split()),
        "db": {k: db.get(k) for k in ("host", "database", "user") if db.get(k) is not None},
        "captured_at": captured_at or art.utc_now(),
        "status": status,
        "value": None if value is None else int(value),
        "case_count": len(names),
        "case_ids": names,
        "case_ids_sha256": art.sha256_obj(names),
    }
    if status == "ok" and int(value) != len(names):
        # 计数与清单不一致 → 说明 SQL 之间产生了竞态；不猜，直接标脏
        record["status"] = "inconsistent"
        record["reason"] = f"count {value} != distinct names {len(names)}"
    return record


def capture(out: str | pathlib.Path, host: str, database: str, user: str,
            password: Optional[str], timeout_s: float = 30.0) -> Dict[str, Any]:
    """用 psql 跑 ETL 的两条 SQL（只读），返回基线工件并落盘。"""
    out_path = pathlib.Path(out)
    if shutil.which("psql") is None:
        record = build_record(None, [], {"host": host, "database": database, "user": user})
        record["reason"] = "psql not found on PATH: cannot capture baseline"
        art.write_json(out_path, record)
        return record
    env = dict(os.environ)
    if password:
        env["PGPASSWORD"] = password
    base_cmd = ["psql", "-h", host, "-d", database, "-U", user, "-A", "-t", "-v",
                "ON_ERROR_STOP=1", "-c"]
    try:
        raw_count = subprocess.run(base_cmd + [KPI_SQL], env=env, capture_output=True,
                                   text=True, timeout=timeout_s, check=True).stdout
        raw_names = subprocess.run(base_cmd + [CASES_SQL], env=env, capture_output=True,
                                   text=True, timeout=timeout_s, check=True).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        record = build_record(None, [], {"host": host, "database": database, "user": user})
        record["reason"] = f"{type(exc).__name__}: {exc}"
        art.write_json(out_path, record)
        return record
    value: Optional[int]
    try:
        value = int(raw_count.strip().splitlines()[0])
    except (ValueError, IndexError):
        value = None
    names: List[str] = [ln.strip() for ln in raw_names.splitlines() if ln.strip()]
    record = build_record(value, names, {"host": host, "database": database, "user": user})
    art.write_json(out_path, record)
    record["artifact"] = str(out_path)
    return record


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="KPI 外部基线捕获（只读 SQL + 工件冻结）")
    ap.add_argument("--out", default="artifacts/benchmark_runner/kpi_baseline.json")
    ap.add_argument("--capture", action="store_true", help="查库并冻结基线工件")
    ap.add_argument("--show", action="store_true", help="打印已有基线工件")
    ap.add_argument("--host", default=os.environ.get("TP_PG_HOST", "127.0.0.1"))
    ap.add_argument("--database", default=os.environ.get("TP_PG_DB", "tensorplatform"))
    ap.add_argument("--user", default=os.environ.get("TP_PG_USER", "foundation"))
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args(argv)

    if args.capture:
        password = os.environ.get("TP_PG_PASSWORD") or os.environ.get("PGPASSWORD")
        record = capture(args.out, args.host, args.database, args.user, password, args.timeout)
    elif args.show:
        p = pathlib.Path(args.out)
        if not p.is_file():
            print(f"baseline not found: {p}", file=sys.stderr)
            return 1
        record = art.read_json(p)
    else:
        ap.print_help()
        return 3
    print(json.dumps({k: v for k, v in record.items() if k != "case_ids"}, indent=2,
                     ensure_ascii=False))
    return 0 if record.get("status") == "ok" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())