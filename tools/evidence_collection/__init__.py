"""能力台账证据自动化采集管线（evidence_collection）。

平台已有两层证据机制：

* ``tools/capability_audit_gate`` —— 静态审计（源码 AST + 基准工件 + 五级级联），**不执行代码**；
* 能力台账人工审计 —— 报告/文档引用，靠人写。

本包补第三条腿：**执行型证据**。每个能力的 Map（能力 → 面 / 探针 / 参考工件 / 测试）
来自 ``config/evidence_collection/spec.json``；采集器真跑 HTTP 探针与 pytest，复算参考误差，
按 ``ec-grade-v1`` 自动定级，产出带 sha256 的 ``acceptance.json`` + 判据表，并回写台账。

零领域语义：本包代码只有 HTTP / 算术 / 文件摘要与规则级联，域信息全部在 spec 里注入。
"""
from __future__ import annotations

from . import artifacts, collect, execute, expr, gate, grade, ledger, reference, runtime, spec

__all__ = ["artifacts", "collect", "execute", "expr", "gate", "grade", "ledger",
           "reference", "runtime", "spec"]
__version__ = "1.0.0"