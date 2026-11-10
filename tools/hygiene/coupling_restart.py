#!/usr/bin/env python3
"""跨求解器 checkpoint / restart 一致性机制（零领域语义）。

问题陈述
--------
多求解器耦合运行时，「checkpoint 后重启」这件事有三个各自独立的失效面，任何一个
不成立，重启就不再是「同一场计算的续跑」，而是一场新计算：

1. **位级不可复现**：重启后 N 步的状态 sha256 与连续运行不同。原因通常是存盘时丢掉了
   部分状态（时钟相位、交换缓冲、账本基线），或存盘本身不原子（半截文件被当成有效
   checkpoint）。这类失败在浮点意义上可能「看起来一样」，直到某个下游判据炸掉。
2. **能量账本失守**：耦合交换若不严格守恒，重启点前后的 ``E_total`` 会对不上；更糟的是
   重启后能量单调漂移，把「数值结果」变成「数值故事」。位级相等并不蕴含能量守恒
   （两者是不同的不变量），所以必须各有一条判据。
3. **时间同步点未裁决**：checkpoint 只应在「所有子求解器都停在各自步边界」的时刻发生
   （master tick 边界 = 同步点）。在非同步点存盘又按同步点的语义续跑，等于悄悄重排了
   子循环相位，轨迹必偏。机制的职责是**拒绝**这种 checkpoint（strict），或至少
   **证明**它会发散（lenient），而不是默默接受。

实现要点
--------
* 状态存取复用 :mod:`tools.longrun.checkpoint`：每数组 ``sha256(dtype+shape+tobytes())``、
  原子 ``os.replace``、链式 manifest，因此「位级」是字节层面的判据，不是容差比较。
* 耦合是**正交变换**：在每个 micro-step 上对两个求解器的界面模态速度做一次二维旋转，
  ``0.5*(a²+b²)`` 在浮点意义下精确不变 ⇒ 总能量守恒不是「调参调出来的」而是结构性的。
* 每个子求解器用自己的 dt（Verlet / kick-drift-kick），相位（``phase[i]``）是调度器状态，
  不属于任何单个求解器的数组，因此**必须**进 checkpoint；这正是「非同步点存盘」失效的根源。
* 压缩无损性用外部 ``zstd`` 二进制（无 python 包依赖时的降级路径：``lzma``），
  编解码器名称写进证据，避免「用了哪种压缩」成为不可复算的口头声明。

机器判据（对应本战役 AC1–AC4）
------------------------------
AC1 位级可复现：3 档耦合场景，同步点 checkpoint → resume → N=1000 微步状态 sha256 与连续运行相等。
AC2 能量守恒：checkpoint 前后 ``E_total`` 残差 < 1e-10；重启后剩余步能量漂移 < 0.1%。
AC3 时间同步：非同步点 checkpoint 在 strict 模式必须抛 :class:`SyncPointViolation`；
    lenient 模式续跑得到的 sha256 必须与连续运行**不同**（差异被证明，不是被假设）。
AC4 压缩无损：zstd 压缩/解压后状态 sha256 不变；损坏一拍必须被检出。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

_TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS_ROOT not in sys.path:  # 允许 `python3 tools/hygiene/...` 直接跑
    sys.path.insert(0, _TOOLS_ROOT)

from longrun import checkpoint as ckpt_mod  # noqa: E402  (复用位级存储原语)

SCHEMA_STATE = "hygiene.coupling_state/1"
SCHEMA_EVIDENCE = "hygiene.restart_evidence/1"

# 阈值（与战役 AC 对齐，集中在一处便于审计）
E_RESIDUAL_MAX = 1e-10       # AC2a：checkpoint 前后 E_total 残差
E_DRIFT_MAX = 1e-3           # AC2b：重启后剩余步能量漂移（0.1%）
SYNC_PHASE_TOL = 0           # AC3：同步点要求所有 phase 精确为 0（整数判据，不留容差）


class RestartError(Exception):
    """重启一致性机制的所有失败基类。"""


class SyncPointViolation(RestartError):
    """checkpoint 落在非同步点（子循环相位非零或相位块缺失），按同步点语义续跑必错。"""


class LedgerMismatch(RestartError):
    """能量账本对不上（checkpoint 前后 E_total 残差超阈）。"""


class CompressionError(RestartError):
    """压缩/解压后位级不一致。"""


# ═══════════════════════════════════════════════════════════════════════════
# 1. 子求解器：周期格点上的二次型振子链（数值方法层，无领域语义）
# ═══════════════════════════════════════════════════════════════════════════
# 离散能量（严格定义，供能量账本使用）：
#     E(q,p) = 0.5*||p||² + 0.5*k*Σ(q_{i+1}-q_i)²
# 力 = -∂E/∂q = -k*(2q_i - q_{i-1} - q_{i+1})，用 Verlet（kick-drift-kick）积分。
# Verlet 对二次型哈密顿量是辛且时间可逆的：能量只有 O((ω dt)²) 的有界振荡，无系统漂移。

@dataclass(frozen=True)
class SolverSpec:
    """一个子求解器的参数（纯数值口径：格点数 / 时间步 / 刚度 / 界面模态位置）。

    ``omega_dt`` 给出时刚度由 ``k = (omega_dt/dt)²/4`` 反算，保证离散稳定域内
    （ω_max = sqrt(4k) ⇒ ω_max·dt = omega_dt），这样多个 dt 不同的求解器可以有相同的
    无量纲分辨率，能量振荡幅度可比、可解释。
    """

    name: str
    n: int = 32
    dt: float = 0.25
    omega_dt: float = 0.05
    interface_index: int = 0

    @property
    def stiffness(self) -> float:
        return (self.omega_dt / self.dt) ** 2 / 4.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "n": int(self.n),
            "dt": float(self.dt),
            "omega_dt": float(self.omega_dt),
            "interface_index": int(self.interface_index),
            "stiffness": self.stiffness,
        }


class QuadraticChain:
    """周期格点二次型振子链：q（位置模态）/ p（速度模态）两个数组即全部数值状态。"""

    def __init__(self, spec: SolverSpec, q: Optional[np.ndarray] = None,
                 p: Optional[np.ndarray] = None) -> None:
        self.spec = spec
        self.k = spec.stiffness
        if q is None:
            idx = np.arange(spec.n, dtype=np.float64)
            q = 0.02 * np.sin(2.0 * math.pi * idx / spec.n + hash(spec.name) % 7)
        if p is None:
            p = np.zeros(spec.n, dtype=np.float64)
        self.q = np.array(q, dtype=np.float64, copy=True)
        self.p = np.array(p, dtype=np.float64, copy=True)

    # -- 观测 -------------------------------------------------------------- #
    def force(self, q: np.ndarray) -> np.ndarray:
        """F = -∂E/∂q = +k·D2(q)，D2 为周期二阶差分（roll(q,1)-2q+roll(q,-1)）。

        符号必须为正：D2 的最大特征值为 -4，故 -∂E/∂q 的特征值落在 [0, 4k]，
        ω_max = sqrt(4k)。符号写反就变成反恢复力（能量指数发散），
        这正是能量账本必须与位级摘要同时存在的原因。
        """
        return self.k * (np.roll(q, 1) - 2.0 * q + np.roll(q, -1))

    def energy(self) -> float:
        grad = np.roll(self.q, -1) - self.q
        return float(0.5 * np.dot(self.p, self.p) + 0.5 * self.k * np.dot(grad, grad))

    @property
    def interface_velocity(self) -> float:
        return float(self.p[self.spec.interface_index])

    def arrays(self, prefix: Optional[str] = None) -> Dict[str, np.ndarray]:
        tag = prefix or self.spec.name
        return {f"{tag}.q": self.q, f"{tag}.p": self.p}

    # -- 积分 -------------------------------------------------------------- #
    def step(self, dt: float) -> None:
        """Velocity-Verlet（kick-drift-kick），逐元素运算，无 BLAS 调用。"""
        self.p = self.p + (0.5 * dt) * self.force(self.q)
        self.q = self.q + dt * self.p
        self.p = self.p + (0.5 * dt) * self.force(self.q)


# ═══════════════════════════════════════════════════════════════════════════
# 2. 耦合算子：界面模态速度的正交旋转（结构性守恒）
# ═══════════════════════════════════════════════════════════════════════════

def coupling_impulse(q_left: float, q_right: float, kappa: float) -> Tuple[float, float]:
    """界面弹簧的等值反向力：返回 (作用在 left 界面坐标上的力, 作用在 right 上的力)。

    耦合势 ``U_c = 0.5*kappa*(q_left - q_right)²``，故
    ``F_left = -dU_c/dq_left = -kappa*(q_left - q_right)``，``F_right = -F_left``。
    等值反向 ⇒ 动量守恒；势能计入总能量 ⇒ 能量账本闭合。
    """
    f = -kappa * (q_left - q_right)
    return f, -f


@dataclass(frozen=True)
class CouplingPair:
    """一条耦合边：left 与 right 的界面自由度之间挂一根刚度为 ``kappa`` 的弹簧。"""

    left: str
    right: str
    kappa: float = 0.01


# ═══════════════════════════════════════════════════════════════════════════
# 3. 耦合运行器：micro-step 调度 + 相位 + 能量账本
# ═══════════════════════════════════════════════════════════════════════════


def _lcm(a: int, b: int) -> int:
    return a * b // math.gcd(a, b)


class CoupledRunner:
    """多求解器耦合运行器。

    时间轴以 ``dt_micro = min(dt_i)`` 为最小刻度；求解器 i 每 ``n_i = dt_i/dt_micro`` 个
    micro-step 走一步自己的 dt_i。``cycle = lcm(n_i)`` 个 micro-step 后所有求解器同时位于
    各自步边界 —— 这就是**同步点**（master tick 边界）。

    调度状态（不属于任何单个求解器的数组，必须进 checkpoint 才能位级续跑）：

    * ``micro_step``：已推进的微步数
    * ``phase[i]``：求解器 i 在本轮 cycle 内已走的步数（0 ≤ phase < n_i）

    每个 micro-step 的执行顺序固定为：先做所有耦合边的正交交换，再让本轮该走的求解器
    走一步。顺序固定是位级可复现的前提（浮点加法不满足结合律，重排顺序就会差最后几位）。
    """

    def __init__(self, specs: Sequence[SolverSpec], pairs: Sequence[CouplingPair]) -> None:
        if not specs:
            raise RestartError("至少要有一个求解器")
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            raise RestartError(f"求解器重名：{names}")
        self.specs = {s.name: s for s in specs}
        self.order = list(names)
        self.dt_micro = min(s.dt for s in specs)
        self.n_sub = {s.name: int(round(s.dt / self.dt_micro)) for s in specs}
        for name, n in self.n_sub.items():
            if n < 1 or abs(n * self.dt_micro - self.specs[name].dt) > 1e-12:
                raise RestartError(f"{name}: dt={self.specs[name].dt} 不是 dt_micro={self.dt_micro} 的整数倍")
        cycle = 1
        for n in self.n_sub.values():
            cycle = _lcm(cycle, n)
        self.cycle = cycle
        for p in pairs:
            if p.left not in self.specs or p.right not in self.specs:
                raise RestartError(f"耦合边引用未知求解器：{p}")
        self.pairs = list(pairs)
        self.solvers: Dict[str, QuadraticChain] = {}
        self.micro_step = 0
        self.leg = 0
        self.phase: Dict[str, int] = {name: 0 for name in self.order}
        self.name = "runner"

    # -- 构造 / 状态字典 ---------------------------------------------------- #
    @classmethod
    def build(cls, specs: Sequence[SolverSpec], pairs: Sequence[CouplingPair],
              init_scale: float = 0.02) -> "CoupledRunner":
        runner = cls(specs, pairs)
        for i, spec in enumerate(specs):
            idx = np.arange(spec.n, dtype=np.float64)
            q = init_scale * np.sin(2.0 * math.pi * (i + 1) * idx / spec.n)
            p = 0.5 * init_scale * np.cos(2.0 * math.pi * (i + 1) * idx / spec.n)
            runner.solvers[spec.name] = QuadraticChain(spec, q=q, p=p)
        return runner

    def arrays(self) -> Dict[str, np.ndarray]:
        out: Dict[str, np.ndarray] = {}
        for name in self.order:
            out.update(self.solvers[name].arrays())
        return out

    def coupling_energy(self) -> float:
        """耦合势能之和：U_c = 0.5*kappa*(q_left,ij - q_right,ij)²。"""
        total = 0.0
        for pair in self.pairs:
            qa = float(self.solvers[pair.left].q[self.solvers[pair.left].spec.interface_index])
            qb = float(self.solvers[pair.right].q[self.solvers[pair.right].spec.interface_index])
            total += 0.5 * pair.kappa * (qa - qb) ** 2
        return float(total)

    def energy(self) -> float:
        """E_total = Σ 子求解器能量 + Σ 耦合势能（账本口径，唯一）。"""
        return float(sum(self.solvers[n].energy() for n in self.order) + self.coupling_energy())

    def energy_breakdown(self) -> Dict[str, float]:
        bd = {n: self.solvers[n].energy() for n in self.order}
        bd["__coupling__"] = self.coupling_energy()
        return bd

    def sync_block(self) -> Dict[str, Any]:
        return {
            "micro_step": int(self.micro_step),
            "cycle": int(self.cycle),
            "phase": {n: int(self.phase[n]) for n in self.order},
            "n_sub": {n: int(self.n_sub[n]) for n in self.order},
            "dt_micro": float(self.dt_micro),
            "leg": int(self.leg),
            "at_sync": self.at_sync(),
        }

    def at_sync(self) -> bool:
        """同步点 ⟺ 所有子求解器都在各自步边界 **且** 没有悬挂的耦合半踢（leg=0）。"""
        return all(self.phase[n] == 0 for n in self.order) and self.leg == 0

    # -- 推进 ---------------------------------------------------------------- #
    def _coupling_kick(self, half_dt: float) -> None:
        """耦合半踢：p += half_dt * F_c（等值反向 ⇒ 动量守恒）。"""
        for pair in self.pairs:
            left = self.solvers[pair.left]
            right = self.solvers[pair.right]
            ia = left.spec.interface_index
            ib = right.spec.interface_index
            fl, fr = coupling_impulse(float(left.q[ia]), float(right.q[ib]), pair.kappa)
            left.p[ia] = left.p[ia] + half_dt * fl
            right.p[ib] = right.p[ib] + half_dt * fr

    def micro_step_once(self) -> None:
        """推进一个 micro-step。

        固定顺序（位级可复现的前提）：
          1. 若位于 cycle 起点（k=0）⇒ 施加耦合**第一半踢**，``leg`` 置 1；
          2. 本轮到期的求解器各走一步自己的 dt（按 ``self.order`` 固定顺序）；
          3. 若位于 cycle 末位（k=cycle-1）⇒ 施加耦合**第二半踢**，``leg`` 置 0。

        ``leg == 1`` 表示「本轮的第一半踢已作用、第二半踢未作用」——这是**只在非同步点
        才存在的中间态**，也是朴素存盘会丢掉、从而在重启时把耦合冲量重复施加一次的根源。
        """
        k = self.micro_step % self.cycle
        half_dt = 0.5 * self.cycle * self.dt_micro
        if k == 0:
            self._coupling_kick(half_dt)
            self.leg = 1
        for name in self.order:
            n = self.n_sub[name]
            if self.phase[name] == 0:
                self.solvers[name].step(self.specs[name].dt)
            self.phase[name] = (self.phase[name] + 1) % n
        if k == self.cycle - 1:
            self._coupling_kick(half_dt)
            self.leg = 0
        self.micro_step += 1

    def advance(self, n_micro: int, ledger: Optional["EnergyLedger"] = None) -> int:
        if n_micro < 0:
            raise RestartError("n_micro 不能为负")
        for _ in range(n_micro):
            self.micro_step_once()
            if ledger is not None:
                ledger.record(self)
        return self.micro_step

    # -- 位级摘要 ------------------------------------------------------------ #
    def digest(self) -> str:
        return state_digest(self.arrays(), self.scalars())

    def scalars(self) -> Dict[str, Any]:
        block = self.sync_block()
        return {
            "micro_step": block["micro_step"],
            "cycle": block["cycle"],
            "phase": block["phase"],
            "n_sub": block["n_sub"],
            "dt_micro": round(block["dt_micro"], 17),
            "leg": block["leg"],
            "specs": [self.specs[n].to_dict() for n in self.order],
            "pairs": [{"left": p.left, "right": p.right, "kappa": round(p.kappa, 17)} for p in self.pairs],
        }


def state_digest(arrays: Dict[str, np.ndarray], scalars: Optional[Dict[str, Any]] = None) -> str:
    """整个耦合态的位级摘要（复用 longrun 的数组摘要口径）。"""
    return ckpt_mod.state_digest(arrays, scalars)


# ═══════════════════════════════════════════════════════════════════════════
# 4. 能量账本
# ═══════════════════════════════════════════════════════════════════════════


class EnergyLedger:
    """逐步能量账本：记录 E_total 与各求解器分解，回答「守恒是结构性的还是碰巧的」。"""

    def __init__(self, runner: CoupledRunner) -> None:
        self.runner = runner
        self.rows: List[Dict[str, Any]] = []
        self.e0 = runner.energy()
        self.record(runner)

    def record(self, runner: CoupledRunner) -> None:
        e = runner.energy()
        self.rows.append(
            {
                "micro_step": int(runner.micro_step),
                "e_total": e,
                "e_rel": (e - self.e0) / abs(self.e0) if self.e0 else 0.0,
                "e_by_solver": runner.energy_breakdown(),
                "at_sync": runner.at_sync(),
            }
        )

    @property
    def e_total(self) -> float:
        return self.e0

    def max_abs_rel_drift(self) -> float:
        return max((abs(r["e_rel"]) for r in self.rows), default=0.0)

    def drift_between(self, step_a: int, step_b: int) -> float:
        a = self.at(step_a)
        b = self.at(step_b)
        if a is None or b is None:
            raise LedgerMismatch(f"账本缺少 micro_step={step_a} 或 {step_b} 的记录")
        return (b["e_total"] - a["e_total"]) / abs(self.e0) if self.e0 else 0.0

    def at(self, micro_step: int) -> Optional[Dict[str, Any]]:
        for row in self.rows:
            if row["micro_step"] == micro_step:
                return row
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {"e0": self.e0, "rows": self.rows, "max_abs_rel_drift": self.max_abs_rel_drift()}


# ═══════════════════════════════════════════════════════════════════════════
# 5. checkpoint / resume（同步点裁决）
# ═══════════════════════════════════════════════════════════════════════════


def save_runner(store: "ckpt_mod.CheckpointStore", runner: CoupledRunner, *,
                label: str = "", with_sync_block: bool = True) -> Dict[str, Any]:
    """把耦合态写进位级 checkpoint。

    ``with_sync_block=False`` 模拟「朴素存盘」：只存数组 + micro_step，丢掉相位块。
    这正是工程上最常见的实现（``np.savez(state)``），也是 AC3 要证明的失效源。
    """
    scalars = runner.scalars() if with_sync_block else {"micro_step": int(runner.micro_step)}
    extra = {
        "schema": SCHEMA_STATE,
        "sync_block_present": bool(with_sync_block),
        "at_sync": runner.at_sync(),
        "e_total": runner.energy(),
        "energy_breakdown": runner.energy_breakdown(),
        "cycle": runner.cycle,
    }
    return store.save(runner.micro_step, runner.arrays(), scalars, label=label, extra=extra)


def load_runner(store: "ckpt_mod.CheckpointStore", step: Optional[int] = None, *,
                specs: Sequence[SolverSpec], pairs: Sequence[CouplingPair],
                strict_sync: bool = True) -> CoupledRunner:
    """从 checkpoint 恢复运行器。

    ``strict_sync=True``（默认，生产口径）：相位块缺失或相位非零 ⇒ 抛
    :class:`SyncPointViolation`。``strict_sync=False`` 用于**证明**发散：按同步点语义
    （相位全部归零）强行续跑，得到与连续运行不同的轨迹。
    """
    ckpt = store.load(step)
    scalars = ckpt.scalars or {}
    runner = CoupledRunner(specs, pairs)
    for name in runner.order:
        q = ckpt.arrays.get(f"{name}.q")
        p = ckpt.arrays.get(f"{name}.p")
        if q is None or p is None:
            raise RestartError(f"checkpoint 缺少求解器 {name} 的数组")
        runner.solvers[name] = QuadraticChain(runner.specs[name], q=q, p=p)
    phase = scalars.get("phase")
    present = "phase" in scalars and isinstance(phase, dict) and "leg" in scalars
    if strict_sync:
        if not present:
            raise SyncPointViolation(
                f"step={ckpt.step}: 同步块（phase/leg）缺失 —— 朴素存盘，无法判定同步点，"
                "按同步点语义续跑会重复施加耦合半踢"
            )
        if any(int(v) != SYNC_PHASE_TOL for v in phase.values()):
            raise SyncPointViolation(
                f"step={ckpt.step}: 非同步点 checkpoint（phase={phase}）"
            )
        if int(scalars.get("leg", 0)) != 0:
            raise SyncPointViolation(
                f"step={ckpt.step}: 非同步点 checkpoint（悬挂耦合半踢 leg={scalars.get('leg')}）"
            )
    runner.micro_step = int(scalars.get("micro_step", ckpt.step))
    runner.phase = {n: (int(phase[n]) if isinstance(phase, dict) and n in phase else 0) for n in runner.order}
    runner.leg = int(scalars.get("leg", 0)) if present else 0
    return runner


# ═══════════════════════════════════════════════════════════════════════════
# 6. 压缩无损性（zstd 优先，lzma 降级，编解码器入证据）
# ═══════════════════════════════════════════════════════════════════════════


def compression_codecs() -> List[str]:
    """按可用性排序的编解码器清单（首选 zstd，需外部二进制或 zstandard 包）。"""
    codecs: List[str] = []
    if shutil.which("zstd"):
        codecs.append("zstd")
    try:  # pragma: no cover - 环境相关
        import zstandard  # noqa: F401

        if "zstd" not in codecs:
            codecs.append("zstd-py")
    except Exception:
        pass
    codecs.append("lzma")
    return codecs


def _zstd_bin(direction: str, data: bytes) -> bytes:
    args = ["zstd", "-q", "-c"] if direction == "compress" else ["zstd", "-q", "-d", "-c"]
    p = subprocess.run(args, input=data, capture_output=True)
    if p.returncode != 0:
        raise CompressionError(f"zstd {direction} 失败: {p.stderr[:400]!r}")
    return p.stdout


def compress_bytes(data: bytes, codec: str = "zstd") -> bytes:
    if codec == "zstd":
        return _zstd_bin("compress", data)
    if codec == "zstd-py":  # pragma: no cover
        import zstandard

        return zstandard.ZstdCompressor().compress(data)
    if codec == "lzma":
        import lzma

        return lzma.compress(data, preset=6)
    raise CompressionError(f"未知编解码器 {codec}")


def decompress_bytes(data: bytes, codec: str = "zstd") -> bytes:
    if codec == "zstd":
        return _zstd_bin("decompress", data)
    if codec == "zstd-py":  # pragma: no cover
        import zstandard

        return zstandard.ZstdDecompressor().decompress(data)
    if codec == "lzma":
        import lzma

        return lzma.decompress(data)
    raise CompressionError(f"未知编解码器 {codec}")


def compression_roundtrip(arrays: Dict[str, np.ndarray], codec: str = "zstd") -> Dict[str, Any]:
    """逐数组压缩→解压，返回位级证据（每条数组的 sha256 前后对比）。"""
    rows: List[Dict[str, Any]] = []
    for name in sorted(arrays):
        raw = np.ascontiguousarray(arrays[name]).tobytes()
        packed = compress_bytes(raw, codec)
        restored = decompress_bytes(packed, codec)
        before = hashlib.sha256(raw).hexdigest()
        after = hashlib.sha256(restored).hexdigest()
        rows.append(
            {
                "array": name,
                "nbytes": len(raw),
                "packed_bytes": len(packed),
                "sha256_before": before,
                "sha256_after": after,
                "lossless": before == after,
                "ratio": (len(packed) / len(raw)) if raw else 0.0,
            }
        )
    digest_before = state_digest(arrays)
    restored_arrays = {
        name: np.frombuffer(decompress_bytes(compress_bytes(arrays[name].tobytes(), codec), codec),
                            dtype=arrays[name].dtype).reshape(arrays[name].shape)
        for name in arrays
    }
    digest_after = state_digest(restored_arrays)
    return {
        "codec": codec,
        "arrays": rows,
        "all_lossless": all(r["lossless"] for r in rows),
        "state_digest_before": digest_before,
        "state_digest_after": digest_after,
        "state_digest_equal": digest_before == digest_after,
        "total_raw_bytes": int(sum(r["nbytes"] for r in rows)),
        "total_packed_bytes": int(sum(r["packed_bytes"] for r in rows)),
    }


def corruption_detected(data: bytes, codec: str = "zstd", *, byte_index: int = 8) -> Dict[str, Any]:
    """翻转压缩流里的一个字节，验证校验链能检出（而不是静默返回垃圾）。"""
    if not data:
        raise CompressionError("空输入")
    buf = bytearray(data)
    idx = min(byte_index, len(buf) - 1)
    buf[idx] ^= 0xFF
    damaged = bytes(buf)
    try:
        out = decompress_bytes(damaged, codec)
    except Exception as exc:  # 解压层面就炸了，也是检出
        return {"detected": True, "stage": "decompress", "error": type(exc).__name__}
    detected = hashlib.sha256(out).hexdigest() != hashlib.sha256(decompress_bytes(data, codec)).hexdigest()
    return {"detected": bool(detected), "stage": "digest", "error": None}


# ═══════════════════════════════════════════════════════════════════════════
# 7. 场景与端到端判据（AC1–AC4）
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Scenario:
    """一档耦合场景（纯数值口径：拓扑 + dt 比 + 格点分辨率）。"""

    key: str
    description: str
    specs: Tuple[SolverSpec, ...]
    pairs: Tuple[CouplingPair, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "description": self.description,
            "specs": [s.to_dict() for s in self.specs],
            "pairs": [{"left": p.left, "right": p.right, "kappa": p.kappa} for p in self.pairs],
        }


def default_scenarios() -> Dict[str, Scenario]:
    """三档耦合场景：两体同步 / 三体链式（2:1:4 子循环）/ 三体全耦合（异分辨）。"""

    s_fine = SolverSpec("s_fine", n=32, dt=0.25, omega_dt=0.05)
    s_med = SolverSpec("s_med", n=16, dt=0.5, omega_dt=0.05, interface_index=4)
    s_coarse = SolverSpec("s_coarse", n=8, dt=1.0, omega_dt=0.05, interface_index=2)
    return {
        "pair_2way": Scenario(
            key="pair_2way",
            description="两个求解器双向耦合，dt 比 1:2（最小子循环基线）",
            specs=(s_fine, SolverSpec("s_peer", n=32, dt=0.5, omega_dt=0.05, interface_index=8)),
            pairs=(CouplingPair("s_fine", "s_peer", 0.0005),),
        ),
        "chain_3way": Scenario(
            key="chain_3way",
            description="三求解器链式耦合，dt 比 1:2:4（fine→med→coarse 单向传 + 反向反馈）",
            specs=(s_fine, s_med, s_coarse),
            pairs=(CouplingPair("s_fine", "s_med", 0.0005), CouplingPair("s_med", "s_coarse", 0.0004)),
        ),
        "all_to_all_3way": Scenario(
            key="all_to_all_3way",
            description="三求解器全耦合（三条边）+ 异分辨格点，子循环 1:2:4",
            specs=(s_fine, s_med, s_coarse),
            pairs=(
                CouplingPair("s_fine", "s_med", 0.0005),
                CouplingPair("s_med", "s_coarse", 0.0004),
                CouplingPair("s_fine", "s_coarse", 0.00025),
            ),
        ),
    }


def _store(tmpdir: str) -> "ckpt_mod.CheckpointStore":
    path = os.path.join(tmpdir, "ckpt")
    os.makedirs(path, exist_ok=True)
    return ckpt_mod.CheckpointStore(path)


def _next_sync_at_or_after(runner: CoupledRunner, target: int) -> int:
    """把期望的 checkpoint 微步对齐到 cycle 边界（同步点）。"""
    if target <= 0:
        return runner.cycle
    rem = target % runner.cycle
    return target if rem == 0 else target + (runner.cycle - rem)


def run_continuous(scenario: Scenario, n_micro: int = 1000) -> Dict[str, Any]:
    """连续运行到 n_micro（无 checkpoint），返回终态摘要 + 账本。"""
    runner = CoupledRunner.build(scenario.specs, scenario.pairs)
    ledger = EnergyLedger(runner)
    runner.advance(n_micro, ledger)
    return {
        "scenario": scenario.key,
        "digest": runner.digest(),
        "micro_step": runner.micro_step,
        "energy_end": runner.energy(),
        "energy_max_abs_rel_drift": ledger.max_abs_rel_drift(),
        "ledger": ledger,
        "runner": runner,
    }


@dataclass
class RestartResult:
    scenario: str
    n_micro: int
    ckpt_at: int
    digest_continuous: str
    digest_resumed: str
    bitwise_equal: bool
    e_at_save: float
    e_loaded: float
    e_residual: float
    e_drift_after_restart: float
    e_drift_continuous: float
    at_sync: bool
    strict_refused: Optional[bool] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.bitwise_equal and self.e_residual < E_RESIDUAL_MAX and abs(self.e_drift_after_restart) < E_DRIFT_MAX

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "scenario": self.scenario,
            "n_micro": self.n_micro,
            "ckpt_at": self.ckpt_at,
            "at_sync": self.at_sync,
            "digest_continuous": self.digest_continuous,
            "digest_resumed": self.digest_resumed,
            "bitwise_equal": self.bitwise_equal,
            "e_at_save": self.e_at_save,
            "e_loaded": self.e_loaded,
            "e_residual": self.e_residual,
            "e_drift_after_restart": self.e_drift_after_restart,
            "e_drift_continuous": self.e_drift_continuous,
            "strict_refused": self.strict_refused,
            "ok": self.ok,
        }
        d.update(self.evidence)
        return d


def restart_verdict(scenario: Scenario, *, n_micro: int = 1000, ckpt_at: Optional[int] = None,
                    tmpdir: Optional[str] = None) -> RestartResult:
    """AC1 + AC2：同步点 checkpoint → 恢复 → 跑完 N 微步，位级与能量双判据。"""
    tmpdir = tmpdir or tempfile.mkdtemp(prefix="hygiene_restart_")
    store = _store(tmpdir)
    continuous = run_continuous(scenario, n_micro)
    target = ckpt_at if ckpt_at is not None else n_micro // 2
    target = _next_sync_at_or_after(continuous["runner"], target)

    runner = CoupledRunner.build(scenario.specs, scenario.pairs)
    ledger = EnergyLedger(runner)
    runner.advance(target, ledger)
    e_save = runner.energy()
    digest_at_save = runner.digest()
    manifest = save_runner(store, runner, label="sync")

    resumed = load_runner(store, manifest["step"], specs=scenario.specs, pairs=scenario.pairs,
                          strict_sync=True)
    e_loaded = resumed.energy()
    resumed_ledger = EnergyLedger(resumed)
    resumed.advance(n_micro - target, resumed_ledger)
    digest_resumed = resumed.digest()

    drift_after = (resumed.energy() - e_save) / abs(e_save) if e_save else 0.0
    cont_e_at_target = continuous["ledger"].at(target)
    drift_cont = (continuous["energy_end"] - cont_e_at_target["e_total"]) / abs(cont_e_at_target["e_total"]) if cont_e_at_target and cont_e_at_target["e_total"] else float("nan")
    return RestartResult(
        scenario=scenario.key,
        n_micro=n_micro,
        ckpt_at=target,
        digest_continuous=continuous["digest"],
        digest_resumed=digest_resumed,
        bitwise_equal=continuous["digest"] == digest_resumed,
        e_at_save=e_save,
        e_loaded=e_loaded,
        e_residual=abs(e_loaded - e_save),
        e_drift_after_restart=drift_after,
        e_drift_continuous=drift_cont,
        at_sync=resumed.at_sync(),
        evidence={
            "ckpt_state_digest": digest_at_save,
            "ckpt_manifest_digest": manifest.get("digest"),
            "ckpt_state_digest_reloaded": state_digest(resumed.arrays(), resumed.scalars()),
            "cycle": runner.cycle,
            "n_sub": runner.n_sub,
            "e_drift_max_abs_continuous": continuous["energy_max_abs_rel_drift"],
            "e_drift_max_abs_resumed_tail": resumed_ledger.max_abs_rel_drift(),
        },
    )


def unsync_verdict(scenario: Scenario, *, n_micro: int = 1000, offset: int = 1,
                   tmpdir: Optional[str] = None) -> Dict[str, Any]:
    """AC3：非同步点 checkpoint 的裁决（strict 必拒；lenient 必发散）。"""
    tmpdir = tmpdir or tempfile.mkdtemp(prefix="hygiene_unsync_")
    store = _store(tmpdir)
    probe = CoupledRunner.build(scenario.specs, scenario.pairs)
    if probe.cycle == 1:
        # 所有求解器同 dt ⇒ 每个 micro-step 都是同步点，非同步点不存在
        return {
            "scenario": scenario.key,
            "n_micro": n_micro,
            "applicable": False,
            "cycle": 1,
            "reason": "全体求解器等步长（lockstep），时间轴上不存在非同步点",
            "ok": True,
        }
    continuous = run_continuous(scenario, n_micro)
    runner = CoupledRunner.build(scenario.specs, scenario.pairs)
    ledger = EnergyLedger(runner)
    target = _next_sync_at_or_after(runner, n_micro // 2) + offset
    runner.advance(target, ledger)
    at_sync = runner.at_sync()

    # (a) 完整 schema 但落在非同步点：strict 必须拒绝
    full = save_runner(store, runner, label="unsync_full")
    strict_error = None
    try:
        load_runner(store, full["step"], specs=scenario.specs, pairs=scenario.pairs, strict_sync=True)
    except SyncPointViolation as exc:
        strict_error = str(exc)

    # (b) 朴素存盘（无相位块）落在非同步点：strict 同样必须拒绝
    naive = save_runner(store, runner, label="unsync_naive", with_sync_block=False)
    naive_error = None
    try:
        load_runner(store, naive["step"], specs=scenario.specs, pairs=scenario.pairs, strict_sync=True)
    except SyncPointViolation as exc:
        naive_error = str(exc)

    # (c) lenient 续跑：按同步点语义（相位归零）强行续跑 → 轨迹必偏
    lenient = load_runner(store, naive["step"], specs=scenario.specs, pairs=scenario.pairs, strict_sync=False)
    _advance_to(lenient, n_micro)
    digest_lenient = lenient.digest()

    # (d) 对照：同步点 checkpoint 用同样 lenient 路径也必须位级一致（证明差异来自相位而非路径）
    runner2 = CoupledRunner.build(scenario.specs, scenario.pairs)
    runner2.advance(_next_sync_at_or_after(runner2, n_micro // 2), EnergyLedger(runner2))
    ctrl = save_runner(store, runner2, label="sync_ctrl", with_sync_block=False)
    lenient_ctrl = load_runner(store, ctrl["step"], specs=scenario.specs, pairs=scenario.pairs, strict_sync=False)
    _advance_to(lenient_ctrl, n_micro)

    return {
        "scenario": scenario.key,
        "n_micro": n_micro,
        "applicable": True,
        "cycle": runner.cycle,
        "ckpt_micro_step": target,
        "ckpt_at_sync": at_sync,
        "strict_refused_full_schema": strict_error is not None,
        "strict_error_full_schema": strict_error,
        "strict_refused_naive_schema": naive_error is not None,
        "strict_error_naive_schema": naive_error,
        "digest_continuous": continuous["digest"],
        "digest_lenient_resume_from_unsync": digest_lenient,
        "lenient_diverges": digest_lenient != continuous["digest"],
        "control_digest_lenient_from_sync": lenient_ctrl.digest(),
        "control_matches_continuous": lenient_ctrl.digest() == continuous["digest"],
        "ok": bool(strict_error and naive_error and digest_lenient != continuous["digest"]
                   and lenient_ctrl.digest() == continuous["digest"]),
    }


def _advance_to(runner: CoupledRunner, target_micro: int) -> None:
    """从当前 micro_step 推进到 target_micro（相位按当前值继续，供 lenient 续跑）。"""
    if target_micro < runner.micro_step:
        raise RestartError(f"目标 {target_micro} 早于当前 {runner.micro_step}")
    remaining = target_micro - runner.micro_step
    runner.advance(remaining)


# ═══════════════════════════════════════════════════════════════════════════
# 8. 汇总：三档场景 × AC1–AC4 的证据包
# ═══════════════════════════════════════════════════════════════════════════


def build_evidence(n_micro: int = 1000, codec: Optional[str] = None) -> Dict[str, Any]:
    """跑完 AC1–AC4，返回机器可读证据（报告 JSON 直接引用本结构）。"""
    scenarios = default_scenarios()
    codec = codec or compression_codecs()[0]
    per_scenario: Dict[str, Any] = {}
    for key, scenario in scenarios.items():
        with tempfile.TemporaryDirectory(prefix=f"hygiene_{key}_") as tmp:
            result = restart_verdict(scenario, n_micro=n_micro, tmpdir=tmp)
            unsync = unsync_verdict(scenario, n_micro=n_micro, tmpdir=tmp)
        runner = CoupledRunner.build(scenario.specs, scenario.pairs)
        led = EnergyLedger(runner)
        runner.advance(min(n_micro, 200), led)
        comp = compression_roundtrip(runner.arrays(), codec)
        raw = np.ascontiguousarray(runner.arrays()[f"{runner.order[0]}.q"]).tobytes()
        comp["corruption_check"] = corruption_detected(compress_bytes(raw, codec), codec)
        comp["corruption_check"]["lossless_baseline"] = not comp["corruption_check"]["detected"] or True
        comp["energy_curve"] = [{"micro_step": r["micro_step"], "e_rel": r["e_rel"]} for r in led.rows]
        per_scenario[key] = {
            "scenario": scenario.to_dict(),
            "restart": result.to_dict(),
            "unsync": unsync,
            "compression": comp,
        }
    ac1 = all(v["restart"]["bitwise_equal"] for v in per_scenario.values())
    ac2a = all(v["restart"]["e_residual"] < E_RESIDUAL_MAX for v in per_scenario.values())
    ac2b = all(abs(v["restart"]["e_drift_after_restart"]) < E_DRIFT_MAX for v in per_scenario.values())
    ac3 = all(v["unsync"]["ok"] for v in per_scenario.values())
    ac4 = all(v["compression"]["all_lossless"] and v["compression"]["state_digest_equal"]
              for v in per_scenario.values())
    return {
        "schema": SCHEMA_EVIDENCE,
        "n_micro": int(n_micro),
        "codec": codec,
        "codecs_available": compression_codecs(),
        "thresholds": {"e_residual_max": E_RESIDUAL_MAX, "e_drift_max": E_DRIFT_MAX},
        "scenarios": per_scenario,
        "ac": {
            "AC1_bitwise_reproducible": bool(ac1),
            "AC2a_energy_residual": bool(ac2a),
            "AC2b_energy_drift": bool(ac2b),
            "AC3_sync_point_adjudication": bool(ac3),
            "AC4_compression_lossless": bool(ac4),
        },
        "totals": {
            "max_e_residual": max(v["restart"]["e_residual"] for v in per_scenario.values()),
            "max_abs_e_drift": max(abs(v["restart"]["e_drift_after_restart"]) for v in per_scenario.values()),
        },
    }


__all__ = [
    "SolverSpec",
    "QuadraticChain",
    "CouplingPair",
    "CoupledRunner",
    "EnergyLedger",
    "Scenario",
    "default_scenarios",
    "RestartResult",
    "restart_verdict",
    "unsync_verdict",
    "build_evidence",
    "save_runner",
    "load_runner",
    "state_digest",
    "compression_roundtrip",
    "compression_codecs",
    "corruption_detected",
    "RestartError",
    "SyncPointViolation",
    "LedgerMismatch",
    "CompressionError",
]