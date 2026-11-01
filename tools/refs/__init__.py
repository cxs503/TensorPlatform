"""参考值语料（Reference-Value Corpus）与一致性审计工具链。

本包把"文献/规范/解析基准"变成机器可读、可溯源、带容差与覆盖因子的资产，
并提供对已合并平台功能（阻力/稳性公式）与基准数据集的一致性审计。

模块
----
schema   参考值资产模式：校验 / 规范化 JSON / sha256 锚定 / 容差判定
build    程序化生成语料（数字零手抄：全部由闭式表达式算出）
audit    一致性审计引擎（G1..G4 四道门）+ 平台公式复核

用法::

    python3 -m tools.refs.build      # 重新生成 tools/refs/corpus.json
    python3 -m tools.refs.audit      # 审计已入库基准数据集与平台公式
"""
from __future__ import annotations

__all__ = ["schema", "build", "audit"]