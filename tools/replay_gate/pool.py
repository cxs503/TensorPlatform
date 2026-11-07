#!/usr/bin/env python3
"""已交付声明池（replay claim pool）——复现门的抽样母体。

池里每一条都是**已交付的申报值**，来源只允许两类（禁止凭空造数）：

``repo_artifact``
    仓内证据文件里的数值（含 ``path`` + ``sha256`` + ``locator``）：阻力域取
    ``backend/data/benchmark_reference.json`` 的 Rt / Fr；结构域取
    ``backend/services/ice_abrasion.py::plate_buckling_stress`` 的交付值。
``analytic_reference``
    教科书/规范解析式给出的参考值（深水波、横摇/垂荡固有频率、板屈曲系数等），
    附 ``formula`` 与 ``citation``；这类主张的复现要求是「独立实现/独立数值方法
    在同一参数下给出同一数」。

判据（``kernel`` + ``params`` + ``tol_rel``）与申报值同存一池，池本身由
``sha256`` 落盘冻结：池被改动 → 抽样清单 sha256 变化 → 复现率不可跨版本比较。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POOL_REL = Path("artifacts/replay_gate/claims_pool.json")
BENCHMARK_REL = Path("backend/data/benchmark_reference.json")
ICE_REL = Path("backend/services/ice_abrasion.py")

DOMAINS = ("resistance", "structure", "seakeeping", "lbm", "fem", "dem")
DOMAIN_ZH = {"resistance": "阻力", "structure": "结构", "seakeeping": "耐波",
             "lbm": "LBM", "fem": "FEM", "dem": "DEM"}
G = 9.80665


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── 阻力：benchmark 正典（Rt 恒等式 / Fr 一致性 / legacy 反例） ────────────
def _resistance(root: Path) -> list[dict]:
    path = root / BENCHMARK_REL
    data = json.loads(path.read_text(encoding="utf-8"))
    sha = _sha(path)
    out: list[dict] = []
    for c in data["cases"]:
        cid = c["case_id"]
        geo = c.get("geometry") or {}
        art = {"path": str(BENCHMARK_REL), "sha256": sha, "locator": cid}
        # (a) 总阻力恒等式 Rt = Ct·½ρV²S（实验记录值 vs 由 Ct 复算）
        if c.get("experimental_rt_n") is not None and c.get("rt_status") == "DERIVED":
            out.append({
                "id": f"res-rt-{cid.lower()}",
                "domain": "resistance",
                "title": f"{cid} 总阻力恒等式 Rt=Ct·½ρV²S",
                "kernel": "rt_identity",
                "params": {"ct": c["experimental_ct"],
                           "rho": c["reference_density_kg_m3"],
                           "V": c["speed_ms"],
                           "S": geo.get("wetted_surface_m2")},
                "declared": c["experimental_rt_n"],
                "tol_rel": 0.03,
                "artifact": art,
                "provenance": {"kind": "repo_artifact", "detail": f"{BENCHMARK_REL}#{cid}.experimental_rt_n"},
            })
        # (b) Fr 一致性 Fr = V/sqrt(gL)（台账标记 DERIVED 的行）
        if c.get("froude_status") == "DERIVED" and geo.get("length_m"):
            out.append({
                "id": f"res-fr-{cid.lower()}",
                "domain": "resistance",
                "title": f"{cid} 傅汝德数一致性 Fr=V/√(gL)",
                "kernel": "froude",
                "params": {"V": c["speed_ms"], "L": geo["length_m"]},
                "declared": c["froude_number"],
                "tol_rel": 0.03,
                "artifact": art,
                "provenance": {"kind": "repo_artifact", "detail": f"{BENCHMARK_REL}#{cid}.froude_number"},
            })
        # (c) legacy 反例：历史申报 Rt 与 Ct 恒等式不能同时成立（台账已判 WITHHELD）
        legacy = c.get("legacy_values") or {}
        if (legacy.get("filed_experimental_rt_n") is not None
                and c.get("experimental_rt_n") is None and geo.get("wetted_surface_m2")):
            out.append({
                "id": f"res-legacy-rt-{cid.lower()}",
                "domain": "resistance",
                "title": f"{cid} legacy 申报 Rt 与 Ct 恒等式不自洽（台账判 WITHHELD）",
                "kernel": "rt_identity",
                "params": {"ct": c["experimental_ct"],
                           "rho": c["reference_density_kg_m3"],
                           "V": legacy.get("filed_speed_ms", c["speed_ms"]),
                           "S": geo.get("wetted_surface_m2")},
                "declared": legacy["filed_experimental_rt_n"],
                "tol_rel": 0.03,
                "artifact": art,
                "provenance": {"kind": "repo_artifact_legacy",
                               "detail": f"{BENCHMARK_REL}#{cid}.legacy_values.filed_experimental_rt_n"},
                "note": "台账自身已把它移入 legacy 并判 WITHHELD；复现门预期判真实数值漂移（不是门坏了）。",
            })
    return out


# ── 结构：板条屈曲（交付实现值 vs 独立闭式/能量商） ───────────────────────
def _load_ice_module(root: Path):
    path = root / ICE_REL
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_ice_abrasion_claim_src", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # dataclass 需要模块已登记
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


def _structure(root: Path) -> list[dict]:
    path = root / ICE_REL
    mod = _load_ice_module(root)
    if mod is None:
        return []
    sha = _sha(path)
    combos = [(0.010, 0.60), (0.012, 0.60), (0.014, 0.80), (0.016, 0.80),
              (0.018, 0.90), (0.020, 0.90), (0.022, 1.00), (0.012, 1.20),
              (0.025, 1.20)]
    out: list[dict] = []
    for t, b in combos:
        declared = float(mod.plate_buckling_stress(t, b))
        out.append({
            "id": f"str-plate-t{int(t*1000):02d}-b{int(b*100):03d}",
            "domain": "structure",
            "title": f"板条屈曲 σ_cr(t={t*1000:.0f} mm, b={b*1000:.0f} mm)",
            "kernel": "plate_buckling_sigma_cr",
            "params": {"t": t, "b": b, "e": float(mod.STEEL_E), "nu": float(mod.STEEL_NU)},
            "declared": declared,
            "tol_rel": 0.03,
            "artifact": {"path": str(ICE_REL), "sha256": sha, "locator": "plate_buckling_stress"},
            "provenance": {"kind": "repo_artifact",
                           "detail": f"{ICE_REL}::plate_buckling_stress(t={t},b={b})"},
        })
    for a_over_b in (1.0, 2.0, 3.0):
        b = 0.8
        out.append({
            "id": f"str-rr-k-a{int(a_over_b*10):02d}",
            "domain": "structure",
            "title": f"长板屈曲系数 k(a/b={a_over_b}) 能量商最小值=4",
            "kernel": "rayleigh_ritz_k",
            "params": {"a": a_over_b * b, "b": b, "m": 0},
            "declared": 4.0,
            "tol_rel": 0.03,
            "artifact": None,
            "provenance": {"kind": "analytic_reference",
                           "detail": "k=min_m (m·b/a+a/(m·b))²；a/b 为整数时最小值恰为 4（Timoshenko & Gere）"},
        })
    return out


# ── 耐波：解析参考 + RK4 数值积分复现 ────────────────────────────────────
def _seakeeping() -> list[dict]:
    out: list[dict] = []
    for T in (6.0, 8.0, 10.0, 12.0):
        out.append({
            "id": f"sea-wavelen-t{int(T)}",
            "domain": "seakeeping",
            "title": f"深水波长 λ(T={T:.0f}s)",
            "kernel": "wave_length",
            "params": {"T": T},
            "declared": G * T * T / (2 * math.pi),
            "tol_rel": 0.03,
            "artifact": None,
            "provenance": {"kind": "analytic_reference", "detail": "λ=gT²/(2π)（线性深水色散关系）"},
        })
    for GM, kr in ((0.35, 3.20), (0.80, 4.00), (1.50, 5.20), (2.40, 6.00)):
        out.append({
            "id": f"sea-roll-gm{int(GM*100):03d}",
            "domain": "seakeeping",
            "title": f"横摇固有周期 T(GM={GM}, k_r={kr})",
            "kernel": "roll_period",
            "params": {"GM": GM, "k_r": kr},
            "declared": 2 * math.pi * kr / math.sqrt(G * GM),
            "tol_rel": 0.03,
            "artifact": None,
            "provenance": {"kind": "analytic_reference", "detail": "T=2πk_r/√(g·GM)（线性横摇）"},
        })
    for GM, kr, zeta in ((0.80, 4.00, 0.03), (1.50, 5.20, 0.06), (2.40, 6.00, 0.10), (0.35, 3.20, 0.05)):
        wn = math.sqrt(G * GM) / kr
        out.append({
            "id": f"sea-rolldecay-gm{int(GM*100):03d}-z{int(zeta*100):02d}",
            "domain": "seakeeping",
            "title": f"横摇阻尼周期 T_d(GM={GM}, ζ={zeta}) RK4 复现",
            "kernel": "roll_decay_period_rk4",
            "params": {"GM": GM, "k_r": kr, "zeta": zeta, "dt": 0.005, "t_end": 120.0},
            "declared": 2 * math.pi / wn / math.sqrt(1 - zeta * zeta),
            "tol_rel": 0.03,
            "artifact": None,
            "provenance": {"kind": "analytic_reference",
                           "detail": "T_d=T_n/√(1-ζ²)，T_n=2πk_r/√(g·GM)；判据=RK4 直接积分衰减方程"},
        })
    return out


# ── LBM：D2Q9 真迭代 vs 通道解析解 ──────────────────────────────────────
def _lbm() -> list[dict]:
    sets = [(19, 0.10, 6.0e-5), (23, 0.12, 8.0e-5), (15, 0.08, 4.0e-5), (29, 0.20, 1.0e-4)]
    out: list[dict] = []
    for H, nu, F in sets:
        out.append({
            "id": f"lbm-poiseuille-h{H:02d}-nu{int(nu*100):03d}",
            "domain": "lbm",
            "title": f"D2Q9 通道 Poiseuille u_max(H={H}, ν={nu})",
            "kernel": "lbm_channel_umax",
            "params": {"H": H, "nu": nu, "force": F, "steps": 30000, "tol": 1e-10},
            "declared": F * H * H / (8 * nu),
            "tol_rel": 0.03,
            "artifact": None,
            "provenance": {"kind": "analytic_reference",
                           "detail": "u_max=F·H²/(8ν)（Poiseuille 解析解，格子单位）"},
        })
    for H, nu, F in sets[:3]:
        out.append({
            "id": f"lbm-mass-h{H:02d}-nu{int(nu*100):03d}",
            "domain": "lbm",
            "title": f"D2Q9 质量守恒 Σρ(H={H})",
            "kernel": "lbm_mass_total",
            "params": {"H": H, "nu": nu, "force": F, "steps": 400},
            "declared": float(H),
            "tol_rel": 0.03,
            "artifact": None,
            "provenance": {"kind": "analytic_reference", "detail": "无质量源 → Σρ 恒等于初始 H（每格 1.0）"},
        })
    for nu, k, t in ((0.02, 1.0, 5.0), (0.05, 2.0, 2.0), (0.10, 1.5, 3.0), (0.01, 0.5, 10.0)):
        out.append({
            "id": f"lbm-shear-nu{int(nu*100):03d}-k{int(k*10):02d}",
            "domain": "lbm",
            "title": f"剪切波衰减 u/u0(ν={nu}, k={k}, t={t})",
            "kernel": "shear_wave_decay",
            "params": {"nu": nu, "k": k, "t": t},
            "declared": math.exp(-nu * k * k * t),
            "tol_rel": 0.03,
            "artifact": None,
            "provenance": {"kind": "analytic_reference", "detail": "u=u0·exp(-νk²t)（Stokes 剪切波）"},
        })
    return out


# ── FEM：Hermite 梁 / 杆 / 等参元 vs 解析 ───────────────────────────────
def _fem() -> list[dict]:
    out: list[dict] = []
    for P, L, E, I in ((1.0e4, 4.0, 2.1e11, 3.0e-5), (5.0e4, 6.0, 2.1e11, 8.0e-5),
                       (2.0e4, 2.5, 7.0e10, 1.2e-5), (8.0e4, 8.0, 2.06e11, 2.0e-4)):
        out.append({
            "id": f"fem-beam-p{int(P/1e4):02d}-l{int(L*10):02d}",
            "domain": "fem",
            "title": f"Hermite 悬臂梁端部挠度(P={P:.1e} N, L={L} m)",
            "kernel": "fem_beam_tip",
            "params": {"P": P, "L": L, "E": E, "I": I, "n_elem": 4},
            "declared": P * L ** 3 / (3 * E * I),
            "tol_rel": 0.03,
            "artifact": None,
            "provenance": {"kind": "analytic_reference", "detail": "δ=PL³/(3EI)（Euler-Bernoulli）"},
        })
    for q, EA, L in ((2.0e4, 2.1e9, 3.0), (5.0e4, 1.5e9, 5.0), (1.0e4, 8.0e8, 2.0), (3.0e4, 3.0e9, 7.0)):
        out.append({
            "id": f"fem-bar-q{int(q/1e4):02d}-l{int(L):02d}",
            "domain": "fem",
            "title": f"线性杆单元均布轴载端部位移(q={q:.0e} N/m, L={L} m)",
            "kernel": "fem_bar_tip_uniform",
            "params": {"q": q, "EA": EA, "L": L, "n_elem": 8},
            "declared": q * L * L / (2 * EA),
            "tol_rel": 0.03,
            "artifact": None,
            "provenance": {"kind": "analytic_reference", "detail": "u= qL²/(2EA)（轴向均布荷载）"},
        })
    quads = [([0, 1, 1.3, 0.2], [0, 0.1, 1.1, 1.0]), ([0, 2, 2.4, 0.4], [0, 0.2, 2.2, 2.0]),
             ([0, 0.5, 0.6, 0.1], [0, 0.05, 3.0, 2.95]), ([0, 1.5, 1.0, -0.5], [0, 0.0, 2.0, 2.0])]
    for i, (xs, ys) in enumerate(quads):
        # 多边形面积（鞋带公式）作为解析基准
        area = 0.0
        n = len(xs)
        for a in range(n):
            b = (a + 1) % n
            area += xs[a] * ys[b] - xs[b] * ys[a]
        out.append({
            "id": f"fem-quad-area-{i}",
            "domain": "fem",
            "title": f"双线性等参元面积(节点={list(zip(xs, ys))})",
            "kernel": "fem_quad_area",
            "params": {"xs": xs, "ys": ys},
            "declared": abs(area) / 2.0,
            "tol_rel": 0.03,
            "artifact": None,
            "provenance": {"kind": "analytic_reference", "detail": "鞋带公式多边形面积 = ∫det J dξdη"},
        })
    return out


# ── DEM：Hertz 接触 / 对心碰撞 / 弹道积分 ───────────────────────────────
def _dem() -> list[dict]:
    out: list[dict] = []
    for E, nu, R, delta in ((7.0e10, 0.22, 0.02, 1.0e-5), (2.0e11, 0.30, 0.05, 2.0e-5),
                            (3.0e10, 0.25, 0.10, 5.0e-6), (2.06e11, 0.30, 0.01, 3.0e-5)):
        e_star = E / (2 * (1 - nu * nu))
        out.append({
            "id": f"dem-hertz-r{int(R*1000):03d}-b{int(delta*1e6):02d}",
            "domain": "dem",
            "title": f"Hertz 接触力(R={R} m, δ={delta:.0e} m)",
            "kernel": "dem_hertz_force",
            "params": {"E": E, "nu": nu, "R": R, "delta": delta},
            "declared": 4.0 / 3.0 * e_star * math.sqrt(R) * delta ** 1.5,
            "tol_rel": 0.03,
            "artifact": None,
            "provenance": {"kind": "analytic_reference", "detail": "F=(4/3)E*√R·δ^{3/2}（Hertz 1882）"},
        })
    for m1, v1, m2, v2, e in ((2.0, 1.0, 5.0, 0.0, 1.0), (1.0, 2.0, 1.0, -1.0, 0.8),
                              (3.0, 0.5, 1.0, 0.0, 0.5), (0.5, 1.5, 2.0, 0.5, 1.0)):
        out.append({
            "id": f"dem-coll-m{int(m1*10):02d}-v{int(v1*10):02d}-e{int(e*10):02d}",
            "domain": "dem",
            "title": f"对心碰撞后 v1'(m1={m1}, v1={v1}, m2={m2}, v2={v2}, e={e})",
            "kernel": "dem_collision_v1_after",
            "params": {"m1": m1, "v1": v1, "m2": m2, "v2": v2, "e": e},
            "declared": ((m1 - e * m2) * v1 + (1 + e) * m2 * v2) / (m1 + m2),
            "tol_rel": 0.03,
            "artifact": None,
            "provenance": {"kind": "analytic_reference", "detail": "动量守恒 + 恢复系数 e 的一维解"},
        })
    for v0, t in ((0.5, 1.0), (2.0, 0.25), (-1.0, 0.75), (0.0, 2.0)):
        out.append({
            "id": f"dem-ball-v{int(v0*100):04d}-t{int(t*100):03d}",
            "domain": "dem",
            "title": f"自由落体位移(v0={v0}, t={t})",
            "kernel": "dem_ballistic_drop",
            "params": {"v0": v0, "t": t},
            "declared": v0 * t + 0.5 * G * t * t,
            "tol_rel": 0.03,
            "artifact": None,
            "provenance": {"kind": "analytic_reference", "detail": "s=v0t+½gt²（时间积分器基准）"},
        })
    return out


def build_pool(root: str | Path | None = None) -> dict:
    root = Path(root or ROOT)
    claims = (_resistance(root) + _structure(root) + _seakeeping() + _lbm() + _fem() + _dem())
    for c in claims:
        c.setdefault("requires", [])
        c.setdefault("seed", 0)
        c.setdefault("stochastic", False)
        c.setdefault("note", "")
        if c.get("params"):
            for v in c["params"].values():
                if isinstance(v, float) and not math.isfinite(v):
                    raise ValueError(f"claim {c['id']} 参数非有限: {c['params']}")
    ids = [c["id"] for c in claims]
    if len(ids) != len(set(ids)):
        raise ValueError("claim id 重复")
    per_domain = {d: sum(1 for c in claims if c["domain"] == d) for d in DOMAINS}
    body = json.dumps(claims, ensure_ascii=False, sort_keys=True).encode()
    return {
        "pool_version": "replay-claim-pool/1.0",
        "built_at": _now(),
        "n": len(claims),
        "per_domain": per_domain,
        "sha256": hashlib.sha256(body).hexdigest(),
        "domains": list(DOMAINS),
        "claims": claims,
    }


def write_pool(pool: dict, path: str | Path | None = None) -> Path:
    out = Path(path or (ROOT / POOL_REL))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pool, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    pool = build_pool()
    p = write_pool(pool)
    print(f"claims={pool['n']} per_domain={pool['per_domain']} sha256={pool['sha256'][:12]}")
    print(f"written {p}")