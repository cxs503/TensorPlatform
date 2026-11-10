"""cycle_baseline —— 任务周期四段分解与调度公平基线（平台级度量，零领域语义）。

分层（每层可独立单测）
----------------------
* ``spec``       口径冻结（段定义/门限/预注册指纹 SPEC_SHA256）
* ``stats``      纯统计（百分位/Jain/Gini/门禁等级）
* ``segments``   四段分解 + 分位账本 + 归因表
* ``fairness``   worker 公平性 + 饥饿/优先级反转检测 + 检测器判分
* ``simulator``  三策略模拟器 + 解析预测 + trace 反算 Jain
* ``sources``    只读取数（Postgres + git 落地索引）
* ``provenance`` 每行 SQL 原文 + sha256（零手抄证据链）
* ``runner``     组装报告与判据 (a)–(e)
* ``render``/``figures``  论文级文档与配图

CLI: ``python -m tools.cycle_baseline --help``
"""
from __future__ import annotations

from .spec import PREREG_ID, SCHEMA_VERSION, SEGMENTS, SPEC_SHA256  # noqa: F401

__all__ = ["PREREG_ID", "SCHEMA_VERSION", "SEGMENTS", "SPEC_SHA256"]