"""
Prometheus Metrics 集成
提供应用指标收集和暴露
"""
import time
from typing import Callable

from fastapi import Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    Summary,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware

# 请求计数器
REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP Requests", ["method", "endpoint", "status_code"]
)

# 请求耗时直方图
REQUEST_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP Request Duration", ["method", "endpoint"]
)

# 活跃请求数
ACTIVE_REQUESTS = Gauge("http_active_requests", "Active HTTP Requests")

# 响应大小
RESPONSE_SIZE = Summary(
    "http_response_size_bytes", "HTTP Response Size", ["method", "endpoint"]
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Prometheus 中间件 - 收集指标"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 增加活跃请求数
        ACTIVE_REQUESTS.inc()

        # 记录开始时间
        start_time = time.time()

        try:
            # 处理请求
            response = await call_next(request)
            return response
        finally:
            # 计算耗时
            duration = time.time() - start_time

            # 增加请求计数
            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=request.url.path,
                status_code=response.status_code,
            ).inc()

            # 记录请求耗时
            REQUEST_DURATION.labels(
                method=request.method, endpoint=request.url.path
            ).observe(duration)

            # 降低活跃请求数
            ACTIVE_REQUESTS.dec()


def metrics_endpoint(request: Request) -> Response:
    """Prometheus 指标端点"""
    from fastapi.responses import Response

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def setup_prometheus(app):
    """在 FastAPI 应用中设置 Prometheus"""
    app.add_middleware(PrometheusMiddleware)
    app.add_route("/metrics", metrics_endpoint)


if __name__ == "__main__":
    # 测试代码
    print("Prometheus Metrics Module loaded")
    print(f"Metrics endpoint: /metrics")
    print(f"Available metrics:")
    print("  - http_requests_total")
    print("  - http_request_duration_seconds")
    print("  - http_active_requests")
    print("  - http_response_size_bytes")
