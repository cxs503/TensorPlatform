"""m1406 增量收编测试：``/calculations/benchmark_audit`` 账本端点 + C_T 反推 + 端口探测。

背景（三处真实缺陷，均可在本文件内复现）：

1. 对标链把 ``speed`` 当载荷键发给只认 ``speed_ms`` 的端点 → 静默按 1.0 m/s 计算；
2. 只探测 :8002（多数部署已不监听）→ 整链退化为 "API call failed"，``calc_ct`` 恒 0；
3. 端点只回 ``total_resistance_n``，链上却读 ``ct`` → 同样恒 0。

模块按文件路径加载（``api/__init__`` 会连带 psycopg2 等部署依赖，不属于被测逻辑）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from services.benchmark_integrity import (  # noqa: E402
    DEFAULT_PLATFORM_PORTS,
    ship_platform_bases,
    total_resistance_coefficient,
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


ship_benchmark = _load_module(
    "ship_benchmark_m1614", BACKEND_DIR / "api" / "domain" / "ship_benchmark.py"
)


# ---------------------------------------------------------------------------
# 1. 账本端点
# ---------------------------------------------------------------------------

def test_benchmark_audit_endpoint_grades_every_case():
    out = ship_benchmark.benchmark_audit()
    assert out["total"] == 12
    assert out["binding"] is True
    counts = out["verdict_counts"]
    assert counts["PASS"] + counts["WARN"] + counts["WITHHELD"] + counts["FAIL"] == 12
    # 正典 v3.0：SUBOFF 4 行可复核（PASS），全船 8 行缺实测湿面积（WITHHELD）
    assert counts["PASS"] == 4 and counts["WITHHELD"] == 8
    assert counts["FAIL"] == 0


def test_benchmark_audit_endpoint_reports_identity_checks():
    out = ship_benchmark.benchmark_audit()
    row = next(r for r in out["rows"] if r["case_id"] == "SUBOFF-01")
    names = {c["check"] for c in row["checks"]}
    assert {"froude_closure", "friction_floor", "resistance_closure"} <= names
    assert row["verdict"] == "PASS"


def test_benchmark_audit_endpoint_is_serialisable():
    out = ship_benchmark.benchmark_audit()
    json.dumps(out)  # 端点必须能直接 JSON 序列化（FastAPI 返回契约）


# ---------------------------------------------------------------------------
# 2. C_T 反推（端点只给 R_T 时必须能还原系数，而不是静默 0）
# ---------------------------------------------------------------------------

def test_ct_derived_from_rt_and_reference_area():
    case = {"geometry": {"wetted_surface_m2": 5.42}}
    resp = {"total_resistance_n": 92.52}
    ct = total_resistance_coefficient(resp, case, rho=998.2, speed_ms=3.0)
    assert ct == pytest.approx(92.52 / (0.5 * 998.2 * 5.42 * 9.0), rel=1e-12)
    assert ct == pytest.approx(0.003800, abs=5e-6)


def test_ct_prefers_reported_coefficient():
    case = {"geometry": {"wetted_surface_m2": 5.42}}
    resp = {"total_resistance_coefficient": 0.0038, "total_resistance_n": 1.0}
    assert total_resistance_coefficient(resp, case, 998.2, 3.0) == pytest.approx(0.0038)


def test_ct_withheld_when_no_area():
    """没有湿面积就如实返回 None（不猜），由调用方记 WITHHELD。"""
    assert total_resistance_coefficient({"total_resistance_n": 92.52}, {}, 998.2, 3.0) is None
    assert total_resistance_coefficient({}, {}, 998.2, 3.0) is None


def test_resistance_endpoint_publishes_ct_and_method():
    """端点在 HTTP 路径上也要给出 C_T 与模型来源（否则链上又只能猜）。"""
    import asyncio

    calcs = _load_module("calcs_m1614", BACKEND_DIR / "api" / "calculations.py")
    resp = asyncio.run(calcs.calc_resistance(calcs.ResistanceRequest(
        ship_type="SUBOFF", speed_ms=3.0, length=4.356, beam=0.508, draft=0.508, cb=0.542)))
    assert resp.total_resistance_coefficient > 0
    assert resp.total_resistance_coefficient == pytest.approx(
        resp.total_resistance_n / (0.5 * 998.0 * resp.wetted_surface_m2 * 9.0), rel=1e-6)
    assert "ITTC-1957" in resp.method


# ---------------------------------------------------------------------------
# 3. 端口探测
# ---------------------------------------------------------------------------

def test_platform_bases_cover_historical_ports():
    bases = ship_platform_bases()
    assert bases[0].endswith(":8001")
    for port in DEFAULT_PLATFORM_PORTS:
        assert any(b.endswith(f":{port}") for b in bases)


def test_platform_bases_env_wins_and_dedups(monkeypatch):
    monkeypatch.setenv("SHIP_PLATFORM_URL", "http://ship-engine.internal:9000/")
    bases = ship_platform_bases()
    assert bases[0] == "http://ship-engine.internal:9000"
    assert len(bases) == len(set(bases))