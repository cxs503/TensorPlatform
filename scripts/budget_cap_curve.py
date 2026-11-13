#!/usr/bin/env python3
"""FP15-BUDGET-ACCOUNTING ② —— 预算帽 ↔ 成功率曲线（零手抄，全量 API 复算）。

做什么
------
从 missions API（``GET {TENSOR_API_BASE}/tensor/missions``）拉**全部终态单**，
按 ``budget.max_tool_calls`` 分档输出

    {cap, n, succ, fail, rate, near_cap_rate}

并同时给出 24h 窗口子集（同 schema），窗口起止时间戳写在 ``window`` 里。
分母口径写死在代码里（不是口头约定）：``fail`` = 终态但非 ``succeeded``
（failed/stopped/revoked/orphan 全算没成功），``rate`` = succ / n。

关键设计
--------
* **零手抄**：每个数字都从 API 响应算出来，脚本自带一致性自检
  （分档 n 之和 == 群体 n；群体 n == API 各终态 status 的 total 之和，delta 必须 ±0）。
  自检不过 → 退出码 2（机器门可失败），绝不悄悄输出半张表。
* **用量出处必须可见**：``budget_used.tool_calls`` 优先；缺失则退回
  ``result`` 事实（``services/budget_accounting.py`` 的同一套推导），并在
  ``usage_sources`` 里如实计数。历史单没有预算记账时 ``near_cap_rate`` 的
  分母（``usage_known``）会显著缩小——这一栏**不允许**用 0 冒充算过。
* **n >= 100 才算一档**：不足 100 的档照常输出但标 ``below_floor``，
  不参与"建议"。

用法::

    python scripts/budget_cap_curve.py                     # 默认 24h 窗口
    python scripts/budget_cap_curve.py --hours 72 --out artifacts/fp15/budget_cap_curve.json
    TENSOR_API_BASE=http://127.0.0.1:8010/api/v1 python scripts/budget_cap_curve.py
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:          # 复用单一真源：预算推导
    sys.path.insert(0, str(BACKEND))

from services.budget_accounting import derive_budget_used, SOURCE_UNAVAILABLE  # noqa: E402

PAGE_SIZE = 200                            # API 上限（tensor_missions.list_missions clamp）
N_FLOOR = 100
#: "贴帽" 判据：与 FP15 实测口径一致，缺 5 次以内视为贴帽
NEAR_CAP_MARGIN = 5
#: 终态词表单一真源（仓内契约文件，不硬编码）
VOCAB = REPO / "backend" / "contracts" / "mission_status_vocab.v1.json"


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _api_base() -> str:
    return os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")


def _login(base: str) -> str:
    token = os.environ.get("TENSOR_TOKEN")
    if token:
        return token
    user = os.environ.get("TENSOR_AGENT_USER") or os.environ.get("ECO_ADMIN_USER")
    password = os.environ.get("TENSOR_AGENT_PASS") or os.environ.get("ECO_ADMIN_PASS")
    if not user or not password:
        raise SystemExit("need TENSOR_TOKEN or TENSOR_AGENT_USER/TENSOR_AGENT_PASS")
    req = urllib.request.Request(
        f"{base}/auth/login",
        data=json.dumps({"username": user, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    return payload["access_token"]


def _get(base: str, token: str, path: str) -> dict[str, Any]:
    req = urllib.request.Request(f"{base}{path}",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def terminal_statuses() -> list[str]:
    """终态词表从仓内契约文件读（改词表=改契约，脚本自动跟随）。"""
    return list(json.loads(VOCAB.read_text())["terminal"])


def fetch_terminal_missions(base: str, token: str, *,
                            max_pages: int = 400) -> tuple[list[dict], dict[str, int]]:
    """Pull every terminal mission, page by page. Returns (rows, api_total_by_status).

    ``api_total_by_status`` is the API's own ``total`` for each status filter —
    the independent counter the checks compare against (±0).
    """
    rows: list[dict] = []
    totals: dict[str, int] = {}
    for status in terminal_statuses():
        offset, total = 0, None
        while True:
            if len(rows) // PAGE_SIZE > max_pages:
                raise SystemExit(f"page guard tripped at {len(rows)} rows — refusing "
                                 "to report a truncated population")
            query = urllib.parse.urlencode({"status": status, "limit": PAGE_SIZE,
                                            "offset": offset})
            payload = _get(base, token, f"/tensor/missions?{query}")
            items = payload.get("items") or []
            total = int(payload.get("total") or 0)
            rows.extend(items)
            offset += len(items)
            if not items or offset >= total:
                break
        totals[status] = int(total or 0)
        fetched = sum(1 for r in rows if r.get("status") == status)
        if fetched != totals[status]:
            raise SystemExit(f"status {status}: fetched {fetched} of API total {totals[status]}")
    return rows, totals


# ── per-mission facts ────────────────────────────────────────────────────────

def _as_iso(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def cap_of(mission: dict) -> int | None:
    budget = mission.get("budget")
    if isinstance(budget, str):
        try:
            budget = json.loads(budget)
        except ValueError:
            budget = {}
    if not isinstance(budget, dict):
        return None
    try:
        cap = int(budget.get("max_tool_calls"))
    except (TypeError, ValueError):
        return None
    return cap if cap > 0 else None


def usage_of(mission: dict) -> tuple[int | None, str]:
    """(tool_calls_used, source). Prefers the persisted record, then result facts."""
    record = mission.get("budget_used")
    if isinstance(record, str):
        try:
            record = json.loads(record)
        except ValueError:
            record = {}
    if isinstance(record, dict) and isinstance(record.get("tool_calls"), int):
        return int(record["tool_calls"]), str(record.get("source") or "budget_used")
    derived = derive_budget_used(result=mission.get("result"))
    tool_calls = derived["tool_calls"]
    source = derived["sources"]["tool_calls"]
    return (tool_calls, source) if source != SOURCE_UNAVAILABLE else (None, SOURCE_UNAVAILABLE)


# ── aggregation ──────────────────────────────────────────────────────────────

def _pct(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 2) if whole else None


def _median(values: list[int]) -> float | None:
    return float(statistics.median(values)) if values else None


def aggregate_missions(rows: Iterable[dict], *, n_floor: int = N_FLOOR,
                       near_cap_margin: int = NEAR_CAP_MARGIN) -> dict[str, Any]:
    """Group terminal missions by ``max_tool_calls`` cap.

    Pure function over already-fetched mission dicts — the unit the tests pin.
    Denominators are explicit: ``rate`` = succ/n over all rows in the bucket,
    ``near_cap_rate`` / ``at_cap_rate`` = share of rows **with known usage**
    (``usage_known``), so a bucket without usage data reports ``None`` instead
    of a fake 0%.
    """
    rows = list(rows)
    buckets: dict[Any, dict[str, Any]] = {}
    usage_sources: dict[str, int] = {}
    for mission in rows:
        cap = cap_of(mission)
        key = cap if cap is not None else "unset"
        bucket = buckets.setdefault(key, {
            "cap": key, "n": 0, "succ": 0, "fail": 0,
            "usage_known": 0, "near_cap": 0, "at_cap": 0,
            "all_usage": [], "succ_usage": [],
        })
        bucket["n"] += 1
        succeeded = mission.get("status") == "succeeded"
        bucket["succ" if succeeded else "fail"] += 1
        used, source = usage_of(mission)
        usage_sources[source] = usage_sources.get(source, 0) + 1
        if used is None:
            continue
        bucket["usage_known"] += 1
        bucket["all_usage"].append(used)
        if succeeded:
            bucket["succ_usage"].append(used)
        if isinstance(cap, int):
            if used >= cap - near_cap_margin:
                bucket["near_cap"] += 1
            if used >= cap:
                bucket["at_cap"] += 1

    out_buckets: list[dict[str, Any]] = []
    for bucket in buckets.values():
        n = bucket["n"]
        known = bucket["usage_known"]
        out_buckets.append({
            "cap": bucket["cap"],
            "n": n,
            "succ": bucket["succ"],
            "fail": bucket["fail"],
            "rate": _pct(bucket["succ"], n),
            "near_cap_rate": _pct(bucket["near_cap"], known),
            "at_cap_rate": _pct(bucket["at_cap"], known),
            "usage_known": known,
            "usage_unknown": n - known,
            "tool_calls_p50_succ": _median(bucket["succ_usage"]),
            "tool_calls_p50_all": _median(bucket["all_usage"]),
            "below_floor": n < n_floor,
        })
    out_buckets.sort(key=lambda b: (b["cap"] == "unset", b["cap"] if isinstance(b["cap"], int) else 10 ** 9))

    total_n = sum(b["n"] for b in out_buckets)
    total_succ = sum(b["succ"] for b in out_buckets)
    return {
        "buckets": out_buckets,
        "totals": {
            "n": total_n,
            "succ": total_succ,
            "fail": total_n - total_succ,
            "rate": _pct(total_succ, total_n),
            "usage_known": sum(b["usage_known"] for b in out_buckets),
            "usage_unknown": sum(b["usage_unknown"] for b in out_buckets),
        },
        "n_floor": n_floor,
        "near_cap_margin": near_cap_margin,
        "buckets_ge_floor": [b["cap"] for b in out_buckets if not b["below_floor"]],
        "usage_sources": usage_sources,
    }


def filter_window(rows: Iterable[dict], since: datetime, until: datetime) -> list[dict]:
    s, u = since, until
    keep = []
    for mission in rows:
        stamp = _as_iso(mission.get("finished_at"))
        if stamp is None:
            continue
        if s <= stamp <= u:
            keep.append(mission)
    return keep


def build_report(rows: list[dict], totals_by_status: dict[str, int], *,
                 hours: float, now: datetime | None = None,
                 base: str | None = None, n_floor: int = N_FLOOR) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    since = moment - timedelta(hours=hours)
    population = aggregate_missions(rows, n_floor=n_floor)
    window_rows = filter_window(rows, since, moment)
    window = aggregate_missions(window_rows, n_floor=n_floor)

    api_total = sum(totals_by_status.values())
    checks = {
        "api_total": api_total,
        "api_total_by_status": totals_by_status,
        "population_n": population["totals"]["n"],
        "population_delta_vs_api": population["totals"]["n"] - api_total,
        "buckets_sum_equals_population":
            sum(b["n"] for b in population["buckets"]) == population["totals"]["n"],
        "window_buckets_sum_equals_window_total":
            sum(b["n"] for b in window["buckets"]) == window["totals"]["n"],
        "window_rows": len(window_rows),
        "window_is_subset": len(window_rows) <= len(rows),
    }
    checks["all_zero_delta"] = (
        checks["population_delta_vs_api"] == 0
        and checks["buckets_sum_equals_population"]
        and checks["window_buckets_sum_equals_window_total"]
        and checks["window_is_subset"]
    )
    return {
        "generated_at": moment.replace(microsecond=0).isoformat(),
        "schema": "fp15.budget_cap_curve.v1",
        "api_base": base or _api_base(),
        "window": {
            "since": since.replace(microsecond=0).isoformat(),
            "until": moment.replace(microsecond=0).isoformat(),
            "hours": hours,
            "definition": "finished_at in [since, until]; population = all terminal missions",
        },
        "terminal_statuses": terminal_statuses(),
        "population": population,
        "window_stats": window,
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FP15 budget cap ↔ success-rate curve")
    parser.add_argument("--hours", type=float, default=24.0, help="window length (default 24h)")
    parser.add_argument("--out", default="artifacts/fp15/budget_cap_curve.json")
    parser.add_argument("--n-floor", type=int, default=N_FLOOR)
    parser.add_argument("--base", default=None, help="API base (default TENSOR_API_BASE)")
    args = parser.parse_args(argv)

    base = (args.base or _api_base()).rstrip("/")
    token = _login(base)
    rows, totals_by_status = fetch_terminal_missions(base, token)
    report = build_report(rows, totals_by_status, hours=args.hours, base=base,
                          n_floor=args.n_floor)

    out = Path(args.out)
    if not out.is_absolute():
        out = REPO / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    def _print_table(title: str, section: dict[str, Any]) -> None:
        print(f"\n== {title} (n={section['totals']['n']}) ==")
        print(f"{'cap':>6} {'n':>6} {'succ':>6} {'fail':>6} {'rate%':>7} "
              f"{'near_cap%':>10} {'at_cap%':>8} {'usage_known':>12} {'p50_succ':>9}")
        for bucket in section["buckets"]:
            flag = " (below floor)" if bucket["below_floor"] else ""
            print(f"{str(bucket['cap']):>6} {bucket['n']:>6} {bucket['succ']:>6} "
                  f"{bucket['fail']:>6} {str(bucket['rate']):>7} "
                  f"{str(bucket['near_cap_rate']):>10} {str(bucket['at_cap_rate']):>8} "
                  f"{bucket['usage_known']:>12} {str(bucket['tool_calls_p50_succ']):>9}{flag}")

    print(f"api={base} population={report['checks']['population_n']} "
          f"api_total={report['checks']['api_total']} delta={report['checks']['population_delta_vs_api']}")
    _print_table("population (all terminal)", report["population"])
    _print_table(f"window ({args.hours}h)", report["window_stats"])
    print(f"\nchecks: {json.dumps(report['checks'], ensure_ascii=False)}")
    print(f"artifact: {out}")
    return 0 if report["checks"]["all_zero_delta"] else 2


if __name__ == "__main__":
    raise SystemExit(main())