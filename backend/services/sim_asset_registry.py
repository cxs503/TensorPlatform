"""simulation_assets 读取层（FEAT-SIM-DATAHOUSE 子件1 的查询面）。

只做两件事：把守护（scripts/sim_asset_harvest.py）登记的行按分页/过滤读出来；
把「到底存了多少、丢了多少」聚合成一行总账。写路径只有守护，API 只读。

设计取舍：
  * SQL 拼装与行映射放在这里（而不是 router 里），于是过滤/分页口径可以被
    tests/test_sim_assets_api.py 用假 fetch 逐条断言，不依赖数据库。
  * 过滤值一律走白名单列映射 + 参数化绑定，不做字符串拼接（repo/state/case
    都是外部可控输入）。排序同理走白名单，不认识就退回默认。
"""
from __future__ import annotations

from typing import Any, Iterable

# 允许的过滤列（外部参数名 → 真实列名）
FILTER_COLUMNS = {
    "repo": "repo",
    "case": "case_key",
    "case_key": "case_key",
    "state": "state",
    "mission_id": "mission_id",
    "event_id": "event_id",
}
# 允许的排序列
SORT_COLUMNS = {
    "captured_at": "captured_at",
    "bytes": "bytes_total",
    "files": "file_count",
    "id": "id",
}
STATES = ("captured", "partial", "missed")

# 列表端点返回的列（清单在详情里，列表只给摘要 —— 10 万级文件的清单不进列表）
LIST_COLUMNS = ("id, event_id, mission_id, repo, case_key, report_url, disk_path, "
                "bytes_total, file_count, state, capture_reason, source_worktree, "
                "captured_at, "
                "jsonb_array_length(field_manifest) AS field_count, "
                "jsonb_array_length(curve_manifest) AS curve_count, "
                "jsonb_array_length(geometry_manifest) AS geometry_count, "
                "jsonb_array_length(config_manifest) AS config_count")


class QueryError(ValueError):
    """调用方参数非法（→ HTTP 422）。"""


def build_filters(**kwargs: Any) -> tuple[str, dict]:
    """把外部参数翻成 WHERE 子句 + 绑定参数。空值/None 一律忽略。"""
    clauses: list[str] = []
    params: dict = {}
    for key, value in kwargs.items():
        if value is None or value == "":
            continue
        column = FILTER_COLUMNS.get(key)
        if column is None:
            raise QueryError(f"不支持的过滤字段: {key}")
        if key == "state":
            if value not in STATES:
                raise QueryError(f"state 必须是 {STATES} 之一，收到 {value!r}")
            clauses.append(f"state = :state")
            params["state"] = value
            continue
        if key == "mission_id":
            try:
                params["mission_id"] = int(value)
            except (TypeError, ValueError) as exc:
                raise QueryError(f"mission_id 必须是整数: {value!r}") from exc
            clauses.append("mission_id = :mission_id")
            continue
        if key in ("repo", "case", "case_key"):
            pname = "case_key" if key in ("case", "case_key") else "repo"
            clauses.append(f"{column} = :{pname}")
            params[pname] = str(value)
            continue
        clauses.append(f"{column} = :{key}")
        params[key] = str(value)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def build_list_sql(*, filters: dict, page: int, page_size: int,
                   sort: str = "captured_at", order: str = "desc") -> tuple[str, dict]:
    if page < 1:
        raise QueryError("page 从 1 开始")
    if not (1 <= page_size <= 200):
        raise QueryError("page_size 必须在 1..200 之间")
    sort_col = SORT_COLUMNS.get(sort)
    if sort_col is None:
        raise QueryError(f"不支持的排序字段: {sort}")
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    where, params = build_filters(**filters)
    offset = (page - 1) * page_size
    sql = (f"SELECT {LIST_COLUMNS} FROM simulation_assets{where} "
           f"ORDER BY {sort_col} {direction}, id {direction} "
           f"LIMIT {int(page_size)} OFFSET {int(offset)}")
    return sql, params


def build_count_sql(filters: dict) -> tuple[str, dict]:
    where, params = build_filters(**filters)
    return f"SELECT count(*) FROM simulation_assets{where}", params


def build_summary_sql() -> str:
    return ("SELECT coalesce(repo, '<unknown>') AS repo, state, count(*) AS events, "
            "coalesce(sum(bytes_total), 0) AS bytes, coalesce(sum(file_count), 0) AS files "
            "FROM simulation_assets GROUP BY 1, 2 ORDER BY 1, 2")


def row_to_item(row: Any) -> dict:
    keys = ["id", "event_id", "mission_id", "repo", "case_key", "report_url",
            "disk_path", "bytes_total", "file_count", "state", "capture_reason",
            "source_worktree", "captured_at", "field_count", "curve_count",
            "geometry_count", "config_count"]
    out = {}
    for key, value in zip(keys, row):
        if key == "captured_at" and value is not None:
            out[key] = value.isoformat() if hasattr(value, "isoformat") else str(value)
        else:
            out[key] = value
    return out


def paginate(items: Iterable[dict], *, total: int, page: int, page_size: int) -> dict:
    return {
        "items": list(items),
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "pages": (int(total) + page_size - 1) // page_size if page_size else 0,
    }