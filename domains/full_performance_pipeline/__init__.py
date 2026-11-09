"""跨域集成验证域包：失速/污底/导航/风载/砰击/拖曳/甲板积水 端到端串联。

对外入口：`pipeline.run_all()` / `report.build_report()`（见 README.md）。
"""
from . import contracts, modules_core, modules_ext, pipeline, providers, synthetic  # noqa: F401

__all__ = ["contracts", "modules_core", "modules_ext", "pipeline", "providers", "synthetic"]
