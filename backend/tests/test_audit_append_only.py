"""PR-8（AOG P1）：审计日志 append-only 门禁。

三层证据，每层都能红：
1. **迁移声明**：migrations/099 必须声明 BEFORE UPDATE OR DELETE 触发器且
   RAISE，且幂等（存在性守卫）——删掉触发器/改成 AFTER/去掉 RAISE 都红；
2. **自愈镜像**：api/audit.py 的 log_action 在 PG 方言下同装触发器（sqlite
   测试路径如实跳过，方言门被拆也一样红）；
3. **源面零改写**：backend 源里不得出现 UPDATE audit_logs / DELETE FROM
   audit_logs 写入方（触发器守卫与迁移文件自身除外）。

PG 行为级验证（直连 UPDATE 真被 RAISE）需要 PostgreSQL，CI 是 sqlite
内存库跑不了，按 TP_AUDIT_PG_DSN 环境变量 skipif——容器/生产环境提供
DSN 即自动跑真库四步探针（INSERT 过 / UPDATE 拒 / DELETE 拒 / 复 INSERT 过）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
MIGRATION = BACKEND / "migrations" / "099_audit_logs_append_only.sql"
AUDIT_API = BACKEND / "api" / "audit.py"


def test_migration_declares_append_only_trigger():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "BEFORE UPDATE OR DELETE ON audit_logs" in sql, "触发器必须拦 UPDATE 与 DELETE 两类"
    assert "RAISE EXCEPTION" in sql and "append-only" in sql, "拦截必须显式 RAISE"
    assert "audit_logs_append_only_guard" in sql
    # 幂等：重复执行不得报错
    assert "CREATE OR REPLACE FUNCTION" in sql
    assert "pg_trigger" in sql, "触发器创建须有存在性守卫（可重复执行）"


def test_selfheal_ddl_installs_trigger_on_postgres():
    src = AUDIT_API.read_text(encoding="utf-8")
    assert "_PG_APPEND_ONLY_SQL" in src, "log_action 自愈路径须镜像迁移装触发器"
    assert "audit_logs_append_only" in src
    assert "BEFORE UPDATE OR DELETE ON audit_logs" in src
    assert "_is_postgres" in src, "方言门：sqlite 测试路径不执行 PG DDL"


def test_no_update_or_delete_writers_in_backend():
    """写入方必须 INSERT-only：任何 UPDATE/DELETE audit_logs 的代码都是违规。"""
    offenders = []
    for py in BACKEND.rglob("*.py"):
        rel = py.relative_to(REPO_ROOT)
        if "tests/" in str(rel) or py == MIGRATION:
            continue
        src = py.read_text(encoding="utf-8", errors="ignore")
        if py == AUDIT_API:
            # 触发器守卫 SQL 自身含 UPDATE OR DELETE 字样，剔除守卫串后扫描
            src = re.sub(r'_PG_APPEND_ONLY_SQL = """.*?"""', "", src, flags=re.S)
        for m in re.finditer(r"(UPDATE\s+audit_logs|DELETE\s+FROM\s+audit_logs)", src, re.I):
            offenders.append(f"{rel}: {m.group(0)}")
    assert offenders == [], f"发现审计日志改写方: {offenders}"


@pytest.mark.skipif(
    not os.environ.get("TP_AUDIT_PG_DSN"),
    reason="行为级验证需要 PostgreSQL（CI 为 sqlite 内存库）；容器/生产环境设 TP_AUDIT_PG_DSN 即跑",
)
def test_pg_direct_update_and_delete_are_raised():
    """真库四步探针：建表+装触发器 → INSERT 过 → UPDATE 拒 → DELETE 拒 → 复 INSERT 过。"""
    import psycopg2

    dsn = os.environ["TP_AUDIT_PG_DSN"]
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS audit_logs_ap8 CASCADE")
            sql = MIGRATION.read_text(encoding="utf-8").replace("audit_logs", "audit_logs_ap8")
            cur.execute(sql)
            cur.execute("INSERT INTO audit_logs_ap8 (action, entity_type, user_name) "
                        "VALUES ('login', 'security', 'probe')")
            cur.execute("SELECT id FROM audit_logs_ap8")
            row_id = cur.fetchone()[0]
            with pytest.raises(Exception, match="append-only"):
                cur.execute("UPDATE audit_logs_ap8 SET detail='tampered' WHERE id=%s", (row_id,))
            conn.rollback()
            with pytest.raises(Exception, match="append-only"):
                cur.execute("DELETE FROM audit_logs_ap8 WHERE id=%s", (row_id,))
            conn.rollback()
            cur.execute("INSERT INTO audit_logs_ap8 (action, entity_type, user_name) "
                        "VALUES ('logout', 'security', 'probe')")
            cur.execute("SELECT COUNT(*) FROM audit_logs_ap8")
            assert cur.fetchone()[0] == 2, "拒绝改写后 INSERT 通道必须仍然可用"
            cur.execute("DROP TABLE IF EXISTS audit_logs_ap8 CASCADE")
