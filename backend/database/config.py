"""
数据库配置
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://foundation:foundation123@localhost:5432/open_digital_foundation"
)

_IS_SQLITE = DATABASE_URL.startswith("sqlite")

_connect_args = {}
_pool_kwargs = {}
if _IS_SQLITE:
    # SQLite 校验与 PostgreSQL 不同：连接可能被 FastAPI 线程池中的多个线程使用
    _connect_args["check_same_thread"] = False
    if ":memory:" in DATABASE_URL or "mode=memory" in DATABASE_URL:
        # 内存库：SingletonThreadPool/QueuePool 不接受 max_overflow/pool_timeout，
        # 且 SingletonThreadPool 按线程持有连接会导致内存库数据跨线程不一致，
        # 使用 StaticPool 共享单一连接。
        _pool_kwargs["poolclass"] = StaticPool
    else:
        # 文件库使用默认 QueuePool，可安全使用队列式连接池参数
        _pool_kwargs.update(
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_timeout=10,
        )
else:
    _connect_args["options"] = "-c timezone=utc -c client_encoding=UTF8"
    _pool_kwargs.update(
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=10,
    )

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    echo_pool=False,
    **_pool_kwargs,
)

# --- LOCAL SQLITE COMPAT (loongarch local deployment, not upstream) -----------
# The mission/factory APIs call PostgreSQL-only helpers. Under SQLite we register
# equivalents so the same code path runs. Advisory locks are no-ops because
# SQLite serialises writers; hashtextextended is a stable 64-bit digest.
if engine.dialect.name == "sqlite":
    from sqlalchemy import event as _sa_event

    @_sa_event.listens_for(engine, "connect")
    def _sqlite_pg_compat(dbapi_connection, _connection_record):
        import hashlib

        def _advisory_lock(*_args, **_kwargs):
            return None

        def _hashtextextended(value, seed=0):
            raw = hashlib.blake2b(f"{seed}:{value}".encode("utf-8"), digest_size=8).digest()
            return int.from_bytes(raw, "big", signed=True)

        for _name, _fn, _nargs in (
            ("pg_advisory_xact_lock", _advisory_lock, -1),
            ("pg_advisory_lock", _advisory_lock, -1),
            ("pg_advisory_unlock", _advisory_lock, -1),
            ("hashtextextended", _hashtextextended, 2),
        ):
            try:
                dbapi_connection.create_function(_name, _nargs, _fn)
            except Exception:
                pass

    @_sa_event.listens_for(engine, "before_cursor_execute")
    def _sqlite_rewrite_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        """(kept for diagnostics; actual rewriting happens in the Session layer)"""
        return None

    # ---- Session-level structural rewrite -----------------------------------
    # 17 call sites use PostgreSQL row locking ("FOR UPDATE", "FOR UPDATE SKIP
    # LOCKED", "FOR UPDATE OF a"). SQLite has no such clause; overriding Session
    # .execute lets us strip it in ONE place instead of editing every query.
    import re as _re
    from sqlalchemy import text as _text
    from sqlalchemy.sql.elements import TextClause as _TextClause
    from sqlalchemy.orm import Session as _Session

    _LOCK_RE = _re.compile(
        r"\s+FOR\s+(?:NO\s+KEY\s+)?(?:UPDATE|SHARE)"
        r"(?:\s+OF\s+\w+(?:\s*,\s*\w+)*)?"
        r"(?:\s+(?:NOWAIT|SKIP\s+LOCKED|WAIT\s+\d+))?",
        _re.I)

    class _SqliteCompatSession(_Session):
        """SQLite portability layer: PG-only SQL is rewritten in ONE place."""

        def execute(self, statement, *args, **kwargs):
            if isinstance(statement, _TextClause):
                sql = str(statement)
                new = sql
                # 0) 去掉 PG 类型转换 (:param::bigint / '...'::jsonb / ::text[])
                #    注意: 类型名只允许单个 token, 否则会跨行吞掉紧随其后的 SQL 关键字
                new = _re.sub(r"::\s*[A-Za-z_][A-Za-z0-9_]*(\[\])?", "", new)
                # 0b) CAST(x AS jsonb) -> x   (SQLite 无 jsonb 类型)
                new = _re.sub(r"CAST\(\s*([^()]+?)\s+AS\s+jsonb\s*\)", r"\1", new, flags=_re.I)
                # 0c) jsonb_array_elements_text(x) -> json_each(x)  (JSON1 表函数)
                new = _re.sub(r"jsonb_array_elements_text\s*\(", "json_each(", new, flags=_re.I)
                # 0d) JSON 函数映射: jsonb_set/jsonb_build_object/jsonb_agg
                new = _re.sub(r"jsonb_set\(\s*([^,()]+?)\s*,\s*'\{([^}]+)\}'\s*,", r"json_set(\1, '$.\2',", new, flags=_re.I)
                new = _re.sub(r"\bjsonb_build_object\s*\(", "json_object(", new, flags=_re.I)
                new = _re.sub(r"\bjsonb_agg\b", "json_group_array", new, flags=_re.I)
                new = _re.sub(r"\bjson_build_object\s*\(", "json_object(", new, flags=_re.I)
                # 1) now()/CURRENT_TIMESTAMP - interval 'N seconds' -> datetime('now','-N seconds')
                new = _re.sub(
                    r"(?:now\(\)|CURRENT_TIMESTAMP)\s*-\s*interval\s*'(\d+)\s*seconds?'",
                    r"datetime('now','-\1 seconds')", new, flags=_re.I)
                # 2) now() + interval 'N seconds'
                new = _re.sub(
                    r"(?:now\(\)|CURRENT_TIMESTAMP)\s*\+\s*interval\s*'(\d+)\s*seconds?'",
                    r"datetime('now','+\1 seconds')", new, flags=_re.I)
                # 3) plain now() -> datetime('now')  (comparable with stored timestamps)
                new = _re.sub(r"\bnow\(\)", "datetime('now')", new, flags=_re.I)
                # 4) row locking
                new = _LOCK_RE.sub("", new)
                if new != sql:
                    statement = _text(new)
            return super().execute(statement, *args, **kwargs)

    _SessionFactory = lambda: sessionmaker(  # noqa: E731
        autocommit=False, autoflush=False, bind=engine, class_=_SqliteCompatSession)
    _USE_COMPAT_SESSION = True
else:
    _USE_COMPAT_SESSION = False
# -----------------------------------------------------------------------------

if _USE_COMPAT_SESSION:
    SessionLocal = _SessionFactory()
else:
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
