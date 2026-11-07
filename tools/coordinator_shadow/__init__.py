"""coordinator_shadow v1 —— 常驻影子协调官（IN27-H1）。

每 30 分钟一拍：读 chronicle s1–s8 脉搏 + daemon WARN 流 + 三个零流器官计数
（信用积压 / 收养停滞 / 声明率）→ LLM 判读（可否决，不可新增越界）→ 结构化诊断落
``coordinator_shadow`` 账本 → 够格的越界项立诊断 mission 到 TensorPlatform（去重、冷却、
开单上限）。**只读 + 只立单，不动手**：手术权归 H3。

分层（每层可单测、可单独给负控）
-------------------------------
- ``pulse``      只读脉搏（``ReadOnlyRunner`` 机器门 + 探针留 SQL 原文）
- ``registry``   器官/指标字典（判读语汇闭合，防幻觉器官）
- ``diagnose``   定量越界检测 + 立单纪律（持久性/冷却/上限/LLM 否决）
- ``judge``      LLM 判读者 + 解析/校验/回退（无 LLM 时 ``RuleJudge`` 顶上）
- ``filing``     立单通道（``WriteGuard`` 只放行一条 INSERT）
- ``ledger``     append-only 账本 + 跨拍状态 + 心跳
- ``runtime``    一拍总装（失败也留痕）
- ``daemon``     常驻循环（30 min 对齐、单实例锁、优雅退出）
- ``acceptance`` 72h 验收裁决器（连续覆盖 / 无人工修 / 被领执行 ≥3 / 误报率 <30%）
- ``soak``       72h 时钟加速演练（合成平台状态，确定性）

版本
----
``SCHEMA_VERSION = "coordinator-shadow-v1"``；判读 ``coordinator-shadow-verdict/1``；
拍 ``coordinator-shadow-beat/1``；验收 ``coordinator-shadow-acceptance/1``。
"""
from __future__ import annotations

SCHEMA_VERSION = "coordinator-shadow-v1"

from . import acceptance, diagnose, filing, judge, ledger, pulse, registry, runtime, soak  # noqa: E402

__all__ = [
    "SCHEMA_VERSION",
    "acceptance",
    "diagnose",
    "filing",
    "judge",
    "ledger",
    "pulse",
    "registry",
    "runtime",
    "soak",
]