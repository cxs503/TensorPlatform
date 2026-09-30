"""
TDengine时序数据库服务
用于工业物联网时序数据存储、查询和分析
"""
import json
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import taospy as tdengine
    from taospy import connector

    TDENGINE_AVAILABLE = True
except ImportError:
    try:
        import tdengine

        TDENGINE_AVAILABLE = True
    except ImportError:
        TDENGINE_AVAILABLE = False
        print("TDengine not available, using mock")


class TDengineService:
    """TDengine时序数据库服务"""

    def __init__(self):
        self.host = os.getenv("TDENGINE_HOST", "localhost")
        self.port = int(os.getenv("TDENGINE_PORT", "6030"))
        self.username = os.getenv("TDENGINE_USER", "root")
        self.password = os.getenv("TDENGINE_PASSWORD", "taosdata")
        self.database = os.getenv("TDENGINE_DATABASE", "foundation_iot")

        self._conn = None
        if TDENGINE_AVAILABLE:
            self._connect()

    def _connect(self):
        """连接TDengine"""
        if not TDENGINE_AVAILABLE:
            return

        try:
            self._conn = connector.connect(
                host=self.host,
                port=self.port,
                user=self.username,
                password=self.password,
            )
            print(f"Connected to TDengine {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to connect to TDengine: {e}")
            self._conn = None

    @contextmanager
    def get_connection(self):
        """获取连接上下文"""
        conn = self._conn
        if conn is None:
            raise Exception("Not connected to TDengine")
        try:
            yield conn
        except Exception as e:
            raise e

    def create_database(self, database: Optional[str] = None, keep_days: int = 365):
        """
        创建数据库

        Args:
            database: 数据库名
            keep_days: 数据保留天数
        """
        db = database or self.database

        if not TDENGINE_AVAILABLE:
            print(f"Mock: Created database {db}")
            return True

        try:
            with self.get_connection() as conn:
                conn.execute(f"CREATE DATABASE IF NOT EXISTS {db} KEEP {keep_days}")
                conn.execute(f"USE {db}")
            return True
        except Exception as e:
            print(f"Failed to create database: {e}")
            return False

    def create_super_table(
        self,
        table_name: str,
        tags_schema: Dict[str, str],
        database: Optional[str] = None,
    ):
        """
        创建超级表

        Args:
            table_name: 表名
            tags_schema: 标签定义 {"tag_name": "tag_type"}
            database: 数据库名
        """
        db = database or self.database

        if not TDENGINE_AVAILABLE:
            print(f"Mock: Created super table {table_name}")
            return True

        try:
            with self.get_connection() as conn:
                conn.execute(f"USE {db}")
                # 构建标签定义
                tags_def = ", ".join([f"{k} {v}" for k, v in tags_schema.items()])
                sql = f"CREATE STABLE IF NOT EXISTS {table_name} (ts TIMESTAMP, value DOUBLE) TAGS ({tags_def})"
                conn.execute(sql)
            return True
        except Exception as e:
            print(f"Failed to create super table: {e}")
            return False

    def create_sub_table(
        self,
        table_name: str,
        super_table: str,
        tags: Dict[str, Any],
        database: Optional[str] = None,
    ):
        """
        创建子表

        Args:
            table_name: 子表名
            super_table: 超级表名
            tags: 标签值
            database: 数据库名
        """
        db = database or self.database

        if not TDENGINE_AVAILABLE:
            print(f"Mock: Created sub table {table_name}")
            return True

        try:
            with self.get_connection() as conn:
                conn.execute(f"USE {db}")
                # 构建标签值
                tags_str = ", ".join(
                    [f"'{v}'" if isinstance(v, str) else str(v) for v in tags.values()]
                )
                tags_keys = ", ".join(tags.keys())
                sql = f"CREATE TABLE IF NOT EXISTS {table_name} USING {super_table} ({tags_keys}) TAGS ({tags_str})"
                conn.execute(sql)
            return True
        except Exception as e:
            print(f"Failed to create sub table: {e}")
            return False

    def insert_data(
        self,
        table_name: str,
        timestamps: List[datetime],
        values: List[float],
        database: Optional[str] = None,
    ):
        """
        插入时序数据

        Args:
            table_name: 表名
            timestamps: 时间戳列表
            values: 值列表
            database: 数据库名
        """
        db = database or self.database

        if not TDENGINE_AVAILABLE:
            print(f"Mock: Inserted {len(values)} records to {table_name}")
            return True

        try:
            with self.get_connection() as conn:
                conn.execute(f"USE {db}")
                # 构建批量插入语句
                for ts, val in zip(timestamps, values):
                    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    sql = f"INSERT INTO {table_name} VALUES ('{ts_str}', {val})"
                    conn.execute(sql)
            return True
        except Exception as e:
            print(f"Failed to insert data: {e}")
            return False

    def query(
        self,
        sql: str,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        执行查询

        Args:
            sql: SQL语句
            database: 数据库名
        """
        db = database or self.database

        if not TDENGINE_AVAILABLE:
            # 返回模拟数据
            return [
                {"ts": "2024-01-01 00:00:00", "value": 10.5},
                {"ts": "2024-01-01 00:01:00", "value": 11.2},
            ]

        try:
            with self.get_connection() as conn:
                conn.execute(f"USE {db}")
                result = conn.query(sql)

                # 转换为字典列表
                columns = result.fields if hasattr(result, "fields") else []
                rows = result.fetch_all() if hasattr(result, "fetch_all") else []

                if not columns:
                    return []

                col_names = [c.name() for c in columns]
                return [dict(zip(col_names, row)) for row in rows]
        except Exception as e:
            print(f"Query failed: {e}")
            return []

    def get_latest(
        self,
        table_name: str,
        database: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """获取最新数据"""
        sql = f"SELECT * FROM {table_name} ORDER BY ts DESC LIMIT 1"
        results = self.query(sql, database)
        return results[0] if results else None

    def get_aggregate(
        self,
        table_name: str,
        start_time: str,
        end_time: str,
        interval: str = "1m",
        agg_func: str = "avg",
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取聚合数据

        Args:
            table_name: 表名
            start_time: 开始时间
            end_time: 结束时间
            interval: 聚合间隔 (1m, 5m, 1h, 1d)
            agg_func: 聚合函数 (avg, sum, max, min, count)
            database: 数据库名
        """
        sql = f"""
        SELECT _wstart as ts, {agg_func}(value) as value
        FROM {table_name}
        WHERE ts >= '{start_time}' AND ts < '{end_time}'
        INTERVAL ({interval})
        """
        return self.query(sql, database)

    def list_tables(
        self,
        super_table: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[str]:
        """列出所有表"""
        db = database or self.database

        if super_table:
            sql = f"SHOW {super_table} TABLES"
        else:
            sql = "SHOW TABLES"

        results = self.query(sql, db)
        return [r.get("table_name") for r in results if r.get("table_name")]

    def delete_table(
        self,
        table_name: str,
        database: Optional[str] = None,
    ) -> bool:
        """删除表"""
        db = database or self.database

        if not TDENGINE_AVAILABLE:
            print(f"Mock: Deleted table {table_name}")
            return True

        try:
            with self.get_connection() as conn:
                conn.execute(f"USE {db}")
                conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            return True
        except Exception as e:
            print(f"Failed to delete table: {e}")
            return False

    def close(self):
        """关闭连接"""
        if self._conn:
            self._conn.close()
            self._conn = None


# 全局实例
_tdengine_service: Optional[TDengineService] = None


def get_tdengine_service() -> TDengineService:
    """获取TDengine服务实例"""
    global _tdengine_service
    if _tdengine_service is None:
        _tdengine_service = TDengineService()
    return _tdengine_service
