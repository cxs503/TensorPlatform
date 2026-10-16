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

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
