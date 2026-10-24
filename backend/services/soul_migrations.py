"""Tensor Soul 迁移：版本 + 校验和登记，重复应用幂等。

与仓库既有迁移（091/092/093 的 CREATE TABLE IF NOT EXISTS 风格）一致，
但多了两道机械保证（契约 v0.1 第 2 条"版本、校验和、幂等迁移"）：

1. 每个迁移文件登记 sha256；重复应用同一版本视为幂等空操作；
2. 同一版本文件名下内容变了（校验和不符）→ 抛错而不是静默覆盖，
   避免"跑过一遍的库和仓库里的迁移文件不是同一份"。

本模块只在本仓库的隔离开发/测试库上使用；不触碰生产库。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import text

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

# 版本号 = 文件名前缀；顺序即应用顺序。
SOUL_MIGRATIONS: tuple[str, ...] = ("094_factory_soul.sql",)


class MigrationChecksumMismatch(RuntimeError):
    """已登记的版本与仓库中的文件内容不一致。"""


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def apply_soul_migrations(engine) -> dict:
    """按顺序应用 Soul 迁移，返回 {version: 'applied'|'unchanged'}。

    幂等性由两处共同保证：DDL 全部 IF NOT EXISTS/IF EXISTS，
    以及 factory_soul_schema_versions 的版本登记。
    """
    applied: dict[str, str] = {}
    for filename in SOUL_MIGRATIONS:
        version = filename.split("_", 1)[0]
        path = MIGRATIONS_DIR / filename
        raw = path.read_bytes()
        checksum = _digest(raw)
        with engine.begin() as db:
            # 登记表本身由迁移创建：先确保它存在（该 DDL 在迁移文件里也有，幂等）。
            db.execute(text("""CREATE TABLE IF NOT EXISTS factory_soul_schema_versions (
                version TEXT PRIMARY KEY,
                checksum TEXT NOT NULL CHECK (length(checksum) = 64),
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"""))
            row = db.execute(
                text("SELECT checksum FROM factory_soul_schema_versions WHERE version=:v"),
                {"v": version}).scalar()
            if row is not None:
                if row != checksum:
                    raise MigrationChecksumMismatch(
                        f"{filename}: registered checksum {row} != file checksum {checksum}")
                applied[version] = "unchanged"
                continue
            db.exec_driver_sql(raw.decode("utf-8"),
                               execution_options={"no_parameters": True})
            db.execute(
                text("INSERT INTO factory_soul_schema_versions (version, checksum) "
                     "VALUES (:v, :c) ON CONFLICT (version) DO NOTHING"),
                {"v": version, "c": checksum})
            applied[version] = "applied"
    return applied