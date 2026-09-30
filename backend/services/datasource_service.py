"""Persistent, organization-scoped data-source connectors."""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import requests
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from database.config import SessionLocal
from sqlalchemy import text

try:
    import pymysql
    MYSQL_AVAILABLE = True
except ImportError:
    pymysql = None
    MYSQL_AVAILABLE = False

try:
    from influxdb import InfluxDBClient
    INFLUXDB_AVAILABLE = True
except ImportError:
    InfluxDBClient = None
    INFLUXDB_AVAILABLE = False

try:
    from opcua import Client
    OPCUA_AVAILABLE = True
except ImportError:
    Client = None
    OPCUA_AVAILABLE = False


logger = logging.getLogger(__name__)
_READ_ONLY_START = re.compile(r"^\s*(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN)\b", re.IGNORECASE)
_READ_ONLY_DENY = re.compile(
    r"\b(INTO\s+(OUTFILE|DUMPFILE)|FOR\s+UPDATE|LOCK\s+IN\s+SHARE\s+MODE|"
    r"LOAD_FILE\s*\(|SLEEP\s*\(|BENCHMARK\s*\()",
    re.IGNORECASE,
)


def validate_read_only_query(sql: str) -> str:
    """Accept a single read-only statement and reject server-side side effects."""
    query = (sql or "").strip()
    if not query:
        raise ValueError("查询语句不能为空")
    query = query[:-1].rstrip() if query.endswith(";") else query
    if ";" in query:
        raise ValueError("一次只能执行一条查询语句")
    if not _READ_ONLY_START.match(query):
        raise ValueError("数据接入仅允许 SELECT、SHOW、DESCRIBE 或 EXPLAIN 只读查询")
    if _READ_ONLY_DENY.search(query):
        raise ValueError("查询包含不允许的文件、锁定或高风险函数")
    return query


def _credential_cipher() -> Fernet:
    secret = (
        os.getenv("DATASOURCE_ENCRYPTION_KEY")
        or os.getenv("JWT_SECRET_KEY")
        or "open-digital-foundation-datasource-fallback-key"
    )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_credential(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _credential_cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_credential(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return _credential_cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("数据源凭据无法解密，请重新录入密码") from exc


class DataSourceRepository:
    """Small SQL repository; schema is created lazily for upgrade compatibility."""

    _lock = threading.Lock()

    def ensure_schema(self) -> None:
        with self._lock:
            db = SessionLocal()
            try:
                db.execute(text("""
                    CREATE TABLE IF NOT EXISTS data_source_configs (
                        id VARCHAR(128) PRIMARY KEY,
                        organization_id INTEGER,
                        name VARCHAR(200) NOT NULL,
                        source_type VARCHAR(32) NOT NULL,
                        host VARCHAR(255),
                        port INTEGER,
                        database_name VARCHAR(255),
                        username VARCHAR(255),
                        password_encrypted TEXT,
                        endpoint VARCHAR(1000),
                        status VARCHAR(32) NOT NULL DEFAULT 'disconnected',
                        last_error TEXT,
                        last_test_at TIMESTAMP,
                        created_by INTEGER,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                """))
                db.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_data_source_org "
                    "ON data_source_configs (organization_id, created_at DESC)"
                ))
                db.commit()
            finally:
                db.close()

    def save(self, config: Dict[str, Any], password: Optional[str]) -> None:
        self.ensure_schema()
        db = SessionLocal()
        try:
            db.execute(text("""
                INSERT INTO data_source_configs (
                    id, organization_id, name, source_type, host, port,
                    database_name, username, password_encrypted, endpoint,
                    status, created_by, created_at, updated_at
                ) VALUES (
                    :id, :organization_id, :name, :type, :host, :port,
                    :database, :username, :password, :endpoint,
                    'disconnected', :created_by, NOW(), NOW()
                )
                ON CONFLICT (id) DO UPDATE SET
                    name=EXCLUDED.name, host=EXCLUDED.host, port=EXCLUDED.port,
                    database_name=EXCLUDED.database_name, username=EXCLUDED.username,
                    password_encrypted=EXCLUDED.password_encrypted,
                    endpoint=EXCLUDED.endpoint, status='disconnected',
                    last_error=NULL, updated_at=NOW()
            """), {
                **config,
                "password": encrypt_credential(password),
            })
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _row_to_dict(row, include_secret: bool = False) -> Dict[str, Any]:
        item = {
            "id": row[0], "organization_id": row[1], "name": row[2],
            "type": row[3], "host": row[4], "port": row[5],
            "database": row[6], "username": row[7], "endpoint": row[9],
            "status": row[10] or "disconnected",
            "connected": (row[10] == "connected"),
            "last_error": row[11],
            "last_test_at": row[12].isoformat() if row[12] else None,
            "created_at": row[13].isoformat() if row[13] else None,
        }
        if include_secret:
            item["password"] = decrypt_credential(row[8])
        return item

    def list(self, organization_id: int, source_type: Optional[str] = None) -> List[Dict[str, Any]]:
        self.ensure_schema()
        db = SessionLocal()
        try:
            query = """
                SELECT id, organization_id, name, source_type, host, port,
                       database_name, username, password_encrypted, endpoint,
                       status, last_error, last_test_at, created_at
                FROM data_source_configs WHERE organization_id=:organization_id
            """
            params: Dict[str, Any] = {"organization_id": organization_id}
            if source_type:
                query += " AND source_type=:source_type"
                params["source_type"] = source_type
            query += " ORDER BY created_at DESC"
            return [self._row_to_dict(row) for row in db.execute(text(query), params).fetchall()]
        finally:
            db.close()

    def get(self, source_id: str, organization_id: int, include_secret: bool = False) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        db = SessionLocal()
        try:
            row = db.execute(text("""
                SELECT id, organization_id, name, source_type, host, port,
                       database_name, username, password_encrypted, endpoint,
                       status, last_error, last_test_at, created_at
                FROM data_source_configs
                WHERE id=:id AND organization_id=:organization_id
            """), {"id": source_id, "organization_id": organization_id}).fetchone()
            return self._row_to_dict(row, include_secret) if row else None
        finally:
            db.close()

    def update_status(self, source_id: str, organization_id: int, status: str, error: Optional[str] = None, tested: bool = False) -> None:
        self.ensure_schema()
        db = SessionLocal()
        try:
            tested_sql = ", last_test_at=NOW()" if tested else ""
            db.execute(text(
                "UPDATE data_source_configs SET status=:status, last_error=:error, "
                f"updated_at=NOW(){tested_sql} WHERE id=:id AND organization_id=:organization_id"
            ), {"status": status, "error": error, "id": source_id, "organization_id": organization_id})
            db.commit()
        finally:
            db.close()

    def delete(self, source_id: str, organization_id: int) -> bool:
        self.ensure_schema()
        db = SessionLocal()
        try:
            result = db.execute(text(
                "DELETE FROM data_source_configs WHERE id=:id AND organization_id=:organization_id"
            ), {"id": source_id, "organization_id": organization_id})
            db.commit()
            return bool(result.rowcount)
        finally:
            db.close()


class DataSource(ABC):
    last_error: Optional[str] = None

    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def disconnect(self): ...

    @abstractmethod
    def test_connection(self) -> bool: ...

    @abstractmethod
    def query(self, query: str) -> List[Dict]: ...


class MySQLDataSource(DataSource):
    def __init__(self, host: str, port: int, database: str, username: str, password: str):
        self.host, self.port, self.database = host, int(port), database
        self.username, self.password = username, password
        self._conn = None
        self.last_error = None

    def _open(self):
        if not MYSQL_AVAILABLE:
            raise RuntimeError("MySQL 驱动未安装")
        return pymysql.connect(
            host=self.host, port=self.port, database=self.database,
            user=self.username, password=self.password,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5, read_timeout=15, write_timeout=15,
            charset="utf8mb4", autocommit=True,
        )

    def connect(self) -> bool:
        try:
            self._conn = self._open()
            with self._conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self._conn = None
            return False

    def disconnect(self):
        if self._conn:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def test_connection(self) -> bool:
        conn = None
        try:
            conn = self._open()
            with conn.cursor() as cursor:
                cursor.execute("SELECT DATABASE() AS database_name, VERSION() AS server_version")
                cursor.fetchone()
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False
        finally:
            if conn:
                conn.close()

    def _ready(self):
        if self._conn:
            try:
                self._conn.ping(reconnect=True)
                return
            except Exception:
                self._conn = None
        if not self.connect():
            raise RuntimeError(self.last_error or "MySQL 连接失败")

    def query(self, sql: str) -> List[Dict]:
        query = validate_read_only_query(sql)
        self._ready()
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(query)
                return list(cursor.fetchmany(10000))
        except Exception as exc:
            self.last_error = str(exc)
            raise RuntimeError(f"MySQL 查询失败: {self.last_error}") from exc

    def get_tables(self) -> List[Dict]:
        return self.query(
            "SELECT TABLE_NAME AS name, TABLE_TYPE AS type "
            "FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
            "ORDER BY TABLE_NAME"
        )


class InfluxDBDataSource(DataSource):
    def __init__(self, host: str, port: int, database: str, username: str = None, password: str = None):
        self.host, self.port, self.database = host, int(port), database
        self.username, self.password = username, password
        self._client = None
        self.last_error = None

    def _new_client(self):
        if not INFLUXDB_AVAILABLE:
            raise RuntimeError("InfluxDB 驱动未安装")
        return InfluxDBClient(host=self.host, port=self.port, username=self.username,
                              password=self.password, database=self.database, timeout=5)

    def connect(self) -> bool:
        try:
            self._client = self._new_client()
            self._client.ping()
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc); self._client = None; return False

    def disconnect(self):
        if self._client:
            try: self._client.close()
            except Exception: pass
            self._client = None

    def test_connection(self) -> bool:
        client = None
        try:
            client = self._new_client(); client.ping(); self.last_error = None; return True
        except Exception as exc:
            self.last_error = str(exc); return False
        finally:
            if client:
                try: client.close()
                except Exception: pass

    def query(self, query: str) -> List[Dict]:
        if not self._client and not self.connect():
            raise RuntimeError(self.last_error or "InfluxDB 连接失败")
        try: return list(self._client.query(query).get_points())
        except Exception as exc: raise RuntimeError(f"InfluxDB 查询失败: {exc}") from exc

    def get_measurements(self) -> List[Dict]:
        return [{"name": row.get("name"), "type": "measurement"} for row in self.query("SHOW MEASUREMENTS")]


class OPCUADataSource(DataSource):
    def __init__(self, endpoint: str, username: str = None, password: str = None):
        self.endpoint, self.username, self.password = endpoint, username, password
        self._client = None
        self.last_error = None

    def _new_client(self):
        if not OPCUA_AVAILABLE:
            raise RuntimeError("OPC UA 驱动未安装")
        client = Client(self.endpoint, timeout=5)
        if self.username:
            client.set_user(self.username)
        if self.password:
            client.set_password(self.password)
        return client

    def connect(self) -> bool:
        try:
            self._client = self._new_client(); self._client.connect(); self.last_error = None; return True
        except Exception as exc:
            self.last_error = str(exc); self._client = None; return False

    def disconnect(self):
        if self._client:
            try: self._client.disconnect()
            except Exception: pass
            self._client = None

    def test_connection(self) -> bool:
        client = None
        try:
            client = self._new_client(); client.connect(); self.last_error = None; return True
        except Exception as exc:
            self.last_error = str(exc); return False
        finally:
            if client:
                try: client.disconnect()
                except Exception: pass

    def query(self, node_id: str) -> List[Dict]:
        if not self._client and not self.connect():
            raise RuntimeError(self.last_error or "OPC UA 连接失败")
        node = self._client.get_node(node_id)
        return [{"node_id": node_id, "value": node.get_value(), "timestamp": datetime.utcnow().isoformat()}]

    def get_children(self, node_id: str = None) -> List[Dict]:
        if not self._client and not self.connect():
            raise RuntimeError(self.last_error or "OPC UA 连接失败")
        node = self._client.get_node(node_id) if node_id else self._client.get_root_node()
        return [{"node_id": child.nodeid.to_string(), "name": child.get_browse_name().Name, "type": "node"}
                for child in node.get_children()]


class TDengineDataSource(DataSource):
    """TDengine 3.x connector through the official taosAdapter REST endpoint."""

    def __init__(self, host: str, port: int, database: str, username: str = "root", password: str = "taosdata"):
        host = (host or "").strip().rstrip("/")
        self.base_url = host if host.startswith(("http://", "https://")) else f"http://{host}:{int(port)}"
        self.database = database
        self.username = username or "root"
        self.password = password or ""
        self.last_error = None
        self._connected = False

    def _execute(self, sql: str, use_database: bool = True) -> List[Dict]:
        suffix = f"/{quote(self.database, safe='')}" if use_database and self.database else ""
        try:
            response = requests.post(
                f"{self.base_url}/rest/sql{suffix}",
                data=sql.encode("utf-8"),
                auth=(self.username, self.password),
                headers={"Content-Type": "text/plain; charset=utf-8"},
                timeout=(5, 30),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            self.last_error = str(exc)
            raise RuntimeError(f"TDengine REST连接失败: {exc}") from exc
        except ValueError as exc:
            self.last_error = "REST响应不是有效JSON"
            raise RuntimeError("TDengine REST响应格式异常") from exc

        if int(payload.get("code", -1)) != 0:
            self.last_error = str(payload.get("desc") or payload.get("message") or "TDengine查询失败")
            raise RuntimeError(self.last_error)
        column_meta = payload.get("column_meta") or []
        columns = [str(item[0] if isinstance(item, (list, tuple)) else item.get("name")) for item in column_meta]
        data = payload.get("data") or []
        self.last_error = None
        return [dict(zip(columns, row)) for row in data] if columns else []

    def connect(self) -> bool:
        try:
            self._execute("SHOW TABLES")
            self._connected = True
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self._connected = False
            return False

    def disconnect(self):
        self._connected = False

    def test_connection(self) -> bool:
        try:
            self._execute("SHOW TABLES")
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False

    def query(self, sql: str) -> List[Dict]:
        query = validate_read_only_query(sql)
        return self._execute(query)

    def get_tables(self) -> List[Dict]:
        items: List[Dict] = []
        seen = set()
        for statement, item_type in (("SHOW STABLES", "STABLE"), ("SHOW TABLES", "TABLE")):
            for row in self._execute(statement):
                name = next((value for value in row.values() if isinstance(value, str) and value), None)
                if name and name not in seen:
                    seen.add(name)
                    items.append({"name": name, "type": item_type})
        return items


class DataSourceManager:
    def __init__(self):
        self.sources: Dict[str, DataSource] = {}
        self.repository = DataSourceRepository()

    @staticmethod
    def driver_availability() -> Dict[str, bool]:
        return {"mysql": MYSQL_AVAILABLE, "influxdb": INFLUXDB_AVAILABLE,
                "opcua": OPCUA_AVAILABLE, "tdengine": True}

    def _build(self, config: Dict[str, Any]) -> DataSource:
        if config["type"] == "mysql":
            return MySQLDataSource(config["host"], config["port"], config["database"], config["username"], config.get("password") or "")
        if config["type"] == "influxdb":
            return InfluxDBDataSource(config["host"], config["port"], config["database"], config.get("username"), config.get("password"))
        if config["type"] == "opcua":
            return OPCUADataSource(config["endpoint"], config.get("username"), config.get("password"))
        if config["type"] == "tdengine":
            return TDengineDataSource(config["host"], config["port"], config["database"],
                                      config.get("username") or "root", config.get("password") or "")
        raise ValueError(f"不支持的数据源类型: {config['type']}")

    def _get_source(self, source_id: str, organization_id: int) -> DataSource:
        config = self.repository.get(source_id, organization_id, include_secret=True)
        if not config:
            raise KeyError("数据源不存在")
        if source_id not in self.sources:
            self.sources[source_id] = self._build(config)
        return self.sources[source_id]

    def _add(self, config: Dict[str, Any], password: Optional[str]) -> str:
        self.repository.save(config, password)
        full = {**config, "password": password}
        self.sources[config["id"]] = self._build(full)
        return config["id"]

    def add_mysql_source(self, name: str, host: str, port: int, database: str, username: str, password: str, organization_id: int, created_by: int) -> str:
        source_id = f"mysql_{uuid.uuid4().hex[:16]}"
        return self._add({"id": source_id, "organization_id": organization_id, "name": name,
                          "type": "mysql", "host": host, "port": port, "database": database,
                          "username": username, "endpoint": None, "created_by": created_by}, password)

    def add_influxdb_source(self, name: str, host: str, port: int, database: str, username: str, password: str, organization_id: int, created_by: int) -> str:
        source_id = f"influxdb_{uuid.uuid4().hex[:16]}"
        return self._add({"id": source_id, "organization_id": organization_id, "name": name,
                          "type": "influxdb", "host": host, "port": port, "database": database,
                          "username": username, "endpoint": None, "created_by": created_by}, password)

    def add_opcua_source(self, name: str, endpoint: str, username: str, password: str, organization_id: int, created_by: int) -> str:
        source_id = f"opcua_{uuid.uuid4().hex[:16]}"
        return self._add({"id": source_id, "organization_id": organization_id, "name": name,
                          "type": "opcua", "host": None, "port": None, "database": None,
                          "username": username, "endpoint": endpoint, "created_by": created_by}, password)

    def add_tdengine_source(self, name: str, host: str, port: int, database: str, username: str, password: str, organization_id: int, created_by: int) -> str:
        source_id = f"tdengine_{uuid.uuid4().hex[:16]}"
        return self._add({"id": source_id, "organization_id": organization_id, "name": name,
                          "type": "tdengine", "host": host, "port": port, "database": database,
                          "username": username or "root", "endpoint": None, "created_by": created_by}, password)

    def list_sources(self, organization_id: int, source_type: str = None) -> List[Dict]:
        return self.repository.list(organization_id, source_type)

    def get_config(self, source_id: str, organization_id: int) -> Optional[Dict]:
        return self.repository.get(source_id, organization_id)

    @staticmethod
    def _safe_diagnostic(error: Optional[str]) -> Dict[str, str]:
        message = (error or "").lower()
        if "1045" in message or "access denied" in message:
            return {"code": "authentication_failed", "message": "用户名、密码或远程登录权限不正确"}
        if "1049" in message or "unknown database" in message:
            return {"code": "database_not_found", "message": "目标数据库不存在或当前账号无权访问"}
        if "database not exist" in message or "db not exist" in message:
            return {"code": "database_not_found", "message": "目标TDengine数据库不存在或当前账号无权访问"}
        if "timed out" in message or "timeout" in message:
            return {"code": "connection_timeout", "message": "连接超时，请检查网络、防火墙和端口"}
        if "refused" in message:
            return {"code": "connection_refused", "message": "目标端口拒绝连接，请检查数据库服务状态"}
        return {"code": "unreachable_or_auth_failed", "message": "无法连接，请检查地址、数据库、账号和权限"}

    def diagnose_connection(self, source_id: str, organization_id: int) -> Dict[str, Any]:
        config = self.get_config(source_id, organization_id)
        if not config:
            return {"ok": False, "code": "not_found", "message": "数据源不存在"}
        if not self.driver_availability().get(config["type"], False):
            return {"ok": False, "code": "driver_unavailable", "message": f"{config['type']} 驱动未安装，无法连接"}
        try:
            source = self._get_source(source_id, organization_id)
            ok = source.test_connection()
        except Exception as exc:
            ok = False
            source = None
            error = str(exc)
        else:
            error = source.last_error
        if ok:
            self.repository.update_status(source_id, organization_id, config["status"], None, tested=True)
            return {"ok": True, "code": "reachable", "message": "数据库、目标库和账号权限验证成功"}
        diagnostic = self._safe_diagnostic(error)
        self.repository.update_status(source_id, organization_id, "error", diagnostic["message"], tested=True)
        return {"ok": False, **diagnostic}

    def connect(self, source_id: str, organization_id: int) -> bool:
        source = self._get_source(source_id, organization_id)
        success = source.connect()
        diagnostic = None if success else self._safe_diagnostic(source.last_error)["message"]
        self.repository.update_status(source_id, organization_id, "connected" if success else "error", diagnostic, tested=True)
        return success

    def disconnect(self, source_id: str, organization_id: int):
        source = self.sources.get(source_id)
        if source: source.disconnect()
        if not self.get_config(source_id, organization_id): raise KeyError("数据源不存在")
        self.repository.update_status(source_id, organization_id, "disconnected", None)

    def query(self, source_id: str, query: str, organization_id: int) -> List[Dict]:
        return self._get_source(source_id, organization_id).query(query)

    def get_tables(self, source_id: str, organization_id: int) -> List[Dict]:
        source = self._get_source(source_id, organization_id)
        if isinstance(source, MySQLDataSource): return source.get_tables()
        if isinstance(source, InfluxDBDataSource): return source.get_measurements()
        if isinstance(source, OPCUADataSource): return source.get_children()
        if isinstance(source, TDengineDataSource): return source.get_tables()
        return []

    def delete_source(self, source_id: str, organization_id: int) -> bool:
        source = self.sources.pop(source_id, None)
        if source: source.disconnect()
        return self.repository.delete(source_id, organization_id)


_datasource_manager: Optional[DataSourceManager] = None


def get_datasource_manager() -> DataSourceManager:
    global _datasource_manager
    if _datasource_manager is None:
        _datasource_manager = DataSourceManager()
    return _datasource_manager
