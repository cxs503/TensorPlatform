"""PR-6: Soul Runtime 生产入口接线回归（AOG SPEC M07）。

审计 #3554 M07 实锤：/tensor/soul* 只挂在 factory_app（独立进程），
main:app 上不可达 → 生产入口 404。本文件三断言：

  1. main:app 真挂载（直接 import main，不用 conftest 的 skip 夹具——
     生产入口坏了必须红，不允许静默跳过）；
  2. hypothesis 事件类型进入两处契约（services.EVENT_TYPES + API Literal）；
  3. 未知事件类型被拒（mutation probe：门必须能失败）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


class TestSoulWiredIntoMainApp:
    def test_soul_routes_mounted_on_main_app(self):
        import main  # noqa: PLC0415 — 生产入口本体， import 失败=真红线

        paths = {getattr(r, "path", "") for r in main.app.routes}
        soul = [p for p in paths if p.startswith("/api/v1/tensor/soul")]
        assert soul, "soul routes not mounted on main:app (production entry 404)"
        assert any(p.endswith("/observe") for p in soul), f"observe endpoint missing: {soul}"
        assert any(p.endswith("/agents") for p in soul), f"agents endpoint missing: {soul}"


class TestHypothesisEventContract:
    def test_services_contract_includes_hypothesis(self):
        from services.soul_runtime import EVENT_TYPES

        assert "hypothesis" in EVENT_TYPES, (
            "AOG SPEC: 开发过程即研究过程——假设（先注册后执行）必须是合法事件类型"
        )

    def test_api_model_accepts_hypothesis(self):
        import uuid

        from pydantic import ValidationError

        from api.soul_runtime import Observation

        body = Observation(
            agent_id=uuid.uuid4(),
            event_type="hypothesis",
            source="prereg",
            payload={"statement": "τ_eff 地板可消除艉重", "falsifiable": True},
            idempotency_key="k-h1",
        )
        assert body.event_type == "hypothesis"

    def test_api_model_rejects_unknown_event_type(self):
        import uuid

        from pydantic import ValidationError

        from api.soul_runtime import Observation

        with pytest.raises(ValidationError):
            Observation(
                agent_id=uuid.uuid4(),
                event_type="vibe",  # 不在枚举内必须 422
                source="prereg",
                payload={},
                idempotency_key="k-h2",
            )
