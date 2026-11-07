"""interrogator —— 战略质询官（IN27-H4）。

owner 的「是否做到位了」式审计职能转移件：按 24h 周期，从平台真实信号出发
自动生成 3-5 个战略质询使命（问对不问错），质询结果回填账本，并把被证实的
质询转化为真实修补/改进单；连续性与转化率由机器判据裁定（acceptance.py）。

模块边界（只读平台 + 立单，不动手修）：
  probes.py        信号探针（每个信号带 source/status，拿不到记 unavailable，不补零）
  question_bank.py 五个战略质询族（零流器官 / grade 天花板 / 探索预算 / S-N 轨 / 质询自身转化）
  generator.py     周期生成器（3-5 条、指纹去重、goal 模板含判据与 honest FAIL）
  ledger.py        SQLite 账本（cycles / interrogations / conversions）+ 审计 JSONL
  acceptance.py    机器验收门（7 日连续 + ≥2 件真实转化）
  daemon.py        24h 主循环
  cli.py           命令行（probe / cycle / answer / convert / verify / backfill）
"""
from __future__ import annotations

__all__ = [
    "probes",
    "question_bank",
    "generator",
    "ledger",
    "acceptance",
    "daemon",
    "cli",
]

MECHANISM_ID = "IN27-H4"
GENERATOR_TAG = "interrogator"