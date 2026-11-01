"""m1757 — 阻力端点路线复核回归测试（无网络，psycopg2 以桩占位）。

锁定三件事：

1. **域内水面船默认走 Holtrop 全量路线**：KVLCC2 / KCS / 18 万吨散货船在
   `resistance_method="auto"` 下 `resistance_route=="holtrop"`，且 R_T 与仓内
   Holtrop 参考实现的偏差 < 3 %（平台 15 % 可接受带的 1/5）。
2. **越域船型不静默外推**：SUBOFF 几何（B/T=1.07、Cb=0.542、L=4.36 m）在
   `auto` 下退回 legacy；强制 `holtrop` 必须 400 并给出逐项越域理由。
3. **分项可加且来源可溯**：R_T == R_F + R_form + R_residual（残差 < 1e-3 N），
   且响应里的 `form_factor` 是实际使用值（Holtrop 路线为算出的 1+k1）。
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def _install_psycopg2_stub() -> None:
    """端点 import 链会拉 DB 引擎；本测试只要纯函数，用桩占位。"""
    if "psycopg2" in sys.modules:
        return

    class _Stub(types.ModuleType):
        def __getattr__(self, name):                      # noqa: D105
            value = type(name, (), {"__init__": lambda *a, **k: None})
            setattr(self, name, value)
            return value

    for name in ("psycopg2", "psycopg2.extensions", "psycopg2.extras"):
        mod = _Stub(name)
        mod.__version__ = "2.9.9"
        mod.paramstyle = "pyformat"
        sys.modules[name] = mod


_install_psycopg2_stub()

from fastapi import HTTPException                              # noqa: E402

from api.calculations import ResistanceRequest, calc_resistance  # noqa: E402
from services.resistance_holtrop import (                      # noqa: E402
    ResistanceInput,
    applicability_violations,
    compute_resistance,
)

RHO = 1025.0
NU = 1.19e-6

#: 域内水面船（主尺度=公开标模，湿面积不注入，交给各路自算）
SURFACE_HULLS = {
    "KVLCC2": dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098, cm=0.9998,
                   lcb=3.48, speeds=(7.20, 7.72, 8.23)),
    "KCS": dict(length=230.0, beam=32.2, draft=10.8, cb=0.651, cm=0.9849,
                lcb=-1.48, speeds=(5.00, 6.17, 7.40)),
    "Bulk180k": dict(length=290.0, beam=45.0, draft=18.0, cb=0.82, cm=0.995,
                     lcb=2.5, speeds=(6.00, 7.00, 8.00)),
}

SUBOFF = dict(length=4.356, beam=0.508, draft=0.476, cb=0.542)


def _endpoint(**kw):
    return asyncio.run(calc_resistance(ResistanceRequest(
        water_density=RHO, kinematic_viscosity=NU, **kw)))


@pytest.mark.parametrize("ship", sorted(SURFACE_HULLS))
def test_auto_routes_surface_hulls_to_holtrop(ship):
    h = SURFACE_HULLS[ship]
    for v in h["speeds"]:
        resp = _endpoint(ship_type=ship, speed_ms=v, cb=h["cb"],
                         length=h["length"], beam=h["beam"], draft=h["draft"])
        assert resp.resistance_route == "holtrop", (ship, v, resp.method_notes)
        assert resp.method_notes  # 假设/适用范围随响应下发
        assert resp.wetted_surface_source == "holtrop_regression"


@pytest.mark.parametrize("ship", sorted(SURFACE_HULLS))
def test_auto_matches_holtrop_reference_within_3pct(ship):
    h = SURFACE_HULLS[ship]
    for v in h["speeds"]:
        resp = _endpoint(ship_type=ship, speed_ms=v, cb=h["cb"],
                         length=h["length"], beam=h["beam"], draft=h["draft"])
        ref = compute_resistance(ResistanceInput(
            length=h["length"], beam=h["beam"], draft=h["draft"], cb=h["cb"],
            speed_ms=v, rho=RHO, nu=NU, cm=h["cm"], lcb_pct=h["lcb"]))
        err = 100.0 * (resp.total_resistance_n - ref["rt"]) / ref["rt"]
        assert abs(err) < 3.0, f"{ship} Fn={resp.froude_number:.3f}: {err:+.2f}%"


def test_components_are_additive_and_exposed_form_factor_is_used():
    resp = _endpoint(ship_type="KVLCC2", length=320.0, beam=58.0, draft=20.8,
                     cb=0.8098, speed_ms=7.716)
    total = (resp.frictional_resistance_n + resp.form_resistance_n
             + resp.residual_resistance_n)
    assert abs(total - resp.total_resistance_n) < 1e-3
    # Holtrop 路线必须下发算出的 1+k1（而非请求默认 1.0）
    assert resp.form_factor > 1.0
    assert resp.form_factor == pytest.approx(1.0 + resp.form_resistance_n
                                             / resp.frictional_resistance_n, rel=1e-3)


def test_legacy_route_still_reachable_and_keeps_its_bias_documented():
    """legacy 路线保留（对标链复现用），且其偏差在响应里被显式标注。"""
    legacy = _endpoint(ship_type="KCS", length=230.0, beam=32.2, draft=10.8,
                       cb=0.651, speed_ms=6.17, resistance_method="legacy")
    assert legacy.resistance_route == "legacy"
    assert any("legacy" in note for note in legacy.method_notes)
    ref = compute_resistance(ResistanceInput(length=230.0, beam=32.2, draft=10.8,
                                            cb=0.651, speed_ms=6.17, rho=RHO, nu=NU,
                                            cm=0.9849, lcb_pct=-1.48))
    err = 100.0 * (legacy.total_resistance_n - ref["rt"]) / ref["rt"]
    # 该路线对 KCS 类船型高估 >15%（平台的不可接受带），这一偏差是本单修复对象
    assert err > 15.0, f"legacy 偏差应 >15%，实测 {err:.2f}%"


def test_out_of_domain_hull_falls_back_and_forced_holtrop_rejects():
    assert applicability_violations(**SUBOFF, speed_ms=3.0)  # 越域
    resp = _endpoint(ship_type="SUBOFF", speed_ms=3.0, **SUBOFF)
    assert resp.resistance_route == "legacy"
    assert any("Holtrop domain" in n for n in resp.method_notes)
    with pytest.raises(HTTPException) as excinfo:
        _endpoint(ship_type="SUBOFF", speed_ms=3.0, resistance_method="holtrop", **SUBOFF)
    assert excinfo.value.status_code == 400
    assert "适用域" in excinfo.value.detail


def test_validated_wetted_surface_overrides_regression():
    """几何验证过的 S 优先于回归式（平台"几何先验证"规则）。"""
    base = _endpoint(ship_type="KVLCC2", length=320.0, beam=58.0, draft=20.8,
                     cb=0.8098, speed_ms=7.716)
    given = _endpoint(ship_type="KVLCC2", length=320.0, beam=58.0, draft=20.8,
                      cb=0.8098, speed_ms=7.716, wetted_surface_m2=27000.0)
    assert given.wetted_surface_source == "input_validated"
    assert given.wetted_surface_m2 == pytest.approx(27000.0)
    # 湿面积 +1.2 % → R_F 项同比上升，R_T 必须随之增大且仍在 5 % 内
    assert 0.0 < (given.total_resistance_n - base.total_resistance_n) / base.total_resistance_n < 0.05


def test_unknown_method_is_rejected():
    with pytest.raises(HTTPException) as excinfo:
        _endpoint(ship_type="KVLCC2", length=320.0, beam=58.0, draft=20.8,
                  cb=0.8098, speed_ms=7.716, resistance_method="bogus")
    assert excinfo.value.status_code == 400