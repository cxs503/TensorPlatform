"""坐标系与旋转矩阵 (coordinate frames & rotations) — 跨求解器交换的几何契约.

三个常见失效模式在这里被拦下:
  1. **手性/朝向错**: 船体系 z-up 右手系与某些求解器的 y-up 系混用 -> 结果"看起来
     对、量级对、方向错"。本模块用 ``det(R) == +1`` 与 ``R^T R == I`` 双判据把
     它变成机器可算的错误 (容差 1e-12)。
  2. **平移被当矢量旋转**: 点 (point) 与矢量 (vector) 变换不同 (点加平移, 矢量不加),
     接口分开命名, 不允许"顺手用同一个函数"。
  3. **变换链不可追溯**: 每次跨坐标系都通过 :class:`FrameGraph` 的最短路, 并记录
     经过的帧序列 (``hops``), 落盘进报告即可复算。

约定
----
* 全部右手系; ``z_up=True`` 的帧以 z 为垂向。欧拉角采用 z-up 惯例
  ``R = Rz(yaw) @ Ry(pitch) @ Rx(roll)`` (内旋, 先绕 x 转 roll)。
* 变换 ``(R, t)`` 的含义是 ``p_parent = R @ p_child + t`` (子帧坐标 -> 父帧坐标)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

Vec3 = Tuple[float, float, float]
Mat3 = Tuple[Vec3, Vec3, Vec3]

#: 正交性/行列式判定容差 (验收判据: det(R)=1±1e-12)
ORTHO_TOL = 1e-12

IDENTITY: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
ZERO_T: Vec3 = (0.0, 0.0, 0.0)


class FrameError(ValueError):
    """坐标系/旋转矩阵不合法 (契约违约)."""


# ---------------------------------------------------------------------------
# 矩阵基础
# ---------------------------------------------------------------------------
def mat_mul(a: Mat3, b: Mat3) -> Mat3:
    return tuple(  # type: ignore[return-value]
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )


def mat_vec(a: Mat3, v: Vec3) -> Vec3:
    return tuple(sum(a[i][k] * v[k] for k in range(3)) for i in range(3))  # type: ignore[return-value]


def transpose(a: Mat3) -> Mat3:
    return tuple(tuple(a[j][i] for j in range(3)) for i in range(3))  # type: ignore[return-value]


def det(a: Mat3) -> float:
    return (
        a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
        - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
        + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0])
    )


def vec_add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vec_sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vec_norm(a: Vec3) -> float:
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


def orthonormality_error(r: Mat3) -> float:
    """``max|R^T R - I|`` (无穷范数), 验收判据用 (须 <= 1e-12)."""
    rt = transpose(r)
    rr = mat_mul(rt, r)
    return max(abs(rr[i][j] - IDENTITY[i][j]) for i in range(3) for j in range(3))


def det_error(r: Mat3) -> float:
    """``|det(R) - 1|``."""
    return abs(det(r) - 1.0)


def check_rotation(r: Mat3, *, tol: float = ORTHO_TOL, label: str = "R") -> Mat3:
    """旋转矩阵三项校验: 形状/有限性 + 正交性 + ``det=+1`` (含镜像判别)."""
    if not (isinstance(r, (tuple, list)) and len(r) == 3):
        raise FrameError(f"{label} 必须是 3x3 矩阵")
    for row in r:
        if not (isinstance(row, (tuple, list)) and len(row) == 3):
            raise FrameError(f"{label} 必须是 3x3 矩阵")
        for v in row:
            if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                raise FrameError(f"{label} 含非法元素: {v!r}")
    m: Mat3 = tuple(tuple(float(v) for v in row) for row in r)  # type: ignore[assignment]
    e_ortho = orthonormality_error(m)
    if e_ortho > tol:
        raise FrameError(f"{label} 非正交: max|R^T R - I| = {e_ortho:.3e} > {tol:g}")
    d = det(m)
    if abs(d - 1.0) > tol:
        kind = "含镜像 (det=-1)" if d < 0 else "det 非 1"
        raise FrameError(f"{label} {kind}: det = {d:.15f} (|det-1| = {abs(d - 1):.3e})")
    return m


# ---------------------------------------------------------------------------
# 旋转构造
# ---------------------------------------------------------------------------
def rot_x(angle_rad: float) -> Mat3:
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))


def rot_y(angle_rad: float) -> Mat3:
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))


def rot_z(angle_rad: float) -> Mat3:
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def euler_zup(roll: float = 0.0, pitch: float = 0.0, yaw: float = 0.0,
              *, degrees: bool = False) -> Mat3:
    """z-up 内旋欧拉角 -> 旋转矩阵 ``Rz(yaw) @ Ry(pitch) @ Rx(roll)``."""
    k = math.pi / 180.0 if degrees else 1.0
    return mat_mul(mat_mul(rot_z(yaw * k), rot_y(pitch * k)), rot_x(roll * k))


def axis_angle(axis: Sequence[float], angle_rad: float) -> Mat3:
    """罗德里格斯公式; ``axis`` 需非零 (零轴 -> :class:`FrameError`)."""
    n = vec_norm(tuple(float(v) for v in axis))  # type: ignore[arg-type]
    if n == 0.0:
        raise FrameError("旋转轴为零向量, 无法定义旋转")
    x, y, z = (float(v) / n for v in axis)
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    C = 1.0 - c
    return (
        (c + x * x * C, x * y * C - z * s, x * z * C + y * s),
        (y * x * C + z * s, c + y * y * C, y * z * C - x * s),
        (z * x * C - y * s, z * y * C + x * s, c + z * z * C),
    )


def from_quaternion(w: float, x: float, y: float, z: float) -> Mat3:
    """单位四元数 -> 旋转矩阵 (先归一化; 模长 0 -> :class:`FrameError`)."""
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if n == 0.0:
        raise FrameError("零四元数无法定义旋转")
    w, x, y, z = w / n, x / n, y / n, z / n
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )


def to_quaternion(r: Mat3) -> Tuple[float, float, float, float]:
    """旋转矩阵 -> (w, x, y, z); 用于跨求解器交换 (有些求解器只收四元数)."""
    m = check_rotation(r)
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2][1] - m[1][2]) / s
        y = (m[0][2] - m[2][0]) / s
        z = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        w = (m[2][1] - m[1][2]) / s
        x = 0.25 * s
        y = (m[0][1] + m[1][0]) / s
        z = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        w = (m[0][2] - m[2][0]) / s
        x = (m[0][1] + m[1][0]) / s
        y = 0.25 * s
        z = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        w = (m[1][0] - m[0][1]) / s
        x = (m[0][2] + m[2][0]) / s
        y = (m[1][2] + m[2][1]) / s
        z = 0.25 * s
    return (w, x, y, z)


# ---------------------------------------------------------------------------
# 帧 / 变换
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Frame:
    """一个坐标系. ``parent`` 为空表示全局参考帧."""

    name: str
    handed: str = "right"   # right / left
    z_up: bool = True
    note: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "handed": self.handed, "z_up": self.z_up, "note": self.note}


@dataclass(frozen=True)
class RigidTransform:
    """刚体变换: ``p_parent = R @ p_child + t``."""

    rotation: Mat3 = IDENTITY
    translation: Vec3 = ZERO_T

    def __post_init__(self) -> None:
        object.__setattr__(self, "rotation", check_rotation(self.rotation))

    @property
    def det(self) -> float:
        return det(self.rotation)

    def apply_point(self, p: Sequence[float]) -> Vec3:
        return vec_add(mat_vec(self.rotation, _as_vec3(p)), self.translation)

    def apply_vector(self, v: Sequence[float]) -> Vec3:
        return mat_vec(self.rotation, _as_vec3(v))

    def inverse(self) -> "RigidTransform":
        rt = transpose(self.rotation)
        return RigidTransform(rt, mat_vec(rt, tuple(-x for x in self.translation)))  # type: ignore[arg-type]

    def compose(self, other: "RigidTransform") -> "RigidTransform":
        """``self ∘ other``: 先 other (other 的子帧) 再 self."""
        return RigidTransform(
            mat_mul(self.rotation, other.rotation),
            vec_add(mat_vec(self.rotation, other.translation), self.translation),
        )

    def to_dict(self) -> dict:
        return {
            "rotation": [list(r) for r in self.rotation],
            "translation": list(self.translation),
            "det": self.det,
            "orthonormality_error": orthonormality_error(self.rotation),
        }


def _as_vec3(p: Sequence[float]) -> Vec3:
    if not (isinstance(p, (tuple, list)) and len(p) == 3):
        raise FrameError(f"三维点/矢量需长度 3, 收到 {p!r}")
    out = tuple(float(x) for x in p)
    if not all(math.isfinite(x) for x in out):
        raise FrameError(f"含非有限分量: {p!r}")
    return out  # type: ignore[return-value]


@dataclass
class FrameGraph:
    """帧之间的相对位姿图 (有向边可反向遍历), 提供最短路变换与追踪."""

    frames: Dict[str, Frame] = field(default_factory=dict)
    _edges: Dict[Tuple[str, str], RigidTransform] = field(default_factory=dict)

    def add_frame(self, frame: Frame) -> Frame:
        if frame.name in self.frames:
            raise FrameError(f"帧重复登记: {frame.name!r}")
        self.frames[frame.name] = frame
        return frame

    def add_relative(self, child: str, parent: str, transform: RigidTransform) -> None:
        """登记 ``child -> parent`` 的子帧到父帧变换."""
        for name in (child, parent):
            if name not in self.frames:
                raise FrameError(f"未登记帧: {name!r}")
        if (child, parent) in self._edges:
            raise FrameError(f"帧关系重复登记: {child} -> {parent}")
        self._edges[(child, parent)] = transform

    def direct(self, child: str, parent: str) -> RigidTransform:
        if (child, parent) in self._edges:
            return self._edges[(child, parent)]
        if (parent, child) in self._edges:
            return self._edges[(parent, child)].inverse()
        raise FrameError(f"无直接帧变换: {child} -> {parent}")

    def neighbours(self, name: str) -> List[Tuple[str, RigidTransform]]:
        out: List[Tuple[str, RigidTransform]] = []
        for (child, parent), tf in self._edges.items():
            if child == name:
                out.append((parent, tf))
            elif parent == name:
                out.append((child, tf.inverse()))
        return out

    def path(self, src: str, dst: str) -> List[str]:
        """BFS 最短路帧序列 (含首尾); 不可达 -> :class:`FrameError`."""
        for name in (src, dst):
            if name not in self.frames:
                raise FrameError(f"未登记帧: {name!r}")
        if src == dst:
            return [src]
        prev: Dict[str, str] = {src: src}
        queue: List[str] = [src]
        while queue:
            cur = queue.pop(0)
            for nxt, _ in self.neighbours(cur):
                if nxt in prev:
                    continue
                prev[nxt] = cur
                if nxt == dst:
                    hops = [dst]
                    while hops[-1] != src:
                        hops.append(prev[hops[-1]])
                    return list(reversed(hops))
                queue.append(nxt)
        raise FrameError(f"帧不可达: {src} -> {dst}")

    def transform(self, src: str, dst: str) -> RigidTransform:
        """``src -> dst`` 的合成刚体变换 (沿最短路复合)."""
        hops = self.path(src, dst)
        tf = RigidTransform()
        for a, b in zip(hops, hops[1:]):
            tf = tf.compose(self.direct(a, b))
        return tf

    def transform_point(self, p: Sequence[float], src: str, dst: str) -> Vec3:
        return self.transform(src, dst).apply_point(p)

    def transform_vector(self, v: Sequence[float], src: str, dst: str) -> Vec3:
        return self.transform(src, dst).apply_vector(v)

    def trace(self, src: str, dst: str) -> List[dict]:
        """逐跳记录变换 (审计/报告用)."""
        hops = self.path(src, dst)
        return [
            {"from": a, "to": b, **self.direct(a, b).to_dict()} for a, b in zip(hops, hops[1:])
        ]


def standard_frames_placeholder() -> FrameGraph:
    """空图 (测试夹具用). 具体帧由数据文件经 :func:`load_frames` 注入."""
    return FrameGraph()


def load_frames(payload: dict) -> FrameGraph:
    """从 JSON 载荷构建帧图.

    格式::

        {"frames": [{"name": "a", "handed": "right", "z_up": true, "note": "..."}],
         "relations": [{"child": "b", "parent": "a",
                        "rotation": [[...],[...],[...]], "translation": [0,0,0]}]}
    """
    if not isinstance(payload, dict):
        raise FrameError("帧定义必须是 JSON 对象")
    g = FrameGraph()
    for f in payload.get("frames", []):
        g.add_frame(Frame(
            name=f["name"],
            handed=f.get("handed", "right"),
            z_up=bool(f.get("z_up", True)),
            note=f.get("note", ""),
        ))
    for rel in payload.get("relations", []):
        rot = rel.get("rotation", [list(r) for r in IDENTITY])
        tr = rel.get("translation", list(ZERO_T))
        g.add_relative(rel["child"], rel["parent"], RigidTransform(
            tuple(tuple(float(v) for v in row) for row in rot),  # type: ignore[arg-type]
            tuple(float(v) for v in tr),  # type: ignore[arg-type]
        ))
    return g


def rotation_named(name: str) -> Mat3:
    """按名取常规旋转 (数据文件里的 ``rotation: "ship_to_earth"`` 用)."""
    table = {
        "identity": IDENTITY,
        # z-up <-> y-up: x -> x, z -> y, y -> -z (右手系保持)
        "z_up_to_y_up": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
    }
    try:
        return check_rotation(table[name], label=name)
    except KeyError as exc:
        raise FrameError(f"未登记的命名旋转: {name!r}") from exc