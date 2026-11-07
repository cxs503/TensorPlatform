"""harness 单元清单（spec）。

一个"单元"= harness 层里一个可独立部署的进程入口。清单是**机器可读的白名单**：
只有清单内的文件才允许被镜像、被门控、被部署 —— 门控仓的存在意义之一就是
杜绝"协调者随手 sed 一个 daemon"的无记录改动。

canary 模式：
    exec_dry_run  —— 在沙箱里以单进程真跑一遍（psql/网络全部被 shim 拦掉），
                     用于顶层即执行的小 daemon；能证明"改完还能启动并优雅降级"。
    symbol_probe  —— 只做 AST 符号探针（不执行模块）。用于顶层即副作用、
                     或需要真 LLM/真 DB 写权的长驻进程；执行式试跑会污染生产，
                     此时如实降级并记录 mode（不接受"看起来跑过了"的自述）。
"""

from __future__ import annotations

from dataclasses import dataclass

ROLES = ("dispatcher", "worker", "daemon")
CANARY_MODES = ("exec_dry_run", "symbol_probe")


@dataclass(frozen=True)
class HarnessUnit:
    """harness 层一个可部署单元。"""

    name: str
    role: str
    relpath: str
    canary: str = "symbol_probe"
    expect_symbols: tuple[str, ...] = ()
    expect_exit: tuple[int, ...] = (0,)
    timeout_s: int = 25
    note: str = ""

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"{self.name}: role must be one of {ROLES}")
        if self.canary not in CANARY_MODES:
            raise ValueError(f"{self.name}: canary must be one of {CANARY_MODES}")
        if self.canary == "symbol_probe" and not self.expect_symbols:
            raise ValueError(f"{self.name}: symbol_probe requires expect_symbols")
        if not self.relpath.endswith(".py"):
            raise ValueError(f"{self.name}: only python units are gated (got {self.relpath})")

    @property
    def mirror_relpath(self) -> str:
        """在门控仓镜像目录内的相对路径。"""
        return self.relpath

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "relpath": self.relpath,
            "canary": self.canary,
            "expect_symbols": list(self.expect_symbols),
            "expect_exit": list(self.expect_exit),
            "timeout_s": self.timeout_s,
            "note": self.note,
        }


#: harness 层首版门控白名单（2026-11-07 现网快照）。
DEFAULT_UNITS: tuple[HarnessUnit, ...] = (
    HarnessUnit(
        name="campaign_dispatcher",
        role="dispatcher",
        relpath="campaign_dispatcher.py",
        canary="symbol_probe",
        expect_symbols=("log", "q", "api", "build_goal", "main"),
        note="战役 DAG 派发器；顶层即绑定 DB/令牌，执行式试跑需生产写权 → 符号探针",
    ),
    HarnessUnit(
        name="tp_worker",
        role="worker",
        relpath="tp_worker.py",
        canary="symbol_probe",
        expect_symbols=("load_env", "api", "prepare_worktree", "main"),
        note="任务机 worker；执行需 git 检出与平台 API → 符号探针",
    ),
    HarnessUnit(
        name="chronicle_clock",
        role="daemon",
        relpath="chronicle_clock_once.py",
        canary="exec_dry_run",
        expect_symbols=("psql", "one", "has"),
        expect_exit=(0, 1),
        note="战史官钟单拍；纯 SQL 无 LLM，psql shim 下应打印 SQLERR 并 exit(1)",
    ),
    HarnessUnit(
        name="saturation_regulator",
        role="daemon",
        relpath="saturation_regulator_loop_once.py",
        canary="exec_dry_run",
        expect_symbols=("q",),
        expect_exit=(0,),
        note="饱和调节器单跳；db 不可达时应静默跳过 exit(0)（不得挂死）",
    ),
    HarnessUnit(
        name="coordinator_shadow",
        role="daemon",
        relpath="coordinator_shadow.py",
        canary="symbol_probe",
        expect_symbols=("log", "q", "gather_pulse", "think", "beat"),
        note="影子协调官；含 LLM 调用与 /var/log 写入 → 符号探针",
    ),
    HarnessUnit(
        name="report_watch",
        role="daemon",
        relpath="report_watch.py",
        canary="symbol_probe",
        expect_symbols=("log", "api", "q", "inventory", "main"),
        note="报告守望线；会创建补报单 → 符号探针",
    ),
    HarnessUnit(
        name="wave_cycle",
        role="daemon",
        relpath="wave_cycle.py",
        canary="symbol_probe",
        expect_symbols=("log", "q", "api", "login", "load_state", "save_state"),
        note="波次循环；写 state 文件并派单 → 符号探针",
    ),
    HarnessUnit(
        name="credit_settler",
        role="daemon",
        relpath="credit_settler_loop.py",
        canary="symbol_probe",
        expect_symbols=("log", "q", "settle"),
        note="信用结算环；写记账表 → 符号探针",
    ),
)


def units_by_name(units=DEFAULT_UNITS) -> dict[str, HarnessUnit]:
    return {u.name: u for u in units}


def unit_paths(units=DEFAULT_UNITS) -> list[str]:
    return [u.relpath for u in units]


def resolve_unit(relpath: str, units=DEFAULT_UNITS) -> HarnessUnit | None:
    for u in units:
        if u.relpath == relpath or u.name == relpath:
            return u
    return None