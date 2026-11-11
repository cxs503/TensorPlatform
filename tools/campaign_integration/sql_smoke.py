"""成熟化单的 SQL **冒烟验证**：在会话级 TEMP 表里真跑 INSERT / 回滚（零副作用）。

为什么值得做：`feature_ledger.sql` 与 rollback 脚本是交付物，但「文件存在」不等于
「语句能跑、幂等、回滚干净」。平台库的 `feature_ledger` 对本工作树只读，故本模块在一张
**TEMPORARY** 表（列定义与线上同构）上验证三件事：

1. **语法**：``INSERT ... SELECT ... WHERE NOT EXISTS`` 与 ``DELETE`` 都能执行；
2. **幂等**：同一份 INSERT 连跑两次，行数不变（第二遍被 ``NOT EXISTS`` 挡住）；
3. **回滚干净**：执行 rollback 后残差为 0。

连接信息只从环境/参数取（默认本地库名），TEMP 表在会话结束即消失——**不碰任何线上数据**。
验证不可用（无 psql / 无 TEMP 权限）时如实返回 ``available=False``，绝不假装通过。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 与 backend 侧 ``feature_ledger`` 同构的会话级临时表（列名/类型逐一对应）
TEMP_DDL = """CREATE TEMP TABLE feature_ledger (
    id serial PRIMARY KEY,
    slug text NOT NULL,
    title text NOT NULL,
    domain text NOT NULL,
    source text NOT NULL,
    acceptance text NOT NULL,
    ledger_group text,
    status text DEFAULT 'planned'::text NOT NULL,
    evidence text,
    mission_id bigint,
    created_at timestamp with time zone DEFAULT now(),
    done_at timestamp with time zone
);"""


def psql_available(dsn: str = "tensorplatform") -> Dict[str, Any]:
    """探测 psql 与 TEMP 权限（不可用即 ``available=False``，由调用方降级为静态校验）。"""
    if not shutil.which("psql"):
        return {"available": False, "reason": "psql_not_found"}
    try:
        proc = subprocess.run(
            ["psql", "-d", dsn, "-A", "-t", "-c",
             "select has_database_privilege(current_database(),'TEMP')"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - 环境相关
        return {"available": False, "reason": f"psql_error:{exc}"}
    if proc.returncode != 0:
        return {"available": False, "reason": "psql_connect_failed",
                "stderr": (proc.stderr or "").strip()[:200]}
    return {"available": proc.stdout.strip() == "t", "reason": "ok"}


def verify(insert_sql: str, rollback_sql: str, dsn: str = "tensorplatform",
           timeout: float = 60.0) -> Dict[str, Any]:
    """在 TEMP 表上实跑：第一遍 / 第二遍（幂等）/ 回滚后残差。"""
    probe = psql_available(dsn)
    if not probe.get("available"):
        return {"available": False, **probe}

    script = "\n".join([
        "\\set ON_ERROR_STOP on",
        TEMP_DDL,
        insert_sql,
        "SELECT 'AFTER_FIRST=' || count(*) FROM feature_ledger;",
        insert_sql,
        "SELECT 'AFTER_SECOND=' || count(*) FROM feature_ledger;",
        rollback_sql,
        "SELECT 'AFTER_ROLLBACK=' || count(*) FROM feature_ledger;",
        "",
    ])
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fh:
        fh.write(script)
        path = Path(fh.name)
    try:
        proc = subprocess.run(["psql", "-d", dsn, "-q", "-A", "-t", "-f", str(path)],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - 环境相关
        return {"available": False, "reason": f"psql_error:{exc}"}
    finally:
        path.unlink(missing_ok=True)

    counts: Dict[str, Optional[int]] = {"after_first": None, "after_second": None,
                                        "after_rollback": None}
    markers: List[str] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        for key, tag in (("after_first", "AFTER_FIRST="), ("after_second", "AFTER_SECOND="),
                         ("after_rollback", "AFTER_ROLLBACK=")):
            if line.startswith(tag):
                counts[key] = int(line[len(tag):])
                markers.append(key)
    ok = (proc.returncode == 0
          and all(v is not None for v in counts.values())
          and counts["after_first"] == counts["after_second"]
          and counts["after_rollback"] == 0)
    return {"available": True, "returncode": proc.returncode, **counts,
            "idempotent": counts["after_first"] == counts["after_second"],
            "rollback_clean": counts["after_rollback"] == 0,
            "ok": ok,
            "stderr": (proc.stderr or "").strip()[:300]}


__all__ = ["TEMP_DDL", "psql_available", "verify"]