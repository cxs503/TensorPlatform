"""能力台账自动审计 v2 —— 可执行证据面（executable evidence plane）。

本包在既有静态审计（``tools.capability_audit_gate``，只看 AST 结构信号 + 落盘基准
工件）之上，补上**机器可执行**的一层：

1. ``exec_evidence``：按配置声明的 pytest 选择器实际执行测试（子进程 + JUnit XML），
   按配置声明的探针实际调用实现并核验不变量（恒等式 / 敏感度 / 决定性）。
2. ``cascade_v2``：把静态信号与可执行证据按**预声明**级联合并成证据等级。
3. ``roadmap``：把 MOCK/STUB 的升级路径做成机器可校验的工件（算法引用 + 步骤 +
   验收命令），并为 WITHHELD 生成证据补齐动作。
4. ``ledger_v2``：规范化 JSON + sha256 证据索引 + 两次运行可复现校验。
5. ``figures`` / ``cli``：审计图与命令行。

零领域语义
==========
本包代码**不含**任何域词条 / 域判据 / 域算例：能力 id、测试选择器、探针调用目标、
不变量表达式、基准口径、升级路径全部由 ``config/`` 下的数据文件注入；
``tests/capability_audit_v2`` 只用合成能力名（``cap.*``）证明机制。
"""
from .cascade_v2 import RULESET_VERSION, adjudicate  # noqa: F401
from .exec_evidence import collect  # noqa: F401
from .ledger_v2 import canonical_json, digest_of, build_ledger  # noqa: F401
from .roadmap import build_roadmap  # noqa: F401
from .spec_v2 import load_spec_v2, SpecV2Error  # noqa: F401

__all__ = [
    "RULESET_VERSION",
    "adjudicate",
    "collect",
    "canonical_json",
    "digest_of",
    "build_ledger",
    "build_roadmap",
    "load_spec_v2",
    "SpecV2Error",
]