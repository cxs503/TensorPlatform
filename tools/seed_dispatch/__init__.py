"""跨战役知识蒸馏 → EVOI 派单排序的集成层（域无关机制，零领域语义）。

模块面
------
``params``       全部阈值/窗口/口径的数据面（``--config`` 可覆盖，未知字段报错）
``log``          真实派单日志适配：派单时刻特征 + 结局标签 + 成本；严格回看、无事后字段
``policy``       P(pass) 逻辑回归 / 成本模型 / EVOI / walk-forward / IPS-SNIPS / precision@k / AUC
``integration``  种子候选（同构设计矩阵）+ EVOI 排名 + 泄漏审计 + 判据汇总
``fixtures``     合成 DGP 夹具（已知真值，供测试与审计灵敏度）
``report``       图 + 论文级 markdown（数字程序化注入）
``acceptance``   一条命令跑完 (a)~(g) 并落 ``eval_report.json`` / ``eval_report.md`` / ``MANIFEST.json``

用法::

    python3 -m tools.seed_dispatch.cli \\
        --snapshot runs/p318w2/n10/mission_snapshot.json \\
        --distill runs/p318w2/n10/distill \\
        --out runs/p318w2/n10
"""

from __future__ import annotations

__all__ = ["run", "SeedDispatchParams", "load_params"]


def run(**kwargs):  # pragma: no cover - 薄转发
    from .acceptance import run as _run

    return _run(**kwargs)


def __getattr__(name: str):  # pragma: no cover - 惰性属性，避免导入即依赖 numpy/matplotlib
    if name == "SeedDispatchParams":
        from .params import SeedDispatchParams

        return SeedDispatchParams
    if name == "load_params":
        from .params import load_params

        return load_params
    raise AttributeError(name)