"""船舶平台端点单一事实源的回归测试。

覆盖 2026-11-01 事故（代理层写死 :8002 而服务在 :8001 + 服务宕机无守护）：
1. 端点解析/规范化/环境变量优先级；
2. 四条调用链必须共用同一 origin（防止再次各自硬编码漂移）；
3. 探测函数在服务不可达时不抛异常；
4. 服务在线时做一次真实健康探测（离线自动 skip，不算失败）。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from services.ship_api_endpoint import (
    API_PREFIX,
    DEFAULT_ORIGIN,
    DEFAULT_PORT,
    ship_api_base,
    ship_api_origin,
    ship_api_url,
    probe,
)


class TestResolution:
    def test_default_port_is_8001(self):
        """默认端口必须是船舶平台真实监听的 8001。"""
        assert DEFAULT_PORT == 8001
        assert ship_api_origin({}) == DEFAULT_ORIGIN
        assert ship_api_base({}) == DEFAULT_ORIGIN + API_PREFIX

    def test_env_origin_form(self):
        env = {"SHIP_API_BASE": "http://127.0.0.1:9001"}
        assert ship_api_origin(env) == "http://127.0.0.1:9001"
        assert ship_api_base(env) == "http://127.0.0.1:9001" + API_PREFIX

    @pytest.mark.parametrize(
        "raw",
        [
            "http://host:8001",
            "http://host:8001/",
            "http://host:8001/api/v1",
            "http://host:8001/api/v1/",
            "  http://host:8001/api/v1  ",
        ],
    )
    def test_all_input_forms_normalize_to_same_origin(self, raw):
        """四种写法（含尾斜杠/含 api/v1/前后空格）必须收敛为同一 origin。"""
        assert ship_api_origin({"SHIP_API_BASE": raw}) == "http://host:8001"

    def test_env_priority_and_legacy_alias(self):
        assert ship_api_origin({"SHIP_API_BASE": "http://a:1", "SHIP_PLATFORM_URL": "http://b:2"}) == "http://a:1"
        assert ship_api_origin({"SHIP_PLATFORM_URL": "http://b:2"}) == "http://b:2"
        # 空串视为未设置 → 回落到默认
        assert ship_api_origin({"SHIP_API_BASE": "", "SHIP_PLATFORM_URL": ""}) == DEFAULT_ORIGIN

    @pytest.mark.parametrize(
        "path,expected",
        [
            ("health", DEFAULT_ORIGIN + "/api/v1/health"),
            ("/health", DEFAULT_ORIGIN + "/api/v1/health"),
            ("calculations/resistance", DEFAULT_ORIGIN + "/api/v1/calculations/resistance"),
            ("/api/v1/calculations/resistance", DEFAULT_ORIGIN + "/api/v1/calculations/resistance"),
        ],
    )
    def test_url_join_never_doubles_prefix(self, path, expected):
        assert ship_api_url(path, {}) == expected


class TestAllCallChainsAgree:
    """四条调用链必须解析到同一 origin —— 防端口漂移复发。"""

    def test_proxy_default_matches_ssot(self, monkeypatch):
        from services import ship_proxy

        monkeypatch.delenv("SHIP_API_BASE", raising=False)
        monkeypatch.delenv("SHIP_PLATFORM_URL", raising=False)
        proxy = ship_proxy.ShipProxy()
        assert proxy.base_url == ship_api_base()
        assert ":8002" not in proxy.base_url

    def test_proxy_honours_env_override(self, monkeypatch):
        from services import ship_proxy

        monkeypatch.setenv("SHIP_API_BASE", "http://127.0.0.1:9999/api/v1")
        assert ship_proxy.ShipProxy().base_url == "http://127.0.0.1:9999/api/v1"

    @pytest.mark.parametrize(
        "module_file,var",
        [
            ("backend/api/domain/ship_batch.py", "RESISTANCE_ENDPOINT"),
            ("backend/api/domain/ship_benchmark.py", None),
        ],
    )
    def test_no_hardcoded_legacy_port(self, module_file, var):
        """源码中不得再出现写死的 :8002 端点（只允许注释里的历史说明）。"""
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(root, module_file)
        if not os.path.exists(path):
            pytest.skip(f"{module_file} 不存在")
        with open(path, encoding="utf-8") as fh:
            offenders = [
                ln
                for ln in fh
                if "8002" in ln and not ln.lstrip().startswith("#")
            ]
        assert offenders == [], f"{module_file} 仍有写死的 8002: {offenders}"


class TestProbe:
    def test_probe_unreachable_is_not_exception(self):
        """指向空端口 → 返回 ok=False 且带原因，不抛异常。"""
        env = {"SHIP_API_BASE": "http://127.0.0.1:9"}  # discard port，必然拒连
        result = probe(env, timeout=1.0)
        assert result["ok"] is False
        # 依次尝试 {base}/health 与 {origin}/health（平台健康路由只在根路径）
        assert any(u.endswith("/api/v1/health") for u in result["tried"])
        assert any(u.endswith(":9/health") for u in result["tried"])
        assert result["detail"]

    def test_probe_reports_real_service_when_online(self):
        """服务在线则断言健康；离线 skip（不让缺依赖把门禁染红）。"""
        result = probe(timeout=2.0)
        if not result["ok"]:
            pytest.skip(f"船舶平台离线：{result['detail']}")
        assert result["status"] == 200
        assert "healthy" in str(result["detail"])


class TestWatchdogScript:
    def test_watchdog_runs_and_emits_json(self):
        """守护脚本必须可执行且输出机器可解析 JSON（退出码 0 在线 / 1 离线）。"""
        import json

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        script = os.path.join(root, "tools/ops/ship_api_watchdog.py")
        assert os.path.exists(script), "缺少 tools/ops/ship_api_watchdog.py"
        proc = subprocess.run(
            [sys.executable, script, "--timeout", "2"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode in (0, 1), proc.stderr[-500:]
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        assert "ok" in payload and "url" in payload