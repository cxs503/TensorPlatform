"""H6 成熟度门（能力增量账本 + 五级成熟度 + 交叉一致性）。

平台共性机制：零领域语义，源路径与锚点由 ``data/maturity_gate_h6/h6.spec.json`` 注入。
"""

from __future__ import annotations

from . import core  # noqa: F401

__all__ = ["core", "cli", "report"]
__version__ = core.SCHEMA_VERSION