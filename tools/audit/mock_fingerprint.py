"""MOCK 指纹探测器：用「物理主动参数的响应结构」机器裁决端点真假。

背景
----
平台的证据分级（REAL/MOCK/STUB/WITHHELD）历史上靠人工读报告判定：
mission 1523 靠人眼发现回转要素 advance/L=3.50、transfer/L=2.00、TD/L=4.50
同时落在 0.5L 整数栅格上且对舵角/航速全不敏感，从而判 maneuvering=MOCK；
mission 1547 靠人眼发现 /calculations/seakeeping 的垂荡 RAO 恒为 1.260。
每个域都要重新靠人眼发现一次「硬编码指纹」，成本高且易漏。

本工具把这件事机器化。对每个端点声明一个**物理主动参数**（航速、舵角、波周期、
航向、进速系数、KG、Cb …）与若干**关键输出量**，在其余输入不变的前提下扫该参数，
按六条纯数值判据裁决：

  INVARIANT    输出跨扫参的**相对展布** <= 1e-9（浮点意义下完全相同）
               ⇒ 该量对物理输入零响应。最强 MOCK 证据。
  RIGID_RATIO  两个输出量的**比值**跨扫参恒定到 1e-9
               ⇒ 两条本应独立的回归式被写成同一式子的倍数（m1760 实测：
                 Wageningen B 端点 KQ ≡ KT/10，跨 J=0.1…0.9 精确成立）。
  FLAT         同一次响应内，本应随频率/位置变化的**向量输出**其内部展布 <= 1e-9
               ⇒ 该通道是常量填充（m1760 实测：strip 端点 pitch_rao ≡ 0.02）。
  PLATEAU      序列尾部若干点严格相等而前段不等
               ⇒ 输出被截断/夹紧（eta0 在 J>=0.7 后恒为 0.85）。
  COARSE_LATTICE 全部值落在同一粗步长 h 的整数倍上且 h/scale >= 1%
               ⇒ 查表/取整指纹（m1523 的 0.5L 栅格属此类）。
  DIRECTION_VIOLATION 声明了单调查方向（inc/dec）而实测符号相反。
  RESPONSIVE   其余 ⇒ 至少对物理输入有真实响应（**不足以**证明精度达标）。

端点级判决：
     MOCK        任一必须响应量 INVARIANT / RIGID_RATIO / FLAT / DIRECTION_VIOLATION
     PARTIAL     有响应但存在 COARSE_LATTICE / PLATEAU
     RESPONSIVE  全部必须响应量 RESPONSIVE 且无方向违背
     UNAVAILABLE HTTP 非 200（不等于 MOCK，单独记账；422 的几何自洽门禁属**正面**证据）

纪律
----
* 只读：只对在线服务发 POST，不写平台状态、不改任何端点。
* 判决只用被测端点**自身的响应结构**，不引用任何黑箱参考值 ⇒ 判决本身不需被判决。
* 探针原始回执全量落盘（artifacts/mock_sweep/raw.json），判决可离线回放
  （`--offline`），归因链不缺环；新增判据可在旧回执上重算，不必重打服务。

用法
----
    python3 tools/audit/mock_fingerprint.py --live      # 打在线服务，刷新 raw.json + scoreboard.json
    python3 tools/audit/mock_fingerprint.py --offline   # 只回放已落盘 raw.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
OUT_DIR = os.path.join(ROOT, "artifacts", "mock_sweep")
RAW_PATH = os.path.join(OUT_DIR, "raw.json")
SCORE_PATH = os.path.join(OUT_DIR, "scoreboard.json")

INVARIANT_TOL = 1e-9
LATTICE_MIN_RATIO = 1e-2
PLATEAU_MIN_TAIL = 1   # 尾部 >=1 对相邻点严格相等 ⇒ 视为夹紧（前置：序列整体非恒定）

# ──────────────────────────────────────────────────────────────────────────────
# 探针规格
#   keys:      标量输出名 -> 物理方向期望（inc/dec/None）
#   vector_keys: 向量输出名（一次响应即一条曲线）
#   relations: [(num, den)]  比值刚性检查
#   plateau_keys: 夹紧/截断检查
#   physics:   判据的自然语言出处（写进台账，便于人复核）
# ──────────────────────────────────────────────────────────────────────────────
SPECS: List[Dict[str, Any]] = [
    {
        "id": "resistance-speed",
        "endpoint": "calculations/resistance",
        "base": {"length": 230.0, "beam": 32.2, "draft": 10.8,
                 "displacement": 52030.0, "speed": 7.7, "cb": 0.6505},
        "sweep": "speed",
        "values": [3.0, 5.0, 7.7, 10.0, 12.0],
        "keys": {"rt": "inc", "reynolds_number": "inc", "pe": "inc"},
        "relations": [["rf", "rt"]],
        "physics": "R_T 随航速单调增（Re ∝ U）；R_F/R_T 为阻份分解，必随 U 变（波阻份额增长）",
    },
    {
        "id": "resistance-cb",
        "endpoint": "calculations/resistance",
        "base": {"length": 230.0, "beam": 32.2, "draft": 10.8,
                 "displacement": 52030.0, "speed": 7.7, "cb": 0.6505},
        "sweep": "cb",
        "values": [0.55, 0.60, 0.6505, 0.70, 0.75],
        "sync": {"displacement": "L*B*T*cb"},   # 保持几何自洽（D3 门禁）
        "keys": {"cr": None, "ct": None},
        "physics": "剩余阻力系数 C_R 随丰满度 Cb 改变；几何须自洽（∇ = Cb·L·B·T）",
    },
    {
        "id": "stability-kg",
        "endpoint": "calculations/stability",
        "base": {"length": 230.0, "beam": 32.2, "draft": 10.8,
                 "displacement": 52030.0, "cb": 0.6505, "kg": 11.0},
        "sweep": "kg",
        "values": [9.0, 11.0, 13.0, 18.0, 24.0],
        "keys": {"gm": "dec", "gz_max": "dec"},
        "relations": [["gm", "km"]],
        "physics": "GM = KM - KG ⇒ GM 对 KG 严格线性递减（斜率为 -1），GM/KM 必变",
    },
    {
        "id": "stability-beam",
        "endpoint": "calculations/stability",
        "base": {"length": 230.0, "beam": 32.2, "draft": 10.8,
                 "displacement": 52030.0, "cb": 0.6505, "kg": 11.0},
        "sweep": "beam",
        "values": [24.0, 28.0, 32.2, 36.0, 40.0],
        "sync": {"draft": "displacement/(L*beam*cb)"},
        "keys": {"bm": "inc", "gm": "inc"},
        "physics": "BM = I/∇ ∝ B²/∇ ⇒ 定 ∇ 下 BM 随 B 增严格增（KB 不随 B 变，不列入判据）",
    },
    {
        "id": "maneuvering-rudder",
        "endpoint": "maneuvering/turning",
        "base": {"L": 230.0, "B": 32.2, "T": 10.8, "displacement": 52030.0,
                 "Cb": 0.6505, "KG": 11.0, "Dp": 7.0, "Ar": 55.0,
                 "speed": 7.7, "rudder_angle": 35.0},
        "sweep": "rudder_angle",
        "values": [5.0, 10.0, 20.0, 35.0],
        "keys": {"tactical_diameter": "dec", "advance": "dec", "transfer": "dec"},
        "physics": "舵角增大 ⇒ 回转圈收缩（D、advance、transfer 均降）",
    },
    {
        "id": "maneuvering-speed",
        "endpoint": "maneuvering/turning",
        "base": {"L": 230.0, "B": 32.2, "T": 10.8, "displacement": 52030.0,
                 "Cb": 0.6505, "KG": 11.0, "Dp": 7.0, "Ar": 55.0,
                 "speed": 7.7, "rudder_angle": 35.0},
        "sweep": "speed",
        "values": [5.0, 6.0, 7.7, 9.0],
        "keys": {"tactical_diameter": "inc", "advance": "inc"},
        "physics": "回转直径随航速近似线性增大（Nomoto 线性域）",
    },
    {
        "id": "mmg-rudder",
        "endpoint": "maneuvering-mmg/turning",
        "base": {"L": 230.0, "B": 32.2, "T": 10.8, "displacement": 52030.0,
                 "Cb": 0.6505, "Dp": 7.0, "n": 2.0, "Ar": 55.0,
                 "V0": 7.7, "rudder_angle": 35.0},
        "sweep": "rudder_angle",
        "values": [10.0, 20.0, 30.0, 35.0],
        "keys": {"tactical_diameter": "dec", "advance": "dec"},
        "physics": "MMG 3-DOF：δ↑ ⇒ 定常回转半径↓",
    },
    {
        "id": "seakeeping-period",
        "endpoint": "calculations/seakeeping",
        "base": {"length": 180.0, "beam": 28.0, "draft": 10.0, "displacement": 41000.0,
                 "speed": 5.14, "cb": 0.70, "wave_height": 3.0,
                 "wave_period": 9.0, "heading": 180.0},
        "sweep": "wave_period",
        "values": [5.0, 7.0, 9.0, 12.0, 16.0],
        "keys": {"heave_rao_max": None, "pitch_rao_max": None, "roll_rao_max": None},
        "physics": "RAO 是波浪频率的函数；5→16 s 跨过共振区，RAO 必变",
    },
    {
        "id": "seakeeping-heading",
        "endpoint": "calculations/seakeeping",
        "base": {"length": 180.0, "beam": 28.0, "draft": 10.0, "displacement": 41000.0,
                 "speed": 5.14, "cb": 0.70, "wave_height": 3.0,
                 "wave_period": 9.0, "heading": 180.0},
        "sweep": "heading",
        "values": [0.0, 45.0, 90.0, 135.0, 180.0],
        "keys": {"roll_rao_max": None, "pitch_rao_max": None},
        "physics": "横浪(heading=90)横摇响应远大于顶浪(180)；纵摇反之",
    },
    {
        "id": "seakeeping-rao-spectrum",
        "endpoint": "seakeeping/rao",
        "base": {"L": 180.0, "B": 28.0, "T": 10.0, "D": 16.0, "displacement": 41000.0,
                 "Cb": 0.70, "KG": 9.0, "GM": 1.5, "speed": 5.14,
                 "wave_frequency": 0.6, "heading": 180.0},
        "sweep": "wave_frequency",
        "values": [0.3, 0.6, 1.1],
        "vector_keys": ["heave_rao", "pitch_rao"],
        "physics": "端点输入含单一 wave_frequency，若返回整条谱则必须随请求频率改变（忽略输入=指纹）",
    },
    {
        "id": "strip-heading",
        "endpoint": "seakeeping-advanced/strip-theory",
        "base": {"L": 180.0, "B": 28.0, "T": 10.0, "D": 16.0, "displacement": 41000.0,
                 "Cb": 0.70, "Cm": 0.98, "Cwp": 0.80, "KG": 9.0, "GM": 1.5,
                 "speed": 5.14, "wave_frequency": 0.6, "wave_amplitude": 1.5,
                 "heading": 180.0},
        "sweep": "heading",
        "values": [0.0, 45.0, 90.0, 135.0, 180.0],
        "vector_keys": ["heave_rao", "pitch_rao", "roll_rao"],
        "keys": {"natural_period_roll": None},
        "physics": "切片理论：横浪横摇 >> 顶浪横摇；纵摇顶浪最大 ⇒ RAO 曲线必随航向变",
    },
    {
        "id": "strip-frequency",
        "endpoint": "seakeeping-advanced/strip-theory",
        "base": {"L": 180.0, "B": 28.0, "T": 10.0, "D": 16.0, "displacement": 41000.0,
                 "Cb": 0.70, "Cm": 0.98, "Cwp": 0.80, "KG": 9.0, "GM": 1.5,
                 "speed": 5.14, "wave_frequency": 0.6, "wave_amplitude": 1.5,
                 "heading": 180.0},
        "sweep": "wave_frequency",
        "values": [0.3, 0.5, 0.7, 0.9, 1.2],
        "keys": {"added_mass_heave": None, "damping_heave": None, "heave_rao": None},
        "physics": "附加质量/阻尼/RAO 均随波浪频率变化（阻尼高频趋零）",
    },
    {
        "id": "propeller-wageningen-j",
        "endpoint": "propeller-design/wageningen-b",
        "base": {"J": 0.5, "P_D": 1.0, "Z": 4, "Ae_Ao": 0.55},
        "sweep": "J",
        "values": [0.1, 0.3, 0.5, 0.7, 0.9],
        "keys": {"KT": "dec", "KQ": "dec"},
        "relations": [["KQ", "KT"]],
        "plateau_keys": ["eta0"],
        "physics": "B 系列 KT、KQ 是各自的 J 多项式回归，二者比值必随 J 变（恒比=同一式倍数）；"
                   "eta0 过峰值后必下降，截断在常数=夹紧",
    },
    {
        "id": "ext-maneuvering-speed",
        "endpoint": "extended-calculations/maneuvering",
        "base": {"length": 230.0, "beam": 32.2, "draft": 10.8,
                 "displacement": 52030.0, "speed": 7.7, "cb": 0.6505,
                 "rudder_area": 55.0, "prop_diameter": 7.0},
        "sweep": "speed",
        "values": [5.0, 6.0, 7.7, 9.0],
        "keys": {"tactical_diameter": "inc", "advance": "inc"},
        "physics": "回转直径 ∝ 航速（Nomoto 线性域）",
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# 数值内核（纯函数、无 I/O ⇒ 可离线单测、可在旧回执上重算）
# ──────────────────────────────────────────────────────────────────────────────
def find_key(obj: Any, key: str) -> Any:
    """在嵌套响应里取 key 的数值或数值列表；支持 'a.b' 路径。"""
    if "." in key:
        cur = obj
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
        return cur if isinstance(cur, (int, float, list)) else None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            got = find_key(v, key)
            if got is not None:
                return got
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            got = find_key(v, key)
            if got is not None:
                return got
    return None


def as_floats(v: Any) -> Optional[List[float]]:
    """标量 -> [v]；数值列表 -> 列表；其余 -> None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return [float(v)]
    if isinstance(v, (list, tuple)) and v and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        return [float(x) for x in v]
    return None


def relative_spread(vals: Sequence[float]) -> float:
    lo, hi = min(vals), max(vals)
    scale = max(abs(lo), abs(hi))
    if scale == 0.0:
        return 0.0
    return (hi - lo) / scale


def lattice_step(vals: Sequence[float]) -> Optional[float]:
    """全部值落在同一粗步长 h 的整数倍上时返回 h。"""
    vs = sorted(set(vals))
    if len(vs) < 3:
        return None
    deltas = [b - a for a, b in zip(vs, vs[1:]) if b - a > 0]
    if not deltas:
        return None
    h = min(deltas)
    if h <= 0:
        return None
    base = vs[0]
    for v in vs:
        q = (v - base) / h
        if abs(q - round(q)) > 1e-9 * max(1.0, abs(q)):
            return None
    return h


def direction_violation(vals: Sequence[float], expect: Optional[str]) -> Optional[bool]:
    if not expect or len(vals) < 2:
        return None
    diffs = [b - a for a, b in zip(vals, vals[1:])]
    if expect == "inc":
        return any(d < 0 for d in diffs)
    if expect == "dec":
        return any(d > 0 for d in diffs)
    return None


def classify_series(vals: Sequence[float], expect: Optional[str] = None,
                    tol: float = INVARIANT_TOL) -> Dict[str, Any]:
    """单标量量跨扫参裁决。"""
    vs = [float(v) for v in vals]
    out: Dict[str, Any] = {"kind": "scalar", "values": vs, "n": len(vs)}
    if len(vs) < 2:
        out["verdict"] = "NOT_APPLICABLE"
        return out

    spread = relative_spread(vs)
    scale = max(abs(min(vs)), abs(max(vs)))
    out.update(relative_spread=spread, scale=scale, expect=expect)

    if spread <= tol:
        out["verdict"] = "INVARIANT"
        return out

    viol = direction_violation(vs, expect)
    out["direction_violation"] = viol

    h = lattice_step(vs)
    ratio = (h / scale) if (h is not None and scale > 0) else None
    out.update(lattice_step=h, lattice_ratio=ratio)

    if viol:
        out["verdict"] = "DIRECTION_VIOLATION"
    elif h is not None and ratio is not None and ratio >= LATTICE_MIN_RATIO:
        out["verdict"] = "COARSE_LATTICE"
    else:
        out["verdict"] = "RESPONSIVE"
    return out


def classify_vectors(series: Sequence[Sequence[float]],
                     tol: float = INVARIANT_TOL) -> Dict[str, Any]:
    """向量输出（一次响应一条曲线）跨扫参裁决 + 响应内平坦性。"""
    out: Dict[str, Any] = {"kind": "vector", "n": len(series)}
    if len(series) < 1:
        out["verdict"] = "NOT_APPLICABLE"
        return out
    len_ok = len({len(s) for s in series}) == 1
    out["same_length"] = len_ok
    if not len_ok:
        out["verdict"] = "RESPONSIVE"
        return out
    m = len(series[0])
    out["length"] = m
    if m < 2:
        out["verdict"] = "NOT_APPLICABLE"
        return out
    # 跨扫参逐点展布（取最坏点）
    worst = 0.0
    for i in range(m):
        col = [s[i] for s in series]
        worst = max(worst, relative_spread(col))
    out["across_sweep_spread"] = worst
    # 响应内平坦性（单条曲线内部的展布）
    within = [relative_spread(s) for s in series]
    out["within_response_spread_mean"] = sum(within) / len(within)
    if max(within) <= tol and m >= 5:
        out["verdict"] = "FLAT"          # 通道内恒定填充（更具体的判据优先）
    elif worst <= tol:
        out["verdict"] = "INVARIANT"
    else:
        out["verdict"] = "RESPONSIVE"
    return out


def classify_ratio(num: Sequence[float], den: Sequence[float],
                   tol: float = INVARIANT_TOL) -> Dict[str, Any]:
    """两输出量的比值跨扫参裁决：恒定到 tol ⇒ RIGID_RATIO。"""
    ratios = []
    for a, b in zip(num, den):
        if b == 0:
            continue
        ratios.append(a / b)
    out: Dict[str, Any] = {"kind": "ratio", "ratios": ratios, "n": len(ratios)}
    if len(ratios) < 3:
        out["verdict"] = "NOT_APPLICABLE"
        return out
    sp = relative_spread(ratios)
    out["relative_spread"] = sp
    out["verdict"] = "RIGID_RATIO" if sp <= tol else "RESPONSIVE"
    return out


def plateau_tail(vals: Sequence[float], tol: float = INVARIANT_TOL) -> Dict[str, Any]:
    """序列尾部是否被夹紧（尾部 >=2 点严格相等而前段不等）。"""
    vs = [float(v) for v in vals]
    out: Dict[str, Any] = {"kind": "plateau", "values": vs}
    if len(vs) < 3:
        out["verdict"] = "NOT_APPLICABLE"
        return out
    k = 0
    for i in range(len(vs) - 1, len(vs) - 1 - PLATEAU_MIN_TAIL, -1):
        if abs(vs[i] - vs[i - 1]) <= tol * max(1.0, abs(vs[i])):
            k += 1
        else:
            break
    out["tail_equal_pairs"] = k
    out["verdict"] = "PLATEAU" if k >= PLATEAU_MIN_TAIL else "RESPONSIVE"
    return out


MOCK_LEVEL = {"INVARIANT", "RIGID_RATIO", "FLAT", "DIRECTION_VIOLATION"}
PARTIAL_LEVEL = {"COARSE_LATTICE", "PLATEAU"}


def endpoint_verdict(per_key: Dict[str, Dict[str, Any]], http_status: int) -> str:
    if http_status != 200:
        return "UNAVAILABLE"
    verdicts = [v.get("verdict") for v in per_key.values()]
    if not verdicts:
        return "NO_KEY"
    if any(v in MOCK_LEVEL for v in verdicts):
        return "MOCK"
    if any(v in PARTIAL_LEVEL for v in verdicts):
        return "PARTIAL"
    if all(v == "RESPONSIVE" for v in verdicts):
        return "RESPONSIVE"
    return "PARTIAL"


# ──────────────────────────────────────────────────────────────────────────────
# 载荷生成 / 在线探针 / 判决汇总
# ──────────────────────────────────────────────────────────────────────────────
def build_payload(spec: Dict[str, Any], val: float) -> Dict[str, Any]:
    """按 spec 生成一份自洽载荷；sync 表达式里的 L/B/T/displacement/cb/beam 由当前载荷代换。"""
    p = dict(spec["base"])
    p[spec["sweep"]] = val
    for target, expr in (spec.get("sync") or {}).items():
        env = {k: p[k] for k in ("L", "B", "T", "D", "beam", "draft", "length",
                                 "displacement", "cb", "Cb", "speed", "speed") if k in p}
        env.update({"L": p.get("L", p.get("length")), "B": p.get("B", p.get("beam")),
                    "T": p.get("T", p.get("draft"))})
        p[target] = float(eval(expr, {"__builtins__": {}}, env))  # 仅本地常量表达式
    return p


def post_json(endpoint: str, payload: Dict[str, Any], timeout: float = 30.0) -> Tuple[int, Any]:
    import urllib.error
    import urllib.request

    url = BASE.rstrip("/") + "/" + endpoint.lstrip("/")
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"detail": body[:800]}
        return e.code, parsed
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def run_live(specs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    raw: Dict[str, Any] = {"api_base": BASE, "probes": {}}
    for spec in specs:
        probes = []
        for val in spec["values"]:
            payload = build_payload(spec, val)
            status, resp = post_json(spec["endpoint"], payload)
            probes.append({"sweep_value": val, "payload": payload,
                           "http_status": status, "response": resp})
        raw["probes"][spec["id"]] = {"spec": spec, "probes": probes}
    return raw


def score(raw: Dict[str, Any]) -> Dict[str, Any]:
    rows: Dict[str, Dict[str, Any]] = {}
    for sid, blk in raw["probes"].items():
        spec = blk["spec"]
        probes = blk["probes"]
        ok = [p for p in probes if p["http_status"] == 200]
        per_key: Dict[str, Dict[str, Any]] = {}

        for key, expect in (spec.get("keys") or {}).items():
            series = []
            for p in ok:
                got = as_floats(find_key(p["response"], key))
                if got is not None and len(got) == 1:
                    series.append(got[0])
            if len(series) >= 2:
                per_key[key] = classify_series(series, expect=expect)

        for key in (spec.get("vector_keys") or []):
            series: List[List[float]] = []
            for p in ok:
                got = as_floats(find_key(p["response"], key))
                if got is not None:
                    series.append(got)
            if series:
                per_key[key] = classify_vectors(series)

        for num, den in (spec.get("relations") or []):
            a, b = [], []
            for p in ok:
                va = as_floats(find_key(p["response"], num))
                vb = as_floats(find_key(p["response"], den))
                if va and vb and len(va) == 1 and len(vb) == 1:
                    a.append(va[0])
                    b.append(vb[0])
            if len(a) >= 3:
                per_key[f"{num}/{den}"] = classify_ratio(a, b)

        for key in (spec.get("plateau_keys") or []):
            series = []
            for p in ok:
                got = as_floats(find_key(p["response"], key))
                if got is not None and len(got) == 1:
                    series.append(got[0])
            if len(series) >= 3:
                per_key[f"{key}(tail)"] = plateau_tail(series)

        status = probes[0]["http_status"] if probes else 0
        err = next((p["response"] for p in probes if p["http_status"] != 200), None)
        rows[sid] = {
            "id": sid,
            "endpoint": spec["endpoint"],
            "sweep": spec["sweep"],
            "values": spec["values"],
            "physics": spec["physics"],
            "http_status": status,
            "http_error": (str(err)[:300] if err else None),
            "keys": per_key,
            "verdict": endpoint_verdict(per_key, status),
        }

    summary: Dict[str, Any] = {"n_specs": len(rows), "verdict_counts": {},
                               "mock": [], "partial": [], "unavailable": [],
                               "responsive": [], "mock_keys": []}
    for r in rows.values():
        summary["verdict_counts"][r["verdict"]] = summary["verdict_counts"].get(r["verdict"], 0) + 1
        bucket = {"MOCK": "mock", "PARTIAL": "partial", "UNAVAILABLE": "unavailable",
                  "RESPONSIVE": "responsive"}.get(r["verdict"])
        if bucket:
            summary[bucket].append(r["id"])
        for k, v in r["keys"].items():
            if v.get("verdict") in MOCK_LEVEL:
                summary["mock_keys"].append({"probe": r["id"], "endpoint": r["endpoint"],
                                             "key": k, "verdict": v["verdict"]})
    return {"api_base": raw.get("api_base"), "rows": rows, "summary": summary}


def load_raw(path: str = RAW_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="MOCK 指纹探测器（响应结构判据）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--live", action="store_true", help="打在线服务并刷新 artifacts")
    g.add_argument("--offline", action="store_true", help="回放已落盘 raw.json（无网络）")
    args = ap.parse_args(argv)

    os.makedirs(OUT_DIR, exist_ok=True)
    if args.live:
        raw = run_live(SPECS)
        with open(RAW_PATH, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=1)
    else:
        raw = load_raw()

    sc = score(raw)
    with open(SCORE_PATH, "w", encoding="utf-8") as f:
        json.dump(sc, f, ensure_ascii=False, indent=1)

    s = sc["summary"]
    print(f"probes: {s['n_specs']}   {s['verdict_counts']}")
    for r in sc["rows"].values():
        det = ", ".join(f"{k}={v.get('verdict')}" for k, v in r["keys"].items())
        print(f"  [{r['verdict']:<11}] {r['id']:<26} {r['endpoint']:<40} {det}")
    if s["mock"]:
        print("MOCK 指纹命中：" + ", ".join(s["mock"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())