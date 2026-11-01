"""m1649 · 耐波性（seakeeping）验证欠账的 CI 判据。

三层断言，全部可在 CI 离线跑通（不需要 :8001）：

1. **独立复算层** —— `tools/benchmarks/seakeeping_anchors.py` 的 11 项判据必须全绿。
   这一层的价值在于「另一条数值路线」：解析闭式 vs 数值积分/极值搜索，
   因此能抓到「闭式写错、且测试跟着一起抄错」的失败模式（m1433 自带测试做不到）。
2. **判据自反性（有牙证明）** —— 给锚点注入一个已知偏差，必须被抓到；
   否则判据是空转的。
3. **接线层** —— 耐波域模块必须被 `backend/main.py` 挂载。
   m1649 复算时该模块（3 个端点、518 行服务）从未被 import，线上实测
   `/api/v1/calculations/seakeeping_rao` 返回 404，属「已合并但不可达」的死代码。
"""

from __future__ import annotations

import math
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools", "benchmarks"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

import seakeeping_anchors as anc  # noqa: E402
from services import seakeeping as sk  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 独立复算层
# ---------------------------------------------------------------------------


def test_anchor_ledger_is_green():
    ledger = anc.run()
    assert ledger["verdict"] == "PASS", f"failed anchors: {ledger['failed']}"
    assert ledger["gated_count"] >= 10
    # 恒等/数值层的整体精度必须远优于 15% 的平台精度线
    assert ledger["worst_gated_error"] < 1e-3


@pytest.mark.parametrize("anchor_id", ["S1", "S2", "S3", "S4", "S5", "S6",
                                       "S7", "S8", "S9", "S10"])
def test_each_gated_anchor(anchor_id):
    fn = {a.__name__.split("_")[0].upper(): a for a in anc.ANCHORS}[anchor_id]
    res = fn()
    assert res["error"] <= res["tol"], f"{anchor_id} {res['name']}: {res['error']:.3e}"


# ---------------------------------------------------------------------------
# 2. 判据自反性：注入偏差必须被抓到
# ---------------------------------------------------------------------------


def test_anchor_detects_a_wrong_closed_form(monkeypatch):
    """把波数故意放大 2% ，S1（色散）必须变红。"""
    real = sk.wave_number
    monkeypatch.setattr(sk, "wave_number", lambda w: real(w) * 1.02)
    assert anc.s1_dispersion()["error"] > 1e-2


def test_anchor_detects_a_wrong_lengthwise_factor(monkeypatch):
    real = sk.waterplane_length_factor
    monkeypatch.setattr(sk, "waterplane_length_factor", lambda u: real(u) * 0.97)
    assert anc.s2_lengthwise_factor()["error"] > 1e-2


# ---------------------------------------------------------------------------
# 3. 接线层：耐波域必须被主应用挂载
# ---------------------------------------------------------------------------


def test_seakeeping_router_is_mounted_in_main():
    text = open(os.path.join(_ROOT, "backend", "main.py"), encoding="utf-8").read()
    assert "ship_seakeeping" in text, (
        "backend/api/domain/ship_seakeeping.py 未被 main.py 挂载："
        "/api/v1/calculations/seakeeping_* 在线上不可达"
    )
    assert re.search(r"include_router\(\s*ship_seakeeping\.router", text)


def test_seakeeping_routes_are_served_by_an_app():
    """最小 ASGI 装配：路由可服务且几何门禁在端点层生效（400 而非 200）。"""
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
    except Exception:  # pragma: no cover
        pytest.skip("fastapi TestClient unavailable")

    # 按文件路径加载，绕开 api/domain/__init__.py 的 DB 依赖（CI 轻环境无 psycopg2）
    import importlib.util
    import types

    path = os.path.join(_ROOT, "backend", "api", "domain", "ship_seakeeping.py")
    spec = importlib.util.spec_from_file_location("_m1649_ship_seakeeping", path)
    ship_seakeeping = importlib.util.module_from_spec(spec)
    sys.modules["_m1649_ship_seakeeping"] = ship_seakeeping
    spec.loader.exec_module(ship_seakeeping)
    assert isinstance(ship_seakeeping, types.ModuleType)

    app = FastAPI()
    app.include_router(ship_seakeeping.router, prefix="/api/v1")
    client = TestClient(app)

    hull = {"L": 150.0, "B": 22.0, "T": 8.0, "Cb": 0.65, "Cwp": 0.75,
            "displacement_t": 17589.0, "KG": 8.0, "GM_T": 1.0}
    ok = client.post("/api/v1/calculations/seakeeping_rao",
                     json={"hull": hull, "n_points": 21})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["hydrostatics"]["geometry_residual_pct"] == pytest.approx(0.0, abs=1e-9)
    assert body["natural_periods_s"]["heave"] > 0.0

    # 几何不闭合必须被拒（∇ 与 Cb·L·B·T 差 +30%）
    bad = dict(hull, displacement_t=23000.0)
    assert client.post("/api/v1/calculations/seakeeping_rao",
                       json={"hull": bad, "n_points": 21}).status_code == 400

    spec = client.post("/api/v1/calculations/seakeeping_spectrum",
                       json={"kind": "jonswap", "Hs": 3.0, "Tp": 8.0, "n_points": 201})
    assert spec.status_code == 200
    assert abs(spec.json()["Hs_error_pct"]) < 1e-9

    resp = client.post("/api/v1/calculations/seakeeping_response",
                       json={"hull": hull, "Hs": 3.0, "Tp": 8.0, "n_points": 201})
    assert resp.status_code == 200
    assert resp.json()["statistics"]["sigma_heave"] > 0.0


def test_heave_rao_long_wave_and_short_wave_limits_are_physical():
    """两端极限：λ>>L ⇒ RAO→1；λ<<L ⇒ RAO→0（单调衰减且为正）。"""
    m = sk.SeakeepingModel(L=150.0, B=22.0, T=8.0, Cb=0.65, Cwp=0.75,
                           displacement_t=17589.0, KG=8.0, GM_T=1.0)
    assert m.rao_at(1e-5)["heave_rao_m_per_m"] == pytest.approx(1.0, abs=1e-3)
    hi = m.rao_at(3.0)["heave_rao_m_per_m"]
    assert 0.0 < hi < 0.05
    # 高频率段必须单调下降（无寄生振荡）
    vals = [m.rao_at(w)["heave_rao_m_per_m"] for w in (0.8, 1.2, 1.8, 2.4, 3.0)]
    assert all(b < a for a, b in zip(vals, vals[1:]))