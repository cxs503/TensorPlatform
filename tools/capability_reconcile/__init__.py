"""能力台账三源自动对账引擎（capability reconciliation engine）。

输入：能力台账（任意 JSON，形状见 :func:`loader.normalize_ledger`）+ 代码库 +
测试套件 + 文档库（+ 可选的 junit 执行结果）。
输出：每项能力的三源（code/test/doc）一致性报告、过度声明检测、证据等级调整
建议、sha256 链式审计日志。

纪律（TensorPlatform 铁律）：本包**零领域语义**——不含任何船舶/物理域词条、
域判据或域算例。能力 id、域标签、源文件绑定全部由输入台账 / 绑定配置 / 自动
解析注入；`tests/test_capability_reconcile.py::test_zero_domain_hardcoding`
用注入数据反向校验包内源码不出现台账里的 id 与域标签。
"""
from __future__ import annotations

from .core import LEVELS, RANK, canonical_json, sha256_hex  # noqa: F401

__all__ = ["LEVELS", "RANK", "canonical_json", "sha256_hex"]
__version__ = "1.0.0"