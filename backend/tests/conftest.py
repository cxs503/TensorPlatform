"""Pytest fixtures"""
import pytest
from fastapi.testclient import TestClient
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
try:                                    # optional runtime deps (cryptography, ...)
    from main import app
except Exception as exc:                # noqa: BLE001
    _APP_IMPORT_ERROR = exc
    app = None
else:
    _APP_IMPORT_ERROR = None

@pytest.fixture
def client():
    if app is None:                     # pure-module test suites must still collect
        pytest.skip(f"FastAPI app unavailable: {_APP_IMPORT_ERROR}")
    return TestClient(app)

@pytest.fixture
def auth_headers(client):
    resp = client.post("/api/v1/auth/login", json={"username":"admin","password":"CHANGE_ME_ADMIN"})
    token = resp.json().get("access_token","")
    return {"Authorization": f"Bearer {token}"}


# ── built-in async test shim ───────────────────────────────────────────────
# 环境里没有 pytest-asyncio 时，``@pytest.mark.asyncio`` 的协程测试会被 pytest
# 判为 "async def functions are not natively supported" 直接失败（P337W1-N02
# 基线：backend/tests/test_tenant_isolation.py 5 FAILED，全部是这一条环境缺口）。
# 这里用最小 shim 在事件循环里跑协程测试；若真正的插件在场则让位于它。
import asyncio as _asyncio
import inspect as _inspect


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "asyncio: coroutine test executed by the built-in shim"
    )


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if pyfuncitem.config.pluginmanager.hasplugin("asyncio"):
        return None
    if not _inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in getattr(pyfuncitem._fixtureinfo, "argnames", ())
    }
    _asyncio.run(pyfuncitem.obj(**kwargs))
    return True
