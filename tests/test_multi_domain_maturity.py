"""多域前沿集成成熟化 —— pytest 验收（>=20 例）。

覆盖判据 (a)–(f)：
- (a) 波浪操纵-LES 耦合：LES C_D vs 解析，误差 <15%；
- (b) DEM-波浪耦合：颗粒响应 vs 闭式解，误差 <20%；
- (c) 跨域接口契约：守恒残差 <1e-8，版本兼容；
- (d) 端到端：守恒误差 <5%；
- (e) 报告（由 make_report 生成，此处校验 JSON 判据字段）；
- (f) API >=3 端点、非法输入 422。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from domains.multi_domain_maturity.wave_field import (
    AiryWave,
    RHO_WATER,
    dispersion,
    ship_surge_exact,
    ship_surge_terminal_velocity,
    ship_surge_transient,
)
from domains.multi_domain_maturity.les import (
    LESChannel1D,
    poiseuille_C_D,
    poiseuille_profile,
)
from domains.multi_domain_maturity.dem import (
    PolyParticle,
    Tetrahedron,
    analytic_drag_response,
    tetra_contact,
)
from domains.multi_domain_maturity.interface import (
    FieldGrid,
    InterfaceContract,
    check_contracts,
    conservation_residual,
    exchange_field,
    refine_grid,
)
from domains.multi_domain_maturity.coupling import E2EConfig, run_e2e


# ---------- 波浪场 ----------

def test_dispersion_deep_water():
    # 深水极限 tanh(kh)->1：omega = sqrt(g k)
    k, h = 0.5, 1000.0
    assert dispersion(k, h) == pytest.approx(math.sqrt(9.81 * k), rel=1e-6)


def test_dispersion_shallow_water():
    # 浅水极限 tanh(kh)->1 但 kh 小：omega ≈ sqrt(g k) * sqrt(tanh(kh))
    k, h = 0.01, 10.0
    assert dispersion(k, h) == pytest.approx(math.sqrt(9.81 * k * math.tanh(k * h)), rel=1e-12)


def test_airy_wave_properties():
    w = AiryWave(a=0.5, L=20.0, h=10.0)
    assert w.k == pytest.approx(2 * math.pi / 20.0)
    assert w.omega == pytest.approx(math.sqrt(9.81 * w.k * math.tanh(w.k * 10.0)))
    assert w.T == pytest.approx(2 * math.pi / w.omega)
    assert w.c == pytest.approx(w.omega / w.k)


def test_airy_wave_invalid():
    with pytest.raises(ValueError):
        AiryWave(a=-0.5, L=20.0, h=10.0)
    with pytest.raises(ValueError):
        AiryWave(a=0.5, L=0.0, h=10.0)


def test_airy_velocity_bounded():
    w = AiryWave(a=0.5, L=20.0, h=10.0)
    u, wv = w.velocity(0.0, -1.0, 0.0)
    # 表面轨道速度幅值 a*omega
    assert abs(u) <= w.a * w.omega * 1.01
    assert abs(wv) <= w.a * w.omega * 1.01


def test_stokes_drift_positive_and_decays():
    w = AiryWave(a=0.5, L=20.0, h=10.0)
    us_surf = w.stokes_drift(0.0)
    us_deep = w.stokes_drift(-5.0)
    assert us_surf > 0
    assert us_deep < us_surf  # 随深度衰减


def test_drift_force_scales_with_cd():
    w = AiryWave(a=0.5, L=20.0, h=10.0)
    f1 = w.drift_force(1.0, 1.0, 0.0)
    f2 = w.drift_force(2.0, 1.0, 0.0)
    assert f2 == pytest.approx(2.0 * f1)


def test_ship_terminal_velocity():
    F, C_D, A = 400.0, 0.8, 50.0
    v_t = ship_surge_terminal_velocity(F, C_D, A)
    assert v_t == pytest.approx(math.sqrt(2 * F / (RHO_WATER * C_D * A)))


def test_ship_surge_transient_converges_to_terminal():
    F, C_D, A, m = 400.0, 0.8, 50.0, 5.0e6
    v_t = ship_surge_terminal_velocity(F, C_D, A)
    tau = m * v_t / F
    traj = ship_surge_transient(F, C_D, A, m, 5 * tau, 0.01 * tau)
    v_end = traj[-1][1]
    assert v_end == pytest.approx(v_t, rel=0.01)


def test_ship_surge_exact_matches_transient():
    F, C_D, A, m = 400.0, 0.8, 50.0, 5.0e6
    v_t = ship_surge_terminal_velocity(F, C_D, A)
    tau = m * v_t / F
    t = 2.0 * tau
    v_exact = ship_surge_exact(t, F, C_D, A, m)
    traj = ship_surge_transient(F, C_D, A, m, t, 0.001 * tau)
    assert traj[-1][1] == pytest.approx(v_exact, rel=1e-4)


# ---------- LES ----------

def test_poisson_solve_bounded_zero_mean():
    from domains.multi_domain_maturity.les import _poisson_solve

    nx, ny, dx, dy = 16, 8, 1.0, 1.0
    X, Y = np.meshgrid(np.arange(nx) * dx, np.arange(ny) * dy, indexing="ij")
    rhs = np.cos(2 * np.pi * X / (nx * dx)) * np.cos(np.pi * Y / (ny * dy))
    p = _poisson_solve(rhs, dx, dy)
    assert np.isfinite(p).all()
    assert abs(p.mean()) < 1e-12
    # 解的量级与 rhs 一致（∇²p = rhs ⇒ p ~ rhs / k^2）
    assert p.max() < 10.0 * rhs.max()


def test_les1d_poiseuille_profile():
    nu, U_mean, Ly = 1.0 / 100.0, 1.0, 1.0
    f_x = 12.0 * nu * U_mean / Ly**2
    les = LESChannel1D(Ly=Ly, ny=128, nu=nu, U_mean=U_mean, body_force_x=f_x)
    les.run(100.0, 0.002)
    ana = poiseuille_profile(les.y, Ly, U_mean)
    rel = np.abs(les.u - ana) / ana.max()
    assert rel.max() < 0.15  # 判据 a 的 15% 线


def test_les1d_drag_coefficient():
    nu, U_mean, Ly = 1.0 / 100.0, 1.0, 1.0
    f_x = 12.0 * nu * U_mean / Ly**2
    les = LESChannel1D(Ly=Ly, ny=128, nu=nu, U_mean=U_mean, body_force_x=f_x)
    les.run(100.0, 0.002)
    U_num = les.mean_velocity()
    Re_num = U_num * Ly / nu
    C_D_les = les.drag_coefficient()
    C_D_ana = poiseuille_C_D(Re_num)
    err = abs(C_D_les - C_D_ana) / C_D_ana
    assert err < 0.15  # 判据 a


def test_les1d_smagorinsky_nonnegative():
    nu, U_mean, Ly = 1.0 / 100.0, 1.0, 1.0
    les = LESChannel1D(Ly=Ly, ny=64, nu=nu, U_mean=U_mean, body_force_x=0.12)
    les.run(5.0, 0.002)
    assert (les.nu_t >= 0).all()


def test_les1d_mean_velocity_positive():
    nu, U_mean, Ly = 1.0 / 100.0, 1.0, 1.0
    f_x = 12.0 * nu * U_mean / Ly**2
    les = LESChannel1D(Ly=Ly, ny=64, nu=nu, U_mean=U_mean, body_force_x=f_x)
    les.run(100.0, 0.002)
    assert les.mean_velocity() > 0.5


def test_poiseuille_C_D_formula():
    assert poiseuille_C_D(100.0) == pytest.approx(0.12)
    with pytest.raises(ValueError):
        poiseuille_C_D(0.0)


# ---------- DEM ----------

def _unit_tetra(s: float = 1.0) -> np.ndarray:
    return np.array([[0, 0, 0], [s, 0, 0], [0, s, 0], [0, 0, s]], dtype=float)


def test_tetra_volume():
    t = Tetrahedron(_unit_tetra(1.0))
    assert t.volume == pytest.approx(1.0 / 6.0)


def test_tetra_degenerate_raises():
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0]], dtype=float)
    with pytest.raises(ValueError):
        Tetrahedron(v)


def test_tetra_face_normals_outward():
    t = Tetrahedron(_unit_tetra(1.0))
    normals = t.face_normals()
    assert normals.shape == (4, 3)
    for n in normals:
        assert np.linalg.norm(n) == pytest.approx(1.0)
    # 朝外：每个法向与 (质心 - 面心) 同向
    v = t.verts
    idx = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]
    for (i, j, k), n in zip(idx, normals):
        c = (v[i] + v[j] + v[k]) / 3.0
        # 朝外：法向与 (面心->质心) 反向
        assert np.dot(n, t.centroid - c) < 0


def test_tetra_inertia_tensor_positive_definite():
    t = Tetrahedron(_unit_tetra(1.0))
    I = t.inertia_tensor(1.0)
    eig = np.linalg.eigvalsh(I)
    assert (eig > 0).all()


def test_analytic_drag_response_zero_initial():
    v0 = analytic_drag_response(0.0, 1.0, 1.0, 1.0, 0.0)
    assert v0 == pytest.approx(0.0, abs=1e-12)


def test_analytic_drag_response_steady_amplitude():
    # 稳态幅值 U0/sqrt(1+omega^2 tau^2)
    U0, omega, tau = 1.0, 1.0, 1.0
    # 取大 t，瞬态衰减
    v = analytic_drag_response(50.0, U0, omega, tau, 0.0)
    # 稳态解 U0 cos(omega t)/D 在 t=50 的值
    D = 1.0 + (omega * tau) ** 2
    v_steady = U0 * (math.cos(omega * 50.0) + omega * tau * math.sin(omega * 50.0)) / D
    assert v == pytest.approx(v_steady, rel=1e-3)


def test_dem_particle_matches_analytic():
    # 中性浮力 + 线性拖曳 + 振荡流
    wave_omega, U0 = 1.75, 0.64
    s = 0.5
    V = s**3 / 6.0
    mass = RHO_WATER * V
    b = 200.0
    p = PolyParticle(_unit_tetra(s), mass, drag_coeff=b, rho_body=RHO_WATER)
    p.r = np.array([0.0, 0.0, -1.0])
    tau = mass / b
    phi = -math.pi / 2
    n, dt = 400, 0.01
    for i in range(n):
        t = i * dt
        u_f = U0 * math.cos(wave_omega * t - phi)
        p.step(dt, np.array([u_f, 0.0, 0.0]), free_surface=0.0)
    v_end = p.v[0]
    v_ana = analytic_drag_response(n * dt, U0, wave_omega, tau, phi)
    err = abs(v_end - v_ana) / max(abs(v_ana), 1e-12)
    assert err < 0.20  # 判据 b


def test_tetra_contact_touching():
    a = Tetrahedron(_unit_tetra(1.0))
    # b 与 a 共享一个面（接触）
    b = Tetrahedron(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=float))
    contact, gap = tetra_contact(a, b)
    assert contact
    assert gap <= 1e-6


def test_tetra_contact_separated():
    a = Tetrahedron(_unit_tetra(1.0))
    b = Tetrahedron(_unit_tetra(1.0) + np.array([0, 0, 5.0]))
    contact, gap = tetra_contact(a, b)
    assert not contact
    assert gap > 1.0


# ---------- 接口契约 ----------

def _cos_field(nx=16, ny=8, dx=1.0, dy=1.0) -> FieldGrid:
    X, _ = np.meshgrid(np.arange(nx) * dx, np.arange(ny) * dy, indexing="ij")
    vals = 1.0 + 0.5 * np.cos(2.0 * math.pi * X / (nx * dx))
    return FieldGrid(name="f", shape=(nx, ny), dx=dx, dy=dy, values=vals)


def test_refine_grid_shape():
    g = _cos_field()
    gf = refine_grid(g, 4)
    assert gf.shape == (64, 32)
    assert gf.dx == pytest.approx(g.dx / 4)


def test_refine_grid_invalid_ratio():
    g = _cos_field()
    with pytest.raises(ValueError):
        refine_grid(g, 0)
    with pytest.raises(ValueError):
        refine_grid(g, 1.5)


def test_conservation_residual_below_1e_8():
    g = _cos_field()
    gf = refine_grid(g, 4)
    res = conservation_residual(g, gf)
    assert res < 1e-8  # 判据 c


def test_exchange_field_with_contract():
    g = _cos_field()
    contract = InterfaceContract(
        field_name="f", units="m/s", frame="inertial",
        shape=(16, 8), dtype="float64", sign_convention="positive_up",
    )
    gf, res = exchange_field(g, 4, contract)
    assert res < 1e-8
    assert gf.shape == (64, 32)


def test_exchange_field_contract_shape_mismatch():
    g = _cos_field()
    contract = InterfaceContract(
        field_name="f", units="m/s", frame="inertial",
        shape=(99, 8), dtype="float64", sign_convention="positive_up",
    )
    with pytest.raises(ValueError):
        exchange_field(g, 4, contract)


def test_contract_version_compatible_same_major():
    a = InterfaceContract("f", "m/s", "inertial", (16, 8), "float64", "pos", major=1, minor=0)
    b = InterfaceContract("f", "m/s", "inertial", (16, 8), "float64", "pos", major=1, minor=2)
    ok, msg = check_contracts(a, b)
    assert ok


def test_contract_version_incompatible_major():
    a = InterfaceContract("f", "m/s", "inertial", (16, 8), "float64", "pos", major=1, minor=0)
    b = InterfaceContract("f", "m/s", "inertial", (16, 8), "float64", "pos", major=2, minor=0)
    ok, msg = check_contracts(a, b)
    assert not ok
    assert "major" in msg


def test_contract_units_mismatch():
    a = InterfaceContract("f", "m/s", "inertial", (16, 8), "float64", "pos")
    b = InterfaceContract("f", "ft/s", "inertial", (16, 8), "float64", "pos")
    ok, msg = check_contracts(a, b)
    assert not ok


# ---------- 端到端 ----------

def test_e2e_verdict_pass():
    r = run_e2e(E2EConfig())
    assert r.verdict == "pass"


def test_e2e_les_error_below_15pct():
    r = run_e2e(E2EConfig())
    assert r.les_err < 0.15  # 判据 a


def test_e2e_dem_error_below_20pct():
    r = run_e2e(E2EConfig())
    assert r.dem_err < 0.20  # 判据 b


def test_e2e_interface_residual():
    r = run_e2e(E2EConfig())
    assert r.interface_residual < 1e-8  # 判据 c
    assert r.interface_ok


def test_e2e_max_conservation_error_below_5pct():
    r = run_e2e(E2EConfig())
    assert r.max_conservation_err < 0.05  # 判据 d


# ---------- API ----------

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from domains.multi_domain_maturity.api import app

    return TestClient(app)


def test_api_health(client):
    r = client.get("/api/v1/multi-domain/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_api_wave_les(client):
    r = client.post(
        "/api/v1/multi-domain/wave-les",
        json={"wave_a": 0.5, "wave_L": 20.0, "water_h": 10.0,
              "ship_CD": 0.8, "ship_A": 50.0, "les_Re": 100.0,
              "les_ny": 64, "les_t_end": 100.0, "les_dt": 0.002},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["les_error_lt_15pct"] is True


def test_api_dem_wave(client):
    r = client.post(
        "/api/v1/multi-domain/dem-wave",
        json={"wave_a": 0.5, "wave_L": 20.0, "water_h": 10.0,
              "particle_size": 0.5, "mass": 21.4, "drag_coeff": 200.0,
              "n_steps": 400, "dt": 0.01},
    )
    assert r.status_code == 200
    assert r.json()["error_lt_20pct"] is True


def test_api_interface(client):
    r = client.post(
        "/api/v1/multi-domain/interface",
        json={"nx": 16, "ny": 8, "ratio": 4,
              "major_a": 1, "minor_a": 0, "major_b": 1, "minor_b": 2},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["residual_lt_1e-8"] is True
    assert body["compatible"] is True


def test_api_e2e(client):
    r = client.post(
        "/api/v1/multi-domain/e2e",
        json={"wave_a": 0.5, "wave_L": 20.0, "water_h": 10.0,
              "ship_mass": 5.0e6, "ship_CD": 0.8, "ship_A": 50.0,
              "les_Re": 100.0, "interface_ratio": 4},
    )
    assert r.status_code == 200
    assert r.json()["verdict"] == "pass"


def test_api_invalid_input_422(client):
    # 非法输入：wave_a <= 0
    r = client.post(
        "/api/v1/multi-domain/wave-les",
        json={"wave_a": -0.5, "wave_L": 20.0, "water_h": 10.0,
              "ship_CD": 0.8, "ship_A": 50.0, "les_Re": 100.0},
    )
    assert r.status_code == 422


def test_api_invalid_input_missing_field(client):
    r = client.post("/api/v1/multi-domain/dem-wave", json={"wave_a": 0.5})
    assert r.status_code == 422


def test_api_invalid_type_422(client):
    r = client.post(
        "/api/v1/multi-domain/interface",
        json={"nx": "not_an_int", "ny": 8, "ratio": 4},
    )
    assert r.status_code == 422
