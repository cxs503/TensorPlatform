"""cycle_baseline.provenance —— 零手抄的证据链：每行带 SQL 原文 + 结果 sha256。

纪律（战役判据 c）
------------------
基线表的**每一行**都必须能回答三个问题：这数字从哪条 SQL 来？重跑会不会一样？谁改过？
因此每行携带：

* ``sql``：产出该行原值的 SQL 原文（不是"见某文档"，是可直接粘贴执行的字面量）；
* ``sql_sha256``：SQL 原文摘要（防事后改语句）；
* ``values``：该行数值载荷（canonical JSON）；
* ``value_sha256``：载荷摘要 —— 重算结果不一致即暴露漂移。

``verify_row`` 只做一件事：把载荷重新哈希并与登记值比对。报告里的数字全部由
``build_baseline_table`` 程序化注入，不经人手抄。
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from .spec import canonical_json, sha256_of


def seal_row(key: str, population: str, values: dict[str, Any], sql: str,
             extra: dict[str, Any] | None = None) -> dict[str, Any]:
    row = {
        "key": key,
        "population": population,
        "values": values,
        "value_sha256": sha256_of(values),
        "sql": sql,
        "sql_sha256": sha256_of(sql),
    }
    if extra:
        row.update(extra)
    return row


def verify_row(row: dict[str, Any]) -> bool:
    if not row.get("sql") or not row.get("value_sha256"):
        return False
    if sha256_of(row["sql"]) != row.get("sql_sha256"):
        return False
    return sha256_of(row.get("values") or {}) == row["value_sha256"]


def verify_table(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    bad = [r.get("key") for r in rows if not verify_row(r)]
    return {"n_rows": len(rows), "n_bad": len(bad), "bad_keys": bad,
            "all_ok": not bad and bool(rows)}


def dedupe_numeric(values: dict[str, Any]) -> dict[str, Any]:
    """载荷只保留可复算的数值/等级字段（排除时间戳与路径，避免"同数据不同 sha"）。"""
    drop = {"generated_utc", "elapsed_s", "duration_s"}
    return {k: v for k, v in values.items() if k not in drop}


def table_from_ledger(ledger: dict[str, Any], sql_map: dict[str, str],
                      segments: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for seg in segments:
        entry = ledger.get(seg) or {}
        rows.append(seal_row(
            key=seg, population="cohort_terminal",
            values=dedupe_numeric({k: entry.get(k) for k in
                                   ("n", "coverage", "grade", "p50", "p90", "p99",
                                    "mean_s", "share_of_measured_total",
                                    "share_complete_case_n", "negative_share")}),
            sql=sql_map.get(seg, ""),
        ))
    cyc = ledger.get("cycle_s") or {}
    rows.append(seal_row(
        key="cycle_s", population="cohort_terminal",
        values=dedupe_numeric({k: cyc.get(k) for k in ("n", "coverage", "p50", "p90", "p99",
                                                       "mean_s")}),
        sql=sql_map.get("cycle_s", "")))
    return rows


def table_from_attribution(attribution: Sequence[dict[str, Any]],
                           sql_map: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for a in attribution:
        rows.append(seal_row(
            key=f"{a['status']}|{a['repo']}", population="cohort_terminal_attribution",
            values=dedupe_numeric(a), sql=sql_map.get("attribution", "")))
    return rows