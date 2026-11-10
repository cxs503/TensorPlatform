"""五级成熟度阶梯：定义、序关系、与历史四档等级（REAL/MOCK/STUB/WITHHELD）的映射。

阶梯（rank 递增，全序）::

    0 not_supported           无任何可执行证据
    1 implemented             实现存在（源码/端点工件 sha256 可追溯）
    2 tested                  有可重跑的执行证据（重跑命令 + 通过 + 日志 sha256）
    3 benchmark_verified      有数值判据（value/tolerance/baseline）且落在容差内
    4 coupled_prototype       与相邻能力耦合端到端跑通（接口契约摘要 + 组件≥2）
    5 engineering_qualified   独立席位复核 + 多次重跑同判 + 适用包线 + 签署工件

历史四档 → 阶梯的映射（**只用于封顶，不用于抬级**）：

===================  ======================  ==========================================
历史档位              阶梯等价                封顶语义（contradiction cap）
===================  ======================  ==========================================
NOT_SUPPORTED         0                       0（不可越过 implemented）
STUB                  0                       0（空壳不得称为实现）
WITHHELD              1                       1（证据不足即封顶 implemented）
MOCK                  1                       1（硬编码/随机实现最多算 implemented）
REAL                  3                       不封顶（REAL 的语义=至少 benchmark_verified）
===================  ======================  ==========================================

``REAL`` 不封顶但**入口门槛是 3**：一条能力若被推断为 ``benchmark_verified`` 及以上，
才允许在台账里保留 REAL 标签；否则必须下调（fail-closed）。
"""
from __future__ import annotations

from typing import Dict, Optional

#: 阶梯（下标即 rank）
LADDER = (
    "not_supported",
    "implemented",
    "tested",
    "benchmark_verified",
    "coupled_prototype",
    "engineering_qualified",
)

#: 等级名 → rank
RANK_OF: Dict[str, int] = {name: i for i, name in enumerate(LADDER)}

#: 历史四档 → 等价 rank（仅用于语义对齐与报告）
LEGACY_EQUIVALENT: Dict[str, int] = {
    "NOT_SUPPORTED": 0,
    "STUB": 0,
    "WITHHELD": 1,
    "MOCK": 1,
    "REAL": 3,
}

#: 历史四档 → 封顶 rank（contradiction cap，未登记档位按 0 处理）
LEGACY_CAP: Dict[str, int] = {
    "NOT_SUPPORTED": 0,
    "STUB": 0,
    "WITHHELD": 1,
    "MOCK": 1,
}

#: 台账保留 ``REAL`` 标签所需的最低 rank
REAL_MIN_RANK = RANK_OF["benchmark_verified"]

#: rank → 等级名
LEVEL_OF_RANK: Dict[int, str] = {v: k for k, v in RANK_OF.items()}


def rank_of(level: str) -> int:
    """等级名 → rank；未登记即抛 ``KeyError``（不允许默认放行）。"""
    try:
        return RANK_OF[level]
    except KeyError as exc:  # pragma: no cover - 防御分支
        raise KeyError(f"未登记的成熟度等级 {level!r}（应在 {LADDER} 内）") from exc


def level_of_rank(rank: int) -> str:
    """rank → 等级名；越界夹到 [0, 5]（显式夹取，不静默）。"""
    if rank < 0:
        return LADDER[0]
    if rank >= len(LADDER):
        return LADDER[-1]
    return LEVEL_OF_RANK[rank]


def legacy_cap(legacy_level: Optional[str]) -> Optional[int]:
    """历史档位 → 封顶 rank；``None``/未登记 → ``None``（无封顶）。

    未登记档位（拼写错误等）**不加封顶**而不是加 0 封顶：封顶是惩罚项，
    未知惩罚不可臆造；未知档位的处理是"忽略并记入 reasons"。
    """
    if legacy_level is None:
        return None
    return LEGACY_CAP.get(legacy_level.strip().upper())


def legacy_to_ladder(legacy_level: Optional[str]) -> Optional[str]:
    """历史档位 → 等价的阶梯等级名（报告用；缺失返回 ``None``）。"""
    if legacy_level is None:
        return None
    rank = LEGACY_EQUIVALENT.get(legacy_level.strip().upper())
    return None if rank is None else level_of_rank(rank)


def keeps_real(inferred_level: str) -> bool:
    """推断等级是否足以支撑台账里的 ``REAL`` 标签。"""
    return rank_of(inferred_level) >= REAL_MIN_RANK