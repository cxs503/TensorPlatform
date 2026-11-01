"""耐波性三端点 D5 修复的验收测试（A1~A6，机器判据）。

对应 mission 1547 验收条款：

A1 端点消费 wave_frequency 与 heading（12 组配对最大相对差 > 1e-2）
A2 垂荡 / 纵摇不恒等（逐点差 > 1e-3）
A3 顶浪/横浪 横摇峰值比 < 0.2
A4 T33 高于无附加质量下界；T44 反解 k_xx∈[0.30B,0.42B]；T33 与自报 A33 自洽（<5%）
A5 切片理论无常数段/下限截断；A33 单位 kg 且 A33/m∈[0.3,2]
A6 /motion 与 /strip-theory 自然周期互差 < 15%，且与解析不确定带相容

测试通过 ``backend/api`` 下的模块直接装载（不触发平台的 DB 依赖），
并且对 HTTP 面（TestClient）做同样的判据，确保端点契约与内核一致。
"""
from __future__ import annotations

import math
import pathlib
import sys

import pytest

_API_DIR = pathlib.Path(__file__).resolve().parents[1] / "backend" / "api"
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

import seakeeping_physics as sp  # noqa: E402

G = 9.80665
RHO = 1025.0

SHIPS = {
    "KVLCC2": dict(L=320.0, B=58.0, T=20.8, displacement=312622.0, Cb=0.8098,
                   Cwp=0.86, KG=18.6, GM=9.0, speed=15.0),
    "S175": dict(L=175.0, B=25.4, T=9.5, displacement=24400.0, Cb=0.572,
                 Cwp=0.76, KG=8.0, GM=1.5, speed=15.0),
    "BARGE60": dict(L=60.0, B=12.0, T=4.0, displacement=2200.0, Cb=0.90,
                    Cwp=0.94, KG=3.0, GM=2.0, speed=0.0),
}


def _body(s, w=0.6, heading=180.0, speed=None):
    return dict(L=s["L"], B=s["B"], T=s["T"], D=1.5 * s["T"],
                displacement=s["displacement"], Cb=s["Cb"], KG=s["KG"], GM=s["GM"],
                speed=(s["speed"] if speed is None else speed),
                wave_frequency=w, heading=heading)


def _body_strip(s, w=0.6, heading=180.0):
    return dict(L=s["L"], B=s["B"], T=s["T"], D=1.5 * s["T"],
                displacement=s["displacement"], Cb=s["Cb"], Cm=0.95, Cwp=s["Cwp"],
                KG=s["KG"], GM=s["GM"], speed=s["speed"], wave_frequency=w,
                wave_amplitude=1.0, heading=heading, water_density=RHO)


@pytest.fixture(scope="module")
def client():
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import seakeeping_physics_app as appmod
    assert fastapi  # 使用导入到的 FastAPI 应用
    return TestClient(appmod.app)


def _rao(client, body):
    r = client.post("/api/v1/seakeeping/rao", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _motion(client, body):
    r = client.post("/api/v1/seakeeping/motion", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _strip(client, body):
    r = client.post("/api/v1/seakeeping-advanced/strip-theory", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _rel(a, b):
    return max(abs(x - y) / max(abs(x), abs(y), 1e-12) for x, y in zip(a, b))


# --------------------------------------------------------------------- A1


def test_a1_rao_consumes_wave_frequency_and_heading(client):
    """A1：12 组配对中，非解析不变量的配对最大相对差必须 > 1e-2。"""
    diffs = {}
    for name, s in SHIPS.items():
        base = _rao(client, _body(s, 0.6, 180.0))
        variants = {
            "wf_high": _body(s, 2.4, 180.0),
            "head0": _body(s, 0.6, 0.0),
            "beam90": _body(s, 0.6, 90.0),
            "stern135": _body(s, 0.6, 135.0),
        }
        for key, body in variants.items():
            other = _rao(client, body)
            r = _rel(base["heave_rao"], other["heave_rao"])
            if key == "head0" and abs(s["speed"]) < 1e-9:
                # 零速前后对称船体：β=0 与 β=180 激励解析恒等（不变量，不含信息量）
                assert r < 1e-12
                diffs[f"{name}:{key}"] = "n/a(symmetry invariant)"
                continue
            assert base["frequencies"] and other["frequencies"]
            diffs[f"{name}:{key}"] = r
            assert r > 1e-2, f"{name}:{key} 相对差 {r:.3e} 过小 —— 输入未被消费"
    assert len(diffs) == 12


def test_a1_wave_frequency_shifts_grid_centre(client):
    """A1 补充：wave_frequency 输入必须体现在返回的频率栅格/遭遇频率上。"""
    s = SHIPS["KVLCC2"]
    a = _rao(client, _body(s, 0.40, 180.0))
    b = _rao(client, _body(s, 1.20, 180.0))
    assert max(a["frequencies"]) != max(b["frequencies"]) or \
        set(a["frequencies"]) != set(b["frequencies"])
    assert a["encounter_frequencies"] != b["encounter_frequencies"]


# --------------------------------------------------------------------- A2


def test_a2_heave_and_pitch_are_distinguishable(client):
    """A2：垂荡 (m/m) 与纵摇 (rad/(ka)) 逐点不恒等，最大相对差 > 1e-3。"""
    for name, s in SHIPS.items():
        d = _rao(client, _body(s))
        r = _rel(d["heave_rao"], d["pitch_rao"])
        assert r > 1e-3, f"{name} heave/pitch 最大相对差仅 {r:.3e}"
        n_diff = sum(1 for h, p in zip(d["heave_rao"], d["pitch_rao"])
                     if abs(h - p) / max(h, p, 1e-12) > 1e-3)
        assert n_diff > 0.5 * len(d["heave_rao"])


# --------------------------------------------------------------------- A3


def test_a3_head_seas_roll_much_smaller_than_beam(client):
    """A3：顶浪/横浪 横摇峰值比 < 0.2（对称性：顶浪无横摇激励）。"""
    for name, s in SHIPS.items():
        head = _rao(client, _body(s, 0.6, 180.0))["roll_rao"]
        beam = _rao(client, _body(s, 0.6, 90.0))["roll_rao"]
        assert max(beam) > 1e-3, f"{name} 横浪横摇 RAO 全零，判据失效"
        ratio = max(head) / max(beam)
        assert ratio < 0.2, f"{name} 顶浪/横浪 横摇峰值比 {ratio:.3f}"


# --------------------------------------------------------------------- A4


def test_a4_natural_periods_are_physical(client):
    """A4：T33 高于无附加质量下界；T44 反解 k_xx∈[0.30B,0.42B]；T33 与 A33 自洽 <5%。"""
    for name, s in SHIPS.items():
        mot = _motion(client, _body(s))
        strip = _strip(client, _body_strip(s))
        t33 = mot["natural_periods"]["heave"]
        t44 = mot["natural_periods"]["roll"]

        mass = s["displacement"] * 1000.0
        aw = sp.cwp_from_cb(s["Cb"]) * s["L"] * s["B"]
        t_min = 2.0 * math.pi * math.sqrt(mass / (RHO * G * aw))
        assert t33 > t_min, f"{name} T33={t33:.3f}s 低于下界 {t_min:.3f}s（等价 A33=0）"

        k_xx = t44 * math.sqrt(G * s["GM"]) / (2.0 * math.pi)
        assert 0.30 * s["B"] <= k_xx <= 0.42 * s["B"], \
            f"{name} 反解 k_xx={k_xx:.3f}m = {k_xx / s['B']:.3f}B 越出 0.30B~0.42B"

        a33 = strip["added_mass_heave"]                      # kg
        t_from_a33 = 2.0 * math.pi * math.sqrt((mass + a33) / (RHO * G * aw))
        err = abs(t_from_a33 - strip["natural_period_heave"]) / strip["natural_period_heave"]
        assert err < 0.05, f"{name} 自报 T33 与自报 A33 不一致 {err * 100:.2f}%"


# --------------------------------------------------------------------- A5


def test_a5_strip_has_no_floor_or_constant_segments(client):
    """A5：切片理论 RAO 无下限截断/常数段；A33 单位 kg 且 A33/m∈[0.3,2]。"""
    for name, s in SHIPS.items():
        d = _strip(client, _body_strip(s, 0.6))
        d_beam = _strip(client, _body_strip(s, 0.6, heading=90.0))   # 横摇须在横浪下考察
        for key, src in (("heave_rao", d), ("pitch_rao", d), ("roll_rao", d_beam)):
            seq = src[key]
            assert len(seq) >= 30
            spread = max(seq) - min(seq)
            assert spread > 1e-3, f"{name}{key} 近乎常数（截断指纹）"
            # 不得出现「高频尾部钉死在某个常数」：末 5 点必须仍在变化
            tail = seq[-5:]
            assert max(tail) - min(tail) > 1e-6, f"{name}{key} 高频尾部被 clamp"
            assert not any(abs(v - 0.5) < 1e-12 for v in seq), \
                f"{name}{key} 出现硬编码下限 0.5"
            assert not any(abs(v - 0.02) < 1e-12 for v in seq), \
                f"{name}{key} 出现硬编码下限 0.02"

        mass = s["displacement"] * 1000.0
        ratio = d["added_mass_heave"] / mass
        assert 0.3 <= ratio <= 2.0, f"{name} A33/m={ratio:.3f} 越出 0.3~2"
        # 量纲自检：A33 应为 kg 量级（不是吨位×0.85 之类的吨值）
        assert d["added_mass_heave"] > 1e6, f"{name} A33={d['added_mass_heave']:.3e} 疑非 kg"


def test_a5_strip_high_frequency_tail_decays(client):
    """A5 补充：高频段响应必须衰减（辐射阻尼主导），不能保持平台值。"""
    s = SHIPS["KVLCC2"]
    d = _strip(client, _body_strip(s, 0.6))
    i_peak = max(range(len(d["heave_rao"])), key=lambda i: d["heave_rao"][i])
    assert d["heave_rao"][-1] < d["heave_rao"][i_peak]


# --------------------------------------------------------------------- A6


def test_a6_motion_and_strip_periods_agree(client):
    """A6：两端口径自然周期互差 < 15%，并与解析不确定带相容。"""
    for name, s in SHIPS.items():
        mot = _motion(client, _body(s))["natural_periods"]
        st = _strip(client, _body_strip(s))
        pairs = (("heave", "natural_period_heave"), ("pitch", "natural_period_pitch"),
                 ("roll", "natural_period_roll"))
        for k_m, k_s in pairs:
            a, b = mot[k_m], st[k_s]
            err = abs(a - b) / max(a, b)
            assert err < 0.15, f"{name}:{k_m} 两端口径互差 {err * 100:.2f}%"

        # 解析不确定带：A33/m∈[0.5,1.5] ⇒ T33 落在区间内
        mass = s["displacement"] * 1000.0
        aw = sp.cwp_from_cb(s["Cb"]) * s["L"] * s["B"]
        lo = 2.0 * math.pi * math.sqrt(mass * (1.0 + 0.5) / (RHO * G * aw))
        hi = 2.0 * math.pi * math.sqrt(mass * (1.0 + 1.5) / (RHO * G * aw))
        assert lo <= mot["heave"] <= hi, f"{name} T33={mot['heave']:.2f} 不在 [{lo:.2f},{hi:.2f}]"
        # 纵摇：k_yy/L∈[0.24,0.32] 且含附加惯性（系数 1~1.3）
        # 解析不确定带：水线惯性矩 I_LF/(B L³/12) ∈[0.5,1.0]（详细型线 0.5~0.75，
        # 矩形上界 1.0），k_yy/L∈[0.24,0.32]，附加惯性比 ∈[0,1.0]
        rect = s["B"] * s["L"] ** 3 / 12.0
        nabla = s["displacement"] / (RHO / 1000.0)
        gm_lo = 0.5 * rect / nabla + 0.53 * s["T"] - s["KG"]
        gm_hi = 1.0 * rect / nabla + 0.53 * s["T"] - s["KG"]
        t_lo = 2.0 * math.pi * (0.24 * s["L"]) / math.sqrt(G * gm_hi)
        t_hi = 2.0 * math.pi * (0.32 * s["L"]) / math.sqrt(G * gm_lo) * math.sqrt(2.0)
        assert t_lo <= mot["pitch"] <= t_hi, \
            f"{name} T55={mot['pitch']:.2f} 不在解析带 [{t_lo:.2f},{t_hi:.2f}]"


# --------------------------------------------------------------- 物理不变量


def test_long_wave_heave_limit_is_unity(client):
    """长波极限：kL<0.3 时垂荡 RAO→1（Newman 1977 §6.10），误差 <10%。"""
    for name, s in SHIPS.items():
        d = _rao(client, _body(s))
        idx = [i for i, w in enumerate(d["frequencies"]) if sp.deep_water_k(w) * s["L"] < 0.3]
        assert idx, f"{name} 栅格内无 kL<0.3 的验算点"
        i = idx[-1]
        assert abs(d["heave_rao"][i] - 1.0) < 0.10, \
            f"{name} 长波垂荡 RAO={d['heave_rao'][i]:.4f}"


def test_motion_amplitudes_not_hardcoded(client):
    """/motion 幅值随输入变化（原实现恒为 1.5/3.0/8.0）。"""
    s = SHIPS["KVLCC2"]
    a = _motion(client, _body(s, 0.6, 180.0))["motion_amplitudes"]
    b = _motion(client, _body(s, 2.0, 90.0))["motion_amplitudes"]
    c = _motion(client, _body(s, 0.3, 0.0, speed=0.5))["motion_amplitudes"]
    for key in ("heave", "pitch", "roll"):
        vals = {round(a[key], 9), round(b[key], 9), round(c[key], 9)}
        assert len(vals) > 1, f"{key} 幅值对三组输入恒等（硬编码）"
    assert not (abs(a["heave"] - 1.5) < 1e-9 and abs(a["pitch"] - 3.0) < 1e-9
                and abs(a["roll"] - 8.0) < 1e-9)


def test_encounter_frequency_definition():
    """遭遇频率定义：顶浪 U>0 时 ωe>ω，随浪时 ωe<ω。"""
    w, u = 0.6, 15.0 * 1852.0 / 3600.0
    assert sp.encounter_frequency(w, u, 180.0) > w
    assert sp.encounter_frequency(w, u, 0.0) < w
    assert abs(sp.encounter_frequency(w, u, 90.0) - w) < 1e-12