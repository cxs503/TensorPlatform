"""
集中式日志服务
支持日志收集、存储、搜索、分析
"""
import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from elasticsearch import Elasticsearch

    ELK_AVAILABLE = True
except ImportError:
    ELK_AVAILABLE = False
    print("Elasticsearch not available, using local logging")


class LogService:
    """日志服务"""

    def __init__(self):
        # 日志存储配置
        self.log_dir = Path("/var/log/open-foundation")
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 日志文件
        self.access_log = self.log_dir / "access.log"
        self.error_log = self.log_dir / "error.log"
        self.audit_log = self.log_dir / "audit.log"

        # Elasticsearch配置
        self.es_host = os.getenv("ELASTICSEARCH_HOST", "localhost")
        self.es_port = int(os.getenv("ELASTICSEARCH_PORT", "9200"))
        self.es_index = os.getenv("ELASTICSEARCH_INDEX", "foundation-logs")

        self._es = None
        if ELK_AVAILABLE:
            self._connect_elasticsearch()

        # 内存日志缓存
        self.log_cache: List[Dict] = []
        self.max_cache_size = 1000

    def _connect_elasticsearch(self):
        """连接Elasticsearch"""
        try:
            self._es = Elasticsearch([f"http://{self.es_host}:{self.es_port}"])
            # 创建索引
            if not self._es.indices.exists(index=self.es_index):
                self._es.indices.create(
                    index=self.es_index,
                    body={
                        "mappings": {
                            "properties": {
                                "timestamp": {"type": "date"},
                                "level": {"type": "keyword"},
                                "service": {"type": "keyword"},
                                "message": {"type": "text"},
                                "user_id": {"type": "keyword"},
                                "endpoint": {"type": "keyword"},
                                "method": {"type": "keyword"},
                                "status_code": {"type": "integer"},
                                "response_time": {"type": "float"},
                                "extra": {"type": "object"},
                            }
                        }
                    },
                )
            print(f"Connected to Elasticsearch {self.es_host}:{self.es_port}")
        except Exception as e:
            print(f"Failed to connect to Elasticsearch: {e}")
            self._es = None

    def _add_to_cache(self, log: Dict):
        """添加到缓存"""
        self.log_cache.append(log)
        if len(self.log_cache) > self.max_cache_size:
            self.log_cache = self.log_cache[-self.max_cache_size :]

    def log_access(
        self,
        endpoint: str,
        method: str,
        status_code: int,
        response_time: float,
        user_id: str = None,
        ip: str = None,
    ):
        """记录访问日志"""
        log = {
            "timestamp": datetime.now().isoformat(),
            "level": "info",
            "service": "api",
            "message": f"{method} {endpoint} {status_code}",
            "endpoint": endpoint,
            "method": method,
            "status_code": status_code,
            "response_time": response_time,
            "user_id": user_id,
            "ip": ip,
        }

        self._write_to_file(self.access_log, log)
        self._add_to_cache(log)

        if self._es:
            self._es.index(index=self.es_index, body=log)

    def log_error(
        self,
        message: str,
        error: str = None,
        stack_trace: str = None,
        user_id: str = None,
        endpoint: str = None,
    ):
        """记录错误日志"""
        log = {
            "timestamp": datetime.now().isoformat(),
            "level": "error",
            "service": "api",
            "message": message,
            "error": error,
            "stack_trace": stack_trace,
            "user_id": user_id,
            "endpoint": endpoint,
        }

        self._write_to_file(self.error_log, log)
        self._add_to_cache(log)

        if self._es:
            self._es.index(index=self.es_index, body=log)

    def log_audit(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        user_id: str,
        details: Dict = None,
    ):
        """记录审计日志"""
        log = {
            "timestamp": datetime.now().isoformat(),
            "level": "audit",
            "service": "api",
            "message": f"User {user_id} performed {action} on {resource_type}/{resource_id}",
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "user_id": user_id,
            "details": details or {},
        }

        self._write_to_file(self.audit_log, log)
        self._add_to_cache(log)

        if self._es:
            self._es.index(index=self.es_index, body=log)

    def log_event(self, event_type: str, message: str, level: str = "info", **extra):
        """记录通用事件"""
        log = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "service": "api",
            "event_type": event_type,
            "message": message,
            **extra,
        }

        self._add_to_cache(log)

        if self._es:
            self._es.index(index=self.es_index, body=log)

    def _write_to_file(self, filepath: Path, log: Dict):
        """写入日志文件"""
        try:
            with open(filepath, "a") as f:
                f.write(json.dumps(log) + "\n")
        except Exception as e:
            print(f"Failed to write log: {e}")

    def search(
        self,
        query: str = None,
        level: str = None,
        service: str = None,
        user_id: str = None,
        start_time: str = None,
        end_time: str = None,
        limit: int = 100,
    ) -> List[Dict]:
        """搜索日志"""
        # 使用缓存搜索
        results = self.log_cache

        if query:
            results = [
                r for r in results if query.lower() in r.get("message", "").lower()
            ]
        if level:
            results = [r for r in results if r.get("level") == level]
        if service:
            results = [r for r in results if r.get("service") == service]
        if user_id:
            results = [r for r in results if r.get("user_id") == user_id]

        # 按时间排序
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return results[:limit]

    def get_statistics(
        self,
        start_time: str = None,
        end_time: str = None,
    ) -> Dict[str, Any]:
        """获取日志统计"""
        logs = self.log_cache

        # 统计各级别数量
        level_counts = defaultdict(int)
        for log in logs:
            level_counts[log.get("level", "unknown")] += 1

        # 统计各服务数量
        service_counts = defaultdict(int)
        for log in logs:
            service_counts[log.get("service", "unknown")] += 1

        # 统计错误
        errors = [r for r in logs if r.get("level") == "error"]

        # 统计响应时间
        response_times = [
            r.get("response_time", 0) for r in logs if r.get("response_time")
        ]
        avg_response_time = (
            sum(response_times) / len(response_times) if response_times else 0
        )

        return {
            "total_logs": len(logs),
            "level_counts": dict(level_counts),
            "service_counts": dict(service_counts),
            "error_count": len(errors),
            "avg_response_time_ms": round(avg_response_time, 2),
            "time_range": {
                "start": logs[-1].get("timestamp") if logs else None,
                "end": logs[0].get("timestamp") if logs else None,
            },
        }

    def get_recent_logs(self, limit: int = 100, level: str = None) -> List[Dict]:
        """获取最近的日志"""
        logs = self.log_cache
        if level:
            logs = [l for l in logs if l.get("level") == level]
        logs.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return logs[:limit]


# 全局实例
_log_service: Optional[LogService] = None


def get_log_service() -> LogService:
    """获取日志服务"""
    global _log_service
    if _log_service is None:
        _log_service = LogService()
    return _log_service
