"""m1849 — 振动噪声域接线修复 + 接线普查闸门判据锁。

背景
----
m1820 已实锤：``backend/api/domain/ship_vibration.py`` 的 6 条
``/api/v1/calculations/{vibration,noise}/*`` 端点**从未被 main.py import/挂载**，
线上只能 404，于是该域被判 NOT_SUPPORTED —— "代码 REAL、能力不可达"。
本文件把修复与普查闸门一起钉成机器判据：

* **W1 模块面**：模块真的声明了那 6 条端点（不是文档里的口头承诺）；
* **W2 挂载面**：``backend/main.py`` 真的 ``include_router`` 了该 router
  （AST 解析，不 import 整个 app，避免 DB/环境依赖）；
* **W3 可达表**：把 router 挂到一个最小 FastAPI app 上，``router.routes`` 里
  必须能查到那 6 条路径（"挂载 = 可路由"，而非"import 成功"）；
* **W4 数值锚**：端点返回的量必须对上**独立**闭式解（Euler–Bernoulli 梁、
  Kirchhoff 薄板、单自由度隔振、质量定律隔声、IEC 61672 A 计权），
  参考式在测试内独立实现，不复用被测模块——因此不是自证；
* **W5 闸门负控制**：``tools/audit/wiring_census.py`` 在合成夹具树上必须能把
  未挂载模块判成 ``UNWIRED``，挂载后转 ``LIVE/MOUNTED_*``。没有负控制，
  闸门"永远绿"等于没闸门；
* **W6 baseline**：未登记的新增未接线域必须让闸门 FAIL，已登记（带理由）豁免。

判据基线：全部来自定义式 / 解析解 / 标准表（IEC 61672 标称值），不引用任何
需要联网的文献点值，故可离线自证。
"""
from __future__ import annotations

import ast
import math
import os
import sys
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, os.path.join(REPO, "tools", "audit"))

# 按文件路径加载：绕过 api/domain/__init__.py 的 DB（psycopg2）依赖，
# 使本判据锁在无数据库的裸环境也能跑（m1820 探针同策略）。
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "ship_vibration_under_test",
    os.path.join(REPO, "backend", "api", "domain", "ship_vibration.py"))
VIB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(VIB)
# pydantic v2 解析 Optional/List 前向引用需要模块已在 sys.modules 里
sys.modules[_spec.name] = VIB
VIB.NoiseRequest.model_rebuild()
VIB.LevelsRequest.model_rebuild()
VIB.ExcitationRequest.model_rebuild()

import wiring_census as WC  # noqa: E402

EXPECTED_PATHS = {
    "/calculations/vibration/beam_natural",
    "/calculations/vibration/plate_natural",
    "/calculations/vibration/excitation",
    "/calculations/vibration/isolation",
    "/calculations/noise/panel_tl",
    "/calculations/noise/levels",
}


# ── W1 模块面 ───────────────────────────────────────────────────────────
def test_w1_module_declares_six_endpoints():
    got = {e["path"] for e in WC.scan_module(
        __import__("pathlib").Path(REPO) / "backend/api/domain/ship_vibration.py")["endpoints"]}
    assert got == EXPECTED_PATHS, f"端点声明漂移: 缺 {EXPECTED_PATHS - got} 多 {got - EXPECTED_PATHS}"


# ── W2 挂载面 ───────────────────────────────────────────────────────────
def test_w2_main_py_mounts_vibration_router():
    mounts = WC.scan_mounts(__import__("pathlib").Path(REPO) / "backend/main.py")
    assert mounts["parse_error"] is None
    assert "ship_vibration" in mounts["mounted"], (
        "main.py 未挂载 ship_vibration.router → 该域端点线上必 404（m1820 回归）")


def test_w2_mount_is_guarded():
    """接线必须容错：import 失败不得阻断 app 启动（部署安全）。"""
    src = (__import__("pathlib").Path(REPO) / "backend/main.py").read_text()
    tree = ast.parse(src)
    guarded = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            seg = ast.get_source_segment(src, node) or ""
            if "ship_vibration" in seg and "include_router" in seg:
                guarded = True
    assert guarded, "ship_vibration 的 include_router 未包在 try/except 内"


# ── W3 可达表 ───────────────────────────────────────────────────────────
def test_w3_router_exposes_expected_paths():
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(VIB.router, prefix="/api/v1")
    live = {getattr(r, "path", "") for r in VIB.router.routes}
    assert {"/api/v1" + p for p in EXPECTED_PATHS} <= live | {
        r.path for r in app.routes if hasattr(r, "path")}


def test_w3_local_census_verdict_is_not_unwired():
    doc = WC.census(__import__("pathlib").Path(REPO), base_url=None)
    row = {r["module"]: r for r in doc["modules"]}["ship_vibration"]
    assert row["mounted_in_main"] is True
    assert row["verdict"] != WC.UNWIRED


# ── W4 数值锚（独立闭式解）────────────────────────────────────────────
def _ssss_beam_f1(E, I, rho, A, L):
    """简支梁一阶：f1 = (pi^2 / 2pi) * sqrt(EI/(rho A L^4))   (独立实现)"""
    return (math.pi ** 2 / (2 * math.pi)) * math.sqrt(E * I / (rho * A * L ** 4))


def _cantilever_f1(E, I, rho, A, L):
    """悬臂梁一阶：lambda1=1.87510407, f1=lambda1^2/(2pi) sqrt(EI/(rho A L^4))"""
    return 1.87510407 ** 2 / (2 * math.pi) * math.sqrt(E * I / (rho * A * L ** 4))


def _plate_f11_ssss(a, b, E, nu, rho, h):
    """Kirchhoff SSSS 板一阶（独立实现）: f = pi/2 sqrt(D/(rho h)) (1/a^2+1/b^2)"""
    D = E * h ** 3 / (12 * (1 - nu ** 2))
    return math.pi / 2 * math.sqrt(D / (rho * h)) * (1 / a ** 2 + 1 / b ** 2)


@pytest.mark.parametrize("bc,ref", [("pinned-pinned", _ssss_beam_f1),
                                    ("cantilever", _cantilever_f1)])
def test_w4_beam_natural_matches_closed_form(bc, ref):
    E, I, rho, A, L = 2.1e11, 8.0e-6, 7850.0, 0.0096, 4.0
    out = VIB.api_beam_natural(VIB.BeamRequest(
        bc=bc, E=E, I=I, rho=rho, A=A, L=L, n_modes=3, fem_check=True))
    f1 = out["frequencies_hz"][0]
    r = ref(E, I, rho, A, L)
    assert abs(f1 - r) / r < 0.01, (bc, f1, r)
    # FEM 交叉核：一致质量 Hermite 单元应收敛到解析值
    assert abs(out["fem_vs_analytic_pct"][0]) < 3.0


def test_w4_plate_natural_matches_closed_form():
    a, b, E, nu, rho, h = 1.2, 0.8, 2.1e11, 0.3, 7850.0, 0.008
    out = VIB.api_plate_natural(VIB.PlateRequest(
        a=a, b=b, E=E, nu=nu, rho=rho, h=h, n_max=2, rayleigh_check=True))
    f11 = [m for m in out["modes"] if m["m"] == 1 and m["n"] == 1][0]["f_hz"]
    ref = _plate_f11_ssss(a, b, E, nu, rho, h)
    assert abs(f11 - ref) / ref < 0.005, (f11, ref)
    assert abs(out["rayleigh_vs_navier_pct"]) < 1.0


def test_w4_isolation_transmissibility_identity():
    """T = sqrt((1+(2 zeta r)^2)/((1-r^2)^2+(2 zeta r)^2))，且 r>>1 时限幅衰减。"""
    f_n, zeta = 10.0, 0.05
    out = VIB.api_isolation(VIB.IsolationRequest(f=5.0, f_n=f_n, zeta=zeta))
    r = 0.5
    t_ref = math.sqrt((1 + (2 * zeta * r) ** 2) /
                      ((1 - r ** 2) ** 2 + (2 * zeta * r) ** 2))
    assert abs(out["transmissibility"] - t_ref) / t_ref < 1e-6
    hi = VIB.api_isolation(VIB.IsolationRequest(f=100.0, f_n=f_n, zeta=zeta))
    assert hi["transmissibility"] < 0.1        # 隔振区必须真的衰减
    # 衰减量记法（20log10 T 为负 / 衰减正）不作硬绑定，只判量级 >20 dB
    assert abs(float(hi["isolation_db"])) > 20.0


def test_w4_mass_law_normal_incidence_tl():
    """正入射质量定律 TL = 20 log10(omega m / (2 rho c))。"""
    f, m = 1000.0, 47.1          # 6mm 钢面密度
    out = VIB.api_panel_tl(VIB.NoiseRequest(f=f, surface_density=m, field_incidence=False))
    ref = 20 * math.log10(2 * math.pi * f * m / (2 * VIB.AIR_RHO * VIB.AIR_C))
    assert abs(out["tl_normal_db"] - ref) < 0.5, (out["tl_normal_db"], ref)
    # 场入射不得优于正入射（掠入射补强 → TL 反而更低）
    fld = VIB.api_panel_tl(VIB.NoiseRequest(f=f, surface_density=m, field_incidence=True))
    assert fld["tl_field_db"] < out["tl_normal_db"]


def test_w4_a_weighting_pins_iec61672():
    """IEC 61672-1 标称 A 计权：1 kHz = 0 dB，100 Hz ≈ -19.1 dB，10 kHz ≈ -2.5 dB。"""
    out = VIB.api_levels(VIB.LevelsRequest(levels=[70.0],
                                           center_freqs=[100.0, 1000.0, 10000.0]))
    a = out["a_weighted"]
    assert abs(a[1]) < 0.05, a
    assert abs(a[0] + 19.1) < 0.4, a
    assert abs(a[2] + 2.5) < 0.4, a


def test_w4_energy_summation():
    """不相干声级能量叠加：2×70 dB = 73.01 dB。"""
    out = VIB.api_levels(VIB.LevelsRequest(levels=[70.0, 70.0]))
    assert abs(out["combined_db"] - 73.0103) < 1e-3


def test_w4_blade_rate_orders():
    """叶频 = n_blades * rpm/60；谐次线性。"""
    out = VIB.api_excitation(VIB.ExcitationRequest(n_blades=5, rpm=120.0, n_harmonics=3))
    br = out["blade_rate"]
    assert abs(br["shaft_hz"] - 2.0) < 1e-12
    assert abs(br["bpf_hz"] - 10.0) < 1e-12       # 5 叶 × 120 rpm / 60
    assert [round(x, 9) for x in br["harmonics_hz"]] == [10.0, 20.0, 30.0]


# ── W5 闸门负控制（合成夹具树）────────────────────────────────────────
def _make_tree(tmp_path, mounted: bool):
    dom = tmp_path / "backend" / "api" / "domain"
    dom.mkdir(parents=True)
    (dom / "__init__.py").write_text("")
    (dom / "wave_demo.py").write_text(textwrap.dedent('''
        from fastapi import APIRouter
        router = APIRouter(tags=["demo"])

        @router.post("/calculations/demo/thing")
        def thing() -> dict:
            return {"ok": True}
    '''))
    main = tmp_path / "backend" / "main.py"
    if mounted:
        main.write_text("from fastapi import FastAPI\n"
                        "from api.domain import wave_demo\n"
                        "app = FastAPI()\n"
                        "app.include_router(wave_demo.router, prefix='/api/v1')\n")
    else:
        main.write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    return tmp_path


def test_w5_gate_flags_unwired_module(tmp_path):
    doc = WC.census(_make_tree(tmp_path, mounted=False))
    row = {r["module"]: r for r in doc["modules"]}["wave_demo"]
    assert row["n_endpoints"] == 1
    assert row["verdict"] == WC.UNWIRED
    assert doc["summary"]["unwired_modules"] == ["wave_demo"]


def test_w5_gate_clears_after_mount(tmp_path):
    doc = WC.census(_make_tree(tmp_path, mounted=True))
    row = {r["module"]: r for r in doc["modules"]}["wave_demo"]
    assert row["mounted_in_main"] is True
    assert row["verdict"] != WC.UNWIRED
    assert doc["summary"]["unwired_modules"] == []


def test_w5_live_plane_withheld_is_not_counted_as_wired(tmp_path):
    """实况面不可信时，挂载了也只能到 MOUNTED_NOT_DEPLOYED，绝不判 LIVE。"""
    doc = WC.census(_make_tree(tmp_path, mounted=True),
                    base_url="http://127.0.0.1:9")   # 必然连不上
    row = {r["module"]: r for r in doc["modules"]}["wave_demo"]
    assert doc["summary"]["live_plane"]["status"] == "WITHHELD"
    assert row["verdict"] == WC.MOUNTED_NOT_DEPLOYED


# ── W6 baseline 闸门 ──────────────────────────────────────────────────
def test_w6_baseline_requires_reason_and_flags_drift(tmp_path):
    doc = WC.census(_make_tree(tmp_path, mounted=False))
    ok = WC.apply_baseline(doc, {"entries": [{"module": "wave_demo", "reason": "迁移中"}]})
    assert ok["fail"] is False and ok["unwired_known"] == ["wave_demo"]
    bad = WC.apply_baseline(doc, {"entries": []})
    assert bad["fail"] is True and bad["unwired_new"] == ["wave_demo"]
    stale = WC.apply_baseline(
        WC.census(_make_tree(tmp_path / "b", mounted=True)), {"entries": [{"module": "wave_demo", "reason": "x"}]})
    assert stale["baseline_stale"] == ["wave_demo"] and stale["fail"] is False


def test_w6_repo_gate_passes_with_committed_baseline():
    """仓内 baseline 覆盖全部现存未接线域 → 闸门绿；新增域则红（drift 保护）。"""
    import json
    from pathlib import Path
    root = Path(REPO)
    doc = WC.census(root, base_url=None)
    base = json.loads((root / "artifacts/wiring_census/baseline.json").read_text("utf-8"))
    diff = WC.apply_baseline(doc, base)
    assert diff["fail"] is False, diff
    assert "ship_vibration" not in doc["summary"]["unwired_modules"]