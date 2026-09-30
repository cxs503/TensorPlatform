"""
API网关服务
提供路由管理、限流、负载均衡、熔断器等功能
"""
import asyncio
import time
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class RouteMethod(str, Enum):
    """路由方法"""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


class HealthStatus(str, Enum):
    """健康状态"""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"


class Backend:
    """后端服务"""

    def __init__(self, name: str, url: str, weight: int = 1, metadata: Dict = None):
        self.id = f"backend_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.url = url
        self.weight = weight
        self.metadata = metadata or {}
        self.health_status = HealthStatus.HEALTHY
        self.last_check = datetime.now().isoformat()
        self.failure_count = 0
        self.success_count = 0
        self.response_time_avg = 0


class Route:
    """API路由"""

    def __init__(
        self,
        path: str,
        method: str,
        backend_url: str,
        backends: List[str] = None,  # backend IDs
        timeout: int = 30,
        retries: int = 3,
        rate_limit: int = 100,  # 每分钟请求数
        auth_required: bool = False,
        plugins: List[str] = None,
    ):
        self.id = f"route_{uuid.uuid4().hex[:12]}"
        self.path = path
        self.method = method
        self.backend_url = backend_url
        self.backends = backends or []
        self.timeout = timeout
        self.retries = retries
        self.rate_limit = rate_limit
        self.auth_required = auth_required
        self.plugins = plugins or []
        self.enabled = True
        self.created_at = datetime.now().isoformat()


class CircuitBreaker:
    """熔断器"""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        half_open_requests: int = 3,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_requests = half_open_requests
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half-open"

    def record_success(self):
        """记录成功"""
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self):
        """记录失败"""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = "open"

    def can_request(self) -> bool:
        """是否可以发起请求"""
        if self.state == "closed":
            return True

        if self.state == "open":
            if self.last_failure_time:
                elapsed = time.time() - self.last_failure_time
                if elapsed >= self.recovery_timeout:
                    self.state = "half-open"
                    return True
            return False

        # half-open state
        return True


class RateLimiter:
    """速率限制器"""

    def __init__(self, rate: int, window: int = 60):
        self.rate = rate  # 最大请求数
        self.window = window  # 时间窗口（秒）
        self.requests: Dict[str, List[float]] = {}

    def is_allowed(self, key: str) -> bool:
        """检查是否允许请求"""
        now = time.time()
        window_start = now - self.window

        # 获取或初始化请求记录
        if key not in self.requests:
            self.requests[key] = []

        # 清理过期的请求记录
        self.requests[key] = [t for t in self.requests[key] if t > window_start]

        # 检查是否超过限制
        if len(self.requests[key]) >= self.rate:
            return False

        # 记录新请求
        self.requests[key].append(now)
        return True

    def get_remaining(self, key: str) -> int:
        """获取剩余请求数"""
        now = time.time()
        window_start = now - self.window

        if key not in self.requests:
            return self.rate

        self.requests[key] = [t for t in self.requests[key] if t > window_start]
        return max(0, self.rate - len(self.requests[key]))


class LoadBalancer:
    """负载均衡器"""

    def __init__(self, strategy: str = "round_robin"):
        self.strategy = strategy
        self.backend_states: Dict[str, int] = {}  # backend_id -> counter

    def select_backend(self, backends: List[Backend]) -> Optional[Backend]:
        """选择后端"""
        # 过滤健康的后端
        healthy = [b for b in backends if b.health_status == HealthStatus.HEALTHY]
        if not healthy:
            return None

        if self.strategy == "round_robin":
            return self._round_robin(healthy)
        elif self.strategy == "weighted":
            return self._weighted(healthy)
        elif self.strategy == "least_connections":
            return self._least_connections(healthy)
        else:
            return healthy[0]

    def _round_robin(self, backends: List[Backend]) -> Backend:
        """轮询"""
        backend_id = backends[0].id
        counter = self.backend_states.get(backend_id, 0)
        index = counter % len(backends)
        self.backend_states[backends[index].id] = counter + 1
        return backends[index]

    def _weighted(self, backends: List[Backend]) -> Backend:
        """加权轮询"""
        total_weight = sum(b.weight for b in backends)
        rand = (time.time() * 1000) % total_weight
        cumulative = 0
        for b in backends:
            cumulative += b.weight
            if rand <= cumulative:
                return b
        return backends[0]

    def _least_connections(self, backends: List[Backend]) -> Backend:
        """最少连接"""
        return min(backends, key=lambda b: b.success_count)


class APIGatewayService:
    """API网关服务"""

    def __init__(self):
        self.routes: Dict[str, Route] = {}
        self.backends: Dict[str, Backend] = {}
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.rate_limiters: Dict[str, RateLimiter] = {}
        self.load_balancer = LoadBalancer("round_robin")
        self.request_count = 0
        self.plugin_handlers: Dict[str, Callable] = {}

    def add_backend(
        self, name: str, url: str, weight: int = 1, metadata: Dict = None
    ) -> Backend:
        """添加后端服务"""
        backend = Backend(name, url, weight, metadata)
        self.backends[backend.id] = backend
        # 为后端创建熔断器
        self.circuit_breakers[backend.id] = CircuitBreaker()
        return backend

    def remove_backend(self, backend_id: str) -> bool:
        """移除后端"""
        if backend_id in self.backends:
            del self.backends[backend_id]
            if backend_id in self.circuit_breakers:
                del self.circuit_breakers[backend_id]
            return True
        return False

    def add_route(
        self,
        path: str,
        method: str,
        backend_url: str,
        backends: List[str] = None,
        timeout: int = 30,
        retries: int = 3,
        rate_limit: int = 100,
        auth_required: bool = False,
        plugins: List[str] = None,
    ) -> Route:
        """添加路由"""
        route = Route(
            path,
            method,
            backend_url,
            backends,
            timeout,
            retries,
            rate_limit,
            auth_required,
            plugins,
        )
        self.routes[route.id] = route

        # 为路由创建速率限制器
        self.rate_limiters[route.id] = RateLimiter(rate_limit)

        return route

    def remove_route(self, route_id: str) -> bool:
        """移除路由"""
        if route_id in self.routes:
            del self.routes[route_id]
            if route_id in self.rate_limiters:
                del self.rate_limiters[route_id]
            return True
        return False

    def find_route(self, path: str, method: str) -> Optional[Route]:
        """查找路由"""
        for route in self.routes.values():
            if (
                route.enabled
                and route.method == method
                and self._match_path(route.path, path)
            ):
                return route
        return None

    def _match_path(self, pattern: str, path: str) -> bool:
        """路径匹配（简单实现）"""
        # 支持精确匹配和前缀匹配
        if pattern.endswith("*"):
            return path.startswith(pattern[:-1])
        return pattern == path

    def check_rate_limit(self, route_id: str, client_key: str) -> bool:
        """检查速率限制"""
        limiter = self.rate_limiters.get(route_id)
        if not limiter:
            return True
        return limiter.is_allowed(client_key)

    def get_rate_limit_remaining(self, route_id: str, client_key: str) -> int:
        """获取剩余请求数"""
        limiter = self.rate_limiters.get(route_id)
        if not limiter:
            return -1
        return limiter.get_remaining(client_key)

    def select_backend_for_route(self, route: Route) -> Optional[Backend]:
        """为路由选择后端"""
        if route.backends:
            backend_objs = [
                self.backends[b_id] for b_id in route.backends if b_id in self.backends
            ]
        else:
            backend_objs = list(self.backends.values())

        return self.load_balancer.select_backend(backend_objs)

    def record_backend_success(self, backend_id: str, response_time: float):
        """记录后端成功"""
        backend = self.backends.get(backend_id)
        if backend:
            backend.success_count += 1
            # 计算平均响应时间
            backend.response_time_avg = (
                backend.response_time_avg * (backend.success_count - 1) + response_time
            ) / backend.success_count
            backend.health_status = HealthStatus.HEALTHY
            backend.last_check = datetime.now().isoformat()

        # 熔断器记录成功
        cb = self.circuit_breakers.get(backend_id)
        if cb:
            cb.record_success()

    def record_backend_failure(self, backend_id: str):
        """记录后端失败"""
        backend = self.backends.get(backend_id)
        if backend:
            backend.failure_count += 1
            if backend.failure_count >= 5:
                backend.health_status = HealthStatus.UNHEALTHY
            backend.last_check = datetime.now().isoformat()

        # 熔断器记录失败
        cb = self.circuit_breakers.get(backend_id)
        if cb:
            cb.record_failure()

    def check_circuit_breaker(self, backend_id: str) -> bool:
        """检查熔断器状态"""
        cb = self.circuit_breakers.get(backend_id)
        if not cb:
            return True
        return cb.can_request()

    def register_plugin(self, name: str, handler: Callable):
        """注册插件"""
        self.plugin_handlers[name] = handler

    def execute_plugins(self, route: Route, request: Dict, response: Dict):
        """执行插件"""
        for plugin_name in route.plugins:
            handler = self.plugin_handlers.get(plugin_name)
            if handler:
                try:
                    handler(request, response)
                except Exception:
                    pass  # 插件执行失败不影响主流程

    def get_gateway_stats(self) -> Dict:
        """获取网关统计"""
        return {
            "total_routes": len(self.routes),
            "total_backends": len(self.backends),
            "total_requests": self.request_count,
            "healthy_backends": sum(
                1
                for b in self.backends.values()
                if b.health_status == HealthStatus.HEALTHY
            ),
            "backends": [
                {
                    "id": b.id,
                    "name": b.name,
                    "url": b.url,
                    "health_status": b.health_status,
                    "response_time_avg": round(b.response_time_avg, 2),
                    "success_count": b.success_count,
                    "failure_count": b.failure_count,
                }
                for b in self.backends.values()
            ],
        }


# 全局实例
_gateway_service: Optional[APIGatewayService] = None


def get_gateway_service() -> APIGatewayService:
    """获取API网关服务"""
    global _gateway_service
    if _gateway_service is None:
        _gateway_service = APIGatewayService()
    return _gateway_service
