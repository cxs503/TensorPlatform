"""云图演化动画·场数据生成器 (FEAT-SIM-DATAHOUSE)

为 benchmark_cases 生成物理动机的场快照序列（sim-assets 事件），供
cloud_evo.py 渲染成演化动画。场是「船体绕流/波面」的 2D 演化：

  - 标量场：波面高程 η(x,y,t) —— Kelvin 尾迹 + 船体兴波，随船前进演化。
  - 矢量场：绕流速度 (u,v)(x,y,t) —— 势流 + 尾迹扰动，随船前进演化。

物理动机（非随机）：以 benchmark_reference.json 的几何（L/B/T/Cb）与
速度（speed_ms）为输入，用 Froude 数决定尾迹半角 α=asin(1/Fr)，
波面用 Kelvin 尾迹解析式叠加，速度场用偶极子势流 + 尾迹涡。

用法：
    python3 backend/tools/cloud_evo_fieldgen.py --cases-dir <out> \
        --benchmark backend/data/benchmark_reference.json \
        --n-frames 24 --fps 10
    # 产出 <out>/<case_id>/ 下每个 case 一个事件目录（frame_*.npy + manifest.json）
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np

G = 9.81


def _load_benchmark(path: Path) -> List[Dict]:
    d = json.loads(Path(path).read_text())
    return d["cases"]


def _geom(case: Dict) -> Dict:
    g = case.get("geometry", {}) or {}
    return {
        "L": float(g.get("length_m", 5.72)),
        "B": float(g.get("beam_m", 0.759)),
        "T": float(g.get("draft_m", 0.246)),
        "Cb": float(g.get("cb", 0.508)),
    }


def _speed(case: Dict) -> float:
    return float(case.get("speed_ms", 1.0))


def _kelvin_wave(x: np.ndarray, y: np.ndarray, t: float, L: float,
                 Fr: float, amp: float) -> np.ndarray:
    """Kelvin 尾迹波面（船在 x 方向前进，源在 x0=Fr*L*t）。"""
    x0 = Fr * L * t
    X = x - x0
    Y = y
    alpha = math.asin(min(1.0 / max(Fr, 0.5), 1.0)) if Fr > 0.5 else 0.35
    # 尾迹扇区内叠加余弦波
    k = 2 * math.pi / (L * 0.5)
    r = np.hypot(X, Y)
    mask = (X < 0) & (np.abs(Y) < np.tan(alpha) * np.abs(X) + 0.5)
    w = amp * np.cos(k * X - 0.5 * k * Y * Y / np.maximum(np.abs(X), 1e-3)) * mask
    # 船体附近兴波（近场）
    near = amp * 0.6 * np.exp(-(r ** 2) / (L * 0.3) ** 2) * np.cos(k * X)
    return w + near


def _dipole_flow(x: np.ndarray, y: np.ndarray, t: float, L: float,
                 U: float) -> Tuple[np.ndarray, np.ndarray]:
    """偶极子势流（船体绕流）+ 尾迹涡。"""
    x0 = U * t
    X = x - x0
    Y = y
    r2 = X ** 2 + Y ** 2 + 1e-6
    # 偶极子（指向 -x，模拟船体排开）
    m = U * L * 0.5
    u = U + m * (Y ** 2 - X ** 2) / r2 ** 2
    v = -2 * m * X * Y / r2 ** 2
    # 尾迹涡（船后）
    wake = (X < -0.5 * L)
    gamma = U * L * 0.15
    u = u + gamma * Y / (r2 + 1e-3) * wake
    v = v - gamma * X / (r2 + 1e-3) * wake
    return u, v


def _gen_scalar_event(case: Dict, n_frames: int, nx: int = 60, ny: int = 40) -> List[np.ndarray]:
    g = _geom(case)
    U = _speed(case)
    Fr = U / math.sqrt(G * g["L"])
    amp = 0.05 * g["L"] * min(Fr, 1.0)
    x = np.linspace(-2 * g["L"], 2 * g["L"], nx)
    y = np.linspace(-1.5 * g["L"], 1.5 * g["L"], ny)
    X, Y = np.meshgrid(x, y)
    frames = []
    for i in range(n_frames):
        t = i / max(n_frames - 1, 1) * (2 * g["L"] / U)  # 船走完 2L
        w = _kelvin_wave(X, Y, t, g["L"], Fr, amp)
        frames.append(w)
    return frames


def _gen_vector_event(case: Dict, n_frames: int, nx: int = 60, ny: int = 40) -> List[np.ndarray]:
    g = _geom(case)
    U = _speed(case)
    x = np.linspace(-2 * g["L"], 2 * g["L"], nx)
    y = np.linspace(-1.5 * g["L"], 1.5 * g["L"], ny)
    X, Y = np.meshgrid(x, y)
    frames = []
    for i in range(n_frames):
        t = i / max(n_frames - 1, 1) * (2 * g["L"] / U)
        u, v = _dipole_flow(X, Y, t, g["L"], U)
        frames.append(np.stack([u, v], axis=-1))
    return frames


def _write_event(out_dir: Path, case_id: str, frames: List[np.ndarray],
                 kind: str, meta: Dict) -> None:
    d = out_dir / case_id
    d.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(frames):
        np.save(d / f"frame_{i:03d}.npy", f)
    man = {"case": case_id, "kind": kind, "n_frames": len(frames), **meta}
    (d / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases-dir", required=True, help="输出事件目录")
    ap.add_argument("--benchmark", default="backend/data/benchmark_reference.json")
    ap.add_argument("--n-frames", type=int, default=24)
    ap.add_argument("--scalar-cases", type=int, default=12, help="标量场案例数")
    ap.add_argument("--vector-cases", type=int, default=8, help="矢量场案例数")
    args = ap.parse_args()

    cases = _load_benchmark(Path(args.benchmark))
    out = Path(args.cases_dir)
    out.mkdir(parents=True, exist_ok=True)

    produced = []
    # 标量场：前 N 个 case（每个 case 一个事件）
    for ci, case in enumerate(cases[: args.scalar_cases]):
        cid = case["case_id"]
        frames = _gen_scalar_event(case, args.n_frames)
        _write_event(out, cid, frames, "scalar",
                     {"ship_type": case["ship_type"], "speed_ms": case["speed_ms"]})
        produced.append({"case": cid, "kind": "scalar", "n_frames": len(frames)})

    # 矢量场：后 M 个 case（复用，不同物理场）
    for ci, case in enumerate(cases[: args.vector_cases]):
        cid = case["case_id"] + "-vec"
        frames = _gen_vector_event(case, args.n_frames)
        _write_event(out, cid, frames, "vector",
                     {"ship_type": case["ship_type"], "speed_ms": case["speed_ms"]})
        produced.append({"case": cid, "kind": "vector", "n_frames": len(frames)})

    print(json.dumps({"produced": produced, "total": len(produced)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
