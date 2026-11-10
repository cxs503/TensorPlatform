#!/usr/bin/env python3
"""机制重放一致性套件（mech_replay）：平台门禁机制的历史重放 golden 套件 + 变异检测。

模块地图
--------
* :mod:`tools.mech_replay.spec`        —— 声明式规则表 + 纯函数解释器（判词骨架）
* :mod:`tools.mech_replay.mechanisms`  —— 8 个核心机制的规则表与纯函数入口 ``judge``
* :mod:`tools.mech_replay.reference`   —— 8 个机制的独立第二实现（差分用，防自证）
* :mod:`tools.mech_replay.collector`   —— 真实战役记录 → 脱敏 fixture + sha256 清单
* :mod:`tools.mech_replay.replay`      —— 离线重放 + 判词 diff + 幂等/置换对校验
* :mod:`tools.mech_replay.mutate`      —— 6 类退化注入与检出率度量
* :mod:`tools.mech_replay.gate`        —— C1–C5 机器门（CI 绿/红 + 报告 JSON）
* :mod:`tools.mech_replay.figures`     —— 报告图（覆盖/检出矩阵/一致性）

快速用法
--------
    python3 -m tools.mech_replay --mode replay --fixtures artifacts/mech_replay/fixtures
    python3 -m tools.mech_replay --mode gate
    python3 -m tools.mech_replay --mode mutate --mechanism merge_gate
    python3 -m tools.mech_replay --mode collect --snapshot artifacts/mech_replay/sources_snapshot.json
"""

from __future__ import annotations

from tools.mech_replay import mechanisms, reference, spec  # noqa: F401

__all__ = ["mechanisms", "reference", "spec"]
__version__ = "1.0.0"