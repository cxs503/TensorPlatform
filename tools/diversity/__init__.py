"""多样性引擎（IN25-T2）：氏族清单 / 盲预测锁 / 同族查重。

三个子模块可独立使用，互不依赖平台域语义：
    roster —— 族×框架×角色 的全格席位矩阵 + 种子派生 + 现场对账
    prereg —— 盲预测锁定（锁先于跑、锁不可改、结果不得早于锁）
    dedup  —— 方案摘要相似度门（表层/骨架/人口三层，fail-closed）
"""
from tools.diversity import dedup, prereg, roster  # noqa: F401

__all__ = ["roster", "prereg", "dedup"]