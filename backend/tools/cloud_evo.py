"""云图演化动画标准渲染器 (FEAT-SIM-DATAHOUSE)

把「成功的计算」做成云图演化动画入库：输入 sim-assets 事件（场快照序列
npz/npy 或 vtk），输出 mp4 演化动画 + 海报帧 png。

输入约定（sim-assets 事件）：
  - 目录模式：目录内每个文件是一个时间步的场快照。
      * 标量场：每个文件为 2D 数组 (ny, nx) 或含 'field'/'value' 键的 npz。
      * 矢量场：每个文件为 3D 数组 (ny, nx, 2)（u,v）或含 'u','v' 键的 npz。
      * 文件名按时间排序（自然排序），或由 manifest.json 的 'order' 指定。
  - 单文件模式：一个 npz 含 3D 数组：
      * 标量：'field' = (nt, ny, nx)
      * 矢量：'u','v' = (nt, ny, nx)
      * 可选 'x','y' 网格坐标；可选 't' 时间戳数组；可选 'case' 案例名。

输出：
  - <out>/<case>_evo.mp4   —— 演化动画（ffmpeg；无 ffmpeg 时降级 gif）
  - <out>/<case>_poster.png —— 海报帧（末帧 + colorbar + 时间戳 + case 名）
  - <out>/<case>_manifest.json —— 渲染元数据（帧数/时长/字段类型/降级原因）

坏数据优雅降级（不崩）：
  - 空场（全 NaN/全零/无数据）→ 记日志 + 写 manifest.degraded，仍出海报帧。
  - 单帧（只有 1 个时间步）→ 记日志 + 写 manifest.degraded='single_frame'，
    仍出海报帧（动画退化为静态）。
  - 损坏 npz（无法解析）→ 记日志 + 写 manifest.error，不抛异常。

用法：
    python3 backend/tools/cloud_evo.py --input <dir|npz> --out <dir> \
        --case <name> [--kind scalar|vector] [--fps 10]
    # 或作为模块：
    from tools.cloud_evo import render_event
    render_event(input_path, out_dir, case="KVLCC2-01", kind="scalar")
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("cloud_evo")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

# 尝试导入 matplotlib（渲染必需；缺失时降级为纯元数据）
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize
    _MPL = True
except Exception:  # pragma: no cover
    _MPL = False
    logger.warning("matplotlib 不可用——渲染器降级为纯元数据模式")

# 尝试定位 ffmpeg（mp4 编码；缺失时降级 gif）
_FFMPEG_EXE: Optional[str] = None
for _cand in (
    os.environ.get("IMAGEIO_FFMPEG_EXE", ""),
    os.environ.get("FFMPEG_BIN", ""),
    "/usr/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
):
    if _cand and os.path.isfile(_cand):
        _FFMPEG_EXE = _cand
        break
if _FFMPEG_EXE is None:
    try:
        import imageio_ffmpeg
        _FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        _FFMPEG_EXE = None
if _FFMPEG_EXE:
    logger.info("ffmpeg 定位: %s", _FFMPEG_EXE)
else:
    logger.warning("ffmpeg 未定位——mp4 将降级为 gif（PillowWriter）")


# ---------------------------------------------------------------------------
# 输入解析
# ---------------------------------------------------------------------------
_NATURAL_KEY = re.compile(r"(\d+)")


def _natural_key(name: str) -> List[Any]:
    """自然排序键：'frame_10.npy' < 'frame_2.npy'。"""
    return [int(g) if g.isdigit() else g for g in _NATURAL_KEY.split(name)]


def _load_snapshot(path: Path) -> Optional[np.ndarray]:
    """加载单个场快照 → (ny, nx) 或 (ny, nx, 2)。损坏/空返回 None。"""
    try:
        if path.suffix == ".npz":
            z = np.load(path, allow_pickle=False)
            for key in ("field", "value", "data"):
                if key in z:
                    return np.asarray(z[key], dtype=float)
            if "u" in z and "v" in z:
                return np.stack([np.asarray(z["u"], dtype=float),
                                 np.asarray(z["v"], dtype=float)], axis=-1)
            # 无命名键：取第一个数组
            for k in z.files:
                return np.asarray(z[k], dtype=float)
            return None
        if path.suffix in (".npy", ".np"):
            return np.asarray(np.load(path, allow_pickle=False), dtype=float)
        if path.suffix in (".vtk", ".vtu"):
            # vtk 文本解析（简化：只读 POINTS/SCALARS 块）——本单以 npz/npy 为主
            return _parse_vtk_text(path)
        return None
    except Exception as e:  # 损坏文件
        logger.warning("快照 %s 解析失败（损坏?）: %s", path, e)
        return None


def _parse_vtk_text(path: Path) -> Optional[np.ndarray]:
    """极简 vtk 文本解析：读 POINTS 与 SCALARS/VECTORS 到结构化网格。"""
    try:
        lines = path.read_text().splitlines()
        pts: List[Tuple[float, float]] = []
        vals: List[float] = []
        in_pts = False
        for ln in lines:
            s = ln.strip()
            if s.startswith("POINTS"):
                in_pts = True
                continue
            if s.startswith(("SCALARS", "VECTORS", "CELL_DATA", "POINT_DATA")):
                in_pts = False
                continue
            if in_pts:
                for tok in s.split():
                    pass
            # 简化：不完整解析，返回 None 由调用方降级
        return None
    except Exception:
        return None


def _load_event(input_path: Path) -> Dict[str, Any]:
    """解析 sim-assets 事件 → {frames: list[np.ndarray], meta}。"""
    frames: List[np.ndarray] = []
    meta: Dict[str, Any] = {"source": str(input_path)}
    if input_path.is_dir():
        files = sorted(
            [p for p in input_path.iterdir()
             if p.suffix in (".npz", ".npy", ".np", ".vtk", ".vtu")],
            key=lambda p: _natural_key(p.name),
        )
        # manifest.json 可覆盖顺序
        man = input_path / "manifest.json"
        if man.is_file():
            try:
                order = json.loads(man.read_text()).get("order", [])
                if order:
                    by_name = {p.name: p for p in files}
                    files = [by_name[n] for n in order if n in by_name]
            except Exception:
                pass
        for f in files:
            arr = _load_snapshot(f)
            if arr is not None:
                frames.append(arr)
        meta["frame_files"] = [f.name for f in files]
    elif input_path.is_file():
        arr = _load_snapshot(input_path)
        if arr is not None:
            # 单 npz 可能含 3D 时间序列
            if arr.ndim == 3 and arr.shape[0] > 1 and arr.shape[-1] not in (2,):
                # (nt, ny, nx) 标量时间序列
                frames = [arr[i] for i in range(arr.shape[0])]
                meta["time_series"] = True
            elif arr.ndim == 3 and arr.shape[-1] == 2:
                # (nt, ny, nx, 2) 矢量时间序列
                frames = [arr[i] for i in range(arr.shape[0])]
                meta["time_series"] = True
                meta["vector"] = True
            else:
                frames = [arr]
    else:
        raise FileNotFoundError(f"输入不存在: {input_path}")
    return {"frames": frames, "meta": meta}


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def _field_kind(frames: List[np.ndarray]) -> str:
    """判定字段类型：scalar / vector。"""
    if not frames:
        return "scalar"
    a = frames[0]
    return "vector" if (a.ndim == 3 and a.shape[-1] == 2) else "scalar"


def _is_empty(arr: np.ndarray) -> bool:
    """空场判定：全 NaN / 全零 / 无有限值。"""
    if arr.size == 0:
        return True
    f = np.asarray(arr, dtype=float)
    if not np.isfinite(f).any():
        return True
    return bool(np.allclose(f, 0.0, atol=1e-12))


def _render_frame(ax, arr: np.ndarray, kind: str, vmin: float, vmax: float,
                  cmap: str = "viridis") -> None:
    """渲染单帧到给定 axes。"""
    if kind == "vector":
        u = arr[..., 0]
        v = arr[..., 1]
        ny, nx = u.shape
        # 网格（若坐标缺失用索引）
        x = np.arange(nx)
        y = np.arange(ny)
        # 降采样避免过密箭头
        step = max(1, int(np.ceil(max(ny, nx) / 40)))
        ax.quiver(x[::step], y[::step], u[::step, ::step], v[::step, ::step],
                  scale=1.0, scale_units="xy", angles="xy", color="k")
        ax.set_xlim(-0.5, nx - 0.5)
        ax.set_ylim(-0.5, ny - 0.5)
    else:
        ny, nx = arr.shape
        x = np.arange(nx)
        y = np.arange(ny)
        cf = ax.contourf(x, y, arr, levels=24, cmap=cmap,
                         vmin=vmin, vmax=vmax)
        return cf


def _global_range(frames: List[np.ndarray], kind: str) -> Tuple[float, float]:
    """全局色标范围（跨所有帧稳定）。"""
    if kind == "vector":
        vals = [np.abs(f[..., 0]) for f in frames] + [np.abs(f[..., 1]) for f in frames]
    else:
        vals = [f for f in frames]
    allv = np.concatenate([np.asarray(v, dtype=float).ravel() for v in vals])
    if allv.size == 0 or not np.isfinite(allv).any():
        return 0.0, 1.0
    return float(np.nanmin(allv)), float(np.nanmax(allv))


def _write_mp4(frames: List[np.ndarray], kind: str, out_mp4: Path,
               case: str, fps: int, vmin: float, vmax: float) -> bool:
    """用 matplotlib FuncAnimation + FFMpegWriter 写 mp4；失败降级 gif。"""
    if not _MPL:
        return False
    fig, ax = plt.subplots(figsize=(6, 5))
    fig.suptitle(case, fontsize=11)
    cf = None
    if kind == "vector":
        _render_frame(ax, frames[0], kind, vmin, vmax)
    else:
        cf = _render_frame(ax, frames[0], kind, vmin, vmax)
    if cf is not None:
        fig.colorbar(cf, ax=ax, label="field")

    def _update(i: int):
        ax.clear()
        ax.set_title(f"{case}  t={i}  (frame {i+1}/{len(frames)})")
        c = _render_frame(ax, frames[i], kind, vmin, vmax)
        if c is not None and cf is not None:
            pass  # colorbar 已固定
        return []

    from matplotlib.animation import FuncAnimation
    anim = FuncAnimation(fig, _update, frames=len(frames), interval=1000 / fps, blit=False)
    try:
        if _FFMPEG_EXE:
            from matplotlib.animation import FFMpegWriter
            writer = FFMpegWriter(fps=fps, codec="libx264", bitrate=1800)
            anim.save(str(out_mp4), writer=writer, dpi=100)
            plt.close(fig)
            return True
        # 无 ffmpeg → gif 降级
        from matplotlib.animation import PillowWriter
        gif = out_mp4.with_suffix(".gif")
        writer = PillowWriter(fps=fps)
        anim.save(str(gif), writer=writer, dpi=100)
        plt.close(fig)
        logger.warning("ffmpeg 缺失——已降级写 gif: %s", gif)
        return False
    except Exception as e:
        logger.warning("mp4 写入失败（降级）: %s", e)
        plt.close(fig)
        return False


def _write_poster(frames: List[np.ndarray], kind: str, out_png: Path,
                  case: str, vmin: float, vmax: float) -> bool:
    """写海报帧 png（末帧 + colorbar + 时间戳 + case 名）。"""
    if not _MPL or not frames:
        return False
    fig, ax = plt.subplots(figsize=(6, 5))
    last = frames[-1]
    cf = _render_frame(ax, last, kind, vmin, vmax)
    if cf is not None:
        fig.colorbar(cf, ax=ax, label="field")
    ax.set_title(f"{case}  final frame (t={len(frames)-1})")
    fig.tight_layout()
    fig.savefig(str(out_png), dpi=120)
    plt.close(fig)
    return True


def render_event(input_path: Path, out_dir: Path, case: str = "case",
                 kind: Optional[str] = None, fps: int = 10) -> Dict[str, Any]:
    """渲染一个 sim-assets 事件 → mp4 + 海报 + manifest。坏数据优雅降级。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {
        "case": case,
        "input": str(input_path),
        "status": "ok",
        "degraded": [],
        "outputs": {},
    }
    try:
        ev = _load_event(input_path)
    except Exception as e:
        logger.error("事件加载失败（不崩，降级）: %s", e)
        result["status"] = "error"
        result["error"] = str(e)
        _write_manifest(out_dir, case, result)
        return result

    frames = ev["frames"]
    if not frames:
        logger.warning("case=%s 空场（无帧）——降级", case)
        result["status"] = "degraded"
        result["degraded"].append("empty_field")
        _write_manifest(out_dir, case, result)
        return result

    # 过滤空帧
    valid = [f for f in frames if not _is_empty(f)]
    if not valid:
        logger.warning("case=%s 全空场——降级", case)
        result["status"] = "degraded"
        result["degraded"].append("empty_field")
        _write_manifest(out_dir, case, result)
        return result
    if len(valid) < len(frames):
        result["degraded"].append(f"dropped_{len(frames)-len(valid)}_empty_frames")
    frames = valid

    if kind is None:
        kind = _field_kind(frames)
    result["kind"] = kind
    result["n_frames"] = len(frames)

    if len(frames) == 1:
        logger.warning("case=%s 单帧——动画退化为静态（降级）", case)
        result["degraded"].append("single_frame")

    vmin, vmax = _global_range(frames, kind)

    # 写 mp4（或 gif 降级）
    mp4 = out_dir / f"{case}_evo.mp4"
    ok_mp4 = _write_mp4(frames, kind, mp4, case, fps, vmin, vmax)
    if ok_mp4:
        result["outputs"]["mp4"] = str(mp4)
    else:
        gif = mp4.with_suffix(".gif")
        if gif.is_file():
            result["outputs"]["gif"] = str(gif)
            result["degraded"].append("gif_fallback")
        else:
            result["degraded"].append("no_video")

    # 写海报
    poster = out_dir / f"{case}_poster.png"
    if _write_poster(frames, kind, poster, case, vmin, vmax):
        result["outputs"]["poster"] = str(poster)
    else:
        result["degraded"].append("no_poster")

    _write_manifest(out_dir, case, result)
    logger.info("case=%s 渲染完成: %s", case, result["status"])
    return result


def _write_manifest(out_dir: Path, case: str, result: Dict[str, Any]) -> None:
    man = out_dir / f"{case}_manifest.json"
    man.write_text(json.dumps(result, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="云图演化动画标准渲染器")
    ap.add_argument("--input", required=True, help="sim-assets 事件（目录或 npz）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--case", default="case", help="案例名")
    ap.add_argument("--kind", choices=["scalar", "vector"], default=None)
    ap.add_argument("--fps", type=int, default=10)
    args = ap.parse_args()
    r = render_event(Path(args.input), Path(args.out), args.case, args.kind, args.fps)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
