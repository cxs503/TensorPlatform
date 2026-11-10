"""能力台账自动审计管线（capability_audit_gate）。

把「某能力是 REAL / MOCK / STUB / WITHHELD / NOT_SUPPORTED」从自述升级为
**CI 可复算、可回归拦截**的机器判定：

* ``spec``   能力清单/证据绑定/基准口径（外部注入，代码零领域语义）
* ``resolve``显式绑定优先、路径名模式兜底
* ``scan``   实现/测试 AST 信号 + 基准工件数值（零手抄）
* ``rules``  五级判定级联（阈值先声明，规则带版本号）
* ``gate``   基线回归防护（等级下降 → CI 红，需 ack 文件）
* ``report`` JSON + Markdown 证据链报告

边界：静态流水线不执行代码；运行期缺陷交给在线探针（tools/capability_audit）。
"""
from __future__ import annotations

from . import cache, engine, gate, report, resolve, rules, scan, spec, taxonomy

__all__ = ["cache", "engine", "gate", "report", "resolve", "rules", "scan", "spec", "taxonomy"]
__version__ = "1.0.0"