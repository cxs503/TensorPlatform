"""context_package —— 任务干货包协议（打包/渲染/审计/实证）。

出口：
- ``build_package``  : 按任务类组装干货包（密度门 + 预算 + 去重）
- ``render_block``   : 渲染成注入块（索引密度 + 决策键 + 详情指针）
- ``inject``         : tp_worker 注入面挂钩（off/report/enforce，fail-open）
- ``validate_package`` / ``validate_decl`` : 包体与工作包契约声明校验
"""

from .assembler import build_package, covered_by
from .hook import inject
from .ledger import attribute, read_ledger, record
from .loader import est_tokens, load_config
from .package_schema import validate_decl, validate_package
from .render import render_block, render_tokens
from .taxonomy import class_spec, detect_class

__all__ = ["build_package", "covered_by", "inject", "attribute", "read_ledger", "record",
           "est_tokens", "load_config", "validate_decl", "validate_package",
           "render_block", "render_tokens", "class_spec", "detect_class"]
